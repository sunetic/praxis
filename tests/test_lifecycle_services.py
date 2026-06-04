from datetime import datetime

import pytest

from app.models import models
from app.services.lifecycle import (
    FunctionLifecycleService,
    FunctionState,
    LifecycleConstraintChecker,
    LifecycleValidationError,
    PageLifecycleService,
    PageState,
    ScheduleLifecycleService,
    ScheduleState,
)


def test_page_lifecycle_allows_valid_transition_and_blocks_invalid_transition():
    checker = LifecycleConstraintChecker()
    checker.validate_page_transition(PageState.DRAFT.value, PageState.PREVIEWING.value)
    with pytest.raises(LifecycleValidationError):
        checker.validate_page_transition(PageState.ARCHIVED.value, PageState.PUBLISHED.value)


def test_page_publish_and_rollback_switch_release_pointer():
    service = PageLifecycleService()
    page = models.Page(id=1, name="Slow SQL", status=PageState.DRAFT.value)

    r1 = service.publish(page, {"version": "v1"})
    r1.id = 101
    assert page.status == PageState.PUBLISHED.value
    assert page.current_release is r1
    assert r1.version == 1

    page.status = PageState.DRAFT.value
    r2 = service.publish(page, {"version": "v2"})
    r2.id = 102
    assert r2.version == 2
    assert page.current_release is r2

    restored = service.rollback(page, target_release_id=101)
    assert restored.id == 101
    assert page.current_release is r1
    assert page.status == PageState.PUBLISHED.value


def test_page_publish_is_allowed_in_published_state_for_republish():
    service = PageLifecycleService()
    page = models.Page(id=2, name="Republish", status=PageState.DRAFT.value)

    first = service.publish(page, {"version": "v1"})
    assert first.version == 1
    assert page.status == PageState.PUBLISHED.value

    second = service.publish(page, {"version": "v2"})
    assert second.version == 2
    assert page.status == PageState.PUBLISHED.value


def test_function_release_and_immutability_guard():
    service = FunctionLifecycleService()
    fn = models.Function(id=10, name="run-report", status=FunctionState.DRAFT.value)

    release = service.release(fn, code_snapshot="return 1", dependency_manifest={"pandas": "2.2"})
    release.id = 201

    assert fn.status == FunctionState.RELEASED.value
    assert fn.current_release is release
    service.ensure_released_target(fn)

    with pytest.raises(LifecycleValidationError):
        service.guard_release_immutable(release, {"code_snapshot": "return 2"})


def test_function_released_state_can_return_to_draft_for_new_iteration():
    checker = LifecycleConstraintChecker()
    checker.validate_function_transition(FunctionState.RELEASED.value, FunctionState.DRAFT.value)


def test_function_requires_release_for_production_path():
    service = FunctionLifecycleService()
    fn = models.Function(id=11, name="draft-only", status=FunctionState.DRAFT.value)
    with pytest.raises(LifecycleValidationError):
        service.ensure_released_target(fn)


def test_schedule_definition_and_next_run_calculation():
    service = ScheduleLifecycleService()
    now = datetime(2026, 3, 14, 10, 0, 0)

    next_interval = service.calculate_next_run_at(
        schedule_type="interval",
        cron_expression=None,
        interval_seconds=300,
        now=now,
    )
    assert next_interval == datetime(2026, 3, 14, 10, 5, 0)

    next_cron = service.calculate_next_run_at(
        schedule_type="cron",
        cron_expression="15 10 * * *",
        interval_seconds=None,
        now=now,
    )
    assert next_cron == datetime(2026, 3, 14, 10, 15, 0)

    with pytest.raises(LifecycleValidationError):
        service.validate_definition(
            schedule_type="interval",
            cron_expression=None,
            interval_seconds=0,
        )


def test_schedule_pause_and_resume_enforce_state_constraints():
    service = ScheduleLifecycleService()
    schedule = models.Schedule(
        name="daily",
        status=ScheduleState.ACTIVE.value,
        schedule_type="cron",
        cron_expression="0 9 * * *",
        interval_seconds=None,
        timezone="UTC",
        function_id=1,
    )

    service.pause(schedule)
    assert schedule.status == ScheduleState.PAUSED.value

    service.resume(schedule, now=datetime(2026, 3, 14, 8, 1, 0))
    assert schedule.status == ScheduleState.ACTIVE.value
    assert schedule.next_run_at == datetime(2026, 3, 14, 9, 0, 0)

    with pytest.raises(LifecycleValidationError):
        service.pause(schedule=models.Schedule(name="x", status="paused", function_id=1))
