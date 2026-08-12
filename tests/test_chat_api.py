import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import chat as chat_api
from app.api import chat_history
from app.api import chat_pending as chat_pending_api
from app.api import conversations as conversations_api
from app.core.config import Settings
from app.db.database import Base
from app.models import models
from app.schemas import schemas
from app.services.chat import stream_helpers as chat_stream_helpers
from app.services.chat import tool_binding as chat_tool_binding
from app.services.chat import turn_context as chat_turn_context
from app.services.chat.stream_helpers import _normalize_json_payload
from app.services.chat.turn_context import TurnContextExtras, build_agent_turn_context
from app.skills.store import SkillStore

_llm_configured = bool(
    os.getenv("PRAXIS_LLM_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_settings_normalize_relative_sqlite_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./frontend/praxis.db")
    monkeypatch.setenv("TRACING_DB_PATH", "./frontend/tracing.db")

    settings = Settings()

    assert settings.database_url == "sqlite:////workspace/praxis/frontend/praxis.db"
    assert settings.tracing_db_path == "/workspace/praxis/frontend/tracing.db"


def test_settings_keep_absolute_and_non_sqlite_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/praxis")
    settings = Settings()
    assert settings.database_url == "postgresql://user:pass@localhost:5432/praxis"

    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/praxis.db")
    settings = Settings()
    assert settings.database_url == f"sqlite:///{Path('/tmp/praxis.db').resolve()}"


def test_normalize_json_payload_serializes_datetime():
    payload = {
        "ok": True,
        "nested": {"ts": datetime(2026, 3, 11, 12, 0, 0)},
    }

    normalized = _normalize_json_payload(payload)

    assert normalized is not None
    assert normalized["ok"] is True
    assert normalized["nested"]["ts"] == "2026-03-11 12:00:00"


@pytest.mark.anyio
async def test_save_messages_to_db_upserts_streaming_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    monkeypatch.setattr("app.db.database.SessionLocal", factory)
    db = factory()
    try:
        conversation = models.Conversation(title="stream-upsert")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        message = models.Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            tool_calls=[{"id": "tc-1", "name": "execute_sql", "result": None}],
            content_parts=[
                {
                    "type": "tool_use",
                    "id": "tc-1",
                    "name": "execute_sql",
                    "result": None,
                }
            ],
        )
        await chat_stream_helpers._save_messages_to_db([message])
        assert message.id is not None

        message.content = "查询完成"
        message.tool_calls[0]["result"] = {"success": True}
        message.content_parts[0]["result"] = {"success": True}
        message.content_parts.append({"type": "text", "text": "查询完成"})
        await chat_stream_helpers._save_messages_to_db([message])

        db.expire_all()
        rows = db.query(models.Message).filter_by(conversation_id=conversation.id).all()
        assert len(rows) == 1
        assert rows[0].id == message.id
        assert rows[0].content == "查询完成"
        assert rows[0].tool_calls[0]["result"]["success"] is True
        assert rows[0].content_parts[-1] == {"type": "text", "text": "查询完成"}
    finally:
        db.close()
        engine.dispose()


def test_stream_user_message_persistence_is_idempotent_and_not_duplicated_in_llm_context(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="stream-user-persistence")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        created = chat_history.ensure_stream_user_message(db, conversation.id, "请检查数据")
        reused = chat_history.ensure_stream_user_message(db, conversation.id, "请检查数据")
        chat_messages, raw_messages = chat_history.load_chat_messages(
            db,
            conversation.id,
            "请检查数据",
        )

        assert created is not None
        assert reused is not None
        assert reused.id == created.id
        assert [(message.role, message.content) for message in raw_messages] == [
            ("user", "请检查数据")
        ]
        assert chat_messages == [{"role": "user", "content": "请检查数据"}]
        user_events = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation.id,
                models.ChatEvent.event_type == "user_message",
            )
            .all()
        )
        assert len(user_events) == 1
        assert user_events[0].payload["message_id"] == created.id
        assert user_events[0].turn_seq == 1
        assert user_events[0].part_seq == 0
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("event_type", ["tool_start", "tool_result"])
def test_map_tool_event_to_step_event_preserves_parallel_flag(event_type: str) -> None:
    event = {
        "type": event_type,
        "data": {
            "tool_call_id": "call-parallel",
            "name": "execute_sql",
            "arguments": '{"sql":"SELECT 1"}',
            "parallel": True,
            "result": {"success": True, "data": {"rows": [{"value": 1}]}},
        },
    }

    mapped = chat_stream_helpers._map_tool_event_to_step_event(
        event,
        trace_id="trace-parallel",
        route_source="chat",
    )

    assert mapped["type"] == ("step_start" if event_type == "tool_start" else "step_result")
    assert mapped["data"]["parallel"] is True


class _FakeSelectorLLM:
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
    ):
        del tools, stream
        _ = messages
        yield {
            "choices": [
                {
                    "message": {
                        "content": '{"add":[],"remove":["skill-layered-diagnosis-policy","ob-stats-ops"],"reason":"switch topic"}'
                    }
                }
            ]
        }


