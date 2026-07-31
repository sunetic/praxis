import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import Base
from app.models import models
from app.services.function.runtime import FunctionRuntimeResult, FunctionRuntimeService
from app.services.scheduler.worker import SchedulerWorker


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "scheduler.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_function_and_schedule(
    db: Session,
    *,
    schedule_status: str = "active",
    schedule_type: str = "interval",
    interval_seconds: int | None = 60,
    cron_expression: str | None = None,
    datasource_id: int | None = None,
    max_retries: int = 0,
    retry_backoff_seconds: int = 0,
) -> models.Schedule:
    fn = models.Function(name="scheduled-fn", status="released")
    db.add(fn)
    db.flush()

    release = models.FunctionRelease(
        function_id=fn.id,
        version=1,
        code_snapshot="result = payload",
    )
    db.add(release)
    db.flush()
    fn.current_release = release
    db.flush()

    schedule = models.Schedule(
        name="job-1",
        status=schedule_status,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        timezone="UTC",
        datasource_id=datasource_id,
        function_id=fn.id,
        function_release_id=release.id,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


class StubRuntime:
    def __init__(self, results: list[FunctionRuntimeResult]):
        self.results = results
        self.calls: list[tuple[int, dict, int | None]] = []

    async def invoke(
        self,
        function: models.Function,
        payload: dict,
        datasource_id: int | None = None,
        timeout_seconds: float = 30.0,
        trace_id: str | None = None,
    ):
        del timeout_seconds, trace_id
        self.calls.append((function.id, payload, datasource_id))
        if self.results:
            return self.results.pop(0)
        return FunctionRuntimeResult(
            run_id="stub",
            status="success",
            output={},
            error_class=None,
            error_code=None,
            error_message=None,
            duration_ms=1,
        )


class RaisingRuntime:
    def __init__(self, error: Exception):
        self.error = error
        self.calls: list[tuple[int, dict, int | None]] = []

    async def invoke(
        self,
        function: models.Function,
        payload: dict,
        datasource_id: int | None = None,
        timeout_seconds: float = 30.0,
        trace_id: str | None = None,
    ):
        del timeout_seconds, trace_id
        self.calls.append((function.id, payload, datasource_id))
        raise self.error


@pytest.mark.anyio
async def test_scheduler_loads_only_active_jobs_on_startup(session_factory):
    db = session_factory()
    active = _create_function_and_schedule(db, schedule_status="active", interval_seconds=300)
    _create_function_and_schedule(db, schedule_status="paused", interval_seconds=300)
    db.close()

    runtime = StubRuntime([])
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.start()
    try:
        job_ids = {job.id for job in worker._scheduler.get_jobs()}
        assert f"schedule:{active.id}" in job_ids
        assert "scheduler:sync" in job_ids
    finally:
        await worker.shutdown()


@pytest.mark.anyio
async def test_scheduler_run_now_reuses_schedule_pipeline(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(db, schedule_status="active", interval_seconds=120)
    db.close()

    runtime = StubRuntime(
        [
            FunctionRuntimeResult(
                run_id="r-success",
                status="success",
                output={"ok": True},
                error_class=None,
                error_code=None,
                error_message=None,
                duration_ms=10,
            )
        ]
    )
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    run_id = await worker.run_now(schedule.id)

    db2 = session_factory()
    run = db2.query(models.ScheduleRun).filter(models.ScheduleRun.run_id == run_id).one()
    assert run.trigger_type == "manual"
    assert run.status == "success"
    assert len(runtime.calls) == 1
    assert runtime.calls[0][1]["trigger_type"] == "manual"
    assert runtime.calls[0][2] is None
    db2.close()


@pytest.mark.anyio
async def test_scheduler_runtime_prefers_schedule_datasource_over_payload(session_factory):
    db = session_factory()
    primary = models.DataSource(
        name="primary-ds",
        host="127.0.0.1",
        port=2881,
        tenant_role="user",
        status="active",
    )
    fallback = models.DataSource(
        name="fallback-ds",
        host="127.0.0.2",
        port=2881,
        tenant_role="user",
        status="active",
    )
    db.add_all([primary, fallback])
    db.flush()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        interval_seconds=120,
        datasource_id=primary.id,
    )
    schedule.input_payload = {"datasource_id": fallback.id}
    db.commit()
    db.close()

    runtime = StubRuntime(
        [
            FunctionRuntimeResult(
                run_id="r-success",
                status="success",
                output={"ok": True},
                error_class=None,
                error_code=None,
                error_message=None,
                duration_ms=10,
            )
        ]
    )
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.run_now(schedule.id)

    assert len(runtime.calls) == 1
    assert runtime.calls[0][2] == primary.id


@pytest.mark.anyio
async def test_scheduler_retry_policy_persists_each_attempt(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        interval_seconds=60,
        max_retries=2,
        retry_backoff_seconds=0,
    )
    db.close()

    runtime = StubRuntime(
        [
            FunctionRuntimeResult(
                run_id="r1",
                status="failed",
                output=None,
                error_class="timeout",
                error_code=None,
                error_message="t",
                duration_ms=1,
            ),
            FunctionRuntimeResult(
                run_id="r2",
                status="failed",
                output=None,
                error_class="runtime",
                error_code=None,
                error_message="r",
                duration_ms=1,
            ),
            FunctionRuntimeResult(
                run_id="r3",
                status="success",
                output={},
                error_class=None,
                error_code=None,
                error_message=None,
                duration_ms=1,
            ),
        ]
    )

    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.run_now(schedule.id)

    db2 = session_factory()
    runs = (
        db2.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == schedule.id)
        .order_by(models.ScheduleRun.attempt.asc())
        .all()
    )
    assert [run.status for run in runs] == ["retrying", "retrying", "success"]
    assert [run.retry_count for run in runs] == [0, 1, 2]
    assert len({run.correlation_id for run in runs}) == 1
    db2.close()


