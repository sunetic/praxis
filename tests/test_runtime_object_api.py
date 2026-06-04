from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import functions as functions_api
from app.api import schedules as schedules_api
from app.db.database import Base
from app.models import models
from app.services.function.builder import FunctionBuilderService
from app.services.scheduler.builder import SchedulerBuilderService


class _FakeLLM:
    async def chat(self, messages: Any, tools: Any = None, stream: bool = True, **kwargs: Any):
        content = """
        {
          "intent_summary": "居中、交互按钮页面",
          "plan": {"goal": "构建按钮页面", "todos": ["设置居中", "添加按钮"]},
          "config": {"title": "交互按钮页", "description": "用于测试构建链路"},
          "source": {"language": "tsx", "code": "export default function Page(){return <main><button>立即操作</button></main>}"},
          "runtime": {"framework": "html", "preview_html": "<!doctype html><html><body><main><button>立即操作</button></main></body></html>"}
        }
        """
        yield {"choices": [{"message": {"content": content}}]}


class _FakeFunctionLLM:
    async def chat(self, messages: Any, tools: Any = None, stream: bool = True, **kwargs: Any):
        user_content = str((messages[-1] or {}).get("content") or "")
        if "第一个租户" in user_content:
            content = """
            {
              "intent_summary": "按默认策略选择租户并查询数据库列表",
              "plan": {"goal": "查询业务租户数据库列表", "todos": ["选择租户", "查询数据库", "返回结果"]},
              "uses_db": true,
              "sql": "SHOW DATABASES",
              "output_fields": [
                {"name": "tenant_name", "kind": "constant", "value": "default-business"},
                {"name": "rows", "kind": "payload_len", "key": "rows"}
              ],
              "clarification_questions": ["请确认“第一个”是否按名称升序选择？"],
              "default_strategy": ["若未指定排序，默认按名称升序选择第一个对象。"]
            }
            """
        else:
            content = """
            {
              "intent_summary": "带运行标识的函数",
              "plan": {"goal": "增加运行标识字段", "todos": ["保留原输出", "增加 run_id"]},
              "uses_db": false,
              "sql": "",
              "output_fields": [
                {"name": "ok", "kind": "constant", "value": true},
                {"name": "run_id", "kind": "context", "path": "trace_id"},
                {"name": "rows", "kind": "payload_len", "key": "rows"}
              ],
              "clarification_questions": [],
              "default_strategy": []
            }
            """
        yield {"choices": [{"message": {"content": content}}]}


class _FakeSchedulerLLM:
    async def chat(self, messages: Any, tools: Any = None, stream: bool = True, **kwargs: Any):
        content = """
        {
          "patch": {
            "schedule_type": "interval",
            "interval_seconds": 300,
            "max_retries": 3,
            "timezone": "Asia/Shanghai"
          },
          "summary": "调度更新完成"
        }
        """
        yield {"choices": [{"message": {"content": content}}]}


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


@pytest.fixture(autouse=True)
def patch_function_builder_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        functions_api,
        "FunctionBuilderService",
        lambda: FunctionBuilderService(llm_client=_FakeFunctionLLM()),
    )


@pytest.fixture(autouse=True)
def patch_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path / "workspace"))


@pytest.fixture(autouse=True)
def patch_scheduler_builder_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        schedules_api,
        "SchedulerBuilderService",
        lambda: SchedulerBuilderService(llm_client=_FakeSchedulerLLM()),
    )


def test_function_name_supports_unicode_and_slug_is_system_managed(session_factory: Any):
    db = session_factory()
    try:
        created = functions_api.create_function({"name": "  慢 SQL 分析  "}, db=db)
        assert created["name"] == "慢 SQL 分析"
        assert isinstance(created["slug"], str)
        assert created["slug"]

        queried = functions_api.get_function_by_slug(created["slug"], db=db)
        assert queried["id"] == created["id"]

        legacy_alias = functions_api.get_function_by_name(created["slug"], db=db)
        assert legacy_alias["id"] == created["id"]

        duplicated = functions_api.create_function({"name": "慢 SQL 分析"}, db=db)
        assert duplicated["name"] == "慢 SQL 分析"
        assert duplicated["slug"] != created["slug"]

        renamed = functions_api.update_function(created["id"], {"name": "新的函数名称"}, db=db)
        assert renamed["name"] == "新的函数名称"
        assert renamed["slug"] == created["slug"]

        with pytest.raises(HTTPException) as invalid:
            functions_api.update_function(created["id"], {"slug": "manual-slug"}, db=db)
        assert invalid.value.status_code == 400
    finally:
        db.close()


