import asyncio

from . import db
from .storage import Storage


class MockExecutor:
    """Simulated runner for tests/dev without a cluster.

    Instantly transitions the job through the lifecycle and writes a stub log.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    async def execute(self, job_id: str):
        job = db.get_job(job_id)
        if not job:
            return
        db.update_job(job_id, status="running", started_at=db.now_iso())
        await asyncio.sleep(0.1)
        self.storage.upload_bytes(
            f"jobs/{job_id}/logs/run.log",
            f"mock run: {job['entry_command']}\n".encode(),
        )
        db.update_job(
            job_id,
            status="done",
            finished_at=db.now_iso(),
            output_count=0,
            s3_prefix=f"ddp/jobs/{job_id}/",
        )
