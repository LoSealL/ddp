import asyncio
import os
import json

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, field_validator
from typing import Optional

from . import db, auth, gpu
from .storage import Storage

router = APIRouter(prefix="/api/admin", tags=["admin"])
storage = Storage()

ALLOWED_PARAMS = {
    "time_window_start", "time_window_end", "time_window_repeat",
    "gpu_default_quota", "storage_default_quota_gb", "gpu_devices",
}


class UserUpdate(BaseModel):
    is_admin: Optional[int] = None
    gpu_quota_override: Optional[int] = None
    storage_quota_override_gb: Optional[float] = None

    @field_validator("is_admin")
    @classmethod
    def is_admin_must_be_bool_int(cls, v):
        if v is not None and v not in (0, 1):
            raise ValueError("is_admin must be 0 or 1")
        return v


# ── User Management ─────────────────────────────

@router.get("/users")
async def list_users(user: dict = Depends(auth.require_admin)):
    return db.list_users()


@router.patch("/users/{user_id}")
async def update_user_endpoint(user_id: int, body: UserUpdate, user: dict = Depends(auth.require_admin)):
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "User not found")

    updates = {}
    for field in ("is_admin", "gpu_quota_override", "storage_quota_override_gb"):
        val = getattr(body, field)
        if val is not None or field in body.model_fields_set:
            updates[field] = val

    if "is_admin" in updates:
        if not updates["is_admin"] and user_id == user["id"]:
            raise HTTPException(403, "Cannot remove your own admin privileges")
        if not updates["is_admin"] and db.count_admins() <= 1:
            raise HTTPException(403, "Cannot remove the last admin")

    try:
        db.update_user(user_id, **updates)
    except ValueError as e:
        raise HTTPException(400, str(e))

    db.log_event("INFO", "admin", f"User {target['username']} updated",
                 user_id=user["id"], details=json.dumps(updates))
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user_endpoint(user_id: int, user: dict = Depends(auth.require_admin)):
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


# ── System Parameters ───────────────────────────

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

    if "gpu_devices" in body:
        if not isinstance(body["gpu_devices"], list) or \
                not all(isinstance(d, dict) and "uuid" in d for d in body["gpu_devices"]):
            raise HTTPException(400, "gpu_devices must be a list of {uuid, enabled}")

    for key, value in body.items():
        if key == "gpu_devices":
            value = json.dumps(value)
        db.set_param(key, value, user_id=user["id"])

    db.log_event("INFO", "admin", "System params updated", user_id=user["id"],
                 details=json.dumps(body))
    return {"ok": True}


# ── System Logs ─────────────────────────────────

@router.get("/logs")
async def list_admin_logs(
    level: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(auth.require_admin),
):
    logs = db.list_logs(level=level, category=category, limit=limit, offset=offset)
    total = db.count_logs(level=level, category=category)
    return {"logs": logs, "total": total}


# ── Monitoring ──────────────────────────────────

@router.get("/monitoring")
async def get_monitoring(user: dict = Depends(auth.require_admin)):
    all_jobs = db.list_jobs()
    job_counts = {s: 0 for s in ("pending", "running", "done", "failed", "timeout", "cancelled")}
    for job in all_jobs:
        if job["status"] in job_counts:
            job_counts[job["status"]] += 1

    try:
        gpu_devices = await asyncio.to_thread(gpu.fetch_gpu_status)
    except Exception:
        gpu_devices = []

    # ponytail: list_objects_v2 caps at 1000 keys; use paginator if bucket grows large
    objects = storage.list_objects(storage.bucket)
    total_size = sum(o["size"] for o in objects)
    s3_info = {
        "bucket": storage.bucket,
        "endpoint": os.environ.get("DDP_S3_ENDPOINT", "http://172.16.50.100:9000"),
        "object_count": len(objects),
        "total_size_bytes": total_size,
    }

    return {
        "jobs": job_counts,
        "gpus": gpu_devices,
        "s3": s3_info,
    }
