from datetime import datetime, timezone, timedelta

import pytest

from app import db, timecheck


def _dt(h, m, dow=None):
    """Create a UTC datetime at hour:h minute:m. dow=0=Monday. Base date 2026-07-15 (Wednesday=2)."""
    dt = datetime(2026, 7, 15, h, m, tzinfo=timezone.utc)
    if dow is not None:
        days_diff = dow - 2  # Wednesday=2
        dt = dt + timedelta(days=days_diff)
    return dt


class TestTimeWindow:
    def test_inside_normal_window(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 12, 0, weekday=2) is True

    def test_outside_normal_window(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 20, 0, weekday=2) is False

    def test_inside_overnight_window(self):
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 23, 0, weekday=2) is True

    def test_inside_overnight_window_early_morning(self):
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 3, 0, weekday=2) is True

    def test_outside_overnight_window(self):
        assert timecheck.is_in_window(22, 0, 6, 0, "daily", 12, 0, weekday=2) is False

    def test_weekdays_monday_inside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=0) is True

    def test_weekdays_saturday_outside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=5) is False

    def test_weekdays_sunday_outside(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "weekdays", 12, 0, weekday=6) is False

    def test_boundary_start(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 9, 0, weekday=2) is True

    def test_boundary_end_excluded(self):
        assert timecheck.is_in_window(9, 0, 17, 0, "daily", 17, 0, weekday=2) is False


class TestNextWindowOpen:
    def test_already_inside_returns_same_time(self):
        dt = _dt(12, 0)  # Wednesday 12:00
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "daily")
        assert result == dt

    def test_finds_next_morning_window(self):
        # 18:00 on Wednesday, window 09:00-17:00 -> next open is tomorrow 09:00
        dt = _dt(18, 0)
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "daily")
        assert result.hour == 9
        assert result.minute == 0
        assert result.day == 16  # next day

    def test_finds_overnight_open(self):
        # 12:00 on Wednesday, window 22:00-06:00 -> next open is 22:00 today
        dt = _dt(12, 0)
        result = timecheck.next_window_open(dt, 22, 0, 6, 0, "daily")
        assert result.hour == 22
        assert result.minute == 0
        assert result.day == 15  # same day

    def test_weekdays_skips_weekend(self):
        # Friday 18:00, window 09:00-17:00 weekdays -> next open is Monday 09:00
        dt = _dt(18, 0, dow=4)  # Friday
        result = timecheck.next_window_open(dt, 9, 0, 17, 0, "weekdays")
        assert result.weekday() == 0  # Monday
        assert result.hour == 9


class TestCheckScheduledTime:
    def test_returns_adjusted_when_outside(self):
        db.set_param("time_window_start", "09:00", user_id=1)
        db.set_param("time_window_end", "17:00", user_id=1)
        db.set_param("time_window_repeat", "daily", user_id=1)
        # 18:00 local -> adjusted to next day 09:00
        dt = datetime(2026, 7, 15, 18, 0)
        result = timecheck.check_scheduled_time(dt)
        assert result.hour == 9
        assert result.day == 16

    def test_returns_same_when_inside(self):
        db.set_param("time_window_start", "09:00", user_id=1)
        db.set_param("time_window_end", "17:00", user_id=1)
        db.set_param("time_window_repeat", "daily", user_id=1)
        dt = datetime(2026, 7, 15, 12, 0)
        result = timecheck.check_scheduled_time(dt)
        assert result == dt


class TestComputeNextRun:
    def test_daily_advances_one_day(self):
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "daily", "repeat_weekdays": None}
        nxt = timecheck._compute_next_run(job)
        assert nxt.day == 21
        assert nxt.hour == 22
        assert nxt.minute == 0

    def test_weekly_mon_wed_fri_from_mon(self):
        # 2026-07-20 是周一(isoweekday=1), weekdays=1,3,5 -> next is Wed(3)
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "1,3,5"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 3
        assert nxt.day == 22

    def test_weekly_sunday_only_from_mon(self):
        # 2026-07-20 周一 -> next Sunday(7) = 2026-07-26
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "7"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 7
        assert nxt.day == 26

    def test_weekday_order_does_not_matter(self):
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "5,3,1"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 3