def test_editing_released_function_creates_new_draft_iteration(session_factory: Any):
    db = session_factory()
    try:
        created = functions_api.create_function(
            {
                "name": "调度清理",
                "description": "v1",
                "draft_code": "result = {'ok': True}",
            },
            db=db,
        )
        function_id = created["id"]

        released = functions_api.release_function(function_id, {}, db=db)
        current_release_id = released["function"]["current_release_id"]
        assert released["function"]["status"] == "released"
        assert isinstance(current_release_id, int)

        updated = functions_api.update_function(
            function_id,
            {"name": "调度清理 v2", "description": "v2"},
            db=db,
        )
        assert updated["status"] == "draft"
        assert updated["current_release_id"] == current_release_id

        refreshed = functions_api.get_function(function_id, db=db)
        assert refreshed["status"] == "draft"
        assert refreshed["current_release_id"] == current_release_id
    finally:
        db.close()


@pytest.mark.anyio
async def test_function_api_release_invoke_and_history(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {
                "name": "slow-sql-runtime",
                "description": "slow sql analysis",
                "draft_code": "result = {'rows': len(payload.get('rows', []))}",
            },
            db=db,
        )
        fn_id = fn["id"]

        built = functions_api.build_function(fn_id, {"prompt": "增加结果字段 run_id"}, db=db)
        assert "run_id" in built["function"]["draft_code"]
        assert built["build_run"]["status"] == "done"
        assert built["function"]["description"] == "slow sql analysis"
        assert built["function"]["description"] != built["build_summary"]
        phases = [event["phase"] for event in built["build_run"]["events"]]
        assert phases == ["apply"]

        strategy = functions_api.decide_function_strategy(
            fn_id,
            {"requirement": "slow sql analysis", "reuse_threshold": 0.95, "extend_threshold": 0.5},
            db=db,
        )
        assert strategy["strategy"] in {"reuse", "extend", "create"}

        verification = functions_api.verify_function(fn_id, {}, db=db)
        assert verification["verification"]["passed"] is True

        draft_invoke = await functions_api.invoke_function(
            fn_id,
            {
                "payload": {"rows": [1, 2]},
                "runtime_path": "draft",
                "execution_mode": "plan",
                "write_mode": "readonly",
            },
            db=db,
        )
        assert draft_invoke["status"] == "success"
        assert draft_invoke["output"]["rows"] == 2
        assert draft_invoke["runtime_path"] == "draft"

        released = functions_api.release_function(fn_id, {}, db=db)
        assert released["function"]["status"] == "released"
        assert released["release"]["release_metadata"]["verification"]["passed"] is True
        assert released["function"]["source_path"]
        assert released["function"]["current_commit_sha"]
        assert released["function"]["release_commit_sha"]
        assert Path(str(released["function"]["source_path"])).exists()

        invoke = await functions_api.invoke_function(fn_id, {"payload": {"rows": [1, 2, 3]}}, db=db)
        assert invoke["status"] == "success"
        assert invoke["output"]["rows"] == 3

        releases = functions_api.list_function_releases(fn_id, db=db)
        runs = functions_api.list_function_runs(fn_id, db=db)
        assert len(releases) == 1
        assert len(runs) >= 1
    finally:
        db.close()


