"""The digital twin: a model that is kept in step with detector data and can be forked.

Loop per detector interval:
1. forecast: run the model from the last update to now with the expected demand;
2. check readings (``QualityChecker``) and record sensor health;
3. compare good readings with the forecast (residuals) and look for incidents;
4. assimilate: pull the model toward the readings;
5. correct the demand level from toll-plaza transaction counts;
6. compute ramp-meter rates from the corrected state (they are sent to the road).

``SyntheticRoad`` plays the part of the real corridor in the demo and tests. It runs
its own copy of the model with different demand, random noise and hidden incidents,
and only exposes detector readings and plaza counts, as a real road would.
"""

from __future__ import annotations

import collections
import copy
import threading
from dataclasses import dataclass, field

from .control import RampMetering, default_metering
from .ctm import State, initial_state, step
from .demand import Demand, morning_peak
from .estimation import Alarm, Assimilator, IncidentDetector
from .kpi import clock, snapshot
from .network import Network
from .scenario import Scenario, what_if
from .sensors import QualityChecker, Reading, SyntheticDetectors


@dataclass
class SensorHealth:
    readings: int = 0
    rejected: int = 0
    last_flags: tuple[str, ...] = ()
    last_seen_s: float | None = None

    @property
    def status(self) -> str:
        if self.readings == 0:
            return "no_data"
        if self.last_flags:
            return "fault"
        return "ok"


