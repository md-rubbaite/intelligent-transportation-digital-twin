import pytest

from itwin.network import load_network, parse_network

SMALL = """
name = "test corridor"
step_seconds = 10
downstream_capacity_vph = 4000
capacity_drop = 0.0

[cells]
count = 6
length_km = 0.5
lanes = 2
free_flow_kmh = 90
capacity_vphpl = 2000
jam_density_vpkpl = 150

[[entries]]
id = "E0"
boundary = 0
ramp_capacity_vph = 4000

[[entries]]
id = "E1"
boundary = 3
ramp_capacity_vph = 1800
meterable = true
max_queue_veh = 50
merge_priority = 0.3
plaza = { etc_lanes = 1, manual_lanes = 1, etc_service_vphpl = 1000, manual_service_vphpl = 300, etc_share = 0.5 }

[[exits]]
id = "X1"
boundary = 2
split_ratio = 0.2
capacity_vph = 1800

[[detectors]]
id = "D1"
cell = "C01"

[[detectors]]
id = "D4"
cell = "C04"
"""


@pytest.fixture(scope="session")
def net():
    return load_network()


@pytest.fixture
def small():
    return parse_network(SMALL)


@pytest.fixture
def small_text():
    return SMALL
