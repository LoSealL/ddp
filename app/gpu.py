import os
import re
import urllib.request

METRICS_URL = os.environ.get("DDP_HAMI_METRICS", "http://10.43.219.93:31993/metrics")

_LINE = re.compile(r"^(\w+)\{([^}]*)\}\s+([0-9.eE+-]+)$")
_LABEL = re.compile(r'(\w+)="([^"]*)"')

_METRICS = {
    "hami_gpu_memory_limit_bytes": "mem_total",
    "hami_gpu_memory_allocated_bytes": "mem_used",
    "hami_gpu_core_limit_ratio": "cores_total",
    "hami_gpu_core_allocated_ratio": "cores_used",
    "hami_gpu_shared_count": "shared",
}


def fetch_gpu_status() -> list[dict]:
    """Scrape HAMi scheduler metrics -> per-GPU allocation status."""
    with urllib.request.urlopen(METRICS_URL, timeout=5) as resp:
        text = resp.read().decode()

    gpus: dict[str, dict] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m or m.group(1) not in _METRICS:
            continue
        name, raw_labels, raw_val = m.groups()
        labels = dict(_LABEL.findall(raw_labels))
        uuid = labels.get("device_uuid")
        if not uuid:
            continue
        g = gpus.setdefault(uuid, {
            "uuid": uuid,
            "node": labels.get("node", ""),
            "index": int(labels.get("device_index", 0)),
            "type": labels.get("device_type", ""),
            "mem_total": 0, "mem_used": 0,
            "cores_total": 0, "cores_used": 0, "shared": 0,
        })
        g[_METRICS[name]] = float(raw_val)
    return sorted(gpus.values(), key=lambda g: (g["node"], g["index"]))
