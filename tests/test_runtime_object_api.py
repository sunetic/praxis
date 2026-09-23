from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import functions as functions_api
from app.api import schedules as schedules_api
from app.db.database import Base
from app.models import models


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "runtime-object-api.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def seed_released_function(db, name):
    """Independent Scheduler fixture, not evidence of publication or validation."""
    function = models.Function(name=name, slug=name, status="released")
    db.add(function)
    db.flush()
    release = models.FunctionRelease(
        function_id=function.id,
        version=1,
        code_snapshot="def main(payload, context):\n    return {'ok': True}\n",
        dependency_manifest={},
    )
    db.add(release)
    db.flush()
    function.current_release_id = release.id
    db.commit()
    return {"id": function.id}


def test_function_name_supports_unicode_and_slug_is_system_managed(session_factory: Any):
    db = session_factory()
    try:
        created = functions_api.create_function(
            functions_api.FunctionCreate(name="  慢 SQL 分析  "), db=db
        )
        assert created["name"] == "慢 SQL 分析"
        assert isinstance(created["slug"], str)
        assert created["slug"]

        queried = functions_api.get_function_by_slug(created["slug"], db=db)
        assert queried["id"] == created["id"]

        duplicated = functions_api.create_function(
            functions_api.FunctionCreate(name="慢 SQL 分析"), db=db
        )
        assert duplicated["name"] == "慢 SQL 分析"
        assert duplicated["slug"] != created["slug"]

        renamed = functions_api.update_function(
            created["id"], functions_api.FunctionUpdate(name="新的函数名称"), db=db
        )
        assert renamed["name"] == "新的函数名称"
        assert renamed["slug"] == created["slug"]

        with pytest.raises(ValidationError, match="Extra inputs"):
            functions_api.FunctionUpdate(slug="manual-slug")
    finally:
        db.close()


@pytest.mark.anyio
async def test_schedule_api_update_pause_resume_and_empty_history(session_factory: Any):
    db = session_factory()
    try:
        datasource = models.DataSource(
            name="schedule-ds",
            host="127.0.0.1",
            port=2881,
            tenant_role="user",
            status="active",
        )
        db.add(datasource)
        db.flush()
        fn = seed_released_function(db, "scheduled-report")
        fn_id = fn["id"]

        schedule = schedules_api.create_schedule(
            {
                "name": "interval-job",
                "function_id": fn_id,
                "schedule_type": "interval",
                "interval_seconds": 60,
                "datasource_id": datasource.id,
                "max_retries": 1,
                "retry_backoff_seconds": 0,
            },
            db=db,
        )
        schedule_id = schedule["id"]
        assert schedule["timezone"] == "Asia/Shanghai"
        assert schedule["datasource_id"] == datasource.id

        updated = schedules_api.update_schedule(
            schedule_id, {"interval_seconds": 300, "max_retries": 3}, db=db
        )
        assert updated["interval_seconds"] == 300 and updated["max_retries"] == 3

        paused = schedules_api.pause_schedule(schedule_id, db=db)
        assert paused["status"] == "paused"

        resumed = schedules_api.resume_schedule(schedule_id, db=db)
        assert resumed["status"] == "active"

        disabled = schedules_api.disable_schedule(schedule_id, db=db)
        assert disabled["status"] == "paused"

        enabled = schedules_api.enable_schedule(schedule_id, db=db)
        assert enabled["status"] == "active"

        # Actual manual submission is covered by the native Scheduler worker suite.
        runs = schedules_api.list_schedule_runs(schedule_id, db=db)
        assert runs == []
    finally:
        db.close()


def test_schedule_api_rejects_invalid_timezone(session_factory: Any):
    db = session_factory()
    try:
        fn = seed_released_function(db, "invalid-timezone-fn")
        with pytest.raises(HTTPException) as exc:
            schedules_api.create_schedule(
                {
                    "name": "bad-timezone",
                    "function_id": fn["id"],
                    "schedule_type": "interval",
                    "interval_seconds": 60,
                    "timezone": "Mars/Base",
                },
                db=db,
            )
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_schedule_api_create_triggers_runtime_refresh(
    session_factory: Any, monkeypatch: pytest.MonkeyPatch
):
    class _FakeWorker:
        def __init__(self) -> None:
            self.full_refresh_calls = 0
            self.single_sync_calls: list[int] = []

        def request_refresh(self, timeout_seconds: float = 3.0) -> bool:
            del timeout_seconds
            self.full_refresh_calls += 1
            return True

        def request_sync_schedule(self, schedule_id: int, timeout_seconds: float = 3.0) -> bool:
            del timeout_seconds
            self.single_sync_calls.append(schedule_id)
            return True

    fake_worker = _FakeWorker()
    monkeypatch.setattr(schedules_api, "get_scheduler_worker", lambda: fake_worker)

    db = session_factory()
    try:
        fn = seed_released_function(db, "refresh-on-create")
        created = schedules_api.create_schedule(
            {
                "name": "refresh-create-schedule",
                "function_id": fn["id"],
                "schedule_type": "interval",
                "interval_seconds": 60,
            },
            db=db,
        )
        assert created["id"] > 0
        assert fake_worker.full_refresh_calls == 0
        assert fake_worker.single_sync_calls == [created["id"]]
    finally:
        db.close()


