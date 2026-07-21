import socket

import pytest

FUTURE = "2099-01-01T00:00"


def _s3_reachable(timeout=3):
    from urllib.parse import urlparse
    import os

    u = urlparse(os.environ.get("DDP_S3_ENDPOINT", "http://172.16.50.100:9000"))
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
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
def authed_client(client):
    client.post("/api/auth/register", data={"username": "alice", "password": "pass123"})
    return client


def _submit_job(client, make_zip=None, name="test job"):
    resp = client.post(
        "/api/jobs",
        data={
            "name": name,
            "image": "ddp-cuda-ssh:latest",
            "scheduled_at": FUTURE,
            "timeout_minutes": "5",
        },
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# ── Auth ──────────────────────────────────────


class TestAuth:
    def test_register_sets_cookie(self, client):
        resp = client.post(
            "/api/auth/register", data={"username": "newuser", "password": "pass123"}
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "newuser"
        assert "session" in resp.cookies

    def test_register_duplicate(self, authed_client):
        resp = authed_client.post(
            "/api/auth/register", data={"username": "alice", "password": "pass123"}
        )
        assert resp.status_code == 409

    def test_register_short_username(self, client):
        resp = client.post(
            "/api/auth/register", data={"username": "a", "password": "pass123"}
        )
        assert resp.status_code == 400

    def test_register_short_password(self, client):
        resp = client.post(
            "/api/auth/register", data={"username": "bob", "password": "12345"}
        )
        assert resp.status_code == 400

    def test_login_success(self, client):
        client.post(
            "/api/auth/register", data={"username": "charlie", "password": "pass123"}
        )
        client.post("/api/auth/logout")
        resp = client.post(
            "/api/auth/login", data={"username": "charlie", "password": "pass123"}
        )
        assert resp.status_code == 200

    def test_login_wrong_password(self, client):
        client.post(
            "/api/auth/register", data={"username": "dave", "password": "pass123"}
        )
        client.post("/api/auth/logout")
        resp = client.post(
            "/api/auth/login", data={"username": "dave", "password": "wrong"}
        )
        assert resp.status_code == 401

    def test_me_requires_auth(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_returns_user(self, authed_client):
        resp = authed_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == "alice"

    def test_change_password(self, client):
        client.post(
            "/api/auth/register", data={"username": "pwuser", "password": "oldpass1"}
        )
        r = client.post(
            "/api/auth/password",
            data={"old_password": "oldpass1", "new_password": "newpass2"},
        )
        assert r.status_code == 200
        client.post("/api/auth/logout")
        assert (
            client.post(
                "/api/auth/login", data={"username": "pwuser", "password": "oldpass1"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login", data={"username": "pwuser", "password": "newpass2"}
            ).status_code
            == 200
        )

    def test_change_password_wrong_old(self, authed_client):
        r = authed_client.post(
            "/api/auth/password",
            data={"old_password": "nope", "new_password": "newpass2"},
        )
        assert r.status_code == 403

    def test_logout_clears_session(self, authed_client):
        authed_client.post("/api/auth/logout")
        assert authed_client.get("/api/auth/me").status_code == 401


# ── Jobs ──────────────────────────────────────


class TestJobs:
    def test_submit(self, authed_client, make_zip):
        job_id = _submit_job(authed_client, make_zip, "my job")
        assert job_id

    def test_list_after_submit(self, authed_client, make_zip):
        _submit_job(authed_client, make_zip, "job A")
        _submit_job(authed_client, make_zip, "job B")
        resp = authed_client.get("/api/jobs")
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) == 2
        assert jobs[0]["name"] == "job B"  # newest first

    def test_get_detail(self, authed_client, make_zip):
        job_id = _submit_job(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    def test_get_nonexistent(self, authed_client):
        resp = authed_client.get("/api/jobs/nonexistent-id")
        assert resp.status_code == 404

    def test_edit_pending(self, authed_client):
        job_id = _submit_job(authed_client)
        resp = authed_client.patch(
            f"/api/jobs/{job_id}",
            data={
                "name": "renamed",
                "gpus": "0",
                "timeout_minutes": "99",
                "scheduled_at": "2099-03-01T10:00",
            },
        )
        assert resp.status_code == 200
        job = resp.json()
        assert job["name"] == "renamed"
        assert job["timeout_minutes"] == 99
        from datetime import datetime, timezone

        assert datetime.fromisoformat(job["scheduled_at"]) == datetime.fromisoformat(
            "2099-03-01T10:00"
        ).astimezone(timezone.utc)

    def test_edit_non_pending_rejected(self, authed_client):
        job_id = _submit_job(authed_client)
        authed_client.delete(f"/api/jobs/{job_id}")  # -> cancelled
        resp = authed_client.patch(f"/api/jobs/{job_id}", data={"name": "x"})
        assert resp.status_code == 409

    def test_cancel_pending(self, authed_client, make_zip):
        job_id = _submit_job(authed_client, make_zip)
        resp = authed_client.delete(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        job = authed_client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "cancelled"

    def test_reject_unknown_image(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "bad", "image": "windowsxp", "scheduled_at": FUTURE},
        )
        assert resp.status_code == 400

    def test_cpu_quota_reject(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "fat",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "cpu": "999",
            },
        )
        assert resp.status_code == 403
        assert "CPU quota" in resp.json()["detail"]

    def test_memory_quota_reject(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "fat",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "memory_gb": "999",
            },
        )
        assert resp.status_code == 403
        assert "Memory quota" in resp.json()["detail"]

    def test_submit_requires_auth(self, client):
        resp = client.post(
            "/api/jobs",
            data={"name": "x", "image": "ddp-cuda-ssh:latest", "scheduled_at": FUTURE},
        )
        assert resp.status_code == 401

    def test_user_isolation(self, authed_client, client, make_zip):
        job_id = _submit_job(authed_client, make_zip)
        # Register as different user
        client.post(
            "/api/auth/register", data={"username": "eve", "password": "pass123"}
        )
        # Eve can't see Alice's job
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert client.get("/api/jobs").json() == []

    def test_submit_weekly_without_weekdays_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "wk",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "repeat_type": "weekly",
            },
        )
        assert resp.status_code == 400
        assert "weekday" in resp.json()["detail"].lower()

    def test_submit_weekly_bad_weekday_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "wk",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "repeat_type": "weekly",
                "repeat_weekdays": ["1", "8"],
            },
        )
        assert resp.status_code == 400

    def test_submit_weekly_ok(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "wk",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "repeat_type": "weekly",
                "repeat_weekdays": ["1", "3", "5"],
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = authed_client.get(f"/api/jobs/{job_id}").json()
        assert job["repeat_type"] == "weekly"
        assert job["repeat_weekdays"] == "1,3,5"

    def test_submit_daily_clears_weekdays(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "daily",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "repeat_type": "daily",
                "repeat_weekdays": ["1", "2"],
            },
        )
        assert resp.status_code == 200
        job = authed_client.get(f"/api/jobs/{resp.json()['id']}").json()
        assert job["repeat_type"] == "daily"
        assert job["repeat_weekdays"] is None

    def test_submit_bad_repeat_type_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={
                "name": "x",
                "image": "ddp-cuda-ssh:latest",
                "scheduled_at": FUTURE,
                "repeat_type": "monthly",
            },
        )
        assert resp.status_code == 400

    def test_edit_repeat_type(self, authed_client):
        job_id = _submit_job(authed_client)
        resp = authed_client.patch(f"/api/jobs/{job_id}", data={"repeat_type": "daily"})
        assert resp.status_code == 200
        assert resp.json()["repeat_type"] == "daily"


