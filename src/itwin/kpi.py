"""Operational KPIs and level of service."""

from __future__ import annotations

from .ctm import State, speed_kmh
from .network import Network

# HCM basic freeway segment LOS thresholds, converted from pc/mi/ln to veh/km/ln.
# Mixed traffic is treated as passenger cars here (no heavy-vehicle adjustment).
_LOS = [(6.8, "A"), (11.2, "B"), (16.2, "C"), (21.7, "D"), (28.0, "E")]


def level_of_service(density_per_lane: float) -> str:
    for limit, los in _LOS:
        if density_per_lane <= limit:
            return los
    return "F"


def travel_time_min(net: Network, state: State) -> float:
    return sum(c.length_km / max(speed_kmh(net, state, i), 1.0) for i, c in enumerate(net.cells)) * 60.0


def snapshot(net: Network, state: State) -> dict:
    cells = []
    for i, c in enumerate(net.cells):
        k = state.density[i]
        v = speed_kmh(net, state, i)
        open_lanes = max(c.lanes - state.lanes_closed[i], 0)
        cells.append(
            {
                "id": c.id,
                "km": round(net.position_km(i), 2),
                "lanes": c.lanes,
                "lanes_open": open_lanes,
                "density_vpkpl": round(k / c.lanes, 1),
                "speed_kmh": round(v, 1),
                "flow_vph": round(k * v),
                "los": level_of_service(k / c.lanes),
            }
        )
    entries = []
    for e in net.entries:
        es = state.entries[e.id]
        arrivals = es.last_arrivals_vph
        wait_min = es.queue / arrivals * 60.0 if arrivals > 1 else 0.0
        entries.append(
            {
                "id": e.id,
                "name": e.name,
                "km": round(net.position_km(e.boundary), 2),
                "queue_veh": round(es.queue, 1),
                "queue_etc_veh": round(es.queue_etc, 1) if e.plaza else None,
                "queue_manual_veh": round(es.queue_manual, 1) if e.plaza else None,
                "arrivals_vph": round(arrivals),
                "flow_vph": round(es.last_flow_vph),
                "meter_vph": round(es.meter_vph) if es.meter_vph is not None else None,
                "est_wait_min": round(wait_min, 1),
                "queue_limit_veh": e.max_queue_veh,
            }
        )
    t = state.totals
    vf = net.cells[0].free_flow_kmh
    return {
        "time_s": state.time_s,
        "clock": clock(state.time_s),
        "travel_time_min": round(travel_time_min(net, state), 2),
        "free_flow_travel_time_min": round(net.free_flow_travel_time_min(), 2),
        "mean_speed_kmh": round(t.vkt / t.vht_mainline, 1) if t.vht_mainline > 0 else vf,
        "vehicles_on_mainline": round(sum(k * c.length_km for k, c in zip(state.density, net.cells, strict=True))),
        "totals": {
            "vkt": round(t.vkt),
            "vht": round(t.vht, 1),
            "vht_queues": round(t.vht_queues, 1),
            "delay_vh": round(max(0.0, t.vht_mainline - t.vkt / vf) + t.vht_queues, 1),
            "entered": round(t.entered),
            "exited": round(t.exited),
        },
        "cells": cells,
        "entries": entries,
    }


def clock(seconds: float) -> str:
    s = int(round(seconds)) % 86400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
