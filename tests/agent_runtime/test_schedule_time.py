from datetime import UTC, datetime

import pytest

from app.db.base import Base
from app.models.models import Schedule
from app.services.lifecycle import LifecycleValidationError, ScheduleLifecycleService
from app.services.scheduler.result import ScheduleRuntimeResult
from app.services.scheduler.triggers import cron_trigger
from app.services.scheduler.worker import SchedulerWorker


@pytest.mark.parametrize("expression", ["30 9 * * 1-5", "30 9 * * mon-fri"])
def test_next_run_and_execution_agree_on_workday_timezone(expression):
    schedule = Schedule(schedule_type="cron", cron_expression=expression, timezone="Asia/Shanghai")
    now = datetime(2026, 9, 18, 2, tzinfo=UTC)  # Friday 10:00 local, past today's 09:30.
    expected = datetime(2026, 9, 21, 1, 30)
    assert (
        ScheduleLifecycleService().calculate_next_run_at(
            schedule_type="cron",
            cron_expression=expression,
            interval_seconds=None,
            timezone=schedule.timezone,
            now=now,
        )
        == expected
    )
    trigger = SchedulerWorker()._build_trigger(schedule)
    assert trigger.get_next_fire_time(None, now).astimezone(UTC).replace(tzinfo=None) == expected


@pytest.mark.parametrize("weekday", ["0", "7", "sun"])
def test_sunday_is_not_monday_in_execution(weekday):
    result = cron_trigger(f"0 9 * * {weekday}").get_next_fire_time(
        None, datetime(2026, 9, 19, tzinfo=UTC)
    )
    assert result == datetime(2026, 9, 20, 9, tzinfo=UTC)


def test_calendar_library_handles_dst_offset_and_strictly_next_occurrence():
    service = ScheduleLifecycleService()
    result = service.calculate_next_run_at(
        schedule_type="cron",
        cron_expression="0 9 * * *",
        interval_seconds=None,
        timezone="America/New_York",
        now=datetime(2026, 3, 7, 14, tzinfo=UTC),
    )
    assert result == datetime(2026, 3, 8, 13)


@pytest.mark.parametrize(
    "expression", ["99 9 * * *", "0 25 * * *", "* * * * 8", "* * * * */0", "bad"]
)
def test_invalid_cron_is_rejected_before_saving_even_when_paused(expression):
    with pytest.raises(LifecycleValidationError):
        ScheduleLifecycleService().validate_definition(
            schedule_type="cron", cron_expression=expression, interval_seconds=None
        )


@pytest.mark.parametrize("new_status", ["active", "paused"])
def test_finished_occurrence_does_not_restore_old_timing_or_undo_a_pause(store, new_status):
    Base.metadata.create_all(store.sessions.kw["bind"])
    with store.sessions.begin() as db:
        row = Schedule(
            name="timing race",
            target_type="agent",
            target_id=1,
            schedule_type="interval",
            interval_seconds=300,
            timezone="UTC",
            status="active",
        )
        db.add(row)
        db.flush()
        schedule_id = row.id
        db.expunge(row)
        old_snapshot = row
    with store.sessions.begin() as db:
        row = db.get(Schedule, schedule_id)
        row.status = new_status
        row.interval_seconds = 900
        row.next_run_at = None
    SchedulerWorker(session_factory=store.sessions)._persist_submitted_result(
        old_snapshot,
        0,
        ScheduleRuntimeResult(
            run_id="native",
            status="queued",
            output=None,
            output_summary=None,
            error_class=None,
            error_message=None,
            duration_ms=1,
        ),
    )
    with store.sessions() as db:
        current = db.get(Schedule, schedule_id)
        assert current.status == new_status
        assert current.interval_seconds == 900
        if new_status == "paused":
            assert current.next_run_at is None
        else:
            assert (current.next_run_at - current.last_run_at).total_seconds() == 900
