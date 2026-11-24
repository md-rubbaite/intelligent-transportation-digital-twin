import pytest

from itwin.ctm import initial_state
from itwin.sensors import QualityChecker, Reading, SyntheticDetectors, density_to_occupancy, occupancy_to_density


def good(det="D04", t=100.0, flow=3000.0, speed=80.0, occ=None):
    occ = density_to_occupancy(flow / speed / 3) if occ is None else occ
    return Reading(det, t, flow, speed, occ)


def test_occupancy_round_trip():
    assert occupancy_to_density(density_to_occupancy(20.0)) == pytest.approx(20.0)


def test_good_reading_passes(net):
    assert QualityChecker(net).check(good(), 100).ok


@pytest.mark.parametrize(
    ("reading", "flag"),
    [
        (Reading("D04", 100, None, None, None), "missing"),
        (good(flow=99999), "flow_out_of_range"),
        (good(speed=300), "speed_out_of_range"),
        (good(occ=140), "occupancy_out_of_range"),
        (good(t=-500), "stale"),
        (good(t=900), "future_timestamp"),
        (good(flow=3000, speed=80, occ=60), "inconsistent"),
        (Reading("D99", 100, 1, 1, 1), "unknown_detector"),
    ],
)
def test_bad_readings_are_flagged(net, reading, flag):
    r = QualityChecker(net).check(reading, 100)
    assert not r.ok and flag in r.flags


def test_stuck_detector_is_flagged_after_repeats(net):
    q = QualityChecker(net, stuck_repeats=4)
    results = [q.check(good(t=100 + 30 * i), 100 + 30 * i) for i in range(4)]
    assert [r.ok for r in results] == [True, True, True, False]
    assert "stuck" in results[-1].flags


def test_zero_flow_repeats_are_not_stuck(net):
    q = QualityChecker(net)
    results = [q.check(Reading("D04", 100 + 30 * i, 0.0, 80.0, 0.0), 100 + 30 * i) for i in range(6)]
    assert all(r.ok for r in results)


def test_synthetic_detectors_read_the_state(net):
    st = initial_state(net)
    st.density[net.cell_index("C04")] = 60.0  # 20 veh/km/lane, free flow
    rs = {r.detector_id: r for r in SyntheticDetectors(net, noise=0.0).read(st)}
    r = rs["D04"]
    assert r.speed_kmh == pytest.approx(80)
    assert r.flow_vph == pytest.approx(4800)
    assert occupancy_to_density(r.occupancy_pct) == pytest.approx(20, rel=0.01)


def test_synthetic_faults(net):
    st = initial_state(net)
    st.density = [30.0] * net.n_cells
    det = SyntheticDetectors(net, noise=0.0, faults={"D01": "missing", "D04": "spike", "D07": "stuck"})
    first = {r.detector_id: r for r in det.read(st)}
    st.density = [90.0] * net.n_cells
    second = {r.detector_id: r for r in det.read(st)}
    assert first["D01"].flow_vph is None
    assert first["D04"].flow_vph == 99999.0
    assert second["D07"].flow_vph == first["D07"].flow_vph  # frozen
    assert second["D10"].flow_vph != first["D10"].flow_vph
