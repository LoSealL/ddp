import io
import os
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


class Storage:
    """S3-compatible storage (MinIO / AWS S3).

    Interface mirrors what the executor and API routes need:
      upload_bytes(key, data)              -> s3_uri
      list_objects(bucket, prefix)         -> [{key, size, s3_uri}]
      get_object_bytes(key)                -> bytes
      stream_object(key)                   -> iterator[bytes]
      object_exists(key)                   -> bool
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
    ):
        endpoint_url = endpoint_url or os.environ.get(
            "DDP_S3_ENDPOINT", "http://172.16.50.100:9000"
        )
        access_key = access_key or os.environ.get("DDP_S3_ACCESS_KEY", "admin")
        secret_key = secret_key or os.environ.get("DDP_S3_SECRET_KEY", "yuanqi,123")
        self.bucket = bucket or os.environ.get("DDP_S3_BUCKET", "ddp")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.s3.create_bucket(Bucket=self.bucket)

    def upload_bytes(self, key: str, data: bytes) -> str:
        self.s3.upload_fileobj(io.BytesIO(data), self.bucket, key)
        return f"s3://{self.bucket}/{key}"

    def append_bytes(self, key: str, data: bytes, cap: int = 10 * 1024 * 1024) -> str:
        """S3 不支持原生 append：读旧 → 拼接 → 裁剪尾部 → 重传。

        key 不存在时按空串处理；超 cap 字节时只保留尾部 cap 字节。
        """
        try:
            existing = self.get_object_bytes(key)
        except ClientError:
            existing = b""
        combined = existing + data
        if len(combined) > cap:
            combined = combined[-cap:]
        self.upload_bytes(key, combined)
        return f"s3://{self.bucket}/{key}"

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict]:
        resp = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        results = []
        for obj in resp.get("Contents", []):
            results.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "s3_uri": f"s3://{bucket}/{obj['Key']}",
                }
            )
        return results

    def get_object_bytes(self, key: str) -> bytes:
        resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def stream_object(self, key: str):
        resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].iter_chunks()

    def object_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete_prefix(self, prefix: str):
        """Delete all objects under a prefix."""
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objects:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