# ── S3-backed endpoints ───────────────────────


class TestS3Endpoints:
    def _setup_job_with_s3_data(self, authed_client, make_zip):
        """Submit a job, then inject S3 data (logs + outputs)."""
        from app.main import storage

        job_id = _submit_job(authed_client, make_zip)
        storage.upload_bytes(
            f"jobs/{job_id}/logs/run.log", b"=== python main.py ===\nhello world\n"
        )
        storage.upload_bytes(f"jobs/{job_id}/output/result.txt", b"result data")
        storage.upload_bytes(f"jobs/{job_id}/output/data/final.csv", b"a,b,c\n1,2,3")
        return job_id

    def test_get_logs(self, authed_client, make_zip):
        job_id = self._setup_job_with_s3_data(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/logs")
        assert resp.status_code == 200
        assert "hello world" in resp.json()["logs"]

    def test_get_logs_empty(self, authed_client, make_zip):
        job_id = _submit_job(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/logs")
        assert resp.status_code == 200
        assert resp.json()["logs"] == ""

    def test_download_logs(self, authed_client, make_zip):
        job_id = self._setup_job_with_s3_data(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/logs/download")
        assert resp.status_code == 200
        assert b"hello world" in resp.content
        assert "attachment" in resp.headers.get("content-disposition", "")

    def test_list_outputs(self, authed_client, make_zip):
        job_id = self._setup_job_with_s3_data(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/outputs")
        assert resp.status_code == 200
        outputs = resp.json()["outputs"]
        assert len(outputs) == 2
        for o in outputs:
            assert "download_url" in o
            assert o["download_url"].startswith(f"/api/jobs/{job_id}/download/")

    def test_download_output(self, authed_client, make_zip):
        job_id = self._setup_job_with_s3_data(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/download/output/result.txt")
        assert resp.status_code == 200
        assert resp.content == b"result data"

    def test_download_output_nested(self, authed_client, make_zip):
        job_id = self._setup_job_with_s3_data(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/download/output/data/final.csv")
        assert resp.status_code == 200
        assert resp.content == b"a,b,c\n1,2,3"

    def test_download_nonexistent(self, authed_client, make_zip):
        job_id = _submit_job(authed_client, make_zip)
        resp = authed_client.get(f"/api/jobs/{job_id}/download/output/nope.txt")
        assert resp.status_code == 404
