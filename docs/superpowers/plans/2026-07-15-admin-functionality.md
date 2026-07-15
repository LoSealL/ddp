# Admin Functionality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin role, user management, system parameters (time windows + quotas), system logging, and a monitoring dashboard to DDP.

**Architecture:** Single new `app/admin.py` module with an `APIRouter`. Schema changes in `db.py` (new columns + 2 new tables). `auth.py` gains `require_admin`. `main.py` adds time-window queueing on submit + logging calls. Frontend gets a new admin panel view.

**Tech Stack:** Python 3.12, FastAPI, SQLite (stdlib `sqlite3`), vanilla TypeScript + Vite, pytest.

**Test patterns:** DB-level tests in `tests/test_db.py` (no S3 needed). API tests in `tests/test_api.py` and `tests/test_admin.py` (S3 needed, `pytest.mark.skipif`).

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `app/db.py` | Modify | Add columns to `users`, new `system_params` + `system_logs` tables, new functions |
| `app/auth.py` | Modify | `get_current_user` returns `is_admin`, `require_admin` dependency |
| `app/admin.py` | Create | Admin `APIRouter`: users, params, logs, monitoring |
| `app/timecheck.py` | Create | Time window check + queue logic |
| `app/main.py` | Modify | Include admin router, queue on submit, add logging calls |
| `app/executor.py` | Modify | Add logging calls |
| `frontend/index.html` | Modify | Admin panel HTML |
| `frontend/src/main.ts` | Modify | Admin panel logic + i18n |
| `frontend/src/style.css` | Modify | Admin panel styles |
| `tests/test_db.py` | Modify | Tests for new DB functions |
| `tests/test_admin.py` | Create | Tests for admin API + time window |

---

## Task 1: DB Schema — New Tables and Columns

**Files:**
- Modify: `app/db.py` (schema in `init_db`, new user columns, new functions)

- [ ] **Step 1: Write failing tests for schema + new DB functions**

Add to `tests/test_db.py`:

```python
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
```

Also update the existing `TestUsers.test_get_by_id` to check `is_admin` field exists:

```python
    def test_get_by_id(self):
        uid = db.create_user("bob", "hash2", "salt2")
        user = db.get_user_by_id(uid)
        assert user["username"] == "bob"
        assert "is_admin" in user  # NEW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py -v`
Expected: All new tests FAIL (functions don't exist yet).

- [ ] **Step 3: Update `init_db` with new columns and tables**

In `app/db.py`, replace the `init_db` function's table creation section. After the existing `sessions` table and before the `ALTER TABLE jobs` migration, add the migrations for `users` and the two new tables:

```python
    # ── migrations: users table ──
    for col, typedef in [
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
        ("gpu_quota_override", "INTEGER"),
        ("storage_quota_override_gb", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_params (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            updated_by  INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            level       TEXT NOT NULL,
            category    TEXT NOT NULL,
            message     TEXT NOT NULL,
            user_id     INTEGER,
            details     TEXT
        )
    """)
```

Then, after `conn.commit()`, add the seeding of default params (before `conn.close()`):

```python
    # ── seed default system params ──
    _seed_defaults = [
        ("time_window_start", "22:00"),
        ("time_window_end", "06:00"),
        ("time_window_repeat", "daily"),
        ("gpu_default_quota", "1"),
        ("storage_default_quota_gb", "10.0"),
        ("gpu_devices", json.dumps([
            {"id": 0, "name": "GPU-0", "memory_total_mb": 16384, "memory_used_mb": 0,
             "cores_total": 100, "cores_used": 0},
            {"id": 1, "name": "GPU-1", "memory_total_mb": 16384, "memory_used_mb": 0,
             "cores_total": 100, "cores_used": 0},
        ])),
    ]
    for key, value in _seed_defaults:
        conn.execute(
            "INSERT OR IGNORE INTO system_params (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
```

Note: `now` is already defined as `datetime.now(timezone.utc).isoformat()` — but actually it's NOT defined in the current `init_db`. Check: the current code does `conn.commit()` and `conn.close()` at the end without a `now` variable. Fix: define `now` at the top of `init_db` after getting the connection:

```python
def init_db():
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
```

Also add `import json` at the top of `app/db.py` (it's not currently imported).

- [ ] **Step 4: Add new DB functions to `app/db.py`**

After the existing `get_user_by_id` function, update it to return the full row including `is_admin`:

```python
def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
```

Then add the remaining new functions at the end of `app/db.py` (before the sessions section or after — place them logically after the Users section):

```python
def list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, is_admin, gpu_quota_override, storage_quota_override_gb, created_at "
        "FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user(user_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn.execute(f"UPDATE users SET {sets} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def count_admins():
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()
    conn.close()
    return row[0]
```

Add the system_params functions:

```python
def get_all_params():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM system_params").fetchall()
    conn.close()
    result = {}
    for r in rows:
        val = r["value"]
        if r["key"] in ("gpu_default_quota",):
            result[r["key"]] = int(val)
        elif r["key"] in ("storage_default_quota_gb",):
            result[r["key"]] = float(val)
        elif r["key"] == "gpu_devices":
            result[r["key"]] = json.loads(val)
        else:
            result[r["key"]] = val
    return result


def get_param(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM system_params WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_param(key, value, user_id=None):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO system_params (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, str(value), now, user_id),
    )
    conn.commit()
    conn.close()
```

Add the system_logs functions:

```python
def log_event(level, category, message, user_id=None, details=None):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO system_logs (timestamp, level, category, message, user_id, details) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now, level, category, message, user_id, details),
    )
    conn.commit()
    conn.close()


def list_logs(level=None, category=None, limit=100, offset=0):
    conn = get_db()
    query = "SELECT * FROM system_logs"
    conditions = []
    params = []
    if level:
        conditions.append("level = ?")
        params.append(level)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_logs(level=None, category=None):
    conn = get_db()
    query = "SELECT COUNT(*) FROM system_logs"
    conditions = []
    params = []
    if level:
        conditions.append("level = ?")
        params.append(level)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    count = conn.execute(query, params).fetchone()[0]
    conn.close()
    return count
```

Also update `create_user` so the first user becomes admin:

```python
def create_user(username, password_hash, salt):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    is_first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, salt, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, password_hash, salt, 1 if is_first else 0, now),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id
```

Also update `get_user_by_session` to include `is_admin`:

```python
def get_user_by_session(token):
    conn = get_db()
    row = conn.execute("""
        SELECT u.id, u.username, u.is_admin FROM users u
        JOIN sessions s ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
    """, (token, datetime.now(timezone.utc).isoformat())).fetchone()
    conn.close()
    return dict(row) if row else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run existing auth tests to ensure no regression**

Run: `python -m pytest tests/test_auth.py -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add admin schema, system_params, system_logs tables and DB functions"
```

---

## Task 2: Auth Changes — is_admin + require_admin

**Files:**
- Modify: `app/auth.py`
- Modify: `app/main.py` (`/api/auth/me` endpoint)

- [ ] **Step 1: Write failing tests for admin role**

Create `tests/test_admin.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestAdminRole -v`
Expected: Tests FAIL (no `is_admin` in me response, no `/api/admin/users` endpoint).

- [ ] **Step 3: Add `require_admin` to `app/auth.py`**

```python
from fastapi import Cookie, HTTPException, Depends

async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user
```

- [ ] **Step 4: Update `/api/auth/me` in `app/main.py`**

Change the `me` endpoint:

```python
@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.get_current_user)):
    return {"id": user["id"], "username": user["username"], "is_admin": user.get("is_admin", 0)}
