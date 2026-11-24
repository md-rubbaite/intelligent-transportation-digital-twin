"""Reproduce the validation numbers quoted in the docs (synthetic runs only).

    python examples/validation.py

1. Incident detection: false alarms in incident-free peaks; detection delay and
   location for single-lane closures at different places.
2. Tracking: twin vs road speed error over the peak.
3. Ramp metering: mainline vs total vehicle-hours over the peak (open loop).
"""

from __future__ import annotations

import statistics

from itwin.control import default_metering
from itwin.ctm import initial_state, speed_kmh, step
from itwin.demand import Demand, morning_peak
from itwin.kpi import travel_time_min
from itwin.network import load_network
from itwin.twin import DigitalTwin, HiddenIncident, SyntheticRoad, run_closed_loop

net = load_network()


def mae(a, b):
    return sum(abs(speed_kmh(net, a, i) - speed_kmh(net, b, i)) for i in range(net.n_cells)) / net.n_cells


def detection() -> None:
    print("1. Incident detection")
    seeds = [21, 22, 23, 24, 25, 26, 27, 28]
    fa = 0
    for s in seeds:
        twin, road = DigitalTwin(net), SyntheticRoad(net, seed=s)
        run_closed_loop(twin, road, 10.5 * 3600)
        fa += any(a["kind"] == "possible_incident" for a in twin.alerts)
    print(f"   incident-free peaks (06:00-10:30): {fa} of {len(seeds)} runs with a false alarm")
    start = 7.75 * 3600
    rows = []
    for cell, s in [("C02", 31), ("C05", 32), ("C08", 33), ("C11", 34), ("C14", 35), ("C17", 36), ("C20", 37)]:
        twin = DigitalTwin(net)
        road = SyntheticRoad(net, seed=s, incidents=[HiddenIncident(cell, 1, start, start + 1800)])
        run_closed_loop(twin, road, start)
        ci = net.cell_index(cell)
        demand_ratio = road.state.boundary_flow[ci] / (net.cells[ci].capacity_vphpl * (net.cells[ci].lanes - 1))
        found = None
        while road.state.time_s < start + 1200 and found is None:
            run_closed_loop(twin, road, road.state.time_s + road.interval_s)
            found = next((a for a in twin.alerts if a["kind"] == "possible_incident"), None)
        if found:
            ci = net.cell_index(cell)
            lo, hi = found["between_km"]
            right = lo <= net.position_km(ci) < hi
            rows.append((cell, demand_ratio, round((road.state.time_s - start) / 60, 1), right))
        else:
            rows.append((cell, demand_ratio, None, False))
    for cell, ratio, delay, right in rows:
        result = (
            f"detected after {delay} min, location {'correct' if right else 'WRONG'}" if delay is not None else "not detected in 20 min"
        )
        km = net.position_km(net.cell_index(cell))
        print(f"   1 lane closed at {cell} (km {km:4.1f}), demand / remaining capacity {ratio:.2f}: {result}")


def tracking() -> None:
    print("2. Tracking (twin vs road, mean absolute speed error over cells, sampled every 5 min)")
    errs = []
    for s in (41, 42, 43):
        twin, road = DigitalTwin(net), SyntheticRoad(net, seed=s)
        t = 6 * 3600
        while t < 10.5 * 3600:
            t += 300
            run_closed_loop(twin, road, t)
            errs.append(mae(twin.state, road.state))
    p95 = sorted(errs)[int(0.95 * len(errs))]
    print(f"   median {statistics.median(errs):.1f} km/h, 95th percentile {p95:.1f} km/h, max {max(errs):.1f} km/h")


def metering() -> None:
    print("3. Ramp metering over the peak (06:00-11:00, model only)")
    for label, meter in (("off", None), ("PI-ALINEA", default_metering())):
        st = initial_state(net, 6 * 3600)
        d = Demand(morning_peak())
        worst = 0.0
        while st.time_s < 11 * 3600:
            if meter:
                meter.apply(net, st)
            step(net, st, d.at(st.time_s))
            worst = max(worst, travel_time_min(net, st))
        t = st.totals
        print(
            f"   {label:<10} mainline {t.vht_mainline:7.0f} veh-h, entry queues {t.vht_queues:5.0f} veh-h, total {t.vht:7.0f} veh-h, "
            f"worst travel time {worst:5.1f} min"
        )


if __name__ == "__main__":
    detection()
    tracking()
    metering()
