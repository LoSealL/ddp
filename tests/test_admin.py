import io
import socket

import pytest

FUTURE = "2099-01-01T00:00"


def _s3_reachable(host="127.0.0.1", port=9000, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _s3_reachable(), reason="S3 unreachable")


@pytest.fixture
def client():
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client):
    """First registered user is admin."""
    client.post("/api/auth/register", data={"username": "admin", "password": "pass123"})
    return client


@pytest.fixture
def normal_client(admin_client):
    """Second registered user is non-admin. Separate TestClient so its session
    cookie doesn't clobber admin's (lifespan is re-entrant on the scheduler)."""
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        c.post("/api/auth/register", data={"username": "normie", "password": "pass123"})
        yield c


class TestAdminRole:
    def test_first_user_is_admin(self, admin_client):
        resp = admin_client.get("/api/auth/me")
        assert resp.json()["is_admin"] == 1

    def test_second_user_not_admin(self, normal_client):
        resp = normal_client.get("/api/auth/me")
        assert resp.json()["is_admin"] == 0

    def test_non_admin_gets_403_on_admin_users(self, normal_client):
        resp = normal_client.get("/api/admin/users")
        assert resp.status_code == 403

    def test_unauth_gets_401_on_admin(self, client):
        resp = client.get("/api/admin/users")
        assert resp.status_code == 401

    def test_admin_can_list_users(self, admin_client):
        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        users = resp.json()
        assert len(users) == 1
        assert users[0]["username"] == "admin"
        assert users[0]["is_admin"] == 1


