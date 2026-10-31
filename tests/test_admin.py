"""Authentication, roles and the admin API."""

import json

import pytest

from conftest import ADMIN_PASSWORD, login
from crowdcount.server.auth import TokenService, hash_password, verify_password
from crowdcount.server.models import UserInDB, UserRole
from crowdcount.zones import Zone, load_zones, save_zones

TRIANGLE = [[0, 0], [100, 0], [100, 80]]


class TestAuth:
    def test_first_start_creates_admin_from_env(self, client, tmp_path):
        users = json.loads((tmp_path / "data" / "users.json").read_text())["users"]
        assert [(u["username"], u["role"]) for u in users] == [("admin", "admin")]
        assert ADMIN_PASSWORD not in (tmp_path / "data" / "users.json").read_text()

    def test_generated_password_when_env_unset(self, make_client, monkeypatch, tmp_path, caplog):
        monkeypatch.delenv("CROWDCOUNT_ADMIN_PASSWORD")
        make_client()
        assert "Created the first admin account" in caplog.text
        assert "admin123" not in caplog.text

    def test_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert resp.status_code == 401

    def test_unknown_user(self, client):
        resp = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever"})
        assert resp.status_code == 401

    def test_me_and_logout_revokes_token(self, client, admin_headers):
        assert client.get("/api/auth/me", headers=admin_headers).json()["username"] == "admin"
        assert client.post("/api/auth/logout", headers=admin_headers).status_code == 200
        assert client.get("/api/auth/me", headers=admin_headers).status_code == 401

    def test_garbage_token(self, client):
        assert client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401

    def test_change_password(self, client, admin_headers):
        bad = client.post(
            "/api/auth/password",
            headers=admin_headers,
            json={"current_password": "x", "new_password": "new-password-1"},
        )
        assert bad.status_code == 400
        ok = client.post(
            "/api/auth/password",
            headers=admin_headers,
            json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-1"},
        )
        assert ok.status_code == 200
        login(client, "admin", "new-password-1")

    def test_jwt_secret_is_generated_and_persisted(self, client, tmp_path, make_client, admin_headers):
        secret_file = tmp_path / "data" / ".jwt_secret"
        assert secret_file.stat().st_mode & 0o077 == 0  # owner-only
        second = make_client()  # same data dir, new process-equivalent
        assert second.get("/api/auth/me", headers=admin_headers).status_code == 200

    def test_externally_generated_bcrypt_hashes_verify(self):
        # CrowdCount 1.x stored passlib bcrypt hashes; any standard $2a$/$2b$ hash must keep
        # working. This is the OpenBSD test vector for the password "U*U".
        vector = "$2a$05$CCCCCCCCCCCCCCCCCCCCC.E5YPO9kmyuRGyh0XouQYb4YMJKvyOeW"
        assert verify_password("U*U", vector)
        assert verify_password("U*U", vector.replace("$2a$", "$2b$", 1))
        assert not verify_password("U*V", vector)
        assert verify_password("pw", hash_password("pw"))
        assert not verify_password("pw", "not-a-hash")

    def test_expired_token(self):
        from datetime import timedelta

        tokens = TokenService("s" * 40, ttl=timedelta(seconds=-1))
        token, _ = tokens.issue(UserInDB(username="a", password_hash="x", role=UserRole.ADMIN))
        assert tokens.decode(token) is None


class TestRoles:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/admin/users"),
            ("get", "/api/admin/zones"),
            ("put", "/api/admin/config/thresholds"),
            ("get", "/api/admin/logs"),
            ("get", "/api/admin/logs/export/csv"),
        ],
    )
    def test_admin_endpoints_need_a_token(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    def test_regular_users_get_403(self, client, admin_headers):
        client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={"username": "viewer", "password": "viewer-pass", "role": "user"},
        )
        headers = login(client, "viewer", "viewer-pass")
        assert client.get("/api/admin/users", headers=headers).status_code == 403


