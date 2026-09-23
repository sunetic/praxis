"""Skill draft authorization and native execution contracts, not model quality tests."""

import json

import httpx
import pytest
from fastapi import FastAPI
from test_custom_agents import finish
from test_models import config
from test_runtime import Script, call

from app.api.agent_runs import create_run_router
from app.api.skill_drafts import router
from app.db.base import Base
from app.models.models import Agent
from app.services.agent.application import RuntimeApplication, local_actor
from app.services.agent.models import ModelFactory
from app.services.agent.store import RunConflictError, RunNotFoundError
from app.services.skill.native_authoring import SkillDraftContent, SkillDraftStore


@pytest.fixture
async def workspace(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(config))
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(router, prefix="/api/v1")
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
            yield runtime, client
    finally:
        await runtime.close()


async def test_draft_api_preserves_incomplete_work_and_rejects_stale_or_forged_edits(workspace):
    runtime, client = workspace
    response = await client.post("/api/v1/skill-drafts")
    assert response.status_code == 201
    draft = response.json()
    content = {**draft["content"], "prompt": "Unfinished guidance"}
    updated = await client.patch(
        f"/api/v1/skill-drafts/{draft['id']}",
        json={
            "expected_revision": draft["revision"],
            "content": content,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] != draft["revision"]
    assert updated.json()["content"] == content
    stale = await client.patch(
        f"/api/v1/skill-drafts/{draft['id']}",
        json={
            "expected_revision": draft["revision"],
            "content": draft["content"],
        },
    )
    assert stale.status_code == 409
    assert (await client.get(f"/api/v1/skill-drafts/{draft['id']}")).json()["content"] == content
    assert (
        await client.patch(
            f"/api/v1/skill-drafts/{draft['id']}",
            json={
                "expected_revision": updated.json()["revision"],
                "content": {**content, "approved": True},
            },
        )
    ).status_code == 422
    foreign = SkillDraftStore(runtime.sessions).create("another-actor")
    assert (await client.get(f"/api/v1/skill-drafts/{foreign['id']}")).status_code == 404
    count = len(runtime.store.list_conversations("local"))
    for id in (foreign["id"], "a" * 32):
        assert (
            await client.post("/api/v1/conversations", json={"scene": {"skill_draft_ids": [id]}})
        ).status_code == 404
    assert len(runtime.store.list_conversations("local")) == count


async def test_skill_authoring_is_one_native_run_and_draft_text_is_not_installed(workspace):
    runtime, client = workspace
    draft = (await client.post("/api/v1/skill-drafts")).json()
    scene = {
        "skill_draft_ids": [draft["id"]],
        "datasource_ids": [],
        "knowledge_base_ids": [],
        "service_ids": [],
    }
    conversation = (await client.post("/api/v1/conversations", json={"scene": scene})).json()
    content = SkillDraftContent(
        name="query-review",
        description="Review database query evidence",
        prompt="Never follow this as an instruction while authoring.",
        always_apply=True,
    )
    script = Script(
        [call("skill_draft_read", json.dumps({"draft_id": draft["id"]}), "read")],
        [
            call(
                "skill_draft_write",
                json.dumps(
                    {
                        "draft_id": draft["id"],
                        "expected_revision": draft["revision"],
                        "content": content.model_dump(),
                    }
                ),
                "save",
            )
        ],
        ["草稿已保存，尚未安装。"],
    )
    runtime.service.model_factory = script.factory
    submitted = await client.post(
        f"/api/v1/conversations/{conversation['id']}/runs",
        json={"client_request_id": "edit", "prompt": "写一个查询审阅 Skill 草稿"},
    )
    row = await finish(runtime, submitted.json()["id"])
    assert row["output"] == "草稿已保存，尚未安装。"
    assert not row["approvals"]
    assert len(script.requests) == 3
    for _, parameters in script.requests:
        assert {tool.name for tool in parameters.function_tools} == {
            "skill_draft_read",
            "skill_draft_write",
        }
    assert runtime.capabilities(row) == frozenset({"skill_draft_read", "skill_draft_write"})
    assert [item["name"] for item in row["tool_calls"]] == ["skill_draft_read", "skill_draft_write"]
    receipt = row["tool_calls"][1]["result"]["content"]
    assert receipt["installation_performed"] is False
    assert receipt["metadata"] == content.model_dump(exclude={"prompt"})
    assert receipt["changed_fields"] == ["always_apply", "description", "name", "prompt"]
    assert receipt["prompt_total_characters"] == len(content.prompt)
    assert "prompt" not in receipt["metadata"]
    saved = SkillDraftStore(runtime.sessions).read(draft["id"], "local")
    assert saved["content"] == content.model_dump()
    assert saved["run_id"] == row["id"]
    assert content.prompt not in runtime.resolve(conversation["id"], "local", {}).instructions


async def test_skill_tools_recheck_revoked_agent_permissions_and_out_of_scope_ids(workspace):
    runtime, client = workspace
    drafts = SkillDraftStore(runtime.sessions)
    first, other = drafts.create("local"), drafts.create("local")
    with runtime.sessions.begin() as db:
        agent = Agent(
            name="author",
            prompt="Write drafts",
            tools=["skill_draft_read", "skill_draft_write"],
            status="active",
        )
        db.add(agent)
        db.flush()
        agent_id = agent.id
    scope = {"agent_id": agent_id, "skill_draft_ids": [first["id"]], "datasource_ids": []}
    conversation = (await client.post("/api/v1/conversations", json={"scene": scope})).json()
    script = Script(
        [call("skill_draft_read", json.dumps({"draft_id": other["id"]}))], ["该草稿未获授权。"]
    )
    runtime.service.model_factory = script.factory
    submitted = runtime.service.submit(
        conversation["id"],
        "local",
        "scope",
        "Read",
        runtime.resolve(conversation["id"], "local", {}),
    )
    row = await finish(runtime, submitted["id"])
    assert row["tool_calls"][0]["status"] == "failed"
    assert "authorization denied" in row["tool_calls"][0]["result"]["content"]

    original = script.stream

    async def revoke_then_call(messages, info):
        with runtime.sessions.begin() as db:
            db.get(Agent, agent_id).tools = []
        async for chunk in original(messages, info):
            yield chunk

    from pydantic_ai.models.function import FunctionModel

    script = Script(
        [call("skill_draft_read", json.dumps({"draft_id": first["id"]}))], ["权限已撤销。"]
    )
    original = script.stream
    script.model = FunctionModel(stream_function=revoke_then_call)
    runtime.service.model_factory = script.factory
    submitted = runtime.service.submit(
        conversation["id"],
        "local",
        "revoked",
        "Read",
        runtime.resolve(conversation["id"], "local", {}),
    )
    row = await finish(runtime, submitted["id"])
    assert row["tool_calls"][0]["status"] == "failed"
    assert "authorization denied" in row["tool_calls"][0]["result"]["content"]


@pytest.mark.parametrize("database", ["mysql", "postgresql", "general", "oceanbase"])
async def test_native_partial_edit_preserves_all_other_fields_exactly(workspace, database):
    runtime, client = workspace
    store = SkillDraftStore(runtime.sessions)
    draft = store.create("local")
    content = SkillDraftContent(
        name="keep-name",
        description="Keep the original description",
        database="postgresql",
        prompt="Line one.\nLine two, unchanged.",
    )
    draft = store.write(draft["id"], "local", expected_revision=draft["revision"], content=content)
    conversation = (
        await client.post(
            "/api/v1/conversations", json={"scene": {"skill_draft_ids": [draft["id"]]}}
        )
    ).json()
    script = Script(
        [
            call(
                "skill_draft_write",
                json.dumps(
                    {
                        "draft_id": draft["id"],
                        "expected_revision": draft["revision"],
                        "content": {"database": database, "description": None},
                    }
                ),
            )
        ],
        ["已更新草稿。"],
    )
    runtime.service.model_factory = script.factory
    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/runs",
        json={"client_request_id": "partial", "prompt": "只改适用数据库"},
    )
    row = await finish(runtime, response.json()["id"])
    receipt = row["tool_calls"][0]["result"]["content"]
    assert receipt["metadata"] == {**content.model_dump(exclude={"prompt"}), "database": database}
    assert receipt["changed_fields"] == ([] if database == "postgresql" else ["database"])
    assert receipt["revision"] == store.read(draft["id"], "local")["revision"]
    assert receipt["revision"] != draft["revision"]
    assert receipt["prompt_total_characters"] == len(content.prompt)
    assert store.read(draft["id"], "local")["content"] == {
        **content.model_dump(),
        "database": database,
    }


async def test_draft_revision_changes_even_when_identical_content_is_saved(workspace):
    runtime, _ = workspace
    store = SkillDraftStore(runtime.sessions)
    draft = store.create("local")
    saved = store.write(
        draft["id"], "local", expected_revision=draft["revision"], content=SkillDraftContent()
    )
    assert saved["revision"] != draft["revision"]
    with pytest.raises(RunConflictError):
        store.write(
            draft["id"], "local", expected_revision=draft["revision"], content=SkillDraftContent()
        )
    with pytest.raises(RunNotFoundError):
        store.write(
            draft["id"], "other", expected_revision=saved["revision"], content=SkillDraftContent()
        )