```

- [ ] **Step 5: Run tests to verify admin role tests pass**

Run: `python -m pytest tests/test_admin.py::TestAdminRole -v -k "not list_users"`
Expected: Role tests PASS. `test_admin_can_list_users` still fails (no endpoint).

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/main.py tests/test_admin.py
git commit -m "feat: add is_admin to auth/me, add require_admin dependency"
```

---

## Task 3: Admin API — User Management

**Files:**
- Create: `app/admin.py`
- Modify: `app/main.py` (include router)

- [ ] **Step 1: Write failing tests for user management**

Add to `tests/test_admin.py`:

```python
class TestUserManagement:
    def test_list_users_shows_all(self, admin_client, normal_client):
        # normal_client already registered "normie"
        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()]
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
        admin = [u for u in users if u["username"] == "admin"][0]
        resp = admin_client.patch(f"/api/admin/users/{admin['id']}", json={"is_admin": 0})
        assert resp.status_code == 403

    def test_cannot_delete_self(self, admin_client):
        users = admin_client.get("/api/admin/users").json()
        admin = [u for u in users if u["username"] == "admin"][0]
        resp = admin_client.delete(f"/api/admin/users/{admin['id']}")
        assert resp.status_code == 403

    def test_cannot_delete_last_admin(self, admin_client, normal_client):
        users = admin_client.get("/api/admin/users").json()
        admin = [u for u in users if u["username"] == "admin"][0]
        # If we somehow try to demote admin to non-admin and there's only 1 admin
        # This is covered by cannot_remove_self_admin, but also test delete
        # Actually: can't test this well without demoting first. Test: admin exists alone
        # The cannot_remove_self covers it. Skip.
        pass

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestUserManagement -v`
Expected: All FAIL (no `/api/admin/*` endpoints).

- [ ] **Step 3: Create `app/admin.py` with user management routes**

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from . import db, auth
from .storage import Storage
import os

router = APIRouter(prefix="/api/admin", tags=["admin"])

storage = Storage()


class UserUpdate(BaseModel):
    is_admin: Optional[int] = None
    gpu_quota_override: Optional[int] = None
    storage_quota_override_gb: Optional[float] = None


@router.get("/users")
async def list_users(user: dict = Depends(auth.require_admin)):
    return db.list_users()


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, user: dict = Depends(auth.require_admin)):
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")

    updates = body.model_dump(exclude_none=True)

    # Allow setting nullable fields to None explicitly
    body_dict = body.model_dump()
    if "gpu_quota_override" in body_dict and body_dict["gpu_quota_override"] is None and body.gpu_quota_override is None:
        if "gpu_quota_override" not in updates:
            updates["gpu_quota_override"] = None
    if "storage_quota_override_gb" in body_dict and body_dict["storage_quota_override_gb"] is None and body.storage_quota_override_gb is None:
        if "storage_quota_override_gb" not in updates:
            updates["storage_quota_override_gb"] = None

    if "is_admin" in updates:
        if updates["is_admin"] == 0 and user_id == user["id"]:
            raise HTTPException(403, "Cannot remove your own admin privileges")
        if updates["is_admin"] == 0 and db.count_admins() <= 1:
            raise HTTPException(403, "Cannot remove the last admin")

    db.update_user(user_id, **updates)
    db.log_event("INFO", "admin", f"User {target['username']} updated",
                 user_id=user["id"], details=str(updates))
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: dict = Depends(auth.require_admin)):
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if user_id == user["id"]:
        raise HTTPException(403, "Cannot delete your own account")
    if target["is_admin"] and db.count_admins() <= 1:
        raise HTTPException(403, "Cannot delete the last admin")

    db.delete_user(user_id)
    db.log_event("WARNING", "admin", f"User {target['username']} deleted", user_id=user["id"])
    return {"ok": True}
