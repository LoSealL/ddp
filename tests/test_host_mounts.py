import json

import pytest
from fastapi import HTTPException

from app.main import _validate_host_mounts


def ok(host, container):
    return json.loads(_validate_host_mounts(json.dumps([{"host": host, "container": container}])))


def test_empty():
    assert _validate_host_mounts("") == ""
    assert _validate_host_mounts("  ") == ""


def test_valid_normalized():
    assert ok("/mnt/sdb4/data/", "/mnt/data") == [{"host": "/mnt/sdb4/data", "container": "/mnt/data"}]
    assert ok("/home/u/x", "/data") == [{"host": "/home/u/x", "container": "/data"}]


@pytest.mark.parametrize("host", ["/", "/etc", "/etc/passwd", "/var/lib/kubelet", "/root", "/proc/1", "/usr/local", "/dev/nvidia0"])
def test_host_blacklist(host):
    with pytest.raises(HTTPException):
        ok(host, "/mnt/x")


@pytest.mark.parametrize("cont", ["/", "/workspace", "/workspace/x", "/dev/shm", "/etc", "/bin/sh"])
def test_container_reserved(cont):
    with pytest.raises(HTTPException):
        ok("/mnt/sdb4/x", cont)


@pytest.mark.parametrize("raw", ["not json", "[1]", "[{}]", '[{"host":"rel","container":"/x"}]', '[{"host":"/mnt/x","container":"rel"}]'])
def test_malformed(raw):
    with pytest.raises(HTTPException):
        _validate_host_mounts(raw)
