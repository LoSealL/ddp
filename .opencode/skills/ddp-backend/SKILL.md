---
name: ddp-backend
description: Use when integrating with or calling the DDP (Delayed Dispatch Platform) backend API — submitting Python jobs, checking job status, fetching logs/outputs, or implementing auth flows. Covers all REST endpoints, session cookie auth, multipart upload, and the mock S3/executor swap path.
---

# DDP Backend Integration

## Overview

DDP is a FastAPI service that accepts zipped Python projects, schedules them for one-shot delayed execution, collects output artifacts to S3, and guarantees resource cleanup. This skill documents every API endpoint, the auth model, and how to swap mock components for production.

**Base URL:** `http://localhost:8000` (configurable via uvicorn `--host`/`--port`)

## When to Use

- Building a frontend, CLI, or SDK that calls the DDP API
- Writing integration tests against the backend
- Replacing mock executor/storage with K8s/AWS S3
- Debugging 401/422/409 errors from the API

## Auth Model

All `/api/jobs/*` endpoints require a valid session cookie. Auth uses server-side sessions (SQLite), not JWT.

### Flow

1. `POST /api/auth/register` or `POST /api/auth/login` with `multipart/form-data` (`username`, `password`)
2. Server responds with `Set-Cookie: session=<token>; HttpOnly; SameSite=Lax` (7-day expiry)
3. Include this cookie in all subsequent requests
4. `POST /api/auth/logout` invalidates the session server-side

### Constraints

- Username: min 2 chars
- Password: min 6 chars
- Duplicate username → HTTP 409
- Wrong password → HTTP 401
- Missing/expired cookie → HTTP 401 on protected routes

### Auth Endpoints

| Method | Path | Body (form-data) | Response |
|--------|------|-------------------|----------|
| POST | `/api/auth/register` | `username`, `password` | `{"ok":true,"username":"..."}` + Set-Cookie |
| POST | `/api/auth/login` | `username`, `password` | `{"ok":true}` + Set-Cookie |
| POST | `/api/auth/logout` | — (cookie required) | `{"ok":true}` + clears cookie |
| GET | `/api/auth/me` | — (cookie required) | `{"id":1,"username":"..."}` |

## Job API

All job endpoints require the session cookie. Jobs are scoped per-user — you only see/modify your own jobs.

### Submit Job

```
POST /api/jobs
Content-Type: multipart/form-data
Cookie: session=<token>
```

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file` | File (.zip) | Yes | — | Must end with `.zip` |
| `name` | String | Yes | — | Human-readable job name |
| `entry_command` | String | No | `python main.py` | Shell command run in project root |
| `scheduled_at` | String | Yes | — | `datetime-local` format: `YYYY-MM-DDTHH:MM` (interpreted as local time, converted to UTC server-side) |
| `timeout_minutes` | Integer | No | `60` | Hard kill limit. 1–1440. |

**Response:** `{"id":"<uuid>","status":"pending"}`

**Errors:**
- 401: Not authenticated
- 400: File is not a .zip

### List Jobs

```
GET /api/jobs
Cookie: session=<token>
```

**Response:** Array of job objects (newest first), scoped to current user.

```json
[
  {
    "id": "uuid",
    "name": "nightly ETL",
    "filename": "project.zip",
    "entry_command": "python main.py",
    "scheduled_at": "2026-07-13T15:22:00+00:00",
    "timeout_minutes": 60,
    "status": "done",
    "created_at": "2026-07-13T15:21:48+00:00",
    "started_at": "2026-07-13T15:22:00+00:00",
    "finished_at": "2026-07-13T15:22:01+00:00",
    "s3_prefix": "ddp/jobs/<uuid>/",
    "output_count": 2,
    "error": null,
    "user_id": 1
  }
]
```

### Get Job Detail

```
GET /api/jobs/{job_id}
Cookie: session=<token>
```

Returns the job object (same shape as list item). 404 if not found or not owned by current user.

### Get Job Logs

```
GET /api/jobs/{job_id}/logs
Cookie: session=<token>
```

**Response:** `{"logs": "...full stdout+stderr..."}`

Logs include pip install output (if `requirements.txt` exists) and the entry command output. Empty string if job hasn't run yet.

### Get Job Outputs

```
GET /api/jobs/{job_id}/outputs
Cookie: session=<token>
```

**Response:**
```json
{
  "outputs": [
    {
      "key": "jobs/<uuid>/output/result.txt",
      "size": 65,
      "s3_uri": "s3://ddp/jobs/<uuid>/output/result.txt"
    }
  ]
}
```

Download artifacts via the static mount: `GET /s3/{key}` (no auth required in mock mode).

### Cancel / Delete Job

```
DELETE /api/jobs/{job_id}
Cookie: session=<token>
```

Behavior depends on job status:

| Status | Action | Result |
|--------|--------|--------|
| `pending` | Cancels scheduled trigger, marks `cancelled` | `{"ok":true}` |
| `running` | Rejected | HTTP 409 |
| `done`/`failed`/`timeout`/`cancelled` | Deletes from DB | `{"ok":true}` |

## Job Lifecycle

```
pending → running → done
                 → failed
                 → timeout
         → cancelled (via DELETE while pending)
