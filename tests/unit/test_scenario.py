import pytest

from itwin.control import default_metering
from itwin.ctm import initial_state, step
from itwin.demand import Demand, morning_peak
from itwin.network import NetworkError
from itwin.scenario import Closure, PlazaChange, Scenario, apply_plaza_changes, what_if


@pytest.fixture
def start(net):
    st = initial_state(net, 7.5 * 3600)
    d = Demand(morning_peak())
    for _ in range(60):
        step(net, st, d.at(st.time_s))
    return st


def test_no_change_gives_zero_delta(net, start):
    r = what_if(net, start, Demand(morning_peak()), default_metering(), Scenario("same", 30))
    assert all(v == 0 for v in r["delta"].values())


def test_closure_adds_delay(net, start):
    r = what_if(net, start, Demand(morning_peak()), default_metering(), Scenario("close", 45, (Closure("C14", 1, 0, 30),)))
    assert r["delta"]["delay_vh"] > 50
    assert r["delta"]["mean_travel_time_min"] > 0


def test_earlier_reopening_is_better(net, start):
    start.lanes_closed[net.cell_index("C14")] = 1
    d, m = Demand(morning_peak()), default_metering()
    early = what_if(net, start, d, m, Scenario("15", 60, reopen_after_min=15))["delta"]["delay_vh"]
    late = what_if(net, start, d, m, Scenario("30", 60, reopen_after_min=30))["delta"]["delay_vh"]
    assert early < late < 0


def test_demand_correction_is_kept_in_scenarios(net, start):
    """Regression: scaled() used to drop per-entry corrections, so scenarios ran on less traffic."""
    d = Demand(morning_peak(), multipliers={"E0": 1.1, "E1": 1.1, "E2": 1.1, "E3": 1.1})
    assert d.scaled(1.0).at(8 * 3600) == d.at(8 * 3600)
    r = what_if(net, start, d, default_metering(), Scenario("same", 30, demand_factor=1.0))
    assert r["delta"]["delay_vh"] == 0


def test_higher_demand_adds_delay(net, start):
    r = what_if(net, start, Demand(morning_peak()), default_metering(), Scenario("more", 60, demand_factor=1.1))
    assert r["delta"]["delay_vh"] > 0


def test_plaza_change_is_applied(net):
    new = apply_plaza_changes(net, (PlazaChange("E0", manual_lanes=8),))
    assert new.entry("E0").plaza.manual_lanes == 8
    assert net.entry("E0").plaza.manual_lanes == 6  # original untouched


def test_extra_plaza_lanes_cut_a_plaza_queue(net):
    st = initial_state(net, 8 * 3600)
    st.entries["E0"].queue_manual = 300.0
    d = Demand(morning_peak())
    r = what_if(net, st, d, default_metering(), Scenario("plaza", 30, plaza_changes=(PlazaChange("E0", manual_lanes=9),)))
    assert r["scenario_result"]["vht_queues"] < r["baseline"]["vht_queues"]


def test_queue_warnings(net, start):
    r = what_if(net, start, Demand(morning_peak()), default_metering(), Scenario("bad", 60, (Closure("C10", 3, 0, 40),)))
    assert any("spillback" in w for w in r["warnings"])


@pytest.mark.parametrize(
    "sc",
    [
        Scenario("x", horizon_min=1),
        Scenario("x", demand_factor=5),
        Scenario("x", closures=(Closure("C14", 4),)),
        Scenario("x", closures=(Closure("C14", 1, -5),)),
        Scenario("x", horizon_min=30, reopen_after_min=45),
    ],
)
def test_invalid_scenarios(net, start, sc):
    with pytest.raises(ValueError):
        what_if(net, start, Demand(morning_peak()), default_metering(), sc)


def test_plaza_change_on_entry_without_plaza(small_text):
    from itwin.network import parse_network

    net = parse_network(small_text)
    with pytest.raises(NetworkError):
        Scenario("x", plaza_changes=(PlazaChange("E0", manual_lanes=2),)).validate(net)


def test_start_state_is_not_modified(net, start):
    before = (start.time_s, list(start.density), start.totals.vht)
    what_if(net, start, Demand(morning_peak()), default_metering(), Scenario("close", 30, (Closure("C14", 2),)))
    assert (start.time_s, list(start.density), start.totals.vht) == before
