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

    def test_unique_username(self):
        db.create_user("dup", "h", "s")
        with pytest.raises(sqlite3.IntegrityError):
            db.create_user("dup", "h2", "s2")


class TestJobs:
    def _setup_user(self):
        return db.create_user("tester", "h", "s")

    def _create_job(self, user_id, name="test job"):
        db.create_job("job-1", user_id, name, "proj.zip", "python main.py", "2026-01-01T00:00:00+00:00", 60)
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
        db.create_job("job-a", uid_a, "A's job", "a.zip", "python a.py", "2026-01-01T00:00:00+00:00", 30)
        db.create_job("job-b", uid_b, "B's job", "b.zip", "python b.py", "2026-01-01T00:00:00+00:00", 30)

        jobs_a = db.list_jobs(uid_a)
        assert len(jobs_a) == 1
        assert jobs_a[0]["name"] == "A's job"

        jobs_b = db.list_jobs(uid_b)
        assert len(jobs_b) == 1
        assert jobs_b[0]["name"] == "B's job"

    def test_list_all(self):
        uid = self._setup_user()
        db.create_job("j1", uid, "first", "a.zip", "python a.py", "2026-01-01T00:00:00+00:00", 30)
        db.create_job("j2", uid, "second", "b.zip", "python b.py", "2026-01-01T00:00:00+00:00", 30)
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
