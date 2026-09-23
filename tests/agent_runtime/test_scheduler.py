import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic_ai import Tool
from test_models import config
from test_runtime import Script, call
from test_service import approve_write

from app.db.base import Base
from app.models.models import Agent, Schedule, ScheduleRun
from app.services.agent.application import RuntimeApplication
from app.services.agent.execution import RegisteredTool
from app.services.agent.models import ModelFactory
from app.services.agent.scheduled_runner import ScheduledAgentRunner
from app.services.scheduler.projection import project_schedule_run
from app.services.scheduler.worker import SchedulerWorker


@pytest.fixture
def fixture_app(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    app = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    with store.sessions.begin() as db:
        agent = Agent(name="scheduled", prompt="Respond to the request.", tools=[], status="active")
        db.add(agent)
        db.flush()
        schedule = Schedule(
            name="native schedule",
            target_type="agent",
            target_id=agent.id,
            status="active",
            schedule_type="interval",
            interval_seconds=300,
            timezone="UTC",
            input_prompt="Explain the limit without claiming success.",
            max_retries=3,
            retry_backoff_seconds=0,
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id
    return app, schedule_id


def read_occurrence(app, occurrence):
    with app.sessions() as db:
        row = db.query(ScheduleRun).filter_by(run_id=occurrence).one()
        return project_schedule_run(db, row)


async def wait_status(app, occurrence, status):
    async with asyncio.timeout(5):
        while True:
            row = read_occurrence(app, occurrence)
            if row["status"] == status:
                return row
            await asyncio.sleep(0.01)


async def test_scheduler_submits_once_and_reads_native_finished_not_success(fixture_app):
    app, schedule_id = fixture_app
    script = Script(["没有执行检查，不能确认结果。"])
    app.service.model_factory = script.factory
    runner = ScheduledAgentRunner(app)
    worker = SchedulerWorker(session_factory=app.sessions, agent_runtime_service=runner)
    occurrence = await worker.run_now(schedule_id)
    accepted = read_occurrence(app, occurrence)
    assert accepted["status"] == "queued"
    assert accepted["finished_at"] is None
    assert accepted["max_retries"] == 0
    await app.start()
    try:
        result = await wait_status(app, occurrence, "finished")
        assert result["output_summary"] == "没有执行检查，不能确认结果。"
        assert result["runtime_status"] == "finished"
        assert result["finished_at"] is not None
        native = app.store.get(result["runtime_run_id"], "local")
        assert native["definition"]["scope"]["entrypoint"] == "scheduler"
        # Simulate a crash after native submit but before copying the native ID.
        with app.sessions.begin() as db:
            db.query(ScheduleRun).filter_by(run_id=occurrence).update({"runtime_run_id": None})
        assert read_occurrence(app, occurrence)["runtime_run_id"] == native["id"]
        duplicate = await runner.invoke(
            agent_id=native["definition"]["scope"]["agent_id"],
            prompt=native["prompt"],
            schedule_run_id=occurrence,
        )
        assert duplicate.run_id == native["id"]
        assert len(script.requests) == 1
        assert len(app.store.list_runs(native["conversation_id"], "local")) == 1
        from app.services.agent.store import RunConflictError

        with pytest.raises(RunConflictError, match="other input"):
            await runner.invoke(
                agent_id=native["definition"]["scope"]["agent_id"],
                prompt="different action",
                schedule_run_id=occurrence,
            )
    finally:
        await worker.shutdown()
        await app.close()


async def test_scheduler_approval_is_same_native_call_and_projection_survives_restart(fixture_app):
    app, schedule_id = fixture_app
    writes = []

    async def write() -> str:
        writes.append("effect")
        return "saved"

    entry = RegisteredTool(
        tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
    )
    app.tools["write"] = app.service.tools["write"] = entry
    with app.sessions.begin() as db:
        db.query(Agent).update({"tools": ["write"]})
    script = Script([call("write", call_id="scheduled-original")], ["已保存。"])
    app.service.model_factory = script.factory
    worker = SchedulerWorker(
        session_factory=app.sessions, agent_runtime_service=ScheduledAgentRunner(app)
    )
    await app.start()
    try:
        occurrence = await worker.run_now(schedule_id)
        paused = await wait_status(app, occurrence, "waiting_approval")
        assert paused["finished_at"] is None
        assert writes == []
        run_id = paused["runtime_run_id"]
        native = app.store.get(run_id, "local")
        approval = native["approvals"][0]
        await app.service.close()
        # A new owner reads durable state; the scheduler does not re-submit.
        restarted = RuntimeApplication(sessions=app.sessions, models=ModelFactory(lambda: config()))
        restarted.tools["write"] = restarted.service.tools["write"] = entry
        restarted.service.model_factory = script.factory
        await restarted.start()
        try:
            restarted.service.approve(
                run_id, "local", approval["call_id"], approval["fingerprint"], True
            )
            result = await wait_status(restarted, occurrence, "finished")
            assert result["runtime_run_id"] == run_id
            assert result["output_summary"] == "已保存。"
            assert writes == ["effect"]
            assert (
                restarted.store.get(run_id, "local")["tool_calls"][0]["call_id"]
                == "scheduled-original"
            )
        finally:
            await restarted.close()
    finally:
        await worker.shutdown()
        await app.close()


async def test_scheduler_does_not_retry_native_infrastructure_failure(fixture_app):
    app, schedule_id = fixture_app
    invocations = []

    async def unavailable(_):
        invocations.append(1)
        raise RuntimeError("provider unavailable")

    app.service.model_factory = unavailable
    worker = SchedulerWorker(
        session_factory=app.sessions, agent_runtime_service=ScheduledAgentRunner(app)
    )
    await app.start()
    try:
        occurrence = await worker.run_now(schedule_id)
        failed = await wait_status(app, occurrence, "failed")
        assert failed["error_summary"] == "runtime_error"
        assert invocations == [1]
        with app.sessions() as db:
            assert db.query(ScheduleRun).count() == 1
    finally:
        await worker.shutdown()
        await app.close()


async def test_scheduler_submission_failure_keeps_reservation_and_has_no_model_retry(fixture_app):
    app, schedule_id = fixture_app
    with app.sessions.begin() as db:
        db.query(Agent).update({"tools": ["not_available"]})
    worker = SchedulerWorker(
        session_factory=app.sessions, agent_runtime_service=ScheduledAgentRunner(app)
    )
    try:
        occurrence = await worker.run_now(schedule_id)
        failed = read_occurrence(app, occurrence)
        assert failed["status"] == "failed"
        assert failed["conversation_id"]
        assert failed["runtime_run_id"] is None
        with app.sessions() as db:
            assert db.query(ScheduleRun).count() == 1
    finally:
        await worker.shutdown()
        await app.close()


async def test_schedule_http_reports_native_status_and_rejects_unsafe_retry_config(
    fixture_app, monkeypatch
):
    from app.api import schedules
    from app.db.database import get_db

    app, schedule_id = fixture_app
    script = Script(["需要更多信息；未执行任何修改。"])
    app.service.model_factory = script.factory
    worker = SchedulerWorker(
        session_factory=app.sessions, agent_runtime_service=ScheduledAgentRunner(app)
    )
    api = FastAPI()
    api.include_router(schedules.router, prefix="/api/v1")

    def session():
        with app.sessions() as db:
            yield db

    api.dependency_overrides[get_db] = session
    monkeypatch.setattr(schedules, "get_scheduler_worker", lambda: worker)
    await app.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://test"
        ) as client:
            with app.sessions() as db:
                agent_id = db.get(Schedule, schedule_id).target_id
            payload = dict(
                name="HTTP schedule",
                target_type="agent",
                target_id=agent_id,
                schedule_type="interval",
                interval_seconds=300,
                status="paused",
                input_prompt="仅说明限制，不实施修改。",
                max_retries=0,
            )
            for bad in ({"max_retries": 2}, {"input_prompt": ""}):
                response = await client.post("/api/v1/schedules", json={**payload, **bad})
                assert response.status_code == 400
            created = await client.post("/api/v1/schedules", json=payload)
            assert created.status_code == 201
            created_id = created.json()["id"]
            accepted = await client.post(f"/api/v1/schedules/{created_id}/run-now")
            assert accepted.status_code == 200
            await wait_status(app, accepted.json()["run_id"], "finished")
            records = (await client.get(f"/api/v1/schedules/{created_id}/runs")).json()
            assert records[0]["status"] == "finished"
            assert records[0]["output_summary"] == "需要更多信息；未执行任何修改。"
            repair = await client.post(
                f"/api/v1/schedules/{created_id}/runs/{records[0]['id']}/repair"
            )
            assert repair.status_code == 409
            assert (await client.post("/api/v1/schedules/999999/run-now")).status_code == 404
    finally:
        await worker.shutdown()
        await app.close()