```

| Status | Meaning |
|--------|---------|
| `pending` | Scheduled, waiting for trigger time |
| `running` | Executor has started, process is alive |
| `done` | Process exited 0, outputs collected |
| `failed` | Process exited non-zero, or exception occurred |
| `timeout` | Process killed after `timeout_minutes` |
| `cancelled` | User cancelled before execution started |

## Submitted Project Format

```
my-project.zip
├── main.py              Entry script (default: python main.py)
├── requirements.txt     Optional, auto pip install before entry command
├── output/              Optional, all files auto-uploaded to S3
└── manifest.json        Optional, declares additional output paths
```

### manifest.json

```json
{
  "outputs": ["report.pdf", "data/final.csv"]
}
```

Paths are relative to project root. Both `output/` dir and manifest paths are collected (deduplication not performed — if a file appears in both, it uploads twice).

### Zip structure handling

If the zip contains a single top-level directory, its contents are lifted to the workspace root automatically. So these two structures are equivalent:

```
zip A:                    zip B:
my-project/               main.py
  main.py                 requirements.txt
  requirements.txt
```

## Quick Reference: curl

```bash
# Register (saves cookie to cookies.txt)
curl -c cookies.txt -X POST http://localhost:8000/api/auth/register \
  -F "username=myuser" -F "password=mypass123"

# Login (if already registered)
curl -c cookies.txt -X POST http://localhost:8000/api/auth/login \
  -F "username=myuser" -F "password=mypass123"

# Check session
curl -b cookies.txt http://localhost:8000/api/auth/me

# Submit job
curl -b cookies.txt -X POST http://localhost:8000/api/jobs \
  -F "name=My Job" \
  -F "file=@project.zip" \
  -F "entry_command=python main.py" \
  -F "scheduled_at=2026-07-14T15:00" \
  -F "timeout_minutes=30"

# List jobs
curl -b cookies.txt http://localhost:8000/api/jobs

# Get job detail
curl -b cookies.txt http://localhost:8000/api/jobs/<job_id>

# Get logs
curl -b cookies.txt http://localhost:8000/api/jobs/<job_id>/logs

# Get outputs
curl -b cookies.txt http://localhost:8000/api/jobs/<job_id>/outputs

# Cancel pending job
curl -b cookies.txt -X DELETE http://localhost:8000/api/jobs/<job_id>

# Logout
curl -b cookies.txt -X POST http://localhost:8000/api/auth/logout
```

## Quick Reference: Python (requests)

```python
import requests

base = "http://localhost:8000"
s = requests.Session()

# Register or login
s.post(f"{base}/api/auth/register", data={"username": "myuser", "password": "mypass123"})
# or:
# s.post(f"{base}/api/auth/login", data={"username": "myuser", "password": "mypass123"})

# Submit job
with open("project.zip", "rb") as f:
    resp = s.post(f"{base}/api/jobs", files={"file": f}, data={
        "name": "My Job",
        "entry_command": "python main.py",
        "scheduled_at": "2026-07-14T15:00",
        "timeout_minutes": 30,
    })
job_id = resp.json()["id"]

# Poll status
job = s.get(f"{base}/api/jobs/{job_id}").json()
print(job["status"])

# Get logs
logs = s.get(f"{base}/api/jobs/{job_id}/logs").json()["logs"]

