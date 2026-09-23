from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.agent.persistence import run_db
from app.services.lifecycle import LifecycleValidationError, ScheduleLifecycleService
from app.services.scheduler.builder import SchedulerBuilderService
from app.services.scheduler.projection import project_schedule_run
from app.services.scheduler.runtime_state import get_scheduler_worker

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
    prompt = str(input_prompt or "").strip() or None
    if target_type == "agent" and not prompt:
        raise HTTPException(status_code=400, detail="Agent schedule requires input_prompt")
    return prompt


def _validate_agent_retry_policy(schedule: models.Schedule) -> None:
    if schedule.target_type == "agent" and schedule.max_retries != 0:
        raise HTTPException(
            status_code=400,
            detail="Agent schedules require max_retries=0; retrying an entire run can repeat effects",
        )


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
    if run.target_type == "agent":
        raise HTTPException(
            status_code=409, detail="Use the native run cancellation or recovery API"
        )
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
    return [project_schedule_run(db, item) for item in runs]


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
            timezone=timezone,
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
    _validate_agent_retry_policy(schedule)
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
            timezone=schedule.timezone,
        )
    else:
        schedule.next_run_at = None
    schedule.updated_at = _utc_now_naive()
    _validate_agent_retry_policy(schedule)
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("update", schedule_id=schedule.id)
    return _serialize(schedule)


@router.post("/{schedule_id}/build")
async def build_schedule(schedule_id: int, payload: dict[str, Any], request: Request):
    runtime = request.app.state.agent_runtime
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    def snapshot():
        with runtime.sessions() as db:
            return _serialize(_get_schedule_or_404(db, schedule_id))

    current = await run_db(snapshot)
    build = await _propose_schedule(runtime.models, prompt, current)

    def save():
        with runtime.sessions() as db:
            return _save_schedule_build(schedule_id, current, build, db)

    return await run_db(save)


async def _propose_schedule(models_factory, prompt: str, current: dict):
    try:
        return await SchedulerBuilderService(models_factory).apply_prompt(prompt, current)
    except ValidationError as err:
        logger.warning(
            "schedule_proposal_invalid errors=%s",
            [{"type": item["type"], "loc": item["loc"]} for item in err.errors()],
        )
        raise HTTPException(
            422, "Model returned an invalid schedule proposal; no changes were saved"
        ) from err
    except ValueError as err:
        raise HTTPException(
            422, "Model returned an invalid schedule proposal; no changes were saved"
        ) from err
    except Exception as err:
        logger.warning("schedule_model_request_failed error_type=%s", type(err).__name__)
        raise HTTPException(502, "Model request failed; no changes were saved") from err


def _save_schedule_build(schedule_id: int, expected: dict, build, db: Session):
    # Claim the exact configuration version atomically before reading/mutating
    # the row. SQLite gets a write lock; other databases lock this row. The
    # transaction rolls back on every validation error, including stale input.
    stamp = _utc_now_naive()
    previous_stamp = (
        datetime.fromisoformat(expected["updated_at"]) if expected["updated_at"] else None
    )
    claimed = db.execute(
        update(models.Schedule)
        .where(models.Schedule.id == schedule_id, models.Schedule.updated_at == previous_stamp)
        .values(updated_at=stamp)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise HTTPException(
            409,
            "Schedule changed while the proposal was generated; submit against the current configuration",
        )
    lifecycle = ScheduleLifecycleService()
    schedule = _get_schedule_or_404(db, schedule_id)
    actual = _serialize(schedule)
    actual["updated_at"] = expected["updated_at"]
    if actual != expected:
        raise HTTPException(
            409, "Schedule changed while the proposal was generated; no changes were saved"
        )
    _ensure_mutable_schedule_payload(schedule, build.patch)
    for field, value in build.patch.items():
        setattr(schedule, field, value)
    schedule.timezone = _normalize_timezone(schedule.timezone, default=DEFAULT_SCHEDULE_TIMEZONE)
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
        raise HTTPException(422, str(err)) from err
    if schedule.status == "active":
        schedule.next_run_at = lifecycle.calculate_next_run_at(
            schedule_type=schedule.schedule_type,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
            timezone=schedule.timezone,
        )
    else:
        schedule.next_run_at = None
    schedule.updated_at = _utc_now_naive()
    _validate_agent_retry_policy(schedule)
    db.commit()
    db.refresh(schedule)
    _refresh_scheduler_runtime("build", schedule_id=schedule.id)
    return {
        "schedule": _serialize(schedule),
        "build_summary": build.summary,
    }


@router.post("/ai-create", status_code=status.HTTP_201_CREATED)
async def ai_create_schedule(payload: dict[str, Any], request: Request):
    runtime = request.app.state.agent_runtime
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    def snapshot():
        with runtime.sessions() as db:
            target = _resolve_schedule_target(db, payload)
            _ensure_user_visible_target_type(target["target_type"])
            _normalize_datasource_id(db, payload.get("datasource_id"))
            _validate_target_input_contract(
                target_type=target["target_type"], input_prompt=payload.get("input_prompt")
            )
            return {
                "target_type": target["target_type"],
                "schedule_type": "cron",
                "cron_expression": "0 9 * * *",
                "interval_seconds": None,
                "timezone": _normalize_timezone(payload.get("timezone")),
                "status": _normalize_schedule_status(payload.get("status")),
                "max_retries": int(payload.get("max_retries", 0)),
            }

    current = await run_db(snapshot)
    build = await _propose_schedule(runtime.models, prompt, current)

    def save():
        with runtime.sessions() as db:
            # Re-check target publication/authorization using the same domain
            # create operation as the non-AI API; the model cannot choose it.
            saved = create_schedule({**payload, **current, **build.patch}, db)
            return {"schedule": saved, "build_summary": build.summary}

    return await run_db(save)


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
    return [project_schedule_run(db, item) for item in runs]


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
async def run_schedule_now(schedule_id: int):
    trace_id = str(uuid.uuid4())
    worker = get_scheduler_worker()
    if worker is None or worker.health().get("shutting_down"):
        raise HTTPException(status_code=503, detail="Scheduler submission service is unavailable")
    try:
        run_id, schedule_run_id = await worker.submit_now(schedule_id, trace_id=trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc
    return {
        "trace_id": trace_id,
        "schedule_id": schedule_id,
        "run_id": run_id,
        "schedule_run_id": schedule_run_id,
    }
