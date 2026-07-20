from datetime import datetime, timedelta

from . import db


def is_in_window(start_h: int, start_m: int, end_h: int, end_m: int,
                 repeat: str, hour: int, minute: int, weekday: int) -> bool:
    """Check if hour:minute on weekday falls inside the window."""
    if repeat == "weekdays" and weekday >= 5:
        return False

    t = hour * 60 + minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m

    if start <= end:
        return start <= t < end
    else:
        return t >= start or t < end


def next_window_open(dt: datetime, start_h: int, start_m: int, end_h: int, end_m: int,
                     repeat: str) -> datetime:
    """Find the next time the window opens at or after dt."""
    if is_in_window(start_h, start_m, end_h, end_m, repeat,
                    dt.hour, dt.minute, dt.weekday()):
        return dt

    candidate = dt.replace(second=0, microsecond=0)
    for _ in range(7 * 24 * 60):  # max 7 days
        candidate = candidate + timedelta(minutes=1)
        if is_in_window(start_h, start_m, end_h, end_m, repeat,
                        candidate.hour, candidate.minute, candidate.weekday()):
            return candidate
    raise ValueError("No window opening found within 7 days")


def check_scheduled_time(local_dt: datetime) -> datetime:
    """Check a scheduled time against current system params.
    Returns the adjusted datetime (same if inside window, or next open if outside)."""
    params = db.get_all_params()
    start = params["time_window_start"]  # "HH:MM"
    end = params["time_window_end"]
    repeat = params["time_window_repeat"]

    start_h, start_m = int(start[:2]), int(start[3:5])
    end_h, end_m = int(end[:2]), int(end[3:5])

    if is_in_window(start_h, start_m, end_h, end_m, repeat,
                    local_dt.hour, local_dt.minute, local_dt.weekday()):
        return local_dt

    return next_window_open(local_dt, start_h, start_m, end_h, end_m, repeat)


def _compute_next_run(job: dict) -> datetime:
    """从 job['scheduled_at'] 算下一个 daily/weekly 触发时间点。

    从 scheduled_at 的第二天起找，避免本轮立刻再次触发。
    """
    from . import db as _db
    cur = datetime.fromisoformat(job["scheduled_at"])
    if cur.tzinfo is None:
        cur = cur.replace(tzinfo=_db.get_tz())
    cur = cur.replace(second=0, microsecond=0)
    if job.get("repeat_type") == "daily":
        return cur + timedelta(days=1)
    # weekly
    raw = job.get("repeat_weekdays") or "1"
    days = sorted({int(d) for d in raw.split(",") if d.strip()})
    for offset in range(1, 8):
        cand = cur + timedelta(days=offset)
        if cand.isoweekday() in days:
            return cand
    raise ValueError("No matching weekday within 7 days")
