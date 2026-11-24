import pytest
from fastapi.testclient import TestClient

from itwin.api import KeyStore, Runner, create_app
from itwin.sensors import SyntheticDetectors
from itwin.twin import DigitalTwin, SyntheticRoad

VIEWER, PLANNER, OPERATOR = "viewer-key-0123456789abcdef", "planner-key-0123456789abcdef", "operator-key-0123456789abcdef"


@pytest.fixture
def twin(net):
    return DigitalTwin(net)


@pytest.fixture
def client(twin):
    keys = KeyStore(f"viewer:{VIEWER},planner:{PLANNER},operator:{OPERATOR}")
    with TestClient(create_app(twin, keys)) as c:
        yield c


def h(key):
    return {"x-api-key": key}


def test_health_and_ready(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["ready"] is True


def test_auth_is_required(client):
    assert client.get("/v1/state").status_code == 401
    assert client.get("/v1/state", headers=h("wrong-key-0123456789abcdef")).status_code == 401


def test_roles(client):
    assert client.get("/v1/whoami", headers=h(VIEWER)).json() == {"role": "viewer"}
    assert client.post("/v1/what-if", json={"name": "x"}, headers=h(VIEWER)).status_code == 403
    assert client.post("/v1/metering", json={"enabled": False}, headers=h(PLANNER)).status_code == 403
    assert client.post("/v1/metering", json={"enabled": False}, headers=h(OPERATOR)).status_code == 200


def test_network_and_state(client):
    n = client.get("/v1/network", headers=h(VIEWER)).json()
    assert n["length_km"] == 12.0 and len(n["cells"]) == 24 and n["entries"][0]["plaza"]["manual_lanes"] == 6
    s = client.get("/v1/state", headers=h(VIEWER)).json()
    assert s["clock"] == "06:00:00" and s["metering_enabled"] is True and len(s["sensors"]) == 8


def test_feed_mode_readings_move_the_twin(client, twin, net):
    road = SyntheticRoad(net)
    for _ in range(4):
        readings, counts = road.advance()
        body = {"readings": [r.__dict__ for r in readings], "plaza_counts_vph": counts, "now_s": road.state.time_s}
        r = client.post("/v1/readings", json=body, headers=h(OPERATOR))
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] == 8
    assert twin.state.time_s == road.state.time_s
    assert client.post("/v1/readings", json=body, headers=h(VIEWER)).status_code == 403


def test_readings_validation(client, twin):
    r = client.post(
        "/v1/readings", json={"readings": [{"detector_id": "D01", "timestamp_s": 30}], "plaza_counts_vph": {"E9": 1}}, headers=h(OPERATOR)
    )
    assert r.status_code == 422
    twin.forecast_to(7 * 3600)
    r = client.post("/v1/readings", json={"readings": [{"detector_id": "D01", "timestamp_s": 30}], "now_s": 60}, headers=h(OPERATOR))
    assert r.status_code == 409


def test_bad_readings_are_reported_not_used(client, twin):
    body = {
        "readings": [{"detector_id": "D01", "timestamp_s": 21630, "flow_vph": 99999, "speed_kmh": 80, "occupancy_pct": 10}],
        "now_s": 21630,
    }
    r = client.post("/v1/readings", json=body, headers=h(OPERATOR)).json()
    assert r["rejected"] == 1
    alerts = client.get("/v1/alerts", headers=h(VIEWER)).json()["alerts"]
    assert alerts[0]["kind"] == "sensor" and "flow_out_of_range" in alerts[0]["flags"]


def test_what_if(client, twin):
    twin.forecast_to(7.75 * 3600)
    body = {"name": "close", "horizon_min": 30, "closures": [{"cell": "C14", "lanes_closed": 1, "duration_min": 20}]}
    r = client.post("/v1/what-if", json=body, headers=h(PLANNER))
    assert r.status_code == 200
    j = r.json()
    assert j["delta"]["delay_vh"] > 0 and j["start_clock"] == "07:45:00"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "horizon_min": 1000},
        {"name": "x", "closures": [{"cell": "C14", "lanes_closed": 9}]},
        {"name": "x", "closures": [{"cell": "C99", "lanes_closed": 1}]},
        {"name": "x", "closures": [{"cell": "bad", "lanes_closed": 1}]},
        {"name": "x", "plaza_changes": [{"entry": "E7", "manual_lanes": 2}]},
    ],
)
def test_what_if_validation(client, body):
    assert client.post("/v1/what-if", json=body, headers=h(PLANNER)).status_code == 422


def test_confirm_closure(client, twin):
    r = client.post("/v1/closures", json={"cell": "C14", "lanes_closed": 1}, headers=h(OPERATOR))
    assert r.json() == {"confirmed_closures": {"C14": 1}}
    assert client.post("/v1/closures", json={"cell": "C14", "lanes_closed": 4}, headers=h(OPERATOR)).status_code == 422
    assert client.post("/v1/closures", json={"cell": "C14", "lanes_closed": 1}, headers=h(PLANNER)).status_code == 403
    assert "operator confirmed" in client.get("/v1/alerts", headers=h(VIEWER)).json()["alerts"][0]["message"]


def test_metering_switch(client, twin):
    client.post("/v1/metering", json={"enabled": False}, headers=h(OPERATOR))
    assert client.get("/v1/state", headers=h(VIEWER)).json()["metering_enabled"] is False


def test_sizing(client):
    r = client.post("/v1/plaza/sizing", json={"arrival_vph": 1850, "service_vphpl": 350, "target_wait_s": 60}, headers=h(PLANNER)).json()
    assert r["lanes"] == 6 and r["mean_wait_s"] <= 60


def test_metrics_and_history(client):
    client.get("/v1/state", headers=h(VIEWER))
    m = client.get("/metrics").text
    assert "itwin_travel_time_minutes 9.0" in m and 'itwin_entry_queue_vehicles{entry="E0"}' in m
    assert 'itwin_http_requests_total{method="GET",status="200"}' in m
    assert client.get("/v1/history", headers=h(VIEWER)).json() == {"points": []}


def test_dashboard_and_headers(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Digital Twin" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    r = client.get("/healthz", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_demo_mode_rejects_external_readings(net):
    twin = DigitalTwin(net)
    road = SyntheticRoad(net)
    runner = Runner(twin, road, speed=1000, start_s=6.5 * 3600, end_s=7 * 3600)
    app = create_app(twin, KeyStore(f"operator:{OPERATOR}"), runner)
    with TestClient(app) as c:
        assert twin.state.time_s >= 6.5 * 3600  # warmed up at startup
        r = c.post("/v1/readings", json={"readings": [{"detector_id": "D01", "timestamp_s": 1}]}, headers=h(OPERATOR))
        assert r.status_code == 409


@pytest.mark.parametrize("spec", ["", "viewer:short", "admin:" + "x" * 30, "nokey"])
def test_keystore_validation(spec):
    with pytest.raises(ValueError):
        KeyStore(spec)


def test_synthetic_detectors_used_by_feed_are_serialisable(net):
    from itwin.ctm import initial_state

    r = SyntheticDetectors(net).read(initial_state(net))[0]
    assert set(r.__dict__) == {"detector_id", "timestamp_s", "flow_vph", "speed_kmh", "occupancy_pct"}
