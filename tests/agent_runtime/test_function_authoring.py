import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic_ai.models.function import FunctionModel
from test_models import config
from test_runtime import Script, call, returns

from app.api.agent_runs import create_run_router
from app.api.functions import router as functions_router
from app.db.base import Base
from app.models.models import Agent, Function, FunctionRelease
from app.services.agent.application import RuntimeApplication, local_actor
from app.services.agent.models import ModelFactory
from app.services.function.native_authoring import (
    AuthoringError,
    FunctionAuthoringStore,
    static_checks,
)


@pytest.fixture
def authoring(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    with store.sessions.begin() as db:
        function = Function(name="Test draft", draft_code="", draft_dependencies={})
        db.add(function)
        db.flush()
        function_id = function.id
    return FunctionAuthoringStore(store.sessions), function_id


def save(authoring, code="def main(payload, context):\n    return payload\n"):
    store, function_id = authoring
    current = store.read(function_id)
    return store.write(
        function_id,
        expected_revision=current["revision_hash"],
        code=code,
        dependencies={},
        run_id=None,
    )


def report(authoring, revision, *, status="passed"):
    store, function_id = authoring
    return store.record_validation(
        function_id,
        revision_id=revision["revision_id"],
        revision_hash=revision["revision_hash"],
        run_id=None,
        checks=[
            *static_checks("def main(payload, context):\n    return payload\n"),
            {"name": "controlled_runtime", "status": status, "executed": status != "unavailable"},
        ],
    )


def test_revision_edits_invalidate_checks_and_do_not_publish(authoring):
    store, function_id = authoring
    first = save(authoring)
    assert store.read(function_id)["changed_files"] == ["main.py"]
    checked = report(authoring, first)
    # Even saving identical bytes produces a new revision with no inherited check.
    second = save(authoring)
    assert second["revision_hash"] == first["revision_hash"]
    assert second["revision_id"] != first["revision_id"]
    assert store.read(function_id)["changed_files"] == []
    assert store.read(function_id)["validation"] is None
    with pytest.raises(AuthoringError, match="exact draft revision"):
        store.publish(
            function_id, expected_revision=first["revision_hash"], validation_id=checked["id"]
        )
    assert store.read(function_id)["current_release_id"] is None


def test_stale_edit_and_check_do_not_overwrite_new_draft(authoring):
    store, function_id = authoring
    first = save(authoring)
    second = save(authoring, "def main(payload, context):\n    return 2\n")
    with pytest.raises(AuthoringError, match="Draft changed"):
        store.write(
            function_id,
            expected_revision=first["revision_hash"],
            code="bad",
            dependencies={},
            run_id=None,
        )
    with pytest.raises(AuthoringError, match="Draft changed"):
        report(authoring, first)
    assert store.read(function_id)["revision_id"] == second["revision_id"]


@pytest.mark.parametrize("status", ["failed", "not_run", "unavailable"])
def test_unexecuted_or_failed_checks_cannot_publish(authoring, status):
    store, function_id = authoring
    revision = save(authoring)
    checked = report(authoring, revision, status=status)
    with pytest.raises(AuthoringError, match="not passed"):
        store.publish(
            function_id, expected_revision=revision["revision_hash"], validation_id=checked["id"]
        )


def test_publication_matches_verified_bytes_is_idempotent_and_preserves_release_on_edit(authoring):
    store, function_id = authoring
    revision = save(authoring)
    checked = report(authoring, revision)
    released = store.publish(
        function_id, expected_revision=revision["revision_hash"], validation_id=checked["id"]
    )
    assert (
        store.publish(
            function_id, expected_revision=revision["revision_hash"], validation_id=checked["id"]
        )["release_id"]
        == released["release_id"]
    )
    save(authoring, "def main(payload, context):\n    return 3\n")
    assert store.read(function_id)["current_release_id"] == released["release_id"]
    with store.sessions() as db:
        assert db.query(FunctionRelease).count() == 1
        assert db.get(FunctionRelease, released["release_id"]).code_snapshot.endswith(
            "return payload\n"
        )


@pytest.mark.parametrize(
    "source",
    ["# def main(payload, context):", "def main(): pass", "def main(payload, context):\n  bad("],
)
def test_static_checks_do_not_confuse_strings_with_a_valid_entrypoint(source):
    assert any(check["status"] != "passed" for check in static_checks(source))


async def test_missing_isolation_is_unavailable_and_never_executes_host_code(tmp_path, monkeypatch):
    from app.services.function import isolated_probe

    monkeypatch.setattr(isolated_probe.shutil, "which", lambda _: None)
    marker = tmp_path / "must-not-exist"
    result = await isolated_probe.controlled_probe(f"open({str(marker)!r}, 'w').write('bad')", {})
    assert result["status"] == "unavailable" and result["executed"] is False
    assert not marker.exists()


async def test_platform_publication_cannot_bypass_revision_checks(authoring):
    from app.services.platform.object_tools import ObjectToolError, ObjectToolService

    store, function_id = authoring
    revision = save(authoring)
    checked = report(authoring, revision, status="unavailable")
    service = ObjectToolService(session_factory=store.sessions)
    with pytest.raises(ObjectToolError, match="not passed"):
        await service.operate(
            object_type="function",
            action="release",
            object_id=function_id,
            payload={
                "expected_revision": revision["revision_hash"],
                "validation_id": checked["id"],
            },
        )
    assert store.read(function_id)["current_release_id"] is None


async def test_authoring_scope_does_not_grant_tools_to_an_empty_custom_agent(store, authoring):
    _, function_id = authoring
    with store.sessions.begin() as db:
        agent = Agent(name="explain", prompt="Explain only", tools=[], skills=[], status="active")
        db.add(agent)
        db.flush()
        agent_id = agent.id
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    try:
        runtime.store.create_conversation("explain", "local")
        definition = runtime.resolve(
            "explain", "local", {"agent_id": agent_id, "function_ids": [function_id]}
        )
        assert not definition.tool_names
    finally:
        await runtime.close()


async def test_function_outside_scene_is_denied_before_writing(store, authoring):
    author, allowed_id = authoring
    with store.sessions.begin() as db:
        forbidden = Function(name="outside", draft_code="", draft_dependencies={})
        db.add(forbidden)
        db.flush()
        forbidden_id = forbidden.id
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("scoped", "local", scene={"function_ids": [allowed_id]})
    script = Script(
        [
            call(
                "function_write",
                json.dumps(
                    {
                        "function_id": forbidden_id,
                        "expected_revision": author.read(forbidden_id)["revision_hash"],
                        "code": "changed",
                        "dependencies": {},
                    }
                ),
            )
        ],
        ["该对象不在本次授权范围内。"],
    )
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        row = runtime.service.submit(
            "scoped", "local", "request", "编辑已授权函数", runtime.resolve("scoped", "local", {})
        )
        async with asyncio.timeout(10):
            while runtime.store.get(row["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(row["id"], "local")
        assert final["status"] == "finished"
        assert final["tool_calls"][0]["status"] == "failed"
        assert author.read(forbidden_id)["code"] == ""
        assert author.read(forbidden_id)["revision_id"] is None
    finally:
        await runtime.close()


async def test_independent_source_and_contract_reads_do_not_compete_for_a_write_lock(
    store, authoring
):
    _, function_id = authoring
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation(
        "parallel-read", "local", scene={"function_ids": [function_id]}
    )
    args = json.dumps({"function_id": function_id})
    script = Script(
        [
            call("function_read", args, "source", index=0),
            call("function_contract", args, "contract", index=1),
        ],
        ["已读取。"],
    )
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        row = runtime.service.submit(
            "parallel-read",
            "local",
            "read",
            "读取源码和运行契约",
            runtime.resolve("parallel-read", "local", {}),
        )
        async with asyncio.timeout(10):
            while runtime.store.get(row["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(row["id"], "local")
        assert final["status"] == "finished"
        assert len(final["tool_calls"]) == 2
        assert all(item["status"] == "succeeded" for item in final["tool_calls"])
        assert len(script.requests) == 2
    finally:
        await runtime.close()


@pytest.mark.parametrize("change_after_approval", [False, True])
async def test_native_publication_approval_is_exact_and_does_not_repeat(
    store, authoring, change_after_approval
):
    author, function_id = authoring
    revision = save(authoring)
    checked = report(authoring, revision)
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation("publish", "local", scene={"function_ids": [function_id]})
    script = Script(
        [
            call(
                "function_publish",
                json.dumps(
                    {
                        "function_id": function_id,
                        "expected_revision": revision["revision_hash"],
                        "validation_id": checked["id"],
                    }
                ),
                "publish-once",
            )
        ],
        ["发布请求已处理。"],
    )
    runtime.service.model_factory = script.factory
    await runtime.start()

    async def settled(run_id):
        async with asyncio.timeout(10):
            while True:
                row = runtime.store.get(run_id, "local")
                if row["status"] not in {"queued", "running"}:
                    return row
                await asyncio.sleep(0.01)

    try:
        definition = runtime.resolve("publish", "local", {})
        row = runtime.service.submit("publish", "local", "publish", "发布已检查的版本", definition)
        paused = await settled(row["id"])
        assert paused["status"] == "waiting_approval"
        assert author.read(function_id)["current_release_id"] is None
        approval = paused["approvals"][0]
        if change_after_approval:
            save(authoring, "def main(payload, context):\n    return 'changed'\n")
        for _ in range(2):
            runtime.service.approve(
                row["id"], "local", approval["call_id"], approval["fingerprint"], True
            )
        # The dispatcher has to claim the resumed run before waiting for terminal.
        async with asyncio.timeout(10):
            while runtime.store.get(row["id"], "local")["status"] == "waiting_approval":
                await asyncio.sleep(0.01)
        finished = await settled(row["id"])
        assert finished["status"] == "finished", finished
        with store.sessions() as db:
            assert db.query(FunctionRelease).count() == (0 if change_after_approval else 1)
        assert len(script.requests) == 2
    finally:
        await runtime.close()


async def test_native_function_build_recovers_from_syntax_error_without_a_second_agent(
    store, authoring, monkeypatch
):
    from app.services.function import isolated_probe

    # Runtime availability is deterministic here, not claimed as a live sandbox test.
    async def unavailable(*_args):
        return {"name": "controlled_runtime", "status": "unavailable", "executed": False}

    monkeypatch.setattr(isolated_probe, "controlled_probe", unavailable)
    author, function_id = authoring
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation(
        "function-chat", "local", scene={"function_ids": [function_id], "datasource_ids": []}
    )
    seen = []

    async def stream(messages, info):
        seen.append(messages)
        assert not info.output_tools
        assert all("finish" not in tool.name for tool in info.function_tools)
        index = len(seen)
        state = author.read(function_id)
        if index == 1:
            yield call(
                "function_write",
                json.dumps(
                    {
                        "function_id": function_id,
                        "expected_revision": state["revision_hash"],
                        "code": "def main(payload, context):\n  return (",
                        "dependencies": {},
                    }
                ),
                "save-bad",
            )
        elif index == 2:
            yield call(
                "function_validate",
                json.dumps(
                    {
                        "function_id": function_id,
                        "expected_revision": state["revision_hash"],
                        "payload": {},
                    }
                ),
                "check-bad",
            )
        elif index == 3:
            assert returns(messages)[-1].content["checks"][0]["status"] == "failed"
            yield call(
                "function_write",
                json.dumps(
                    {
                        "function_id": function_id,
                        "expected_revision": state["revision_hash"],
                        "code": "def main(payload, context):\n    return payload\n",
                        "dependencies": {},
                    }
                ),
                "save-fixed",
            )
        elif index == 4:
            yield call(
                "function_validate",
                json.dumps(
                    {
                        "function_id": function_id,
                        "expected_revision": state["revision_hash"],
                        "payload": {},
                    }
                ),
                "check-fixed",
            )
        else:
            assert index == 5
            yield "草稿已保存，语法检查通过；运行环境不可用，尚未发布。"

    runtime.service.model_factory = AsyncMock(return_value=FunctionModel(stream_function=stream))
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(
        create_run_router(
            runtime.service, actor_dependency=local_actor, resolve_definition=runtime.resolve
        )
    )
    app.include_router(functions_router, prefix="/api/v1")
    await runtime.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/conversations/function-chat/runs",
                json={"client_request_id": "build", "prompt": "写函数并检查，只保存草稿"},
            )
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            async with asyncio.timeout(10):
                while runtime.store.get(run_id, "local")["status"] in {"queued", "running"}:
                    await asyncio.sleep(0.01)
            row = runtime.store.get(run_id, "local")
            assert row["status"] == "finished", row
            assert len(seen) == 5
            result = author.read(function_id)
            assert result["current_release_id"] is None
            assert result["validation"]["checks"][-1]["status"] == "unavailable"
            # Direct HTTP publication cannot use a forged result or bypass the same guard.
            release = await client.post(
                f"/api/v1/functions/{function_id}/release",
                json={
                    "expected_revision": result["revision_hash"],
                    "validation_id": result["validation"]["id"],
                },
            )
            assert release.status_code == 409
            forged = await client.post(
                f"/api/v1/functions/{function_id}/release",
                json={"code_snapshot": "valid", "verification": {"passed": True}},
            )
            assert forged.status_code == 422
    finally:
        await runtime.close()
