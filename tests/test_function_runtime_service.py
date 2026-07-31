import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import Base
from app.models import models
from app.services.function.runtime import (
    FunctionRunStatus,
    FunctionRuntimeService,
    RuntimeDatasourceAccessError,
    RuntimeErrorClass,
    RuntimeErrorCode,
    RuntimePlatformAccessError,
    _execute_code_snapshot,
    _RuntimeDatabaseCapability,
    _RuntimeDatasourceBroker,
    _RuntimePlatformCapability,
    _RuntimeSchedulerHistoryCapability,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "runtime.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_released_function(db: Session, *, code_snapshot: str) -> models.Function:
    fn = models.Function(name="demo-fn", status="released")
    release = models.FunctionRelease(
        function=fn,
        function_id=0,
        version=1,
        code_snapshot=code_snapshot,
        dependency_manifest={},
    )
    db.add(fn)
    db.flush()
    release.function_id = fn.id
    db.add(release)
    db.flush()
    fn.current_release = release
    db.commit()
    db.refresh(fn)
    return fn


def _create_datasource(
    db: Session,
    *,
    name: str,
    tenant_role: str = "user",
    cluster_key: str = "cluster-a",
) -> models.DataSource:
    datasource = models.DataSource(
        name=name,
        host="127.0.0.1",
        port=2881,
        db_type="oceanbase",
        cluster_key=cluster_key,
        tenant_role=tenant_role,
        user=f"{name}_user",
        password=f"{name}_pwd",
        database="monitor_db",
        status="active",
    )
    db.add(datasource)
    db.commit()
    db.refresh(datasource)
    return datasource


def _create_schedule(db: Session, *, name: str = "cleanup-job") -> models.Schedule:
    schedule = models.Schedule(
        name=name,
        status="active",
        target_type="function",
        target_id=1,
        schedule_type="interval",
        interval_seconds=60,
        timezone="UTC",
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


def _create_schedule_run(
    db: Session,
    *,
    schedule_id: int,
    status: str = "success",
    created_at: datetime | None = None,
) -> models.ScheduleRun:
    run = models.ScheduleRun(
        schedule_id=schedule_id,
        run_id=f"run-{schedule_id}-{uuid4().hex[:8]}",
        status=status,
        trigger_type="scheduled",
        attempt=1,
        retry_count=0,
        max_retries=0,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@pytest.mark.anyio
async def test_function_runtime_success_with_context_binding(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot="result = {'value': payload['value'] * 2, 'datasource': context['datasource_id']}",
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    try:
        result = await service.invoke(
            fn,
            {"value": 3},
            datasource_id=42,
            scope_metadata={"scope_object_id": "page-1"},
            timeout_seconds=3,
        )
    finally:
        service._executor.shutdown(cancel_futures=True)

    assert result.status == FunctionRunStatus.SUCCESS.value
    assert result.output == {"value": 6, "datasource": 42}

    verify_db = session_factory()
    row = verify_db.query(models.FunctionRun).filter_by(run_id=result.run_id).one()
    assert row.status == FunctionRunStatus.SUCCESS.value
    assert row.error_class is None
    assert row.runtime_context == {
        "datasource_id": 42,
        "scope": {"scope_object_id": "page-1"},
    }
    verify_db.close()


@pytest.mark.anyio
async def test_function_runtime_timeout_classification(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot="import time\ntime.sleep(1.5)\nresult = 1",
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    try:
        result = await service.invoke(fn, {"value": 1}, timeout_seconds=0.1)
    finally:
        service._executor.shutdown(cancel_futures=True)

    assert result.status == FunctionRunStatus.FAILED.value
    assert result.error_class == RuntimeErrorClass.TIMEOUT.value
    assert result.error_code == RuntimeErrorCode.TIMEOUT.value


@pytest.mark.anyio
async def test_function_runtime_dependency_classification(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot="import definitely_missing_dependency\nresult = 1",
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    try:
        result = await service.invoke(fn, {"value": 1}, timeout_seconds=1)
    finally:
        service._executor.shutdown(cancel_futures=True)

    assert result.status == FunctionRunStatus.FAILED.value
    assert result.error_class == RuntimeErrorClass.DEPENDENCY.value
    assert result.error_code == RuntimeErrorCode.DEPENDENCY_ERROR.value


@pytest.mark.anyio
async def test_function_runtime_datasource_required_error_code(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot="def main(payload, context):\n    return db.query('SHOW DATABASES')\n",
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    try:
        result = await service.invoke(
            fn,
            {},
            timeout_seconds=1,
        )
    finally:
        service._executor.shutdown(cancel_futures=True)

    assert result.status == FunctionRunStatus.FAILED.value
    assert result.error_code == RuntimeErrorCode.DATASOURCE_REQUIRED.value


@pytest.mark.anyio
async def test_function_runtime_cancellation_marks_run_cancelled(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot="import time\ntime.sleep(2)\nresult = payload",
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    task = asyncio.create_task(service.invoke(fn, {"value": 1}, timeout_seconds=5))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    service._executor.shutdown(cancel_futures=True)

    verify_db = session_factory()
    row = verify_db.query(models.FunctionRun).order_by(models.FunctionRun.id.desc()).first()
    assert row is not None
    assert row.status == FunctionRunStatus.CANCELLED.value
    assert row.error_class == RuntimeErrorClass.CANCELLED.value
    verify_db.close()


@pytest.mark.anyio
async def test_function_runtime_supports_class_based_entrypoint(session_factory):
    db = session_factory()
    fn = _create_released_function(
        db,
        code_snapshot=(
            "class Incrementer(FunctionBase):\n"
            "    def run(self, payload, context):\n"
            "        return {'next': payload['value'] + 1, 'datasource': context['datasource_id']}\n"
        ),
    )
    db.close()

    service = FunctionRuntimeService(session_factory=session_factory)
    try:
        result = await service.invoke(
            fn,
            {"value": 5},
            datasource_id=12,
            timeout_seconds=3,
        )
    finally:
        service._executor.shutdown(cancel_futures=True)

    assert result.status == FunctionRunStatus.SUCCESS.value
    assert result.output == {"next": 6, "datasource": 12}


def test_execute_code_snapshot_class_entrypoint_can_use_text_helper():
    result = _execute_code_snapshot(
        code_snapshot=(
            "class SQLPreview(FunctionBase):\n"
            "    def run(self, payload, context):\n"
            "        stmt = text('SELECT 1 AS value')\n"
            "        return {'sql': str(stmt)}\n"
        ),
        payload={},
        context={},
        runtime_services={"db_capability": object()},
    )
    assert result == {"sql": "SELECT 1 AS value"}


def test_execute_code_snapshot_injects_db_capability_for_global_db():
    class FakeDB:
        def query(self, sql, *, datasource=None, role="user", params=None):
            return {
                "sql": sql,
                "datasource": datasource,
                "role": role,
                "params": params,
            }

    result = _execute_code_snapshot(
        code_snapshot="result = db.query('select 1', datasource='monitor', role='sys')",
        payload={},
        context={},
        runtime_services={"db_capability": FakeDB()},
    )
    assert result == {
        "sql": "select 1",
        "datasource": "monitor",
        "role": "sys",
        "params": None,
    }


def test_execute_code_snapshot_supports_context_capability_aliases():
    class FakeDB:
        def query_by_id(self, *, sql, datasource_id, params=None):
            return {
                "rows": [{"sql": sql, "datasource_id": datasource_id, "params": params}],
            }

    class FakePlatform:
        def list(self, object_type, filters=None):
            return [{"object_type": object_type, "filters": filters}]

    result = _execute_code_snapshot(
        code_snapshot=(
            "def main(payload, context):\n"
            "    db = context.get('db')\n"
            "    platform = context.get('platform')\n"
            "    rows = db.query_by_id(sql='select 1', datasource_id=context.get('datasource_id')).get('rows', [])\n"
            "    return {'rows': rows, 'sources': platform.list('datasource')}\n"
        ),
        payload={},
        context={"datasource_id": 7},
        runtime_services={
            "db_capability": FakeDB(),
            "platform_capability": FakePlatform(),
            "scheduler_history_capability": object(),
        },
    )
    assert result == {
        "rows": [{"sql": "select 1", "datasource_id": 7, "params": None}],
        "sources": [{"object_type": "datasource", "filters": None}],
    }


def test_execute_code_snapshot_exposes_platform_capability():
    class FakePlatform:
        def list(self, _object_type, *, filters=None, limit=100):
            _ = filters, limit
            return [{"id": 1, "name": "ds-a"}]

        def get(self, _object_type, _object_id):
            return {"id": 1}

    result = _execute_code_snapshot(
        code_snapshot=(
            "def main(payload, context):\n"
            "    items = platform.list('datasource')\n"
            "    return {'count': len(items), 'first': items[0]['id'] if items else None}\n"
        ),
        payload={},
        context={},
        runtime_services={"db_capability": object(), "platform_capability": FakePlatform()},
    )
    assert result == {"count": 1, "first": 1}


def test_execute_code_snapshot_exposes_scheduler_history_capability():
    class FakeSchedulerHistory:
        def list(self, *, where=None, limit=20):
            schedule_id = (where or {}).get("schedule_id")
            _ = limit
            return [{"id": 11, "schedule_id": schedule_id, "status": "success"}]

    result = _execute_code_snapshot(
        code_snapshot=(
            "def main(payload, context):\n"
            "    rows = scheduler_history.list(where={'schedule_id': payload['schedule_id']})\n"
            "    return {'count': len(rows), 'first': rows[0]['id'] if rows else None}\n"
        ),
        payload={"schedule_id": 123},
        context={},
        runtime_services={
            "db_capability": object(),
            "platform_capability": object(),
            "scheduler_history_capability": FakeSchedulerHistory(),
        },
    )
    assert result == {"count": 1, "first": 11}


def test_runtime_db_capability_allows_datasource_resolution_by_id(session_factory, monkeypatch):
    db = session_factory()
    ds_allowed = _create_datasource(db, name="monitor-main")
    ds_blocked = _create_datasource(db, name="monitor-other")
    ds_allowed_id = ds_allowed.id
    ds_blocked_id = ds_blocked.id
    db.close()

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    broker = _RuntimeDatasourceBroker(
        control_db_url=control_db_url,
        default_datasource_id=ds_allowed_id,
    )
    capability = _RuntimeDatabaseCapability(broker)

    ok = capability.query("select 1")
    assert ok["resolved_datasource_id"] == ds_allowed_id
    assert ok["requested_datasource_id"] == ds_allowed_id

    blocked = capability.query("select 1", datasource=ds_blocked_id)
    assert blocked["resolved_datasource_id"] == ds_blocked_id
    assert blocked["requested_datasource_id"] == ds_blocked_id


def test_runtime_db_capability_allows_access_without_policy(session_factory, monkeypatch):
    db = session_factory()
    ds = _create_datasource(db, name="monitor-free")
    ds_id = ds.id
    db.close()

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    capability = _RuntimeDatabaseCapability(
        _RuntimeDatasourceBroker(
            control_db_url=control_db_url,
            default_datasource_id=None,
        )
    )

    result = capability.query("select 1", datasource=ds_id)
    assert result["resolved_datasource_id"] == ds_id


def test_runtime_db_capability_blocks_write_sql_in_plan_mode(session_factory, monkeypatch):
    db = session_factory()
    ds = _create_datasource(db, name="monitor-plan")
    ds_id = ds.id
    db.close()

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    broker = _RuntimeDatasourceBroker(
        control_db_url=control_db_url,
        default_datasource_id=ds_id,
    )
    capability = _RuntimeDatabaseCapability(broker, execution_mode="plan")

    ok = capability.query("select 1")
    assert ok["resolved_datasource_id"] == ds_id
    with pytest.raises(RuntimeDatasourceAccessError):
        capability.query("update t set c = 1")


def test_runtime_db_capability_converts_qmark_placeholder(session_factory, monkeypatch):
    db = session_factory()
    ds = _create_datasource(db, name="monitor-qmark")
    ds_id = ds.id
    db.close()

    captured: dict[str, Any] = {}

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            captured["sql"] = sql
            captured["params"] = params
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    broker = _RuntimeDatasourceBroker(
        control_db_url=control_db_url,
        default_datasource_id=ds_id,
    )
    capability = _RuntimeDatabaseCapability(broker)

    _ = capability.query("select * from t where datasource_id = ?", params=[ds_id])
    assert captured["sql"] == "select * from t where datasource_id = %s"
    assert captured["params"] == [ds_id]


def test_runtime_db_capability_get_conn_by_id(session_factory, monkeypatch):
    db = session_factory()
    ds = _create_datasource(db, name="monitor-conn")
    ds_id = ds.id
    db.close()

    captured: dict[str, Any] = {}

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            captured["datasource_id"] = datasource.id
            captured["role"] = role
            captured["sql"] = sql
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            captured["explain_role"] = role
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    capability = _RuntimeDatabaseCapability(
        _RuntimeDatasourceBroker(control_db_url=control_db_url, default_datasource_id=ds_id)
    )

    conn = capability.get_conn_by_id(ds_id)
    _ = conn.query("select 1")
    _ = conn.explain("select 1")
    assert captured["datasource_id"] == ds_id
    assert captured["role"] == "user"
    assert captured["explain_role"] == "user"


def test_runtime_db_capability_get_conn_by_id_strict_signature(session_factory):
    db = session_factory()
    ds = _create_datasource(db, name="monitor-conn-strict")
    ds_id = ds.id
    db.close()

    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    capability = _RuntimeDatabaseCapability(
        _RuntimeDatasourceBroker(control_db_url=control_db_url, default_datasource_id=ds_id)
    )

    with pytest.raises(TypeError):
        capability.get_conn_by_id(ds_id, "user")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        capability.get_session_by_id(ds_id, "user")  # type: ignore[call-arg]


def test_runtime_db_capability_get_conn_by_id_targets_exact_datasource(
    session_factory, monkeypatch
):
    db = session_factory()
    business_ds = _create_datasource(
        db,
        name="monitor-business-direct",
        tenant_role="user",
        cluster_key="cluster-direct-id",
    )
    sys_ds = _create_datasource(
        db,
        name="monitor-sys-direct",
        tenant_role="sys",
        cluster_key="cluster-direct-id",
    )
    business_ds_id = business_ds.id
    sys_ds_id = sys_ds.id
    db.close()

    captured: dict[str, Any] = {}

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            captured["datasource_id"] = datasource.id
            captured["role"] = role
            captured["sql"] = sql
            _ = params
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            captured["explain_datasource_id"] = datasource.id
            captured["explain_role"] = role
            _ = sql
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    capability = _RuntimeDatabaseCapability(
        _RuntimeDatasourceBroker(
            control_db_url=control_db_url,
            default_datasource_id=business_ds_id,
        )
    )

    conn = capability.get_conn_by_id(sys_ds_id)
    _ = conn.query("select 1")
    _ = conn.explain("select 1")
    assert captured["datasource_id"] == sys_ds_id
    assert captured["role"] == "sys"
    assert captured["explain_datasource_id"] == sys_ds_id
    assert captured["explain_role"] == "sys"


def test_runtime_db_capability_rejects_system_role_value(session_factory, monkeypatch):
    db = session_factory()
    business_ds = _create_datasource(
        db,
        name="monitor-business",
        tenant_role="user",
        cluster_key="cluster-role-alias",
    )
    sys_ds = _create_datasource(
        db,
        name="monitor-sys",
        tenant_role="sys",
        cluster_key="cluster-role-alias",
    )
    business_ds_id = business_ds.id
    _ = sys_ds
    db.close()

    captured: dict[str, Any] = {}

    class FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):
            captured["datasource_id"] = datasource.id
            captured["role"] = role
            captured["sql"] = sql
            _ = params
            return {"columns": ["v"], "rows": [{"v": 1}], "row_count": 1}

        async def execute_explain(self, datasource, sql, role="user"):
            _ = datasource, sql, role
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.services.function.runtime.get_db_pool", lambda: FakePool())
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    capability = _RuntimeDatabaseCapability(
        _RuntimeDatasourceBroker(
            control_db_url=control_db_url,
            default_datasource_id=business_ds_id,
        )
    )

    with pytest.raises(RuntimeDatasourceAccessError, match="db.query.role 包含未声明值: system"):
        capability.query("select 1", role="system")
    assert captured == {}


def test_runtime_platform_capability_list_and_get(session_factory):
    db = session_factory()
    ds = _create_datasource(db, name="platform-list-ds")
    ds_id = ds.id
    db.close()

    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="plan")

    listed = platform.list("datasource", filters={"status": "active"}, limit=20)
    assert any(int(item.get("id") or 0) == ds_id for item in listed)
    fetched = platform.get("datasource", ds_id)
    assert int(fetched.get("id") or 0) == ds_id


def test_runtime_platform_capability_blocks_mutation_in_plan_mode(session_factory):
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="plan")

    with pytest.raises(RuntimePlatformAccessError, match="plan 模式禁止控制面写操作"):
        platform.crud(object_type="datasource", action="create", payload={})
    with pytest.raises(RuntimePlatformAccessError, match="plan 模式禁止控制面 operate 操作"):
        platform.operate(object_type="function", action="release", object_id=1, payload={})


def test_runtime_platform_capability_rejects_undeclared_scheduler_payload_fields(session_factory):
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="apply")

    with pytest.raises(
        RuntimePlatformAccessError,
        match="platform.crud\\[scheduler.create\\]\\.payload 包含未声明字段: function_id",
    ):
        platform.crud(
            object_type="scheduler",
            action="create",
            payload={"function_id": 1, "schedule_type": "cron"},
        )


def test_runtime_platform_capability_allows_datasource_attributes_payload(session_factory):
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="apply")

    created = platform.crud(
        object_type="datasource",
        action="create",
        payload={
            "name": "ocp-import-ds",
            "host": "127.0.0.1",
            "port": 2881,
            "db_type": "oceanbase",
            "cluster_key": "cluster-ocp",
            "tenant_role": "user",
            "user": "root@tenant-a",
            "password": "secret",
            "database": "test",
            "attributes": {
                "ocp_cluster_id": 101,
                "ocp_tenant_id": 202,
            },
        },
    )

    assert int(created.get("id") or 0) > 0

    verify_db = session_factory()
    row = verify_db.query(models.DataSource).filter(models.DataSource.id == created["id"]).one()
    assert row.attributes == {
        "ocp_cluster_id": 101,
        "ocp_tenant_id": 202,
    }
    verify_db.close()


def test_runtime_platform_list_rejects_invalid_filter_enum(session_factory):
    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="plan")

    with pytest.raises(
        RuntimePlatformAccessError,
        match="platform.list\\[datasource\\]\\.filters.status 包含未声明值: archived",
    ):
        platform.list("datasource", filters={"status": "archived"})


def test_runtime_scheduler_history_delete_requires_dry_run_in_plan_mode(session_factory):
    db = session_factory()
    schedule = _create_schedule(db)
    schedule_id = schedule.id
    _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=45),
    )
    db.close()

    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="plan")
    scheduler_history = _RuntimeSchedulerHistoryCapability(platform, execution_mode="plan")

    with pytest.raises(RuntimePlatformAccessError, match="dry_run=True"):
        scheduler_history.delete(
            where={"schedule_id": schedule_id},
            policy={"retention_seconds": 30 * 24 * 3600},
            dry_run=False,
        )


