import asyncio
import zipfile
import json
import shutil
import os
from pathlib import Path
from datetime import datetime, timezone

from . import db
from .storage import Storage

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
WORKSPACE_DIR = DATA_DIR / "workspaces"

BUCKET = "ddp"


class MockExecutor:
    """Mock K8s Job runner.

    Simulates: Pod creation -> init (unzip + pip install) -> main container (run python)
    -> completion -> output collection -> cleanup.

    Swap with a real K8s client that creates Job objects with:
      activeDeadlineSeconds, ttlSecondsAfterFinished, backoffLimit: 0
    """

    def __init__(self, storage: Storage):
        self.storage = storage
        for d in [UPLOAD_DIR, WORKSPACE_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    async def execute(self, job_id: str):
        job = db.get_job(job_id)
        if not job:
            return

        db.update_job(job_id, status="running",
                      started_at=datetime.now(timezone.utc).isoformat())

        work_dir = WORKSPACE_DIR / job_id

        try:
            # --- init: unzip ---
            work_dir.mkdir(parents=True, exist_ok=True)
            zip_path = UPLOAD_DIR / f"{job_id}.zip"
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(str(work_dir))
            self._flatten_single_toplevel(work_dir)

            log_chunks = []

            # --- init: pip install requirements ---
            req_file = work_dir / "requirements.txt"
            if req_file.exists():
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "-r", "requirements.txt",
                    cwd=str(work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await proc.communicate()
                log_chunks.append(b"=== pip install ===\n")
                log_chunks.append(stdout or b"")
                log_chunks.append(b"\n")

            # --- main: run entry command ---
            log_chunks.append(f"=== {job['entry_command']} ===\n".encode())

            proc = await asyncio.create_subprocess_shell(
                job["entry_command"],
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            timeout_seconds = job["timeout_minutes"] * 60
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
                log_chunks.append(stdout or b"")
                status = "done" if proc.returncode == 0 else "failed"
            except asyncio.TimeoutError:
                proc.kill()
                stdout, _ = await proc.communicate()
                log_chunks.append(stdout or b"")
                log_chunks.append(b"\n[TIMEOUT: killed after max runtime]\n")
                status = "timeout"

            # --- upload logs to S3 ---
            log_bytes = b"".join(log_chunks)
            self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log", log_bytes)

            # --- collect outputs (conventional dir + manifest) ---
            output_count = self._collect_outputs(job_id, work_dir)

            # --- cleanup workspace (zero residue) ---
            shutil.rmtree(str(work_dir), ignore_errors=True)
            os.remove(str(zip_path))

            db.update_job(
                job_id,
                status=status,
                finished_at=datetime.now(timezone.utc).isoformat(),
                output_count=output_count,
                s3_prefix=f"{BUCKET}/jobs/{job_id}/",
            )

        except Exception as e:
            db.update_job(
                job_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )
            shutil.rmtree(str(work_dir), ignore_errors=True)

    def _flatten_single_toplevel(self, work_dir: Path):
        """If zip extracted to a single top-level dir, lift contents up one level."""
        children = [p for p in work_dir.iterdir() if not p.name.startswith(".")]
        visible = [c for c in children if c.name != "__MACOSX"]
        if len(visible) == 1 and visible[0].is_dir():
            inner = visible[0]
            for item in list(inner.iterdir()):
                target = work_dir / item.name
                shutil.move(str(item), str(target))
            inner.rmdir()

    def _collect_outputs(self, job_id: str, work_dir: Path) -> int:
        prefix = f"jobs/{job_id}/output"
        count = 0

        # 1. Conventional output directory
        output_dir = work_dir / "output"
        if output_dir.exists():
            for f in output_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(work_dir / "output")
                    key = f"{prefix}/{rel}".replace("\\", "/")
                    self.storage.upload_file(BUCKET, key, f)
                    count += 1

        # 2. Manifest-declared files
        manifest_path = work_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for fpath in manifest.get("outputs", []):
                    full = work_dir / fpath
                    if full.exists() and full.is_file():
                        key = f"{prefix}/{fpath}".replace("\\", "/")
                        self.storage.upload_file(BUCKET, key, full)
                        count += 1
            except (json.JSONDecodeError, OSError):
                pass

        return count
