from pathlib import Path
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models import models
from app.services.platform.object_tools import ObjectToolService
from app.tools.registry import ObjectCrudTool, ObjectOperateTool


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "object-tools.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _tool_pair(session_factory):
    service = ObjectToolService(session_factory=session_factory)
    return ObjectCrudTool(service=service), ObjectOperateTool(service=service)


def _tool_pair_with_delivery(session_factory, channel_delivery):
    service = ObjectToolService(session_factory=session_factory, channel_delivery=channel_delivery)
    return ObjectCrudTool(service=service), ObjectOperateTool(service=service)


def _create_schedule_with_runs(session_factory):
    db = session_factory()
    schedule = models.Schedule(
        name="retention-job",
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
        run_id="old-run",
        status="success",
        trigger_type="scheduled",
        attempt=1,
        retry_count=0,
        max_retries=0,
        created_at=datetime.utcnow() - timedelta(days=45),
    )
    fresh_run = models.ScheduleRun(
        schedule_id=schedule.id,
        run_id="fresh-run",
        status="success",
        trigger_type="scheduled",
        attempt=1,
        retry_count=0,
        max_retries=0,
        created_at=datetime.utcnow() - timedelta(days=3),
    )
    db.add_all([old_run, fresh_run])
    db.commit()
    db.refresh(schedule)
    db.refresh(old_run)
    db.refresh(fresh_run)
    db.close()
    return schedule, old_run, fresh_run


@pytest.mark.anyio
async def test_object_crud_page_create_and_audit(session_factory):
    crud_tool, _ = _tool_pair(session_factory)
    result = await crud_tool.execute(
        object_type="page",
        action="create",
        payload={"name": "slow-sql-dashboard", "description": "d1"},
        actor="test-user",
    )
    assert result.success is True
    page_id = result.data["id"]
    assert page_id > 0
    assert result.data["status"] == "draft"

    db = session_factory()
    logs = db.query(models.ObjectAuditLog).order_by(models.ObjectAuditLog.id.asc()).all()
    assert len(logs) == 1
    assert logs[0].object_type == "page"
    assert logs[0].object_id == str(page_id)
    assert logs[0].action == "crud:create"
    assert logs[0].result == "success"
    db.close()


@pytest.mark.anyio
async def test_object_operate_page_lifecycle_rejection_and_failure_audit(session_factory):
    crud_tool, operate_tool = _tool_pair(session_factory)
    created = await crud_tool.execute(
        object_type="page",
        action="create",
        payload={"name": "ops-page"},
    )
    page_id = created.data["id"]

    archived = await operate_tool.execute(
        object_type="page",
        action="archive",
        object_id=page_id,
    )
    assert archived.success is True
    assert archived.data["status"] == "archived"

    publish = await operate_tool.execute(
        object_type="page",
        action="publish",
        object_id=page_id,
        payload={"artifact_payload": {"v": 1}},
    )
    assert publish.success is False
    assert publish.error["code"] == "lifecycle_constraint"

    db = session_factory()
    failure_logs = (
        db.query(models.ObjectAuditLog)
        .filter(models.ObjectAuditLog.action == "operate:publish")
        .order_by(models.ObjectAuditLog.id.desc())
        .all()
    )
    assert failure_logs
    assert failure_logs[0].result == "failure"
    db.close()


@pytest.mark.anyio
async def test_object_crud_datasource_persists_attributes(session_factory):
    crud_tool, _ = _tool_pair(session_factory)

    created = await crud_tool.execute(
        object_type="datasource",
        action="create",
        payload={
            "name": "ocp-cluster-a/tenant-a",
            "host": "127.0.0.1",
            "port": 2881,
            "db_type": "oceanbase",
            "cluster_key": "cluster-a",
            "tenant_role": "user",
            "user": "root@tenant-a",
            "password": "secret",
            "database": "test",
            "attributes": {"ocp_cluster_id": 1, "ocp_tenant_id": 2},
        },
        actor="test-user",
    )
    assert created.success is True
    datasource_id = created.data["id"]

    updated = await crud_tool.execute(
        object_type="datasource",
        action="update",
        object_id=datasource_id,
        payload={"attributes": {"ocp_cluster_id": 1, "ocp_tenant_id": 3, "tenant_mode": "MYSQL"}},
        actor="test-user",
    )
    assert updated.success is True

    db = session_factory()
    row = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).one()
    assert row.attributes == {"ocp_cluster_id": 1, "ocp_tenant_id": 3, "tenant_mode": "MYSQL"}
    db.close()


