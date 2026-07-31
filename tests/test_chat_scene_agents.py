from app.services.chat.scene_agents import SceneAgentRegistry
from app.services.chat.stream_helpers import _extract_scene_agent_payload


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
