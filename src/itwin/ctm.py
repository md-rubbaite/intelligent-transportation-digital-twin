"""Cell Transmission Model (Daganzo 1994/1995) with entries, toll plazas and exits.

Extensions used here, all standard in the literature:
- triangular fundamental diagram per cell, capacity scaled by open lanes;
- capacity drop: a congested cell discharges at (1 - drop) x capacity;
- FIFO diverge at exits (an exit that cannot take its share holds the mainline back);
- priority merge at entries (Daganzo's merge rule with a ramp priority share);
- point queues at entries, split into ETC and manual queues at toll plazas;
- an optional metering rate per entry.

The state is plain data so that it can be copied cheaply for what-if runs.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .network import Network

EPS = 1e-9


@dataclass
class EntryState:
    queue_etc: float = 0.0  # vehicles waiting at ETC lanes (or the only queue when there is no plaza)
    queue_manual: float = 0.0
    meter_vph: float | None = None  # None = metering off
    last_flow_vph: float = 0.0
    last_arrivals_vph: float = 0.0

    @property
    def queue(self) -> float:
        return self.queue_etc + self.queue_manual


@dataclass
class Totals:
    arrived: float = 0.0  # vehicles that arrived at entries (including those still queued)
    entered: float = 0.0
    exited: float = 0.0
    vkt: float = 0.0  # vehicle-km travelled on the mainline
    vht_mainline: float = 0.0  # vehicle-hours on the mainline
    vht_queues: float = 0.0  # vehicle-hours waiting at entries and plazas
    exits: dict[str, float] = field(default_factory=dict)

    @property
    def vht(self) -> float:
        return self.vht_mainline + self.vht_queues


@dataclass
class State:
    time_s: float
    density: list[float]  # veh/km per cell
    lanes_closed: list[int]
    entries: dict[str, EntryState]
    totals: Totals = field(default_factory=Totals)
    boundary_flow: list[float] = field(default_factory=list)  # veh/h through each boundary (mainline, after exits)
    exit_flow: dict[str, float] = field(default_factory=dict)

    def copy(self) -> State:
        return copy.deepcopy(self)

    def vehicles_on_network(self, net: Network) -> float:
        return sum(k * c.length_km for k, c in zip(self.density, net.cells, strict=True)) + sum(e.queue for e in self.entries.values())


def initial_state(net: Network, time_s: float = 0.0) -> State:
    return State(
        time_s=time_s,
        density=[0.0] * net.n_cells,
        lanes_closed=[0] * net.n_cells,
        entries={e.id: EntryState() for e in net.entries},
        boundary_flow=[0.0] * (net.n_cells + 1),
    )


def cell_capacity(net: Network, state: State, i: int) -> float:
    c = net.cells[i]
    open_lanes = max(c.lanes - state.lanes_closed[i], 0)
    return c.capacity_vphpl * open_lanes


def sending(net: Network, state: State, i: int) -> float:
    c = net.cells[i]
    q = cell_capacity(net, state, i)
    k = state.density[i]
    free = c.free_flow_kmh * k
    if q <= EPS:
        return 0.0
    if k > q / c.free_flow_kmh + EPS:  # congested: queue discharge rate
        return q * (1.0 - net.capacity_drop)
    return min(free, q)


def receiving(net: Network, state: State, i: int) -> float:
    c = net.cells[i]
    q = cell_capacity(net, state, i)
    return max(0.0, min(q, c.wave_speed_kmh * (c.jam_density - state.density[i])))


def speed_kmh(net: Network, state: State, i: int) -> float:
    c = net.cells[i]
    k = state.density[i]
    if k < 1e-3:
        return c.free_flow_kmh
    flow = min(c.free_flow_kmh * k, cell_capacity(net, state, i), c.wave_speed_kmh * (c.jam_density - k))
    return max(0.0, min(c.free_flow_kmh, flow / k))


def _entry_potential(net: Network, es: EntryState, entry_id: str, arrivals: float, dt_h: float) -> tuple[float, float, float]:
    """Return (total sending, etc part, manual part) the entry could release this step."""
    spec = net.entry(entry_id)
    if spec.plaza is None:
        pot = es.queue_etc / dt_h + arrivals
        etc, man = pot, 0.0
    else:
        p = spec.plaza
        etc = min(es.queue_etc / dt_h + arrivals * p.etc_share, p.etc_capacity_vph)
        man = min(es.queue_manual / dt_h + arrivals * (1.0 - p.etc_share), p.manual_capacity_vph)
    total = etc + man
    cap = spec.ramp_capacity_vph
    if spec.meterable and es.meter_vph is not None:
        cap = min(cap, es.meter_vph)
    return min(total, cap), etc, man


def step(net: Network, state: State, demand_vph: dict[str, float]) -> State:
    """Advance the state by one model step (in place) and return it."""
    dt_h = net.step_seconds / 3600.0
    n = net.n_cells
    S = [sending(net, state, i) for i in range(n)]
    R = [receiving(net, state, i) for i in range(n)]
    inflow = [0.0] * n
    outflow = [0.0] * n
    exit_flow: dict[str, float] = {}
    entry_flow: dict[str, tuple[float, float, float]] = {}  # id -> (flow, etc potential, manual potential)
    boundary_flow = [0.0] * (n + 1)

    for b in range(n + 1):
        entry = net.entry_at(b)
        sr, etc_pot, man_pot = 0.0, 0.0, 0.0
        if entry is not None:
            arrivals = max(0.0, demand_vph.get(entry.id, 0.0))
            state.entries[entry.id].last_arrivals_vph = arrivals
            sr, etc_pot, man_pot = _entry_potential(net, state.entries[entry.id], entry.id, arrivals, dt_h)

        if b == 0:
            fr = min(sr, R[0])
            inflow[0] = fr
            boundary_flow[0] = fr
            if entry is not None:
                entry_flow[entry.id] = (fr, etc_pot, man_pot)
            continue

        s_up = S[b - 1]
        if b == n:
            y = min(s_up, net.downstream_capacity_vph)
            outflow[n - 1] = y
            boundary_flow[n] = y
            continue

        ex = net.exit_at(b)
        beta = ex.split_ratio if ex else 0.0
        if ex and beta > 0:
            s_up = min(s_up, ex.capacity_vph / beta)
        mainline = (1.0 - beta) * s_up
        rb = R[b]
        if entry is None or sr <= EPS:
            fm, fr = min(mainline, rb), 0.0
        elif mainline + sr <= rb:
            fm, fr = mainline, sr
        else:
            p = entry.merge_priority
            fm = min(mainline, max(rb - sr, (1.0 - p) * rb))
            fr = min(sr, rb - fm)
        y_up = fm / (1.0 - beta) if beta < 1.0 else 0.0
        outflow[b - 1] = y_up
        inflow[b] = fm + fr
        boundary_flow[b] = fm
        if ex:
            exit_flow[ex.id] = beta * y_up
        if entry is not None:
            entry_flow[entry.id] = (fr, etc_pot, man_pot)

    # Totals before the density update use the step's flows.
    tot = state.totals
    for i, c in enumerate(net.cells):
        tot.vkt += 0.5 * (inflow[i] + outflow[i]) * c.length_km * dt_h
        tot.vht_mainline += state.density[i] * c.length_km * dt_h
    tot.vht_queues += sum(e.queue for e in state.entries.values()) * dt_h

    for i, c in enumerate(net.cells):
        k = state.density[i] + dt_h / c.length_km * (inflow[i] - outflow[i])
        state.density[i] = min(max(k, 0.0), c.jam_density)

    for eid, (flow, etc_pot, man_pot) in entry_flow.items():
        es = state.entries[eid]
        spec = net.entry(eid)
        arrivals = es.last_arrivals_vph
        pot = etc_pot + man_pot
        share_etc = etc_pot / pot if pot > EPS else 0.0
        f_etc, f_man = flow * share_etc, flow * (1.0 - share_etc)
        if spec.plaza is None:
            es.queue_etc = max(0.0, es.queue_etc + (arrivals - flow) * dt_h)
        else:
            eta = spec.plaza.etc_share
            es.queue_etc = max(0.0, es.queue_etc + (arrivals * eta - f_etc) * dt_h)
            es.queue_manual = max(0.0, es.queue_manual + (arrivals * (1.0 - eta) - f_man) * dt_h)
        es.last_flow_vph = flow
        tot.entered += flow * dt_h
        tot.arrived += arrivals * dt_h

    for xid, f in exit_flow.items():
        tot.exits[xid] = tot.exits.get(xid, 0.0) + f * dt_h
        tot.exited += f * dt_h
    tot.exits["downstream"] = tot.exits.get("downstream", 0.0) + boundary_flow[n] * dt_h
    tot.exited += boundary_flow[n] * dt_h

    state.boundary_flow = boundary_flow
    state.exit_flow = exit_flow
    state.time_s += net.step_seconds
    return state