class TestUsers:
    def test_create_duplicate_and_validation(self, client, admin_headers):
        body = {"username": "ops", "password": "operator-pass", "role": "user"}
        assert client.post("/api/admin/users", headers=admin_headers, json=body).status_code == 201
        assert client.post("/api/admin/users", headers=admin_headers, json=body).status_code == 409
        short = {**body, "username": "x2", "password": "short"}
        assert client.post("/api/admin/users", headers=admin_headers, json=short).status_code == 422
        long = {**body, "username": "x3", "password": "é" * 40}  # 80 bytes > bcrypt's 72
        assert client.post("/api/admin/users", headers=admin_headers, json=long).status_code == 422

    def test_cannot_delete_self_or_last_admin(self, client, admin_headers):
        me = client.get("/api/auth/me", headers=admin_headers).json()
        assert client.delete(f"/api/admin/users/{me['id']}", headers=admin_headers).status_code == 400
        other = client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={"username": "root2", "password": "second-admin", "role": "admin"},
        ).json()
        root2 = login(client, "root2", "second-admin")
        assert client.delete(f"/api/admin/users/{me['id']}", headers=root2).status_code == 200
        assert client.delete(f"/api/admin/users/{other['id']}", headers=root2).status_code == 400


class TestThresholds:
    def test_persisted_applied_live_and_reloaded(self, client, admin_headers, make_client, tmp_path):
        resp = client.put(
            "/api/admin/config/thresholds",
            headers=admin_headers,
            json={"global_threshold": 7, "zone_thresholds": {"Gate": 2}},
        )
        assert resp.status_code == 200
        assert client.state.get_global_threshold() == 7
        assert client.state.get_zone_thresholds() == {"Gate": 2}

        restarted = make_client()  # thresholds load from config.json at startup
        assert restarted.state.get_global_threshold() == 7
        assert restarted.state.get_zone_thresholds() == {"Gate": 2}

    def test_null_removes_zone_threshold(self, client, admin_headers):
        client.put(
            "/api/admin/config/thresholds", headers=admin_headers, json={"zone_thresholds": {"Gate": 2}}
        )
        client.put(
            "/api/admin/config/thresholds", headers=admin_headers, json={"zone_thresholds": {"Gate": None}}
        )
        assert (
            client.get("/api/admin/config/thresholds", headers=admin_headers).json()["zone_thresholds"] == {}
        )
        assert client.state.get_zone_thresholds() == {}

    def test_rejects_non_positive(self, client, admin_headers):
        for body in ({"global_threshold": 0}, {"zone_thresholds": {"A": 0}}):
            assert (
                client.put("/api/admin/config/thresholds", headers=admin_headers, json=body).status_code
                == 422
            )


class TestZones:
    def test_crud_writes_the_detector_zones_file(self, client, admin_headers, tmp_path):
        zones_file = tmp_path / "zones.json"
        resp = client.post(
            "/api/admin/zones", headers=admin_headers, json={"name": "Gate", "points": TRIANGLE}
        )
        assert resp.status_code == 201
        assert [z.name for z in load_zones(zones_file)] == ["Gate"]

        resp = client.put(
            "/api/admin/zones/Gate", headers=admin_headers, json={"enabled": False, "name": "Gate A"}
        )
        assert resp.status_code == 200
        (zone,) = load_zones(zones_file)
        assert (zone.name, zone.enabled) == ("Gate A", False)

        assert client.delete("/api/admin/zones/Gate%20A", headers=admin_headers).status_code == 200
        assert load_zones(zones_file) == []

    def test_validation_and_duplicates(self, client, admin_headers):
        bad = client.post(
            "/api/admin/zones", headers=admin_headers, json={"name": "X", "points": [[0, 0], [1, 1]]}
        )
        assert bad.status_code == 422
        client.post("/api/admin/zones", headers=admin_headers, json={"name": "X", "points": TRIANGLE})
        dup = client.post("/api/admin/zones", headers=admin_headers, json={"name": "X", "points": TRIANGLE})
        assert dup.status_code == 409
        extra = client.post(
            "/api/admin/zones", headers=admin_headers, json={"name": "Y", "points": TRIANGLE, "threshold": 3}
        )
        assert extra.status_code == 422

    def test_rename_and_delete_carry_thresholds(self, client, admin_headers, tmp_path):
        save_zones(tmp_path / "zones.json", [Zone("Gate", [(0, 0), (9, 0), (9, 9)])])
        client.put(
            "/api/admin/config/thresholds", headers=admin_headers, json={"zone_thresholds": {"Gate": 4}}
        )
        client.put("/api/admin/zones/Gate", headers=admin_headers, json={"name": "Door"})
        assert client.state.get_zone_thresholds() == {"Door": 4}
        client.delete("/api/admin/zones/Door", headers=admin_headers)
        assert client.state.get_zone_thresholds() == {}
        assert (
            client.get("/api/admin/config/thresholds", headers=admin_headers).json()["zone_thresholds"] == {}
        )

    def test_invalid_zones_file_reports_500_with_reason(self, client, admin_headers, tmp_path):
        (tmp_path / "zones.json").write_text("{oops")
        resp = client.get("/api/admin/zones", headers=admin_headers)
        assert resp.status_code == 500
        assert "invalid JSON" in resp.json()["detail"]


