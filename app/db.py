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
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN user_id INTEGER")
    except sqlite3.OperationalError:
        pass
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
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (username, password_hash, salt, now),
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
    row = conn.execute("SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
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
        SELECT u.id, u.username FROM users u
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
