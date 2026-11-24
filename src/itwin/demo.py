"""Scripted end-to-end demo on the synthetic corridor (deterministic)."""

from __future__ import annotations

from .ctm import speed_kmh
from .kpi import clock
from .network import Network, load_network
from .plaza import lane_performance, lanes_needed
from .scenario import PlazaChange, Scenario
from .twin import DigitalTwin, HiddenIncident, SyntheticRoad, run_closed_loop

INCIDENT = HiddenIncident(cell="C14", lanes_closed=1, start_s=7.75 * 3600, end_s=8.25 * 3600)


def speed_mae(net: Network, twin: DigitalTwin, road: SyntheticRoad) -> float:
    errs = [abs(speed_kmh(net, twin.state, i) - speed_kmh(net, road.state, i)) for i in range(net.n_cells)]
    return sum(errs) / len(errs)


def run(out=print) -> dict:
    net = load_network()
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, incidents=[INCIDENT], detector_faults={"D10": "stuck"})
    result: dict = {}

    out(
        f"Network: {net.name}: {net.length_km:.1f} km, {net.n_cells} cells, {len(net.entries)} entries with toll plazas, "
        f"{len(net.exits)} exits, {len(net.detectors)} detectors"
    )
    out("All data is synthetic. The 'road' is a second simulation with 5% more demand, noise, a frozen detector and a hidden incident.\n")

    out("1. Plaza sizing at Entry A for the peak (Erlang C)")
    peak = 3900 * 1.05
    manual = peak * 0.45
    now = lane_performance(manual, 350, 6)
    need = lanes_needed(manual, 350, 60)
    out(
        f"   manual arrivals {manual:.0f} veh/h: 6 lanes -> utilisation {now.utilisation:.2f}, mean wait {now.mean_wait_s:.0f} s; "
        f"minimum for a mean wait under 60 s: {need.lanes} lanes"
    )
    result["sizing"] = {"lanes_now": 6, "wait_now_s": round(now.mean_wait_s), "lanes_needed": need.lanes}

    out("\n2. Run twin and road together 06:00 -> 07:45 (twin sets ramp meters on the road)")
    run_closed_loop(twin, road, 7.75 * 3600)
    sensor = [a for a in twin.alerts if a["kind"] == "sensor"]
    out(f"   speed error twin vs road: {speed_mae(net, twin, road):.1f} km/h (mean over cells)")
    out(f"   sensor alerts: {sensor[-1]['message'] if sensor else 'none'}")
    result["sensor_alert"] = bool(sensor)

    out("\n3. Hidden incident on the road at 07:45, km 7.0 (1 of 3 lanes closed). The twin is not told.")
    raised_at = None
    while road.state.time_s < 7.9 * 3600:
        run_closed_loop(twin, road, road.state.time_s + road.interval_s)
        alarms = [a for a in twin.alerts if a["kind"] == "possible_incident"]
        if alarms and raised_at is None:
            raised_at = road.state.time_s
            out(f"   {clock(raised_at)} ALARM: {alarms[0]['message']}")
    if raised_at is None:
        out("   no alarm raised")
    else:
        out(f"   detected {int((raised_at - INCIDENT.start_s) / 60)} min {int((raised_at - INCIDENT.start_s) % 60)} s after it started")
    result["detection_delay_s"] = None if raised_at is None else raised_at - INCIDENT.start_s

    out("\n4. Operator checks CCTV and confirms 1 lane closed at km 7.0; the twin applies it")
    twin.confirm_closure("C14", 1)
    run_closed_loop(twin, road, 8.0 * 3600)
    st = twin.status()
    out(
        f"   {st['clock']}: corridor travel time {st['travel_time_min']:.1f} min (free flow {st['free_flow_travel_time_min']:.1f}); "
        f"speed error vs road {speed_mae(net, twin, road):.1f} km/h"
    )
    slow = [c for c in st["cells"] if c["speed_kmh"] < 40]
    if slow:
        out(f"   slow section km {slow[0]['km']:.1f}–{slow[-1]['km'] + 0.5:.1f}, lowest speed {min(c['speed_kmh'] for c in slow):.0f} km/h")
    out("   entry queues: " + ", ".join(f"{e['id']} {e['queue_veh']:.0f} veh" for e in st["entries"]))
    result["tracking_mae_kmh"] = round(speed_mae(net, twin, road), 2)

    out("\n5. What-if from now, 60 min ahead. Baseline: the lane stays closed for the whole hour")
    scenarios = [
        Scenario("Lane reopened in 15 min", 60, reopen_after_min=15),
        Scenario("Lane reopened in 30 min", 60, reopen_after_min=30),
        Scenario("Lane reopened in 30 min, metering off", 60, reopen_after_min=30, metering=False),
        Scenario("Lane stays closed, 2 more manual lanes at Entry A", 60, plaza_changes=(PlazaChange("E0", manual_lanes=8),)),
        Scenario("Lane stays closed, demand 10% higher", 60, demand_factor=1.1),
    ]
    out(f"   {'scenario':<52}{'Δ delay (veh-h)':>16}{'Δ mean travel time':>20}")
    result["what_if"] = {}
    for sc in scenarios:
        r = twin.what_if(sc)
        d = r["delta"]
        out(f"   {sc.name:<52}{d['delay_vh']:>+16.0f}{d['mean_travel_time_min']:>+16.1f} min")
        if abs(d["delay_vh"]) < 1:
            out("      no measurable effect: this is not where the corridor is constrained")
        for w in r["warnings"]:
            out(f"      ! {w}")
        result["what_if"][sc.name] = d

    out("\n6. The road clears at 08:15. The twin keeps the closure until the operator reopens it")
    prompt = None
    while road.state.time_s < 8.6 * 3600 and prompt is None:
        run_closed_loop(twin, road, road.state.time_s + road.interval_s)
        prompt = next((a for a in twin.alerts if a["kind"] == "check_closure"), None)
    if prompt:
        out(f"   {prompt['time']} PROMPT: {prompt['message']}")
        twin.confirm_closure("C14", 0)
        run_closed_loop(twin, road, road.state.time_s + 600)
        out(f"   operator reopens; 10 min later speed error vs road {speed_mae(net, twin, road):.1f} km/h")
    result["reopen_prompt"] = prompt is not None

    t = road.state.totals
    balance = t.arrived - t.exited - road.state.vehicles_on_network(net)
    out(f"\n7. Conservation check on the road model: arrived − exited − on network = {balance:.6f} veh")
    result["conservation"] = balance
    return result


if __name__ == "__main__":  # pragma: no cover
    run()
