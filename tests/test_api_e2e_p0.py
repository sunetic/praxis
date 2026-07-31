from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import chat_pending as chat_pending_api
from app.api import functions as functions_api
from app.api import schedules as schedules_api
from app.db import database as db_module
from app.db.database import Base
from app.models import models
from app.services.function.builder import FunctionBuildResult
from app.services.platform.coding_engine import CodingEngineApplyResult


def _datasource_payload(
    name: str,
    *,
    cluster_key: str = "127.0.0.1:2881",
) -> dict[str, Any]:
    return {
        "name": name,
        "host": "127.0.0.1",
        "port": 2881,
        "db_type": "oceanbase",
        "cluster_key": cluster_key,
        "tenant_role": "user",
        "user": "tester",
        "password": "secret",
        "database": "test",
    }


def _create_datasource(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/api/v1/datasources", json=_datasource_payload(name))
    assert response.status_code == 201, response.text
    return response.json()


def _create_and_build_function(client: TestClient, name: str = "e2e-function") -> dict[str, Any]:
    created = client.post("/api/v1/functions", json={"name": name})
    assert created.status_code == 201, created.text
    function = created.json()
    built = client.post(
        f"/api/v1/functions/{function['id']}/build",
        json={"prompt": "生成一个返回 ok 的函数"},
    )
    assert built.status_code == 200, built.text
    return built.json()["function"]


def _release_function(client: TestClient, function_id: int) -> dict[str, Any]:
    released = client.post(
        f"/api/v1/functions/{function_id}/release",
        json={"requirement": "e2e release"},
    )
    assert released.status_code == 200, released.text
    return released.json()


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("PRAXIS_CODING_ENGINE", "aider_like")

    db_path = tmp_path / "e2e-api.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(db_module, "engine", engine, raising=False)
    monkeypatch.setattr(db_module, "SessionLocal", session_local, raising=False)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module
    from app.services.scheduler.runtime_state import set_scheduler_worker

    main_module.settings.scheduler_autostart = False
    main_module.settings.builder_runtime_enabled = True
    set_scheduler_worker(None)
    monkeypatch.setattr("app.main.configure_logging", lambda debug: None)

    with TestClient(main_module.app) as client:
        yield client, session_local

    set_scheduler_worker(None)
    engine.dispose()


@pytest.fixture(autouse=True)
def patch_runtime_stubs(monkeypatch: pytest.MonkeyPatch):
    def fake_apply_function_goal(
        self, *, function, goal, workspace_store=None, datasource_schema=None, datasource_id=None
    ):  # noqa: ARG001
        function.draft_code = (
            "def main(payload, context):\n"
            "    data = payload if isinstance(payload, dict) else {}\n"
            "    return {'ok': True, 'echo': data}\n"
        )
        function.draft_dependencies = {}
        return CodingEngineApplyResult(
            changed_files=["main.py"],
            diff_summary="updated main.py",
            tests_suggested=[],
            risk_notes=[],
            assistant_message="Function 草稿已更新",
            generated_title="函数回显",
            generated_description="返回基础结果",
        )

    def fake_suggest_input(self, *, function, prompt, conversation_context, context=None):  # noqa: ARG001
        return {
            "sample_payload": {"name": "alice", "limit": 10},
            "missing_information": [],
            "assumptions": [],
            "rationale": "基于函数签名生成测试入参。",
        }

    class _FakeLLM:
        def __init__(self):
            self._call_count = 0

        async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
            stream: bool = True,
            **kwargs: Any,  # noqa: ARG002
        ) -> AsyncGenerator[dict[str, Any], None]:
            system_text = " ".join(
                str(item.get("content") or "") for item in messages if item.get("role") == "system"
            )
            if not stream:
                # pi_lite_engine: detect by tool presence in system prompt
                if "write_file" in system_text or "function_runtime_probe" in system_text:
                    last_role = (messages[-1] or {}).get("role", "")
                    # After a tool result, return final JSON
                    if last_role == "tool":
                        yield {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "summary": "函数已生成",
                                                "changed_files": ["main.py"],
                                                "diff_summary": "added main.py",
                                                "tests_suggested": [],
                                                "risk_notes": [],
                                                "assistant_message": "Function 草稿已更新",
                                                "generated_title": "函数回显",
                                                "generated_description": "返回基础结果",
                                            },
                                            ensure_ascii=False,
                                        ),
                                        "tool_calls": None,
                                    }
                                }
                            ]
                        }
                        return
                    # Count how many tool results are already in messages
                    tool_result_count = sum(1 for m in messages if m.get("role") == "tool")
                    if tool_result_count == 0:
                        # First call: write the file
                        yield {
                            "choices": [
                                {
                                    "message": {
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "call_write_1",
                                                "type": "function",
                                                "function": {
                                                    "name": "write_file",
                                                    "arguments": json.dumps(
                                                        {
                                                            "path": "main.py",
                                                            "content": "def main(payload, context):\n    return {'ok': True}\n",
                                                        }
                                                    ),
                                                },
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                        return
                    # After write, probe
                    yield {
                        "choices": [
                            {
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_probe_1",
                                            "type": "function",
                                            "function": {
                                                "name": "function_runtime_probe",
                                                "arguments": json.dumps({"payload": {}}),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                    return
                if "strict skill selector" in system_text:
                    yield {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"add":[],"remove":[],"reason":"e2e-selector"}'
                                }
                            }
                        ]
                    }
                    return
                if "Action Fabric pre-router planner" in system_text:
                    yield {"choices": [{"message": {"content": '{"actions":[]}'}}]}
                    return
                if "生成一次性的用户可见元信息" in system_text:
                    yield {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"title":"函数回显","description":"返回基础结果"}'
                                }
                            }
                        ]
                    }
                    return
                yield {
                    "choices": [
                        {"message": {"content": '{"add":[],"remove":[],"reason":"e2e-default"}'}}
                    ]
                }
                return

            yield {
                "choices": [{"delta": {"content": "E2E assistant response"}, "finish_reason": None}]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    fake_llm = _FakeLLM()
    monkeypatch.setattr("app.services.chat.stream_helpers.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.chat.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(functions_api, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.pi_lite_engine.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.llm.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.scope_adapter_base.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.reasoning_engine.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.build_verify_loop.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.api.datasources._probe_and_fill_ob_ids", None)
    monkeypatch.setattr(
        functions_api,
        "FunctionBuilderService",
        lambda: type(
            "_FakeFunctionBuilderService",
            (),
            {
                "apply_prompt": lambda self, *, current_code, current_dependencies, prompt, function_name: (
                    FunctionBuildResult(  # noqa: ARG005
                        draft_code=(
                            "def main(payload, context):\n"
                            "    data = payload if isinstance(payload, dict) else {}\n"
                            "    return {'ok': True, 'echo': data}\n"
                        ),
                        draft_dependencies={},
                        summary="Function 草稿已更新",
                    )
                )
            },
        )(),
    )

    monkeypatch.setattr(
        functions_api.FunctionChatAgent, "apply_function_goal", fake_apply_function_goal
    )
    monkeypatch.setattr(
        functions_api.FunctionChatAgent, "suggest_function_input", fake_suggest_input
    )
    monkeypatch.setattr(
        functions_api.WorkspaceStore, "commit_publish", lambda self, **kwargs: "e2e-commit"
    )


def test_p0_function_mainline_build_release_invoke(api_client):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-mainline")
    release = _release_function(client, function["id"])
    assert release["function"]["status"] == "released"
    invoke = client.post(
        f"/api/v1/functions/{function['id']}/invoke",
        json={
            "payload": {"x": 1},
            "runtime_path": "production",
            "execution_mode": "apply",
            "write_mode": "readonly",
        },
    )
    assert invoke.status_code == 200, invoke.text
    payload = invoke.json()
    assert payload["status"] == "success"
    assert isinstance(payload["run_id"], str) and payload["run_id"]


def test_p0_function_write_apply_requires_confirm(api_client):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-confirm")
    _release_function(client, function["id"])
    invoke = client.post(
        f"/api/v1/functions/{function['id']}/invoke",
        json={
            "payload": {"dry_run": False},
            "runtime_path": "production",
            "execution_mode": "apply",
            "write_mode": "write",
        },
    )
    assert invoke.status_code == 400
    assert "confirm_apply=true" in str(invoke.json().get("detail"))


def test_p0_function_release_blocked_by_verification(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-verify-fail")

    def always_fail_verify(self, *, code_snapshot, dependency_manifest=None):  # noqa: ARG001
        return {
            "passed": False,
            "diagnostics": ["mock verification failed"],
            "checks": [{"name": "mock", "passed": False, "detail": "mock verification failed"}],
        }

    monkeypatch.setattr(
        functions_api.FunctionVerificationHarness, "verify_draft", always_fail_verify
    )
    release = client.post(
        f"/api/v1/functions/{function['id']}/release",
        json={"requirement": "should fail"},
    )
    assert release.status_code == 400
    detail = release.json().get("detail") or {}
    assert detail.get("code") == "verification_failed"
    assert detail.get("diagnostics")


def test_p0_function_chat_build_suggest_invoke(api_client):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-chat-actions"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]

    build_resp = client.post(
        f"/api/v1/functions/{function_id}/chat",
        json={"action": "build", "prompt": "构建一个返回 ok 的函数"},
    )
    assert build_resp.status_code == 200, build_resp.text
    assert build_resp.json()["status"] == "done"

    suggest_resp = client.post(
        f"/api/v1/functions/{function_id}/chat",
        json={"action": "suggest_input"},
    )
    assert suggest_resp.status_code == 200, suggest_resp.text
    assert suggest_resp.json()["data"]["suggestion"]["sample_payload"]["name"] == "alice"

    invoke_resp = client.post(
        f"/api/v1/functions/{function_id}/chat",
        json={
            "action": "invoke",
            "invoke": {
                "payload": {"check": True},
                "runtime_path": "draft",
                "execution_mode": "plan",
                "write_mode": "readonly",
            },
        },
    )
    assert invoke_resp.status_code == 200, invoke_resp.text
    invoke_payload = invoke_resp.json()
    assert invoke_payload["action"] == "invoke"
    assert invoke_payload["status"] in {"success", "failed"}
    assert "build_run" in invoke_payload["data"]


def test_p0_function_chat_build_stream_events(api_client):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-chat-stream"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]

    events: list[dict[str, Any]] = []
    with client.stream(
        "POST",
        f"/api/v1/functions/{function_id}/chat/stream",
        json={
            "action": "build",
            "prompt": "构建一个返回 ok 的函数",
            "scene_agent": {
                "key": "function_build",
                "context": {"function_id": function_id, "page": "function-build"},
                "focus_object": {"kind": "function", "function_id": function_id},
            },
        },
    ) as response:
        assert response.status_code == 200, response.text
        for raw_line in response.iter_lines():
            line = str(raw_line or "").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line.replace("data:", "", 1).strip()
            if not payload:
                continue
            events.append(json.loads(payload))

    assert events, "stream should return at least one event"
    event_types = [str(item.get("type") or "") for item in events]
    assert "phase" in event_types
    assert "assistant" in event_types
    assert event_types[-1] == "done"
    done_event = events[-1]
    assert str(done_event.get("data", {}).get("action") or "") == "build"
    assert str(done_event.get("data", {}).get("status") or "") in {"done", "failed"}


def test_p0_function_chat_build_stream_rejects_mismatched_scene_function_id(api_client):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-chat-scene-guard"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]

    response = client.post(
        f"/api/v1/functions/{function_id}/chat/stream",
        json={
            "action": "build",
            "prompt": "构建一个返回 ok 的函数",
            "scene_agent": {
                "key": "function_build",
                "context": {"function_id": function_id + 1},
                "focus_object": {"kind": "function", "function_id": function_id},
            },
        },
    )

    assert response.status_code == 400
    assert "scene_agent.function_id must match request path" in str(response.json().get("detail"))


def test_p0_function_chat_suggest_and_invoke_stream_events(api_client):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-chat-stream-actions"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]

    with client.stream(
        "POST",
        f"/api/v1/functions/{function_id}/chat/stream",
        json={"action": "suggest_input"},
    ) as suggest_response:
        assert suggest_response.status_code == 200, suggest_response.text
        suggest_events = [
            json.loads(str(line).replace("data:", "", 1).strip())
            for line in suggest_response.iter_lines()
            if str(line or "").strip().startswith("data:")
        ]
    assert suggest_events and str(suggest_events[-1].get("type") or "") == "done"
    assert str(suggest_events[-1].get("data", {}).get("action") or "") == "suggest_input"

    build_resp = client.post(
        f"/api/v1/functions/{function_id}/chat",
        json={"action": "build", "prompt": "构建一个返回 ok 的函数"},
    )
    assert build_resp.status_code == 200, build_resp.text

    with client.stream(
        "POST",
        f"/api/v1/functions/{function_id}/chat/stream",
        json={
            "action": "invoke",
            "invoke": {
                "payload": {"check": True},
                "runtime_path": "draft",
                "execution_mode": "plan",
                "write_mode": "readonly",
            },
        },
    ) as invoke_response:
        assert invoke_response.status_code == 200, invoke_response.text
        invoke_events = [
            json.loads(str(line).replace("data:", "", 1).strip())
            for line in invoke_response.iter_lines()
            if str(line or "").strip().startswith("data:")
        ]
    assert invoke_events and str(invoke_events[-1].get("type") or "") == "done"
    assert str(invoke_events[-1].get("data", {}).get("action") or "") == "invoke"


def test_p0_schedule_requires_released_function(api_client):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-not-released"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]
    schedule = client.post(
        "/api/v1/schedules",
        json={
            "name": "should-fail",
            "target_type": "function",
            "target_id": function_id,
            "schedule_type": "interval",
            "interval_seconds": 60,
        },
    )
    assert schedule.status_code == 400
    assert "released" in str(schedule.json().get("detail"))


def test_p0_schedule_run_now_and_history(api_client):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-schedule-run-now")
    _release_function(client, function["id"])
    schedule = client.post(
        "/api/v1/schedules",
        json={
            "name": "run-now-e2e",
            "target_type": "function",
            "target_id": function["id"],
            "schedule_type": "interval",
            "interval_seconds": 300,
        },
    )
    assert schedule.status_code == 201, schedule.text
    schedule_id = schedule.json()["id"]

    run_now = client.post(f"/api/v1/schedules/{schedule_id}/run-now")
    assert run_now.status_code == 200, run_now.text
    run_id = run_now.json()["run_id"]
    assert isinstance(run_id, str) and run_id

    runs = client.get(f"/api/v1/schedules/{schedule_id}/runs")
    assert runs.status_code == 200, runs.text
    assert any(item.get("run_id") == run_id for item in runs.json())


def test_p0_schedule_pause_resume_disable_enable(api_client):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-schedule-lifecycle")
    _release_function(client, function["id"])
    created = client.post(
        "/api/v1/schedules",
        json={
            "name": "lifecycle-e2e",
            "target_type": "function",
            "target_id": function["id"],
            "schedule_type": "interval",
            "interval_seconds": 120,
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    paused = client.post(f"/api/v1/schedules/{schedule_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/v1/schedules/{schedule_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"

    disabled = client.post(f"/api/v1/schedules/{schedule_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "paused"

    enabled = client.post(f"/api/v1/schedules/{schedule_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"


def test_p0_schedule_run_now_active_worker_path(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client
    function = _create_and_build_function(client, name="fn-worker-path")
    _release_function(client, function["id"])
    created = client.post(
        "/api/v1/schedules",
        json={
            "name": "worker-path-e2e",
            "target_type": "function",
            "target_id": function["id"],
            "schedule_type": "interval",
            "interval_seconds": 600,
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    class _FakeWorker:
        def health(self):
            return {"running": True}

        async def submit_now(self, schedule_id: int, trace_id: str):  # noqa: ARG002
            return f"fake-run-{schedule_id}", None

    monkeypatch.setattr(schedules_api, "get_scheduler_worker", lambda: _FakeWorker())
    run_now = client.post(f"/api/v1/schedules/{schedule_id}/run-now")
    assert run_now.status_code == 200
    assert run_now.json()["run_id"] == f"fake-run-{schedule_id}"


def test_p0_agent_run_and_inactive_rejection(api_client):
    client, _ = api_client
    ds1 = _create_datasource(client, "agent-ds-1")
    ds2 = _create_datasource(client, "agent-ds-2")

    created = client.post(
        "/api/v1/agents",
        json={
            "name": "ops-agent",
            "prompt": "You are ops agent",
            "tools": ["execute_sql"],
            "skills": [],
            "agent_type": "custom",
        },
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]

    run = client.post(
        f"/api/v1/agents/{agent_id}/run",
        json={"title": "巡检会话", "datasource_ids": [ds2["id"], ds1["id"]]},
    )
    assert run.status_code == 201, run.text
    payload = run.json()
    assert payload["datasource_ids"] == [ds2["id"], ds1["id"]]
    assert payload["conversation"]["agent_id"] == agent_id
    assert payload["conversation"]["datasource_id"] == ds2["id"]

    updated = client.patch(f"/api/v1/agents/{agent_id}", json={"status": "inactive"})
    assert updated.status_code == 200, updated.text
    rejected = client.post(f"/api/v1/agents/{agent_id}/run", json={"datasource_ids": [ds1["id"]]})
    assert rejected.status_code == 400
    assert "not active" in str(rejected.json().get("detail"))


def test_p0_build_session_lifecycle(api_client):
    client, _ = api_client
    conv = client.post("/api/v1/conversations", json={"title": "build-session-e2e"})
    assert conv.status_code == 201, conv.text
    conversation_id = conv.json()["id"]

    created = client.post(
        f"/api/v1/conversations/{conversation_id}/build-sessions",
        json={"scope_object_type": "function", "scope_object_id": "123", "ttl_seconds": 1800},
    )
    assert created.status_code == 201, created.text
    session = created.json()

    active = client.get(f"/api/v1/conversations/{conversation_id}/build-sessions/active")
    assert active.status_code == 200, active.text
    assert active.json()["id"] == session["id"]

    heartbeat = client.post(
        f"/api/v1/conversations/{conversation_id}/build-sessions/{session['id']}/heartbeat",
        json={"ttl_seconds": 3600},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["ttl_seconds"] == 3600

    closed = client.delete(
        f"/api/v1/conversations/{conversation_id}/build-sessions/{session['id']}"
    )
    assert closed.status_code == 204, closed.text
    not_found = client.get(f"/api/v1/conversations/{conversation_id}/build-sessions/active")
    assert not_found.status_code == 404


def test_p0_conversation_source_defaults_and_category_filter(api_client):
    client, _ = api_client

    primary = client.post("/api/v1/conversations", json={"title": "main-chat"})
    assert primary.status_code == 201, primary.text
    primary_payload = primary.json()
    assert primary_payload["category"] == "primary"
    assert primary_payload["scene_key"] is None
    assert primary_payload["read_only"] is False

    scene = client.post(
        "/api/v1/conversations",
        json={
            "title": "sql-analysis-scene",
            "category": "scene",
            "scene_key": "sql_analysis",
            "read_only": True,
        },
    )
    assert scene.status_code == 201, scene.text
    scene_payload = scene.json()
    assert scene_payload["category"] == "scene"
    assert scene_payload["scene_key"] == "sql_analysis"
    assert scene_payload["read_only"] is True

    all_conversations = client.get("/api/v1/conversations")
    assert all_conversations.status_code == 200, all_conversations.text
    assert {item["id"] for item in all_conversations.json()} == {
        primary_payload["id"],
        scene_payload["id"],
    }

    primary_only = client.get("/api/v1/conversations", params={"category": "primary"})
    assert primary_only.status_code == 200, primary_only.text
    assert [item["id"] for item in primary_only.json()] == [primary_payload["id"]]

    scene_only = client.get("/api/v1/conversations", params={"category": "scene"})
    assert scene_only.status_code == 200, scene_only.text
    assert [item["id"] for item in scene_only.json()] == [scene_payload["id"]]

    scene_key_match = client.get("/api/v1/conversations", params={"scene_key": "sql_analysis"})
    assert scene_key_match.status_code == 200, scene_key_match.text
    assert [item["id"] for item in scene_key_match.json()] == [scene_payload["id"]]

    scene_key_no_match = client.get(
        "/api/v1/conversations", params={"scene_key": "nonexistent_key"}
    )
    assert scene_key_no_match.status_code == 200, scene_key_no_match.text
    assert scene_key_no_match.json() == []


def test_p0_chat_stream_function_builder_scene_ingress(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client
    created = client.post("/api/v1/functions", json={"name": "fn-builder-scene-ingress"})
    assert created.status_code == 201, created.text
    function_id = created.json()["id"]

    conv = client.post("/api/v1/conversations", json={"title": "function-build-scene"})
    assert conv.status_code == 201, conv.text
    conversation_id = conv.json()["id"]

    session = client.post(
        f"/api/v1/conversations/{conversation_id}/build-sessions",
        json={
            "scope_object_type": "function",
            "scope_object_id": str(function_id),
            "ttl_seconds": 1800,
        },
    )
    assert session.status_code == 201, session.text

    user_message = client.post(
        "/api/v1/messages",
        json={
            "conversation_id": conversation_id,
            "role": "user",
            "content": "构建一个返回 ok 的函数",
        },
    )
    assert user_message.status_code == 201, user_message.text

    async def fake_function_chat_stream(function_id: int, payload: dict[str, Any], db: Any):  # noqa: ARG001
        assert function_id > 0
        assert str(payload.get("action") or "") == "build"
        assert str(payload.get("prompt") or "") == "构建一个返回 ok 的函数"
        scene_agent = (
            payload.get("scene_agent") if isinstance(payload.get("scene_agent"), dict) else {}
        )
        assert str(scene_agent.get("key") or "") == "function_build"

        async def generate():
            yield (
                'data: {"type":"phase","phase":"plan","data":{"status":"running","summary":"Plan · 已解析 Function 需求。"}}\n\n'
            )
            yield 'data: {"type":"assistant","data":{"text":"返回 ok 的函数"}}\n\n'
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "done",
                        "data": {
                            "action": "build",
                            "status": "done",
                            "assistant_message": "返回 ok 的函数",
                            "function": {
                                "id": function_id,
                                "name": "fn-builder-scene-ingress",
                                "status": "draft",
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        return StreamingResponse(generate(), media_type="text/event-stream")

    monkeypatch.setattr(functions_api, "run_function_chat_action_stream", fake_function_chat_stream)

    events: list[dict[str, Any]] = []
    with client.stream(
        "POST",
        f"/api/v1/chat/{conversation_id}/stream",
        json={
            "content": "构建一个返回 ok 的函数",
            "scene_agent": {
                "key": "function_build",
                "context": {"function_id": function_id, "page": "function-build"},
                "focus_object": {"kind": "function", "function_id": function_id},
            },
        },
    ) as response:
        assert response.status_code == 200, response.text
        for raw_line in response.iter_lines():
            line = str(raw_line or "").strip()
            if not line or not line.startswith("data:"):
                continue
            events.append(json.loads(line.replace("data:", "", 1).strip()))

    assert events and str(events[-1].get("type") or "") == "done"
    assert str(events[-1].get("data", {}).get("action") or "") == "build"
    assert str(events[-1].get("data", {}).get("status") or "") == "done"

    chat_events = client.get(f"/api/v1/chat/{conversation_id}/events")
    assert chat_events.status_code == 200, chat_events.text
    event_types = [str(item.get("event_type") or "") for item in chat_events.json()]
    assert "builder_scope" in event_types
    assert "scene_agent_context" in event_types
    assert "done" in event_types

    messages = client.get(f"/api/v1/messages/conversation/{conversation_id}")
    assert messages.status_code == 200, messages.text
    assert any(str(item.get("content") or "") == "返回 ok 的函数" for item in messages.json())


def test_p0_chat_stream_persists_messages_and_events(api_client):
    client, _ = api_client
    conv = client.post("/api/v1/conversations", json={"title": "chat-stream-e2e"})
    assert conv.status_code == 201, conv.text
    conversation_id = conv.json()["id"]

    vds_lines: list[str] = []
    with client.stream(
        "POST",
        f"/api/v1/chat/{conversation_id}/stream",
        json={"content": "你好，帮我做个检查"},
    ) as response:
        assert response.status_code == 200
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            line = line.strip()
            if line:
                vds_lines.append(line)
    assert vds_lines, "stream should return at least one VDS line"
    assert any(line.startswith("d:") for line in vds_lines), (
        "stream should end with finish_message (d:)"
    )

    chat_events = client.get(f"/api/v1/chat/{conversation_id}/events")
    assert chat_events.status_code == 200, chat_events.text
    assert len(chat_events.json()) >= 1

    messages = client.get(f"/api/v1/messages/conversation/{conversation_id}")
    assert messages.status_code == 200, messages.text
    assert any(msg.get("role") == "assistant" for msg in messages.json())


def test_p0_chat_pending_action_confirm_and_cancel(api_client, monkeypatch: pytest.MonkeyPatch):
    client, session_local = api_client
    conv = client.post("/api/v1/conversations", json={"title": "pending-action-e2e"})
    assert conv.status_code == 201, conv.text
    conversation_id = conv.json()["id"]

    token_confirm = "tok-confirm-e2e"
    token_cancel = "tok-cancel-e2e"
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_local() as db:
        db.add(
            models.PendingAction(
                conversation_id=conversation_id,
                token=token_confirm,
                action_type="object_action",
                status="pending",
                payload={
                    "mode": "operate",
                    "object_type": "function",
                    "action": "release",
                    "object_id": 1,
                    "payload": {},
                    "capability_key": "object.operate",
                    "preview": "operate function#1 -> release",
                },
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            models.PendingAction(
                conversation_id=conversation_id,
                token=token_cancel,
                action_type="object_action",
                status="pending",
                payload={
                    "mode": "operate",
                    "object_type": "function",
                    "action": "archive",
                    "object_id": 2,
                    "payload": {},
                    "capability_key": "object.operate",
                    "preview": "operate function#2 -> archive",
                },
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    async def fake_operate(self, *, object_type, action, object_id, payload, actor):  # noqa: ARG001
        return {
            "object_type": object_type,
            "action": action,
            "object_id": object_id,
            "status": "ok",
            "actor": actor,
        }

    monkeypatch.setattr(chat_pending_api.ObjectToolService, "operate", fake_operate)

    pending = client.get(f"/api/v1/chat/{conversation_id}/actions/pending")
    assert pending.status_code == 200, pending.text
    tokens = {item["token"] for item in pending.json()}
    assert token_confirm in tokens and token_cancel in tokens

    confirmed = client.post(f"/api/v1/chat/{conversation_id}/actions/{token_confirm}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "executed"

    cancelled = client.post(f"/api/v1/chat/{conversation_id}/actions/{token_cancel}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    remaining = client.get(f"/api/v1/chat/{conversation_id}/actions/pending")
    assert remaining.status_code == 200
    assert remaining.json() == []


def test_p0_datasource_test_and_agent_reference(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client

    class _FakePool:
        async def test_connection(self, host, port, user, password, database):  # noqa: ARG002
            return True, "ok"

    monkeypatch.setattr("app.db.connection.get_db_pool", lambda: _FakePool())

    ds = _create_datasource(client, "ds-e2e-main")
    tested = client.post(f"/api/v1/datasources/{ds['id']}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True

    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "ds-bind-agent",
            "prompt": "agent prompt",
            "tools": [],
            "skills": [],
            "agent_type": "custom",
        },
    )
    assert agent.status_code == 201, agent.text
    run = client.post(
        f"/api/v1/agents/{agent.json()['id']}/run", json={"datasource_ids": [ds["id"]]}
    )
    assert run.status_code == 201, run.text
    assert run.json()["conversation"]["datasource_id"] == ds["id"]


def test_p0_live_sql_analysis_endpoints(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client

    class _FakePool:
        async def execute_query(self, datasource, sql, role="user", params=None):  # noqa: ARG002
            normalized_sql = " ".join(sql.split()).lower()
            if "sql_analysis_live:db_names" in normalized_sql:
                return {
                    "columns": [],
                    "rows": [
                        {"db_name": "app_db"},
                        {"db_name": "biz_db"},
                    ],
                    "row_count": 2,
                }
            if "sql_analysis_live:recent_sql_metadata" in normalized_sql:
                return {
                    "columns": [],
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "sql_id": "live-top-1",
                            "db_name": "app_db",
                            "user_name": "root",
                            "sql_text": "select * from t_top",
                            "latest_request_time_us": 1711616400000000,
                        }
                    ],
                    "row_count": 1,
                }
            if "sql_analysis_live:recent_sql" in normalized_sql:
                return {
                    "columns": [],
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "sql_id": "live-top-1",
                            "sql_text": "select * from t_top",
                            "latest_last_active_time": "2026-03-28 09:00:00.000000",
                            "plan_count": 2,
                        }
                    ],
                    "row_count": 1,
                }
            if (
                "from oceanbase.gv$ob_sql_audit" in normalized_sql
                and "group by tenant_id, sql_id limit 1" in normalized_sql
            ):
                return {
                    "columns": [],
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "db_name": "app_db",
                            "user_name": "root",
                            "sql_text": "select * from t_top",
                            "executions": 12,
                            "avg_elapsed_time_us": 10000,
                            "avg_execute_time_us": 7000,
                            "max_elapsed_time_us": 30000,
                            "latest_request_time_us": 100,
                            "plan_count": 2,
                        }
                    ],
                    "row_count": 1,
                }
            if (
                "from oceanbase.gv$ob_sql_audit" in normalized_sql
                and "group by tenant_id, sql_id, plan_id" in normalized_sql
            ):
                return {
                    "columns": [],
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "sql_id": "live-top-1",
                            "plan_id": 10001,
                            "plan_hash": 999,
                            "executions": 8,
                            "avg_exe_usec": 120,
                            "elapsed_time": 1200,
                            "execute_time": 1000,
                            "table_scan": 1,
                            "last_active_time": "2026-03-28 09:00:00.000000",
                            "query_sql": "select * from t_top",
                        },
                        {
                            "tenant_id": 1002,
                            "sql_id": "live-top-1",
                            "plan_id": 10002,
                            "plan_hash": 1000,
                            "executions": 4,
                            "avg_exe_usec": 180,
                            "elapsed_time": 1600,
                            "execute_time": 1300,
                            "table_scan": 0,
                            "last_active_time": "2026-03-28 08:00:00.000000",
                            "query_sql": "select * from t_top",
                        },
                    ],
                    "row_count": 2,
                }
            if "from oceanbase.gv$ob_plan_cache_plan_stat" in normalized_sql:
                return {
                    "columns": [],
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "sql_id": "live-top-1",
                            "plan_id": 10001,
                            "plan_hash": 999,
                            "executions": 0,
                            "avg_exe_usec": 120,
                            "elapsed_time": 1200,
                            "execute_time": 1000,
                            "table_scan": 1,
                            "last_active_time": "2026-03-28 09:00:00.000000",
                            "query_sql": "select * from t_top",
                        }
                    ],
                    "row_count": 1,
                }
            if "explain select * from t_top" in normalized_sql:
                return {
                    "columns": [],
                    "rows": [
                        {
                            "id": 1,
                            "select_type": "SIMPLE",
                            "table": "t_top",
                            "rows": 10,
                            "Extra": "Full scan",
                        }
                    ],
                    "row_count": 1,
                }
            if "from oceanbase.gv$ob_plan_cache_plan_explain" in normalized_sql:
                return {"columns": [], "rows": [], "row_count": 0}
            raise AssertionError(f"unexpected sql: {sql}")

        async def execute_explain(self, datasource, sql, role="user", database=None):  # noqa: ARG002
            return await self.execute_query(datasource, f"EXPLAIN {sql}", role=role)

    class _FakeLiveSqlExplainLLM:
        async def chat(self, *args, **kwargs):  # noqa: ARG002
            yield {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "当前为实时视角，执行计划出现表扫描，建议先核对索引路径与过滤条件。",
                                    "risk_points": ["表扫描可能导致读取放大"],
                                    "investigation_steps": ["检查过滤列是否已有可用索引"],
                                    "optimization_directions": ["优先评估索引覆盖与谓词可索引性"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.db.connection.get_db_pool", lambda: _FakePool())
    monkeypatch.setattr(
        "app.services.sql_analysis.live.context.get_llm_client", lambda: _FakeLiveSqlExplainLLM()
    )

    created_sys = client.post(
        "/api/v1/datasources",
        json={
            **_datasource_payload("live-sqla-sys", cluster_key="cluster-live"),
            "tenant_role": "sys",
            "user": "root@test#sys",
            "database": "oceanbase",
        },
    )
    assert created_sys.status_code == 201, created_sys.text
    source_datasource_id = created_sys.json()["id"]

    created_user = client.post(
        "/api/v1/datasources",
        json={
            **_datasource_payload("live-sqla-user", cluster_key="cluster-live"),
            "tenant_role": "user",
            "user": "root@test#wx",
            "database": "app_db",
        },
    )
    assert created_user.status_code == 201, created_user.text
    preferred_datasource_id = created_user.json()["id"]

    base_params = {
        "datasource_id": source_datasource_id,
        "start_time_us": 60_000_000,
        "end_time_us": 120_000_000,
        "tenant_id": 1002,
    }

    db_names = client.get("/api/v1/sql-analysis/live/db-names", params=base_params)
    assert db_names.status_code == 200, db_names.text
    assert db_names.json()["items"] == ["app_db", "biz_db"]

    discovery = client.get("/api/v1/sql-analysis/live/discovery", params=base_params)
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["items"][0]["sql_id"] == "live-top-1"
    assert discovery.json()["items"][0]["plan_count"] == 2
    assert discovery.json()["items"][0]["sql_text"] == "select * from t_top"
    assert discovery.json()["items"][0]["db_name"] == "app_db"
    assert discovery.json()["items"][0]["user_name"] == "root"
    assert discovery.json()["items"][0]["source_datasource_id"] == source_datasource_id
    assert (
        discovery.json()["items"][0]["preferred_execution_datasource_id"] == preferred_datasource_id
    )

    context = client.get(
        "/api/v1/sql-analysis/live/build-context",
        params={**base_params, "sql_id": "live-top-1"},
    )
    assert context.status_code == 200, context.text
    context_payload = context.json()
    assert context_payload["window_plan_total"] == 2
    assert context_payload["current_plan_id"] == 10001
    assert context_payload["facts"]["current_plan"]["plan_id"] == 10001
    assert {item["key"] for item in context_payload["signals"]} >= {
        "table_scan_risk",
        "history_unavailable",
    }
    assert context_payload["facts"]["unavailable_dimensions"][0]["key"] == "executions"
    assert context_payload["plan_explain"]["source"] == "explain_sql"

    explanation = client.post(
        "/api/v1/sql-analysis/live/explain-with-ai",
        params={**base_params, "sql_id": "live-top-1"},
    )
    assert explanation.status_code == 200, explanation.text
    explanation_payload = explanation.json()
    assert "实时视角" in explanation_payload["summary"]
    assert explanation_payload["risk_points"] == ["表扫描可能导致读取放大"]
    assert (
        explanation_payload["context"]["facts"]["current_plan"]["explain_source"] == "explain_sql"
    )


# ---------------------------------------------------------------------------
# Cross-domain smoke tests — verify every API domain is reachable and returns
# expected status codes after the modular restructure.
# ---------------------------------------------------------------------------


def test_p0_datasource_crud_lifecycle(api_client):
    client, _ = api_client

    listed = client.get("/api/v1/datasources")
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)

    ds = _create_datasource(client, "ds-crud-smoke")
    ds_id = ds["id"]
    assert ds["name"] == "ds-crud-smoke"

    fetched = client.get(f"/api/v1/datasources/{ds_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == ds_id

    updated = client.patch(f"/api/v1/datasources/{ds_id}", json={"name": "ds-crud-renamed"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "ds-crud-renamed"

    deleted = client.delete(f"/api/v1/datasources/{ds_id}")
    assert deleted.status_code in (200, 204)

    gone = client.get(f"/api/v1/datasources/{ds_id}")
    assert gone.status_code == 404


def test_p0_settings_get(api_client):
    client, _ = api_client
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, dict)


def test_p0_onboarding_status_and_complete(api_client):
    client, _ = api_client

    status = client.get("/api/v1/onboarding/status")
    assert status.status_code == 200
    assert "completed" in status.json()

    complete = client.post(
        "/api/v1/onboarding/complete",
        json={
            "llm_config": {
                "llm_provider": "test",
                "llm_api_key": "sk-test",
                "llm_model": "gpt-test",
            }
        },
    )
    assert complete.status_code == 200
    assert complete.json()["completed"] is True

    status_after = client.get("/api/v1/onboarding/status")
    assert status_after.json()["completed"] is True


def test_p0_skills_list(api_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client, _ = api_client
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "test_skill.yaml").write_text("name: test_skill\nprompt: hello\nsource: custom\n")
    from app.services.chat import stream_helpers as _sh
    from app.skills.store import SkillStore

    monkeypatch.setattr(_sh, "skill_store", SkillStore(skills_dir=str(skills_dir)))

    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_p0_channels_list(api_client):
    client, _ = api_client
    resp = client.get("/api/v1/channels")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_p0_chat_handoff_create_get_consume(api_client):
    client, _ = api_client

    created = client.post(
        "/api/v1/chat/handoffs",
        json={
            "packet": {
                "type": "sql_detail",
                "version": 1,
                "source": {"page": "sql_detail", "entry": "diagnose"},
                "title": "handoff smoke test",
            },
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    conv_id = payload["conversation"]["id"]
    handoff_id = payload["handoff"]["id"]
    assert payload["handoff"]["status"] == "pending"

    fetched = client.get(f"/api/v1/chat/{conv_id}/handoffs/{handoff_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"

    consumed = client.post(f"/api/v1/chat/{conv_id}/handoffs/{handoff_id}/consume")
    assert consumed.status_code == 200
    assert consumed.json()["status"] == "consumed"

    fetched_after = client.get(f"/api/v1/chat/{conv_id}/handoffs/{handoff_id}")
    assert fetched_after.json()["status"] == "consumed"
