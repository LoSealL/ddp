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
def normal_client(client, admin_client):
    """Second registered user is non-admin."""
    client.post("/api/auth/register", data={"username": "normie", "password": "pass123"})
    return client


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