def test_runtime_scheduler_history_delete_dry_run_returns_candidates_without_removal(
    session_factory,
):
    db = session_factory()
    schedule = _create_schedule(db, name="dry-run-job")
    schedule_id = schedule.id
    old_run = _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=40),
    )
    old_run_id = old_run.id
    _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=5),
    )
    db.close()

    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="plan")
    scheduler_history = _RuntimeSchedulerHistoryCapability(platform, execution_mode="plan")

    result = scheduler_history.delete(
        where={"schedule_id": schedule_id},
        policy={"retention_seconds": 30 * 24 * 3600},
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["deleted_count"] == 0
    assert result["sample_runs"][0]["id"] == old_run_id

    verify_db = session_factory()
    remaining = (
        verify_db.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == schedule_id)
        .count()
    )
    assert remaining == 2
    verify_db.close()


def test_runtime_scheduler_history_delete_apply_removes_matching_runs(session_factory):
    db = session_factory()
    schedule = _create_schedule(db, name="apply-job")
    schedule_id = schedule.id
    old_run = _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=60),
    )
    old_run_id = old_run.id
    latest_old = _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=35),
    )
    latest_old_id = latest_old.id
    fresh_run = _create_schedule_run(
        db,
        schedule_id=schedule_id,
        created_at=datetime.utcnow() - timedelta(days=3),
    )
    fresh_run_id = fresh_run.id
    db.close()

    probe_db = session_factory()
    control_db_url = str(probe_db.get_bind().url)
    probe_db.close()
    platform = _RuntimePlatformCapability(control_db_url=control_db_url, execution_mode="apply")
    scheduler_history = _RuntimeSchedulerHistoryCapability(platform, execution_mode="apply")

    result = scheduler_history.delete(
        where={"schedule_id": schedule_id},
        policy={"retention_seconds": 30 * 24 * 3600, "keep_latest": 1},
        dry_run=False,
    )

    assert result["dry_run"] is False
    assert result["candidate_count"] == 1
    assert result["deleted_count"] == 1
    assert result["sample_runs"][0]["id"] == old_run_id

    verify_db = session_factory()
    remaining_ids = {
        row.id
        for row in verify_db.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == schedule_id)
        .all()
    }
    assert old_run_id not in remaining_ids
    assert latest_old_id in remaining_ids
    assert fresh_run_id in remaining_ids
    verify_db.close()


def test_bind_runtime_context_promotes_execution_mode_from_scope(session_factory):
    service = FunctionRuntimeService(session_factory=session_factory)
    payload, context = service.bind_runtime_context(
        {"ok": True},
        scope_metadata={"execution_mode": "plan", "scope_type": "function-build"},
    )
    assert payload == {"ok": True}
    assert context["execution_mode"] == "plan"
    assert context["scope"] == {"execution_mode": "plan", "scope_type": "function-build"}
