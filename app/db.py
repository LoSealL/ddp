import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ddp.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            filename        TEXT NOT NULL,
            entry_command   TEXT NOT NULL DEFAULT 'python main.py',
            scheduled_at    TEXT NOT NULL,
            timeout_minutes INTEGER NOT NULL DEFAULT 60,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            finished_at     TEXT,
            s3_prefix       TEXT,
            output_count    INTEGER DEFAULT 0,
            error           TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            salt            TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            expires_at  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
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
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN user_id INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN gpu_quota_override INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN storage_quota_override_gb REAL")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    default_params = [
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
    for key, value in default_params:
        conn.execute(
            "INSERT OR IGNORE INTO system_params (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
    conn.commit()
    conn.close()


def create_job(job_id, user_id, name, filename, entry_command, scheduled_at, timeout_minutes):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO jobs (id, user_id, name, filename, entry_command, scheduled_at, timeout_minutes, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (job_id, user_id, name, filename, entry_command, scheduled_at, timeout_minutes, now))
    conn.commit()
    conn.close()


def get_job(job_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_jobs(user_id=None):
    conn = get_db()
    if user_id is not None:
        rows = conn.execute("SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job(job_id, **kwargs):
    conn = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_job(job_id):
    conn = get_db()
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()


# ── Users & Sessions ─────────────────────────────────

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


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_session(token, user_id, expires_at):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, expires_at, now),
    )
    conn.commit()
    conn.close()


def get_user_by_session(token):
    conn = get_db()
    row = conn.execute("""
        SELECT u.id, u.username, u.is_admin FROM users u
        JOIN sessions s ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?
    """, (token, datetime.now(timezone.utc).isoformat())).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Admin: Users ──────────────────────────────────────

def list_users():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, username, is_admin, gpu_quota_override, storage_quota_override_gb, created_at
        FROM users ORDER BY id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_ALLOWED_USER_COLS = {"is_admin", "gpu_quota_override", "storage_quota_override_gb"}


def update_user(user_id, **kwargs):
    bad = set(kwargs) - _ALLOWED_USER_COLS
    if bad:
        raise ValueError(f"Cannot update columns: {bad}")
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
    count = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
    conn.close()
    return count


# ── Admin: System Params ──────────────────────────────

def get_all_params():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM system_params").fetchall()
    conn.close()
    params = {r["key"]: r["value"] for r in rows}
    if "gpu_default_quota" in params:
        params["gpu_default_quota"] = int(params["gpu_default_quota"])
    if "storage_default_quota_gb" in params:
        params["storage_default_quota_gb"] = float(params["storage_default_quota_gb"])
    if "gpu_devices" in params:
        params["gpu_devices"] = json.loads(params["gpu_devices"])
    return params


def get_param(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM system_params WHERE key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    value = row["value"]
    if key == "gpu_default_quota":
        return int(value)
    if key == "storage_default_quota_gb":
        return float(value)
    return value


def set_param(key, value, user_id=None):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO system_params (key, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by
    """, (key, value, now, user_id))
    conn.commit()
    conn.close()


# ── Admin: System Logs ────────────────────────────────

def log_event(level, category, message, user_id=None, details=None):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO system_logs (timestamp, level, category, message, user_id, details)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (now, level, category, message, user_id, details))
    conn.commit()
    conn.close()


def list_logs(level=None, category=None, limit=100, offset=0):
    conn = get_db()
    clauses = []
    params = []
    if level is not None:
        clauses.append("level = ?")
        params.append(level)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]
    rows = conn.execute(
        f"SELECT * FROM system_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_logs(level=None, category=None):
    conn = get_db()
    clauses = []
    params = []
    if level is not None:
        clauses.append("level = ?")
        params.append(level)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    count = conn.execute(f"SELECT COUNT(*) FROM system_logs{where}", params).fetchone()[0]
    conn.close()
    return count