@pytest.mark.anyio
async def test_object_tools_function_and_scheduler_operations(session_factory):
    crud_tool, operate_tool = _tool_pair(session_factory)

    fn_created = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "日报函数", "draft_code": "result = {'ok': True}"},
    )
    assert fn_created.success is True
    function_id = fn_created.data["id"]
    assert fn_created.data["name"] == "日报函数"
    assert str(fn_created.data["slug"]).startswith("fn-")

    released = await operate_tool.execute(
        object_type="function",
        action="release",
        object_id=function_id,
        payload={"code_snapshot": "result = {'ok': True, 'payload': payload}"},
    )
    assert released.success is True
    assert released.data["status"] == "released"
    assert released.data["verification_passed"] is True
    assert released.data["strategy"] in {"reuse", "extend", "create"}
    assert released.data["release"]["release_metadata"]["verification"]["passed"] is True

    scheduler_created = await crud_tool.execute(
        object_type="scheduler",
        action="create",
        payload={
            "name": "job-daily",
            "function_id": function_id,
            "schedule_type": "interval",
            "interval_seconds": 60,
            "max_retries": 1,
            "retry_backoff_seconds": 0,
        },
    )
    assert scheduler_created.success is True
    scheduler_id = scheduler_created.data["id"]

    run_now = await operate_tool.execute(
        object_type="scheduler",
        action="run-now",
        object_id=scheduler_id,
    )
    assert run_now.success is True
    assert isinstance(run_now.data["run_id"], str)

    runs = await operate_tool.execute(
        object_type="scheduler",
        action="list-runs",
        object_id=scheduler_id,
        payload={"limit": 5},
    )
    assert runs.success is True
    assert runs.data["count"] >= 1

    db = session_factory()
    audit_actions = {
        row.action
        for row in db.query(models.ObjectAuditLog).all()
        if row.object_type in {"function", "scheduler"}
    }
    assert "operate:release" in audit_actions
    assert "operate:run-now" in audit_actions
    db.close()


@pytest.mark.anyio
async def test_function_release_is_blocked_when_verification_fails(session_factory):
    crud_tool, operate_tool = _tool_pair(session_factory)
    fn_created = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "broken-fn", "draft_code": "def broken("},
    )
    assert fn_created.success is True
    function_id = fn_created.data["id"]

    released = await operate_tool.execute(
        object_type="function",
        action="release",
        object_id=function_id,
    )
    assert released.success is False
    assert released.error["code"] == "verification_failed"
    assert released.error["details"]["diagnostics"]


@pytest.mark.anyio
async def test_function_strategy_action_returns_candidate_decision(session_factory):
    crud_tool, operate_tool = _tool_pair(session_factory)

    baseline = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "slow-sql-analysis", "draft_code": "result = {'ok': True}"},
    )
    assert baseline.success is True
    baseline_id = baseline.data["id"]
    baseline_released = await operate_tool.execute(
        object_type="function",
        action="release",
        object_id=baseline_id,
    )
    assert baseline_released.success is True

    target = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "new-fn", "description": "Need slow sql analysis"},
    )
    assert target.success is True

    strategy = await operate_tool.execute(
        object_type="function",
        action="strategy",
        object_id=target.data["id"],
        payload={
            "requirement": "slow sql analysis",
            "reuse_threshold": 0.1,
            "extend_threshold": 0.05,
        },
    )
    assert strategy.success is True
    assert strategy.data["strategy"] == "reuse"
    assert strategy.data["top_candidate"]["function_id"] == baseline_id
    assert strategy.data["top_candidate"]["slug"]


@pytest.mark.anyio
async def test_function_object_tool_rejects_client_managed_slug(session_factory):
    crud_tool, _ = _tool_pair(session_factory)

    created = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "任意名称", "slug": "manual-slug"},
    )
    assert created.success is False
    assert created.error["code"] == "invalid_payload"
    assert "slug" in created.error["message"]


@pytest.mark.anyio
async def test_sensitive_action_requires_non_empty_actor(session_factory):
    crud_tool, operate_tool = _tool_pair(session_factory)
    fn_created = await crud_tool.execute(
        object_type="function",
        action="create",
        payload={"name": "policy-fn", "draft_code": "result = {'ok': True}"},
    )
    assert fn_created.success is True
    function_id = fn_created.data["id"]

    release = await operate_tool.execute(
        object_type="function",
        action="release",
        object_id=function_id,
        actor="",
    )
    assert release.success is False
    assert release.error["code"] == "policy_violation"


