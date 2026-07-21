import asyncio
import os
import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Depends, Cookie
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from . import db, auth, admin, timecheck, gpu, images
from .storage import Storage
from .executor import MockExecutor

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

EXECUTOR_MODE = os.environ.get("DDP_EXECUTOR", "k8s")

storage = Storage()
if EXECUTOR_MODE == "k8s":
    from .k8s_executor import K8sExecutor

    executor = K8sExecutor(storage)
else:
    executor = MockExecutor(storage)
scheduler = AsyncIOScheduler(timezone=db.get_tz())


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Reschedule pending jobs after restart
    for job in db.list_jobs():
        if job["status"] in ("pending", "initializing"):
            dt = _not_in_past(datetime.fromisoformat(job["scheduled_at"]))
            trigger = DateTrigger(run_date=dt)
            scheduler.add_job(
                executor.execute,
                trigger,
                args=[job["id"]],
                id=job["id"],
                replace_existing=True,
            )
            if job["status"] == "initializing" and hasattr(executor, "wait_ready"):
                asyncio.create_task(executor.wait_ready(job["id"]))
        elif job["status"] == "running" and hasattr(executor, "watch"):
            # k8s jobs keep running across backend restarts; resume watching
            asyncio.create_task(executor.watch(job["id"]))
    if not scheduler.running:
        scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


app = FastAPI(title="DDP", lifespan=lifespan)
app.include_router(admin.router)
_assets = DIST_DIR / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")


# ── Auth ─────────────────────────────────────────────


@app.post("/api/auth/register")
async def register(username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    if len(username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.get_user_by_username(username):
        raise HTTPException(409, "Username already taken")
    pw_hash, salt = auth.hash_password(password)
    user_id = db.create_user(username, pw_hash, salt)
    db.log_event("INFO", "auth", f"User {username} registered", user_id=user_id)
    token = auth.create_session_for_user(user_id)
    return _json_response({"ok": True, "username": username}, token)


def _json_response(data: dict, token: str):
    resp = JSONResponse(data)
    resp.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="lax",
        max_age=int(auth.SESSION_DURATION.total_seconds()),
    )
    return resp


@app.post("/api/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_username(username)
    if not user or not auth.verify_password(
        password, user["password_hash"], user["salt"]
    ):
        db.log_event("WARNING", "auth", f"Failed login attempt: {username}")
        raise HTTPException(401, "Invalid username or password")
    db.log_event("INFO", "auth", f"User logged in: {username}", user_id=user["id"])
    token = auth.create_session_for_user(user["id"])
    return _json_response({"ok": True}, token)


@app.post("/api/auth/logout")
async def logout(session: str | None = Cookie(None)):
    if session:
        user = db.get_user_by_session(session)
        if user:
            db.log_event(
                "INFO",
                "auth",
                f"User logged out: {user['username']}",
                user_id=user["id"],
            )
        db.delete_session(session)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.post("/api/auth/password")
async def change_password(
    user: dict = Depends(auth.get_current_user),
    old_password: str = Form(...),
    new_password: str = Form(...),
):
    full = db.get_user_by_id(user["id"])
    if not full or not auth.verify_password(
        old_password, full["password_hash"], full["salt"]
    ):
        raise HTTPException(403, "Current password is incorrect")
    if len(new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    pw_hash, salt = auth.hash_password(new_password)
    db.update_user_password(user["id"], pw_hash, salt)
    db.log_event(
        "INFO", "auth", f"Password changed: {user['username']}", user_id=user["id"]
    )
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.get_current_user)):
    full = db.get_user_by_id(user["id"]) or {}
    quota = full.get("gpu_quota_override")
    if quota is None:
        quota = db.get_param("gpu_default_quota") or 0
    try:
        image_list = images.list_images()
    except Exception:
        image_list = []
    params = db.get_all_params()
    cpu_quota = full.get("cpu_quota_override")
    if cpu_quota is None:
        cpu_quota = params.get("cpu_default_quota", 8)
    mem_quota = full.get("memory_quota_override_gb")
    if mem_quota is None:
        mem_quota = params.get("memory_default_quota_gb", 32)
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": user.get("is_admin", 0),
        "mode": EXECUTOR_MODE,
        "gpu_quota": quota,
        "images": image_list,
        "time_window_start": params.get("time_window_start", "00:00"),
        "time_window_end": params.get("time_window_end", "23:59"),
        "gpu_default_quota": params.get("gpu_default_quota", 0),
        "cpu_quota": cpu_quota,
        "mem_quota": mem_quota,
    }


