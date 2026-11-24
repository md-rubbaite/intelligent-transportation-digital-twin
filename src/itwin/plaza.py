"""Toll plaza sizing with the Erlang C (M/M/c) queueing model.

The simulation treats plazas as fluid queues. For planning, the steady-state M/M/c
model gives the probability of waiting and the mean wait for a given number of lanes,
and the smallest number of lanes that meets a target wait. It is a first-order tool:
real arrivals are peaky and service times are not exponential, so results should be
checked against the simulation and field data.
"""

from __future__ import annotations

from dataclasses import dataclass


def erlang_c(servers: int, offered_load: float) -> float:
    """Probability that an arriving vehicle has to wait (Erlang C)."""
    if servers < 1:
        raise ValueError("servers must be >= 1")
    if offered_load < 0:
        raise ValueError("offered load must be >= 0")
    if offered_load == 0:
        return 0.0
    if offered_load >= servers:
        return 1.0
    # Numerically stable recursion through Erlang B.
    b = 1.0
    for k in range(1, servers + 1):
        b = offered_load * b / (k + offered_load * b)
    rho = offered_load / servers
    return b / (1.0 - rho + rho * b)


@dataclass(frozen=True)
class LaneResult:
    lanes: int
    utilisation: float
    p_wait: float
    mean_wait_s: float
    stable: bool


def lane_performance(arrival_vph: float, service_vphpl: float, lanes: int) -> LaneResult:
    a = arrival_vph / service_vphpl
    rho = a / lanes
    if rho >= 1.0:
        return LaneResult(lanes, rho, 1.0, float("inf"), False)
    pw = erlang_c(lanes, a)
    wq_h = pw / (lanes * service_vphpl - arrival_vph)
    return LaneResult(lanes, rho, pw, wq_h * 3600.0, True)


def lanes_needed(arrival_vph: float, service_vphpl: float, target_wait_s: float, max_lanes: int = 40) -> LaneResult:
    """Smallest number of lanes whose mean wait is within the target."""
    if target_wait_s <= 0:
        raise ValueError("target wait must be positive")
    for c in range(1, max_lanes + 1):
        r = lane_performance(arrival_vph, service_vphpl, c)
        if r.stable and r.mean_wait_s <= target_wait_s:
            return r
    raise ValueError(f"no configuration up to {max_lanes} lanes meets the target")
