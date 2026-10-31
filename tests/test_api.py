"""Public dashboard API."""

import numpy as np

from conftest import login
from crowdcount import __version__
from crowdcount.zones import ZoneStats


def publish(state, total, **zones):
    return state.publish(total=total, zones={n: ZoneStats(c, c) for n, c in zones.items()}, points=[(10, 10)])


def test_health_and_version(client):
    data = client.get("/api/health").json()
    assert data == {"status": "ok", "version": __version__, "detector": "offline"}


def test_dashboard_is_served_at_root_with_security_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>" in resp.text
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_legacy_static_urls_redirect(client):
    resp = client.get("/static/index.html", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/index.html"


def test_counts_and_zones(client):
    publish(client.state, 3, Entrance=2, Exit=0)
    count = client.get("/api/count").json()
    assert count["total_count"] == 3
    assert count["timestamp"] is not None
    zones = client.get("/api/zones").json()["zones"]
    assert zones["Entrance"] == {"current": 2, "total_visitors": 2, "threshold": None}
    assert zones["Exit"]["current"] == 0  # empty zones are reported, not dropped


def test_summary_includes_status_and_version(client):
    client.state.begin_run(width=640, height=480, source="lobby.mp4", model="yolov8n.pt")
    publish(client.state, 1)
    data = client.get("/api/summary").json()
    assert data["version"] == __version__
    assert data["status"]["state"] == "running"
    assert data["status"]["source"] == "lobby.mp4"
    assert data["detection_running"] is True


def test_history_limit_validation(client):
    assert client.get("/api/history?limit=0").status_code == 422
    assert client.get("/api/history?limit=10").json() == {"history": [], "count": 0}


def test_alerts_are_read_only_and_recorded_once(client, admin_headers):
    client.state.set_global_threshold(2)
    publish(client.state, 3)
    publish(client.state, 4)
    for _ in range(3):
        data = client.get("/api/alerts").json()
    assert data["has_alerts"] is True
    assert data["alerts"]["global"]["current"] == 4
    history = client.get("/api/admin/alerts/history", headers=admin_headers).json()
    assert history["count"] == 1  # edge-triggered: one breach, one record


def test_images_404_until_frames_exist(client):
    for path in ("/api/heatmap", "/api/snapshot"):
        assert client.get(path).status_code == 404


def test_images_after_frames(client):
    frame = np.zeros((48, 64, 3), np.uint8)
    client.state.begin_run(width=64, height=48)
    client.state.set_frames(frame, frame)
    publish(client.state, 1)
    for path in ("/api/heatmap", "/api/snapshot"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"


def test_heatmap_reset_requires_login(client, admin_headers):
    assert client.post("/api/heatmap/reset").status_code == 401
    assert client.post("/api/heatmap/reset", headers=admin_headers).json() == {"success": True}


def test_regular_users_can_reset_heatmap(client, admin_headers):
    client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "ops", "password": "operator-pass", "role": "user"},
    )
    headers = login(client, "ops", "operator-pass")
    assert client.post("/api/heatmap/reset", headers=headers).status_code == 200


def test_export_csv(client):
    assert client.get("/api/export/csv").status_code == 404
    publish(client.state, 1, A=1)
    publish(client.state, 2, A=2, B=1)
    resp = client.get("/api/export/csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.text.splitlines()[0] == "timestamp,total_count,zone_A,zone_B"


def test_export_pdf(client):
    publish(client.state, 2, A=1)
    resp = client.get("/api/export/pdf")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_websocket_pushes_summary(client):
    publish(client.state, 5)
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["total_count"] == 5


def test_old_public_threshold_endpoint_is_gone(client):
    assert client.post("/alerts/threshold?global_threshold=1").status_code in (404, 405)


def test_zone_layout(client, tmp_path):
    from crowdcount.zones import Zone, save_zones

    assert client.get("/api/zones/layout").json() == {"zones": []}
    save_zones(tmp_path / "zones.json", [Zone("Gate", [(0, 0), (9, 0), (9, 9)], (1, 2, 3))])
    (zone,) = client.get("/api/zones/layout").json()["zones"]
    assert zone["name"] == "Gate" and zone["color"] == [1, 2, 3]