@pytest.mark.anyio
async def test_function_api_write_apply_requires_confirm(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {"name": "write-guard", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        fn_id = fn["id"]
        functions_api.release_function(fn_id, {}, db=db)
        with pytest.raises(Exception) as err:
            await functions_api.invoke_function(
                fn_id,
                {
                    "payload": {},
                    "write_mode": "write",
                    "execution_mode": "apply",
                    "confirm_apply": False,
                },
                db=db,
            )
        assert "confirm_apply" in str(err.value)
    finally:
        db.close()


@pytest.mark.anyio
async def test_function_api_scheduler_history_delete_uses_plan_and_apply_semantics(session_factory: Any):
    db = session_factory()
    try:
        schedule = models.Schedule(
            name="history-retention-target",
            status="active",
            target_type="function",
            target_id=1,
            schedule_type="interval",
            interval_seconds=60,
            timezone="UTC",
        )
        db.add(schedule)
        db.flush()
        old_run = models.ScheduleRun(
            schedule_id=schedule.id,
            run_id="history-old",
            status="success",
            trigger_type="scheduled",
            attempt=1,
            retry_count=0,
            max_retries=0,
            created_at=datetime.utcnow() - timedelta(days=40),
        )
        fresh_run = models.ScheduleRun(
            schedule_id=schedule.id,
            run_id="history-fresh",
            status="success",
            trigger_type="scheduled",
            attempt=1,
            retry_count=0,
            max_retries=0,
            created_at=datetime.utcnow() - timedelta(days=2),
        )
        db.add_all([old_run, fresh_run])
        db.commit()

        fn = functions_api.create_function(
            {
                "name": "清理历史",
                "draft_code": (
                    "def main(payload, context):\n"
                    "    return scheduler_history.delete(\n"
                    "        where={'schedule_id': payload['schedule_id']},\n"
                    "        policy={'retention_seconds': payload['retention_seconds']},\n"
                    "        dry_run=payload.get('dry_run', False),\n"
                    "    )\n"
                ),
            },
            db=db,
        )
        fn_id = fn["id"]

        draft_run = await functions_api.invoke_function(
            fn_id,
            {
                "payload": {"schedule_id": schedule.id, "retention_seconds": 30 * 24 * 3600, "dry_run": True},
                "runtime_path": "draft",
                "execution_mode": "plan",
                "write_mode": "readonly",
            },
            db=db,
        )
        assert draft_run["status"] == "success"
        assert draft_run["output"]["dry_run"] is True
        assert draft_run["output"]["candidate_count"] == 1

        unchanged_count = db.query(models.ScheduleRun).filter(models.ScheduleRun.schedule_id == schedule.id).count()
        assert unchanged_count == 2

        released = functions_api.release_function(fn_id, {}, db=db)
        assert released["function"]["status"] == "released"

        apply_run = await functions_api.invoke_function(
            fn_id,
            {
                "payload": {"schedule_id": schedule.id, "retention_seconds": 30 * 24 * 3600, "dry_run": False},
                "runtime_path": "production",
                "execution_mode": "apply",
                "write_mode": "write",
                "confirm_apply": True,
            },
            db=db,
        )
        assert apply_run["status"] == "success"
        assert apply_run["output"]["deleted_count"] == 1

        remaining_ids = {
            row.id
            for row in db.query(models.ScheduleRun)
            .filter(models.ScheduleRun.schedule_id == schedule.id)
            .all()
        }
        assert old_run.id not in remaining_ids
        assert fresh_run.id in remaining_ids
    finally:
        db.close()


@pytest.mark.anyio
async def test_function_chat_invoke_returns_structured_lifecycle_error(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {"name": "draft-only-function", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        with pytest.raises(HTTPException) as err:
            await functions_api.run_function_chat_action(
                fn["id"],
                {
                    "action": "invoke",
                    "invoke": {
                        "payload": {},
                        "runtime_path": "production",
                    },
                },
                db=db,
            )
        assert err.value.status_code == 400
        assert isinstance(err.value.detail, dict)
        assert err.value.detail.get("error_code") == "release_required"
        assert "no released version" in str(err.value.detail.get("message") or "").lower()
    finally:
        db.close()


@pytest.mark.anyio
async def test_function_chat_invoke_failed_result_contains_structured_error_code(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {
                "name": "draft-query-function",
                "draft_code": "def main(payload, context):\n    return db.query('SHOW DATABASES')\n",
            },
            db=db,
        )
        response = await functions_api.run_function_chat_action(
            fn["id"],
            {
                "action": "invoke",
                "invoke": {
                    "payload": {},
                    "runtime_path": "draft",
                    "execution_mode": "plan",
                    "write_mode": "readonly",
                },
            },
            db=db,
        )
        assert response["status"] == "failed"
        assert response["data"]["error_code"] == "datasource_required"
    finally:
        db.close()


def test_function_build_with_ambiguous_prompt_continues_generation(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {
                "name": "tenant-query",
                "draft_code": "result = {'rows': len(payload.get('rows', []))}",
            },
            db=db,
        )
        fn_id = fn["id"]

        built = functions_api.build_function(
            fn_id,
            {"prompt": "检索数据源，从中找到第一个租户，查询里面所有库"},
            db=db,
        )
        assert built["build_run"]["status"] == "done"
        assert [event["phase"] for event in built["build_run"]["events"]] == ["apply"]
        assert built["function"]["source_path"]
    finally:
        db.close()


def test_function_build_with_ambiguity_mode_default_continues(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {
                "name": "tenant-query-default",
                "draft_code": "result = {'rows': len(payload.get('rows', []))}",
            },
            db=db,
        )
        built = functions_api.build_function(
            fn["id"],
            {
                "prompt": "检索数据源，从中找到第一个租户，查询里面所有库",
                "ambiguity_mode": "default",
            },
            db=db,
        )
        assert built["build_run"]["status"] == "done"
        assert [event["phase"] for event in built["build_run"]["events"]] == ["apply"]
    finally:
        db.close()


@pytest.mark.anyio
async def test_schedule_api_pause_resume_run_now_and_history(session_factory: Any):
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
        fn = functions_api.create_function(
            {"name": "scheduled-report", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        fn_id = fn["id"]
        functions_api.release_function(fn_id, {}, db=db)

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

        built = schedules_api.build_schedule(
            schedule_id,
            {"prompt": "改成每 5 分钟执行一次，失败重试 3 次"},
            db=db,
        )
        assert built["schedule"]["interval_seconds"] == 300
        assert built["schedule"]["max_retries"] == 3

        paused = schedules_api.pause_schedule(schedule_id, db=db)
        assert paused["status"] == "paused"

        resumed = schedules_api.resume_schedule(schedule_id, db=db)
        assert resumed["status"] == "active"

        disabled = schedules_api.disable_schedule(schedule_id, db=db)
        assert disabled["status"] == "paused"

        enabled = schedules_api.enable_schedule(schedule_id, db=db)
        assert enabled["status"] == "active"

        run_now = await schedules_api.run_schedule_now(schedule_id, db=db)
        assert isinstance(run_now["run_id"], str)

        runs = schedules_api.list_schedule_runs(schedule_id, db=db)
        assert len(runs) >= 1
    finally:
        db.close()


def test_schedule_api_rejects_invalid_timezone(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {"name": "invalid-timezone-fn", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        functions_api.release_function(fn["id"], {}, db=db)
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


def test_schedule_api_create_triggers_runtime_refresh(session_factory: Any, monkeypatch: pytest.MonkeyPatch):
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
        fn = functions_api.create_function(
            {"name": "refresh-on-create", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        functions_api.release_function(fn["id"], {}, db=db)
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
        fn = functions_api.create_function(
            {"name": "repair-run-fn", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        functions_api.release_function(fn["id"], {}, db=db)
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
        fn = functions_api.create_function(
            {"name": "runs-pagination-fn", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        functions_api.release_function(fn["id"], {}, db=db)
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
        runs = schedules_api.list_schedule_runs(schedule_id, limit=2, offset=1, response=response, db=db)
        assert [item["run_id"] for item in runs] == ["run-2", "run-1"]
        assert response.headers.get("X-Total-Count") == "3"
        assert response.headers.get("X-Limit") == "2"
        assert response.headers.get("X-Offset") == "1"
    finally:
        db.close()


def test_schedule_api_list_all_runs_supports_global_and_schedule_filter(session_factory: Any):
    db = session_factory()
    try:
        fn = functions_api.create_function(
            {"name": "runs-global-fn", "draft_code": "result = {'ok': True}"},
            db=db,
        )
        functions_api.release_function(fn["id"], {}, db=db)
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
        all_runs = schedules_api.list_all_schedule_runs(limit=10, offset=0, response=response_all, db=db)
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
