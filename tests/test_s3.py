import socket
import uuid

import pytest
from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError

from app.storage import Storage

ENDPOINT = "http://127.0.0.1:9000"
ACCESS_KEY = "admin"
SECRET_KEY = "Minio@2026"


def _s3_reachable(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def storage():
    if not _s3_reachable("127.0.0.1", 9000):
        pytest.skip("S3 server unreachable")
    return Storage(
        endpoint_url=ENDPOINT,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket="ddp-test",
    )


@pytest.fixture
def fresh_key():
    """Unique key per test."""
    key = f"test/{uuid.uuid4()}/sample.txt"
    yield key


# ── Connectivity ──────────────────────────────

class TestConnectivity:
    def test_connection(self, storage):
        """Can we reach the S3 server and list buckets?"""
        resp = storage.s3.list_buckets()
        assert "Buckets" in resp

    def test_bucket_exists(self, storage):
        """The working bucket was created in __init__."""
        storage.s3.head_bucket(Bucket=storage.bucket)

    def test_create_bucket(self, storage):
        """Can create a temporary bucket and it shows up."""
        name = "ddp-test-create"
        storage.s3.create_bucket(Bucket=name)
        resp = storage.s3.list_buckets()
        names = [b["Name"] for b in resp.get("Buckets", [])]
        assert name in names
        storage.s3.delete_bucket(Bucket=name)


# ── Upload / Download round-trip ──────────────

class TestUploadDownload:
    def test_upload_and_get_bytes(self, storage, fresh_key):
        data = b"hello s3\n"
        uri = storage.upload_bytes(fresh_key, data)
        assert uri == f"s3://{storage.bucket}/{fresh_key}"
        assert storage.get_object_bytes(fresh_key) == data

    def test_object_exists(self, storage, fresh_key):
        storage.upload_bytes(fresh_key, b"x")
        assert storage.object_exists(fresh_key)
        assert not storage.object_exists(f"{fresh_key}.nope")

    def test_list_objects_prefix(self, storage):
        prefix = "test/list-check/"
        storage.upload_bytes(f"{prefix}a.txt", b"aaa")
        storage.upload_bytes(f"{prefix}b.txt", b"bbb")
        objs = storage.list_objects(storage.bucket, prefix)
        keys = [o["key"] for o in objs]
        assert f"{prefix}a.txt" in keys
        assert f"{prefix}b.txt" in keys
        # every result has key/size/s3_uri
        for o in objs:
            assert "size" in o and "s3_uri" in o

    def test_stream_object(self, storage, fresh_key):
        data = b"stream me" * 100
        storage.upload_bytes(fresh_key, data)
        chunks = storage.stream_object(fresh_key)
        assert b"".join(chunks) == data

    def test_large_upload(self, storage, fresh_key):
        data = b"\0" * (5 * 1024 * 1024)  # 5 MB, forces multipart
        storage.upload_bytes(fresh_key, data)
        assert storage.get_object_bytes(fresh_key) == data


# ── Error cases ───────────────────────────────

class TestErrors:
    def test_get_nonexistent_key(self, storage):
        with pytest.raises(ClientError):
            storage.get_object_bytes("does/not/exist/zzz.txt")
