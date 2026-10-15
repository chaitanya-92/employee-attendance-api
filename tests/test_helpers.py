from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.main import (
    PunchInIn,
    attendance_date,
    compute_late_minutes,
    compute_overtime,
    compute_work_hours,
    department_trend_pipeline,
    employee_monthly_pipeline,
    from_epoch_ms,
    late_leaderboard_pipeline,
)

UTC = timezone.utc


def test_late_minutes_observes_strict_ten_minute_grace():
    # 09:40 IST is 04:10 UTC.
    on_boundary = datetime(2026, 7, 6, 4, 10, 0, tzinfo=UTC)
    one_second_late = on_boundary + timedelta(seconds=1)

    assert compute_late_minutes(on_boundary, "09:30", "18:30") == 0
    assert compute_late_minutes(one_second_late, "09:30", "18:30") == 10


def test_work_hours_round_half_up():
    start = datetime(2026, 7, 6, 4, 0, tzinfo=UTC)
    assert compute_work_hours(start, start + timedelta(seconds=18)) == 0.01
    assert compute_work_hours(start, start + timedelta(seconds=54)) == 0.02


def test_overnight_punch_in_is_attributed_to_previous_shift_day():
    # 05:30 IST on July 7 is 00:00 UTC; shift runs 22:00-06:00.
    punch_in = datetime(2026, 7, 7, 0, 0, tzinfo=UTC)
    employee = {"shift_start": "22:00", "shift_end": "06:00"}

    assert attendance_date(punch_in, employee) == "2026-07-06"


def test_overnight_overtime_uses_next_calendar_day_shift_end():
    punch_in = datetime(2026, 7, 6, 16, 30, tzinfo=UTC)  # 22:00 IST
    punch_out = datetime(2026, 7, 7, 1, 0, tzinfo=UTC)  # 06:30 IST
    assert compute_overtime(punch_out, "22:00", "06:00", "2026-07-06", punch_in) == 30


def test_timestamp_must_be_integer_epoch_milliseconds():
    with pytest.raises(Exception):
        from_epoch_ms(1783312500)
    with pytest.raises(Exception):
        from_epoch_ms(1783312500000.5)
    with pytest.raises(ValidationError):
        PunchInIn.model_validate({"emp_code": "EMP0001", "punched_at": "1783312500000"})


def test_monthly_analytics_uses_mongodb_pipeline():
    pipeline = employee_monthly_pipeline("EMP0001", "2026-07")
    assert pipeline[0]["$match"]["emp_code"] == "EMP0001"
    assert any("$group" in stage for stage in pipeline)


def test_leaderboard_uses_rank_not_array_position():
    pipeline = late_leaderboard_pipeline("2026-07", 2)
    window_stage = next(stage["$setWindowFields"] for stage in pipeline if "$setWindowFields" in stage)
    assert "$rank" in window_stage["output"]["rank"]
    assert {"rank": {"$lte": 2}} in [stage.get("$match") for stage in pipeline]


def test_department_trend_builds_daily_rows_and_moving_average_in_mongodb():
    pipeline = department_trend_pipeline("Engineering", "2026-07-01", "2026-07-07")
    assert any("$range" in str(stage) for stage in pipeline)
    window_stage = next(stage["$setWindowFields"] for stage in pipeline if "$setWindowFields" in stage)
    assert window_stage["output"]["_moving_avg_raw"]["window"]["documents"] == [-6, 0]
