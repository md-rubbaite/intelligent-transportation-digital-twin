import pytest

from itwin.ctm import initial_state
from itwin.estimation import Assimilator, IncidentDetector
from itwin.sensors import QualityChecker, SyntheticDetectors
from itwin.twin import DigitalTwin, HiddenIncident, SyntheticRoad, run_closed_loop


def test_assimilation_pulls_model_toward_measurement(net):
    truth = initial_state(net)
    truth.density = [20.0 * c.lanes for c in net.cells]  # 20 veh/km/lane everywhere
    model = initial_state(net)
    model.density = [10.0 * c.lanes for c in net.cells]
    results = QualityChecker(net).check_all(SyntheticDetectors(net, noise=0.0).read(truth), truth.time_s)
    before = sum(abs(a - b) for a, b in zip(truth.density, model.density, strict=True))
    res = Assimilator(gain=0.6).apply(net, model, results)
    after = sum(abs(a - b) for a, b in zip(truth.density, model.density, strict=True))
    assert after == pytest.approx(before * 0.4, rel=0.01)
    assert res["D04"] == pytest.approx(30.0, rel=0.01)
    assert res["D22"] == pytest.approx(20.0, rel=0.01)


def test_rejected_readings_are_ignored(net):
    truth = initial_state(net)
    truth.density = [60.0] * net.n_cells
    model = initial_state(net)
    det = SyntheticDetectors(net, noise=0.0, faults={d.id: "spike" for d in net.detectors})
    results = QualityChecker(net).check_all(det.read(truth), 0)
    assert Assimilator().apply(net, model, results) == {}
    assert sum(model.density) == 0


def residuals(up, down, net):
    out = {d.id: 0.0 for d in net.detectors}
    out["D13"], out["D16"] = up * 3, down * 3
    return out


def test_alarm_needs_k_of_n_intervals(net):
    det = IncidentDetector(k=3, n=4)
    assert det.update(net, residuals(10, -3, net), 0) == []
    assert det.update(net, residuals(10, -3, net), 30) == []
    assert det.update(net, residuals(0, 0, net), 60) == []
    raised = det.update(net, residuals(10, -3, net), 90)
    assert len(raised) == 1 and (raised[0].upstream_detector, raised[0].downstream_detector) == ("D13", "D16")
    assert raised[0].since_s == 0
    assert det.update(net, residuals(10, -3, net), 120) == []  # not raised twice


def test_congestion_on_both_sides_is_not_an_incident(net):
    det = IncidentDetector()
    for t in range(10):
        assert det.update(net, residuals(12, 8, net), t * 30) == []


def test_alarm_clears_after_quiet_intervals(net):
    det = IncidentDetector(k=3, n=4, clear_after=3)
    for t in range(4):
        det.update(net, residuals(10, -3, net), t * 30)
    assert det.active
    for t in range(4, 4 + 4 + 2):  # window must empty first, then three quiet intervals
        det.update(net, residuals(0, 0, net), t * 30)
    assert not det.active
    assert det.cleared_last or not det.active


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_no_false_alarm_in_an_incident_free_peak(net, seed):
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, seed=seed)
    run_closed_loop(twin, road, 10 * 3600)
    assert not [a for a in twin.alerts if a["kind"] == "possible_incident"]


@pytest.mark.parametrize(("cell", "pair"), [("C14", ("D13", "D16")), ("C08", ("D07", "D10"))])
def test_hidden_incident_is_found_quickly_and_in_the_right_place(net, cell, pair):
    start = 7.75 * 3600
    twin = DigitalTwin(net)
    road = SyntheticRoad(net, incidents=[HiddenIncident(cell, 1, start, start + 1800)])
    run_closed_loop(twin, road, start + 600)
    alarms = [a for a in twin.alerts if a["kind"] == "possible_incident"]
    assert alarms, "incident not detected within 10 minutes"
    assert (alarms[-1]["upstream_detector"], alarms[-1]["downstream_detector"]) == pair
