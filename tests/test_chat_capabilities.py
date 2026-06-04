from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import capabilities as capabilities_api
from app.db.database import Base
from app.models import models
from app.services.agent.scheduled_runner import ScheduledAgentRunner
from app.services.chat.capabilities import CapabilityBuildInput, build_capability_context, render_capability_context
from app.skills.store import Skill


def test_build_capability_context_treats_declared_tools_as_hints() -> None:
    context = build_capability_context(
        CapabilityBuildInput(
            tools=[
                {"function": {"name": "execute_sql"}},
                {"function": {"name": "call_praxis_service"}},
                {"function": {"name": "object_crud"}},
            ],
            declared_tool_names=["execute_sql", "call_praxis_service"],
        )
    )

    prompt = render_capability_context(context)

    assert "Available Platform Tools:" in prompt
    assert "Contextual Tool Hints:" in prompt
    assert "not a hard whitelist" in prompt
    assert "execute_sql" in prompt
    assert "call_praxis_service" in prompt
    assert "object_crud" in prompt


def test_build_capability_context_includes_datasource_service_knowledge_skill_scene_and_scope() -> None:
    datasource = SimpleNamespace(
        id=9,
        name="cluster-a-user",
        cluster_key="cluster-a",
        tenant_role="user",
        attributes={"ocp_cluster_id": 1001, "tenant_mode": "mysql"},
    )
    service = SimpleNamespace(name="ocp", service_type="ocp_api")
    knowledge = SimpleNamespace(name="OCP Docs")
    skill = Skill(
        name="ocp-api-guide",
        version="1.0.0",
        description="OCP monitor query playbook",
        database="oceanbase",
        always_apply=False,
        prompt="prompt",
    )

    context = build_capability_context(
        CapabilityBuildInput(
            tools=[{"function": {"name": "call_praxis_service"}}],
            declared_tool_names=[],
            datasource=datasource,
            services=[service],
            knowledge_bases=[knowledge],
            active_skills=[skill],
            scene_key="stats_analysis",
            scene_focus={"type": "issue", "id": 1},
            scope_context={"scope_type": "builder", "scope_object_type": "page", "scope_object_id": "99"},
        )
    )
    prompt = render_capability_context(context)

    assert "Current Datasource:" in prompt
    assert "cluster-a-user" in prompt
    assert "cluster_key=cluster-a" in prompt
    assert "Available Services:" in prompt
    assert "auto_bindable" in prompt
    assert "Knowledge Resources:" in prompt
    assert "examples=OCP Docs" in prompt
    assert "Active Skills:" in prompt
    assert "ocp-api-guide" in prompt
    assert "Scene Context:" in prompt
    assert "stats_analysis" in prompt
    assert "Scope Context:" in prompt
    assert "target=page:99" in prompt


def test_render_capability_context_omits_empty_sections() -> None:
    context = build_capability_context(
        CapabilityBuildInput(
            tools=[],
            declared_tool_names=[],
            datasource=None,
        )
    )

    prompt = render_capability_context(context)

    assert "Current Datasource:" in prompt
    assert "Available Services:" not in prompt
    assert "Knowledge Resources:" not in prompt
    assert "Active Skills:" not in prompt


def test_list_capabilities_returns_structured_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(
        capabilities_api.tool_registry,
        "list_tools",
        lambda: [SimpleNamespace(name="execute_sql", description="run sql", parameters={"type": "object"})],
    )

    result = capabilities_api.list_capabilities()

    assert result["tools"][0]["name"] == "execute_sql"
    assert result["tools"][0]["description"] == "run sql"


class _FakeScheduledChatAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stream_general_chat(
        self,
        *,
        messages,
        tools=None,
        system_prompt=None,
        default_datasource_id=None,
        conversation_id=None,
        scope_context=None,
    ):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system_prompt": system_prompt,
                "default_datasource_id": default_datasource_id,
                "conversation_id": conversation_id,
                "scope_context": scope_context,
            }
        )
        yield {"type": "assistant", "phase": "responding", "data": {"text": "ok"}}
        yield {"type": "done", "phase": "done", "data": {"text_emitted": True}}


