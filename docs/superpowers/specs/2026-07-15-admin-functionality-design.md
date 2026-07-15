# Admin Functionality for DDP

**Date:** 2026-07-15
**Status:** Approved (brainstormed)

## Goal

Add administrator role, per-user quotas, global time windows (with queueing), system logging, and a monitoring dashboard to the DDP platform. Normal users remain limited to submitting and managing their own jobs.

## Decisions (from brainstorm)

| Decision | Choice |
|----------|--------|
| First admin account | First registered user (id=1) becomes admin automatically |
| Quota scope | Global default + per-user override (NULL = use default) |
| Time window scope | Global (one window for all users) |
| Window enforcement | Queue until window opens (adjust `scheduled_at` to next opening) |
| HAMI mock | Static configurable GPU device list stored in `system_params` |

## Architecture

Single new module `app/admin.py` containing an `APIRouter` for all admin endpoints. Schema/functions stay in `db.py`. Auth gains a `require_admin` dependency. `main.py` includes the admin router.

```
app/
├── main.py          (+ include admin router, time-window check on submit)
├── auth.py          (+ is_admin on create, require_admin dependency)
├── db.py            (+ users.is_admin/quota cols, system_params, system_logs tables + functions)
├── admin.py         (NEW — admin APIRouter: users, params, logs, monitoring)
├── executor.py      (unchanged)
├── storage.py       (unchanged)
frontend/
├── index.html       (+ admin panel section)
├── src/main.ts      (+ admin panel logic)
```

## Schema Changes (db.py)

### ALTER `users` table

New columns added via `try/except sqlite3.OperationalError` (same pattern as existing `user_id` migration):

- `is_admin INTEGER NOT NULL DEFAULT 0`
- `gpu_quota_override INTEGER` — NULL = use system default
- `storage_quota_override_gb REAL` — NULL = use system default

### NEW `system_params` table

Key-value store for global configuration.

| Column | Type | Notes |
|--------|------|-------|
| `key` | TEXT PRIMARY KEY | |
| `value` | TEXT NOT NULL | JSON-encoded for complex values |
| `updated_at` | TEXT NOT NULL | ISO timestamp |
| `updated_by` | INTEGER | FK users.id |

**Seeded defaults on `init_db()` (INSERT OR IGNORE):**

| Key | Default Value | Type |
|-----|---------------|------|
| `time_window_start` | `"22:00"` | HH:MM string |
| `time_window_end` | `"06:00"` | HH:MM string |
| `time_window_repeat` | `"daily"` | One of: `daily`, `weekly`, `weekdays` |
| `gpu_default_quota` | `1` | Integer (per-user GPU count) |
| `storage_default_quota_gb` | `10.0` | Float (GB per-user) |
| `gpu_devices` | `[{"id":0,"name":"GPU-0","memory_total_mb":16384,"memory_used_mb":0,"cores_total":100,"cores_used":0},...]` | JSON array (HAMI mock) |

### NEW `system_logs` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `timestamp` | TEXT NOT NULL | ISO UTC |
| `level` | TEXT NOT NULL | `INFO`, `WARNING`, `ERROR`, `DEBUG` |
| `category` | TEXT NOT NULL | `auth`, `job`, `admin`, `system` |
| `message` | TEXT NOT NULL | Human-readable |
| `user_id` | INTEGER | NULL if not user-related |
| `details` | TEXT | JSON string for extra context |

## Auth Changes (auth.py)

### `create_user` behavior

When `db.create_user()` is called and there are zero existing users, the new user gets `is_admin=1`. Implementation: `db.create_user()` checks `SELECT COUNT(*) FROM users`; if 0, sets `is_admin=1`.

### `require_admin` dependency

```python
async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    # get_current_user already returns id, username
    # need to fetch is_admin from db
```

`get_current_user` must be updated to also return `is_admin` (query the full row instead of just id+username). `require_admin` raises `HTTPException(403)` if `is_admin == 0`.

### `/api/auth/me` response

Now returns `{"id": ..., "username": ..., "is_admin": 0|1}`.

## Time Window Logic

### Window Check

Given `time_window_start`, `time_window_end` (HH:MM strings) and `time_window_repeat` (`daily`/`weekly`/`weekdays`):