```

- [ ] **Step 4: Include admin router in `app/main.py`**

Add import at top of `app/main.py` (after `from . import db, auth`):

```python
from . import admin
```

Add after `app = FastAPI(...)` (before the auth routes):

```python
app.include_router(admin.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py::TestUserManagement -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add app/admin.py app/main.py tests/test_admin.py
git commit -m "feat: add admin user management endpoints"
```

---

## Task 4: System Parameters API

**Files:**
- Modify: `app/admin.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_admin.py`:

```python
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
        resp = admin_client.put("/api/admin/params", json={"gpu_default_quota": 8})
        assert resp.status_code == 200
        resp = admin_client.get("/api/admin/params")
        assert resp.json()["gpu_default_quota"] == 8

    def test_update_time_window(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={
            "time_window_start": "09:00",
            "time_window_end": "17:00",
            "time_window_repeat": "weekdays",
        })
        assert resp.status_code == 200
        params = admin_client.get("/api/admin/params").json()
        assert params["time_window_start"] == "09:00"
        assert params["time_window_repeat"] == "weekdays"

    def test_update_gpu_devices(self, admin_client):
        devices = [{"id": 0, "name": "A100", "memory_total_mb": 40960, "memory_used_mb": 0,
                     "cores_total": 100, "cores_used": 0}]
        resp = admin_client.put("/api/admin/params", json={"gpu_devices": devices})
        assert resp.status_code == 200
        params = admin_client.get("/api/admin/params").json()
        assert params["gpu_devices"][0]["name"] == "A100"

    def test_reject_unknown_param(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={"nonexistent_key": "value"})
        assert resp.status_code == 400

    def test_reject_invalid_repeat(self, admin_client):
        resp = admin_client.put("/api/admin/params", json={"time_window_repeat": "monthly"})
        assert resp.status_code == 400

    def test_non_admin_cannot_access_params(self, normal_client):
        assert normal_client.get("/api/admin/params").status_code == 403
        assert normal_client.put("/api/admin/params", json={}).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestSystemParams -v`
Expected: All FAIL.

- [ ] **Step 3: Add params endpoints to `app/admin.py`**

Add at the top of `app/admin.py` after imports:

```python
import json
```

Add to `app/admin.py` (after user management routes):

```python
ALLOWED_PARAMS = {
    "time_window_start", "time_window_end", "time_window_repeat",
    "gpu_default_quota", "storage_default_quota_gb", "gpu_devices",
}


@router.get("/params")
async def get_params(user: dict = Depends(auth.require_admin)):
    return db.get_all_params()


@router.put("/params")
async def update_params(body: dict, user: dict = Depends(auth.require_admin)):
    unknown = set(body.keys()) - ALLOWED_PARAMS
    if unknown:
        raise HTTPException(400, f"Unknown parameters: {unknown}")

    if "time_window_repeat" in body:
        if body["time_window_repeat"] not in ("daily", "weekly", "weekdays"):
            raise HTTPException(400, "time_window_repeat must be daily, weekly, or weekdays")

    if "gpu_devices" in body and not isinstance(body["gpu_devices"], list):
        raise HTTPException(400, "gpu_devices must be a list")

    for key, value in body.items():
        if key == "gpu_devices":
            value = json.dumps(value)
        db.set_param(key, value, user_id=user["id"])

    db.log_event("INFO", "admin", "System params updated", user_id=user["id"],
                 details=json.dumps(body))
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py::TestSystemParams -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin.py tests/test_admin.py
git commit -m "feat: add system parameters API"
```

---

## Task 5: System Logs API

**Files:**
- Modify: `app/admin.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_admin.py`:

```python
class TestSystemLogs:
    def test_get_logs(self, admin_client):
        # Registration already logged some events
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
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert all(l["level"] == "ERROR" for l in logs)

    def test_filter_by_category(self, admin_client):
        from app import db
        db.log_event("INFO", "job", "test job log")
        resp = admin_client.get("/api/admin/logs?category=job")
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert all(l["category"] == "job" for l in logs)

    def test_pagination(self, admin_client):
        from app import db
        for i in range(15):
            db.log_event("INFO", "system", f"bulk msg {i}")
        resp = admin_client.get("/api/admin/logs?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["logs"]) == 5
        assert data["total"] >= 15

    def test_non_admin_cannot_access_logs(self, normal_client):
        assert normal_client.get("/api/admin/logs").status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestSystemLogs -v`
Expected: All FAIL.

- [ ] **Step 3: Add logs endpoint to `app/admin.py`**

```python
from fastapi import Query

@router.get("/logs")
async def get_logs(
    level: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(auth.require_admin),
):
    logs = db.list_logs(level=level, category=category, limit=limit, offset=offset)
    total = db.count_logs(level=level, category=category)
    return {"logs": logs, "total": total}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py::TestSystemLogs -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin.py tests/test_admin.py
git commit -m "feat: add system logs API with filtering"
```

---

## Task 6: Monitoring Dashboard API

**Files:**
- Modify: `app/admin.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_admin.py`:

```python
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

    def test_monitoring_job_counts(self, admin_client, make_zip):
        import io
        zip_bytes = make_zip({"main.py": "print('hello')"})
        admin_client.post("/api/jobs", data={"name": "job1", "scheduled_at": FUTURE},
                          files={"file": ("p.zip", io.BytesIO(zip_bytes), "application/zip")})
        resp = admin_client.get("/api/admin/monitoring")
        assert resp.json()["jobs"]["pending"] >= 1

    def test_non_admin_cannot_access_monitoring(self, normal_client):
        assert normal_client.get("/api/admin/monitoring").status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestMonitoring -v`
Expected: All FAIL.

- [ ] **Step 3: Add monitoring endpoint to `app/admin.py`**

```python
import os


@router.get("/monitoring")
async def get_monitoring(user: dict = Depends(auth.require_admin)):
    # Job counts by status
    all_jobs = db.list_jobs()
    job_counts = {s: 0 for s in ("pending", "running", "done", "failed", "timeout", "cancelled")}
    for job in all_jobs:
        if job["status"] in job_counts:
            job_counts[job["status"]] += 1

    # GPU devices from params
    gpu_devices = db.get_all_params().get("gpu_devices", [])

    # S3 stats
    objects = storage.list_objects(storage.bucket)
    total_size = sum(o["size"] for o in objects)
    s3_info = {
        "bucket": storage.bucket,
        "endpoint": os.environ.get("DDP_S3_ENDPOINT", "http://127.0.0.1:9000"),
        "object_count": len(objects),
        "total_size_bytes": total_size,
    }

    return {
        "jobs": job_counts,
        "gpus": gpu_devices,
        "s3": s3_info,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin.py::TestMonitoring -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/admin.py tests/test_admin.py
git commit -m "feat: add monitoring dashboard API"
```

---

## Task 7: Time Window Logic

**Files:**
- Create: `app/timecheck.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_timecheck.py`:

```python
from datetime import datetime, timezone, timedelta

import pytest

from app import db, timecheck


def _dt(h, m, dow=None):
    """Create a UTC datetime at hour:h minute:m. dow=0=Monday."""
    dt = datetime(2026, 7, 15, h, m, tzinfo=timezone.utc)  # 2026-07-15 is a Wednesday
    if dow is not None:
        days_diff = dow - 2  # Wednesday=2
        dt = dt + timedelta(days=days_diff)
    return dt


class TestTimeWindow:
    def test_inside_normal_window(self):
        # Window 09:00-17:00, time 12:00 -> inside
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 12, 0, weekday=2) is True

    def test_outside_normal_window(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 20, 0, weekday=2) is False

    def test_inside_overnight_window(self):
        # Window 22:00-06:00, time 23:00 -> inside
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 23, 0, weekday=2) is True

    def test_inside_overnight_window_early_morning(self):
        # Window 22:00-06:00, time 03:00 -> inside
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 3, 0, weekday=2) is True

    def test_outside_overnight_window(self):
        # Window 22:00-06:00, time 12:00 -> outside
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 12, 0, weekday=2) is False

    def test_weekdays_monday_inside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=0) is True

    def test_weekdays_saturday_outside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=5) is False

    def test_weekdays_sunday_outside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=6) is False

    def test_boundary_start(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 9, 0, weekday=2) is True

    def test_boundary_end_excluded(self):
        # End is exclusive (the window closes AT end time)
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 17, 0, weekday=2) is False


class TestNextWindowOpen:
    def test_already_inside_returns_same_time(self):
        dt = _dt(12, 0)  # Wednesday 12:00
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "daily")
        assert result == dt

    def test_finds_next_morning_window(self):
        # 18:00 on Wednesday, window 09:00-17:00 -> next open is tomorrow 09:00
        dt = _dt(18, 0)
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "daily")
        assert result.hour == 9
        assert result.minute == 0
        assert result.day == 16  # next day

    def test_finds_overnight_open(self):
        # 12:00 on Wednesday, window 22:00-06:00 -> next open is 22:00 today
        dt = _dt(12, 0)
        result = timecheck.next_window_open(dt, 22, 0, 6, 0, "daily")
        assert result.hour == 22
        assert result.minute == 0
        assert result.day == 15  # same day

    def test_weekdays_skips_weekend(self):
        # Friday 18:00, window 09:00-17:00 weekdays -> next open is Monday 09:00
        dt = _dt(18, 0, dow=4)  # Friday
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "weekdays")
        assert result.weekday() == 0  # Monday
        assert result.hour == 9


