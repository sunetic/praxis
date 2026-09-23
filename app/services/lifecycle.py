from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.models import models
from app.services.scheduler.triggers import cron_trigger


class LifecycleValidationError(ValueError):
    pass


class PageState(StrEnum):
    DRAFT = "draft"
    PREVIEWING = "previewing"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class FunctionState(StrEnum):
    DRAFT = "draft"
    RELEASED = "released"


class ScheduleState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass(frozen=True)
class TransitionRule:
    from_state: str
    to_state: str


class LifecycleConstraintChecker:
    _page_transitions = {
        PageState.DRAFT: {PageState.PREVIEWING, PageState.PUBLISHED, PageState.ARCHIVED},
        PageState.PREVIEWING: {PageState.DRAFT, PageState.PUBLISHED, PageState.ARCHIVED},
        PageState.PUBLISHED: {PageState.ARCHIVED, PageState.DRAFT},
        PageState.ARCHIVED: {PageState.DRAFT},
    }

    _function_transitions = {
        FunctionState.DRAFT: {FunctionState.RELEASED},
        FunctionState.RELEASED: {FunctionState.DRAFT},
    }

    _schedule_transitions = {
        ScheduleState.ACTIVE: {ScheduleState.PAUSED},
        ScheduleState.PAUSED: {ScheduleState.ACTIVE},
    }

    _operation_constraints: dict[str, dict[str, set[str]]] = {
        "page": {
            "publish": {PageState.DRAFT, PageState.PREVIEWING, PageState.PUBLISHED},
            "archive": {PageState.DRAFT, PageState.PREVIEWING, PageState.PUBLISHED},
            "rollback": {PageState.PUBLISHED, PageState.PREVIEWING},
        },
        "function": {
            "release": {FunctionState.DRAFT, FunctionState.RELEASED},
            "invoke": {FunctionState.RELEASED},
        },
        "schedule": {
            "pause": {ScheduleState.ACTIVE},
            "resume": {ScheduleState.PAUSED},
            "run-now": {ScheduleState.ACTIVE, ScheduleState.PAUSED},
        },
    }

    def validate_page_transition(self, current: str, target: str) -> None:
        self._validate_transition(self._page_transitions, current, target, "page")

    def validate_function_transition(self, current: str, target: str) -> None:
        self._validate_transition(self._function_transitions, current, target, "function")

    def validate_schedule_transition(self, current: str, target: str) -> None:
        self._validate_transition(self._schedule_transitions, current, target, "schedule")

    def ensure_operation_allowed(self, object_type: str, state: str, action: str) -> None:
        constraints = self._operation_constraints.get(object_type.lower())
        if not constraints or action not in constraints:
            return
        allowed_states = constraints[action]
        if state not in allowed_states:
            allowed = ", ".join(sorted(allowed_states))
            raise LifecycleValidationError(
                f"{object_type}.{action} is not allowed in state '{state}'. Allowed: {allowed}"
            )

    def _validate_transition(
        self,
        transition_map: dict[StrEnum, set[StrEnum]],
        current: str,
        target: str,
        object_type: str,
    ) -> None:
        current_state = next((state for state in transition_map if state.value == current), None)
        target_state = next((state for state in transition_map if state.value == target), None)
        if current_state is None or target_state is None:
            raise LifecycleValidationError(
                f"Unknown {object_type} lifecycle transition: '{current}' -> '{target}'"
            )
        if target_state not in transition_map[current_state]:
            raise LifecycleValidationError(
                f"Invalid {object_type} lifecycle transition: '{current}' -> '{target}'"
            )


class PageLifecycleService:
    def __init__(self, checker: LifecycleConstraintChecker | None = None):
        self.checker = checker or LifecycleConstraintChecker()

    def transition(self, page: models.Page, target_state: PageState) -> None:
        self.checker.validate_page_transition(page.status, target_state.value)
        page.status = target_state.value

    def publish(
        self,
        page: models.Page,
        artifact_payload: dict | None,
        *,
        artifact_uri: str | None = None,
        release_notes: str | None = None,
    ) -> models.PageRelease:
        self.checker.ensure_operation_allowed("page", page.status, "publish")

        latest_version = max((release.version for release in page.releases), default=0)
        release = models.PageRelease(
            page=page,
            page_id=page.id or 0,
            version=latest_version + 1,
            artifact_uri=artifact_uri,
            artifact_payload=artifact_payload,
            release_notes=release_notes,
        )
        page.releases.append(release)
        page.current_release = release
        page.status = PageState.PUBLISHED.value
        return release

    def rollback(self, page: models.Page, target_release_id: int) -> models.PageRelease:
        self.checker.ensure_operation_allowed("page", page.status, "rollback")
        target = next(
            (release for release in page.releases if release.id == target_release_id), None
        )
        if target is None:
            raise LifecycleValidationError(
                f"Page release {target_release_id} not found for rollback"
            )
        page.current_release = target
        page.status = PageState.PUBLISHED.value
        return target

    def archive(self, page: models.Page) -> None:
        self.checker.ensure_operation_allowed("page", page.status, "archive")
        page.status = PageState.ARCHIVED.value


