import base64
import json
import os
import time
import urllib.request

HARBOR_API = os.environ.get("DDP_HARBOR_API", "http://172.16.50.3:5000/api/v2.0")
HARBOR_PROJECT = os.environ.get("DDP_HARBOR_PROJECT", "neospark")
REGISTRY = os.environ.get("DDP_HARBOR_REGISTRY", "172.16.50.3:5000/neospark")
# whitelist of repos offered to users; future tags appear automatically
IMAGE_REPOS = [
    r.strip()
    for r in os.environ.get("DDP_IMAGE_REPOS", "ddp-cuda-ssh,ddp-pytorch-ssh").split(
        ","
    )
    if r.strip()
]

_cache: tuple[float, list[str]] = (0, [])
_TTL = 60


def _harbor_get(path: str):
    req = urllib.request.Request(f"{HARBOR_API}{path}")
    user = os.environ.get("DDP_HARBOR_USER", "admin")
    pw = os.environ.get("DDP_HARBOR_PASSWORD", "Harbor12345")
    req.add_header(
        "Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def list_images() -> list[str]:
    """All tags of the whitelisted repos, e.g. ['ddp-cuda-ssh:latest', ...]."""
    global _cache
    if time.time() - _cache[0] < _TTL:
        return _cache[1]
    images = []
    for repo in IMAGE_REPOS:
        artifacts = _harbor_get(
            f"/projects/{HARBOR_PROJECT}/repositories/{repo}/artifacts?page_size=100"
        )
        tags = sorted(
            {t["name"] for a in artifacts for t in (a.get("tags") or [])},
            key=lambda t: (t == "latest", t),
            reverse=True,
        )
        images.extend(f"{repo}:{t}" for t in tags)
    _cache = (time.time(), images)
    return images


def pull_spec(name: str) -> str:
    """Display name -> full image pull path."""
    return f"{REGISTRY}/{name}"
