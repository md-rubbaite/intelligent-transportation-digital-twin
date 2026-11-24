"""Detector readings, synthetic detectors and data-quality checks.

A reading is what a loop or radar detector reports for one interval: flow (veh/h),
mean speed (km/h) and occupancy (%). Real feeds have gaps, frozen values, spikes and
clock problems, so every reading goes through ``QualityChecker`` before the twin uses
it. Rejected readings are kept with their reason so operators can see sensor health.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .ctm import State, cell_capacity, speed_kmh
from .network import Network

EFFECTIVE_VEHICLE_LENGTH_M = 7.0  # vehicle + detection zone, used to convert density <-> occupancy


@dataclass(frozen=True)
class Reading:
    detector_id: str
    timestamp_s: float
    flow_vph: float | None
    speed_kmh: float | None
    occupancy_pct: float | None


def density_to_occupancy(density_per_lane: float) -> float:
    return min(100.0, density_per_lane * EFFECTIVE_VEHICLE_LENGTH_M / 10.0)


def occupancy_to_density(occupancy_pct: float) -> float:
    return occupancy_pct * 10.0 / EFFECTIVE_VEHICLE_LENGTH_M


@dataclass
class SyntheticDetectors:
    """Reads the 'true' state of a simulation as detectors would, with noise and faults.

    ``faults`` maps detector id to one of: ``missing``, ``stuck``, ``spike``.
    """

    net: Network
    noise: float = 0.05
    seed: int | None = 7
    faults: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._frozen: dict[str, Reading] = {}

    def read(self, state: State) -> list[Reading]:
        out = []
        for d in self.net.detectors:
            i = self.net.cell_index(d.cell)
            c = self.net.cells[i]
            fault = self.faults.get(d.id)
            if fault == "missing":
                out.append(Reading(d.id, state.time_s, None, None, None))
                continue
            if fault == "stuck" and d.id in self._frozen:
                f = self._frozen[d.id]
                out.append(Reading(d.id, state.time_s, f.flow_vph, f.speed_kmh, f.occupancy_pct))
                continue
            k = state.density[i]
            v = speed_kmh(self.net, state, i)
            q = min(k * v, cell_capacity(self.net, state, i) or c.capacity_vph)
            occ = density_to_occupancy(k / c.lanes)
            jitter = (lambda x: max(0.0, x * (1.0 + self._rng.gauss(0.0, self.noise)))) if self.noise > 0 else (lambda x: x)
            r = Reading(d.id, state.time_s, round(jitter(q), 1), round(min(jitter(v), 160.0), 1), round(min(jitter(occ), 100.0), 2))
            if fault == "spike":
                r = Reading(d.id, state.time_s, 99999.0, r.speed_kmh, r.occupancy_pct)
            if fault == "stuck" and (r.flow_vph or 0) > 0:
                self._frozen[d.id] = r  # freezes at the first non-zero reading
            out.append(r)
        return out


@dataclass(frozen=True)
class QualityResult:
    reading: Reading
    ok: bool
    flags: tuple[str, ...]


@dataclass
class QualityChecker:
    """Rule-based checks. Thresholds follow common traffic-data practice and are configurable."""

    net: Network
    max_age_s: float = 120.0
    stuck_repeats: int = 4
    max_flow_per_lane: float = 3000.0
    max_speed_kmh: float = 160.0
    _history: dict[str, list[tuple[float | None, float | None, float | None]]] = field(default_factory=dict)

    def check(self, r: Reading, now_s: float) -> QualityResult:
        flags: list[str] = []
        try:
            cell = self.net.cells[self.net.cell_index(next(d.cell for d in self.net.detectors if d.id == r.detector_id))]
        except StopIteration:
            return QualityResult(r, False, ("unknown_detector",))
        if r.flow_vph is None or r.speed_kmh is None or r.occupancy_pct is None:
            return QualityResult(r, False, ("missing",))
        if now_s - r.timestamp_s > self.max_age_s:
            flags.append("stale")
        if r.timestamp_s - now_s > 5.0:
            flags.append("future_timestamp")
        if not 0 <= r.flow_vph <= self.max_flow_per_lane * cell.lanes:
            flags.append("flow_out_of_range")
        if not 0 <= r.speed_kmh <= self.max_speed_kmh:
            flags.append("speed_out_of_range")
        if not 0 <= r.occupancy_pct <= 100:
            flags.append("occupancy_out_of_range")
        if not flags and r.speed_kmh > 15 and r.flow_vph > 300:
            k_flow = r.flow_vph / r.speed_kmh / cell.lanes
            k_occ = occupancy_to_density(r.occupancy_pct)
            if k_occ > 0 and not 0.4 <= k_flow / k_occ <= 2.5:
                flags.append("inconsistent")
        hist = self._history.setdefault(r.detector_id, [])
        hist.append((r.flow_vph, r.speed_kmh, r.occupancy_pct))
        del hist[: -self.stuck_repeats]
        if len(hist) == self.stuck_repeats and len(set(hist)) == 1 and (r.flow_vph or 0) > 0:
            flags.append("stuck")
        return QualityResult(r, not flags, tuple(flags))

    def check_all(self, readings: list[Reading], now_s: float) -> list[QualityResult]:
        return [self.check(r, now_s) for r in readings]