@pytest.mark.anyio
async def test_select_dynamic_skills_keeps_always_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    ob_dir = skills_dir / "oceanbase"
    ob_dir.mkdir(parents=True)
    (skills_dir / "skill-layered-diagnosis-policy.md").write_text(
        """---
name: skill-layered-diagnosis-policy
version: 1.0.0
description: layered diagnosis policy for all skills
database: general
always_apply: true
---
policy prompt
""",
        encoding="utf-8",
    )
    (ob_dir / "ob-stats-ops.md").write_text(
        """---
name: ob-stats-ops
version: 1.0.0
description: stats troubleshooting skill for oceanbase
database: oceanbase
always_apply: false
---
stats prompt
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(chat_stream_helpers, "skill_store", SkillStore(skills_dir=str(skills_dir)))
    monkeypatch.setattr(chat_stream_helpers, "get_llm_client", lambda: _FakeSelectorLLM())

    conversation = SimpleNamespace(
        id=101,
        agent=SimpleNamespace(skills=["skill-layered-diagnosis-policy", "ob-stats-ops"]),
        active_skills=["skill-layered-diagnosis-policy", "ob-stats-ops"],
    )
    selected = await chat_stream_helpers._select_dynamic_skills(
        conversation=conversation,
        messages=[],
        latest_user_input="只看别的主题",
    )
    assert "skill-layered-diagnosis-policy" in selected["active_skills"]
    assert "ob-stats-ops" not in selected["active_skills"]


def test_filter_tools_by_scope_in_builder_mode():
    tools = [
        {"function": {"name": "object_crud"}},
        {"function": {"name": "object_operate"}},
        {"function": {"name": "execute_sql"}},
        {"function": {"name": "custom_tool"}},
    ]
    scope = {"scope_type": "builder", "scope_object_type": "page", "scope_object_id": "1"}
    filtered = chat_api._filter_tools_by_scope(tools, scope)
    names = [tool["function"]["name"] for tool in filtered]
    assert names == ["object_crud", "object_operate", "execute_sql", "custom_tool"]


def test_inject_service_tools_keeps_call_service_without_domain_metadata(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        datasource = models.DataSource(
            name="tenant",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#tenant",
            password="secret",
            database="test",
            status="active",
            attributes={},
        )
        service = models.Service(
            name="ocp",
            service_type="ocp_api",
            resource_ref="cluster:cluster-a",
            status="active",
            config={"host": "127.0.0.1"},
        )
        db.add_all([datasource, service])
        db.commit()
        db.refresh(datasource)

        tools = [
            {"function": {"name": "execute_sql", "parameters": {"properties": {}}}},
            {
                "function": {
                    "name": "call_praxis_service",
                    "parameters": {
                        "properties": {"service_id": {"description": "Service ID"}},
                        "required": ["service_id"],
                    },
                }
            },
        ]

        patched = chat_api._inject_service_tools(tools, datasource.id, db)
        names = [tool["function"]["name"] for tool in patched]
        assert names == ["execute_sql", "call_praxis_service"]
        fn = patched[1]["function"]
        assert "service_id" not in fn["parameters"]["required"]
        description = fn["parameters"]["properties"]["service_id"]["description"]
        assert "已自动绑定 service_id" in description
        assert "ocp_cluster_id" in description
        assert "不要把 ob_cluster_id / ob_tenant_id 用作 OCP targetId" in description
    finally:
        db.close()
        engine.dispose()


def test_inject_service_tools_injects_generic_service_binding_hint(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        datasource = models.DataSource(
            name="tenant",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#tenant",
            password="secret",
            database="test",
            status="active",
            attributes={"ocp_cluster_id": 2, "ocp_tenant_id": 5, "ob_cluster_id": 1001},
        )
        service = models.Service(
            name="ocp",
            service_type="ocp_api",
            resource_ref="cluster:cluster-a",
            status="active",
            config={"host": "127.0.0.1"},
        )
        db.add_all([datasource, service])
        db.commit()
        db.refresh(datasource)
        db.refresh(service)

        tools = [
            {
                "function": {
                    "name": "call_praxis_service",
                    "parameters": {
                        "properties": {"service_id": {"description": "Service ID"}},
                        "required": ["service_id"],
                    },
                }
            },
        ]

        patched = chat_api._inject_service_tools(tools, datasource.id, db)
        fn = patched[0]["function"]
        assert fn["name"] == "call_praxis_service"
        assert "service_id" not in fn["parameters"]["required"]
        description = fn["parameters"]["properties"]["service_id"]["description"]
        assert f"service_id={service.id}" in description
        assert "ocp_cluster_id" in description
        assert "target=OBCLUSTER" in description
        assert "target=OBTENANT" in description
        assert "ob_cluster_id=1001" not in description
        assert "loaded skills" in description
    finally:
        db.close()
        engine.dispose()


def test_resolve_active_build_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "chat-api-scope.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)  # noqa: N806
    db = Session()
    try:
        conv = models.Conversation(title="c1")
        db.add(conv)
        db.flush()
        session = models.BuildSession(
            conversation_id=conv.id,
            scope_type="builder",
            scope_object_type="page",
            scope_object_id="88",
            ttl_seconds=1800,
            heartbeat_at=datetime(2026, 3, 14, 12, 0, 0),
            expires_at=datetime(2099, 1, 1, 0, 0, 0),
            status="active",
            created_at=datetime(2026, 3, 14, 12, 0, 0),
            updated_at=datetime(2026, 3, 14, 12, 0, 0),
        )
        db.add(session)
        db.commit()

        resolved = chat_api._resolve_active_build_scope(db, conv.id)
        assert resolved is not None
        assert resolved["scope_type"] == "builder"
        assert resolved["scope_object_type"] == "page"
        assert resolved["scope_object_id"] == "88"
    finally:
        db.close()
        engine.dispose()


def test_resolve_active_build_scope_closes_expired_session(tmp_path: Path) -> None:
    db_path = tmp_path / "chat-api-expired-scope.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)  # noqa: N806
    db = Session()
    try:
        conv = models.Conversation(title="c1")
        db.add(conv)
        db.flush()
        session = models.BuildSession(
            conversation_id=conv.id,
            scope_type="builder",
            scope_object_type="page",
            scope_object_id="88",
            ttl_seconds=1800,
            heartbeat_at=datetime(2026, 3, 14, 12, 0, 0),
            expires_at=datetime(2020, 1, 1, 0, 0, 0),
            status="active",
            created_at=datetime(2026, 3, 14, 12, 0, 0),
            updated_at=datetime(2026, 3, 14, 12, 0, 0),
        )
        db.add(session)
        db.commit()

        resolved = chat_api._resolve_active_build_scope(db, conv.id)
        assert resolved is None

        refreshed = (
            db.query(models.BuildSession).filter(models.BuildSession.id == session.id).first()
        )
        assert refreshed is not None
        assert refreshed.status == "closed"
    finally:
        db.close()
        engine.dispose()


def test_resolve_active_build_scope_respects_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(chat_tool_binding.settings, "builder_runtime_enabled", False)
    db_path = tmp_path / "chat-api-flag-scope.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)  # noqa: N806
    db = Session()
    try:
        conv = models.Conversation(title="c1")
        db.add(conv)
        db.commit()
        resolved = chat_api._resolve_active_build_scope(db, conv.id)
        assert resolved is None
    finally:
        monkeypatch.setattr(chat_tool_binding.settings, "builder_runtime_enabled", True)
        db.close()
        engine.dispose()


class _FakeStreamingChatService:
    def __init__(self, events: list[dict]):
        self.events = events
        self.calls: list[dict] = []

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        default_datasource_id: int | None = None,
        conversation_id: int | None = None,
        scope_context: dict | None = None,
        use_state_machine: bool | None = None,
        agent_name: str = "",
        task_state: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system_prompt": system_prompt,
                "default_datasource_id": default_datasource_id,
                "conversation_id": conversation_id,
                "scope_context": scope_context,
                "use_state_machine": use_state_machine,
                "agent_name": agent_name,
                "task_state": task_state,
            }
        )
        for event in self.events:
            yield event


def _build_session_factory(tmp_path: Path) -> tuple[Any, Any]:
    db_path = tmp_path / "chat-stream.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return factory, engine


def test_build_agent_turn_context_renders_tpl_slots(tmp_path: Path) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        datasource = models.DataSource(
            name="tenant",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#tenant",
            password="secret",
            database="test",
            status="active",
            attributes={"ocp_cluster_id": 2},
        )
        kb = models.KnowledgeBase(
            name="OCP API 文档",
            description="OCP REST API reference",
            tags=["ocp"],
        )
        conversation = models.Conversation(title="prompt-turn", datasource_id=None)
        db.add_all([datasource, kb, conversation])
        db.commit()
        db.refresh(datasource)
        db.refresh(conversation)
        conversation.datasource_id = datasource.id
        db.commit()
        db.refresh(conversation)

        system_prompt, _tools, _declared = build_agent_turn_context(
            conversation,
            db,
            scope_context={
                "scope_type": "builder",
                "scope_object_type": "page",
                "scope_object_id": "42",
            },
            extra=TurnContextExtras(
                pending_actions=[
                    models.PendingAction(
                        conversation_id=conversation.id,
                        status="pending",
                        action_type="execute_sql",
                        payload={"sql_preview": "ALTER SYSTEM SET x = 1"},
                    )
                ],
                handoff_payload={
                    "type": "page_handoff",
                    "source": {"label": "Stats", "entry": "drilldown"},
                    "summary": "summary",
                    "facts": [{"label": "sql_id", "value": "abc"}],
                    "context": {"signals": [{"key": "cpu", "severity": "warn", "summary": "high"}]},
                },
                scene_fallback_payload={
                    "key": "stats_analysis",
                    "context": {"k": "v"},
                    "focus_object": {"table": "t1"},
                },
            ),
        )

        assert "Pending Confirmation Context:" in system_prompt
        assert "Handoff Context (first turn only):" in system_prompt
        assert "Handoff First-Turn Reply Contract:" in system_prompt
        assert "Builder Scope Context:" in system_prompt
        assert "Knowledge Base (知识库):" in system_prompt
        assert "key: stats_analysis" in system_prompt
    finally:
        db.close()
        engine.dispose()


def test_get_messages_returns_404_for_missing_conversation(tmp_path: Path) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        with pytest.raises(HTTPException) as excinfo:
            conversations_api.get_messages(999, db)
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Conversation not found"
    finally:
        db.close()
        engine.dispose()


def test_get_messages_returns_empty_for_existing_conversation_without_messages(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="empty-conversation")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        assert conversations_api.get_messages(conversation.id, db) == []
    finally:
        db.close()
        engine.dispose()


def _create_conversation_with_scope(db: Session) -> tuple[models.Conversation, models.BuildSession]:
    now = datetime(2026, 3, 14, 12, 0, 0)
    conversation = models.Conversation(title="builder-conv")
    db.add(conversation)
    db.flush()
    session = models.BuildSession(
        conversation_id=conversation.id,
        scope_type="builder",
        scope_object_type="page",
        scope_object_id="99",
        ttl_seconds=1800,
        heartbeat_at=now,
        expires_at=datetime(2099, 1, 1, 0, 0, 0),
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(conversation)
    db.refresh(session)
    return conversation, session


async def _collect_stream_payloads(response: Any) -> list[dict]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    payloads: list[dict] = []
    for line in "".join(chunks).splitlines():
        # SSE format: data: {...}
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: ") :]))
            continue
        # VDS format: 2:[{...}] (data messages)
        if line.startswith("2:"):
            items = json.loads(line[2:])
            if isinstance(items, list):
                payloads.extend(items)
            continue
        # VDS text: 0:"..."
        if line.startswith("0:"):
            text = json.loads(line[2:])
            payloads.append({"type": "assistant", "data": {"text": text}})
            continue
        # VDS finish message: d:{...}
        if line.startswith("d:"):
            payload = json.loads(line[2:])
            payloads.append({"type": "done", "data": payload})
            continue
        # VDS finish step: e:{...}
        if line.startswith("e:"):
            continue
        # VDS tool_call: 9:{...}
        if line.startswith("9:"):
            tc = json.loads(line[2:])
            payloads.append({"type": "step_start", "data": tc})
            continue
        # VDS tool_result: a:{...}
        if line.startswith("a:"):
            tr = json.loads(line[2:])
            payloads.append({"type": "step_result", "data": tr})
            continue
        # VDS reasoning: g:"..."
        if line.startswith("g:"):
            continue
    return payloads


def test_list_chat_events_orders_by_turn_and_part_sequence(tmp_path: Path) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="timeline-order")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        events = [
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="assistant",
                turn_id="turn-2",
                turn_seq=2,
                part_seq=2,
                role="assistant",
                payload={"content": "assistant-late"},
                created_at=datetime(2026, 3, 14, 12, 0, 4),
            ),
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="step_result",
                turn_id="turn-1",
                turn_seq=1,
                part_seq=1,
                payload={"name": "execute_sql"},
                created_at=datetime(2026, 3, 14, 12, 0, 3),
            ),
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="assistant",
                turn_id="turn-1",
                turn_seq=1,
                part_seq=2,
                role="assistant",
                payload={"content": "assistant-after-tool"},
                created_at=datetime(2026, 3, 14, 12, 0, 1),
            ),
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="user_message",
                turn_id="turn-1",
                turn_seq=1,
                part_seq=0,
                role="user",
                payload={"content": "请分析"},
                created_at=datetime(2026, 3, 14, 12, 0, 2),
            ),
        ]
        db.add_all(events)
        db.commit()

        ordered = chat_api.list_chat_events(conversation_id=conversation.id, db=db)

        assert [event.turn_seq for event in ordered] == [1, 1, 1, 2]
        assert [event.part_seq for event in ordered] == [0, 1, 2, 2]
        assert [event.event_type for event in ordered] == [
            "user_message",
            "step_result",
            "assistant",
            "assistant",
        ]
    finally:
        db.close()
        engine.dispose()


def test_load_resumable_task_state_requires_explicit_resume_and_incomplete_state(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="resume-task")
        db.add(conversation)
        db.flush()
        state = {
            "version": 1,
            "task_run_id": "task-resume-1",
            "status": "checkpointed",
            "contract": {"objective": "run audit", "acceptance_criteria": []},
        }
        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="task_state",
                phase="reflecting",
                payload=state,
            )
        )
        db.commit()

        assert (
            chat_api._load_resumable_task_state(
                db,
                conversation_id=conversation.id,
                user_input="分析另一个问题",
            )
            is None
        )
        restored = chat_api._load_resumable_task_state(
            db,
            conversation_id=conversation.id,
            user_input="继续执行",
        )
        assert restored is not None
        assert restored["task_run_id"] == "task-resume-1"

        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="task_state",
                phase="responding",
                payload={**state, "status": "completed"},
            )
        )
        db.commit()
        assert (
            chat_api._load_resumable_task_state(
                db,
                conversation_id=conversation.id,
                user_input="继续",
            )
            is None
        )
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_passes_persisted_checkpoint_to_reasoning_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    fake_service = _FakeStreamingChatService(
        events=[
            {
                "type": "done",
                "phase": "done",
                "data": {"status": "completed", "completed": True},
                "meta": {},
            }
        ]
    )
    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="resume-stream")
        db.add(conversation)
        db.flush()
        state = {
            "version": 1,
            "task_run_id": "task-stream-resume",
            "status": "checkpointed",
            "contract": {"objective": "run audit", "acceptance_criteria": []},
            "steps": [],
            "evidence": [],
            "failure_episodes": [],
            "metrics": {},
        }
        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="checkpoint",
                phase="reflecting",
                payload={"status": "checkpointed", "task_state": state},
            )
        )
        db.commit()

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="继续执行"),
            db=db,
        )
        await _collect_stream_payloads(response)

        assert len(fake_service.calls) == 1
        assert fake_service.calls[0]["task_state"] is not None
        assert fake_service.calls[0]["task_state"]["task_run_id"] == "task-stream-resume"
    finally:
        db.close()
        engine.dispose()


def test_create_get_and_consume_chat_handoff(tmp_path: Path) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        source_datasource = models.DataSource(
            name="cluster-a-sys",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="sys",
            user="root@test#sys",
            password="secret",
            database="oceanbase",
            status="active",
        )
        preferred_datasource = models.DataSource(
            name="cluster-a-user",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#wx",
            password="secret",
            database="app_db",
            status="active",
        )
        db.add_all([source_datasource, preferred_datasource])
        db.commit()
        db.refresh(source_datasource)
        db.refresh(preferred_datasource)

        request = schemas.ChatHandoffCreate(
            title="SQL Analysis · sql-2",
            datasource_id=source_datasource.id,
            preferred_execution_datasource_id=preferred_datasource.id,
            packet=schemas.ChatHandoffPacket(
                type="sql_analysis_live",
                version=1,
                source=schemas.ChatHandoffSource(
                    page="sql_analysis",
                    entry="drawer",
                    label="SQL Analysis",
                ),
                title="继续分析 SQL sql-2",
                summary="app_db · 1 个诊断信号",
                facts=[
                    schemas.ChatHandoffFact(label="DB", value="app_db"),
                    schemas.ChatHandoffFact(label="SQL ID", value="sql-2"),
                ],
                suggested_prompts=["继续分析这条 SQL 的主要风险"],
                context={
                    "datasource": {"id": source_datasource.id, "cluster_key": "cluster-a"},
                    "focus": {"kind": "sql", "sql_id": "sql-2"},
                    "sql_text": "select * from biz_table",
                },
            ),
        )

        created = chat_api.create_chat_handoff(request=request, db=db)
        assert created.conversation.datasource_id == preferred_datasource.id
        assert created.handoff.status == "pending"
        assert created.handoff.packet.type == "sql_analysis_live"
        assert (
            created.handoff.packet.context["execution"]["preferred_execution_datasource_id"]
            == preferred_datasource.id
        )

        fetched = chat_api.get_chat_handoff(
            conversation_id=created.conversation.id,
            handoff_id=created.handoff.id,
            db=db,
        )
        assert fetched.packet.title == "继续分析 SQL sql-2"

        consumed = chat_api.consume_chat_handoff(
            conversation_id=created.conversation.id,
            handoff_id=created.handoff.id,
            db=db,
        )
        assert consumed.status == "consumed"
        assert consumed.consumed_at is not None
    finally:
        db.close()
        engine.dispose()


def test_create_chat_handoff_resolves_preferred_execution_datasource_from_context(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        source_datasource = models.DataSource(
            name="cluster-a-sys",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="sys",
            user="root@test#sys",
            password="secret",
            database="oceanbase",
            status="active",
        )
        preferred_datasource = models.DataSource(
            name="cluster-a-user",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#wx",
            password="secret",
            database="app_db",
            status="active",
        )
        db.add_all([source_datasource, preferred_datasource])
        db.commit()
        db.refresh(source_datasource)
        db.refresh(preferred_datasource)

        request = schemas.ChatHandoffCreate(
            title="SQL Analysis · sql-3",
            datasource_id=source_datasource.id,
            packet=schemas.ChatHandoffPacket(
                type="sql_analysis_live",
                version=1,
                source=schemas.ChatHandoffSource(
                    page="sql_analysis",
                    entry="drawer",
                    label="SQL Analysis",
                ),
                title="继续分析 SQL sql-3",
                summary="app_db · 1 个诊断信号",
                facts=[
                    schemas.ChatHandoffFact(label="Tenant", value="1002"),
                    schemas.ChatHandoffFact(label="DB", value="app_db"),
                ],
                suggested_prompts=["结合 schema 结构，给出更详细的建议"],
                context={
                    "datasource": {
                        "id": source_datasource.id,
                        "cluster_key": "cluster-a",
                        "tenant_id": 1002,
                        "tenant_name": "wx",
                        "db_name": "app_db",
                    },
                    "focus": {"kind": "sql", "sql_id": "sql-3", "db_name": "app_db"},
                },
            ),
        )

        created = chat_api.create_chat_handoff(request=request, db=db)
        assert created.conversation.datasource_id == preferred_datasource.id
        assert (
            created.handoff.packet.context["execution"]["preferred_execution_datasource_id"]
            == preferred_datasource.id
        )
        assert (
            created.handoff.packet.context["execution"]["source_datasource_id"]
            == source_datasource.id
        )
    finally:
        db.close()
        engine.dispose()


def test_create_chat_handoff_keeps_source_datasource_when_preferred_execution_match_is_ambiguous(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        source_datasource = models.DataSource(
            name="cluster-a-sys",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="sys",
            user="root@test#sys",
            password="secret",
            database="oceanbase",
            status="active",
        )
        first_user = models.DataSource(
            name="cluster-a-user-1",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#u1",
            password="secret",
            database="shared_db",
            status="active",
        )
        second_user = models.DataSource(
            name="cluster-a-user-2",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#u2",
            password="secret",
            database="shared_db",
            status="active",
        )
        db.add_all([source_datasource, first_user, second_user])
        db.commit()
        db.refresh(source_datasource)

        request = schemas.ChatHandoffCreate(
            title="SQL Analysis · ambiguous",
            datasource_id=source_datasource.id,
            packet=schemas.ChatHandoffPacket(
                type="sql_analysis_live",
                version=1,
                source=schemas.ChatHandoffSource(
                    page="sql_analysis",
                    entry="drawer",
                    label="SQL Analysis",
                ),
                title="继续分析 SQL ambiguous",
                summary="shared_db · 需要确认实际租户",
                facts=[schemas.ChatHandoffFact(label="DB", value="shared_db")],
                suggested_prompts=["先确认这条 SQL 属于哪个租户"],
                context={
                    "datasource": {
                        "id": source_datasource.id,
                        "cluster_key": "cluster-a",
                        "db_name": "shared_db",
                    },
                    "focus": {"kind": "sql", "sql_id": "sql-ambiguous", "db_name": "shared_db"},
                },
            ),
        )

        created = chat_api.create_chat_handoff(request=request, db=db)
        assert created.conversation.datasource_id == source_datasource.id
        assert (
            created.handoff.packet.context["execution"]["preferred_execution_datasource_id"]
            == source_datasource.id
        )
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_does_not_override_existing_conversation_datasource(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        original_datasource = models.DataSource(
            name="cluster-a-user",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-a",
            tenant_role="user",
            user="root@test#wx",
            password="secret",
            database="app_db",
            status="active",
        )
        scene_datasource = models.DataSource(
            name="cluster-b-user",
            host="127.0.0.2",
            port=2881,
            db_type="oceanbase",
            cluster_key="cluster-b",
            tenant_role="user",
            user="root@test#other",
            password="secret",
            database="other_db",
            status="active",
        )
        db.add_all([original_datasource, scene_datasource])
        db.commit()
        db.refresh(original_datasource)
        db.refresh(scene_datasource)

        conversation = models.Conversation(
            title="stats-scene-stream-existing",
            datasource_id=original_datasource.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        async def _fake_select_dynamic_skills(
            conversation: Any,
            messages: list[dict],
            latest_user_input: str,
        ) -> dict[str, Any]:
            del conversation, messages, latest_user_input
            return {
                "active_skills": [],
                "added": [],
                "removed": [],
                "reason": "test",
                "selector_ok": True,
            }

        async def _noop_save_messages(messages: list[models.Message]) -> None:
            del messages

        async def _noop_save_events(events: list[models.ChatEvent]) -> None:
            del events

        fake_service = _FakeStreamingChatService(
            events=[
                {
                    "type": "assistant",
                    "phase": "responding",
                    "data": {"text": "这是诊断结论。"},
                },
                {
                    "type": "done",
                    "phase": "done",
                    "data": {"assistant_message": "这是诊断结论。"},
                },
            ]
        )
        monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
        monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
        monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
        monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(
                content="继续分析这张表",
                scene_agent=schemas.SceneAgentRequest(
                    key="stats_analysis",
                    context={
                        "datasource": {
                            "id": scene_datasource.id,
                            "cluster_key": "cluster-b",
                            "tenant_role": "user",
                        },
                    },
                    focus_object={
                        "type": "issue",
                        "table_name": "tb_transactions",
                        "tenant_name": "wx",
                    },
                ),
            ),
            request=None,
            db=db,
        )

        assert isinstance(response, StreamingResponse)
        db.refresh(conversation)
        assert conversation.datasource_id == original_datasource.id
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_injects_and_consumes_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    fake_service = _FakeStreamingChatService(
        events=[
            {
                "type": "assistant",
                "phase": "responding",
                "data": {"text": "已接收 handoff 上下文"},
                "meta": {},
            },
            {"type": "done", "phase": "done", "data": {"text_emitted": True}, "meta": {}},
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
    monkeypatch.setattr(
        chat_api,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "agent.run"}},
            {"function": {"name": "conversation.save_agent"}},
            {"function": {"name": "execute_sql"}},
        ],
    )
    monkeypatch.setattr(
        chat_turn_context,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "agent.run"}},
            {"function": {"name": "conversation.save_agent"}},
            {"function": {"name": "execute_sql"}},
        ],
    )
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="handoff-conv")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        handoff_event = models.ChatEvent(
            conversation_id=conversation.id,
            event_type="handoff",
            phase="pending",
            payload=_normalize_json_payload(
                {
                    "status": "pending",
                    "type": "sql_analysis_live",
                    "version": 1,
                    "source": {"page": "sql_analysis", "entry": "drawer", "label": "SQL Analysis"},
                    "title": "继续分析 SQL sql-2",
                    "summary": "app_db · 1 个诊断信号",
                    "facts": [{"label": "SQL ID", "value": "sql-2"}],
                    "suggested_prompts": ["继续分析这条 SQL 的主要风险"],
                    "context": {
                        "datasource": {"id": 4, "cluster_key": "cluster-a"},
                        "focus": {"kind": "sql", "sql_id": "sql-2", "db_name": "app_db"},
                        "sql_text": "select * from biz_table",
                        "signals": [
                            {
                                "key": "table_scan_risk",
                                "severity": "warning",
                                "summary": "存在表扫描风险",
                                "evidence": "biz_table",
                            }
                        ],
                    },
                }
            ),
        )
        db.add(handoff_event)
        db.commit()
        db.refresh(handoff_event)

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="继续分析", handoff_id=handoff_event.id),
            db=db,
        )
        payloads = await _collect_stream_payloads(response)
        assert any(item["type"] == "assistant" for item in payloads)
        assert fake_service.calls
        system_prompt = fake_service.calls[0]["system_prompt"]
        assert "Handoff Context (first turn only):" in system_prompt
        assert "sql-2" in system_prompt
        assert "Handoff First-Turn Reply Contract:" in system_prompt
        assert "分析思路 / 关键证据 / 下一步建议" in system_prompt
        assert "2-4 of the most relevant" in system_prompt
        assert "Do not call tools in this turn." in system_prompt
        assert fake_service.calls[0]["tools"] in (None, [])

        refreshed = (
            db.query(models.ChatEvent).filter(models.ChatEvent.id == handoff_event.id).first()
        )
        assert refreshed is not None
        assert refreshed.payload["status"] == "consumed"
        assert refreshed.payload["consumed_at"]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_passes_builder_scope_and_filters_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    fake_service = _FakeStreamingChatService(
        events=[
            {"type": "thinking", "phase": "thinking", "data": {"message": "x"}, "meta": {}},
            {
                "type": "assistant",
                "phase": "responding",
                "data": {"text": "已更新页面"},
                "meta": {},
            },
            {"type": "done", "phase": "done", "data": {"text_emitted": True}, "meta": {}},
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
    monkeypatch.setattr(
        chat_api,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "object_crud"}},
            {"function": {"name": "execute_sql"}},
            {"function": {"name": "custom_tool"}},
        ],
    )
    monkeypatch.setattr(
        chat_turn_context,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "object_crud"}},
            {"function": {"name": "execute_sql"}},
            {"function": {"name": "custom_tool"}},
        ],
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation, _ = _create_conversation_with_scope(db)
        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="请继续构建当前页面"),
            db=db,
        )
        payloads = await _collect_stream_payloads(response)

        assert fake_service.calls
        first_call = fake_service.calls[0]
        assert first_call["scope_context"] is not None
        assert first_call["scope_context"]["scope_type"] == "builder"
        assert first_call["scope_context"]["scope_object_type"] == "page"
        assert first_call["scope_context"]["scope_object_id"] == "99"

        tool_names = [tool["function"]["name"] for tool in first_call["tools"]]
        assert tool_names == ["object_crud", "execute_sql", "custom_tool"]

        streamed_types = [item["type"] for item in payloads]
        assert "skill_delta" in streamed_types
        assert "done" in streamed_types

        builder_scope_event = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation.id,
                models.ChatEvent.event_type == "builder_scope",
            )
            .first()
        )
        assert builder_scope_event is not None
        assert builder_scope_event.payload["scope_object_type"] == "page"
        assert builder_scope_event.payload["scope_object_id"] == "99"
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_persists_assistant_segments_around_tool_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    message_snapshots: list[dict[str, Any]] = []
    persisted_event_types: list[str] = []

    async def _capture_messages(messages: list[models.Message]) -> None:
        for message in messages:
            message_snapshots.append(
                {
                    "content": message.content,
                    "tool_calls": json.loads(json.dumps(message.tool_calls)),
                    "content_parts": json.loads(json.dumps(message.content_parts)),
                }
            )

    async def _capture_events(events: list[models.ChatEvent]) -> None:
        persisted_event_types.extend(event.event_type for event in events)

    fake_service = _FakeStreamingChatService(
        events=[
            {
                "type": "assistant_progress",
                "phase": "planning",
                "data": {
                    "text": "我先确认实际表结构，再开始查询。",
                    "stage": "planning",
                },
                "meta": {},
            },
            {
                "type": "assistant",
                "phase": "responding",
                "data": {
                    "text": "由于当前数据源缺少 OCP 集群关联信息，无法直接调用 OCP API 获取监控数据。"
                },
                "meta": {},
            },
            {
                "type": "tool_start",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-service",
                    "name": "execute_sql",
                    "arguments": '{"sql":"SELECT 1"}',
                },
                "meta": {},
            },
            {
                "type": "tool_result",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-service",
                    "name": "execute_sql",
                    "arguments": '{"sql":"SELECT 1"}',
                    "result": {"success": True, "data": {"rows": []}},
                },
                "meta": {},
            },
            {
                "type": "assistant",
                "phase": "responding",
                "data": {"text": "不过我可以尝试通过数据库查询来获取 CPU 负载信息。"},
                "meta": {},
            },
            {"type": "done", "phase": "done", "data": {"text_emitted": True}, "meta": {}},
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _capture_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _capture_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
    monkeypatch.setattr(
        chat_api,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "execute_sql"}},
            {"function": {"name": "call_praxis_service"}},
        ],
    )
    monkeypatch.setattr(
        chat_turn_context,
        "_filter_tools_by_agent",
        lambda agent: [
            {"function": {"name": "execute_sql"}},
            {"function": {"name": "call_praxis_service"}},
        ],
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="segmented-assistant-conv")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="那给出最近一小时的集群 CPU 负载情况"),
            db=db,
        )
        payloads = await _collect_stream_payloads(response)

        assert any(
            item.get("type") == "step_start" or item.get("type") == "step_result"
            for item in payloads
        )
        # Every visible part is committed as an update of the same logical
        # assistant message, instead of waiting for the terminal event.
        assert len(message_snapshots) >= 5
        progress_snapshot = message_snapshots[0]
        assert progress_snapshot["content_parts"] == [
            {
                "type": "progress",
                "text": "我先确认实际表结构，再开始查询。",
                "stage": "planning",
            }
        ]
        tool_started = next(
            snapshot
            for snapshot in message_snapshots
            if snapshot["tool_calls"] and snapshot["tool_calls"][0]["result"] is None
        )
        assert tool_started["tool_calls"][0]["id"] == "tc-service"
        tool_finished = next(
            snapshot
            for snapshot in message_snapshots
            if snapshot["tool_calls"] and snapshot["tool_calls"][0]["result"] is not None
        )
        assert tool_finished["tool_calls"][0]["result"]["success"] is True

        final_snapshot = message_snapshots[-1]
        assert "由于当前数据源缺少 OCP 集群关联信息" in final_snapshot["content"]
        assert "不过我可以尝试通过数据库查询来获取 CPU 负载信息" in final_snapshot["content"]
        all_parts = final_snapshot["content_parts"] or []
        progress_parts = [
            p for p in all_parts if isinstance(p, dict) and p.get("type") == "progress"
        ]
        assert progress_parts == [
            {
                "type": "progress",
                "text": "我先确认实际表结构，再开始查询。",
                "stage": "planning",
            }
        ]
        assert "我先确认实际表结构" not in final_snapshot["content"]
        text_contents = [
            p["text"] for p in all_parts if isinstance(p, dict) and p.get("type") == "text"
        ]
        assert any("由于当前数据源缺少 OCP 集群关联信息" in t for t in text_contents)
        assert any("不过我可以尝试通过数据库查询来获取 CPU 负载信息" in t for t in text_contents)
        assert persisted_event_types == [
            "assistant_progress",
            "assistant",
            "step_start",
            "step_result",
            "assistant",
            "done",
        ]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_persists_tool_result_and_terminal_checkpoint_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    persisted_events: list[dict[str, Any]] = []
    message_snapshots: list[dict[str, Any]] = []

    async def _capture_events(events: list[models.ChatEvent]) -> None:
        for event in events:
            persisted_events.append(
                {
                    "type": event.event_type,
                    "phase": event.phase,
                    "payload": json.loads(json.dumps(event.payload)),
                }
            )

    async def _capture_messages(messages: list[models.Message]) -> None:
        for message in messages:
            message_snapshots.append(
                {
                    "content": message.content,
                    "tool_calls": json.loads(json.dumps(message.tool_calls)),
                    "content_parts": json.loads(json.dumps(message.content_parts)),
                }
            )

    class _DisconnectAfterToolResult:
        def __init__(self) -> None:
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks >= 4

    task_state = {
        "version": 1,
        "task_run_id": "task-disconnect-1",
        "status": "running",
        "contract": {"objective": "inspect service", "acceptance_criteria": []},
        "steps": [],
        "evidence": [],
        "failure_episodes": [],
        "metrics": {},
    }
    fake_service = _FakeStreamingChatService(
        events=[
            {"type": "thinking", "phase": "thinking", "data": {"message": "x"}, "meta": {}},
            {
                "type": "task_state",
                "phase": "planning",
                "data": task_state,
                "meta": {},
            },
            {
                "type": "tool_start",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-durable",
                    "name": "execute_sql",
                    "arguments": '{"sql":"SELECT 1"}',
                },
                "meta": {},
            },
            {
                "type": "tool_result",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-durable",
                    "name": "execute_sql",
                    "arguments": '{"sql":"SELECT 1"}',
                    "result": {"success": True, "data": {"rows": [{"value": 1}]}},
                },
                "meta": {},
            },
            {
                "type": "assistant",
                "phase": "responding",
                "data": {"text": "should not be reached"},
                "meta": {},
            },
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _capture_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _capture_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
    monkeypatch.setattr(
        chat_api,
        "_filter_tools_by_agent",
        lambda agent: [{"function": {"name": "execute_sql"}}],
    )
    monkeypatch.setattr(
        chat_turn_context,
        "_filter_tools_by_agent",
        lambda agent: [{"function": {"name": "execute_sql"}}],
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="durable-disconnect")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="run durable test"),
            request=_DisconnectAfterToolResult(),
            db=db,
        )
        await _collect_stream_payloads(response)

        event_types = [event["type"] for event in persisted_events]
        assert event_types == [
            "thinking",
            "task_state",
            "step_start",
            "step_result",
            "checkpoint",
            "done",
        ]
        checkpoint = next(event for event in persisted_events if event["type"] == "checkpoint")
        assert checkpoint["payload"]["reason_code"] == "client_disconnected"
        assert checkpoint["payload"]["task_state"]["status"] == "checkpointed"
        done = next(event for event in persisted_events if event["type"] == "done")
        assert done["payload"]["status"] == "incomplete"
        assert done["payload"]["completed"] is False

        final_snapshot = message_snapshots[-1]
        assert final_snapshot["tool_calls"][0]["id"] == "tc-durable"
        assert final_snapshot["tool_calls"][0]["result"]["success"] is True
        assert final_snapshot["content"] == ""
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_reports_scope_violation_from_tool_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    fake_service = _FakeStreamingChatService(
        events=[
            {"type": "thinking", "phase": "thinking", "data": {"message": "x"}, "meta": {}},
            {
                "type": "tool_result",
                "phase": "tool_running",
                "data": {
                    "name": "object_operate",
                    "result": {
                        "success": False,
                        "error": {"code": "scope_violation", "message": "outside builder scope"},
                    },
                },
                "meta": {},
            },
            {"type": "done", "phase": "done", "data": {"text_emitted": False}, "meta": {}},
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
    monkeypatch.setattr(
        chat_api,
        "_filter_tools_by_agent",
        lambda agent: [{"function": {"name": "object_operate"}}],
    )
    monkeypatch.setattr(
        chat_turn_context,
        "_filter_tools_by_agent",
        lambda agent: [{"function": {"name": "object_operate"}}],
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation, _ = _create_conversation_with_scope(db)
        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="发布另一个页面"),
            db=db,
        )
        payloads = await _collect_stream_payloads(response)

        step_result_payloads = [item for item in payloads if item.get("type") == "step_result"]
        assert step_result_payloads
        sr = step_result_payloads[0]
        tool_result = sr.get("result") or sr.get("data", {}).get("result") or {}
        tool_error = tool_result.get("error") or {}
        assert tool_error["code"] == "scope_violation"

        fallback_assistant = [item for item in payloads if item.get("type") == "assistant"]
        assert not fallback_assistant
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_chat_stream_emits_error_user_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    fake_service = _FakeStreamingChatService(
        events=[
            {"type": "thinking", "phase": "thinking", "data": {"message": "x"}, "meta": {}},
            {
                "type": "error",
                "phase": "error",
                "data": {"message": "upstream 429", "error_class": "rate_limited"},
                "meta": {},
            },
            {"type": "done", "phase": "done", "data": {"text_emitted": False}, "meta": {}},
        ]
    )

    monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
    monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
    monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
    monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation, _ = _create_conversation_with_scope(db)
        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="测试错误提示"),
            db=db,
        )
        payloads = await _collect_stream_payloads(response)
        error_events = [item for item in payloads if item.get("type") == "error"]
        assert error_events
        error_event = error_events[0]
        expected = "upstream 429"
        assert (
            error_event.get("message") == expected
            or error_event.get("data", {}).get("message") == expected
        )
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_confirm_pending_object_action_executes_and_updates_event(
    tmp_path: Path,
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="c1")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        token = "object-confirm-1"
        pending = models.PendingAction(
            conversation_id=conversation.id,
            token=token,
            action_type="object_action",
            status="pending",
            payload={
                "batch_id": "batch-1",
                "mode": "crud",
                "object_type": "datasource",
                "action": "list",
                "object_id": None,
                "payload": {},
                "source_text": "/object crud datasource list",
                "capability_key": "object.crud",
            },
        )
        db.add(pending)
        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="step_result",
                phase="reflecting",
                payload={
                    "step_id": "step-confirm-1",
                    "kind": "action",
                    "name": "object.crud",
                    "arguments": "{}",
                    "result": {
                        "success": True,
                        "data": {
                            "requires_confirmation": True,
                            "action_type": "object_action",
                            "action_token": token,
                        },
                        "error": None,
                    },
                },
            )
        )
        db.commit()

        result = await chat_api.confirm_pending_action(
            conversation_id=conversation.id,
            token=token,
            db=db,
        )
        assert result["success"] is True
        assert result["status"] == "executed"
        assert isinstance(result["result"].get("count"), int)

        refreshed = (
            db.query(models.PendingAction)
            .filter(
                models.PendingAction.conversation_id == conversation.id,
                models.PendingAction.token == token,
            )
            .first()
        )
        assert refreshed is not None
        assert refreshed.status == "executed"

        updated_event = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation.id,
                models.ChatEvent.event_type == "step_result",
            )
            .order_by(models.ChatEvent.id.desc())
            .first()
        )
        assert updated_event is not None
        payload = updated_event.payload if isinstance(updated_event.payload, dict) else {}
        result_data_wrapper = (
            payload.get("result") if isinstance(payload.get("result"), dict) else {}
        )
        data = (
            result_data_wrapper.get("data")
            if isinstance(result_data_wrapper.get("data"), dict)
            else {}
        )
        assert data.get("requires_confirmation") is False
        assert data.get("confirmed_action_token") == token
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_confirm_execute_sql_adds_assistant_followup_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="c1")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        db.add(
            models.Message(
                conversation_id=conversation.id,
                role="user",
                content="执行后继续告诉我结果",
            )
        )

        datasource = models.DataSource(
            name="cluster-a-user",
            db_type="mysql",
            host="127.0.0.1",
            port=2881,
            user="monitor",
            password="pwd",
            database="app",
            tenant_role="user",
            cluster_key="cluster-a",
            status="active",
        )
        db.add(datasource)
        db.commit()
        db.refresh(datasource)

        token = "sql-confirm-1"
        pending = models.PendingAction(
            conversation_id=conversation.id,
            token=token,
            action_type="execute_sql",
            status="pending",
            payload={
                "sql": "SHOW TABLES LIKE 't'",
                "intent": "查看表是否存在",
                "resolved_datasource_id": datasource.id,
                "resolved_role": "user",
                "tenant_fingerprint": {"tenant_name": "tenant_a"},
                "execution_fingerprint": "fp-1",
            },
        )
        db.add(pending)
        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="step_result",
                phase="reflecting",
                payload={
                    "step_id": "step-confirm-sql-1",
                    "kind": "tool",
                    "name": "execute_sql",
                    "arguments": '{"sql":"SHOW TABLES LIKE \'t\'"}',
                    "result": {
                        "success": True,
                        "data": {
                            "requires_confirmation": True,
                            "action_type": "execute_sql",
                            "action_token": token,
                        },
                        "error": None,
                    },
                },
            )
        )
        db.commit()

        async def _fake_probe(*args, **kwargs):
            return {"tenant_name": "tenant_a"}

        class _FakePool:
            async def execute_query(self, *args, **kwargs):
                return {
                    "columns": ["Tables_in_app"],
                    "rows": [{"Tables_in_app": "t"}],
                    "row_count": 1,
                }

        class _FakeResumeLLM:
            async def chat(
                self,
                messages,
                tools=None,
                stream=False,
                temperature=None,
                response_format=None,
                reasoning_config=None,
            ):
                del messages, tools, stream, temperature, response_format, reasoning_config
                yield {
                    "choices": [
                        {
                            "message": {
                                "content": "已继续完成这一步：目标表存在，查询返回 1 条结果，可继续下一步分析。"
                            }
                        }
                    ]
                }

        monkeypatch.setattr(chat_pending_api, "probe_tenant_fingerprint", _fake_probe)
        monkeypatch.setattr(
            chat_pending_api, "build_execution_fingerprint", lambda **kwargs: "fp-1"
        )
        monkeypatch.setattr("app.db.connection.get_db_pool", lambda: _FakePool())
        monkeypatch.setattr(chat_pending_api, "get_llm_client", lambda: _FakeResumeLLM())

        result = await chat_api.confirm_pending_action(
            conversation_id=conversation.id, token=token, db=db
        )

        assert result["success"] is True
        assert result["status"] == "executed"
        assert (
            result.get("assistant_message")
            == "已继续完成这一步：目标表存在，查询返回 1 条结果，可继续下一步分析。"
        )

        refreshed = (
            db.query(models.PendingAction)
            .filter(
                models.PendingAction.conversation_id == conversation.id,
                models.PendingAction.token == token,
            )
            .first()
        )
        assert refreshed is not None
        assert refreshed.status == "executed"

        updated_event = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation.id,
                models.ChatEvent.event_type == "step_result",
            )
            .order_by(models.ChatEvent.id.desc())
            .first()
        )
        assert updated_event is not None
        payload = updated_event.payload if isinstance(updated_event.payload, dict) else {}
        result_wrapper = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        data = result_wrapper.get("data") if isinstance(result_wrapper.get("data"), dict) else {}
        assert result_wrapper.get("success") is True
        assert data.get("row_count") == 1

        assistant_messages = (
            db.query(models.Message)
            .filter(
                models.Message.conversation_id == conversation.id,
                models.Message.role == "assistant",
            )
            .order_by(models.Message.id.asc())
            .all()
        )
        assert [item.content for item in assistant_messages] == [
            "已继续完成这一步：目标表存在，查询返回 1 条结果，可继续下一步分析。"
        ]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_confirm_execute_sql_marks_failed_event_without_requiring_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="c1")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        db.add(
            models.Message(
                conversation_id=conversation.id,
                role="user",
                content="执行失败的话继续帮我分析原因",
            )
        )

        datasource = models.DataSource(
            name="cluster-a-user",
            db_type="mysql",
            host="127.0.0.1",
            port=2881,
            user="monitor",
            password="pwd",
            database="app",
            tenant_role="user",
            cluster_key="cluster-a",
            status="active",
        )
        db.add(datasource)
        db.commit()
        db.refresh(datasource)

        token = "sql-confirm-1"
        pending = models.PendingAction(
            conversation_id=conversation.id,
            token=token,
            action_type="execute_sql",
            status="pending",
            payload={
                "sql": "UPDATE t SET a=1",
                "intent": "run update",
                "resolved_datasource_id": datasource.id,
                "resolved_role": "user",
                "tenant_fingerprint": {"tenant_name": "tenant_a"},
                "execution_fingerprint": "fp-1",
            },
        )
        db.add(pending)
        db.add(
            models.ChatEvent(
                conversation_id=conversation.id,
                event_type="step_result",
                phase="reflecting",
                payload={
                    "step_id": "step-confirm-sql-1",
                    "kind": "tool",
                    "name": "execute_sql",
                    "arguments": '{"sql":"UPDATE t SET a=1"}',
                    "result": {
                        "success": True,
                        "data": {
                            "requires_confirmation": True,
                            "action_type": "execute_sql",
                            "action_token": token,
                        },
                        "error": None,
                    },
                },
            )
        )
        db.commit()

        async def _fake_probe(*args, **kwargs):
            return {"tenant_name": "tenant_a"}

        async def _raise_execute(*args, **kwargs):
            raise RuntimeError("table not found")

        class _FakePool:
            async def execute_query(self, *args, **kwargs):
                return await _raise_execute(*args, **kwargs)

        class _FakeFailureResumeLLM:
            async def chat(
                self,
                messages,
                tools=None,
                stream=False,
                temperature=None,
                response_format=None,
                reasoning_config=None,
            ):
                del messages, tools, stream, temperature, response_format, reasoning_config
                yield {
                    "choices": [
                        {
                            "message": {
                                "content": "这次确认后的 SQL 执行失败了，核心报错是 table not found。建议先核对目标表名、当前库以及执行租户是否正确，再决定是否重试。"
                            }
                        }
                    ]
                }

        monkeypatch.setattr(chat_pending_api, "probe_tenant_fingerprint", _fake_probe)
        monkeypatch.setattr(
            chat_pending_api, "build_execution_fingerprint", lambda **kwargs: "fp-1"
        )
        monkeypatch.setattr("app.db.connection.get_db_pool", lambda: _FakePool())
        monkeypatch.setattr(chat_pending_api, "get_llm_client", lambda: _FakeFailureResumeLLM())

        with pytest.raises(HTTPException) as excinfo:
            await chat_api.confirm_pending_action(
                conversation_id=conversation.id, token=token, db=db
            )

        assert excinfo.value.status_code == 400
        refreshed = (
            db.query(models.PendingAction)
            .filter(
                models.PendingAction.conversation_id == conversation.id,
                models.PendingAction.token == token,
            )
            .first()
        )
        assert refreshed is not None
        assert refreshed.status == "failed"

        updated_event = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation.id,
                models.ChatEvent.event_type == "step_result",
            )
            .order_by(models.ChatEvent.id.desc())
            .first()
        )
        assert updated_event is not None
        payload = updated_event.payload if isinstance(updated_event.payload, dict) else {}
        result_wrapper = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        data = result_wrapper.get("data") if isinstance(result_wrapper.get("data"), dict) else {}
        assert result_wrapper.get("success") is False
        assert data.get("requires_confirmation") is False
        assert data.get("confirmed_action_token") == token
        error = result_wrapper.get("error") if isinstance(result_wrapper.get("error"), dict) else {}
        assert error.get("code") == "sql_execution_error"
        assert error.get("message") == "SQL execution error: table not found"
        assert payload.get("message") == "SQL 执行失败：table not found"

        assistant_messages = (
            db.query(models.Message)
            .filter(
                models.Message.conversation_id == conversation.id,
                models.Message.role == "assistant",
            )
            .order_by(models.Message.id.asc())
            .all()
        )
        assert [item.content for item in assistant_messages] == [
            "这次确认后的 SQL 执行失败了，核心报错是 table not found。建议先核对目标表名、当前库以及执行租户是否正确，再决定是否重试。"
        ]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_general_chat_system_prompt_includes_kb_prompt_before_skill_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Guard: _build_knowledge_base_prompt() must be called in the general chat handler
    and its output must appear before 'Loaded Skills:' in the system prompt.

    Regression: a refactor accidentally removed the _build_knowledge_base_prompt() call,
    causing LLM to skip the OCP API knowledge-discovery workflow and fall back to execute_sql
    for monitoring requests (observed in conversation #9).
    """
    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        datasource = models.DataSource(
            name="wx/wx",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="wx",
            tenant_role="user",
            user="root@wx",
            password="secret",
            database="app_db",
            status="active",
            attributes={"ocp_cluster_id": 2, "ocp_tenant_id": 5},
        )
        kb = models.KnowledgeBase(
            name="OCP API 文档",
            description="OCP REST API reference",
            tags=["ocp"],
        )
        conversation = models.Conversation(title="ocp-cpu-query")
        db.add_all([datasource, kb, conversation])
        db.commit()
        db.refresh(datasource)
        db.refresh(conversation)
        conversation.datasource_id = datasource.id
        db.commit()

        monkeypatch.setattr("app.db.database.SessionLocal", factory)

        async def _fake_select_dynamic_skills(
            conversation: Any,
            messages: list[dict],
            latest_user_input: str,
        ) -> dict[str, Any]:
            del conversation, messages, latest_user_input
            # mirror real _select_dynamic_skills: populate the store so list_skills() is non-empty
            chat_stream_helpers.skill_store.load()
            return {
                "active_skills": ["ocp-api-guide"],
                "added": ["ocp-api-guide"],
                "removed": [],
                "reason": "cpu query → ocp-api-guide",
                "selector_ok": True,
            }

        async def _noop_save_messages(messages: list[models.Message]) -> None:
            del messages

        async def _noop_save_events(events: list[models.ChatEvent]) -> None:
            del events

        fake_service = _FakeStreamingChatService(
            events=[
                {"type": "assistant", "phase": "responding", "data": {"text": "ok"}},
                {"type": "done", "phase": "done", "data": {"text_emitted": True}},
            ]
        )
        monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
        monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
        monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
        monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="最近1小时 CPU"),
            request=None,
            db=db,
        )

        assert isinstance(response, StreamingResponse)
        await _collect_stream_payloads(response)
        assert fake_service.calls
        system_prompt = fake_service.calls[0]["system_prompt"]

        assert "Knowledge Base (知识库)" in system_prompt, (
            "_build_knowledge_base_prompt() must be called in general chat handler; "
            "missing → LLM skips knowledge-discovery and goes straight to execute_sql."
        )
        kb_pos = system_prompt.index("Knowledge Base (知识库)")
        loaded_skills_pos = system_prompt.index("Loaded Skills:")
        assert kb_pos < loaded_skills_pos, (
            "Knowledge Base instructions must appear before 'Loaded Skills:' so LLM reads "
            "the discovery workflow before skill API directives."
        )
        assert "ocp-api-guide" in system_prompt
        assert "call_praxis_service" in system_prompt
    finally:
        db.close()
        engine.dispose()


