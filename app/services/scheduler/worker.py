from __future__ import annotations

import asyncio
import json
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from opentelemetry import trace
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import fmt_kv, get_logger
from app.db.database import SessionLocal
from app.models import models
from app.services.agent.scheduled_runner import ScheduledAgentRunner
from app.services.function.runtime import FunctionRuntimeService
from app.services.lifecycle import ScheduleLifecycleService
from app.services.scheduler.result import ScheduleRuntimeResult
from app.services.scheduler.runtime import ScheduleTargetRuntimeService

tracer = trace.get_tracer("app.services.scheduler.worker")

logger = get_logger("scheduler.worker")


class SchedulerWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Any = SessionLocal,
        runtime_service: FunctionRuntimeService | None = None,
        agent_runtime_service: ScheduledAgentRunner | None = None,
        lifecycle_service: ScheduleLifecycleService | None = None,
        refresh_interval_seconds: int = 15,
        job_coalesce: bool = True,
        job_misfire_grace_seconds: int | None = 60,
        job_max_instances: int = 1,
    ):
        self._session_factory = session_factory
        self._target_runtime = ScheduleTargetRuntimeService(
            session_factory=session_factory,
            function_runtime_service=runtime_service,
            agent_runtime_service=agent_runtime_service,
        )
        self._lifecycle_service = lifecycle_service or ScheduleLifecycleService()
        self._scheduler = AsyncIOScheduler()
        self._started = False
        self._shutting_down = False
        self._refresh_interval_seconds = max(int(refresh_interval_seconds), 5)
        self._job_coalesce = bool(job_coalesce)
        self._job_misfire_grace_seconds = (
            int(job_misfire_grace_seconds)
            if isinstance(job_misfire_grace_seconds, int) and job_misfire_grace_seconds > 0
            else None
        )
        self._job_max_instances = max(int(job_max_instances), 1)
        self._sync_lock = asyncio.Lock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._started:
            return
        logger.info("scheduler_starting")
        self._event_loop = asyncio.get_running_loop()
        await self.refresh_schedules()
        self._scheduler.add_job(
            self._refresh_entrypoint,
            trigger=IntervalTrigger(seconds=self._refresh_interval_seconds),
            id="scheduler:sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()
        self._started = True
        logger.info("scheduler_started %s", fmt_kv(job_count=len(self._scheduler.get_jobs())))

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._started:
            logger.info("scheduler_shutting_down")
            self._scheduler.shutdown(wait=False)
            self._started = False
            self._event_loop = None
            logger.info("scheduler_stopped")
        if self._background_tasks:
            logger.info(
                "scheduler_waiting_background_tasks %s", fmt_kv(count=len(self._background_tasks))
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=120.0,
                )
            except TimeoutError:
                logger.warning("scheduler_background_tasks_timeout")
        self._target_runtime.shutdown()

    async def run_now(self, schedule_id: int, trace_id: str | None = None) -> str:
        return await self._execute_schedule(
            schedule_id=schedule_id,
            trigger_type="manual",
            trace_id=trace_id,
        )

    async def submit_now(self, schedule_id: int, trace_id: str | None = None) -> tuple[str, int]:
        """Create a run record immediately and execute in background.

        Returns (run_id_str, run_pk) so the caller can open the run drawer
        before execution completes.
        """
        run_id_str, run_pk = await self._submit_schedule(
            schedule_id=schedule_id,
            trigger_type="manual",
            trace_id=trace_id,
        )
        task = asyncio.create_task(
            self._execute_schedule_from_run(
                schedule_id=schedule_id,
                run_id_str=run_id_str,
                run_pk=run_pk,
                trigger_type="manual",
                trace_id=trace_id,
            ),
            name=f"scheduler_run_{run_id_str}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return run_id_str, run_pk

    def health(self) -> dict[str, Any]:
        return {
            "running": bool(self._started and self._scheduler.running),
            "shutting_down": self._shutting_down,
            "job_count": len(self._scheduler.get_jobs()),
        }

    async def _refresh_entrypoint(self) -> None:
        await self.refresh_schedules()

    async def refresh_schedules(self) -> None:
        async with self._sync_lock:
            await self._sync_active_schedules()

    async def sync_schedule(self, schedule_id: int) -> None:
        async with self._sync_lock:
            await self._sync_one_schedule(schedule_id)

    def request_refresh(self, timeout_seconds: float = 3.0) -> bool:
        if not self._started or self._event_loop is None:
            return False
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._event_loop:
            self._event_loop.create_task(self.refresh_schedules())
            return True

        future = asyncio.run_coroutine_threadsafe(self.refresh_schedules(), self._event_loop)
        try:
            future.result(timeout=max(timeout_seconds, 0.1))
            return True
        except FutureTimeoutError:
            logger.warning(
                "scheduler_refresh_request_timeout %s", fmt_kv(timeout_seconds=timeout_seconds)
            )
            return False
        except Exception as exc:
            logger.warning("scheduler_refresh_request_failed error=%s", str(exc))
            return False

    def request_sync_schedule(self, schedule_id: int, timeout_seconds: float = 3.0) -> bool:
        if not self._started or self._event_loop is None:
            return False
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._event_loop:
            self._event_loop.create_task(self.sync_schedule(schedule_id))
            return True

        future = asyncio.run_coroutine_threadsafe(self.sync_schedule(schedule_id), self._event_loop)
        try:
            future.result(timeout=max(timeout_seconds, 0.1))
            return True
        except FutureTimeoutError:
            logger.warning(
                "scheduler_sync_schedule_request_timeout %s",
                fmt_kv(schedule_id=schedule_id, timeout_seconds=timeout_seconds),
            )
            return False
        except Exception as exc:
            logger.warning(
                "scheduler_sync_schedule_request_failed %s error=%s",
                fmt_kv(schedule_id=schedule_id),
                str(exc),
            )
            return False

    async def _sync_active_schedules(self) -> None:
        db = self._session_factory()
        try:
            schedules = db.query(models.Schedule).filter(models.Schedule.status == "active").all()
            active_ids: set[str] = set()
            for schedule in schedules:
                try:
                    job_id = f"schedule:{schedule.id}"
                    active_ids.add(job_id)
                    self._upsert_schedule_job(schedule)
                except Exception as exc:
                    logger.warning(
                        "scheduler_job_sync_skipped %s error=%s",
                        fmt_kv(schedule_id=schedule.id),
                        str(exc),
                    )
            for job in self._scheduler.get_jobs():
                if job.id == "scheduler:sync":
                    continue
                if job.id not in active_ids:
                    self._scheduler.remove_job(job.id)
            logger.info("scheduler_jobs_loaded %s", fmt_kv(active_count=len(schedules)))
        finally:
            db.close()

    async def _sync_one_schedule(self, schedule_id: int) -> None:
        db = self._session_factory()
        try:
            schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
            job_id = f"schedule:{schedule_id}"
            if schedule is None or schedule.status != "active":
                existing_job = self._scheduler.get_job(job_id)
                if existing_job is not None:
                    self._scheduler.remove_job(job_id)
                return
            self._upsert_schedule_job(schedule)
        except Exception as exc:
            logger.warning(
                "scheduler_single_job_sync_failed %s error=%s",
                fmt_kv(schedule_id=schedule_id),
                str(exc),
            )
        finally:
            db.close()

    def _upsert_schedule_job(self, schedule: models.Schedule) -> None:
        trigger = self._build_trigger(schedule)
        job_id = f"schedule:{schedule.id}"
        existing_job = self._scheduler.get_job(job_id)
        if existing_job is not None and self._is_same_trigger(existing_job.trigger, trigger):
            return
        if existing_job is not None:
            self._scheduler.remove_job(job_id)
        self._scheduler.add_job(
            self._scheduled_job_entrypoint,
            trigger=trigger,
            args=[schedule.id],
            id=job_id,
            replace_existing=True,
            coalesce=self._job_coalesce,
            misfire_grace_time=self._job_misfire_grace_seconds,
            max_instances=self._job_max_instances,
        )

    def _is_same_trigger(self, left: Any, right: Any) -> bool:
        return type(left) is type(right) and str(left) == str(right)

    async def _scheduled_job_entrypoint(self, schedule_id: int) -> None:
        with tracer.start_as_current_span(
            "scheduler.job",
            attributes={
                "scheduler.schedule_id": schedule_id,
                "scheduler.trigger_type": "scheduled",
            },
        ):
            await self._execute_schedule(
                schedule_id=schedule_id,
                trigger_type="scheduled",
                trace_id=str(uuid.uuid4()),
            )

    async def _submit_schedule(
        self, *, schedule_id: int, trigger_type: str, trace_id: str | None = None
    ) -> tuple[str, int]:
        """Phase 1+2: load schedule and create run record. Returns (run_id_str, run_pk)."""
        correlation_id = str(uuid.uuid4())
        trace_id = trace_id or correlation_id

        db = self._session_factory()
        try:
            schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
            if schedule is None:
                raise ValueError(f"Schedule {schedule_id} not found")
        finally:
            db.close()

        run_id_str = str(uuid.uuid4())
        db = self._session_factory()
        try:
            run = models.ScheduleRun(
                schedule_id=schedule_id,
                run_id=run_id_str,
                status="running",
                trigger_type=trigger_type,
                attempt=1,
                retry_count=0,
                max_retries=max(schedule.max_retries, 0),
                correlation_id=correlation_id,
                target_type=schedule.target_type,
                started_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            run_pk = run.id
        finally:
            db.close()

        return run_id_str, run_pk

    async def _execute_schedule_from_run(
        self,
        *,
        schedule_id: int,
        run_id_str: str,
        run_pk: int,
        trigger_type: str,
        trace_id: str | None = None,
    ) -> None:
        """Phase 3+4: invoke runtime and write result for an already-created run record."""
        db = self._session_factory()
        try:
            schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
            if schedule is None:
                return
            schedule_snapshot = schedule
            schedule_id_val = schedule.id
            schedule_type = schedule.schedule_type
            schedule_status = schedule.status
            cron_expression = schedule.cron_expression
            interval_seconds = schedule.interval_seconds
        finally:
            db.close()

        cancelled_error: asyncio.CancelledError | None = None
        try:
            runtime_result = await self._invoke_runtime(
                schedule_snapshot, trigger_type, trace_id=trace_id
            )
        except asyncio.CancelledError as exc:
            cancelled_error = exc
            runtime_result = ScheduleRuntimeResult(
                run_id="",
                status="failed",
                output=None,
                output_summary=None,
                error_class="cancelled",
                error_message="Schedule invocation cancelled",
                duration_ms=0,
            )
        except Exception as exc:
            logger.exception(
                "schedule_execute_runtime_error %s",
                fmt_kv(trace_id=trace_id, schedule_id=schedule_id_val, run_id=run_id_str),
            )
            runtime_result = ScheduleRuntimeResult(
                run_id="",
                status="failed",
                output=None,
                output_summary=None,
                error_class="runtime",
                error_message=str(exc),
                duration_ms=0,
            )
        finished_at = datetime.utcnow()

        db = self._session_factory()
        try:
            run = db.query(models.ScheduleRun).filter(models.ScheduleRun.id == run_pk).first()
            if run is not None:
                run.runtime_run_id = runtime_result.run_id or None
                run.runtime_status = runtime_result.status
                run.output_summary = runtime_result.output_summary
                run.output_payload = self._serialize_output(runtime_result.output)
                run.conversation_id = runtime_result.conversation_id
                run.finished_at = finished_at

            if runtime_result.status == "success":
                if run is not None:
                    run.status = "success"
                db.commit()
                schedule_row = (
                    db.query(models.Schedule).filter(models.Schedule.id == schedule_id_val).first()
                )
                if schedule_row is not None:
                    schedule_row.last_run_at = finished_at
                    if schedule_status == "active":
                        schedule_row.next_run_at = self._lifecycle_service.calculate_next_run_at(
                            schedule_type=schedule_type,
                            cron_expression=cron_expression,
                            interval_seconds=interval_seconds,
                            now=finished_at,
                        )
                db.commit()
            else:
                if run is not None:
                    run.error_summary = runtime_result.error_message
                    run.status = "failed"
                db.commit()
                if cancelled_error is not None:
                    raise cancelled_error
        finally:
            db.close()

    async def _execute_schedule(
        self, *, schedule_id: int, trigger_type: str, trace_id: str | None = None
    ) -> str:
        correlation_id = str(uuid.uuid4())
        trace_id = trace_id or correlation_id
        logger.info(
            "schedule_execute_start %s",
            fmt_kv(trace_id=trace_id, schedule_id=schedule_id, trigger_type=trigger_type),
        )
        with tracer.start_as_current_span(
            "scheduler.execute",
            attributes={
                "scheduler.schedule_id": schedule_id,
                "scheduler.trigger_type": trigger_type,
                "scheduler.trace_id": trace_id,
            },
        ) as span:
            # Phase 1: load schedule (short-lived session).
            db = self._session_factory()
            try:
                schedule = (
                    db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
                )
                if schedule is None:
                    raise ValueError(f"Schedule {schedule_id} not found")
                if trigger_type == "scheduled" and schedule.status != "active":
                    logger.info(
                        "schedule_execute_skipped %s",
                        fmt_kv(
                            schedule_id=schedule_id,
                            trigger_type=trigger_type,
                            status=schedule.status,
                        ),
                    )
                    return ""
                # Snapshot fields needed after session close.
                schedule_id_val = schedule.id
                schedule_type = schedule.schedule_type
                schedule_status = schedule.status
                cron_expression = schedule.cron_expression
                interval_seconds = schedule.interval_seconds
                max_retries = max(schedule.max_retries, 0)
                retry_backoff_seconds = schedule.retry_backoff_seconds
                schedule_snapshot = schedule  # kept in memory for runtime; not used for DB writes
            finally:
                db.close()

            span.set_attribute("scheduler.max_retries", max_retries)
            for attempt_index in range(max_retries + 1):
                span.set_attribute("scheduler.attempt", attempt_index + 1)

                # Phase 2: create run record (short-lived session).
                run_id_str = str(uuid.uuid4())
                db = self._session_factory()
                try:
                    run = models.ScheduleRun(
                        schedule_id=schedule_id_val,
                        run_id=run_id_str,
                        status="running",
                        trigger_type=trigger_type,
                        attempt=attempt_index + 1,
                        retry_count=attempt_index,
                        max_retries=max_retries,
                        correlation_id=correlation_id,
                        target_type=schedule_snapshot.target_type,
                        started_at=datetime.utcnow(),
                        created_at=datetime.utcnow(),
                    )
                    db.add(run)
                    db.commit()
                    db.refresh(run)
                    run_pk = run.id
                finally:
                    db.close()

                # Phase 3: invoke runtime — no DB connection held.
                cancelled_error: asyncio.CancelledError | None = None
                try:
                    runtime_result = await self._invoke_runtime(
                        schedule_snapshot, trigger_type, trace_id=trace_id
                    )
                except asyncio.CancelledError as exc:
                    cancelled_error = exc
                    runtime_result = ScheduleRuntimeResult(
                        run_id="",
                        status="failed",
                        output=None,
                        output_summary=None,
                        error_class="cancelled",
                        error_message="Schedule invocation cancelled",
                        duration_ms=0,
                    )
                except Exception as exc:
                    logger.exception(
                        "schedule_execute_runtime_error %s",
                        fmt_kv(
                            trace_id=trace_id,
                            schedule_id=schedule_id_val,
                            run_id=run_id_str,
                            attempt=attempt_index + 1,
                        ),
                    )
                    runtime_result = ScheduleRuntimeResult(
                        run_id="",
                        status="failed",
                        output=None,
                        output_summary=None,
                        error_class="runtime",
                        error_message=str(exc),
                        duration_ms=0,
                    )
                finished_at = datetime.utcnow()

                # Phase 4: write result (short-lived session).
                db = self._session_factory()
                try:
                    run = (
                        db.query(models.ScheduleRun).filter(models.ScheduleRun.id == run_pk).first()
                    )
                    if run is not None:
                        run.runtime_run_id = runtime_result.run_id or None
                        run.runtime_status = runtime_result.status
                        run.output_summary = runtime_result.output_summary
                        run.output_payload = self._serialize_output(runtime_result.output)
                        run.conversation_id = runtime_result.conversation_id
                        run.finished_at = finished_at

                    if runtime_result.status == "success":
                        if run is not None:
                            run.status = "success"
                        db.commit()

                        schedule_row = (
                            db.query(models.Schedule)
                            .filter(models.Schedule.id == schedule_id_val)
                            .first()
                        )
                        if schedule_row is not None:
                            schedule_row.last_run_at = finished_at
                            if schedule_status == "active":
                                schedule_row.next_run_at = (
                                    self._lifecycle_service.calculate_next_run_at(
                                        schedule_type=schedule_type,
                                        cron_expression=cron_expression,
                                        interval_seconds=interval_seconds,
                                        now=finished_at,
                                    )
                                )
                        db.commit()
                        span.set_attribute("scheduler.outcome", "success")
                        logger.info(
                            "schedule_execute_success %s",
                            fmt_kv(
                                trace_id=trace_id,
                                schedule_id=schedule_id_val,
                                run_id=run_id_str,
                                attempt=attempt_index + 1,
                            ),
                        )
                        return run_id_str

                    if run is not None:
                        run.error_summary = runtime_result.error_message
                    if self._is_retryable(runtime_result) and attempt_index < max_retries:
                        if run is not None:
                            run.status = "retrying"
                        db.commit()
                        span.add_event(
                            "scheduler.retry",
                            {
                                "attempt": attempt_index + 1,
                                "error_class": runtime_result.error_class or "",
                            },
                        )
                        logger.warning(
                            "schedule_execute_retry %s",
                            fmt_kv(
                                trace_id=trace_id,
                                schedule_id=schedule_id_val,
                                run_id=run_id_str,
                                attempt=attempt_index + 1,
                                max_retries=max_retries,
                                error_class=runtime_result.error_class,
                            ),
                        )
                    else:
                        if run is not None:
                            run.status = "failed"
                        db.commit()
                        span.set_attribute("scheduler.outcome", "failed")
                        span.set_status(trace.StatusCode.ERROR, runtime_result.error_message or "")
                        logger.error(
                            "schedule_execute_failed %s",
                            fmt_kv(
                                trace_id=trace_id,
                                schedule_id=schedule_id_val,
                                run_id=run_id_str,
                                attempt=attempt_index + 1,
                                error_class=runtime_result.error_class,
                            ),
                        )
                        if cancelled_error is not None:
                            raise cancelled_error
                        return run_id_str
                finally:
                    db.close()

                await asyncio.sleep(max(retry_backoff_seconds, 0))

            return run_id_str

    async def _invoke_runtime(
        self,
        schedule: models.Schedule,
        trigger_type: str,
        trace_id: str | None = None,
    ) -> ScheduleRuntimeResult:
        with tracer.start_as_current_span(
            "scheduler.invoke_runtime",
            attributes={
                "scheduler.target_type": schedule.target_type or "function",
                "scheduler.target_id": str(schedule.target_id or ""),
            },
        ):
            return await self._target_runtime.invoke_schedule(
                schedule,
                trigger_type=trigger_type,
                trace_id=trace_id,
            )

    def _build_trigger(self, schedule: models.Schedule) -> CronTrigger | IntervalTrigger:
        if schedule.schedule_type == "interval":
            if not schedule.interval_seconds:
                raise ValueError("interval schedule requires interval_seconds")
            return IntervalTrigger(seconds=schedule.interval_seconds, timezone=schedule.timezone)

        parts = (schedule.cron_expression or "").split()
        if len(parts) != 5:
            raise ValueError("cron_expression must contain 5 fields")
        minute, hour, day, month, day_of_week = parts
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=schedule.timezone,
        )

    def _is_retryable(self, result: ScheduleRuntimeResult) -> bool:
        return result.error_class in {"runtime", "dependency", "timeout"}

    def _serialize_output(self, output: Any | None) -> Any | None:
        if output is None:
            return None
        try:
            return json.loads(json.dumps(output, default=str, ensure_ascii=False))
        except Exception:
            return {"text": str(output)}
