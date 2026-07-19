import asyncio
import os
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Cookie
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from . import db, auth, admin, timecheck, gpu, images
from .storage import Storage
from .executor import MockExecutor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

EXECUTOR_MODE = os.environ.get("DDP_EXECUTOR", "mock")

storage = Storage()
if EXECUTOR_MODE == "k8s":
    from .k8s_executor import K8sExecutor
    executor = K8sExecutor(storage)
else:
    executor = MockExecutor(storage)
scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Reschedule pending jobs after restart
    for job in db.list_jobs():
        if job["status"] == "pending":
            dt = datetime.fromisoformat(job["scheduled_at"])
            trigger = DateTrigger(run_date=dt)
            scheduler.add_job(
                executor.execute, trigger, args=[job["id"]], id=job["id"],
                replace_existing=True,
            )
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
    resp = _set_cookie(token)
    resp = _json_response({"ok": True, "username": username}, token)
    return resp


def _json_response(data: dict, token: str):
    resp = JSONResponse(data)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=int(auth.SESSION_DURATION.total_seconds()))
    return resp


def _set_cookie(token: str):
    return _json_response({"ok": True}, token)


@app.post("/api/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_username(username)
    if not user or not auth.verify_password(password, user["password_hash"], user["salt"]):
        db.log_event("WARNING", "auth", f"Failed login attempt: {username}")
        raise HTTPException(401, "Invalid username or password")
    db.log_event("INFO", "auth", f"User logged in: {username}", user_id=user["id"])
    token = auth.create_session_for_user(user["id"])
    return _set_cookie(token)


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
    return {"id": user["id"], "username": user["username"], "is_admin": user.get("is_admin", 0),
            "mode": EXECUTOR_MODE, "gpu_quota": quota, "images": image_list}


@app.get("/api/gpus")
async def list_gpus(user: dict = Depends(auth.get_current_user)):
    try:
        gpus = await asyncio.to_thread(gpu.fetch_gpu_status)
    except Exception as e:
        return {"gpus": [], "error": str(e)}
    disabled = {d.get("uuid") for d in db.get_all_params().get("gpu_devices", [])
                if isinstance(d, dict) and not d.get("enabled", True)}
    for g in gpus:
        g["enabled"] = g["uuid"] not in disabled
    return {"gpus": gpus}


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
):
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
        raise HTTPException(403, f"GPU quota exceeded: requested {gpus}, allowed {quota}")
    storage_quota = full_user.get("storage_quota_override_gb")
    if storage_quota is None:
        storage_quota = db.get_param("storage_default_quota_gb") or 10

    job_id = str(uuid.uuid4())

    # Parse local datetime, check time window, convert to UTC
    local_dt = datetime.fromisoformat(scheduled_at)
    adjusted_dt = timecheck.check_scheduled_time(local_dt)
    was_queued = adjusted_dt != local_dt

    dt_utc = adjusted_dt.astimezone(timezone.utc)
    scheduled_utc = dt_utc.isoformat()

    ssh_info = {}
    if hasattr(executor, "prepare"):
        try:
            ssh_info = await executor.prepare(
                {"id": job_id, "user_id": user["id"], "image": image,
                 "storage_gb": storage_quota})
        except Exception as e:
            db.log_event("ERROR", "job", f"Debug env prepare failed: {e}", user_id=user["id"])
            raise HTTPException(502, f"Failed to create debug environment: {e}")

    db.create_job(job_id, user["id"], name, image, entry_command, scheduled_utc,
                  timeout_minutes, gpus=gpus, gpu_mem_mb=gpu_mem_mb if gpus else None,
                  ssh_port=ssh_info.get("ssh_port"), ssh_password=ssh_info.get("ssh_password"))

    db.log_event("INFO", "job", f"Job submitted: {name} ({job_id})", user_id=user["id"])
    if was_queued:
        db.log_event("INFO", "job", f"Job queued until window: {name} -> {adjusted_dt.isoformat()}",
                     user_id=user["id"])

    scheduler.add_job(
        executor.execute, DateTrigger(run_date=dt_utc),
        args=[job_id], id=job_id, replace_existing=True,
    )

    return {"id": job_id, "status": "pending", "queued": was_queued, **ssh_info}


@app.get("/api/jobs")
async def list_jobs(user: dict = Depends(auth.get_current_user)):
    return db.list_jobs(user["id"])


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    return job


@app.get("/api/jobs/{job_id}/logs")
async def get_logs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    try:
        data = storage.get_object_bytes(f"jobs/{job_id}/logs/run.log")
        return {"logs": data.decode("utf-8", errors="replace")}
    except Exception:
        return {"logs": ""}


@app.get("/api/jobs/{job_id}/logs/download")
async def download_logs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    try:
        data = storage.get_object_bytes(f"jobs/{job_id}/logs/run.log")
    except Exception:
        raise HTTPException(404, "Logs not available")
    filename = f"{job['name']}_run.log"
    return StreamingResponse(
        iter([data]),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/jobs/{job_id}/outputs")
async def get_outputs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    objects = storage.list_objects(storage.bucket, f"jobs/{job_id}/output/")
    for o in objects:
        rel = o["key"].split("/", 2)[-1]  # strip jobs/{job_id}/
        o["download_url"] = f"/api/jobs/{job_id}/download/{rel}"
    return {"outputs": objects}


@app.get("/api/jobs/{job_id}/download/{file_path:path}")
async def download_file(job_id: str, file_path: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
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


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    if job["status"] == "pending":
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
            db.update_job(job_id, status="cancelled",
                          finished_at=datetime.now(timezone.utc).isoformat())
            db.log_event("INFO", "job", f"Running job cancelled: {job_id}", user_id=user["id"])
        else:
            raise HTTPException(409, "Cannot cancel a running job in mock mode")
    else:
        storage.delete_prefix(f"jobs/{job_id}/")
        if hasattr(executor, "cleanup"):
            await executor.cleanup(job_id)
        db.delete_job(job_id)
        db.log_event("INFO", "job", f"Job deleted: {job_id}", user_id=user["id"])
    return {"ok": True}


# ── Frontend ─────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_html = DIST_DIR / "index.html"
    if index_html.exists():
        return index_html.read_text(encoding="utf-8")
    return "<h1>Frontend not built</h1><p>Run <code>cd frontend && bun install && bun run build</code></p>"
