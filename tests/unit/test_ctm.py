import random

import pytest

from itwin.ctm import initial_state, receiving, sending, speed_kmh, step
from itwin.network import parse_network


def run(net, state, demand, seconds):
    for _ in range(int(seconds / net.step_seconds)):
        step(net, state, demand)
    return state


def balance(net, st):
    t = st.totals
    return t.arrived - t.exited - st.vehicles_on_network(net)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_vehicles_are_conserved_under_random_demand_and_closures(net, seed):
    rng = random.Random(seed)
    st = initial_state(net)
    for k in range(1500):
        if k % 60 == 0:
            st.lanes_closed = [rng.choice([0, 0, 0, 1, 2, 3]) if rng.random() < 0.1 else 0 for _ in range(net.n_cells)]
        demand = {e.id: rng.uniform(0, 6000 if e.boundary == 0 else 2000) for e in net.entries}
        step(net, st, demand)
        assert min(st.density) >= 0
        assert all(k <= c.jam_density + 1e-9 for k, c in zip(st.density, net.cells, strict=True))
    assert balance(net, st) == pytest.approx(0.0, abs=1e-6)


def test_empty_road_stays_empty(net):
    st = run(net, initial_state(net), {}, 600)
    assert sum(st.density) == 0
    assert st.totals.vht == 0


def test_light_traffic_flows_freely(net):
    st = run(net, initial_state(net), {"E0": 1500, "E1": 300, "E2": 300, "E3": 300}, 3600)
    assert all(speed_kmh(net, st, i) == pytest.approx(80.0) for i in range(net.n_cells))
    assert all(e.queue < 1e-6 for e in st.entries.values())
    # Steady state: everything that enters leaves.
    assert sum(st.boundary_flow[-1:]) + sum(st.exit_flow.values()) == pytest.approx(2400, rel=1e-3)


def test_sending_and_receiving_functions(small):
    st = initial_state(small)
    c = small.cells[0]
    assert sending(small, st, 0) == 0
    assert receiving(small, st, 0) == c.capacity_vph
    st.density[0] = c.critical_density
    assert sending(small, st, 0) == pytest.approx(c.capacity_vph)
    st.density[0] = c.jam_density
    assert receiving(small, st, 0) == pytest.approx(0)


def test_capacity_drop_in_congestion(net):
    st = initial_state(net)
    c = net.cells[5]
    st.density[5] = c.critical_density * 2
    assert sending(net, st, 5) == pytest.approx(c.capacity_vph * (1 - net.capacity_drop))


def test_full_closure_blocks_the_road(small):
    st = initial_state(small)
    st.lanes_closed[4] = 2
    run(small, st, {"E0": 3000}, 1800)
    assert st.boundary_flow[5] == 0
    assert st.density[3] > small.cells[3].critical_density  # queue upstream of the closure
    assert balance(small, st) == pytest.approx(0, abs=1e-6)


def test_lane_closure_reduces_discharge(small):
    st = initial_state(small)
    st.lanes_closed[4] = 1
    run(small, st, {"E0": 3500}, 3600)
    assert st.boundary_flow[5] == pytest.approx(2000, rel=0.02)


def test_exit_is_fifo(small_text):
    net = parse_network(small_text.replace("capacity_vph = 1800", "capacity_vph = 200"))
    st = run(net, initial_state(net), {"E0": 3000}, 1800)
    # The exit takes 20% but can only serve 200 veh/h, so the mainline upstream is held to 1000 veh/h.
    assert st.exit_flow["X1"] == pytest.approx(200, rel=0.02)
    assert st.boundary_flow[2] == pytest.approx(800, rel=0.02)


def test_merge_gives_ramp_its_priority_share(small):
    st = initial_state(small)
    st.lanes_closed[3] = 1  # bottleneck at the merge: 2000 veh/h
    run(small, st, {"E0": 3500, "E1": 1200}, 3600)
    ramp = st.entries["E1"].last_flow_vph
    assert ramp >= 0.3 * 2000 * 0.99 - 1


def test_toll_plaza_queue_grows_at_excess_rate(small):
    st = initial_state(small)
    # Manual lane serves 300 veh/h; manual arrivals are 50% of 1000 = 500 veh/h.
    run(small, st, {"E0": 0, "E1": 1000}, 3600)
    assert st.entries["E1"].queue_manual == pytest.approx(200, rel=0.03)
    assert st.entries["E1"].queue_etc == pytest.approx(0, abs=1e-6)


def test_meter_limits_ramp_flow(small):
    st = initial_state(small)
    st.entries["E1"].meter_vph = 400
    run(small, st, {"E1": 800}, 1800)
    assert st.entries["E1"].last_flow_vph == pytest.approx(400)
    assert st.entries["E1"].queue > 150


def test_state_copy_is_independent(net):
    st = initial_state(net)
    cp = st.copy()
    step(net, cp, {"E0": 3000})
    assert st.time_s == initial_state(net).time_s and sum(st.density) == 0
