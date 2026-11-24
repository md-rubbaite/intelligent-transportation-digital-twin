"""Ramp metering with PI-ALINEA.

ALINEA (Papageorgiou et al., 1991) adjusts the metering rate to keep the density just
downstream of the merge near a target. PI-ALINEA (Wang et al., 2014) adds a
proportional term so that the controlled cell can be a bottleneck further downstream.
Here densities are used instead of detector occupancy; the controlled density can come
from the model or from assimilated detector data.

Rate update every control interval:
    r(k) = r(k-1) - Kp * (rho(k) - rho(k-1)) + Ki * (rho* - rho(k))
clamped to [r_min, ramp capacity]. If the entry queue exceeds its limit the meter is
relaxed (queue override) so that the queue does not spill onto the city street.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ctm import State
from .network import Network


@dataclass(frozen=True)
class MeterConfig:
    entry_id: str
    control_cell: str
    target_ratio: float = 1.0  # rho* as a fraction of the critical density of the controlled cell
    kp_kmh: float = 60.0
    ki_kmh: float = 40.0
    min_rate_vph: float = 240.0


@dataclass
class RampMetering:
    configs: list[MeterConfig]
    interval_s: float = 60.0
    enabled: bool = True
    _last_density: dict[str, float] = field(default_factory=dict)
    _last_update: float | None = None

    def apply(self, net: Network, state: State, densities: list[float] | None = None) -> None:
        """Update meter rates in ``state`` if a control interval has elapsed.

        ``densities`` lets the caller pass estimated densities (for example from
        detectors) instead of the model state.
        """
        if not self.enabled:
            for cfg in self.configs:
                state.entries[cfg.entry_id].meter_vph = None
            return
        for cfg in self.configs:  # queue override is checked at every call, not only per interval
            spec = net.entry(cfg.entry_id)
            es = state.entries[cfg.entry_id]
            if spec.meterable and es.meter_vph is not None and es.queue >= spec.max_queue_veh:
                es.meter_vph = spec.ramp_capacity_vph
        if self._last_update is not None and state.time_s - self._last_update < self.interval_s - 1e-6:
            return
        self._last_update = state.time_s
        rho_all = densities if densities is not None else state.density
        for cfg in self.configs:
            spec = net.entry(cfg.entry_id)
            if not spec.meterable:
                continue
            i = net.cell_index(cfg.control_cell)
            cell = net.cells[i]
            target = cfg.target_ratio * cell.critical_density
            rho = rho_all[i]
            es = state.entries[cfg.entry_id]
            prev_rate = es.meter_vph if es.meter_vph is not None else spec.ramp_capacity_vph
            prev_rho = self._last_density.get(cfg.entry_id, rho)
            rate = prev_rate - cfg.kp_kmh * (rho - prev_rho) + cfg.ki_kmh * (target - rho)
            rate = min(max(rate, cfg.min_rate_vph), spec.ramp_capacity_vph)
            if es.queue >= spec.max_queue_veh:
                rate = spec.ramp_capacity_vph  # queue override
            es.meter_vph = rate
            self._last_density[cfg.entry_id] = rho

    def disable(self, state: State) -> None:
        self.enabled = False
        for cfg in self.configs:
            state.entries[cfg.entry_id].meter_vph = None


def default_metering() -> RampMetering:
    """Meters for the demo corridor. E3 watches C19, upstream of the lane drop at the end (remote bottleneck)."""
    return RampMetering(
        [
            MeterConfig("E1", "C07"),
            MeterConfig("E2", "C13"),
            MeterConfig("E3", "C19"),
        ]
    )
