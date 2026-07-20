import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ddp.db"

# All timestamps are local wall-clock time; no UTC conversion anywhere.
# Offset is an admin-tunable system param (tz_offset_hours), default +8.
_tz_cache: tuple[float, timezone | None] = (0.0, None)


def get_tz() -> timezone:
    global _tz_cache
    ts, tz = _tz_cache
    if tz is None or time.time() - ts > 30:
        off = 8
        try:
            v = get_param("tz_offset_hours")
            if v is not None:
                off = int(v)
        except Exception:
            pass
        tz = timezone(timedelta(hours=off))
        _tz_cache = (time.time(), tz)
    return tz


def now_iso():
    return datetime.now(get_tz()).isoformat()


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    now = now_iso()
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
        conn.execute("ALTER TABLE jobs ADD COLUMN gpus INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN gpu_mem_mb INTEGER")
    except sqlite3.OperationalError:
        pass
    for col, ddl in [("image", "TEXT"), ("ssh_port", "INTEGER"), ("ssh_password", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN output_path TEXT NOT NULL DEFAULT 'output'")
    except sqlite3.OperationalError:
        pass
    for ddl in ["ALTER TABLE jobs ADD COLUMN cpu REAL NOT NULL DEFAULT 2",
                "ALTER TABLE jobs ADD COLUMN memory_gb REAL NOT NULL DEFAULT 4",
                "ALTER TABLE users ADD COLUMN cpu_quota_override REAL",
                "ALTER TABLE users ADD COLUMN memory_quota_override_gb REAL"]:
        try:
            conn.execute(ddl)
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
    for ddl in ["ALTER TABLE jobs ADD COLUMN repeat_type TEXT NOT NULL DEFAULT 'none'",
                "ALTER TABLE jobs ADD COLUMN repeat_weekdays TEXT"]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    default_params = [
        ("time_window_start", "22:00"),
        ("time_window_end", "06:00"),
        ("time_window_repeat", "daily"),
        ("gpu_default_quota", "1"),
        ("storage_default_quota_gb", "10.0"),
        ("gpu_devices", "[]"),
        ("tz_offset_hours", "8"),
        ("cpu_default_quota", "8"),
        ("memory_default_quota_gb", "32"),
    ]
    for key, value in default_params:
        conn.execute(
            "INSERT OR IGNORE INTO system_params (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
    conn.commit()
    conn.close()


def create_job(job_id, user_id, name, image, entry_command, scheduled_at, timeout_minutes,
               gpus=0, gpu_mem_mb=None, ssh_port=None, ssh_password=None, status="pending",
               output_path="output", cpu=2, memory_gb=4,
               repeat_type="none", repeat_weekdays=None):
    now = now_iso()
    conn = get_db()
    conn.execute("""
        INSERT INTO jobs (id, user_id, name, filename, image, entry_command, scheduled_at, timeout_minutes,
                          gpus, gpu_mem_mb, ssh_port, ssh_password, status, output_path, cpu, memory_gb,
                          repeat_type, repeat_weekdays, created_at)
        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, user_id, name, image, entry_command, scheduled_at, timeout_minutes,
          gpus, gpu_mem_mb, ssh_port, ssh_password, status, output_path, cpu, memory_gb,
          repeat_type, repeat_weekdays, now))
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
    now = now_iso()
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
    now = now_iso()
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
    """, (token, now_iso())).fetchone()
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
        SELECT id, username, is_admin, gpu_quota_override, storage_quota_override_gb,
               cpu_quota_override, memory_quota_override_gb, created_at
        FROM users ORDER BY id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_ALLOWED_USER_COLS = {"is_admin", "gpu_quota_override", "storage_quota_override_gb",
                      "cpu_quota_override", "memory_quota_override_gb"}


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


def update_user_password(user_id, password_hash, salt):
    conn = get_db()
    conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                 (password_hash, salt, user_id))
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
    for key in ("gpu_default_quota", "tz_offset_hours", "cpu_default_quota"):
        if key in params:
            params[key] = int(params[key])
    for key in ("storage_default_quota_gb", "memory_default_quota_gb"):
        if key in params:
            params[key] = float(params[key])
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
    if key in ("gpu_default_quota", "tz_offset_hours", "cpu_default_quota"):
        return int(value)
    if key in ("storage_default_quota_gb", "memory_default_quota_gb"):
        return float(value)
    return value


def set_param(key, value, user_id=None):
    now = now_iso()
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
    now = now_iso()
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


def clear_logs():
    conn = get_db()
    conn.execute("DELETE FROM system_logs")
    conn.commit()
    conn.close()


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