@pytest.mark.anyio
async def test_scheduled_runner_includes_shared_capability_prompt(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "scheduled-capabilities.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        datasource = models.DataSource(
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
            attributes={"ocp_cluster_id": 1001},
        )
        agent = models.Agent(
            name="巡检 Agent",
            prompt="你是巡检助手。",
            tools=["call_praxis_service"],
            skills=["ocp-api-guide"],
            status="active",
            agent_type="custom",
        )
        service = models.Service(
            name="ocp",
            service_type="ocp_api",
            resource_ref="cluster:cluster-a",
            status="active",
            config={"host": "127.0.0.1"},
        )
        knowledge = models.KnowledgeBase(name="OCP Docs", description="docs", tags=["ocp"])
        db.add_all([datasource, agent, service, knowledge])
        db.commit()
        db.refresh(datasource)
        db.refresh(agent)
        agent.datasources.append(datasource)
        db.commit()

        # The ScheduledAgentRunner delegates to the ASGI chat stream endpoint.
        # We monkeypatch the endpoint to capture the system prompt it builds.
        from app.api import chat as chat_api_mod
        from app.services.chat import stream_helpers as stream_helpers_mod

        captured_calls: list[dict] = []

        class _FakeStreamingChatService:
            async def chat_with_tools(self, messages=None, tools=None, system_prompt=None, **kwargs):
                captured_calls.append({
                    "messages": messages,
                    "tools": tools,
                    "system_prompt": system_prompt,
                })
                yield {"type": "assistant", "phase": "responding", "data": {"text": "ok"}}
                yield {"type": "done", "phase": "done", "data": {"text_emitted": True}}

        async def _fake_select_dynamic_skills(conversation, messages, latest_user_input):
            return {"active_skills": ["ocp-api-guide"], "added": [], "removed": [], "reason": "test", "selector_ok": True}

        async def _noop_save_messages(messages):
            pass

        async def _noop_save_events(events):
            pass

        monkeypatch.setattr("app.db.database.engine", engine, raising=False)
        monkeypatch.setattr("app.db.database.SessionLocal", factory, raising=False)
        monkeypatch.setattr(chat_api_mod, "get_chat_service", lambda: _FakeStreamingChatService())
        monkeypatch.setattr(chat_api_mod, "_select_dynamic_skills", _fake_select_dynamic_skills)
        monkeypatch.setattr(chat_api_mod, "_save_messages_to_db", _noop_save_messages)
        monkeypatch.setattr(chat_api_mod, "_save_chat_events_to_db", _noop_save_events)

        import app.main as main_module
        from app.services.scheduler.runtime_state import set_scheduler_worker
        main_module.settings.scheduler_autostart = False
        set_scheduler_worker(None)

        runner = ScheduledAgentRunner(session_factory=factory)
        result = await runner.invoke(agent=agent, prompt="检查最近一小时 CPU", datasource_id=datasource.id)

        assert result.status == "success"
        assert captured_calls
        system_prompt = captured_calls[0]["system_prompt"]
        assert "Available Platform Tools:" in system_prompt
        assert "Contextual Tool Hints:" in system_prompt
        assert "Current Datasource:" in system_prompt
    finally:
        db.close()
        engine.dispose()


def test_skill_layered_diagnosis_policy_has_api_priority_exception() -> None:
    """Guard: skill-layered-diagnosis-policy must include an explicit API-priority exception clause.
    Without it the LLM interprets 'default to conventional diagnosis' as a reason to skip the
    OCP API workflow defined by ocp-api-guide (regression observed in conversation #9).
    """
    from app.skills.store import SkillStore

    store = SkillStore()
    store.load()
    skill = store.get("skill-layered-diagnosis-policy")
    assert skill is not None, "skill-layered-diagnosis-policy must exist in skills/"
    assert "ocp-api-guide" in skill.prompt, (
        "skill-layered-diagnosis-policy must name ocp-api-guide in its API-priority exception clause"
    )
    assert "不得以" in skill.prompt, (
        "skill-layered-diagnosis-policy must forbid using '常规诊断' as a reason to skip API workflow"
    )
