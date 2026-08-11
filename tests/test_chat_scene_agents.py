import inspect

import pytest

from app.services.chat.agent import ChatAgent, ChatCoreAgent
from app.services.chat.scene_agents import SceneAgentRegistry
from app.services.chat.scene_agents.session_transaction import SessionTransactionAgent
from app.services.chat.scene_agents.sql_analysis import SqlAnalysisAgent
from app.services.chat.scene_agents.stats_analysis import StatsAnalysisAgent
from app.services.chat.stream_helpers import _extract_scene_agent_payload
from app.services.function.chat_agent import FunctionChatAgent
from app.services.page.chat_agent import PageChatAgent
from app.services.skill.skill_builder_agent import SkillBuilderAgent


@pytest.mark.parametrize(
    ("agent_type", "expected_display_name"),
    [
        (ChatAgent, "Assistant"),
        (FunctionChatAgent, "FunctionChatAgent"),
        (PageChatAgent, "PageChatAgent"),
        (SessionTransactionAgent, "SessionAnalysisAgent"),
        (SqlAnalysisAgent, "SqlAnalysisAgent"),
        (StatsAnalysisAgent, "StatsAnalysisAgent"),
        (SkillBuilderAgent, "skill_builder"),
    ],
)
def test_agent_type_owns_its_display_name(
    agent_type: type[ChatCoreAgent], expected_display_name: str
) -> None:
    assert agent_type.display_name == expected_display_name


def test_build_core_dependency_is_owned_by_build_agents() -> None:
    assert "agent_core" not in inspect.signature(ChatCoreAgent).parameters
    assert "agent_core" in inspect.signature(FunctionChatAgent).parameters
    assert "agent_core" in inspect.signature(PageChatAgent).parameters


def test_extract_scene_agent_payload_prefers_scene_agent() -> None:
    payload = _extract_scene_agent_payload(
        {
            "scene_agent": {
                "key": "stats_analysis",
                "context": {"tab": "risk"},
                "focus_object": {"type": "risk_candidate", "id": 1},
                "tools": ["object_crud", "call_praxis_service"],
                "skills": ["ob-stats-ops"],
            }
        }
    )
    assert payload is not None
    assert payload["key"] == "stats_analysis"
    assert payload["source"] == "scene_agent"
    assert payload["tools"] == ["object_crud", "call_praxis_service"]


def test_extract_scene_agent_payload_maps_legacy_page_agent() -> None:
    payload = _extract_scene_agent_payload(
        {
            "page_agent": {
                "profile": "stats_analysis_agent",
                "context": {"entry": "drawer"},
                "focus_object": {"type": "issue", "id": "failed:12"},
                "tools": ["execute_sql", "call_praxis_service"],
            }
        }
    )
    assert payload is not None
    assert payload["key"] == "stats_analysis"
    assert payload["source"] == "page_agent_compat"
    assert payload["tools"] == ["execute_sql", "call_praxis_service"]


def test_scene_agent_registry_returns_none_for_unknown_scene() -> None:
    registry = SceneAgentRegistry()
    assert registry.resolve("unknown_scene") is None