# Get outputs
outputs = s.get(f"{base}/api/jobs/{job_id}/outputs").json()["outputs"]
for o in outputs:
    print(o["s3_uri"], o["size"])
```

## Quick Reference: JavaScript (fetch)

```javascript
// Login (cookie is set automatically by browser)
await fetch('/api/auth/login', {
  method: 'POST',
  body: new FormData(document.querySelector('#login-form')),
});

// Submit job
const fd = new FormData();
fd.append('name', 'My Job');
fd.append('file', fileInput.files[0]);
fd.append('entry_command', 'python main.py');
fd.append('scheduled_at', '2026-07-14T15:00');
fd.append('timeout_minutes', '30');
const resp = await fetch('/api/jobs', { method: 'POST', body: fd });
const { id } = await resp.json();

// List jobs
const jobs = await (await fetch('/api/jobs')).json();
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| 401 on all job requests | Call `/api/auth/login` or `/api/auth/register` first; ensure cookie is sent |
| 422 on job submit | All form fields including `file` must have a `name` attribute; `scheduled_at` is required |
| 400 "Must upload a .zip file" | Ensure uploaded file ends with `.zip` |
| 404 on job detail | Job doesn't exist or belongs to another user |
| 409 on cancel | Cannot cancel a `running` job in mock mode |
| Job runs immediately | `scheduled_at` is in the past — server schedules it for immediate execution |
| Outputs not collected | Files must be in `output/` dir or listed in `manifest.json` at project root |
| `scheduled_at` timezone confusion | HTML `datetime-local` gives naive local time; server converts to UTC. API callers should send local time string without timezone suffix |

## Mock → Production Swap

The backend is designed so two modules can be replaced without touching API routes:

### Executor: `app/executor.py`

| Mock | Production |
|------|-----------|
| `asyncio.create_subprocess_shell` | K8s `Job` with `activeDeadlineSeconds`, `ttlSecondsAfterFinished`, `backoffLimit: 0` |
| `proc.kill()` on timeout | `activeDeadlineSeconds` auto-terminates Pod |
| `shutil.rmtree(work_dir)` | `ttlSecondsAfterFinished: 60` auto-cleans Pod |
| No GPU | HAMI vGPU scheduling via K8s device plugin annotations |

The `execute(job_id)` method signature stays the same. Replace the internal implementation with a K8s client (`kubernetes` Python lib) that creates a `Job` manifest and watches for completion.

### Storage: `app/storage.py`

| Mock | Production |
|------|-----------|
| `shutil.copy2` to local dir | `boto3` `S3Client.upload_file` |
| `Path.rglob` for listing | `boto3` `list_objects_v2` |

The `upload_file(bucket, key, local_path)` and `list_objects(bucket, prefix)` method signatures stay the same. Replace internals with boto3 calls.

### Database: `app/db.py`

SQLite → PostgreSQL/MySQL. All functions use standard SQL, no SQLite-specific syntax. Swap the connection string and driver.

## Architecture Reference

```
┌─────────────┐     ┌──────────────────────────────────┐
│  Client     │────▶│  FastAPI (app/main.py)           │
│ (browser/   │ REST│  ├── Auth (app/auth.py)          │
│  CLI/SDK)   │     │  ├── DB (app/db.py, SQLite)      │
└─────────────┘     │  ├── Scheduler (APScheduler)     │
                    │  ├── Executor (app/executor.py)  │──▶ subprocess / K8s Job
                    │  └── Storage (app/storage.py)    │──▶ filesystem / S3
                    └──────────────────────────────────┘
```

### File Map

| File | Responsibility |
|------|---------------|
| `app/main.py` | FastAPI routes, lifespan, scheduler init, frontend serving |
| `app/auth.py` | Password hashing (pbkdf2_hmac), session tokens, `get_current_user` dependency |
| `app/db.py` | SQLite: `jobs`, `users`, `sessions` tables; all CRUD functions |
| `app/executor.py` | Unzip → pip install → run command → collect outputs → cleanup |
| `app/storage.py` | Mock S3: `upload_file`, `list_objects` (swap for boto3) |
| `frontend/index.html` | Single-page app: auth, job submit, list, detail modal, i18n |
| `app/main.py:24-28` | `_parse_local_to_utc`: converts `datetime-local` to UTC ISO |
| `app/main.py:31-45` | `lifespan`: reschedules pending jobs on restart |
