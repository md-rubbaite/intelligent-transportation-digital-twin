import pytest

from itwin.plaza import erlang_c, lane_performance, lanes_needed


def test_erlang_c_known_values():
    assert erlang_c(1, 0.5) == pytest.approx(0.5)  # M/M/1: P(wait) = rho
    assert erlang_c(2, 1.0) == pytest.approx(1 / 3)
    assert erlang_c(3, 0) == 0
    assert erlang_c(2, 2.5) == 1.0


def test_mm1_mean_wait():
    r = lane_performance(arrival_vph=300, service_vphpl=400, lanes=1)
    # Wq = rho / (mu - lambda) hours
    assert r.mean_wait_s == pytest.approx(0.75 / 100 * 3600)


def test_unstable_configuration():
    r = lane_performance(900, 300, 3)
    assert not r.stable and r.mean_wait_s == float("inf")


def test_lanes_needed_is_minimal():
    r = lanes_needed(1850, 350, 60)
    assert r.stable and r.mean_wait_s <= 60
    assert lane_performance(1850, 350, r.lanes - 1).mean_wait_s > 60


def test_more_lanes_never_wait_longer():
    waits = [lane_performance(1000, 300, c).mean_wait_s for c in range(4, 10)]
    assert waits == sorted(waits, reverse=True)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        erlang_c(0, 1)
    with pytest.raises(ValueError):
        lanes_needed(100, 100, 0)
    with pytest.raises(ValueError, match="no configuration"):
        lanes_needed(100000, 10, 1, max_lanes=3)