class FunctionLifecycleService:
    def __init__(self, checker: LifecycleConstraintChecker | None = None):
        self.checker = checker or LifecycleConstraintChecker()

    def transition(self, function: models.Function, target_state: FunctionState) -> None:
        self.checker.validate_function_transition(function.status, target_state.value)
        function.status = target_state.value

    def release(
        self,
        function: models.Function,
        *,
        code_snapshot: str,
        dependency_manifest: dict | None = None,
        release_metadata: dict | None = None,
    ) -> models.FunctionRelease:
        self.checker.ensure_operation_allowed("function", function.status, "release")

        latest_version = max((release.version for release in function.releases), default=0)
        release = models.FunctionRelease(
            function=function,
            function_id=function.id or 0,
            version=latest_version + 1,
            code_snapshot=code_snapshot,
            dependency_manifest=dependency_manifest,
            release_metadata=release_metadata,
        )
        function.releases.append(release)
        function.current_release = release
        function.status = FunctionState.RELEASED.value
        return release

    def ensure_released_target(self, function: models.Function) -> None:
        if function.current_release is None:
            raise LifecycleValidationError("Function has no released version for production path")
        if function.status != FunctionState.RELEASED.value:
            raise LifecycleValidationError(
                f"Function must be in '{FunctionState.RELEASED.value}' state for production path"
            )

    def guard_release_immutable(self, release: models.FunctionRelease, patch: dict) -> None:
        if patch:
            raise LifecycleValidationError(
                f"Function release {release.id} is immutable and cannot be modified"
            )


class ScheduleLifecycleService:
    def __init__(self, checker: LifecycleConstraintChecker | None = None):
        self.checker = checker or LifecycleConstraintChecker()

    def validate_definition(
        self,
        *,
        schedule_type: str,
        cron_expression: str | None,
        interval_seconds: int | None,
    ) -> None:
        if schedule_type == "interval":
            if cron_expression is not None:
                raise LifecycleValidationError("interval schedule must not define cron_expression")
            if interval_seconds is None or interval_seconds <= 0:
                raise LifecycleValidationError(
                    "interval schedule must define positive interval_seconds"
                )
            return

        if schedule_type == "cron":
            if interval_seconds is not None:
                raise LifecycleValidationError("cron schedule must not define interval_seconds")
            if not cron_expression:
                raise LifecycleValidationError("cron schedule must define cron_expression")
            try:
                cron_trigger(cron_expression)
            except (ValueError, KeyError) as err:
                raise LifecycleValidationError(str(err)) from err
            return

        raise LifecycleValidationError(f"Unsupported schedule_type: {schedule_type}")

    def calculate_next_run_at(
        self,
        *,
        schedule_type: str,
        cron_expression: str | None,
        interval_seconds: int | None,
        now: datetime | None = None,
        timezone: str = "UTC",
    ) -> datetime:
        now = now or datetime.now(UTC)
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        if schedule_type == "interval":
            self.validate_definition(
                schedule_type=schedule_type,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
            )
            return (now + timedelta(seconds=interval_seconds or 0)).replace(tzinfo=None)

        self.validate_definition(
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
        try:
            result = cron_trigger(cron_expression or "", timezone).get_next_fire_time(
                None, now + timedelta(microseconds=1)
            )
        except (ValueError, KeyError) as err:
            raise LifecycleValidationError(str(err)) from err
        if result is None:
            raise LifecycleValidationError("Cron expression has no future occurrence")
        return result.astimezone(UTC).replace(tzinfo=None)

    def pause(self, schedule: models.Schedule) -> None:
        self.checker.ensure_operation_allowed("schedule", schedule.status, "pause")
        self.checker.validate_schedule_transition(schedule.status, ScheduleState.PAUSED.value)
        schedule.status = ScheduleState.PAUSED.value
        schedule.next_run_at = None

    def resume(self, schedule: models.Schedule, now: datetime | None = None) -> None:
        self.checker.ensure_operation_allowed("schedule", schedule.status, "resume")
        self.checker.validate_schedule_transition(schedule.status, ScheduleState.ACTIVE.value)
        schedule.status = ScheduleState.ACTIVE.value
        schedule.next_run_at = self.calculate_next_run_at(
            schedule_type=schedule.schedule_type,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
            now=now,
            timezone=schedule.timezone or "UTC",
        )
