"""What-if scenarios forked from the current state of the twin.

A scenario changes one or more things (lane closures, demand, metering, plaza lanes)
for a horizon and is compared with a baseline that starts from the same state and
uses the same demand. Both runs are deterministic, so the difference is the effect of
the change alone.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field

from .control import RampMetering
from .ctm import State, Totals, step
from .demand import Demand
from .kpi import snapshot, travel_time_min
from .network import Network, NetworkError


@dataclass(frozen=True)
class Closure:
    cell: str
    lanes_closed: int
    start_min: float = 0.0
    duration_min: float = 30.0


@dataclass(frozen=True)
class PlazaChange:
    entry: str
    etc_lanes: int | None = None
    manual_lanes: int | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    horizon_min: float = 60.0
    closures: tuple[Closure, ...] = ()
    demand_factor: float = 1.0
    metering: bool | None = None  # None = keep the current setting
    reopen_after_min: float | None = None  # clear the closures already in the state after this many minutes
    plaza_changes: tuple[PlazaChange, ...] = ()

    def validate(self, net: Network) -> None:
        if not 5 <= self.horizon_min <= 360:
            raise ValueError("horizon_min must be between 5 and 360")
        if self.reopen_after_min is not None and not 0 <= self.reopen_after_min <= self.horizon_min:
            raise ValueError("reopen_after_min must be between 0 and the horizon")
        if not 0.3 <= self.demand_factor <= 2.0:
            raise ValueError("demand_factor must be between 0.3 and 2.0")
        for c in self.closures:
            i = net.cell_index(c.cell)
            if not 1 <= c.lanes_closed <= net.cells[i].lanes:
                raise ValueError(f"lanes_closed for {c.cell} must be between 1 and {net.cells[i].lanes}")
            if c.start_min < 0 or c.duration_min <= 0:
                raise ValueError("closure start must be >= 0 and duration > 0")
        for p in self.plaza_changes:
            e = net.entry(p.entry)
            if e.plaza is None:
                raise NetworkError(f"entry {p.entry} has no toll plaza")
            for v in (p.etc_lanes, p.manual_lanes):
                if v is not None and not 0 <= v <= 20:
                    raise ValueError("plaza lanes must be between 0 and 20")


def apply_plaza_changes(net: Network, changes: tuple[PlazaChange, ...]) -> Network:
    if not changes:
        return net
    by_id = {p.entry: p for p in changes}
    entries = []
    for e in net.entries:
        p = by_id.get(e.id)
        if p and e.plaza:
            plaza = dataclasses.replace(
                e.plaza,
                etc_lanes=e.plaza.etc_lanes if p.etc_lanes is None else p.etc_lanes,
                manual_lanes=e.plaza.manual_lanes if p.manual_lanes is None else p.manual_lanes,
            )
            if plaza.etc_lanes + plaza.manual_lanes == 0:
                raise NetworkError(f"entry {e.id}: a plaza needs at least one lane")
            e = dataclasses.replace(e, plaza=plaza)
        entries.append(e)
    return dataclasses.replace(net, entries=tuple(entries))


@dataclass
class RunResult:
    kpis: dict
    max_queue_veh: dict[str, float]
    min_speed_kmh: float
    max_travel_time_min: float
    mean_travel_time_min: float
    series: list[dict] = field(default_factory=list)


def simulate(
    net: Network,
    start: State,
    demand: Demand,
    horizon_min: float,
    metering: RampMetering | None,
    closures: tuple[Closure, ...] = (),
    sample_every_s: float = 300.0,
    reopen_after_min: float | None = None,
) -> RunResult:
    state = start.copy()
    state.totals = Totals()  # results cover the horizon only
    t0 = state.time_s
    end = t0 + horizon_min * 60.0
    base_closed = list(state.lanes_closed)
    max_q = {e.id: state.entries[e.id].queue for e in net.entries}
    min_v = float("inf")
    max_tt = 0.0
    tt_sum, n_steps = 0.0, 0
    series = []
    next_sample = t0
    while state.time_s < end - 1e-6:
        rel_min = (state.time_s - t0) / 60.0
        closed = [0] * net.n_cells if reopen_after_min is not None and rel_min >= reopen_after_min else list(base_closed)
        for c in closures:
            if c.start_min <= rel_min < c.start_min + c.duration_min:
                i = net.cell_index(c.cell)
                closed[i] = min(net.cells[i].lanes, max(closed[i], c.lanes_closed))
        state.lanes_closed = closed
        if metering is not None:
            metering.apply(net, state)
        step(net, state, demand.at(state.time_s))
        for eid, es in state.entries.items():
            max_q[eid] = max(max_q[eid], es.queue)
        tt = travel_time_min(net, state)
        max_tt = max(max_tt, tt)
        tt_sum += tt
        n_steps += 1
        min_v = min(min_v, min(s["speed_kmh"] for s in snapshot(net, state)["cells"]))
        if state.time_s >= next_sample:
            series.append({"t_min": round((state.time_s - t0) / 60.0, 1), "travel_time_min": round(tt, 2)})
            next_sample += sample_every_s
    state.lanes_closed = base_closed
    return RunResult(
        snapshot(net, state),
        {k: round(v, 1) for k, v in max_q.items()},
        round(min_v, 1),
        round(max_tt, 2),
        round(tt_sum / max(n_steps, 1), 2),
        series,
    )


def what_if(net: Network, start: State, demand: Demand, metering: RampMetering, scenario: Scenario) -> dict:
    scenario.validate(net)
    base_meter = copy.deepcopy(metering)
    scen_meter = copy.deepcopy(metering)
    if scenario.metering is not None:
        scen_meter.enabled = scenario.metering
    scen_net = apply_plaza_changes(net, scenario.plaza_changes)
    base = simulate(net, start, demand, scenario.horizon_min, base_meter)
    scen = simulate(
        scen_net,
        start,
        demand.scaled(scenario.demand_factor),
        scenario.horizon_min,
        scen_meter,
        scenario.closures,
        reopen_after_min=scenario.reopen_after_min,
    )

    def pick(r: RunResult) -> dict:
        t = r.kpis["totals"]
        return {
            "vht": t["vht"],
            "vht_queues": t["vht_queues"],
            "delay_vh": t["delay_vh"],
            "vkt": t["vkt"],
            "exited": t["exited"],
            "max_travel_time_min": r.max_travel_time_min,
            "mean_travel_time_min": r.mean_travel_time_min,
            "end_travel_time_min": r.kpis["travel_time_min"],
            "min_speed_kmh": r.min_speed_kmh,
            "max_queue_veh": r.max_queue_veh,
        }

    b, s = pick(base), pick(scen)
    delta = {
        k: round(s[k] - b[k], 2)
        for k in ("vht", "vht_queues", "delay_vh", "exited", "mean_travel_time_min", "max_travel_time_min", "end_travel_time_min")
    }
    queue_limits = {e.id: e.max_queue_veh for e in net.entries}
    warnings = [
        f"Queue at {eid} reaches {q:.0f} veh (limit {queue_limits[eid]:.0f}): spillback onto the street is likely"
        for eid, q in s["max_queue_veh"].items()
        if q > queue_limits[eid]
    ]
    return {
        "scenario": dataclasses.asdict(scenario),
        "start_clock": snapshot(net, start)["clock"],
        "baseline": b,
        "scenario_result": s,
        "delta": delta,
        "series": {"baseline": base.series, "scenario": scen.series},
        "warnings": warnings,
    }
