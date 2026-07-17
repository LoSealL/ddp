import pytest

from app import auth, db


class TestPasswordHashing:
    def test_different_salts(self):
        h1, s1 = auth.hash_password("password123")
        h2, s2 = auth.hash_password("password123")
        assert s1 != s2
        assert h1 != h2

    def test_verify_correct(self):
        h, s = auth.hash_password("mypassword")
        assert auth.verify_password("mypassword", h, s)

    def test_verify_wrong(self):
        h, s = auth.hash_password("mypassword")
        assert not auth.verify_password("wrongpassword", h, s)

    def test_verify_empty(self):
        h, s = auth.hash_password("mypassword")
        assert not auth.verify_password("", h, s)


class TestSession:
    def test_create_and_lookup(self):
        user_id = db.create_user("alice", "fakehash", "fakesalt")
        token = auth.create_session_for_user(user_id)
        user = db.get_user_by_session(token)
        assert user is not None
        assert user["username"] == "alice"

    def test_lookup_invalid_token(self):
        assert db.get_user_by_session("nonexistent") is None

    def test_delete_session(self):
        user_id = db.create_user("bob", "fakehash", "fakesalt")
        token = auth.create_session_for_user(user_id)
        assert db.get_user_by_session(token) is not None
        db.delete_session(token)
        assert db.get_user_by_session(token) is None