@app.get("/api/gpus")
async def list_gpus(user: dict = Depends(auth.get_current_user)):
    try:
        gpus = await asyncio.to_thread(gpu.fetch_gpu_status)
    except Exception as e:
        return {"gpus": [], "error": str(e)}
    disabled = {
        d.get("uuid")
        for d in db.get_all_params().get("gpu_devices", [])
        if isinstance(d, dict) and not d.get("enabled", True)
    }
    for g in gpus:
        g["enabled"] = g["uuid"] not in disabled
    return {"gpus": gpus}


def _not_in_past(dt: datetime) -> datetime:
    """APScheduler silently drops triggers set in the past — clamp to now+1min."""
    now = datetime.now(db.get_tz())
    return dt if dt > now else now + timedelta(minutes=1)


def _validate_output_path(p: str) -> str:
    p = (p or "").strip() or "output"
    full = os.path.normpath(p if os.path.isabs(p) else os.path.join("/workspace", p))
    if not full.startswith("/workspace/"):
        raise HTTPException(
            400, "output_path must resolve to a directory inside /workspace"
        )
    return p


def _validate_repeat(repeat_type: str | None, repeat_weekdays: list[str] | None):
    """返回 (repeat_type, repeat_weekdays_str_or_None)；非法时 raise HTTPException(400)."""
    repeat_type = (repeat_type or "none").strip()
    if repeat_type not in ("none", "daily", "weekly"):
        raise HTTPException(400, f"Invalid repeat_type: {repeat_type}")
    days: list[int] = []
    if repeat_type == "weekly":
        if not repeat_weekdays:
            raise HTTPException(400, "weekly requires at least one weekday (1-7)")
        for d in repeat_weekdays:
            try:
                n = int(d)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Invalid weekday: {d}")
            if not 1 <= n <= 7:
                raise HTTPException(400, f"Weekday must be 1-7, got {n}")
            days.append(n)
    return repeat_type, (",".join(map(str, sorted(set(days))))) if days else None


# ── Jobs (protected) ─────────────────────────────────


@app.post("/api/jobs")
async def create_job(
    user: dict = Depends(auth.get_current_user),
    name: str = Form(...),
    image: str = Form(...),
    entry_command: str = Form("python main.py"),
    scheduled_at: str = Form(...),
    timeout_minutes: int = Form(60),
    gpus: int = Form(0),
    gpu_mem_mb: int | None = Form(None),
    cpu: float = Form(2),
    memory_gb: float = Form(4),
    output_path: str = Form("output"),
    repeat_type: str = Form("none"),
    repeat_weekdays: list[str] = Form([]),
):
    output_path = _validate_output_path(output_path)
    if cpu <= 0 or memory_gb <= 0:
        raise HTTPException(400, "cpu and memory_gb must be positive")
    try:
        known = images.list_images()
    except Exception:
        known = []
    if known and image not in known:
        raise HTTPException(400, f"Unknown image: {image}")
    if gpus < 0:
        raise HTTPException(400, "gpus must be >= 0")
    full_user = db.get_user_by_id(user["id"]) or {}
    quota = full_user.get("gpu_quota_override")
    if quota is None:
        quota = db.get_param("gpu_default_quota") or 0
    if gpus > quota:
        raise HTTPException(
            403, f"GPU quota exceeded: requested {gpus}, allowed {quota}"
        )
    cpu_cap = full_user.get("cpu_quota_override")
    if cpu_cap is None:
        cpu_cap = db.get_param("cpu_default_quota") or 8
    if cpu > cpu_cap:
        raise HTTPException(
            403, f"CPU quota exceeded: requested {cpu}, allowed {cpu_cap}"
        )
    mem_cap = full_user.get("memory_quota_override_gb")
    if mem_cap is None:
        mem_cap = db.get_param("memory_default_quota_gb") or 32
    if memory_gb > mem_cap:
        raise HTTPException(
            403, f"Memory quota exceeded: requested {memory_gb}GB, allowed {mem_cap}GB"
        )
    storage_quota = full_user.get("storage_quota_override_gb")
    if storage_quota is None:
        storage_quota = db.get_param("storage_default_quota_gb") or 10

    job_id = str(uuid.uuid4())

    # Parse local datetime, check time window, convert to UTC (admins bypass)
    local_dt = datetime.fromisoformat(scheduled_at)
    if user.get("is_admin"):
        adjusted_dt = local_dt
    else:
        adjusted_dt = timecheck.check_scheduled_time(local_dt)
    was_queued = adjusted_dt != local_dt

    dt_local = _not_in_past(adjusted_dt.replace(tzinfo=db.get_tz()))
    scheduled_utc = dt_local.isoformat()

    ssh_info = {}
    if hasattr(executor, "prepare"):
        try:
            ssh_info = await executor.prepare(
                {
                    "id": job_id,
                    "user_id": user["id"],
                    "image": image,
                    "storage_gb": storage_quota,
                }
            )
        except Exception as e:
            db.log_event(
                "ERROR", "job", f"Debug env prepare failed: {e}", user_id=user["id"]
            )
            raise HTTPException(502, f"Failed to create debug environment: {e}")

    initializing = bool(ssh_info)
    rt, rw = _validate_repeat(repeat_type, repeat_weekdays)
    db.create_job(
        job_id,
        user["id"],
        name,
        image,
        entry_command,
        scheduled_utc,
        timeout_minutes,
        gpus=gpus,
        gpu_mem_mb=gpu_mem_mb if gpus else None,
        ssh_port=ssh_info.get("ssh_port"),
        ssh_password=ssh_info.get("ssh_password"),
        status="initializing" if initializing else "pending",
        output_path=output_path,
        cpu=cpu,
        memory_gb=memory_gb,
        repeat_type=rt,
        repeat_weekdays=rw,
    )
    if initializing:
        asyncio.create_task(executor.wait_ready(job_id))

    db.log_event("INFO", "job", f"Job submitted: {name} ({job_id})", user_id=user["id"])
    if was_queued:
        db.log_event(
            "INFO",
            "job",
            f"Job queued until window: {name} -> {adjusted_dt.isoformat()}",
            user_id=user["id"],
        )

    scheduler.add_job(
        executor.execute,
        DateTrigger(run_date=dt_local),
        args=[job_id],
        id=job_id,
        replace_existing=True,
    )

    return {
        "id": job_id,
        "status": "initializing" if initializing else "pending",
        "queued": was_queued,
        **ssh_info,
    }