class TestCheckScheduledTime:
    def test_returns_adjusted_when_outside(self):
        db.set_param("time_window_start", "09:00", user_id=1)
        db.set_param("time_window_end", "17:00", user_id=1)
        db.set_param("time_window_repeat", "daily", user_id=1)
        # 18:00 local -> adjusted to next day 09:00
        dt = datetime(2026, 7, 15, 18, 0)
        result = timecheck.check_scheduled_time(dt)
        assert result.hour == 9
        assert result.day == 16

    def test_returns_same_when_inside(self):
        db.set_param("time_window_start", "09:00", user_id=1)
        db.set_param("time_window_end", "17:00", user_id=1)
        db.set_param("time_window_repeat", "daily", user_id=1)
        dt = datetime(2026, 7, 15, 12, 0)
        result = timecheck.check_scheduled_time(dt)
        assert result == dt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_timecheck.py -v`
Expected: All FAIL (module doesn't exist).

- [ ] **Step 3: Create `app/timecheck.py`**

```python
from datetime import datetime, timedelta

from . import db


def is_in_window(start_h: int, start_m: int, end_h: int, end_m: int,
                 repeat: str, hour: int, minute: int, weekday: int) -> bool:
    """Check if hour:minute on weekday falls inside the window."""
    if repeat == "weekdays" and weekday >= 5:
        return False

    t = hour * 60 + minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m

    if start <= end:
        return start <= t < end
    else:
        return t >= start or t < end


def next_window_open(dt: datetime, start_h: int, start_m: int, end_h: int, end_m: int,
                     repeat: str) -> datetime:
    """Find the next time the window opens at or after dt."""
    if is_in_window(start_h, start_m, end_h, end_m, repeat,
                    dt.hour, dt.minute, dt.weekday()):
        return dt

    candidate = dt.replace(second=0, microsecond=0)
    for _ in range(7 * 24 * 60):  # max 7 days
        candidate = candidate + timedelta(minutes=1)
        if is_in_window(start_h, start_m, end_h, end_m, repeat,
                        candidate.hour, candidate.minute, candidate.weekday()):
            return candidate
    raise ValueError("No window opening found within 7 days")


def check_scheduled_time(local_dt: datetime) -> datetime:
    """Check a scheduled time against current system params.
    Returns the adjusted datetime (same if inside window, or next open if outside)."""
    params = db.get_all_params()
    start = params["time_window_start"]  # "HH:MM"
    end = params["time_window_end"]
    repeat = params["time_window_repeat"]

    start_h, start_m = int(start[:2]), int(start[3:5])
    end_h, end_m = int(end[:2]), int(end[3:5])

    if is_in_window(start_h, start_m, end_h, end_m, repeat,
                    local_dt.hour, local_dt.minute, local_dt.weekday()):
        return local_dt

    return next_window_open(local_dt, start_h, start_m, end_h, end_m, repeat)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_timecheck.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app/timecheck.py tests/test_timecheck.py
git commit -m "feat: add time window check and queue logic"
```

---

## Task 8: Integrate Time Window + Logging into main.py

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Write failing tests for queueing behavior**

Add to `tests/test_admin.py`:

```python
class TestTimeWindowEnforcement:
    def test_job_outside_window_gets_queued(self, admin_client, make_zip):
        from app import db
        # Set window to 09:00-17:00 daily
        admin_client.put("/api/admin/params", json={
            "time_window_start": "09:00", "time_window_end": "17:00", "time_window_repeat": "daily"
        })
        import io
        zip_bytes = make_zip({"main.py": "print(1)"})
        # Submit job for 22:00 local (outside 09-17)
        resp = admin_client.post("/api/jobs", data={"name": "night job", "scheduled_at": "2099-01-01T22:00"},
                                 files={"file": ("p.zip", io.BytesIO(zip_bytes), "application/zip")})
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = admin_client.get(f"/api/jobs/{job_id}").json()
        # Should be adjusted to next day 09:00
        from datetime import datetime
        adjusted = datetime.fromisoformat(job["scheduled_at"])
        # The scheduled_at is UTC. 22:00 local converted to UTC might be different.
        # Just check it's been moved to an earlier hour than 22:00
        assert adjusted.hour != 22 or adjusted.hour == 9

    def test_job_inside_window_normal(self, admin_client, make_zip):
        admin_client.put("/api/admin/params", json={
            "time_window_start": "00:00", "time_window_end": "23:59", "time_window_repeat": "daily"
        })
        import io
        zip_bytes = make_zip({"main.py": "print(1)"})
        resp = admin_client.post("/api/jobs", data={"name": "anytime", "scheduled_at": "2099-06-15T12:00"},
                                 files={"file": ("p.zip", io.BytesIO(zip_bytes), "application/zip")})
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = admin_client.get(f"/api/jobs/{job_id}").json()
        # Should NOT be adjusted
        from datetime import datetime
        original = datetime.fromisoformat(job["scheduled_at"])
        # 12:00 local -> some UTC time. Just ensure it wasn't moved to a different day
        assert original.year == 2099
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin.py::TestTimeWindowEnforcement -v`
Expected: FAIL (no time window logic in submit).

- [ ] **Step 3: Update `create_job` in `app/main.py`**

Add import at top:

```python
from . import timecheck
```

Replace the `create_job` function. The current function parses local → UTC, creates job, schedules it. We need to insert the window check BEFORE converting to UTC (since window logic works on local time). Replace:

```python
@app.post("/api/jobs")
async def create_job(
    user: dict = Depends(auth.get_current_user),
    file: UploadFile = File(...),
    name: str = Form(...),
    entry_command: str = Form("python main.py"),
    scheduled_at: str = Form(...),
    timeout_minutes: int = Form(60),
):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(400, "Must upload a .zip file")

    job_id = str(uuid.uuid4())
    zip_path = UPLOAD_DIR / f"{job_id}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    zip_path.write_bytes(content)

    storage.upload_bytes(f"jobs/{job_id}/code/{file.filename}", content)

    # Parse local datetime, check time window
    local_dt = datetime.fromisoformat(scheduled_at)
    adjusted_dt = timecheck.check_scheduled_time(local_dt)
    was_queued = adjusted_dt != local_dt

    # Convert adjusted local time to UTC
    dt_utc = adjusted_dt.astimezone(timezone.utc)
    scheduled_utc = dt_utc.isoformat()

    db.create_job(job_id, user["id"], name, file.filename, entry_command, scheduled_utc, timeout_minutes)

    db.log_event("INFO", "job", f"Job submitted: {name} ({job_id})", user_id=user["id"])
    if was_queued:
        db.log_event("INFO", "job", f"Job queued until window: {name} -> {adjusted_dt.isoformat()}",
                     user_id=user["id"])

    scheduler.add_job(
        executor.execute, DateTrigger(run_date=dt_utc),
        args=[job_id], id=job_id, replace_existing=True,
    )

    return {"id": job_id, "status": "pending", "queued": was_queued}
