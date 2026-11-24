"""Keep the model in step with the road: data assimilation and incident detection.

Assimilation is a simple nudging (constant-gain) filter: after the model forecasts the
next interval, each cell with a good detector reading is pulled toward the measured
density, and neighbouring cells without detectors get a smaller share of the correction.
This is a deliberate choice over an ensemble Kalman filter (see ADR 0002).

Incident detection compares observations with the model forecast instead of comparing
two detectors with each other. Recurrent bottlenecks such as the lane drop are already
in the model, so they do not raise alarms; an unexpected queue does. An alarm needs
the upstream detector to read denser than forecast and the downstream detector to read
lighter than forecast for several consecutive intervals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ctm import State
from .network import Network
from .sensors import QualityResult, occupancy_to_density


def measured_density(net: Network, qr: QualityResult) -> float | None:
    """Density (veh/km, all lanes) from a good reading. Occupancy is used in congestion."""
    if not qr.ok:
        return None
    r = qr.reading
    cell = net.cells[net.cell_index(next(d.cell for d in net.detectors if d.id == r.detector_id))]
    if r.speed_kmh is not None and r.speed_kmh >= 40 and r.flow_vph is not None:
        return r.flow_vph / r.speed_kmh
    return occupancy_to_density(r.occupancy_pct or 0.0) * cell.lanes


@dataclass
class Assimilator:
    """Constant-gain nudging with corrections interpolated between detectors.

    Each detector gives a residual (measured minus forecast density, per lane). Cells
    between two detectors get a linear blend of the two residuals; cells beyond the
    first or last detector take the nearest one. Corrections never cross a detector
    whose reading was rejected: such a detector simply contributes nothing.
    """

    gain: float = 0.6

    def apply(self, net: Network, state: State, results: list[QualityResult]) -> dict[str, float]:
        """Nudge densities toward good readings. Returns residuals (measured - forecast, veh/km) per detector."""
        residuals: dict[str, float] = {}
        anchors: list[tuple[int, float]] = []  # (cell index, residual per lane)
        for qr in results:
            k_meas = measured_density(net, qr)
            if k_meas is None:
                continue
            det = next(d for d in net.detectors if d.id == qr.reading.detector_id)
            i = net.cell_index(det.cell)
            resid = k_meas - state.density[i]
            residuals[det.id] = resid
            anchors.append((i, resid / net.cells[i].lanes))
        if not anchors:
            return residuals
        anchors.sort()
        for i, c in enumerate(net.cells):
            if i <= anchors[0][0]:
                r = anchors[0][1]
            elif i >= anchors[-1][0]:
                r = anchors[-1][1]
            else:
                for (a, ra), (b, rb) in zip(anchors, anchors[1:], strict=False):
                    if a <= i <= b:
                        w = (i - a) / (b - a) if b > a else 0.0
                        r = ra + w * (rb - ra)
                        break
            state.density[i] = min(max(state.density[i] + self.gain * r * c.lanes, 0.0), c.jam_density)
        return residuals


@dataclass(frozen=True)
class Alarm:
    kind: str
    upstream_detector: str
    downstream_detector: str
    between_km: tuple[float, float]
    since_s: float
    upstream_residual: float
    downstream_residual: float


@dataclass
class IncidentDetector:
    """Residual-based detector (thresholds in veh/km per lane).

    A pair of adjacent detectors is suspicious in an interval when the upstream reading
    is denser than forecast by ``up_threshold`` and the downstream one is not denser than
    forecast by more than ``down_threshold`` (normally it is lighter: the road after an
    incident is starved). An alarm needs ``k`` suspicious intervals out of the last ``n``.
    Defaults were chosen from synthetic runs (see docs/architecture/overview.md): no false
    alarms in 4 incident-free peak runs even at a threshold of 4, and 4 of 4 single-lane
    incidents found within 2 to 4 minutes.
    """

    up_threshold: float = 7.0
    down_threshold: float = -1.0
    k: int = 3
    n: int = 4
    clear_after: int = 6  # consecutive normal intervals before an active alarm is cleared
    _window: dict[tuple[str, str], list[bool]] = field(default_factory=dict)
    _since: dict[tuple[str, str], float] = field(default_factory=dict)
    _clear: dict[tuple[str, str], int] = field(default_factory=dict)
    active: dict[tuple[str, str], Alarm] = field(default_factory=dict)
    cleared_last: list[Alarm] = field(default_factory=list)

    def update(self, net: Network, residuals: dict[str, float], now_s: float) -> list[Alarm]:
        """Feed one interval of residuals; return alarms raised in this call."""
        raised: list[Alarm] = []
        self.cleared_last = []
        dets = list(net.detectors)
        for up, down in zip(dets, dets[1:], strict=False):
            key = (up.id, down.id)
            if up.id not in residuals or down.id not in residuals:
                continue  # no decision without both readings
            ru = residuals[up.id] / net.cells[net.cell_index(up.cell)].lanes
            rd = residuals[down.id] / net.cells[net.cell_index(down.cell)].lanes
            hit = ru >= self.up_threshold and rd <= self.down_threshold
            w = self._window.setdefault(key, [])
            w.append(hit)
            del w[: -self.n]
            if hit:
                self._since.setdefault(key, now_s)
            if sum(w) >= self.k:
                self._clear[key] = 0
                if key not in self.active:
                    a = Alarm(
                        "possible_incident",
                        up.id,
                        down.id,
                        (round(net.position_km(net.cell_index(up.cell)), 2), round(net.position_km(net.cell_index(down.cell) + 1), 2)),
                        self._since.get(key, now_s),
                        round(ru, 1),
                        round(rd, 1),
                    )
                    self.active[key] = a
                    raised.append(a)
            elif not any(w):
                self._since.pop(key, None)
                if key in self.active:
                    self._clear[key] = self._clear.get(key, 0) + 1
                    if self._clear[key] >= self.clear_after:
                        self.cleared_last.append(self.active.pop(key))
            if key in self.active and any(w):
                self._clear[key] = 0
        return raised
