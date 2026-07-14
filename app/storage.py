import shutil
from pathlib import Path


class Storage:
    """Mock S3 backed by local filesystem.

    Interface mirrors the subset of boto3 we need:
      upload_file(bucket, key, local_path) -> s3_uri
      list_objects(bucket, prefix) -> [{key, size, s3_uri}]

    Swap this class for a real S3Client (boto3) when going to production.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def upload_file(self, bucket: str, key: str, local_path: Path) -> str:
        dest = self.base_dir / bucket / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(local_path), str(dest))
        return f"s3://{bucket}/{key}"

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict]:
        bucket_dir = self.base_dir / bucket
        if not bucket_dir.exists():
            return []
        results = []
        for f in sorted(bucket_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(bucket_dir)
            key = str(rel).replace("\\", "/")
            if prefix and not key.startswith(prefix):
                continue
            results.append({
                "key": key,
                "size": f.stat().st_size,
                "s3_uri": f"s3://{bucket}/{key}",
            })
        return results