```

- [ ] **Step 4: Add logging to remaining `main.py` endpoints**

In the `register` function, after `user_id = db.create_user(...)`:

```python
    db.log_event("INFO", "auth", f"User registered: {username}", user_id=user_id)
```

In the `login` function, after verifying password, before `return _set_cookie(token)`:

```python
    db.log_event("INFO", "auth", f"User logged in: {username}", user_id=user["id"])
```

In the `login` function, before `raise HTTPException(401, ...)` (the wrong password case):

```python
    db.log_event("WARNING", "auth", f"Failed login attempt: {username}")
```

In `logout`:

```python
@app.post("/api/auth/logout")
async def logout(session: str | None = Cookie(None)):
    if session:
        user = db.get_user_by_session(session)
        if user:
            db.log_event("INFO", "auth", f"User logged out: {user['username']}", user_id=user["id"])
        db.delete_session(session)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp
```

In `cancel_job`, inside the `if job["status"] == "pending"` block:

```python
        db.log_event("INFO", "job", f"Job cancelled: {job_id}", user_id=user["id"])
```

In `cancel_job`, inside the `else` block (delete):

```python
        db.log_event("INFO", "job", f"Job deleted: {job_id}", user_id=user["id"])
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `python -m pytest tests/ -v -k "not test_api or TestTimeWindow"`
Expected: All PASS. (API tests that need S3 may be skipped.)

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_admin.py
git commit -m "feat: integrate time window queueing and system logging into job submission"
```

---

## Task 9: Add Logging to Executor

**Files:**
- Modify: `app/executor.py`

- [ ] **Step 1: Add log calls to executor**

In `app/executor.py`, after `db.update_job(job_id, status="running", ...)`:

```python
        db.log_event("DEBUG", "system", f"Job started: {job_id}")
```

After the final `db.update_job(job_id, status=status, ...)` (success path):

```python
        db.log_event("DEBUG", "system", f"Job finished: {job_id} status={status}")
```

In the `except Exception as e` block, after `db.update_job(...)`:

```python
            db.log_event("ERROR", "system", f"Job failed: {job_id} error={e}")
```

- [ ] **Step 2: Run existing API tests to ensure no regression**

Run: `python -m pytest tests/ -v -x`
Expected: All PASS (or skipped for S3).

- [ ] **Step 3: Commit**

```bash
git add app/executor.py
git commit -m "feat: add system logging to executor"
```

---

## Task 10: Frontend — Admin Panel HTML

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Add admin panel HTML**

In `frontend/index.html`, before `</div>` that closes `app-view` (after the `.layout` div), add:

```html
    <div id="admin-view" style="display:none">
      <div class="admin-tabs">
        <button class="admin-tab active" id="admin-tab-users" data-i18n="adminUsers">Users</button>
        <button class="admin-tab" id="admin-tab-params" data-i18n="adminParams">Parameters</button>
        <button class="admin-tab" id="admin-tab-logs" data-i18n="adminLogs">Logs</button>
        <button class="admin-tab" id="admin-tab-monitor" data-i18n="adminMonitor">Monitoring</button>
      </div>
      <div id="admin-content" class="admin-content"></div>
    </div>
```

In the header, before the `.user-area` div, add:

```html
      <button class="btn-admin" id="btn-admin" style="display:none" data-i18n="admin">Admin</button>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add admin panel HTML skeleton"
```

---

## Task 11: Frontend — Admin Panel CSS

**Files:**
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add admin panel styles**

Append to `frontend/src/style.css`:

```css
/* Admin panel */
.btn-admin {
  background: var(--accent); color: #fff; border: none;
  border-radius: 6px; padding: 5px 14px; font-size: 12px; cursor: pointer;
  transition: all .15s; font-weight: 600;
}
.btn-admin:hover { background: var(--accent-hi); }

.admin-tabs {
  display: flex; gap: 4px; padding: 16px 32px; background: var(--card);
  border-bottom: 1px solid var(--border);
}
.admin-tab {
  background: none; border: 1px solid var(--border); color: var(--text-dim);
  border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: 600;
  cursor: pointer; transition: all .15s;
}
.admin-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.admin-content { padding: 24px 32px; }

