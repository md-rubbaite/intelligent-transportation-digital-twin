from itwin.control import MeterConfig, RampMetering, default_metering
from itwin.ctm import initial_state, step
from itwin.demand import Demand, morning_peak
from itwin.kpi import travel_time_min


def meter_for(net, **kw):
    return RampMetering([MeterConfig("E1", "C07", **kw)])


def test_rate_falls_when_density_above_target(net):
    st = initial_state(net)
    m = meter_for(net)
    st.density[7] = net.cells[7].critical_density * 1.5
    m.apply(net, st)
    assert st.entries["E1"].meter_vph < net.entry("E1").ramp_capacity_vph


def test_rate_rises_when_density_below_target(net):
    st = initial_state(net)
    m = meter_for(net)
    st.entries["E1"].meter_vph = 600
    st.time_s += 60
    m.apply(net, st)
    assert st.entries["E1"].meter_vph > 600


def test_rate_is_clamped(net):
    st = initial_state(net)
    m = meter_for(net, min_rate_vph=300)
    st.density[7] = net.cells[7].jam_density
    m.apply(net, st)
    assert st.entries["E1"].meter_vph == 300


def test_rate_updates_only_once_per_interval(net):
    st = initial_state(net)
    m = meter_for(net)
    st.density[7] = net.cells[7].critical_density * 1.5
    m.apply(net, st)
    first = st.entries["E1"].meter_vph
    st.density[7] = net.cells[7].jam_density
    st.time_s += 10
    m.apply(net, st)
    assert st.entries["E1"].meter_vph == first


def test_queue_override_releases_the_ramp(net):
    st = initial_state(net)
    m = meter_for(net)
    st.density[7] = net.cells[7].jam_density
    m.apply(net, st)
    st.entries["E1"].queue_manual = net.entry("E1").max_queue_veh + 1
    st.time_s += 10
    m.apply(net, st)  # within the interval, but the override acts at once
    assert st.entries["E1"].meter_vph == net.entry("E1").ramp_capacity_vph


def test_disabled_metering_clears_rates(net):
    st = initial_state(net)
    m = meter_for(net)
    st.entries["E1"].meter_vph = 500
    m.disable(st)
    assert st.entries["E1"].meter_vph is None
    m.apply(net, st)
    assert st.entries["E1"].meter_vph is None


def peak_run(net, metering):
    st = initial_state(net, 6 * 3600)
    d = Demand(morning_peak())
    worst = 0.0
    while st.time_s < 11 * 3600:
        if metering:
            metering.apply(net, st)
        step(net, st, d.at(st.time_s))
        worst = max(worst, travel_time_min(net, st))
    return st, worst


def test_metering_protects_the_mainline_over_the_peak(net):
    base, tt_base = peak_run(net, None)
    met, tt_met = peak_run(net, default_metering())
    assert met.totals.vht_mainline < base.totals.vht_mainline * 0.97
    assert tt_met < tt_base
    # The cost moves to the ramps; total vehicle-hours must not get worse.
    assert met.totals.vht <= base.totals.vht * 1.001
    assert met.totals.vht_queues > 0
