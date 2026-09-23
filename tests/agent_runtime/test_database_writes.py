"""Native SQL approval and no-replay boundaries, separate from LLM quality."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_custom_agents import create, custom_app  # noqa: F401
from test_runtime import Script, call

from app.models.models import Agent, DataSource
from app.services.datasource import agent_tools, write_executor
from app.services.datasource.write_executor import (
    WriteNotExecutedError,
    WriteOutcomeUnknownError,
    validate_write_sql,
)
from app.services.platform.settings_store import upsert_setting

SQL = "UPDATE counters SET n = n + 1 WHERE id = 1"


@pytest.fixture
async def writes(custom_app, monkeypatch):  # noqa: F811
    runtime, client = custom_app
    with runtime.sessions.begin() as db:
        upsert_setting(db, "sql_allow_mutating", True)
    agent = await create(client, tools=["request_database_change", "query_database"])
    conversation = (
        await client.post("/api/v1/conversations", json={"scene": {"agent_id": agent["id"]}})
    ).json()["id"]
    effects = []

    async def execute(source, sql):
        effects.append((source.id, sql))
        return {"command_status": "UPDATE 1", "returned_rows": False}

    monkeypatch.setattr(agent_tools, "execute_write", execute)
    yield runtime, client, agent, conversation, effects


async def wait_state(runtime, run_id):
    async with asyncio.timeout(5):
        while True:
            row = runtime.store.get(run_id, "local")
            if row["status"] not in {"queued", "running"}:
                return row
            await asyncio.sleep(0.01)


async def finish(runtime, run_id):
    async with asyncio.timeout(5):
        while True:
            row = runtime.store.get(run_id, "local")
            if row["status"] not in {"queued", "running", "waiting_approval"}:
                assert row["status"] == "finished", row["error_code"]
                return row
            await asyncio.sleep(0.01)


async def submit(writes, script):
    runtime, client, _, conversation, _ = writes
    runtime.service.model_factory = script.factory
    response = await client.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={
            "client_request_id": "write-test",
            "prompt": "更新获授权的数据，先让我审批。",
        },
    )
    assert response.status_code == 202, response.text
    return await wait_state(runtime, response.json()["id"])


def write_call(id="write-1", sql=SQL):
    return call("request_database_change", json.dumps({"datasource_id": 1, "sql": sql}), id)


async def decide(client, row, approved=True):
    approval = next(a for a in row["approvals"] if a["decision"] == "pending")
    response = await client.post(
        f"/api/v1/runs/{row['id']}/tool-calls/{approval['call_id']}/approval",
        json={"fingerprint": approval["fingerprint"], "approved": approved},
    )
    assert response.status_code == 200, response.text


async def test_exact_sql_approval_is_durable_and_duplicate_decisions_dispatch_once(writes):
    runtime, client, _, _, effects = writes
    script = Script([write_call()], ["数据库返回 UPDATE 1。"])
    row = await submit(writes, script)
    assert row["status"] == "waiting_approval"
    assert effects == []
    assert row["tool_calls"][0]["arguments"] == {"datasource_id": 1, "sql": SQL}
    assert row["tool_calls"][0]["target"]["user"] == "test"
    await decide(client, row)
    await decide(client, row)
    final = await finish(runtime, row["id"])
    assert effects == [(1, SQL)]
    assert final["tool_calls"][0]["status"] == "succeeded"
    assert final["tool_calls"][0]["result"]["content"]["approval"]["decided_by"] == "local"
    assert len(script.requests) == 2


async def test_denial_is_a_tool_result_and_same_action_does_not_ask_again(writes):
    runtime, client, _, _, effects = writes
    row = await submit(writes, Script([write_call()], [write_call("retry")], ["没有执行写入。 "]))
    await decide(client, row, False)
    final = await finish(runtime, row["id"])
    assert effects == []
    assert len(final["approvals"]) == 1
    assert next(c for c in final["tool_calls"] if c["call_id"] == "retry")["status"] == "denied"


@pytest.mark.parametrize(
    "change", ["password", "host", "database", "access_level", "status", "grant", "policy"]
)
async def test_changed_target_or_revoked_permission_cannot_use_old_approval(writes, change):
    runtime, client, agent, _, effects = writes
    row = await submit(writes, Script([write_call()], ["原批准未执行。 "]))
    assert row["status"] == "waiting_approval"
    with runtime.sessions.begin() as db:
        if change == "grant":
            db.get(Agent, agent["id"]).datasources = []
        elif change == "policy":
            upsert_setting(db, "sql_allow_mutating", False)
        else:
            setattr(db.get(DataSource, 1), change, "inactive" if change == "status" else "changed")
    await decide(client, row)
    final = await wait_state(runtime, row["id"])
    # Wait for the decided batch to actually resume, not the old paused state.
    if final["status"] == "waiting_approval":
        final = await finish(runtime, row["id"])
    assert final["status"] == "finished"
    assert effects == []


async def test_prepare_failure_returns_to_agent_and_corrected_sql_needs_new_approval(
    writes, monkeypatch
):
    runtime, client, _, _, effects = writes

    async def execute(source, sql):
        if "missing_column" in sql:
            raise WriteNotExecutedError(
                "Unknown column 'missing_column'", stage="statement_preparation"
            )
        effects.append((source.id, sql))
        return {"command_status": "UPDATE 1"}

    monkeypatch.setattr(agent_tools, "execute_write", execute)
    row = await submit(
        writes,
        Script(
            [write_call(sql="UPDATE counters SET missing_column = 1")],
            [write_call("corrected")],
            ["更正后的语句执行了一次。"],
        ),
    )
    await decide(client, row)
    async with asyncio.timeout(5):
        while len(runtime.store.get(row["id"], "local")["approvals"]) < 2:
            await asyncio.sleep(0.01)
    row = await wait_state(runtime, row["id"])
    assert effects == []
    failed = next(c for c in row["tool_calls"] if c["call_id"] == "write-1")
    assert failed["status"] == "failed"
    failure = json.loads(failed["result"]["content"])
    assert failure == {
        "outcome": "not_executed",
        "datasource_id": 1,
        "database": "test",
        "observed_at": failure["observed_at"],
        "execution_stage": "statement_preparation",
        "business_statement_dispatched": False,
        "database_error": "Unknown column 'missing_column'",
        "cause": "not_determined_by_this_receipt",
    }
    await decide(client, row)
    await finish(runtime, row["id"])
    assert effects == [(1, SQL)]


async def test_lost_execution_reply_is_unknown_and_does_not_retry(writes, monkeypatch):
    runtime, client, _, _, effects = writes

    async def lose_reply(source, sql):
        effects.append((source.id, sql))
        raise WriteOutcomeUnknownError("Connection lost after sending SQL")

    monkeypatch.setattr(agent_tools, "execute_write", lose_reply)
    row = await submit(writes, Script([write_call()]))
    await decide(client, row)
    async with asyncio.timeout(5):
        while True:
            final = runtime.store.get(row["id"], "local")
            if final["status"] == "interrupted":
                break
            await asyncio.sleep(0.01)
    assert effects == [(1, SQL)]
    assert final["error_code"] == "reconciliation_required"
    assert final["tool_calls"][0]["status"] == "outcome_unknown"
    await decide(client, row)  # Repeated approval cannot revive a dispatched call.
    assert effects == [(1, SQL)]


async def test_write_policy_filters_schemas_and_no_confirmation_bypass(writes):
    runtime, _, _, conversation, effects = writes
    with runtime.sessions.begin() as db:
        upsert_setting(db, "ai_action_confirmation_bypass", True)
    row = await submit(writes, Script([write_call()], ["取消。 "]))
    assert row["status"] == "waiting_approval" and effects == []
    runtime.service.cancel(row["id"], "local")
    with runtime.sessions.begin() as db:
        upsert_setting(db, "sql_allow_mutating", False)
    assert "request_database_change" not in runtime.resolve(conversation, "local", {}).tool_names
    assert "request_database_change" not in runtime.capabilities(row)


async def test_conversation_auto_approval_is_narrow_expiring_and_audited(writes):
    runtime, client, agent, conversation, effects = writes
    grant = {
        "tool_name": "request_database_change",
        "agent_id": agent["id"],
        "datasource_id": 1,
        "expires_at": time.time() + 30 * 60,
    }
    response = await client.patch(
        f"/api/v1/conversations/{conversation}", json={"scene": {"auto_approval": grant}}
    )
    assert response.status_code == 200, response.text
    row = await submit(writes, Script([write_call()], ["已执行一次。 "]))
    assert row["status"] == "finished"
    assert effects == [(1, SQL)]
    assert row["approvals"] == [
        {
            **row["approvals"][0],
            "decision": "approved",
            "decided_by": "conversation_auto_approval",
        }
    ]
    event = next(
        item
        for item in runtime.store.read_events(row["id"], "local")
        if item["kind"] == "approval_decided"
    )
    assert event["payload"]["automatic"] is True
    assert event["payload"]["policy"]["datasource_id"] == 1
    assert "only to the request_database_change tool" in row["definition"]["instructions"]


async def test_expired_or_overlong_auto_approval_cannot_bypass_confirmation(writes):
    runtime, client, agent, conversation, effects = writes
    base = {
        "tool_name": "request_database_change",
        "agent_id": agent["id"],
        "datasource_id": 1,
    }
    overlong = await client.patch(
        f"/api/v1/conversations/{conversation}",
        json={"scene": {"auto_approval": {**base, "expires_at": time.time() + 3600}}},
    )
    assert overlong.status_code == 422
    expired = await client.patch(
        f"/api/v1/conversations/{conversation}",
        json={"scene": {"auto_approval": {**base, "expires_at": time.time() - 1}}},
    )
    assert expired.status_code == 200
    row = await submit(writes, Script([write_call()], ["取消。 "]))
    assert row["status"] == "waiting_approval"
    assert effects == []
    runtime.service.cancel(row["id"], "local")


async def test_scope_change_revokes_grant_and_run_payload_cannot_create_one(writes):
    runtime, client, agent, conversation, effects = writes
    grant = {
        "tool_name": "request_database_change",
        "agent_id": agent["id"],
        "datasource_id": 1,
        "expires_at": time.time() + 30 * 60,
    }
    enabled = await client.patch(
        f"/api/v1/conversations/{conversation}", json={"scene": {"auto_approval": grant}}
    )
    assert enabled.status_code == 200
    changed = await client.patch(
        f"/api/v1/conversations/{conversation}", json={"scene": {"datasource_ids": []}}
    )
    assert changed.json()["scene"]["auto_approval"] is None
    restored = await client.patch(
        f"/api/v1/conversations/{conversation}", json={"scene": {"datasource_ids": [1]}}
    )
    assert restored.json()["scene"]["auto_approval"] is None
    runtime.service.model_factory = Script([write_call()], ["取消。 "]).factory
    response = await client.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={
            "client_request_id": "payload-grant",
            "prompt": "更新数据",
            "scene": {"auto_approval": grant},
        },
    )
    row = await wait_state(runtime, response.json()["id"])
    assert row["status"] == "waiting_approval"
    assert effects == []
    runtime.service.cancel(row["id"], "local")


async def test_reconciliation_api_continues_unknown_result_without_reusing_approval(
    writes, monkeypatch
):
    runtime, client, _, _, effects = writes

    async def lose_reply(source, sql):
        effects.append((source.id, sql))
        raise WriteOutcomeUnknownError("Reply lost after dispatch")

    monkeypatch.setattr(agent_tools, "execute_write", lose_reply)
    row = await submit(
        writes, Script([write_call()], [write_call("new-action")], ["新动作未获批准。 "])
    )
    await decide(client, row)
    async with asyncio.timeout(5):
        while (current := runtime.store.get(row["id"], "local"))["status"] != "interrupted":
            await asyncio.sleep(0.01)
    resume_path = f"/api/v1/runs/{row['id']}/resume"
    assert (
        await client.post(resume_path, json={"expected_event_seq": current["event_seq"]})
    ).status_code == 409
    tool = current["tool_calls"][0]
    path = f"/api/v1/runs/{row['id']}/tool-calls/{tool['call_id']}/reconciliation"
    body = {
        "fingerprint": tool["fingerprint"],
        "resolution": "succeeded",
        "evidence": "Independent database check: n=1; connection ended.",
        "execution_stopped": True,
    }
    for invalid in (
        {**body, "execution_stopped": "true"},
        {**body, "execution_stopped": False},
        {**body, "evidence": " "},
    ):
        assert (await client.post(path, json=invalid)).status_code == 422
    response = await client.post(path, json=body)
    assert response.status_code == 200
    assert response.json()["verified_by_platform"] is False
    assert (await client.post(path, json=body)).json() == response.json()
    checked = runtime.store.get(row["id"], "local")
    assert checked["status"] == "interrupted" and effects == [(1, SQL)]
    assert (
        await client.post(resume_path, json={"expected_event_seq": checked["event_seq"]})
    ).status_code == 202
    async with asyncio.timeout(5):
        while (continued := runtime.store.get(row["id"], "local"))["status"] != "waiting_approval":
            await asyncio.sleep(0.01)
    assert effects == [(1, SQL)]
    assert len(continued["approvals"]) == 2
    assert (
        next(a for a in continued["approvals"] if a["call_id"] == "new-action")["decision"]
        == "pending"
    )
    await decide(client, row)  # Old approval is idempotent, not authorization for the new call.
    assert effects == [(1, SQL)]
    await decide(client, continued, False)
    await finish(runtime, row["id"])
    assert effects == [(1, SQL)]


@pytest.mark.parametrize(
    "sql",
    [
        SQL,
        "INSERT INTO counters(id,n) VALUES(1,0)",
        "DELETE FROM counters WHERE id=1",
        "CREATE TABLE t(id INT)",
        "ALTER TABLE t ADD COLUMN n INT",
        "DROP TABLE t",
        "TRUNCATE TABLE t",
        "GRANT SELECT ON t TO reader",
        "REVOKE SELECT ON t FROM reader",
    ],
)
def test_write_statement_boundary_accepts_supported_single_statements(sql):
    validate_write_sql(sql, "mysql")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "UPDATE t SET a=1; DELETE FROM t",
        "COMMIT",
        "START TRANSACTION",
        "SET autocommit=0",
        "USE other_db",
        "CALL proc()",
        "/*! DELETE FROM t */",
        "",
    ],
)
def test_write_statement_boundary_rejects_scripts_and_session_control(sql):
    with pytest.raises(ValueError):
        validate_write_sql(sql, "mysql")


@pytest.mark.parametrize("engine", ["mysql", "postgresql"])
@pytest.mark.parametrize("stage", ["connect", "prepare", "execute", "success", "cancel"])
async def test_executor_never_retries_and_distinguishes_dispatch_boundary(
    monkeypatch, engine, stage
):
    attempts, closed = [], []

    async def execute(sql, *args):
        attempts.append((sql, args))
        preparation = sql.startswith("PREPARE") or sql == "pg-prepare"
        if stage == ("prepare" if preparation else "execute"):
            raise RuntimeError("secret-password interrupted")
        if stage == "cancel" and not preparation:
            raise asyncio.CancelledError
        return "UPDATE 1"

    cursor = SimpleNamespace(execute=execute, description=None, rowcount=1)

    async def get_cursor(_type):
        return cursor

    async def prepare(sql):
        assert sql == SQL
        return await execute("pg-prepare")

    connection = SimpleNamespace(
        cursor=get_cursor,
        execute=execute,
        prepare=prepare,
        close=lambda: closed.append(True),
        terminate=lambda: closed.append(True),
    )

    async def connect(**kwargs):
        assert kwargs["password"] == "secret-password"
        if stage == "connect":
            raise RuntimeError("secret-password connection refused")
        return connection

    monkeypatch.setattr(write_executor.aiomysql, "connect", connect)
    monkeypatch.setattr(write_executor.asyncpg, "connect", connect)
    source = SimpleNamespace(
        db_type=engine,
        host="fixture",
        port=1,
        user="fixture",
        password="secret-password",
        database="fixture",
    )
    if stage == "success":
        assert (await write_executor.execute_write(source, SQL))["returned_rows"] is False
    else:
        error = (
            asyncio.CancelledError
            if stage == "cancel"
            else (WriteOutcomeUnknownError if stage == "execute" else WriteNotExecutedError)
        )
        with pytest.raises(error) as raised:
            await write_executor.execute_write(source, SQL)
        assert "secret-password" not in str(raised.value)
        if isinstance(raised.value, WriteNotExecutedError):
            assert raised.value.stage == (
                "connection" if stage == "connect" else "statement_preparation"
            )
    assert len(attempts) == (0 if stage == "connect" else 1 if stage == "prepare" else 2)
    assert len(closed) == (0 if stage == "connect" else 1)
