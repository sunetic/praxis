from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.function.runtime import FunctionRuntimeService
from app.services.lifecycle import LifecycleValidationError, ScheduleLifecycleService
from app.services.scheduler.builder import SchedulerBuilderService
from app.services.scheduler.runtime_state import get_scheduler_worker
from app.services.scheduler.worker import SchedulerWorker

router = APIRouter(prefix="/schedules", tags=["Schedules"])
SUPPORTED_SCHEDULE_TARGETS: set[str] = {"function", "agent"}
DEFAULT_SCHEDULE_TIMEZONE = "Asia/Shanghai"
USER_VISIBLE_SCHEDULE_TARGETS: set[str] = {"function", "agent"}
INTERNAL_SCHEDULE_TARGETS: set[str] = set()


def register_schedule_target(target_type: str, *, internal: bool = False) -> None:
    SUPPORTED_SCHEDULE_TARGETS.add(target_type)
    if internal:
        INTERNAL_SCHEDULE_TARGETS.add(target_type)
    else:
        USER_VISIBLE_SCHEDULE_TARGETS.add(target_type)


logger = get_logger("api.schedules")


def _serialize(record: Any) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {column.name: getattr(record, column.name) for column in record.__table__.columns},
            default=str,
            ensure_ascii=False,
        )
    )


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _get_schedule_or_404(db: Session, schedule_id: int) -> models.Schedule:
    schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return schedule


def _get_function_or_404(db: Session, function_id: int) -> models.Function:
    function = db.query(models.Function).filter(models.Function.id == function_id).first()
    if function is None:
        raise HTTPException(status_code=404, detail=f"Function {function_id} not found")
    return function


def _get_agent_or_404(db: Session, agent_id: int) -> models.Agent:
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return agent


def _normalize_schedule_status(raw_status: Any, *, default: str = "active") -> str:
    normalized = str(raw_status or default).strip().lower()
    if normalized not in {"active", "paused"}:
        raise HTTPException(status_code=400, detail="status must be active or paused")
    return normalized


def _normalize_timezone(raw_timezone: Any, *, default: str = DEFAULT_SCHEDULE_TIMEZONE) -> str:
    normalized = str(raw_timezone or default).strip() or default
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as err:
        raise HTTPException(status_code=400, detail=f"timezone '{normalized}' is invalid") from err
    return normalized


def _normalize_datasource_id(db: Session, raw_value: Any) -> int | None:
    if raw_value is None or raw_value == "":
        return None
    try:
        datasource_id = int(raw_value)
    except (TypeError, ValueError) as err:
        raise HTTPException(status_code=400, detail="datasource_id must be an integer") from err
    datasource = (
        db.query(models.DataSource)
        .filter(models.DataSource.id == datasource_id, models.DataSource.status == "active")
        .first()
    )
    if datasource is None:
        raise HTTPException(
            status_code=400, detail=f"Datasource {datasource_id} not found or inactive"
        )
    return datasource.id


def _resolve_schedule_target(
    db: Session,
    payload: dict[str, Any],
    *,
    current: models.Schedule | None = None,
) -> dict[str, Any]:
    raw_target_type = payload.get("target_type")
    raw_target_id = payload.get("target_id")
    if raw_target_id is None and "function_id" in payload:
        raw_target_type = raw_target_type or "function"
        raw_target_id = payload.get("function_id")

    target_type = (
        str(raw_target_type or (current.target_type if current else "function")).strip().lower()
    )
    if target_type not in SUPPORTED_SCHEDULE_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"target_type must be one of: {', '.join(sorted(SUPPORTED_SCHEDULE_TARGETS))}",
        )

    target_id = (
        raw_target_id if raw_target_id is not None else (current.target_id if current else None)
    )
    if not isinstance(target_id, int):
        raise HTTPException(status_code=400, detail="target_id is required")

    if target_type == "function":
        function = _get_function_or_404(db, target_id)
        if function.current_release_id is None or function.status != "released":
            raise HTTPException(
                status_code=400, detail="function must be released before scheduling"
            )
        return {
            "target_type": "function",
            "target_id": function.id,
            "function_id": function.id,
            "function_release_id": function.current_release_id,
        }

    if target_type == "agent":
        agent = _get_agent_or_404(db, target_id)
        if str(agent.status or "").strip().lower() != "active":
            raise HTTPException(status_code=400, detail="agent must be active before scheduling")
        return {
            "target_type": "agent",
            "target_id": agent.id,
            "function_id": None,
            "function_release_id": None,
        }
    if target_type == "stats_analysis":
        datasource = (
            db.query(models.DataSource)
            .filter(models.DataSource.id == target_id, models.DataSource.status == "active")
            .first()
        )
        if datasource is None:
            raise HTTPException(
                status_code=400, detail=f"Datasource {target_id} not found or inactive"
            )
        return {
            "target_type": "stats_analysis",
            "target_id": datasource.id,
            "function_id": None,
            "function_release_id": None,
        }

    if target_type == "collector":
        datasource = (
            db.query(models.DataSource)
            .filter(models.DataSource.id == target_id, models.DataSource.status == "active")
            .first()
        )
        if datasource is None:
            raise HTTPException(
                status_code=400, detail=f"Datasource {target_id} not found or inactive"
            )
        return {
            "target_type": "collector",
            "target_id": datasource.id,
            "function_id": None,
            "function_release_id": None,
        }

    raise HTTPException(status_code=400, detail=f"Unsupported target_type: {target_type}")