@app.get("/api/jobs")
async def list_jobs(user: dict = Depends(auth.get_current_user)):
    return db.list_jobs(user["id"])


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    return job


@app.get("/api/jobs/{job_id}/logs")
async def get_logs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    if job["status"] == "running" and hasattr(executor, "fetch_pod_log"):
        live = await executor.fetch_pod_log(job_id)
        if live is not None:
            return {"logs": live, "live": True}
    try:
        data = storage.get_object_bytes(f"jobs/{job_id}/logs/run.log")
        return {"logs": data.decode("utf-8", errors="replace")}
    except Exception:
        return {"logs": ""}


@app.get("/api/jobs/{job_id}/logs/download")
async def download_logs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    try:
        data = storage.get_object_bytes(f"jobs/{job_id}/logs/run.log")
    except Exception:
        raise HTTPException(404, "Logs not available")
    filename = f"{job['name']}_run.log"
    return StreamingResponse(
        iter([data]),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


@app.get("/api/jobs/{job_id}/outputs")
async def get_outputs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    objects = storage.list_objects(storage.bucket, f"jobs/{job_id}/output/")
    for o in objects:
        rel = o["key"].split("/", 2)[-1]  # strip jobs/{job_id}/
        o["download_url"] = f"/api/jobs/{job_id}/download/{rel}"
    return {"outputs": objects}


@app.get("/api/jobs/{job_id}/download/{file_path:path}")
async def download_file(
    job_id: str, file_path: str, user: dict = Depends(auth.get_current_user)
):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    key = f"jobs/{job_id}/{file_path}"
    if not storage.object_exists(key):
        raise HTTPException(404, "File not found")
    fname = Path(file_path).name
    return StreamingResponse(
        storage.stream_object(key),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@app.patch("/api/jobs/{job_id}")
async def update_pending_job(
    job_id: str,
    user: dict = Depends(auth.get_current_user),
    name: str = Form(None),
    entry_command: str = Form(None),
    scheduled_at: str = Form(None),
    timeout_minutes: int = Form(None),
    gpus: int = Form(None),
    gpu_mem_mb: int | None = Form(None),
    cpu: float = Form(None),
    memory_gb: float = Form(None),
    output_path: str = Form(None),
    repeat_type: str = Form(None),
    repeat_weekdays: list[str] = Form(None),
):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    if job["status"] != "pending":
        raise HTTPException(409, "Only pending jobs can be edited")
    return _apply_job_edits(
        job,
        user,
        name,
        entry_command,
        scheduled_at,
        timeout_minutes,
        gpus,
        gpu_mem_mb,
        output_path,
        cpu=cpu,
        memory_gb=memory_gb,
        repeat_type=repeat_type,
        repeat_weekdays=repeat_weekdays,
    )


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or (job.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(404)
    if job["status"] in ("pending", "initializing"):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        if hasattr(executor, "cleanup"):
            await executor.cleanup(job_id)
        db.update_job(job_id, status="cancelled")
        db.log_event("INFO", "job", f"Job cancelled: {job_id}", user_id=user["id"])
    elif job["status"] in ("running",):
        if hasattr(executor, "cancel"):
            await executor.cancel(job_id)
            db.update_job(job_id, status="cancelled", finished_at=db.now_iso())
            db.log_event(
                "INFO", "job", f"Running job cancelled: {job_id}", user_id=user["id"]
            )
        else:
            raise HTTPException(409, "Cannot cancel a running job in mock mode")
    else:
        # force delete: kill live resources first for running/initializing
        if job["status"] in ("running", "initializing"):
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass
            if hasattr(executor, "cancel"):
                await executor.cancel(job_id)
        storage.delete_prefix(f"jobs/{job_id}/")
        if hasattr(executor, "cleanup"):
            await executor.cleanup(job_id)
        db.delete_job(job_id)
        db.log_event("INFO", "admin",
                     f"Job force-deleted by admin ({job['status']}): {job_id}", user_id=user["id"])
    return {"ok": True}


# ── Admin: job management (all users) ────────────────


def _with_usernames(jobs):
    names = {u["id"]: u["username"] for u in db.list_users()}
    for j in jobs:
        j["username"] = names.get(j.get("user_id"), "?")
    return jobs


@app.get("/api/admin/jobs")
async def admin_list_jobs(user: dict = Depends(auth.require_admin)):
    return _with_usernames(db.list_jobs())


def _apply_job_edits(
    job,
    user,
    name,
    entry_command,
    scheduled_at,
    timeout_minutes,
    gpus,
    gpu_mem_mb,
    output_path,
    cpu=None,
    memory_gb=None,
    repeat_type=None,
    repeat_weekdays=None,
):
    updates = {}
    if (cpu is not None and cpu <= 0) or (memory_gb is not None and memory_gb <= 0):
        raise HTTPException(400, "cpu and memory_gb must be positive")
    if cpu is not None or memory_gb is not None:
        owner = db.get_user_by_id(job["user_id"]) or {}
        if cpu is not None:
            cap = owner.get("cpu_quota_override")
            if cap is None:
                cap = db.get_param("cpu_default_quota") or 8
            if cpu > cap:
                raise HTTPException(
                    403, f"CPU quota exceeded: requested {cpu}, allowed {cap}"
                )
            updates["cpu"] = cpu
        if memory_gb is not None:
            cap = owner.get("memory_quota_override_gb")
            if cap is None:
                cap = db.get_param("memory_default_quota_gb") or 32
            if memory_gb > cap:
                raise HTTPException(
                    403,
                    f"Memory quota exceeded: requested {memory_gb}GB, allowed {cap}GB",
                )
            updates["memory_gb"] = memory_gb
    if name is not None and name.strip():
        updates["name"] = name.strip()
    if entry_command is not None and entry_command.strip():
        updates["entry_command"] = entry_command.strip()
    if timeout_minutes is not None:
        if not 1 <= timeout_minutes <= 1440:
            raise HTTPException(400, "timeout_minutes must be 1-1440")
        updates["timeout_minutes"] = timeout_minutes
    if gpus is not None:
        if gpus < 0:
            raise HTTPException(400, "gpus must be >= 0")
        owner = db.get_user_by_id(job["user_id"]) or {}
        quota = owner.get("gpu_quota_override")
        if quota is None:
            quota = db.get_param("gpu_default_quota") or 0
        if gpus > quota:
            raise HTTPException(
                403, f"GPU quota exceeded: requested {gpus}, allowed {quota}"
            )
        updates["gpus"] = gpus
        updates["gpu_mem_mb"] = gpu_mem_mb if gpus else None
    elif gpu_mem_mb is not None and job.get("gpus"):
        updates["gpu_mem_mb"] = gpu_mem_mb
    if scheduled_at is not None:
        local_dt = datetime.fromisoformat(scheduled_at)
        if not user.get("is_admin"):
            local_dt = timecheck.check_scheduled_time(local_dt)
        dt_local = _not_in_past(local_dt.replace(tzinfo=db.get_tz()))
        updates["scheduled_at"] = dt_local.isoformat()
        scheduler.add_job(
            executor.execute,
            DateTrigger(run_date=dt_local),
            args=[job["id"]],
            id=job["id"],
            replace_existing=True,
        )
    if output_path is not None:
        updates["output_path"] = _validate_output_path(output_path)
    if repeat_type is not None or repeat_weekdays is not None:
        rt = repeat_type or job.get("repeat_type") or "none"
        # weekdays 没传就保留原值（仅当仍是 weekly）
        if repeat_weekdays is None:
            existing_days = (job.get("repeat_weekdays") or "").split(",")
            existing_days = [d for d in existing_days if d.strip()]
            rw_days = existing_days if rt == "weekly" else None
        else:
            rw_days = repeat_weekdays
        rt, rw = _validate_repeat(rt, rw_days if rw_days else None)
        updates["repeat_type"] = rt
        updates["repeat_weekdays"] = rw
    if not updates:
        raise HTTPException(400, "Nothing to update")
    db.update_job(job["id"], **updates)
    db.log_event(
        "INFO", "job", f"Job edited: {job['id']} {sorted(updates)}", user_id=user["id"]
    )
    return db.get_job(job["id"])


@app.patch("/api/admin/jobs/{job_id}")
async def admin_update_job(
    job_id: str,
    user: dict = Depends(auth.require_admin),
    name: str = Form(None),
    entry_command: str = Form(None),
    scheduled_at: str = Form(None),
    timeout_minutes: int = Form(None),
    gpus: int = Form(None),
    gpu_mem_mb: int | None = Form(None),
    cpu: float = Form(None),
    memory_gb: float = Form(None),
    output_path: str = Form(None),
    repeat_type: str = Form(None),
    repeat_weekdays: list[str] = Form(None),
):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["status"] != "pending":
        raise HTTPException(409, "Only pending jobs can be edited")
    return _apply_job_edits(
        job,
        user,
        name,
        entry_command,
        scheduled_at,
        timeout_minutes,
        gpus,
        gpu_mem_mb,
        output_path,
        cpu=cpu,
        memory_gb=memory_gb,
        repeat_type=repeat_type,
        repeat_weekdays=repeat_weekdays,
    )


@app.delete("/api/admin/jobs/{job_id}")
async def admin_delete_job(job_id: str, user: dict = Depends(auth.require_admin)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["status"] in ("pending", "initializing", "running"):
        # cancel: kill live resources, keep the record as cancelled
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        if hasattr(executor, "cancel"):
            await executor.cancel(job_id)
        if hasattr(executor, "cleanup"):
            await executor.cleanup(job_id)
        db.update_job(job_id, status="cancelled", finished_at=db.now_iso())
        db.log_event(
            "INFO", "admin",
            f"Job cancelled by admin ({job['status']}): {job_id}", user_id=user["id"],
        )
    else:
        storage.delete_prefix(f"jobs/{job_id}/")
        if hasattr(executor, "cleanup"):
            await executor.cleanup(job_id)
        db.delete_job(job_id)
        db.log_event(
            "INFO", "admin", f"Job deleted by admin: {job_id}", user_id=user["id"]
        )
    return {"ok": True}


@app.post("/api/admin/jobs/reorder")
async def admin_reorder_jobs(body: dict, user: dict = Depends(auth.require_admin)):
    """Rewrite scheduled_at of pending jobs so they fire in the given order,
    one minute apart starting from the earliest current schedule."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "ids must be a non-empty list")
    jobs = []
    for jid in ids:
        job = db.get_job(jid)
        if not job:
            raise HTTPException(404, f"Unknown job: {jid}")
        if job["status"] != "pending":
            raise HTTPException(409, f"Job {jid} is not pending")
        jobs.append(job)
    base = _not_in_past(min(datetime.fromisoformat(j["scheduled_at"]) for j in jobs))
    for i, job in enumerate(jobs):
        dt = base + timedelta(minutes=i)
        db.update_job(job["id"], scheduled_at=dt.isoformat())
        scheduler.add_job(
            executor.execute,
            DateTrigger(run_date=dt),
            args=[job["id"]],
            id=job["id"],
            replace_existing=True,
        )
    db.log_event(
        "INFO", "admin", f"Pending jobs reordered ({len(jobs)})", user_id=user["id"]
    )
    return {"ok": True}


# ── Frontend ─────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    index_html = DIST_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(
            index_html.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache"},
        )
    return "<h1>Frontend not built</h1><p>Run <code>cd frontend && bun install && bun run build</code></p>"