1. Parse a candidate `scheduled_at` (UTC ISO) and convert to local time.
2. Extract local weekday + HH:MM.
3. Determine if the time falls inside the window:
   - **Normal window** (start <= end, e.g. 09:00-17:00): local HH:MM within [start, end].
   - **Overnight window** (start > end, e.g. 22:00-06:00): local HH:MM >= start OR < end.
4. For `weekdays` repeat: also require Mon-Fri.
5. For `weekly` repeat: window applies every day but the user is telling us the day-of-week pattern... actually `weekly` means it recurs weekly on the same day. Simpler interpretation: `daily` = every day, `weekdays` = Mon-Fri only, `weekly` = treat the specific weekday of the window as the allowed day. Given complexity, `weekly` will be treated as: window opens on the same calendar day each week (the day of the first scheduled time). For simplicity in the mock, `weekly` = every 7th day from the job's scheduled date.

**Simplified decision:** `weekly` is rarely useful for this platform. We implement `daily` and `weekdays` fully. `weekly` maps to `daily` behavior with a TODO comment. This keeps the implementation honest.

### Queueing on Submit

In `create_job` route:

1. Parse `scheduled_at` → `dt` (UTC).
2. Check if `dt` is inside the window.
3. If inside: schedule normally with `DateTrigger(run_date=dt)`.
4. If outside: compute next window opening (`_next_window_open(dt)`), adjust `scheduled_at` to that time in DB and scheduler. The job stays `pending`. User sees the adjusted time via job detail.
5. Log both submission and any queue adjustment.

### `_next_window_open(dt)`

Starting from `dt`, iterate forward in 1-minute increments until a time is found inside the window. Ceiling: 7 days (raises error after). This is simple and correct; performance is irrelevant for once-per-submit.

## System Logging

### `db.log_event(level, category, message, user_id=None, details=None)`

Called at these points:

| Location | Level | Category | Message example |
|----------|-------|----------|-----------------|
| `register` | INFO | auth | "User registered: {username}" |
| `login` | INFO | auth | "User logged in: {username}" |
| `logout` | INFO | auth | "User logged out: {username}" |
| `create_job` | INFO | job | "Job submitted: {name} ({id})" |
| `create_job` (queued) | INFO | job | "Job queued until window opens: {name} -> {adjusted_time}" |
| `cancel_job` | INFO | job | "Job cancelled: {id}" |
| `delete_job` | INFO | job | "Job deleted: {id}" |
| `executor.execute` start | DEBUG | system | "Job started: {id}" |
| `executor.execute` end | DEBUG | system | "Job finished: {id} status={status}" |
| `executor.execute` error | ERROR | system | "Job failed: {id} error={e}" |
| Admin: param update | INFO | admin | "System params updated by {username}" |
| Admin: user update | INFO | admin | "User {target} updated by {username}" |
| Admin: user delete | WARNING | admin | "User {target} deleted by {username}" |
| Failed login | WARNING | auth | "Failed login attempt: {username}" |

`details` is a JSON string for structured data (e.g. `{"key": "gpu_default_quota", "old": 1, "new": 2}`).

## Admin API (app/admin.py)

All endpoints require `Depends(require_admin)`.

### User Management

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/admin/users` | — | `[{id, username, is_admin, gpu_quota_override, storage_quota_override_gb, created_at}, ...]` |
| PATCH | `/api/admin/users/{id}` | `is_admin?`, `gpu_quota_override?`, `storage_quota_override_gb?` | `{ok: true}` |
| DELETE | `/api/admin/users/{id}` | — | `{ok: true}` |

**PATCH validation:**
- Cannot remove admin from self (403).
- Cannot remove admin from the last remaining admin (403).

**DELETE validation:**
- Cannot delete self (403).
- Cannot delete the last admin (403).

### System Parameters

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/admin/params` | — | `{time_window_start, time_window_end, time_window_repeat, gpu_default_quota, storage_default_quota_gb, gpu_devices: [...]}` |
| PUT | `/api/admin/params` | Any subset of param keys | `{ok: true}` |

PUT accepts a JSON body with any subset of keys. Each provided key is validated and updated. Unknown keys → 400. Logs the change with old/new values in `details`.

### Logs

