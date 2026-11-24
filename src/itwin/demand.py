"""Synthetic demand profiles for the entries (veh/h as a function of time of day)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PeakProfile:
    """Base demand plus a Gaussian peak. Times are seconds after midnight."""

    base_vph: float
    peak_vph: float
    peak_time_s: float
    spread_s: float

    def at(self, t: float) -> float:
        z = (t - self.peak_time_s) / self.spread_s
        return self.base_vph + (self.peak_vph - self.base_vph) * math.exp(-0.5 * z * z)


@dataclass
class Demand:
    profiles: dict[str, PeakProfile]
    factor: float = 1.0
    noise: float = 0.0  # relative standard deviation of random variation per call
    seed: int | None = None
    multipliers: dict[str, float] = field(default_factory=dict)  # per-entry correction, e.g. from plaza counts

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def at(self, t: float) -> dict[str, float]:
        out = {}
        for eid, p in self.profiles.items():
            v = p.at(t) * self.factor * self.multipliers.get(eid, 1.0)
            if self.noise > 0:
                v *= max(0.0, 1.0 + self._rng.gauss(0.0, self.noise))
            out[eid] = v
        return out

    def scaled(self, factor: float) -> Demand:
        """Deterministic copy with the overall level multiplied by ``factor`` (corrections kept)."""
        return Demand(dict(self.profiles), self.factor * factor, 0.0, None, dict(self.multipliers))


def morning_peak(peak_time_s: float = 8 * 3600) -> dict[str, PeakProfile]:
    """Illustrative morning peak for the demo corridor (synthetic numbers)."""
    spread = 40 * 60
    return {
        "E0": PeakProfile(2200, 3900, peak_time_s, spread),
        "E1": PeakProfile(450, 1150, peak_time_s, spread),
        "E2": PeakProfile(450, 1150, peak_time_s + 600, spread),
        "E3": PeakProfile(350, 950, peak_time_s + 900, spread),
    }