@pytest.mark.anyio
async def test_scheduler_history_crud_supports_list_and_filtered_delete(session_factory):
    crud_tool, _ = _tool_pair(session_factory)
    schedule, old_run, fresh_run = _create_schedule_with_runs(session_factory)

    listed = await crud_tool.execute(
        object_type="scheduler_history",
        action="list",
        payload={"schedule_id": schedule.id, "limit": 10},
        actor="test-user",
    )
    assert listed.success is True
    assert listed.data["count"] == 2

    dry_run = await crud_tool.execute(
        object_type="scheduler_history",
        action="delete",
        payload={
            "where": {"schedule_id": schedule.id},
            "policy": {"retention_seconds": 30 * 24 * 3600},
            "dry_run": True,
        },
        actor="test-user",
    )
    assert dry_run.success is True
    assert dry_run.data["dry_run"] is True
    assert dry_run.data["candidate_count"] == 1
    assert dry_run.data["sample_runs"][0]["id"] == old_run.id

    deleted = await crud_tool.execute(
        object_type="scheduler_history",
        action="delete",
        payload={
            "where": {"schedule_id": schedule.id},
            "policy": {"retention_seconds": 30 * 24 * 3600},
            "dry_run": False,
        },
        actor="test-user",
    )
    assert deleted.success is True
    assert deleted.data["deleted_count"] == 1

    db = session_factory()
    remaining_ids = {
        row.id
        for row in db.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == schedule.id)
        .all()
    }
    assert old_run.id not in remaining_ids
    assert fresh_run.id in remaining_ids
    delete_logs = (
        db.query(models.ObjectAuditLog)
        .filter(
            models.ObjectAuditLog.object_type == "scheduler_history",
            models.ObjectAuditLog.action == "crud:delete",
            models.ObjectAuditLog.result == "success",
        )
        .order_by(models.ObjectAuditLog.id.asc())
        .all()
    )
    assert delete_logs
    db.close()


@pytest.mark.anyio
async def test_scheduler_history_delete_requires_scope_filter(session_factory):
    crud_tool, _ = _tool_pair(session_factory)

    deleted = await crud_tool.execute(
        object_type="scheduler_history",
        action="delete",
        payload={"dry_run": True},
        actor="test-user",
    )
    assert deleted.success is False
    assert deleted.error["code"] == "missing_delete_scope"


class _FakeChannelDelivery:
    def __init__(self):
        self.calls: list[dict] = []

    async def send(self, *, channel: models.Channel, payload: dict | None = None):
        self.calls.append(
            {
                "channel_id": channel.id,
                "provider": channel.provider,
                "payload": payload or {},
            }
        )
        return {"provider": channel.provider, "channel_id": channel.id, "ok": True}


@pytest.mark.anyio
async def test_object_tools_channel_crud_and_send_operation(session_factory):
    fake_delivery = _FakeChannelDelivery()
    crud_tool, operate_tool = _tool_pair_with_delivery(session_factory, fake_delivery)

    created = await crud_tool.execute(
        object_type="channel",
        action="create",
        payload={
            "name": "钉钉告警",
            "provider": "dingtalk",
            "config": {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test-token",
                "security": {"mode": "keyword", "keyword": "报警"},
                "template": {
                    "type": "markdown",
                    "title": "报警通知",
                    "body": "### 报警\n关键词: 报警",
                },
            },
        },
        actor="test-user",
    )
    assert created.success is True
    channel_id = created.data["id"]

    sent = await operate_tool.execute(
        object_type="channel",
        action="send",
        object_id=channel_id,
        payload={"content": "报警: cpu > 90%"},
        actor="test-user",
    )
    assert sent.success is True
    assert sent.data["object_type"] == "channel"
    assert sent.data["provider"] == "dingtalk"
    assert fake_delivery.calls and fake_delivery.calls[0]["channel_id"] == channel_id

    db = session_factory()
    try:
        logs = (
            db.query(models.ObjectAuditLog)
            .filter(models.ObjectAuditLog.object_type == "channel")
            .order_by(models.ObjectAuditLog.id.asc())
            .all()
        )
        assert {item.action for item in logs} >= {"crud:create", "operate:send"}
    finally:
        db.close()
