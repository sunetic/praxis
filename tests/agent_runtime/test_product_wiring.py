import asyncio
from dataclasses import asdict

import httpx
import pytest
from fastapi import FastAPI
from pydantic_ai.exceptions import ToolFailed
from test_models import config
from test_runtime import Script

from app.api.agent_runs import create_run_router
from app.db.base import Base
from app.models.models import Agent, DataSource
from app.services.agent.application import (
    RuntimeApplication,
    local_actor,
    scoped_tools,
)
from app.services.agent.models import ModelFactory
from app.services.agent.store import RunNotFoundError
from app.services.datasource.agent_tools import database_tools, validate_read_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM example LIMIT 10",
        "SHOW TABLES",
        "DESCRIBE example",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    ],
)
def test_native_read_tool_accepts_read_statements(sql):
    validate_read_sql(sql, "mysql")


def test_native_read_tool_explains_single_statement_boundary():
    with pytest.raises(ToolFailed) as caught:
        validate_read_sql("SELECT 1; SELECT 2; SELECT 3", "mysql")

    message = str(caught.value)
    assert "exactly one SQL statement per call" in message
    assert "received 3" in message
    assert "separate query_database call" in message


def test_native_read_tool_schema_tells_model_about_single_statement_boundary():
    definition = database_tools(lambda: None)["query_database"].tool.tool_def

    assert "exactly one SQL statement" in definition.description
    sql_schema = definition.parameters_json_schema["properties"]["sql"]
    assert "Exactly one SQL statement" in sql_schema["description"]
    assert "Never combine statements" in sql_schema["description"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM example",
        "SELECT 1; DROP TABLE example",
        "SELECT * FROM x FOR UPDATE",
        "SELECT * INTO OUTFILE '/tmp/stolen' FROM x",
        "CALL mutate()",
        "SET autocommit=0",
        "WITH x AS (DELETE FROM example RETURNING *) SELECT * FROM x",
        "CREATE TABLE t (id INT)",
        "SELECT dangerous_udf()",
        "SELECT * FROM x /*! INTO OUTFILE '/tmp/leak' */",
    ],
)
def test_native_read_tool_rejects_write_or_session_control_statements(sql):
    with pytest.raises(ToolFailed):
        validate_read_sql(sql, "mysql")


async def test_native_model_factory_reuses_config_and_keeps_inflight_client_on_rotation():
    current = [config()]
    factory = ModelFactory(lambda: current[0])
    first = await factory.get_model()
    assert await factory.get_model() is first
    current[0] = config(api_key="rotated-secret")
    second = await factory.get_model()
    assert second is not first
    assert not first.provider.client.is_closed()
    await factory.close()
    assert first.provider.client.is_closed()
    assert second.provider.client.is_closed()
    with pytest.raises(RuntimeError, match="closed"):
        await factory.get_model()