def test_schedule_api_repair_run_finalizes_stale_running_record(session_factory: Any):
    db = session_factory()
    try:
        fn = seed_released_function(db, "repair-run-fn")
        schedule = schedules_api.create_schedule(
            {
                "name": "repair-run-schedule",
                "function_id": fn["id"],
                "schedule_type": "interval",
                "interval_seconds": 60,
            },
            db=db,
        )
        schedule_id = int(schedule["id"])
        run = models.ScheduleRun(
            schedule_id=schedule_id,
            run_id="run-stuck-1",
            status="running",
            runtime_status=None,
            trigger_type="scheduled",
            attempt=1,
            retry_count=0,
            max_retries=0,
            started_at=datetime.utcnow() - timedelta(minutes=10),
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        repaired = schedules_api.repair_schedule_run(schedule_id, run.id, db=db)
        assert repaired["status"] == "failed"
        assert repaired["runtime_status"] == "failed"
        assert repaired["error_summary"] == "Manually repaired stale running schedule run"
        assert repaired["finished_at"] is not None
    finally:
        db.close()


def test_schedule_api_runs_support_offset_and_total_header(session_factory: Any):
    db = session_factory()
    try:
        fn = seed_released_function(db, "runs-pagination-fn")
        schedule = schedules_api.create_schedule(
            {
                "name": "runs-pagination",
                "function_id": fn["id"],
                "schedule_type": "interval",
                "interval_seconds": 60,
            },
            db=db,
        )
        schedule_id = int(schedule["id"])

        base = datetime.utcnow()
        db.add_all(
            [
                models.ScheduleRun(
                    schedule_id=schedule_id,
                    run_id="run-1",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base,
                ),
                models.ScheduleRun(
                    schedule_id=schedule_id,
                    run_id="run-2",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base + timedelta(seconds=1),
                ),
                models.ScheduleRun(
                    schedule_id=schedule_id,
                    run_id="run-3",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()

        response = Response()
        runs = schedules_api.list_schedule_runs(
            schedule_id, limit=2, offset=1, response=response, db=db
        )
        assert [item["run_id"] for item in runs] == ["run-2", "run-1"]
        assert response.headers.get("X-Total-Count") == "3"
        assert response.headers.get("X-Limit") == "2"
        assert response.headers.get("X-Offset") == "1"
    finally:
        db.close()


def test_schedule_api_list_all_runs_supports_global_and_schedule_filter(session_factory: Any):
    db = session_factory()
    try:
        fn = seed_released_function(db, "runs-global-fn")
        schedule_a = schedules_api.create_schedule(
            {
                "name": "runs-global-a",
                "function_id": fn["id"],
                "schedule_type": "interval",
                "interval_seconds": 60,
            },
            db=db,
        )
        schedule_b = schedules_api.create_schedule(
            {
                "name": "runs-global-b",
                "function_id": fn["id"],
                "schedule_type": "interval",
                "interval_seconds": 120,
            },
            db=db,
        )
        schedule_a_id = int(schedule_a["id"])
        schedule_b_id = int(schedule_b["id"])

        base = datetime.utcnow()
        db.add_all(
            [
                models.ScheduleRun(
                    schedule_id=schedule_a_id,
                    run_id="run-a-1",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base + timedelta(seconds=1),
                ),
                models.ScheduleRun(
                    schedule_id=schedule_b_id,
                    run_id="run-b-1",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base + timedelta(seconds=2),
                ),
                models.ScheduleRun(
                    schedule_id=schedule_a_id,
                    run_id="run-a-2",
                    status="success",
                    trigger_type="scheduled",
                    attempt=1,
                    retry_count=0,
                    max_retries=0,
                    created_at=base + timedelta(seconds=3),
                ),
            ]
        )
        db.commit()

        response_all = Response()
        all_runs = schedules_api.list_all_schedule_runs(
            limit=10, offset=0, response=response_all, db=db
        )
        assert [item["run_id"] for item in all_runs][:3] == ["run-a-2", "run-b-1", "run-a-1"]
        assert response_all.headers.get("X-Total-Count") == "3"

        response_filtered = Response()
        filtered_runs = schedules_api.list_all_schedule_runs(
            limit=10,
            offset=0,
            schedule_id=schedule_a_id,
            response=response_filtered,
            db=db,
        )
        assert [item["run_id"] for item in filtered_runs] == ["run-a-2", "run-a-1"]
        assert response_filtered.headers.get("X-Total-Count") == "2"
    finally:
        db.close()
