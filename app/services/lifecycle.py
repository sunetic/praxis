from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.models import models


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
        target = next((release for release in page.releases if release.id == target_release_id), None)
        if target is None:
            raise LifecycleValidationError(f"Page release {target_release_id} not found for rollback")
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
            if interval_seconds is None or interval_seconds <= 0:
                raise LifecycleValidationError("interval schedule must define positive interval_seconds")
            return

        if schedule_type == "cron":
            if not cron_expression:
                raise LifecycleValidationError("cron schedule must define cron_expression")
            self._parse_cron(cron_expression)
            return

        raise LifecycleValidationError(f"Unsupported schedule_type: {schedule_type}")

    def calculate_next_run_at(
        self,
        *,
        schedule_type: str,
        cron_expression: str | None,
        interval_seconds: int | None,
        now: datetime | None = None,
    ) -> datetime:
        now = now or datetime.utcnow()
        if schedule_type == "interval":
            self.validate_definition(
                schedule_type=schedule_type,
                cron_expression=cron_expression,
                interval_seconds=interval_seconds,
            )
            return now + timedelta(seconds=interval_seconds or 0)

        self.validate_definition(
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
        return self._next_cron_time(cron_expression or "* * * * *", now)

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
        )

    def _next_cron_time(self, expression: str, now: datetime) -> datetime:
        minute_expr, hour_expr, day_expr, month_expr, weekday_expr = self._parse_cron(expression)
        candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(0, 366 * 24 * 60):
            if self._matches(minute_expr, candidate.minute) and self._matches(hour_expr, candidate.hour):
                if self._matches(day_expr, candidate.day) and self._matches(month_expr, candidate.month):
                    # Python weekday: Monday=0. Cron weekday: Sunday=0/7.
                    cron_weekday = (candidate.weekday() + 1) % 7
                    if self._matches(weekday_expr, cron_weekday):
                        return candidate
            candidate += timedelta(minutes=1)
        raise LifecycleValidationError(f"Unable to calculate next run time from cron: {expression}")

    def _parse_cron(self, expression: str) -> tuple[str, str, str, str, str]:
        parts = expression.split()
        if len(parts) != 5:
            raise LifecycleValidationError("cron expression must have 5 fields")
        return tuple(parts)  # type: ignore[return-value]

    def _matches(self, expr: str, value: int) -> bool:
        if expr == "*":
            return True
        if expr.isdigit():
            return int(expr) == value
        if "/" in expr:
            base, step_text = expr.split("/", 1)
            if not step_text.isdigit():
                raise LifecycleValidationError(f"Unsupported cron token: {expr}")
            step = int(step_text)
            if step <= 0:
                raise LifecycleValidationError(f"Invalid cron step: {expr}")
            if base == "*":
                return value % step == 0
            if base.isdigit():
                start = int(base)
                return value >= start and (value - start) % step == 0
        raise LifecycleValidationError(f"Unsupported cron token: {expr}")