async def test_product_conversation_and_followup_use_native_messages(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    script = Script(["第一点：原子性。第二点：一致性。"], ["第二点是一致性。"])
    runtime.service.model_factory = script.factory
    app = FastAPI()
    app.include_router(
        create_run_router(
            runtime.service, actor_dependency=local_actor, resolve_definition=runtime.resolve
        )
    )
    await runtime.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            conversation = (await client.post("/api/v1/conversations", json={})).json()
            for index, prompt in enumerate(["说两点", "只解释第二点"]):
                response = await client.post(
                    f"/api/v1/conversations/{conversation['id']}/runs",
                    json={"client_request_id": str(index), "prompt": prompt},
                )
                assert response.status_code == 202
                run_id = response.json()["id"]
                async with asyncio.timeout(5):
                    while runtime.store.get(run_id, "local")["status"] not in {
                        "finished",
                        "failed",
                    }:
                        await asyncio.sleep(0.01)
                assert runtime.store.get(run_id, "local")["status"] == "finished"
            history = (await client.get(f"/api/v1/conversations/{conversation['id']}/runs")).json()
            assert len(history) == 2
            assert history[1]["prompt"] == "只解释第二点"
            assert len(script.requests[1][0]) == 3
            schemas = script.requests[0][1].function_tools
            assert schemas == []  # No authorized resources means no usable tools.
            assert all(
                "_runtime" not in tool.parameters_json_schema["properties"] for tool in schemas
            )
    finally:
        await runtime.close()


def test_product_scene_empty_agent_tools_stays_empty_and_bindings_are_enforced(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("local-conversation", "local")
    with store.sessions.begin() as db:
        datasource = DataSource(
            name="private", host="localhost", port=3306, database="sample", status="active"
        )
        agent = Agent(name="no-tools", prompt="Explain only", tools=[], skills=[], status="active")
        db.add_all([datasource, agent])
        db.flush()
        datasource_id, agent_id = datasource.id, agent.id
    definition = runtime.resolve("local-conversation", "local", {"agent_id": agent_id})
    assert definition.tool_names == frozenset()
    assert definition.scope["datasource_ids"] == []
    with pytest.raises(ValueError, match="unauthorized datasource"):
        runtime.resolve(
            "local-conversation", "local", {"agent_id": agent_id, "datasource_ids": [datasource_id]}
        )


def test_scene_tools_intersect_resources_and_custom_configuration(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(config))
    with store.sessions.begin() as db:
        source = DataSource(
            name="allowed", host="localhost", port=5432, database="sample", status="active"
        )
        agent = Agent(
            name="read-only",
            prompt="Read",
            tools=["list_datasources"],
            skills=[],
            status="active",
            datasources=[source],
        )
        db.add_all([source, agent])
        db.flush()
        source_id, agent_id = source.id, agent.id
    for scene, expected in (
        ({}, frozenset()),
        ({"datasource_ids": []}, frozenset()),
        ({"agent_id": agent_id}, frozenset({"list_datasources"})),
        ({"agent_id": agent_id, "datasource_ids": []}, frozenset()),
        ({"agent_id": agent_id, "datasource_ids": [source_id]}, frozenset({"list_datasources"})),
    ):
        definition = runtime.resolve(None, "local", scene)
        assert definition.tool_names == expected
        assert runtime.capabilities({"definition": asdict(definition)}) == expected


def test_resource_filter_preserves_explicit_resource_independent_tools():
    assert scoped_tools(frozenset({"resource_independent", "query_database"}), {}) == frozenset(
        {"resource_independent"}
    )
    assert scoped_tools(frozenset(), {"datasource_ids": [1]}) == frozenset()


async def test_conversation_scope_is_persisted_validated_and_does_not_change_submitted_runs(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    app = FastAPI()
    app.include_router(
        create_run_router(
            runtime.service, actor_dependency=local_actor, resolve_definition=runtime.resolve
        )
    )
    with store.sessions.begin() as db:
        source = DataSource(
            name="allowed", host="localhost", port=3306, database="sample", status="active"
        )
        db.add(source)
        db.flush()
        source_id = source.id
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            conversation = (await client.post("/api/v1/conversations", json={})).json()
            url = f"/api/v1/conversations/{conversation['id']}"
            assert (
                await client.patch(url, json={"scene": {"datasource_ids": []}})
            ).status_code == 200
            submitted = await client.post(
                f"{url}/runs", json={"client_request_id": "scope", "prompt": "解释"}
            )
            assert submitted.status_code == 202
            assert (
                runtime.store.get(submitted.json()["id"], "local")["definition"]["scope"][
                    "datasource_ids"
                ]
                == []
            )
            assert (
                await client.patch(url, json={"scene": {"datasource_ids": [source_id + 1]}})
            ).status_code == 422
            assert (await client.get(url)).json()["scene"]["datasource_ids"] == []
            assert (
                await client.patch(url, json={"scene": {"datasource_ids": None}})
            ).status_code == 200
            assert runtime.resolve(conversation["id"], "local", {}).scope["datasource_ids"] == []
            selected = runtime.resolve(conversation["id"], "local", {"datasource_ids": [source_id]})
            assert selected.scope["datasource_ids"] == [source_id]
            assert '"name": "allowed"' in selected.instructions
            assert "do not ask the user to select it again" in selected.instructions
            assert (
                runtime.store.get(submitted.json()["id"], "local")["definition"]["scope"][
                    "datasource_ids"
                ]
                == []
            )
            with pytest.raises(RunNotFoundError):
                runtime.store.update_conversation(conversation["id"], "other", scene={})
    finally:
        await runtime.close()
