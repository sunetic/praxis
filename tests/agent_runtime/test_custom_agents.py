"""Custom Agent configuration and sessions use the same native runtime as Chat."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from test_models import config
from test_runtime import Script, call

from app.api.agent_runs import create_run_router
from app.api.agents import router as agents_router
from app.api.schedules import router as schedules_router
from app.db.base import Base
from app.db.database import get_db
from app.models.models import Agent, DataSource
from app.services.agent.application import RuntimeApplication, local_actor
from app.services.agent.models import ModelFactory


@pytest.fixture
async def custom_app(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    with store.sessions.begin() as db:
        db.add_all(
            [
                DataSource(
                    id=id,
                    name=f"source-{id}",
                    host="localhost",
                    port=5432,
                    db_type="postgresql",
                    user="test",
                    database="test",
                    status="active" if id < 3 else "inactive",
                )
                for id in (1, 2, 3)
            ]
        )
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(config))
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(schedules_router, prefix="/api/v1")
    app.include_router(
        create_run_router(
            runtime.service, actor_dependency=local_actor, resolve_definition=runtime.resolve
        )
    )

    def session():
        with store.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    await runtime.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield runtime, client
    finally:
        await runtime.close()


async def create(client, **overrides):
    response = await client.post(
        "/api/v1/agents",
        json={
            "name": "Native custom Agent",
            "prompt": "Use a concise answer and preserve evidence.",
            "tools": ["list_datasources"],
            "datasource_ids": [1],
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def finish(runtime, id):
    async with asyncio.timeout(5):
        while True:
            row = runtime.store.get(id, "local")
            if row["status"] not in {"queued", "running"}:
                assert row["status"] == "finished", row["error_code"]
                return row
            await asyncio.sleep(0.01)


async def test_configuration_and_native_session_preserve_authority_and_history(custom_app):
    runtime, client = custom_app
    agent = await create(client)
    assert agent["datasource_ids"] == [1]
    assert (await client.get("/api/v1/agents")).json()[0]["datasource_ids"] == [1]
    response = await client.post("/api/v1/conversations", json={"scene": {"agent_id": agent["id"]}})
    assert response.status_code == 201
    conversation = response.json()["id"]
    assert isinstance(conversation, str)
    assert runtime.store.list_runs(conversation, "local") == []
    script = Script([call("list_datasources")], ["已列出获授权的数据源。"], ["刚才只有 source-1。"])
    runtime.service.model_factory = script.factory
    for index, prompt in enumerate(("列出可访问的数据源", "刚才有哪些？不调用工具")):
        response = await client.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={
                "client_request_id": str(index),
                "prompt": prompt,
            },
        )
        assert response.status_code == 202
        row = await finish(runtime, response.json()["id"])
        assert row["definition"]["scope"]["datasource_ids"] == [1]
        assert row["definition"]["tool_names"] == ["list_datasources"]
        assert agent["prompt"] in row["definition"]["instructions"]
        if index == 0:
            assert [item["id"] for item in row["tool_calls"][0]["result"]["content"]] == [1]
    assert len(script.requests) == 3
    assert len(script.requests[-1][0]) > len(script.requests[0][0])
    assert (await client.post(f"/api/v1/agents/{agent['id']}/run", json={})).status_code == 404


@pytest.mark.parametrize(
    "scope", [{"datasource_ids": [2]}, {"datasource_ids": [3]}, {"unknown": 1}]
)
async def test_invalid_scene_creates_no_conversation(custom_app, scope):
    runtime, client = custom_app
    agent = await create(client)
    before = runtime.store.list_conversations("local")
    response = await client.post(
        "/api/v1/conversations", json={"scene": {"agent_id": agent["id"], **scope}}
    )
    assert response.status_code == 422
    assert runtime.store.list_conversations("local") == before


@pytest.mark.parametrize(
    "patch",
    [
        {"tools": ["execute_sql"]},
        {"datasource_ids": [999]},
        {"datasource_ids": [3]},
    ],
)
async def test_bad_configuration_is_rejected_before_saving(custom_app, patch):
    runtime, client = custom_app
    response = await client.post("/api/v1/agents", json={"name": "bad", "prompt": "test", **patch})
    assert response.status_code == 422
    with runtime.sessions() as db:
        assert db.query(Agent).count() == 0


async def test_empty_scope_is_not_all_and_resource_revocation_affects_existing_conversation(
    custom_app,
):
    runtime, client = custom_app
    agent = await create(client)
    response = await client.post(
        "/api/v1/conversations",
        json={
            "scene": {
                "agent_id": agent["id"],
                "datasource_ids": [],
            }
        },
    )
    conversation = response.json()["id"]
    assert runtime.resolve(conversation, "local", {}).scope["datasource_ids"] == []
    response = await client.patch(
        f"/api/v1/agents/{agent['id']}", json={"datasource_ids": [], "tools": []}
    )
    assert response.status_code == 200
    assert runtime.resolve(conversation, "local", {}).tool_names == frozenset()
    assert (
        await client.patch(
            f"/api/v1/conversations/{conversation}", json={"scene": {"datasource_ids": [1]}}
        )
    ).status_code == 422
    await client.patch(f"/api/v1/agents/{agent['id']}", json={"status": "inactive"})
    assert (
        await client.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={"client_request_id": "x", "prompt": "test"},
        )
    ).status_code == 404


async def test_failed_update_preserves_tool_and_resource_configuration(custom_app):
    _, client = custom_app
    agent = await create(client)
    response = await client.patch(
        f"/api/v1/agents/{agent['id']}", json={"tools": [], "datasource_ids": [999]}
    )
    assert response.status_code == 422
    saved = (await client.get(f"/api/v1/agents/{agent['id']}")).json()
    assert saved["tools"] == ["list_datasources"] and saved["datasource_ids"] == [1]