def _apply_schedule_target(schedule: models.Schedule, resolved_target: dict[str, Any]) -> None:
    schedule.target_type = resolved_target["target_type"]
    schedule.target_id = resolved_target["target_id"]
    schedule.function_id = resolved_target["function_id"]
    schedule.function_release_id = resolved_target["function_release_id"]


def _validate_target_input_contract(*, target_type: str, input_prompt: Any) -> str | None:
    return str(input_prompt or "").strip() or None


def _normalize_schedule_kind(raw_kind: Any, *, default: str = "custom") -> str:
    normalized = str(raw_kind or default).strip().lower()
    if normalized in {"builtin", "built_in"}:
        return "built_in"
    if normalized == "custom":
        return "custom"
    raise HTTPException(status_code=400, detail="kind must be built_in or custom")


def _is_built_in_schedule(schedule: models.Schedule) -> bool:
    if (
        _normalize_schedule_kind(getattr(schedule, "kind", "custom"), default="custom")
        == "built_in"
    ):
        return True
    if str(getattr(schedule, "target_type", "") or "").strip().lower() == "function":
        function = getattr(schedule, "function", None)
        if function is not None and str(getattr(function, "kind", "") or "").strip().lower() in {
            "built_in",
            "builtin",
        }:
            return True
    return False


def _ensure_user_visible_target_type(target_type: str) -> None:
    if target_type not in USER_VISIBLE_SCHEDULE_TARGETS:
        raise HTTPException(
            status_code=400, detail="user-facing schedules only support function or agent targets"
        )


def _ensure_mutable_schedule_payload(schedule: models.Schedule, payload: dict[str, Any]) -> None:
    if not _is_built_in_schedule(schedule):
        return
    allowed_fields = {"status", "schedule_type", "cron_expression", "interval_seconds", "timezone"}
    requested_fields = set(payload.keys())
    blocked_fields = requested_fields - allowed_fields
    if blocked_fields:
        raise HTTPException(
            status_code=403,
            detail="built-in schedules only support status and timing changes",
        )


def _ensure_deletable_schedule(schedule: models.Schedule) -> None:
    if _is_built_in_schedule(schedule):
        raise HTTPException(status_code=403, detail="built-in schedules cannot be deleted")


def _refresh_scheduler_runtime(reason: str, *, schedule_id: int | None = None) -> None:
    worker = get_scheduler_worker()
    if worker is None:
        return
    if schedule_id is not None:
        ok = worker.request_sync_schedule(schedule_id, timeout_seconds=3.0)
    else:
        ok = worker.request_refresh(timeout_seconds=3.0)
    if not ok:
        logger.warning(
            "scheduler_runtime_refresh_skipped %s",
            fmt_kv(reason=reason, schedule_id=schedule_id),
        )