class TestCameras:
    def test_crud(self, client, admin_headers):
        cam = client.post(
            "/api/admin/cameras", headers=admin_headers, json={"name": "Lobby", "source_url": "rtsp://cam/1"}
        ).json()
        assert (
            client.post(
                "/api/admin/cameras",
                headers=admin_headers,
                json={"name": "lobby", "source_url": "/videos/lobby.mp4"},
            ).status_code
            == 409
        )
        updated = client.put(
            f"/api/admin/cameras/{cam['id']}", headers=admin_headers, json={"enabled": False}
        )
        assert updated.json()["enabled"] is False
        assert client.delete(f"/api/admin/cameras/{cam['id']}", headers=admin_headers).status_code == 200
        assert client.get("/api/admin/cameras", headers=admin_headers).json() == {"cameras": []}


class TestLogs:
    def test_actions_are_audited_and_filterable(self, client, admin_headers):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        client.post("/api/admin/zones", headers=admin_headers, json={"name": "Gate", "points": TRIANGLE})
        auth = client.get("/api/admin/logs?category=AUTH", headers=admin_headers).json()
        assert {e["action"] for e in auth["logs"]} >= {"LOGIN_SUCCESS", "LOGIN_FAILED"}
        config = client.get("/api/admin/logs?category=CONFIG", headers=admin_headers).json()
        assert config["logs"][0]["action"] == "ZONE_ADDED"
        assert config["logs"][0]["username"] == "admin"

    def test_date_filters_accept_naive_browser_dates(self, client, admin_headers):
        resp = client.get(
            "/api/admin/logs?start_date=2000-01-01T00:00:00&end_date=2999-12-31T23:59:59",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_exports(self, client, admin_headers):
        csv_resp = client.get("/api/admin/logs/export/csv", headers=admin_headers)
        assert csv_resp.text.startswith("timestamp,category,action")
        pdf = client.get("/api/admin/logs/export/pdf", headers=admin_headers)
        assert pdf.content.startswith(b"%PDF")

    def test_alert_acknowledgement(self, client, admin_headers):
        from crowdcount.zones import ZoneStats

        client.state.set_global_threshold(1)
        client.state.publish(total=2, zones={"A": ZoneStats(2, 2)})
        (record,) = client.get("/api/admin/alerts/history", headers=admin_headers).json()["alerts"]
        assert record["acknowledged"] is False
        acked = client.post(f"/api/admin/alerts/{record['id']}/acknowledge", headers=admin_headers).json()
        assert acked["alert"]["acknowledged_by"] == "admin"
        assert client.post("/api/admin/alerts/nope/acknowledge", headers=admin_headers).status_code == 404

    def test_retention(self, client, admin_headers):
        assert (
            client.put("/api/admin/config/retention?retention_days=7", headers=admin_headers).status_code
            == 200
        )
        assert client.get("/api/admin/config/retention", headers=admin_headers).json() == {
            "log_retention_days": 7
        }