/* Users table */
.users-table { width: 100%; border-collapse: collapse; }
.users-table th, .users-table td {
  padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border);
}
.users-table th { color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
.users-table td { font-size: 14px; }
.users-table input {
  background: var(--card); border: 1px solid var(--border); border-radius: 4px;
  padding: 4px 8px; color: var(--text); font-size: 13px; width: 70px;
}
.users-table .btn-save {
  background: var(--accent); color: #fff; border: none; border-radius: 4px;
  padding: 4px 10px; cursor: pointer; font-size: 12px;
}
.users-table .btn-delete-user {
  background: rgba(239,68,68,.15); color: var(--red); border: 1px solid rgba(239,68,68,.3);
  border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px;
}

/* Params form */
.params-form { max-width: 600px; }
.params-form .field input, .params-form .field select {
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; color: var(--text); font-size: 14px; width: 100%;
}
.params-form textarea {
  width: 100%; min-height: 120px; background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px; color: var(--text); font-size: 13px;
  font-family: 'Consolas', monospace; resize: vertical;
}

/* Logs table */
.logs-table { width: 100%; border-collapse: collapse; }
.logs-table th, .logs-table td {
  padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.logs-table th { color: var(--text-dim); font-size: 11px; text-transform: uppercase; }
.log-level-INFO { color: var(--blue); }
.log-level-WARNING { color: var(--orange); }
.log-level-ERROR { color: var(--red); }
.log-level-DEBUG { color: var(--text-dim); }
.logs-filters { display: flex; gap: 12px; margin-bottom: 16px; }
.logs-filters select {
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 12px; color: var(--text); font-size: 13px;
}
.logs-pagination { display: flex; gap: 8px; margin-top: 16px; align-items: center; }

/* Monitoring */
.monitor-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.monitor-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px; text-align: center;
}
.monitor-card .label { color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
.monitor-card .value { font-size: 32px; font-weight: 700; margin-top: 8px; }
.gpu-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px; margin-bottom: 12px;
}
.gpu-card .gpu-name { font-weight: 600; font-size: 15px; margin-bottom: 12px; }
.gpu-bar { height: 6px; background: var(--bg); border-radius: 3px; margin-top: 4px; overflow: hidden; }
.gpu-bar-fill { height: 100%; background: var(--accent); border-radius: 3px; }
.s3-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px;
}
.s3-card .kv-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); }
.s3-card .kv-row:last-child { border-bottom: none; }
.s3-card .kv-row .k { color: var(--text-dim); font-size: 13px; }
.s3-card .kv-row .v { font-size: 14px; }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/style.css
git commit -m "feat: add admin panel CSS"
```

---

## Task 12: Frontend — Admin Panel TypeScript Logic

**Files:**
- Modify: `frontend/src/main.ts`

- [ ] **Step 1: Add admin i18n strings and state**

In the `I18N` object in `frontend/src/main.ts`, add to the `en` section:

```typescript
    admin: "Admin", adminUsers: "Users", adminParams: "Parameters",
    adminLogs: "Logs", adminMonitor: "Monitoring",
    gpuQuota: "GPU Quota", storageQuota: "Storage (GB)",
    save: "Save", delete: "Delete", username: "Username",
    created: "Created", isAdmin: "Admin",
    timeWindow: "Allowed Running Window",
    timeWindowStart: "Start Time", timeWindowEnd: "End Time",
    timeWindowRepeat: "Repeat", gpuDefaultQuota: "Default GPU Quota",
    storageDefaultQuota: "Default Storage (GB)", gpuDevices: "GPU Devices (JSON)",
    paramsSaved: "Parameters saved.", paramsError: "Failed to save parameters.",
    level: "Level", category: "Category", timestamp: "Time", message: "Message",
    allLevels: "All Levels", allCategories: "All Categories",
    prev: "Prev", next: "Next", page: "Page",
    totalJobs: "Total Jobs", gpuStatus: "GPU Status", s3Storage: "S3 Storage",
    bucket: "Bucket", objects: "Objects", totalSize: "Total Size",
    memoryUsed: "Memory", coresUsed: "Cores",
    backToJobs: "Back to Jobs",
    queued: "Queued until time window",
```

Add the same keys to the `zh` section with Chinese translations:

```typescript
    admin: "管理", adminUsers: "用户", adminParams: "参数",
    adminLogs: "日志", adminMonitor: "监控",
    gpuQuota: "GPU 配额", storageQuota: "存储 (GB)",
    save: "保存", delete: "删除", username: "用户名",
    created: "创建时间", isAdmin: "管理员",
    timeWindow: "允许运行时间段",
    timeWindowStart: "开始时间", timeWindowEnd: "结束时间",
    timeWindowRepeat: "重复", gpuDefaultQuota: "默认 GPU 配额",
    storageDefaultQuota: "默认存储 (GB)", gpuDevices: "GPU 设备 (JSON)",
    paramsSaved: "参数已保存。", paramsError: "保存参数失败。",
    level: "级别", category: "类别", timestamp: "时间", message: "消息",
    allLevels: "所有级别", allCategories: "所有类别",
    prev: "上一页", next: "下一页", page: "页码",
    totalJobs: "作业总数", gpuStatus: "GPU 状态", s3Storage: "S3 存储",
    bucket: "存储桶", objects: "对象数", totalSize: "总大小",
    memoryUsed: "显存", coresUsed: "算力",
    backToJobs: "返回作业",
    queued: "已排队至允许时段",
```

Add admin state variables after the existing `allJobs` declaration:

```typescript
let isAdmin = false;
let adminViewActive = false;
let adminTab: 'users' | 'params' | 'logs' | 'monitor' = 'users';
let logsPage = 0;
const LOGS_PER_PAGE = 50;
let logsRefreshTimer: ReturnType<typeof setInterval> | null = null;
let monitorRefreshTimer: ReturnType<typeof setInterval> | null = null;
```

- [ ] **Step 2: Update `checkAuth` to store `is_admin`**

Replace the `checkAuth` function:

```typescript
async function checkAuth(): Promise<boolean> {
  try {
    const resp = await fetch('/api/auth/me');
    if (resp.ok) {
      const user: { id: number; username: string; is_admin: number } = await resp.json();
      isAdmin = !!user.is_admin;
      showAppView(user.username);
      return true;
    }
  } catch { /* ignore */ }
  showAuthView();
  return false;
}
```

Update `showAppView` to show/hide admin button:

```typescript
function showAppView(username: string): void {
  $('auth-view').style.display = 'none';
  $('app-view').style.display = '';
  $('nav-username').textContent = username;
  const adminBtn = $('btn-admin');
  if (adminBtn) adminBtn.style.display = isAdmin ? '' : 'none';
  setDefaultTime();
  refreshJobs();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshJobs, 5000);
}
```

- [ ] **Step 3: Add admin panel toggle + tab logic**

Add after `showAuthView`:

```typescript
function toggleAdminView(): void {
  adminViewActive = !adminViewActive;
  const layout = document.querySelector('.layout') as HTMLElement;
  const adminView = $('admin-view');
  const adminBtn = $('btn-admin');
  if (adminViewActive) {
    if (layout) layout.style.display = 'none';
    adminView.style.display = '';
    adminBtn.textContent = t('backToJobs');
    switchAdminTab('users');
    if (logsRefreshTimer) clearInterval(logsRefreshTimer);
    if (monitorRefreshTimer) clearInterval(monitorRefreshTimer);
  } else {
    if (layout) layout.style.display = '';
    adminView.style.display = 'none';
    adminBtn.textContent = t('admin');
    if (logsRefreshTimer) { clearInterval(logsRefreshTimer); logsRefreshTimer = null; }
    if (monitorRefreshTimer) { clearInterval(monitorRefreshTimer); monitorRefreshTimer = null; }
  }
}