@pytest.mark.anyio
async def test_scheduler_run_now_executes_scheduler_history_cleanup_function(session_factory):
    db = session_factory()
    target_schedule = models.Schedule(
        name="history-target",
        status="active",
        target_type="function",
        target_id=999,
        schedule_type="interval",
        interval_seconds=60,
        timezone="UTC",
    )
    db.add(target_schedule)
    db.flush()
    stale_run = models.ScheduleRun(
        schedule_id=target_schedule.id,
        run_id="stale-run",
        status="success",
        trigger_type="scheduled",
        attempt=1,
        retry_count=0,
        max_retries=0,
        created_at=datetime.utcnow() - timedelta(days=45),
    )
    fresh_run = models.ScheduleRun(
        schedule_id=target_schedule.id,
        run_id="fresh-run",
        status="success",
        trigger_type="scheduled",
        attempt=1,
        retry_count=0,
        max_retries=0,
        created_at=datetime.utcnow() - timedelta(days=5),
    )
    db.add_all([stale_run, fresh_run])

    fn = models.Function(name="cleanup-history-fn", status="released")
    db.add(fn)
    db.flush()
    release = models.FunctionRelease(
        function_id=fn.id,
        version=1,
        code_snapshot=(
            "def main(payload, context):\n"
            "    return scheduler_history.delete(\n"
            "        where={'schedule_id': payload['target_schedule_id']},\n"
            "        policy={'retention_seconds': payload['retention_seconds']},\n"
            "        dry_run=False,\n"
            "    )\n"
        ),
    )
    db.add(release)
    db.flush()
    fn.current_release = release
    db.flush()

    cleanup_schedule = models.Schedule(
        name="history-cleanup",
        status="active",
        target_type="function",
        target_id=fn.id,
        schedule_type="interval",
        interval_seconds=60,
        timezone="UTC",
        function_id=fn.id,
        function_release_id=release.id,
        input_payload={
            "target_schedule_id": target_schedule.id,
            "retention_seconds": 30 * 24 * 3600,
        },
    )
    db.add(cleanup_schedule)
    db.commit()
    db.refresh(cleanup_schedule)
    db.close()

    worker = SchedulerWorker(
        session_factory=session_factory,
        runtime_service=FunctionRuntimeService(session_factory=session_factory),
    )
    try:
        run_id = await worker.run_now(cleanup_schedule.id)
    finally:
        await worker.shutdown()

    db2 = session_factory()
    cleanup_run = db2.query(models.ScheduleRun).filter(models.ScheduleRun.run_id == run_id).one()
    assert cleanup_run.status == "success"
    assert cleanup_run.output_payload["deleted_count"] == 1

    remaining_target_run_ids = {
        row.run_id
        for row in db2.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == target_schedule.id)
        .all()
    }
    assert "stale-run" not in remaining_target_run_ids
    assert "fresh-run" in remaining_target_run_ids
    db2.close()


@pytest.mark.anyio
async def test_scheduler_stops_retry_for_non_retryable_error(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        interval_seconds=60,
        max_retries=3,
        retry_backoff_seconds=0,
    )
    db.close()

    runtime = StubRuntime(
        [
            FunctionRuntimeResult(
                run_id="r1",
                status="failed",
                output=None,
                error_class="validation",
                error_code=None,
                error_message="bad input",
                duration_ms=1,
            ),
            FunctionRuntimeResult(
                run_id="r2",
                status="success",
                output={},
                error_class=None,
                error_code=None,
                error_message=None,
                duration_ms=1,
            ),
        ]
    )
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.run_now(schedule.id)

    db2 = session_factory()
    runs = db2.query(models.ScheduleRun).filter(models.ScheduleRun.schedule_id == schedule.id).all()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    db2.close()


