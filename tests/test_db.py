import sqlite3

import pytest

from app import db


class TestUsers:
    def test_create_and_get(self):
        uid = db.create_user("alice", "hash1", "salt1")
        user = db.get_user_by_username("alice")
        assert user["id"] == uid
        assert user["username"] == "alice"
        assert user["password_hash"] == "hash1"
        assert user["salt"] == "salt1"

    def test_get_nonexistent(self):
        assert db.get_user_by_username("nobody") is None

    def test_get_by_id(self):
        uid = db.create_user("bob", "hash2", "salt2")
        user = db.get_user_by_id(uid)
        assert user["username"] == "bob"
        assert "is_admin" in user

    def test_unique_username(self):
        db.create_user("dup", "h", "s")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_user("dup", "h2", "s2")


class TestJobs:
    def _setup_user(self):
        return db.create_user("tester", "h", "s")

    def _create_job(self, user_id, name="test job"):
        db.create_job("job-1", user_id, name, "ddp-cuda-ssh:latest", "python main.py", "2026-01-01T00:00:00+00:00", 60)
        return "job-1"

    def test_create_and_get(self):
        uid = self._setup_user()
        self._create_job(uid)
        job = db.get_job("job-1")
        assert job["name"] == "test job"
        assert job["status"] == "pending"
        assert job["user_id"] == uid
        assert job["timeout_minutes"] == 60

    def test_list_scoped_by_user(self):
        uid_a = db.create_user("userA", "h", "s")
        uid_b = db.create_user("userB", "h", "s")
        db.create_job("job-a", uid_a, "A's job", "ddp-cuda-ssh:latest", "python a.py", "2026-01-01T00:00:00+00:00", 30)
        db.create_job("job-b", uid_b, "B's job", "ddp-cuda-ssh:latest", "python b.py", "2026-01-01T00:00:00+00:00", 30)

        jobs_a = db.list_jobs(uid_a)
        assert len(jobs_a) == 1
        assert jobs_a[0]["name"] == "A's job"

        jobs_b = db.list_jobs(uid_b)
        assert len(jobs_b) == 1
        assert jobs_b[0]["name"] == "B's job"

    def test_list_all(self):
        uid = self._setup_user()
        db.create_job("j1", uid, "first", "ddp-cuda-ssh:latest", "python a.py", "2026-01-01T00:00:00+00:00", 30)
        db.create_job("j2", uid, "second", "ddp-cuda-ssh:latest", "python b.py", "2026-01-01T00:00:00+00:00", 30)
        all_jobs = db.list_jobs()
        assert len(all_jobs) == 2

    def test_update_job(self):
        uid = self._setup_user()
        self._create_job(uid)
        db.update_job("job-1", status="running", started_at="2026-01-01T01:00:00+00:00")
        job = db.get_job("job-1")
        assert job["status"] == "running"
        assert job["started_at"] == "2026-01-01T01:00:00+00:00"

    def test_delete_job(self):
        uid = self._setup_user()
        self._create_job(uid)
        db.delete_job("job-1")
        assert db.get_job("job-1") is None

    def test_get_nonexistent(self):
        assert db.get_job("nope") is None


class TestSystemParams:
    def test_default_params_seeded(self):
        params = db.get_all_params()
        assert params["time_window_start"] == "22:00"
        assert params["time_window_end"] == "06:00"
        assert params["time_window_repeat"] == "daily"
        assert params["gpu_default_quota"] == 1
        assert params["storage_default_quota_gb"] == 10.0

    def test_get_single_param(self):
        assert db.get_param("time_window_start") == "22:00"
        assert db.get_param("nonexistent") is None

    def test_set_param(self):
        db.set_param("gpu_default_quota", 4, user_id=1)
        assert db.get_param("gpu_default_quota") == 4

    def test_set_param_json(self):
        db.set_param("gpu_devices", '[{"id":0,"name":"GPU-0"}]', user_id=1)
        import json
        assert json.loads(db.get_param("gpu_devices"))[0]["name"] == "GPU-0"