@pytest.mark.skipif(not _llm_configured, reason="requires LLM API key")
@pytest.mark.anyio
async def test_chat_stream_inline_triggers_save_agent_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: when agent_save tool returns action=save_agent, chat stream must
    inline-trigger _stream_save_agent_workflow and emit save_agent_done — not just
    pass the tool result back to the LLM and let it reply with plain text.
    """

    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    async def _fake_build_agent_draft(
        *,
        conversation: Any,
        messages: list[Any],
        events: list[Any],
        user_input: str,
        available_tool_names: set[str],
        available_skill_names: set[str],
    ) -> dict:
        del conversation, messages, events, user_input, available_tool_names, available_skill_names
        return {
            "name": "测试 Agent",
            "description": "从对话保存的测试 Agent",
            "prompt": "你是一个测试 Agent，负责回答数据库相关问题。",
            "tools": ["execute_sql"],
            "skills": [],
        }

    fake_service = _FakeStreamingChatService(
        events=[
            {
                "type": "tool_start",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-save",
                    "name": "agent_save",
                    "arguments": '{"user_input":"保存为 agent"}',
                },
                "meta": {},
            },
            {
                "type": "tool_result",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-save",
                    "name": "agent_save",
                    "result": {
                        "success": True,
                        "data": {
                            "action": "save_agent",
                            "user_input": "保存为 agent",
                        },
                    },
                },
                "meta": {},
            },
        ]
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="save-agent-test")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        db.add(
            models.Message(
                conversation_id=conversation.id,
                role="user",
                content="查询当前时间",
            )
        )
        db.add(
            models.Message(
                conversation_id=conversation.id,
                role="assistant",
                content="当前时间是 2026-05-11。",
            )
        )
        db.commit()

        monkeypatch.setattr("app.db.database.SessionLocal", factory)
        monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
        monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
        monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
        monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
        monkeypatch.setattr(
            chat_api, "_build_agent_draft_from_conversation", _fake_build_agent_draft
        )

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="保存为 agent"),
            request=None,
            db=db,
        )

        assert isinstance(response, StreamingResponse)
        payloads = await _collect_stream_payloads(response)

        event_types = [p.get("type") for p in payloads]
        assert "save_agent_done" in event_types, (
            "chat stream must emit save_agent_done when agent_save tool returns action=save_agent; "
            f"got event types: {event_types}"
        )

        done_payload = next(p for p in payloads if p.get("type") == "save_agent_done")
        assert done_payload["data"]["stage"] == "completed"
        assert done_payload["data"]["agent_name"] == "测试 Agent"
        assert "/agent?editAgentId=" in done_payload["data"]["agent_url"]

        saved_agent = db.query(models.Agent).filter_by(name="测试 Agent").first()
        assert saved_agent is not None, "Agent must be persisted to DB after save_agent_done"
        assert saved_agent.prompt == "你是一个测试 Agent，负责回答数据库相关问题。"
    finally:
        db.close()
        engine.dispose()


@pytest.mark.skipif(not _llm_configured, reason="requires LLM API key")
@pytest.mark.anyio
async def test_chat_stream_save_agent_workflow_emits_status_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Guard: _stream_save_agent_workflow must emit save_agent_status events for
    summarizing_context and saving_agent stages before save_agent_done.
    """

    async def _fake_select_dynamic_skills(
        conversation: Any,
        messages: list[dict],
        latest_user_input: str,
    ) -> dict[str, Any]:
        del conversation, messages, latest_user_input
        return {
            "active_skills": [],
            "added": [],
            "removed": [],
            "reason": "test",
            "selector_ok": True,
        }

    async def _noop_save_messages(messages: list[models.Message]) -> None:
        del messages

    async def _noop_save_events(events: list[models.ChatEvent]) -> None:
        del events

    async def _fake_build_agent_draft(**kwargs: Any) -> dict:
        del kwargs
        return {
            "name": "状态测试 Agent",
            "description": "测试状态阶段",
            "prompt": "你是状态测试 Agent。",
            "tools": [],
            "skills": [],
        }

    fake_service = _FakeStreamingChatService(
        events=[
            {
                "type": "tool_result",
                "phase": "tool_running",
                "data": {
                    "tool_call_id": "tc-save-2",
                    "name": "agent_save",
                    "result": {
                        "success": True,
                        "data": {"action": "save_agent", "user_input": ""},
                    },
                },
                "meta": {},
            },
        ]
    )

    factory, engine = _build_session_factory(tmp_path)
    db = factory()
    try:
        conversation = models.Conversation(title="save-agent-stages-test")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        monkeypatch.setattr("app.db.database.SessionLocal", factory)
        monkeypatch.setattr(chat_api, "_select_dynamic_skills", _fake_select_dynamic_skills)
        monkeypatch.setattr(chat_api, "_save_messages_to_db", _noop_save_messages)
        monkeypatch.setattr(chat_api, "_save_chat_events_to_db", _noop_save_events)
        monkeypatch.setattr(chat_api, "get_chat_service", lambda: fake_service)
        monkeypatch.setattr(
            chat_api, "_build_agent_draft_from_conversation", _fake_build_agent_draft
        )

        response = await chat_api.chat_stream(
            conversation_id=conversation.id,
            message=schemas.ChatStreamRequest(content="保存为 agent"),
            request=None,
            db=db,
        )

        payloads = await _collect_stream_payloads(response)
        event_types = [p.get("type") for p in payloads]

        status_payloads = [p for p in payloads if p.get("type") == "save_agent_status"]
        stages = [p["data"]["stage"] for p in status_payloads]
        assert "summarizing_context" in stages, f"expected summarizing_context stage, got: {stages}"
        assert "saving_agent" in stages, f"expected saving_agent stage, got: {stages}"

        assert event_types.index("save_agent_status") < event_types.index("save_agent_done"), (
            "save_agent_status must appear before save_agent_done"
        )
    finally:
        db.close()
        engine.dispose()