class DigitalTwin:
    def __init__(self, net: Network, start_s: float = 6 * 3600, metering: RampMetering | None = None, demand: Demand | None = None):
        self.net = net
        self.state: State = initial_state(net, start_s)
        self.expected = demand or Demand(morning_peak())
        self.correction: dict[str, float] = {e.id: 1.0 for e in net.entries}
        self.metering = metering or default_metering()
        self.quality = QualityChecker(net)
        self.assimilator = Assimilator()
        self.incidents = IncidentDetector()
        self.health: dict[str, SensorHealth] = {d.id: SensorHealth() for d in net.detectors}
        self.alerts: collections.deque[dict] = collections.deque(maxlen=200)
        self.history: collections.deque[dict] = collections.deque(maxlen=720)
        self.last_residuals: dict[str, float] = {}
        self._stale: dict[tuple[str, str], int] = {}
        self.lock = threading.RLock()

    # -- demand -----------------------------------------------------------------
    def demand(self) -> Demand:
        """Expected demand with the current per-entry corrections (deterministic)."""
        return Demand(dict(self.expected.profiles), self.expected.factor, 0.0, None, dict(self.correction))

    # -- loop -------------------------------------------------------------------
    def forecast_to(self, t_s: float) -> None:
        d = self.demand()
        while self.state.time_s < t_s - 1e-6:
            step(self.net, self.state, d.at(self.state.time_s))

    def ingest(self, readings: list[Reading], plaza_counts_vph: dict[str, float] | None = None, now_s: float | None = None) -> dict:
        with self.lock:
            now = now_s if now_s is not None else max((r.timestamp_s for r in readings), default=self.state.time_s)
            self.forecast_to(now)
            results = self.quality.check_all(readings, now)
            for qr in results:
                h = self.health.get(qr.reading.detector_id)
                if h is None:
                    continue
                h.readings += 1
                h.last_flags = qr.flags
                if qr.ok:
                    h.last_seen_s = qr.reading.timestamp_s
                else:
                    h.rejected += 1
            residuals = self.assimilator.apply(self.net, self.state, results)
            self.last_residuals = residuals
            raised: list[Alarm] = self.incidents.update(self.net, residuals, now)
            for a in raised:
                self.alerts.appendleft(
                    {
                        "time": clock(now),
                        "kind": a.kind,
                        "message": f"Unexpected queue between {a.upstream_detector} and {a.downstream_detector} "
                        f"(km {a.between_km[0]}–{a.between_km[1]}). Check CCTV; consider a lane-closure what-if.",
                        "upstream_detector": a.upstream_detector,
                        "downstream_detector": a.downstream_detector,
                        "between_km": list(a.between_km),
                    }
                )
            for a in self.incidents.cleared_last:
                explained = self._closures_between(a.upstream_detector, a.downstream_detector)
                msg = (
                    f"Alarm closed: the confirmed closure at {', '.join(explained)} explains the readings between "
                    f"{a.upstream_detector} and {a.downstream_detector}."
                    if explained
                    else f"Readings between {a.upstream_detector} and {a.downstream_detector} back to normal."
                )
                self.alerts.appendleft(
                    {
                        "time": clock(now),
                        "kind": "incident_explained" if explained else "incident_cleared",
                        "message": msg,
                        "upstream_detector": a.upstream_detector,
                        "downstream_detector": a.downstream_detector,
                    }
                )
            self._check_stale_closures(now, residuals)
            for qr in results:
                if not qr.ok:
                    key = (qr.reading.detector_id, qr.flags)
                    if not any(
                        al.get("kind") == "sensor" and (al.get("detector"), tuple(al.get("flags", ()))) == key
                        for al in list(self.alerts)[:20]
                    ):
                        self.alerts.appendleft(
                            {
                                "time": clock(now),
                                "kind": "sensor",
                                "detector": qr.reading.detector_id,
                                "flags": list(qr.flags),
                                "message": f"Detector {qr.reading.detector_id}: {', '.join(qr.flags)}. Reading not used.",
                            }
                        )
            if plaza_counts_vph:
                for eid, measured in plaza_counts_vph.items():
                    es = self.state.entries.get(eid)
                    if es is None:
                        continue
                    modelled = es.last_flow_vph
                    if modelled > 100 and es.queue < 1:
                        ratio = min(max(measured / modelled, 0.5), 1.5)
                        self.correction[eid] = min(max(0.8 * self.correction[eid] + 0.2 * ratio * self.correction[eid], 0.5), 1.5)
            self.metering.apply(self.net, self.state)
            snap = snapshot(self.net, self.state)
            self.history.append(
                {
                    "time_s": snap["time_s"],
                    "travel_time_min": snap["travel_time_min"],
                    "queue_veh": round(sum(e["queue_veh"] for e in snap["entries"]), 1),
                }
            )
            return {
                "time": snap["clock"],
                "accepted": sum(r.ok for r in results),
                "rejected": sum(not r.ok for r in results),
                "alarms": [a.kind for a in raised],
            }

    def _closures_between(self, up_id: str, down_id: str) -> list[str]:
        cell = {d.id: self.net.cell_index(d.cell) for d in self.net.detectors}
        lo, hi = cell[up_id], cell[down_id]
        return [c for c in self.closures() if lo <= self.net.cell_index(c) <= hi]

    def _check_stale_closures(self, now: float, residuals: dict[str, float]) -> None:
        """A confirmed closure the road no longer shows: the detector after it reads denser than forecast."""
        dets = list(self.net.detectors)
        for cell_id, n in self.closures().items():
            i = self.net.cell_index(cell_id)
            down = next((d for d in dets if self.net.cell_index(d.cell) > i), None)
            if down is None or down.id not in residuals:
                continue
            rd = residuals[down.id] / self.net.cells[self.net.cell_index(down.cell)].lanes
            key = (cell_id, down.id)
            count = self._stale.get(key, 0) + 1 if rd >= 3.0 else 0
            self._stale[key] = count
            if count == 6:  # three minutes of more traffic after the closure than the model allows
                self.alerts.appendleft(
                    {
                        "time": clock(now),
                        "kind": "check_closure",
                        "message": f"More traffic is passing {cell_id} than {n} closed lane(s) allow (detector {down.id}). "
                        "If the lane has reopened, confirm it so the twin stays accurate.",
                    }
                )

    def meter_commands(self) -> dict[str, float | None]:
        return {eid: es.meter_vph for eid, es in self.state.entries.items()}

    def set_metering(self, enabled: bool) -> None:
        with self.lock:
            if enabled:
                self.metering.enabled = True
            else:
                self.metering.disable(self.state)

    def confirm_closure(self, cell: str, lanes_closed: int, by: str = "operator") -> None:
        """Tell the twin about a verified lane closure (0 = reopen)."""
        with self.lock:
            i = self.net.cell_index(cell)
            lanes = self.net.cells[i].lanes
            if not 0 <= lanes_closed <= lanes:
                raise ValueError(f"lanes_closed for {cell} must be between 0 and {lanes}")
            self.state.lanes_closed[i] = lanes_closed
            msg = (
                f"{by} confirmed {lanes_closed} of {lanes} lanes closed at {cell} (km {self.net.position_km(i):.1f})"
                if lanes_closed
                else f"{by} reopened all lanes at {cell} (km {self.net.position_km(i):.1f})"
            )
            self.alerts.appendleft({"time": clock(self.state.time_s), "kind": "operator", "message": msg})

    def closures(self) -> dict[str, int]:
        return {self.net.cells[i].id: n for i, n in enumerate(self.state.lanes_closed) if n}

    def what_if(self, scenario: Scenario) -> dict:
        with self.lock:
            start = self.state.copy()
            meter = copy.deepcopy(self.metering)
            demand = self.demand()
        return what_if(self.net, start, demand, meter, scenario)

    def status(self) -> dict:
        with self.lock:
            snap = snapshot(self.net, self.state)
            snap["metering_enabled"] = self.metering.enabled
            snap["confirmed_closures"] = self.closures()
            snap["demand_correction"] = {k: round(v, 3) for k, v in self.correction.items()}
            snap["sensors"] = [
                {
                    "id": d.id,
                    "cell": d.cell,
                    "status": self.health[d.id].status,
                    "flags": list(self.health[d.id].last_flags),
                    "residual_vpkpl": round(self.last_residuals[d.id] / self.net.cells[self.net.cell_index(d.cell)].lanes, 1)
                    if d.id in self.last_residuals
                    else None,
                }
                for d in self.net.detectors
            ]
            snap["alerts"] = list(self.alerts)[:20]
            snap["active_incident_alarms"] = len(self.incidents.active)
            return snap