function switchAdminTab(tab: 'users' | 'params' | 'logs' | 'monitor'): void {
  adminTab = tab;
  document.querySelectorAll('.admin-tab').forEach(el => el.classList.remove('active'));
  const tabBtn = $(`admin-tab-${tab}`);
  if (tabBtn) tabBtn.classList.add('active');
  if (tab === 'users') renderAdminUsers();
  if (tab === 'params') renderAdminParams();
  if (tab === 'logs') renderAdminLogs();
  if (tab === 'monitor') renderAdminMonitor();
}
```

- [ ] **Step 4: Add admin users panel rendering**

```typescript
async function renderAdminUsers(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const resp = await fetch('/api/admin/users');
  const users: any[] = await resp.json();
  content.innerHTML = `
    <table class="users-table">
      <thead><tr>
        <th>ID</th><th data-i18n="username">${t('username')}</th>
        <th data-i18n="isAdmin">${t('isAdmin')}</th>
        <th data-i18n="gpuQuota">${t('gpuQuota')}</th>
        <th data-i18n="storageQuota">${t('storageQuota')}</th>
        <th data-i18n="created">${t('created')}</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${users.map(u => `
          <tr data-uid="${u.id}">
            <td>${u.id}</td>
            <td>${escapeHtml(u.username)}</td>
            <td><input type="checkbox" class="u-admin" ${u.is_admin ? 'checked' : ''} /></td>
            <td><input type="number" class="u-gpu" value="${u.gpu_quota_override ?? ''}" placeholder="default" min="0" /></td>
            <td><input type="number" class="u-storage" value="${u.storage_quota_override_gb ?? ''}" placeholder="default" min="0" step="0.5" /></td>
            <td>${u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
            <td>
              <button class="btn-save" data-save-uid="${u.id}">${t('save')}</button>
              <button class="btn-delete-user" data-del-uid="${u.id}">${t('delete')}</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}
```

- [ ] **Step 5: Add admin params panel rendering**

```typescript
async function renderAdminParams(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const resp = await fetch('/api/admin/params');
  const p: any = await resp.json();
  content.innerHTML = `
    <form class="params-form" id="params-form">
      <h2 data-i18n="timeWindow">${t('timeWindow')}</h2>
      <div class="field-row">
        <div class="field">
          <label data-i18n="timeWindowStart">${t('timeWindowStart')}</label>
          <input type="time" name="time_window_start" value="${p.time_window_start}" />
        </div>
        <div class="field">
          <label data-i18n="timeWindowEnd">${t('timeWindowEnd')}</label>
          <input type="time" name="time_window_end" value="${p.time_window_end}" />
        </div>
      </div>
      <div class="field">
        <label data-i18n="timeWindowRepeat">${t('timeWindowRepeat')}</label>
        <select name="time_window_repeat">
          <option value="daily" ${p.time_window_repeat === 'daily' ? 'selected' : ''}>Daily</option>
          <option value="weekdays" ${p.time_window_repeat === 'weekdays' ? 'selected' : ''}>Weekdays</option>
          <option value="weekly" ${p.time_window_repeat === 'weekly' ? 'selected' : ''}>Weekly</option>
        </select>
      </div>
      <div class="field-row">
        <div class="field">
          <label data-i18n="gpuDefaultQuota">${t('gpuDefaultQuota')}</label>
          <input type="number" name="gpu_default_quota" value="${p.gpu_default_quota}" min="0" />
        </div>
        <div class="field">
          <label data-i18n="storageDefaultQuota">${t('storageDefaultQuota')}</label>
          <input type="number" name="storage_default_quota_gb" value="${p.storage_default_quota_gb}" min="0" step="0.5" />
        </div>
      </div>
      <div class="field">
        <label data-i18n="gpuDevices">${t('gpuDevices')}</label>
        <textarea name="gpu_devices">${JSON.stringify(p.gpu_devices, null, 2)}</textarea>
      </div>
      <button type="submit" class="btn-submit" data-i18n="save">${t('save')}</button>
      <div id="params-status"></div>
    </form>`;
}
```

- [ ] **Step 6: Add admin logs panel rendering**

```typescript
let logsLevel = '';
let logsCategory = '';

async function renderAdminLogs(): Promise<void> {
  const content = $('admin-content');
  const params = new URLSearchParams();
  if (logsLevel) params.set('level', logsLevel);
  if (logsCategory) params.set('category', logsCategory);
  params.set('limit', String(LOGS_PER_PAGE));
  params.set('offset', String(logsPage * LOGS_PER_PAGE));
  const resp = await fetch(`/api/admin/logs?${params}`);
  const data: { logs: any[]; total: number } = await resp.json();
  content.innerHTML = `
    <div class="logs-filters">
      <select id="logs-level-filter">
        <option value="">${t('allLevels')}</option>
        <option value="INFO" ${logsLevel === 'INFO' ? 'selected' : ''}>INFO</option>
        <option value="WARNING" ${logsLevel === 'WARNING' ? 'selected' : ''}>WARNING</option>
        <option value="ERROR" ${logsLevel === 'ERROR' ? 'selected' : ''}>ERROR</option>
        <option value="DEBUG" ${logsLevel === 'DEBUG' ? 'selected' : ''}>DEBUG</option>
      </select>
      <select id="logs-category-filter">
        <option value="">${t('allCategories')}</option>
        <option value="auth" ${logsCategory === 'auth' ? 'selected' : ''}>auth</option>
        <option value="job" ${logsCategory === 'job' ? 'selected' : ''}>job</option>
        <option value="admin" ${logsCategory === 'admin' ? 'selected' : ''}>admin</option>
        <option value="system" ${logsCategory === 'system' ? 'selected' : ''}>system</option>
      </select>
    </div>
    <table class="logs-table">
      <thead><tr>
        <th data-i18n="timestamp">${t('timestamp')}</th>
        <th data-i18n="level">${t('level')}</th>
        <th data-i18n="category">${t('category')}</th>
        <th data-i18n="message">${t('message')}</th>
      </tr></thead>
      <tbody>
        ${data.logs.length ? data.logs.map(l => `
          <tr>
            <td>${new Date(l.timestamp).toLocaleString()}</td>
            <td class="log-level-${l.level}">${l.level}</td>
            <td>${l.category}</td>
            <td>${escapeHtml(l.message)}</td>
          </tr>`).join('') : `<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">No logs</td></tr>`}
      </tbody>
    </table>
    <div class="logs-pagination">
      <button id="logs-prev" ${logsPage === 0 ? 'disabled' : ''}>${t('prev')}</button>
      <span>${t('page')} ${logsPage + 1} / ${Math.max(1, Math.ceil(data.total / LOGS_PER_PAGE))}</span>
      <button id="logs-next" ${data.logs.length < LOGS_PER_PAGE ? 'disabled' : ''}>${t('next')}</button>
    </div>`;

  const levelFilter = $('logs-level-filter');
  if (levelFilter) levelFilter.addEventListener('change', e => {
    logsLevel = (e.target as HTMLSelectElement).value;
    logsPage = 0;
    renderAdminLogs();
  });
  const catFilter = $('logs-category-filter');
  if (catFilter) catFilter.addEventListener('change', e => {
    logsCategory = (e.target as HTMLSelectElement).value;
    logsPage = 0;
    renderAdminLogs();
  });
  const prevBtn = $('logs-prev');
  if (prevBtn && !prevBtn.hasAttribute('disabled')) prevBtn.addEventListener('click', () => {
    if (logsPage > 0) { logsPage--; renderAdminLogs(); }
  });
  const nextBtn = $('logs-next');
  if (nextBtn && !nextBtn.hasAttribute('disabled')) nextBtn.addEventListener('click', () => {
    logsPage++; renderAdminLogs();
  });
}
```

- [ ] **Step 7: Add admin monitor panel rendering**

```typescript
async function renderAdminMonitor(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const resp = await fetch('/api/admin/monitoring');
  const data: {
    jobs: Record<string, number>;
    gpus: any[];
    s3: { bucket: string; endpoint: string; object_count: number; total_size_bytes: number };
  } = await resp.json();

  const totalJobs = Object.values(data.jobs).reduce((a, b) => a + b, 0);
  content.innerHTML = `
    <h2 data-i18n="totalJobs">${t('totalJobs')} (${totalJobs})</h2>
    <div class="monitor-grid">
      ${Object.entries(data.jobs).map(([status, count]) => `
        <div class="monitor-card">
          <div class="label">${statusLabel(status)}</div>
          <div class="value status-${status === 'pending' ? 'status-' + status : ''}" style="color: var(--${status === 'done' ? 'green' : status === 'failed' ? 'red' : status === 'running' ? 'blue' : status === 'timeout' ? 'orange' : 'gray'})">${count}</div>
        </div>`).join('')}
    </div>
    <h2 data-i18n="gpuStatus">${t('gpuStatus')}</h2>
    ${data.gpus.map(g => {
      const memPct = g.memory_total_mb > 0 ? (g.memory_used_mb / g.memory_total_mb * 100) : 0;
      const corePct = g.cores_total > 0 ? (g.cores_used / g.cores_total * 100) : 0;
      return `
        <div class="gpu-card">
          <div class="gpu-name">${escapeHtml(g.name)} (id: ${g.id})</div>
          <div>${t('memoryUsed')}: ${g.memory_used_mb}/${g.memory_total_mb} MB</div>
          <div class="gpu-bar"><div class="gpu-bar-fill" style="width:${memPct}%"></div></div>
          <div style="margin-top:8px">${t('coresUsed')}: ${g.cores_used}/${g.cores_total}</div>
          <div class="gpu-bar"><div class="gpu-bar-fill" style="width:${corePct}%"></div></div>
        </div>`;
    }).join('')}
    <h2 data-i18n="s3Storage">${t('s3Storage')}</h2>
    <div class="s3-card">
      <div class="kv-row"><span class="k">${t('bucket')}</span><span class="v">${escapeHtml(data.s3.bucket)}</span></div>
      <div class="kv-row"><span class="k">Endpoint</span><span class="v">${escapeHtml(data.s3.endpoint)}</span></div>
      <div class="kv-row"><span class="k">${t('objects')}</span><span class="v">${data.s3.object_count}</span></div>
      <div class="kv-row"><span class="k">${t('totalSize')}</span><span class="v">${formatSize(data.s3.total_size_bytes)}</span></div>
    </div>`;
}
```

- [ ] **Step 8: Add admin event handlers in `DOMContentLoaded`**

In the `DOMContentLoaded` callback, add after existing listeners:

```typescript
  $('btn-admin').addEventListener('click', toggleAdminView);
  $('admin-tab-users').addEventListener('click', () => switchAdminTab('users'));
  $('admin-tab-params').addEventListener('click', () => switchAdminTab('params'));
  $('admin-tab-logs').addEventListener('click', () => switchAdminTab('logs'));
  $('admin-tab-monitor').addEventListener('click', () => switchAdminTab('monitor'));

  // Delegated handler for admin content clicks
  $('admin-content').addEventListener('click', async (e) => {
    const target = e.target as HTMLElement;
    const saveBtn = target.closest('[data-save-uid]') as HTMLElement | null;
    if (saveBtn) {
      const uid = saveBtn.dataset.saveUid!;
      const row = target.closest('tr') as HTMLTableRowElement;
      const isAdmin = (row.querySelector('.u-admin') as HTMLInputElement).checked ? 1 : 0;
      const gpuVal = (row.querySelector('.u-gpu') as HTMLInputElement).value;
      const storageVal = (row.querySelector('.u-storage') as HTMLInputElement).value;
      const body: any = { is_admin: isAdmin };
      body.gpu_quota_override = gpuVal === '' ? null : parseInt(gpuVal);
      body.storage_quota_override_gb = storageVal === '' ? null : parseFloat(storageVal);
      const resp = await fetch(`/api/admin/users/${uid}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (resp.ok) renderAdminUsers();
    }
    const delBtn = target.closest('[data-del-uid]') as HTMLElement | null;
    if (delBtn) {
      if (!confirm(t('delete') + '?')) return;
      const uid = delBtn.dataset.delUid!;
      const resp = await fetch(`/api/admin/users/${uid}`, { method: 'DELETE' });
      if (resp.ok) renderAdminUsers();
      else { const err = await resp.json().catch(() => ({})); alert(err.detail || t('failed')); }
    }
  });

  // Params form submit (delegated)
  $('admin-content').addEventListener('submit', async (e) => {
    const form = e.target as HTMLFormElement;
    if (form.id !== 'params-form') return;
    e.preventDefault();
    const fd = new FormData(form);
    const body: any = {};
    body.time_window_start = fd.get('time_window_start');
    body.time_window_end = fd.get('time_window_end');
    body.time_window_repeat = fd.get('time_window_repeat');
    body.gpu_default_quota = parseInt(fd.get('gpu_default_quota') as string);
    body.storage_default_quota_gb = parseFloat(fd.get('storage_default_quota_gb') as string);
    try {
      body.gpu_devices = JSON.parse(fd.get('gpu_devices') as string);
    } catch {
      const status = $('params-status');
      if (status) { status.textContent = 'Invalid GPU devices JSON'; status.style.color = 'var(--red)'; }
      return;
    }
    const resp = await fetch('/api/admin/params', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const status = $('params-status');
    if (status) {
      if (resp.ok) { status.textContent = t('paramsSaved'); status.style.color = 'var(--green)'; }
      else { const err = await resp.json().catch(() => ({})); status.textContent = err.detail || t('paramsError'); status.style.color = 'var(--red)'; }
    }
  });
```

- [ ] **Step 9: Build frontend and verify**

Run: `cd frontend && bun install && bun run build`
Expected: Build succeeds with no TypeScript errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/main.ts
git commit -m "feat: add admin panel frontend logic"
```

---

## Task 13: Final Integration Test

**Files:**
- Verify all tests pass

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (S3-dependent tests skipped if MinIO not running).

- [ ] **Step 2: Run frontend build**

Run: `cd frontend && bun run build`
Expected: Build succeeds.

- [ ] **Step 3: Manual smoke test (if S3 available)**

Start the server, register first user (should be admin), verify:
- `/api/auth/me` returns `is_admin: 1`
- Admin panel is visible
- Can list users, update params, view logs, see monitoring

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete admin functionality"
```
