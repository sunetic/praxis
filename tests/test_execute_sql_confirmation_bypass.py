from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database as database_module
from app.db.database import Base
from app.models import models
from app.services.datasource.router import RoutedDataSource
from app.services.platform.settings_store import upsert_setting
from app.tools import registry as registry_module


class RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    async def execute_query(
        self,
        datasource: models.DataSource,
        sql: str,
        *,
        role: str,
    ) -> dict[str, Any]:
        self.calls.append((datasource.id, sql, role))
        return {"rows": [], "row_count": 1}


SqlRuntime = tuple[Any, int, RecordingPool]


@pytest.fixture
def execute_sql_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SqlRuntime]:
    engine = create_engine(f"sqlite:///{tmp_path}/execute-sql.db")
    session_local = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_local() as db:
        conversation = models.Conversation(title="confirmation-policy")
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id

    datasource = models.DataSource(
        id=7,
        name="write-target",
        host="127.0.0.1",
        port=2881,
        cluster_key="test-cluster",
        tenant_role="user",
    )
    routed = RoutedDataSource(
        datasource=datasource,
        requested_role="user",
        resolved_role="user",
        reason="requested_role_available",
    )
    pool = RecordingPool()

    monkeypatch.setattr(database_module, "SessionLocal", session_local)
    monkeypatch.setattr(registry_module, "resolve_datasource_by_role", lambda *_: routed)
    monkeypatch.setattr("app.db.pool_factory.get_pool_for_datasource", lambda _: pool)

    async def fake_probe(*_: object) -> dict[str, str]:
        return {"effective_tenant_id": "1001", "database_name": "app"}

    monkeypatch.setattr("app.services.datasource.sql_guard.probe_tenant_fingerprint", fake_probe)
    yield session_local, conversation_id, pool
    engine.dispose()


@pytest.mark.asyncio
async def test_mutating_sql_requires_confirmation_by_default(
    execute_sql_runtime: SqlRuntime,
) -> None:
    session_local, conversation_id, pool = execute_sql_runtime
    with session_local() as db:
        upsert_setting(db, "sql_allow_mutating", True)
        db.commit()

    result = await registry_module.ExecuteSQLTool().execute(
        sql="UPDATE accounts SET enabled = 1",
        datasource_id=7,
        conversation_id=conversation_id,
        request_id="batch-default",
    )

    assert result.success is False
    assert result.error["code"] == "pending_confirmation"
    assert result.data["requires_confirmation"] is True
    assert pool.calls == []
    with session_local() as db:
        assert db.query(models.PendingAction).count() == 1


@pytest.mark.asyncio
async def test_bypass_executes_mutating_sql_without_pending_action(
    execute_sql_runtime: SqlRuntime,
) -> None:
    session_local, _, pool = execute_sql_runtime
    with session_local() as db:
        upsert_setting(db, "sql_allow_mutating", True)
        upsert_setting(db, "ai_action_confirmation_bypass", True)
        db.commit()

    result = await registry_module.ExecuteSQLTool().execute(
        sql="UPDATE accounts SET enabled = 1",
        datasource_id=7,
    )

    assert result.success is True
    assert result.data["confirmation_bypassed"] is True
    assert result.data["tenant_fingerprint"]["effective_tenant_id"] == "1001"
    assert len(result.data["execution_fingerprint"]) == 64
    assert pool.calls == [(7, "UPDATE accounts SET enabled = 1", "user")]
    with session_local() as db:
        assert db.query(models.PendingAction).count() == 0


@pytest.mark.asyncio
async def test_bypass_does_not_enable_blocked_write_operations(
    execute_sql_runtime: SqlRuntime,
) -> None:
    session_local, _, pool = execute_sql_runtime
    with session_local() as db:
        upsert_setting(db, "ai_action_confirmation_bypass", True)
        db.commit()

    result = await registry_module.ExecuteSQLTool().execute(
        sql="DELETE FROM accounts",
        datasource_id=7,
    )

    assert result.success is False
    assert result.error["code"] == "sql_safety_mode_active"
    assert pool.calls == []
