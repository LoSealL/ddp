import io
import os
import socket
import zipfile

import pytest

os.environ.setdefault("DDP_EXECUTOR", "mock")

from app import db


def s3_reachable(host="127.0.0.1", port=9000, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Each test gets a fresh temp SQLite DB."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


@pytest.fixture
def make_zip():
    """Factory: create an in-memory zip from {filename: content}."""
    def _make(files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()
    return _make