class TestSystemLogs:
    def test_log_and_list(self):
        db.log_event("INFO", "auth", "User registered: alice", user_id=1)
        logs = db.list_logs()
        assert len(logs) == 1
        assert logs[0]["message"] == "User registered: alice"
        assert logs[0]["level"] == "INFO"
        assert logs[0]["category"] == "auth"

    def test_log_filter_by_level(self):
        db.log_event("INFO", "auth", "msg1")
        db.log_event("ERROR", "system", "msg2")
        errors = db.list_logs(level="ERROR")
        assert len(errors) == 1
        assert errors[0]["message"] == "msg2"

    def test_log_filter_by_category(self):
        db.log_event("INFO", "auth", "msg1")
        db.log_event("INFO", "job", "msg2")
        auth_logs = db.list_logs(category="auth")
        assert len(auth_logs) == 1
        assert auth_logs[0]["message"] == "msg1"

    def test_log_pagination(self):
        for i in range(10):
            db.log_event("INFO", "system", f"msg{i}")
        page = db.list_logs(limit=5, offset=0)
        assert len(page) == 5
        page2 = db.list_logs(limit=5, offset=5)
        assert len(page2) == 5
        assert page[0]["message"] != page2[0]["message"]

    def test_log_count(self):
        db.log_event("INFO", "auth", "msg1")
        db.log_event("ERROR", "system", "msg2")
        assert db.count_logs() == 2
        assert db.count_logs(level="ERROR") == 1

    def test_log_with_details(self):
        db.log_event("INFO", "admin", "params updated", details='{"key":"gpu"}')
        logs = db.list_logs()
        assert logs[0]["details"] == '{"key":"gpu"}'


class TestAdminUserColumns:
    def test_first_user_is_admin(self):
        uid = db.create_user("alice", "h", "s")
        user = db.get_user_by_id(uid)
        assert user["is_admin"] == 1

    def test_second_user_not_admin(self):
        db.create_user("alice", "h", "s")
        uid2 = db.create_user("bob", "h", "s")
        user = db.get_user_by_id(uid2)
        assert user["is_admin"] == 0

    def test_list_users_with_admin_flag(self):
        db.create_user("alice", "h", "s")
        db.create_user("bob", "h", "s")
        users = db.list_users()
        assert len(users) == 2
        assert users[0]["is_admin"] == 1
        assert users[1]["is_admin"] == 0

    def test_update_user_admin_flag(self):
        db.create_user("alice", "h", "s")
        uid2 = db.create_user("bob", "h", "s")
        db.update_user(uid2, is_admin=1)
        assert db.get_user_by_id(uid2)["is_admin"] == 1

    def test_update_user_quotas(self):
        db.create_user("alice", "h", "s")
        uid2 = db.create_user("bob", "h", "s")
        db.update_user(uid2, gpu_quota_override=8, storage_quota_override_gb=50.0)
        user = db.get_user_by_id(uid2)
        assert user["gpu_quota_override"] == 8
        assert user["storage_quota_override_gb"] == 50.0

    def test_delete_user(self):
        db.create_user("alice", "h", "s")
        uid2 = db.create_user("bob", "h", "s")
        db.delete_user(uid2)
        assert db.get_user_by_id(uid2) is None

    def test_count_admins(self):
        db.create_user("alice", "h", "s")
        uid2 = db.create_user("bob", "h", "s")
        assert db.count_admins() == 1
        db.update_user(uid2, is_admin=1)
        assert db.count_admins() == 2


def test_repeat_columns_default(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.create_user("u", "h", "s")
    db.create_job("jid1", user_id=1, name="n", image="img", entry_command="c",
                  scheduled_at="2099-01-01T00:00", timeout_minutes=5)
    job = db.get_job("jid1")
    assert job["repeat_type"] == "none"
    assert job["repeat_weekdays"] is None


def test_create_job_with_repeat(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.create_user("u", "h", "s")
    db.create_job("jid2", user_id=1, name="n", image="img", entry_command="c",
                  scheduled_at="2099-01-01T00:00", timeout_minutes=5,
                  repeat_type="weekly", repeat_weekdays="1,3,5")
    job = db.get_job("jid2")
    assert job["repeat_type"] == "weekly"
    assert job["repeat_weekdays"] == "1,3,5"