class TestUserManagement:
    def test_list_users_shows_all(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        usernames = [u["username"] for u in users]
        assert "admin" in usernames
        assert "normie" in usernames

    def test_promote_user(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        resp = admin_client.patch(f"/api/admin/users/{normie['id']}", json={"is_admin": 1})
        assert resp.status_code == 200
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        assert normie["is_admin"] == 1

    def test_set_quota_override(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        resp = admin_client.patch(f"/api/admin/users/{normie['id']}", json={
            "gpu_quota_override": 4, "storage_quota_override_gb": 25.0
        })
        assert resp.status_code == 200
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        assert normie["gpu_quota_override"] == 4
        assert normie["storage_quota_override_gb"] == 25.0

    def test_clear_quota_override(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        admin_client.patch(f"/api/admin/users/{normie['id']}", json={"gpu_quota_override": 4})
        admin_client.patch(f"/api/admin/users/{normie['id']}", json={"gpu_quota_override": None})
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        assert normie["gpu_quota_override"] is None

    def test_cannot_remove_self_admin(self, admin_client):
        users = admin_client.get("/api/admin/users").json()
        admin_user = [u for u in users if u["username"] == "admin"][0]
        resp = admin_client.patch(f"/api/admin/users/{admin_user['id']}", json={"is_admin": 0})
        assert resp.status_code == 403

    def test_cannot_delete_self(self, admin_client):
        users = admin_client.get("/api/admin/users").json()
        admin_user = [u for u in users if u["username"] == "admin"][0]
        resp = admin_client.delete(f"/api/admin/users/{admin_user['id']}")
        assert resp.status_code == 403

    def test_delete_user(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        normie = [u for u in users if u["username"] == "normie"][0]
        resp = admin_client.delete(f"/api/admin/users/{normie['id']}")
        assert resp.status_code == 200
        users = admin_client.get("/api/admin/users").json()
        assert not any(u["username"] == "normie" for u in users)

    def test_non_admin_cannot_manage_users(self, normal_client):
        resp = normal_client.patch("/api/admin/users/1", json={"is_admin": 1})
        assert resp.status_code == 403
        resp = normal_client.delete("/api/admin/users/1")
        assert resp.status_code == 403


class TestSystemParams:
    def test_get_params(self, admin_client):
        resp = admin_client.get("/api/admin/params")
        assert resp.status_code == 200
        params = resp.json()
        assert params["time_window_start"] == "22:00"
        assert params["time_window_end"] == "06:00"
        assert params["time_window_repeat"] == "daily"
        assert params["gpu_default_quota"] == 1
        assert params["storage_default_quota_gb"] == 10.0
        assert isinstance(params["gpu_devices"], list)

    def test_update_params(self, admin_client):
        admin_client.put("/api/admin/params", json={"gpu_default_quota": 8})
        params = admin_client.get("/api/admin/params").json()
        assert params["gpu_default_quota"] == 8

    def test_update_time_window(self, admin_client):
        admin_client.put("/api/admin/params", json={
            "time_window_start": "09:00",
            "time_window_end": "17:00",
            "time_window_repeat": "weekdays",
        })
        params = admin_client.get("/api/admin/params").json()
        assert params["time_window_start"] == "09:00"
        assert params["time_window_repeat"] == "weekdays"

    def test_update_gpu_devices(self, admin_client):
        devices = [{"uuid": "GPU-aaaa-bbbb", "enabled": False}]
        admin_client.put("/api/admin/params", json={"gpu_devices": devices})
        params = admin_client.get("/api/admin/params").json()
        assert params["gpu_devices"][0]["uuid"] == "GPU-aaaa-bbbb"
        assert params["gpu_devices"][0]["enabled"] is False

    def test_reject_invalid_gpu_devices(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={"gpu_devices": [{"name": "A100"}]})
        assert resp.status_code == 400

    def test_reject_unknown_param(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={"nonexistent_key": "value"})
        assert resp.status_code == 400

    def test_reject_invalid_repeat(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={"time_window_repeat": "monthly"})
        assert resp.status_code == 400

    def test_non_admin_cannot_access_params(self, normal_client):
        assert normal_client.get("/api/admin/params").status_code == 403
        assert normal_client.put("/api/admin/params", json={}).status_code == 403


class TestSystemLogs:
    def test_get_logs(self, admin_client):
        resp = admin_client.get("/api/admin/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_filter_by_level(self, admin_client):
        from app import db
        db.log_event("ERROR", "system", "test error")
        resp = admin_client.get("/api/admin/logs?level=ERROR")
        logs = resp.json()["logs"]
        assert all(l["level"] == "ERROR" for l in logs)

    def test_filter_by_category(self, admin_client):
        from app import db
        db.log_event("INFO", "job", "test job log")
        resp = admin_client.get("/api/admin/logs?category=job")
        logs = resp.json()["logs"]
        assert all(l["category"] == "job" for l in logs)

    def test_pagination(self, admin_client):
        from app import db
        for i in range(15):
            db.log_event("INFO", "system", f"bulk msg {i}")
        resp = admin_client.get("/api/admin/logs?limit=5&offset=0")
        data = resp.json()
        assert len(data["logs"]) == 5
        assert data["total"] >= 15

    def test_non_admin_cannot_access_logs(self, normal_client):
        assert normal_client.get("/api/admin/logs").status_code == 403


class TestMonitoring:
    def test_monitoring_shape(self, admin_client):
        resp = admin_client.get("/api/admin/monitoring")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert "gpus" in data
        assert "s3" in data
        for status in ("pending", "running", "done", "failed", "timeout", "cancelled"):
            assert status in data["jobs"]
        assert isinstance(data["gpus"], list)
        assert "bucket" in data["s3"]
        assert "object_count" in data["s3"]
        assert "total_size_bytes" in data["s3"]

    def test_monitoring_job_counts(self, admin_client):
        admin_client.post("/api/jobs", data={"name": "job1", "image": "ddp-cuda-ssh:latest", "scheduled_at": FUTURE})
        resp = admin_client.get("/api/admin/monitoring")
        assert resp.json()["jobs"]["pending"] >= 1

    def test_non_admin_cannot_access_monitoring(self, normal_client):
        assert normal_client.get("/api/admin/monitoring").status_code == 403


class TestTimeWindowEnforcement:
    def test_job_outside_window_gets_queued(self, admin_client):
        admin_client.put("/api/admin/params", json={
            "time_window_start": "09:00", "time_window_end": "17:00", "time_window_repeat": "daily"
        })
        resp = admin_client.post("/api/jobs", data={"name": "night job", "image": "ddp-cuda-ssh:latest", "scheduled_at": "2099-01-01T22:00"})
        assert resp.status_code == 200
        assert resp.json()["queued"] is True

    def test_job_inside_window_normal(self, admin_client):
        admin_client.put("/api/admin/params", json={
            "time_window_start": "00:00", "time_window_end": "23:59", "time_window_repeat": "daily"
        })
        resp = admin_client.post("/api/jobs", data={"name": "anytime", "image": "ddp-cuda-ssh:latest", "scheduled_at": "2099-06-15T12:00"})
        assert resp.status_code == 200
        assert resp.json()["queued"] is False