def _repair_schedule_run_or_404(
    db: Session,
    *,
    schedule_id: int,
    run_id: int,
    min_age_seconds: int = 120,
) -> models.ScheduleRun:
    run = (
        db.query(models.ScheduleRun)
        .filter(models.ScheduleRun.id == run_id, models.ScheduleRun.schedule_id == schedule_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail=f"Schedule run {run_id} not found")
    if str(run.status or "").strip().lower() != "running":
        raise HTTPException(status_code=409, detail="Only running schedule runs can be repaired")
    started_at = run.started_at or run.created_at
    now = _utc_now_naive()
    if started_at is not None and started_at > now - timedelta(seconds=min_age_seconds):
        raise HTTPException(status_code=409, detail="Schedule run is too recent to repair safely")
    run.status = "failed"
    run.runtime_status = run.runtime_status or "failed"
    run.error_summary = run.error_summary or "Manually repaired stale running schedule run"
    run.finished_at = run.finished_at or now
    db.commit()
    db.refresh(run)
    return run


@router.get("")
def list_schedules(db: Session = Depends(get_db)):
    records = db.query(models.Schedule).order_by(models.Schedule.updated_at.desc()).all()
    return [_serialize(item) for item in records]


@router.get("/runs")
def list_all_schedule_runs(
    limit: int = 20,
    offset: int = 0,
    schedule_id: int | None = None,
    response: Response = None,
    db: Session = Depends(get_db),
):
    normalized_limit = max(1, min(limit, 200))
    normalized_offset = max(int(offset), 0)
    base_query = db.query(models.ScheduleRun)
    if isinstance(schedule_id, int):
        _get_schedule_or_404(db, schedule_id)
        base_query = base_query.filter(models.ScheduleRun.schedule_id == schedule_id)
    total = base_query.count()
    runs = (
        base_query.order_by(models.ScheduleRun.created_at.desc())
        .offset(normalized_offset)
        .limit(normalized_limit)
        .all()
    )
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Limit"] = str(normalized_limit)
        response.headers["X-Offset"] = str(normalized_offset)
    return [_serialize(item) for item in runs]


@router.get("/worker-health")
def scheduler_worker_health():
    settings = get_settings()
    worker = get_scheduler_worker()
    health = (
        worker.health()
        if worker is not None
        else {"running": False, "shutting_down": False, "job_count": 0}
    )
    return {
        "running": bool(health.get("running")),
        "shutting_down": bool(health.get("shutting_down")),
        "job_count": int(health.get("job_count") or 0),
        "autostart": bool(settings.scheduler_autostart),
        "refresh_interval_seconds": int(settings.scheduler_refresh_interval_seconds),
        "job_coalesce": bool(settings.scheduler_job_coalesce),
        "job_misfire_grace_seconds": int(settings.scheduler_job_misfire_grace_seconds),
        "job_max_instances": int(settings.scheduler_job_max_instances),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_schedule(payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    resolved_target = _resolve_schedule_target(db, payload)
    _ensure_user_visible_target_type(resolved_target["target_type"])
    timezone = _normalize_timezone(payload.get("timezone"))
    datasource_id = _normalize_datasource_id(db, payload.get("datasource_id"))

    schedule_type = str(payload.get("schedule_type") or "cron")
    cron_expression = payload.get("cron_expression")
    interval_seconds = payload.get("interval_seconds")
    try:
        lifecycle.validate_definition(
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    status_value = _normalize_schedule_status(payload.get("status"))
    next_run_at = (
        lifecycle.calculate_next_run_at(
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
        if status_value == "active"
        else None
    )
    input_prompt = _validate_target_input_contract(
        target_type=resolved_target["target_type"],
        input_prompt=payload.get("input_prompt"),
    )
    schedule = models.Schedule(
        name=str(
            payload.get("name")
            or f"schedule-{resolved_target['target_type']}-{resolved_target['target_id']}"
        ),
        description=str(payload.get("description") or "").strip() or None,
        kind="custom",
        status=status_value,
        target_type=resolved_target["target_type"],
        target_id=resolved_target["target_id"],
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        interval_seconds=interval_seconds,
        timezone=timezone,
        datasource_id=datasource_id,
        function_id=resolved_target["function_id"],
        function_release_id=resolved_target["function_release_id"],
        input_payload=payload.get("input_payload")
        if isinstance(payload.get("input_payload"), dict)
        else None,
        input_prompt=input_prompt,
        next_run_at=next_run_at,
        max_retries=int(payload.get("max_retries", 0)),
        retry_backoff_seconds=int(payload.get("retry_backoff_seconds", 60)),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("create", schedule_id=schedule.id)
    return _serialize(schedule)


@router.get("/{schedule_id}")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    return _serialize(_get_schedule_or_404(db, schedule_id))


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    _ensure_mutable_schedule_payload(schedule, payload)
    if any(field in payload for field in ("target_type", "target_id", "function_id")):
        resolved_target = _resolve_schedule_target(db, payload, current=schedule)
        if _is_built_in_schedule(schedule):
            _ensure_user_visible_target_type(schedule.target_type)
        else:
            _ensure_user_visible_target_type(resolved_target["target_type"])
        _apply_schedule_target(schedule, resolved_target)
    for field in (
        "name",
        "description",
        "schedule_type",
        "cron_expression",
        "interval_seconds",
        "max_retries",
        "retry_backoff_seconds",
    ):
        if field in payload:
            value = payload[field]
            if field == "description":
                value = str(value or "").strip() or None
            setattr(schedule, field, value)
    if "timezone" in payload:
        schedule.timezone = _normalize_timezone(
            payload.get("timezone"), default=schedule.timezone or DEFAULT_SCHEDULE_TIMEZONE
        )
    if "datasource_id" in payload:
        schedule.datasource_id = _normalize_datasource_id(db, payload.get("datasource_id"))
    if "input_payload" in payload:
        schedule.input_payload = (
            payload["input_payload"] if isinstance(payload["input_payload"], dict) else None
        )
    if "input_prompt" in payload:
        schedule.input_prompt = payload.get("input_prompt")
    if "status" in payload:
        schedule.status = _normalize_schedule_status(payload.get("status"), default=schedule.status)
    schedule.input_prompt = _validate_target_input_contract(
        target_type=schedule.target_type,
        input_prompt=schedule.input_prompt,
    )
    try:
        lifecycle.validate_definition(
            schedule_type=schedule.schedule_type,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
        )
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if schedule.status == "active":
        schedule.next_run_at = lifecycle.calculate_next_run_at(
            schedule_type=schedule.schedule_type,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
        )
    else:
        schedule.next_run_at = None
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("update", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/build")
def build_schedule(schedule_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    builder = SchedulerBuilderService()
    try:
        build = builder.apply_prompt(prompt, _serialize(schedule))
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"scheduler build failed: {err}") from err
    _ensure_mutable_schedule_payload(schedule, build.patch)
    if _is_built_in_schedule(schedule):
        build.patch.pop("name", None)
        build.patch.pop("description", None)
        build.patch.pop("max_retries", None)
        build.patch.pop("retry_backoff_seconds", None)
    for field, value in build.patch.items():
        setattr(schedule, field, value)
    schedule.timezone = _normalize_timezone(schedule.timezone, default=DEFAULT_SCHEDULE_TIMEZONE)
    schedule.input_prompt = _validate_target_input_contract(
        target_type=schedule.target_type,
        input_prompt=schedule.input_prompt,
    )
    lifecycle.validate_definition(
        schedule_type=schedule.schedule_type,
        cron_expression=schedule.cron_expression,
        interval_seconds=schedule.interval_seconds,
    )
    if schedule.status == "active":
        schedule.next_run_at = lifecycle.calculate_next_run_at(
            schedule_type=schedule.schedule_type,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
        )
    else:
        schedule.next_run_at = None
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("build", schedule_id=schedule.id)
    return {
        "schedule": _serialize(schedule),
        "build_summary": build.summary,
    }


@router.post("/ai-create", status_code=status.HTTP_201_CREATED)
def ai_create_schedule(payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    resolved_target = _resolve_schedule_target(db, payload)
    _ensure_user_visible_target_type(resolved_target["target_type"])
    datasource_id = _normalize_datasource_id(db, payload.get("datasource_id"))
    current_timezone = _normalize_timezone(payload.get("timezone"))
    builder = SchedulerBuilderService()
    current = {
        "schedule_type": "cron",
        "cron_expression": "0 9 * * *",
        "interval_seconds": None,
        "timezone": current_timezone,
    }
    try:
        build = builder.apply_prompt(prompt, current)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"scheduler build failed: {err}") from err
    schedule_type = str(build.patch.get("schedule_type") or current["schedule_type"])
    cron_expression = build.patch.get("cron_expression")
    interval_seconds = build.patch.get("interval_seconds")
    timezone = _normalize_timezone(
        build.patch.get("timezone") or current["timezone"], default=current["timezone"]
    )
    status_value = _normalize_schedule_status(build.patch.get("status") or payload.get("status"))
    max_retries = int(build.patch.get("max_retries") or payload.get("max_retries", 0))
    input_prompt = _validate_target_input_contract(
        target_type=resolved_target["target_type"],
        input_prompt=payload.get("input_prompt"),
    )

    lifecycle.validate_definition(
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        interval_seconds=interval_seconds,
    )
    next_run_at = (
        lifecycle.calculate_next_run_at(
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
        )
        if status_value == "active"
        else None
    )
    schedule = models.Schedule(
        name=str(
            payload.get("name")
            or f"schedule-{resolved_target['target_type']}-{resolved_target['target_id']}"
        ),
        description=str(payload.get("description") or "").strip() or None,
        kind="custom",
        status=status_value,
        target_type=resolved_target["target_type"],
        target_id=resolved_target["target_id"],
        schedule_type=schedule_type,
        cron_expression=cron_expression,
        interval_seconds=interval_seconds,
        timezone=timezone,
        datasource_id=datasource_id,
        function_id=resolved_target["function_id"],
        function_release_id=resolved_target["function_release_id"],
        input_payload=payload.get("input_payload")
        if isinstance(payload.get("input_payload"), dict)
        else None,
        input_prompt=input_prompt,
        next_run_at=next_run_at,
        max_retries=max_retries,
        retry_backoff_seconds=int(payload.get("retry_backoff_seconds", 60)),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("ai_create", schedule_id=schedule.id)
    return {
        "schedule": _serialize(schedule),
        "build_summary": build.summary,
    }


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = _get_schedule_or_404(db, schedule_id)
    _ensure_deletable_schedule(schedule)
    db.delete(schedule)
    db.commit()
    _refresh_scheduler_runtime("delete", schedule_id=schedule_id)
    return None


@router.get("/{schedule_id}/runs")
def list_schedule_runs(
    schedule_id: int,
    limit: int = 20,
    offset: int = 0,
    response: Response = None,
    db: Session = Depends(get_db),
):
    _get_schedule_or_404(db, schedule_id)
    normalized_limit = max(1, min(limit, 200))
    normalized_offset = max(int(offset), 0)
    base_query = db.query(models.ScheduleRun).filter(models.ScheduleRun.schedule_id == schedule_id)
    total = base_query.count()
    runs = (
        base_query.order_by(models.ScheduleRun.created_at.desc())
        .offset(normalized_offset)
        .limit(normalized_limit)
        .all()
    )
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Limit"] = str(normalized_limit)
        response.headers["X-Offset"] = str(normalized_offset)
    return [_serialize(item) for item in runs]


@router.post("/{schedule_id}/runs/{run_id}/repair")
def repair_schedule_run(schedule_id: int, run_id: int, db: Session = Depends(get_db)):
    _get_schedule_or_404(db, schedule_id)
    run = _repair_schedule_run_or_404(db, schedule_id=schedule_id, run_id=run_id)
    return _serialize(run)


@router.post("/{schedule_id}/pause")
def pause_schedule(schedule_id: int, db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    try:
        lifecycle.pause(schedule)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("pause", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/resume")
def resume_schedule(schedule_id: int, db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    try:
        lifecycle.resume(schedule)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("resume", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/disable")
def disable_schedule(schedule_id: int, db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    try:
        lifecycle.pause(schedule)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("disable", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/enable")
def enable_schedule(schedule_id: int, db: Session = Depends(get_db)):
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    try:
        lifecycle.resume(schedule)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    schedule.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("enable", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/run-now")
async def run_schedule_now(schedule_id: int, db: Session = Depends(get_db)):
    _get_schedule_or_404(db, schedule_id)
    trace_id = str(uuid.uuid4())
    worker = get_scheduler_worker()
    if worker is not None and worker.health().get("running"):
        run_id, schedule_run_id = await worker.submit_now(schedule_id, trace_id=trace_id)
        return {
            "trace_id": trace_id,
            "schedule_id": schedule_id,
            "run_id": run_id,
            "schedule_run_id": schedule_run_id,
        }

    runtime_session_factory = sessionmaker(
        bind=db.get_bind(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    fallback_worker = SchedulerWorker(
        session_factory=runtime_session_factory,
        runtime_service=FunctionRuntimeService(session_factory=runtime_session_factory),
    )
    run_id, schedule_run_id = await fallback_worker.submit_now(schedule_id, trace_id=trace_id)
    return {
        "trace_id": trace_id,
        "schedule_id": schedule_id,
        "run_id": run_id,
        "schedule_run_id": schedule_run_id,
    }