@pytest.mark.anyio
async def test_scheduler_marks_run_failed_when_runtime_raises(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        interval_seconds=60,
    )
    db.close()

    runtime = RaisingRuntime(RuntimeError("boom"))
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    run_id = await worker.run_now(schedule.id)

    db2 = session_factory()
    run = db2.query(models.ScheduleRun).filter(models.ScheduleRun.run_id == run_id).one()
    assert run.status == "failed"
    assert run.runtime_status == "failed"
    assert run.error_summary == "boom"
    assert run.finished_at is not None
    db2.close()


@pytest.mark.anyio
async def test_scheduler_finalizes_run_before_rethrowing_cancellation(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        interval_seconds=60,
    )
    db.close()

    runtime = RaisingRuntime(asyncio.CancelledError())
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_now(schedule.id)

    db2 = session_factory()
    run = db2.query(models.ScheduleRun).filter(models.ScheduleRun.schedule_id == schedule.id).one()
    assert run.status == "failed"
    assert run.runtime_status == "failed"
    assert run.error_summary == "Schedule invocation cancelled"
    assert run.finished_at is not None
    db2.close()


@pytest.mark.anyio
async def test_scheduler_health_and_safe_shutdown(session_factory):
    runtime = StubRuntime([])
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.start()
    health_running = worker.health()
    assert health_running["running"] is True

    await worker.shutdown()
    health_stopped = worker.health()
    assert health_stopped["running"] is False
    assert health_stopped["shutting_down"] is True


@pytest.mark.anyio
async def test_scheduler_sync_recreates_job_when_trigger_changed(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        schedule_type="cron",
        cron_expression="0 1 * * *",
        interval_seconds=None,
    )
    db.close()

    runtime = StubRuntime([])
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.start()
    try:
        db2 = session_factory()
        current = db2.query(models.Schedule).filter(models.Schedule.id == schedule.id).one()
        current.schedule_type = "interval"
        current.interval_seconds = 30
        current.cron_expression = None
        db2.commit()
        db2.close()

        await worker._sync_active_schedules()
        job = worker._scheduler.get_job(f"schedule:{schedule.id}")
        assert job is not None
        assert int(job.trigger.interval.total_seconds()) == 30
    finally:
        await worker.shutdown()


@pytest.mark.anyio
async def test_scheduler_sync_keeps_job_when_trigger_unchanged(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        schedule_type="interval",
        interval_seconds=60,
    )
    db.close()

    runtime = StubRuntime([])
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.start()
    try:
        job_id = f"schedule:{schedule.id}"
        first = worker._scheduler.get_job(job_id)
        assert first is not None
        first_next_run_at = first.next_run_time

        await worker._sync_active_schedules()

        second = worker._scheduler.get_job(job_id)
        assert second is not None
        assert str(second.trigger) == str(first.trigger)
        assert second.next_run_time == first_next_run_at
    finally:
        await worker.shutdown()


@pytest.mark.anyio
async def test_scheduler_sync_one_schedule_handles_pause_and_resume(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        schedule_type="interval",
        interval_seconds=60,
    )
    db.close()

    runtime = StubRuntime([])
    worker = SchedulerWorker(session_factory=session_factory, runtime_service=runtime)
    await worker.start()
    try:
        job_id = f"schedule:{schedule.id}"
        assert worker._scheduler.get_job(job_id) is not None

        db2 = session_factory()
        paused = db2.query(models.Schedule).filter(models.Schedule.id == schedule.id).one()
        paused.status = "paused"
        db2.commit()
        db2.close()

        await worker.sync_schedule(schedule.id)
        assert worker._scheduler.get_job(job_id) is None

        db3 = session_factory()
        resumed = db3.query(models.Schedule).filter(models.Schedule.id == schedule.id).one()
        resumed.status = "active"
        db3.commit()
        db3.close()

        await worker.sync_schedule(schedule.id)
        assert worker._scheduler.get_job(job_id) is not None
    finally:
        await worker.shutdown()


@pytest.mark.anyio
async def test_scheduler_applies_configurable_job_policy(session_factory):
    db = session_factory()
    schedule = _create_function_and_schedule(
        db,
        schedule_status="active",
        schedule_type="interval",
        interval_seconds=45,
    )
    db.close()

    runtime = StubRuntime([])
    worker = SchedulerWorker(
        session_factory=session_factory,
        runtime_service=runtime,
        job_coalesce=False,
        job_misfire_grace_seconds=300,
        job_max_instances=2,
    )
    await worker.start()
    try:
        job = worker._scheduler.get_job(f"schedule:{schedule.id}")
        assert job is not None
        assert job.coalesce is False
        assert job.misfire_grace_time == 300
        assert job.max_instances == 2
    finally:
        await worker.shutdown()
