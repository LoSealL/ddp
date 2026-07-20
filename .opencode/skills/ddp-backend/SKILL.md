---
name: ddp-backend
description: Use when integrating with or calling the DDP (Delayed Dispatch Platform) backend API — submitting GPU jobs with ssh debug pods, editing pending jobs, checking status, fetching logs/outputs, admin cross-user management, or implementing auth flows. Covers all REST endpoints, session cookie auth, and the mock/k8s executor modes.
---

# DDP Backend Integration

DDP is a FastAPI service that schedules GPU jobs on k8s. On submit the user immediately gets a cpu-only **ssh debug pod** (same image + persistent per-user workspace); at the scheduled time a GPU Job runs the entry command in that same workspace; outputs are harvested to S3.

**Base URL:** `http://172.16.50.3:8888` (k8s) or `http://localhost:8000` (local dev)

## Auth Model

Server-side sessions (SQLite), HttpOnly cookie `session`, 7-day expiry. All job endpoints require it.

| Method | Path | Body (form-data) | Response |
|--------|------|-------------------|----------|
| POST | `/api/auth/register` | `username` (≥2), `password` (≥6) | `{"ok":true,"username"}` + Set-Cookie. First user = admin |
| POST | `/api/auth/login` | `username`, `password` | `{"ok":true}` + Set-Cookie |
| POST | `/api/auth/logout` | — | clears session |
| POST | `/api/auth/password` | `old_password`, `new_password` (≥6) | `{"ok":true}`; 403 wrong old password |
| GET | `/api/auth/me` | — | see below |

`GET /api/auth/me` response:

```json
{
  "id": 1, "username": "alice", "is_admin": 1,
  "mode": "k8s", "gpu_quota": 8,
  "images": ["ddp-cuda-ssh:latest", "ddp-pytorch-ssh:latest"],
  "time_window_start": "22:00", "time_window_end": "06:00",
  "gpu_default_quota": 1
}
```

## Submit Job

```
POST /api/jobs
Content-Type: multipart/form-data
```

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `name` | Yes | — | |
| `image` | Yes | — | Must be one of `/api/auth/me` → `images` (Harbor tags) |
| `entry_command` | No | `python main.py` | Runs in `/workspace` of the GPU pod |
| `scheduled_at` | Yes | — | `YYYY-MM-DDTHH:MM` local time; non-admins are queued to the next allowed window |
| `timeout_minutes` | No | `60` | 1–1440, hard kill via `activeDeadlineSeconds` |
| `gpus` | No | `0` | vGPU count, 403 above user quota |
| `gpu_mem_mb` | No | — | vGPU memory (HAMi `gpumem`) |
| `output_path` | No | `output` | Absolute or `/workspace`-relative; must resolve inside `/workspace`. **Harvested**: uploaded to S3 then deleted from workspace |

**Response** (k8s mode):

```json
{"id": "<uuid>", "status": "initializing", "queued": false,
 "ssh_port": 30439, "ssh_password": "..."}
```

SSH: `ssh root@<any-node-ip> -p <ssh_port>` with the given password. The debug pod has **no GPU** (`nvidia-smi` → "No devices were found") and proxy env pre-set. `/workspace` (also at `~/workspace`) persists across all of the user's jobs.

**Errors:** 400 unknown image / bad output_path; 403 GPU quota exceeded; 502 debug env creation failed.

## Job Lifecycle

```
initializing → pending → running → done / failed / timeout
                   ↘ cancelled
```

- `initializing`: debug pod starting (image pull, sshd). Flips to `pending` when ssh is connectable (~10 min cap → `failed`)
- `pending`: waiting for `scheduled_at`. **Editable** and cancellable
- `running`: GPU Job active. Cancellable (k8s mode)
- `timeout`: killed by `activeDeadlineSeconds`

## Job Endpoints (user-scoped)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/jobs` | Own jobs, newest first |
| GET | `/api/jobs/{id}` | Detail (404 if not owner) |
| PATCH | `/api/jobs/{id}` | **pending only** (else 409). Form fields, all optional: `name`, `entry_command`, `scheduled_at`, `timeout_minutes`, `gpus`, `gpu_mem_mb`, `output_path`. Reschedules the trigger |
| DELETE | `/api/jobs/{id}` | pending/initializing → cancel; running → kill k8s Job (mock: 409); terminal → delete record + S3 prefix |
| GET | `/api/jobs/{id}/logs` | `{"logs": "..."}` (may contain ANSI codes) |
| GET | `/api/jobs/{id}/logs/download` | text file download |
| GET | `/api/jobs/{id}/outputs` | `{"outputs":[{key,size,s3_uri,download_url}]}` |
| GET | `/api/jobs/{id}/download/{path}` | artifact download |
| GET | `/api/gpus` | `{"gpus":[{uuid,node,index,type,mem_total,mem_used,cores_total,cores_used,shared,enabled}]}` — HAMi allocation (bytes) |

## Admin Endpoints (require_admin)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/admin/jobs` | All users' jobs, each with `username` |
| PATCH | `/api/admin/jobs/{id}` | pending only; quota checked against the job's **owner** |
| DELETE | `/api/admin/jobs/{id}` | pending only → cancel (else 409) |
| POST | `/api/admin/jobs/reorder` | `{"ids":[...]}` — rewrites pending `scheduled_at` in 1-min steps from earliest |
| GET | `/api/admin/users` | List users |
| PATCH | `/api/admin/users/{id}` | `{is_admin, gpu_quota_override, storage_quota_override_gb}`; can't demote/delete self or last admin |
| DELETE | `/api/admin/users/{id}` | Also drops the user's workspace PVC |
| GET/PUT | `/api/admin/params` | `time_window_*`, `gpu_default_quota`, `storage_default_quota_gb`, `gpu_devices` ([{uuid,enabled}], display-only disable) |
| GET | `/api/admin/logs` | `?level&category&limit&offset` |
| GET | `/api/admin/monitoring` | Job counts + real GPU + S3 usage |

Admins bypass the time-window check on submit/edit.

## Quick Reference: curl

```bash
curl -c cj -X POST $B/api/auth/login -F username=alice -F password=secret
curl -b cj -X POST $B/api/jobs \
  -F "name=train v2" -F "image=ddp-pytorch-ssh:latest" \
  -F "entry_command=python train.py" -F "scheduled_at=2026-07-20T22:00" \
  -F "timeout_minutes=480" -F "gpus=1" -F "output_path=results"
# -> note ssh_port/ssh_password, debug in the pod
curl -b cj -X PATCH $B/api/jobs/<id> -F "gpus=2"            # pending only
curl -b cj $B/api/jobs/<id>/logs
curl -b cj -X DELETE $B/api/jobs/<id>
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| No ssh info in response | Server is in mock mode (`DDP_EXECUTOR=mock`) — debug pods only exist in k8s mode |
| `python: command not found` in cuda image | Base cuda image has no python; use `bash ...` or install python in the debug pod first |
| output_path 400 | Must resolve to a dir inside `/workspace` (no `..`, not the root itself) |
| 409 on PATCH | Only `pending` jobs are editable |
| Files in output dir "lost" | By design: the output dir is harvested (moved to S3) after each run |
| GPU job Pending forever | All GPUs allocated; it waits until `timeout_minutes` deadline → status `timeout` |

## Mock Mode

`DDP_EXECUTOR=mock` (tests use it via conftest): no cluster calls, no debug pod/ssh, job instantly transitions running→done with a stub log. API surface is identical.