@dataclass
class HiddenIncident:
    cell: str
    lanes_closed: int
    start_s: float
    end_s: float


@dataclass
class SyntheticRoad:
    """Stand-in for the real corridor. Only readings and plaza counts leave this object."""

    net: Network
    start_s: float = 6 * 3600
    demand_factor: float = 1.05
    noise: float = 0.05
    seed: int = 11
    incidents: list[HiddenIncident] = field(default_factory=list)
    detector_faults: dict[str, str] = field(default_factory=dict)
    interval_s: float = 30.0

    def __post_init__(self) -> None:
        self.state = initial_state(self.net, self.start_s)
        self.demand = Demand(morning_peak(), self.demand_factor, noise=0.03, seed=self.seed)
        self.detectors = SyntheticDetectors(self.net, self.noise, self.seed, dict(self.detector_faults))
        self._served: dict[str, float] = {}

    def apply_meters(self, commands: dict[str, float | None]) -> None:
        for eid, rate in commands.items():
            if eid in self.state.entries:
                self.state.entries[eid].meter_vph = rate

    def advance(self) -> tuple[list[Reading], dict[str, float]]:
        """Run one detector interval; return readings and plaza counts (veh/h)."""
        end = self.state.time_s + self.interval_s
        served = {e.id: 0.0 for e in self.net.entries}
        steps = 0
        while self.state.time_s < end - 1e-6:
            t = self.state.time_s
            closed = [0] * self.net.n_cells
            for inc in self.incidents:
                if inc.start_s <= t < inc.end_s:
                    i = self.net.cell_index(inc.cell)
                    closed[i] = max(closed[i], inc.lanes_closed)
            self.state.lanes_closed = closed
            demand = self.demand.at(t)
            step(self.net, self.state, demand)
            for eid, es in self.state.entries.items():
                served[eid] += es.last_flow_vph
            steps += 1
        counts = {k: v / steps for k, v in served.items()} if steps else served
        return self.detectors.read(self.state), counts


def run_closed_loop(twin: DigitalTwin, road: SyntheticRoad, until_s: float) -> None:
    """Advance road and twin together; the twin's meter rates are applied to the road."""
    while road.state.time_s < until_s - 1e-6:
        road.apply_meters(twin.meter_commands() if twin.metering.enabled else {e.id: None for e in twin.net.entries})
        readings, counts = road.advance()
        twin.ingest(readings, counts, road.state.time_s)
