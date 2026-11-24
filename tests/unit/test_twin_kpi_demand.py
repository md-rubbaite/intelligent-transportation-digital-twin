import pytest

from itwin.ctm import initial_state, speed_kmh
from itwin.demand import Demand, PeakProfile, morning_peak
from itwin.kpi import clock, level_of_service, snapshot, travel_time_min
from itwin.scenario import Scenario
from itwin.twin import DigitalTwin, SyntheticRoad, run_closed_loop


def mae(net, a, b):
    return sum(abs(speed_kmh(net, a, i) - speed_kmh(net, b, i)) for i in range(net.n_cells)) / net.n_cells


def test_peak_profile():
    p = PeakProfile(1000, 3000, 8 * 3600, 1800)
    assert p.at(8 * 3600) == 3000
    assert 1000 < p.at(7 * 3600) < 3000
    assert p.at(0) == pytest.approx(1000)


def test_demand_noise_is_reproducible():
    a = Demand(morning_peak(), noise=0.05, seed=3)
    b = Demand(morning_peak(), noise=0.05, seed=3)
    assert [a.at(t) for t in range(0, 600, 60)] == [b.at(t) for t in range(0, 600, 60)]


@pytest.mark.parametrize(("k", "los"), [(5, "A"), (10, "B"), (15, "C"), (20, "D"), (25, "E"), (40, "F")])
def test_level_of_service(k, los):
    assert level_of_service(k) == los


def test_clock():
    assert clock(8 * 3600 + 5 * 60 + 7) == "08:05:07"


def test_snapshot_fields(net):
    st = initial_state(net)
    s = snapshot(net, st)
    assert s["travel_time_min"] == pytest.approx(travel_time_min(net, st)) == pytest.approx(9.0)
    assert len(s["cells"]) == 24 and len(s["entries"]) == 4
    assert s["entries"][0]["queue_etc_veh"] == 0


def test_twin_tracks_the_road_through_the_peak(net):
    twin, road = DigitalTwin(net), SyntheticRoad(net)
    worst = 0.0
    t = 6 * 3600
    while t < 9.5 * 3600:
        t += 900
        run_closed_loop(twin, road, t)
        worst = max(worst, mae(net, twin.state, road.state))
    assert worst < 8.0
    assert mae(net, twin.state, road.state) < 3.0


def test_demand_correction_moves_toward_the_real_level(net):
    twin, road = DigitalTwin(net), SyntheticRoad(net, demand_factor=1.1)
    run_closed_loop(twin, road, 7.5 * 3600)
    assert all(1.03 < v < 1.2 for v in twin.correction.values())


def test_stuck_sensor_alert_and_health(net):
    twin, road = DigitalTwin(net), SyntheticRoad(net, detector_faults={"D10": "stuck"})
    run_closed_loop(twin, road, 7 * 3600)
    status = {s["id"]: s for s in twin.status()["sensors"]}
    assert status["D10"]["status"] == "fault" and "stuck" in status["D10"]["flags"]
    assert status["D04"]["status"] == "ok"
    assert any(a["kind"] == "sensor" and a["detector"] == "D10" for a in twin.alerts)


def test_confirm_closure(net):
    twin = DigitalTwin(net)
    twin.confirm_closure("C14", 1)
    assert twin.closures() == {"C14": 1}
    assert twin.status()["confirmed_closures"] == {"C14": 1}
    twin.confirm_closure("C14", 0)
    assert twin.closures() == {}
    with pytest.raises(ValueError):
        twin.confirm_closure("C14", 4)


def test_twin_meters_follow_the_switch(net):
    twin, road = DigitalTwin(net), SyntheticRoad(net)
    run_closed_loop(twin, road, 8 * 3600)
    assert any(v is not None for v in twin.meter_commands().values())
    twin.set_metering(False)
    assert all(v is None for v in twin.meter_commands().values())


def test_what_if_from_the_twin(net):
    twin, road = DigitalTwin(net), SyntheticRoad(net)
    run_closed_loop(twin, road, 7.5 * 3600)
    t = twin.state.time_s
    r = twin.what_if(Scenario("demand", 30, demand_factor=1.2))
    assert r["delta"]["delay_vh"] > 0
    assert twin.state.time_s == t


def test_twin_asks_to_reopen_a_closure_the_road_no_longer_has(net):
    from itwin.twin import HiddenIncident

    start = 7.75 * 3600
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, incidents=[HiddenIncident("C14", 1, start, start + 900)])
    run_closed_loop(twin, road, start + 300)
    twin.confirm_closure("C14", 1)
    run_closed_loop(twin, road, start + 900 + 600)
    assert any(a["kind"] == "check_closure" and "C14" in a["message"] for a in twin.alerts)


def test_no_reopen_prompt_while_the_closure_is_real(net):
    from itwin.twin import HiddenIncident

    start = 7.75 * 3600
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, incidents=[HiddenIncident("C14", 1, start, start + 3600)])
    run_closed_loop(twin, road, start + 300)
    twin.confirm_closure("C14", 1)
    run_closed_loop(twin, road, start + 2400)
    assert not any(a["kind"] == "check_closure" for a in twin.alerts)


def test_confirmed_closure_explains_the_alarm(net):
    from itwin.twin import HiddenIncident

    start = 7.75 * 3600
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, incidents=[HiddenIncident("C14", 1, start, start + 3600)])
    run_closed_loop(twin, road, start + 300)
    assert any(a["kind"] == "possible_incident" for a in twin.alerts)
    twin.confirm_closure("C14", 1)
    run_closed_loop(twin, road, start + 1200)
    kinds = [a["kind"] for a in twin.alerts]
    assert "incident_explained" in kinds and "incident_cleared" not in kinds
