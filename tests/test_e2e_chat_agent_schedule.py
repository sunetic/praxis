"""E2E: chat → save-agent → run agent → schedule agent → run-now.

Full lifecycle test covering the chain:
  1. Create a conversation + multi-turn chat streaming
  2. Save the conversation as an Agent (via save-agent/stream)
  3. Run the agent (creates a run conversation, then chat-stream)
  4. Create a schedule targeting the agent
  5. Trigger run-now on the schedule
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import schedules as schedules_api
from app.db import database as db_module
from app.db.database import Base
from app.models import models
from app.services.scheduler.result import ScheduleRuntimeResult

# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Minimal mock that handles both streaming chat and non-streaming calls."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        system_text = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "system"
        )

        if not stream:
            if "extracting the optimal execution path" in system_text:
                yield {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "name": "E2E Test Agent",
                                        "description": "Agent created from e2e conversation",
                                        "prompt": "You are an e2e test agent. Follow the user instructions.",
                                        "tools": [],
                                        "skills": [],
                                    }
                                ),
                            },
                        }
                    ],
                }
                return

            if "strict skill selector" in system_text:
                yield {
                    "choices": [{"message": {"content": '{"add":[],"remove":[],"reason":"e2e"}'}}]
                }
                return
            if "Action Fabric pre-router planner" in system_text:
                yield {"choices": [{"message": {"content": '{"actions":[]}'}}]}
                return

            yield {"choices": [{"message": {"content": "{}"}}]}
            return

        yield {"choices": [{"delta": {"content": "E2E assistant reply"}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


# ---------------------------------------------------------------------------
# Fake scheduled agent runner
# ---------------------------------------------------------------------------


class _FakeAgentRunner:
    """Replaces ScheduledAgentRunner so we don't need in-process ASGI."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(
        self,
        *,
        agent: Any,
        prompt: str,
        trace_id: str | None = None,
        timeout_seconds: float = 300.0,
        datasource_id: int | None = None,
    ) -> ScheduleRuntimeResult:
        self.calls.append(
            {
                "agent_id": agent.id,
                "prompt": prompt,
                "trace_id": trace_id,
                "datasource_id": datasource_id,
            }
        )
        return ScheduleRuntimeResult(
            run_id="fake-agent-run-1",
            status="success",
            output={"assistant_message": "scheduled agent ok"},
            output_summary="scheduled agent ok",
            error_class=None,
            error_message=None,
            duration_ms=50,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("PRAXIS_CODING_ENGINE", "aider_like")

    db_path = tmp_path / "e2e-chat-agent-schedule.db"
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

    fake_llm = _FakeLLM()
    monkeypatch.setattr("app.services.chat.stream_helpers.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.chat.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.llm.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.scope_adapter_base.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.reasoning_engine.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.services.agent.build_verify_loop.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr("app.api.chat_agent_draft.get_llm_client", lambda: fake_llm)

    fake_runner = _FakeAgentRunner()

    with TestClient(main_module.app) as client:
        yield client, session_local, fake_runner

    set_scheduler_worker(None)
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_vds_lines(response) -> list[str]:
    lines = []
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def _chat_stream(client: TestClient, conversation_id: int, content: str) -> list[str]:
    with client.stream(
        "POST",
        f"/api/v1/chat/{conversation_id}/stream",
        json={"content": content},
    ) as resp:
        assert resp.status_code == 200, resp.text
        return _parse_vds_lines(resp)


def _save_agent_stream(client: TestClient, conversation_id: int) -> tuple[int, str]:
    """Call save-agent/stream and return (agent_id, agent_name)."""
    with client.stream(
        "POST",
        f"/api/v1/chat/{conversation_id}/save-agent/stream",
        json={"user_input": "save this as agent"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        lines = _parse_vds_lines(resp)

    agent_id = None
    agent_name = None
    for line in lines:
        if not line.startswith("2:"):
            continue
        payload = json.loads(line[2:])
        events = payload if isinstance(payload, list) else [payload]
        for evt in events:
            if isinstance(evt, dict) and evt.get("type") == "save_agent_done":
                agent_id = evt.get("agent_id")
                agent_name = evt.get("agent_name")
    assert agent_id is not None, f"save_agent_done event not found in: {lines}"
    return agent_id, agent_name


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_e2e_chat_save_agent_run_schedule(env, monkeypatch: pytest.MonkeyPatch):
    client, session_local, fake_runner = env

    # ── Step 1: Create conversation and multi-turn chat ──────────────

    conv = client.post("/api/v1/conversations", json={"title": "e2e-agent-lifecycle"})
    assert conv.status_code == 201, conv.text
    conversation_id = conv.json()["id"]

    # Turn 1
    vds1 = _chat_stream(client, conversation_id, "List all slow queries from last week")
    assert any(line.startswith("0:") for line in vds1), "should have assistant text"
    assert any(line.startswith("d:") for line in vds1), "should have finish message"

    # Turn 2
    vds2 = _chat_stream(client, conversation_id, "Now show me the top 5 tables by size")
    assert any(line.startswith("d:") for line in vds2)

    # Turn 3
    vds3 = _chat_stream(client, conversation_id, "Generate an optimization report")
    assert any(line.startswith("d:") for line in vds3)

    # Verify assistant messages persisted (user turns stored as ChatEvents, not Messages)
    msgs = client.get(f"/api/v1/messages/conversation/{conversation_id}")
    assert msgs.status_code == 200, msgs.text
    assistant_msgs = [m for m in msgs.json() if m["role"] == "assistant"]
    assert len(assistant_msgs) == 3, f"expected 3 assistant messages, got {len(assistant_msgs)}"

    # Verify chat events recorded for each turn
    events = client.get(f"/api/v1/chat/{conversation_id}/events")
    assert events.status_code == 200, events.text
    done_events = [e for e in events.json() if e.get("event_type") == "done"]
    assert len(done_events) == 3, f"expected 3 done events (one per turn), got {len(done_events)}"

    # ── Step 2: Save conversation as Agent ───────────────────────────

    agent_id, agent_name = _save_agent_stream(client, conversation_id)
    assert isinstance(agent_id, int)
    assert agent_name  # non-empty

    # Verify agent exists via API
    agent_resp = client.get(f"/api/v1/agents/{agent_id}")
    assert agent_resp.status_code == 200, agent_resp.text
    agent = agent_resp.json()
    assert agent["status"] == "active"
    assert agent["agent_type"] == "custom"
    assert agent["prompt"]  # non-empty prompt

    # ── Step 3: Run the agent ────────────────────────────────────────

    run_resp = client.post(
        f"/api/v1/agents/{agent_id}/run",
        json={"title": "e2e agent run"},
    )
    assert run_resp.status_code == 201, run_resp.text
    run_data = run_resp.json()
    run_conversation_id = run_data["conversation"]["id"]
    assert run_data["conversation"]["agent_id"] == agent_id

    # Stream a message in the agent run conversation
    run_vds = _chat_stream(client, run_conversation_id, "Execute the saved analysis")
    assert any(line.startswith("d:") for line in run_vds)

    # Verify the run conversation has messages
    run_msgs = client.get(f"/api/v1/messages/conversation/{run_conversation_id}")
    assert run_msgs.status_code == 200
    assert any(m["role"] == "assistant" for m in run_msgs.json())

    # ── Step 4: Create a schedule targeting the agent ─────────────────

    schedule_resp = client.post(
        "/api/v1/schedules",
        json={
            "name": "e2e-agent-schedule",
            "target_type": "agent",
            "target_id": agent_id,
            "schedule_type": "interval",
            "interval_seconds": 3600,
            "input_prompt": "Run the saved analysis automatically",
        },
    )
    assert schedule_resp.status_code == 201, schedule_resp.text
    schedule = schedule_resp.json()
    schedule_id = schedule["id"]
    assert schedule["target_type"] == "agent"
    assert schedule["target_id"] == agent_id
    assert schedule["status"] == "active"

    # ── Step 5: Run-now via fake worker ──────────────────────────────

    class _FakeWorker:
        def __init__(self, runner: _FakeAgentRunner):
            self._runner = runner
            self._session_factory = session_local

        def health(self):
            return {"running": True}

        async def submit_now(self, schedule_id: int, trace_id: str):
            db = self._session_factory()
            try:
                sched = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
                if sched and str(sched.target_type) == "agent":
                    agent_record = (
                        db.query(models.Agent).filter(models.Agent.id == sched.target_id).first()
                    )
                    if agent_record:
                        result = await self._runner.invoke(
                            agent=agent_record,
                            prompt=str(sched.input_prompt or ""),
                            trace_id=trace_id,
                            datasource_id=sched.datasource_id,
                        )
                        run_record = models.ScheduleRun(
                            schedule_id=schedule_id,
                            run_id=result.run_id,
                            status=result.status,
                            trigger_type="manual",
                            output_summary=result.output_summary,
                        )
                        db.add(run_record)
                        db.commit()
                        db.refresh(run_record)
                        return result.run_id, run_record.id
            finally:
                db.close()
            return f"fallback-{schedule_id}", None

    monkeypatch.setattr(
        schedules_api,
        "get_scheduler_worker",
        lambda: _FakeWorker(fake_runner),
    )

    run_now_resp = client.post(f"/api/v1/schedules/{schedule_id}/run-now")
    assert run_now_resp.status_code == 200, run_now_resp.text
    run_now_data = run_now_resp.json()
    assert run_now_data["run_id"] == "fake-agent-run-1"
    assert run_now_data["schedule_id"] == schedule_id

    # Verify the agent runner was invoked with correct params
    assert len(fake_runner.calls) == 1
    call = fake_runner.calls[0]
    assert call["agent_id"] == agent_id
    assert call["prompt"] == "Run the saved analysis automatically"

    # Verify schedule run recorded in history
    runs_resp = client.get(f"/api/v1/schedules/{schedule_id}/runs")
    assert runs_resp.status_code == 200, runs_resp.text
    runs = runs_resp.json()
    assert any(r["run_id"] == "fake-agent-run-1" for r in runs)
    matching_run = next(r for r in runs if r["run_id"] == "fake-agent-run-1")
    assert matching_run["status"] == "success"
