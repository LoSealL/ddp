import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Cookie
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from . import db, auth
from .storage import Storage
from .executor import MockExecutor, UPLOAD_DIR, LOG_DIR

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

storage = Storage(DATA_DIR / "mock-s3")
executor = MockExecutor(storage)
scheduler = AsyncIOScheduler(timezone="UTC")


def _parse_local_to_utc(dt_str: str) -> str:
    """HTML datetime-local gives naive local time -> convert to UTC ISO."""
    dt = datetime.fromisoformat(dt_str)
    dt_utc = dt.astimezone().astimezone(timezone.utc)
    return dt_utc.isoformat()


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
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="DDP", lifespan=lifespan)
app.mount("/s3", StaticFiles(directory=str(DATA_DIR / "mock-s3")), name="s3")
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
    token = auth.create_session_for_user(user_id)
    resp = _set_cookie(token)
    resp = _json_response({"ok": True, "username": username}, token)
    return resp


def _json_response(data: dict, token: str):
    from starlette.responses import JSONResponse
    resp = JSONResponse(data)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=int(auth.SESSION_DURATION.total_seconds()))
    return resp


def _set_cookie(token: str):
    return _json_response({"ok": True}, token)


@app.post("/api/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_username(username)
    if not user or not auth.verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(401, "Invalid username or password")
    token = auth.create_session_for_user(user["id"])
    return _set_cookie(token)


@app.post("/api/auth/logout")
async def logout(session: str | None = Cookie(None)):
    if session:
        db.delete_session(session)
    from starlette.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.get_current_user)):
    return {"id": user["id"], "username": user["username"]}


# ── Jobs (protected) ─────────────────────────────────

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

    scheduled_utc = _parse_local_to_utc(scheduled_at)
    db.create_job(job_id, user["id"], name, file.filename, entry_command, scheduled_utc, timeout_minutes)

    dt = datetime.fromisoformat(scheduled_utc)
    scheduler.add_job(
        executor.execute, DateTrigger(run_date=dt),
        args=[job_id], id=job_id, replace_existing=True,
    )

    return {"id": job_id, "status": "pending"}


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
    log_path = LOG_DIR / f"{job_id}.log"
    if log_path.exists():
        return {"logs": log_path.read_text(encoding="utf-8", errors="replace")}
    return {"logs": ""}


@app.get("/api/jobs/{job_id}/outputs")
async def get_outputs(job_id: str, user: dict = Depends(auth.get_current_user)):
    job = db.get_job(job_id)
    if not job or job.get("user_id") != user["id"]:
        raise HTTPException(404)
    objects = storage.list_objects("ddp", f"jobs/{job_id}/")
    return {"outputs": objects}


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
        db.update_job(job_id, status="cancelled")
    elif job["status"] in ("running",):
        raise HTTPException(409, "Cannot cancel a running job in mock mode")
    else:
        db.delete_job(job_id)
    return {"ok": True}


# ── Frontend ─────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_html = DIST_DIR / "index.html"
    if index_html.exists():
        return index_html.read_text(encoding="utf-8")
    return "<h1>Frontend not built</h1><p>Run <code>cd frontend && bun install && bun run build</code></p>"