| Method | Path | Query | Response |
|--------|------|-------|----------|
| GET | `/api/admin/logs` | `level?`, `category?`, `limit=100`, `offset=0` | `{total: N, logs: [{...}]}` |

Returns newest first. `total` is the count after applying level/category filters (before limit/offset) so frontend can paginate.

### Monitoring

| Method | Path | Response |
|--------|------|----------|
| GET | `/api/admin/monitoring` | Dashboard JSON (see below) |

```json
{
  "jobs": {
    "running": 2,
    "pending": 5,
    "done": 13,
    "failed": 1,
    "timeout": 0,
    "cancelled": 2
  },
  "gpus": [
    {"id":0, "name":"GPU-0", "memory_total_mb":16384, "memory_used_mb":4000, "cores_total":100, "cores_used":25}
  ],
  "s3": {
    "bucket": "ddp",
    "endpoint": "http://127.0.0.1:9000",
    "object_count": 42,
    "total_size_bytes": 123456789
  }
}
```

- **Jobs by status:** `SELECT status, COUNT(*) FROM jobs GROUP BY status` across ALL users.
- **GPUs:** return `gpu_devices` from `system_params` as-is.
- **S3:** `storage.list_objects(bucket)` then sum sizes + count. (Mock mode: could be slow with many objects; acceptable for now.)

## Frontend Changes

### Auth → App transition

`/api/auth/me` response now includes `is_admin`. Store it in app state. Show an **Admin** button in the header when `is_admin == 1`.

### Admin Panel

A new view toggled by the Admin button (hides the job submit/list view, shows admin panel). Admin panel has 4 tabbed sections:

1. **Users** — Table: username, admin toggle (checkbox), GPU quota override (number input, blank=default), storage quota override (number input, blank=default), created date, delete button. Save button per row or inline edit + PATCH on blur.

2. **Parameters** — Form with:
   - Time window start (`<input type="time">`), end (`<input type="time">`), repeat (`<select>` with daily/weekly/weekdays).
   - GPU default quota (number), storage default quota (number, GB).
   - GPU devices JSON editor (`<textarea>` with JSON, or a simple repeatable card list). Textarea is simpler.
   - Save button → PUT.

3. **Logs** — Filterable table:
   - Level filter (`<select>`: all/info/warning/error/debug).
   - Category filter (`<select>`: all/auth/job/admin/system).
   - Table columns: timestamp, level, category, message, user.
   - Pagination (limit/offset).
   - Auto-refresh every 10s (toggleable).

4. **Monitoring** — Summary cards:
   - Job status counts (6 colored cards).
   - GPU cards (one per device: memory bar, core usage bar).
   - S3 stats card (bucket, endpoint, object count, total size).
   - Auto-refresh every 10s.

### i18n

All admin panel strings added to the `I18N` dict in both `en` and `zh`.

## Testing

Following the project's existing test pattern (`tests/` directory). New tests:

- `test_admin.py`:
  - First registered user is admin.
  - Second user is not admin.
  - Admin can list/update/delete users.
  - Non-admin gets 403 on admin endpoints.
  - Cannot delete last admin / self.
  - Params get/set round-trip.
  - Time window queueing adjusts `scheduled_at`.
  - Logs are written and retrievable.
  - Monitoring returns expected shape.

Each test uses the existing pattern: create a fresh DB, register users, call endpoints.

## File Change Summary

| File | Change |
|------|--------|
| `app/db.py` | Add columns, 2 new tables, new functions (`log_event`, `list_logs`, `get/set system params`, admin user CRUD) |
| `app/auth.py` | `create_user` → admin for id=1; `get_current_user` returns `is_admin`; `require_admin` dependency |
| `app/admin.py` | NEW — admin APIRouter |
| `app/main.py` | Include admin router; add time-window check + queueing in `create_job`; add logging calls |
| `app/executor.py` | Add `log_event` calls |
| `frontend/index.html` | Admin panel HTML section |
| `frontend/src/main.ts` | Admin panel logic + i18n strings |
| `tests/test_admin.py` | NEW — admin functionality tests |

## Out of Scope

- Per-user time windows (global only for now).
- Retroactive window re-adjustment when params change.
- Real HAMI/K8s GPU integration (mock only).
- Real-time WebSocket monitoring (polling is sufficient).
