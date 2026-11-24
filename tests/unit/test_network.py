import pytest

from itwin.network import NetworkError, parse_network


def test_default_network_shape(net):
    assert net.n_cells == 24
    assert net.length_km == pytest.approx(12.0)
    assert [e.id for e in net.entries] == ["E0", "E1", "E2", "E3"]
    assert [x.id for x in net.exits] == ["X1", "X2", "X3"]
    assert len(net.detectors) == 8
    assert net.cells[22].lanes == 2 and net.cells[0].lanes == 3


def test_triangular_fundamental_diagram(net):
    c = net.cells[0]
    assert c.capacity_vph == 6000
    assert c.critical_density == pytest.approx(75.0)
    assert c.wave_speed_kmh == pytest.approx(2000 / (150 - 25))
    # Capacity is reached on both branches at the critical density.
    assert c.free_flow_kmh * c.critical_density == pytest.approx(c.wave_speed_kmh * (c.jam_density - c.critical_density))


def test_free_flow_travel_time(net):
    assert net.free_flow_travel_time_min() == pytest.approx(12 / 80 * 60)


def test_cfl_condition_is_enforced(small_text):
    with pytest.raises(NetworkError, match="CFL"):
        parse_network(small_text.replace("step_seconds = 10", "step_seconds = 30"))


def test_unknown_keys_rejected(small_text):
    with pytest.raises(NetworkError, match="unknown keys"):
        parse_network(small_text.replace('name = "test corridor"', 'name = "x"\ncolour = "blue"'))


def test_upstream_entry_required(small_text):
    with pytest.raises(NetworkError, match="boundary 0"):
        parse_network(small_text.replace("boundary = 0", "boundary = 1"))


def test_unique_ids(small_text):
    with pytest.raises(NetworkError, match="unique"):
        parse_network(small_text.replace('id = "X1"', 'id = "E1"'))


def test_plaza_needs_matching_lanes(small_text):
    with pytest.raises(NetworkError, match="no ETC lanes"):
        parse_network(small_text.replace("etc_lanes = 1", "etc_lanes = 0"))


def test_detector_on_unknown_cell(small_text):
    with pytest.raises(NetworkError, match="unknown cell"):
        parse_network(small_text.replace('cell = "C04"', 'cell = "C99"'))


def test_jam_density_must_exceed_critical(small_text):
    with pytest.raises(NetworkError, match="jam density"):
        parse_network(
            small_text.replace("free_flow_kmh = 90", "free_flow_kmh = 20").replace("capacity_vphpl = 2000", "capacity_vphpl = 2600")
        )


def test_invalid_toml():
    with pytest.raises(NetworkError, match="invalid TOML"):
        parse_network("name = ")


def test_out_of_range_value(small_text):
    with pytest.raises(NetworkError, match="outside"):
        parse_network(small_text.replace("lanes = 2", "lanes = 12"))


def test_lanes_override_index_checked(small_text):
    with pytest.raises(NetworkError, match="invalid cell indexes"):
        parse_network(small_text.replace("jam_density_vpkpl = 150", 'jam_density_vpkpl = 150\nlanes_override = { "9" = 1 }'))
