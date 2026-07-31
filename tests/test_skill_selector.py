"""Deterministic tests for skill selector logic (no LLM required).

Uses a mock LLM client to verify set arithmetic, always_apply enforcement,
JSON parsing tolerance, invalid-name filtering, and error fallback.
"""

import json
import shutil
from pathlib import Path

import pytest

from app.services.platform.skill_selector import (
    _extract_selector_payload,
    select_skills_for_context,
)
from app.skills.store import SkillStore

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "data" / "skills"


@pytest.fixture()
def skill_store(tmp_path: Path) -> SkillStore:
    target = tmp_path / "skills"
    shutil.copytree(BUILTIN_DIR, target)
    return SkillStore(skills_dir=str(target))


def _make_llm_factory(add=None, remove=None, reason="mock"):
    """Return a factory that produces a mock LLM returning canned JSON."""
    payload = json.dumps({"add": add or [], "remove": remove or [], "reason": reason})

    class MockLLM:
        async def chat(self, messages, tools=None, stream=False):
            yield {"choices": [{"message": {"content": payload}}]}

    return lambda: MockLLM()


def _make_raw_llm_factory(raw_content: str):
    """Return a factory where the LLM returns arbitrary raw text."""

    class MockLLM:
        async def chat(self, messages, tools=None, stream=False):
            yield {"choices": [{"message": {"content": raw_content}}]}

    return lambda: MockLLM()


def _make_error_llm_factory():
    """Return a factory where the LLM raises an exception."""

    class MockLLM:
        async def chat(self, messages, tools=None, stream=False):
            raise RuntimeError("LLM unavailable")
            yield  # noqa: unreachable — make it an async generator

    return lambda: MockLLM()


# -- set arithmetic ----------------------------------------------------------


@pytest.mark.anyio
async def test_add_skills(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(
            add=["mysql-slow-query-triage", "mysql-lock-diagnosis"]
        ),
    )
    assert result["selector_ok"]
    assert "mysql-slow-query-triage" in result["active_skills"]
    assert "mysql-lock-diagnosis" in result["active_skills"]
    assert "mysql-slow-query-triage" in result["added"]


@pytest.mark.anyio
async def test_remove_skills(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        current_active_skill_names=["mysql-slow-query-triage", "mysql-lock-diagnosis"],
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(remove=["mysql-lock-diagnosis"]),
    )
    assert result["selector_ok"]
    assert "mysql-slow-query-triage" in result["active_skills"]
    assert "mysql-lock-diagnosis" not in result["active_skills"]
    assert "mysql-lock-diagnosis" in result["removed"]


@pytest.mark.anyio
async def test_add_and_remove_combined(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        current_active_skill_names=["mysql-slow-query-triage"],
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(
            add=["pg-lock-diagnosis"],
            remove=["mysql-slow-query-triage"],
        ),
    )
    assert result["selector_ok"]
    assert "pg-lock-diagnosis" in result["active_skills"]
    assert "mysql-slow-query-triage" not in result["active_skills"]


# -- always_apply enforcement ------------------------------------------------


@pytest.mark.anyio
async def test_always_apply_cannot_be_removed(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(remove=["skill-layered-diagnosis-policy"]),
    )
    assert result["selector_ok"]
    assert "skill-layered-diagnosis-policy" in result["active_skills"]
    assert "skill-layered-diagnosis-policy" not in result["removed"]


@pytest.mark.anyio
async def test_always_apply_present_even_with_empty_response(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(),
    )
    assert result["selector_ok"]
    assert "skill-layered-diagnosis-policy" in result["active_skills"]


# -- invalid name filtering --------------------------------------------------


@pytest.mark.anyio
async def test_invalid_skill_names_ignored(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(
            add=["nonexistent-skill", "mysql-slow-query-triage"],
            remove=["also-fake"],
        ),
    )
    assert result["selector_ok"]
    assert "mysql-slow-query-triage" in result["active_skills"]
    assert "nonexistent-skill" not in result["active_skills"]


# -- JSON parsing tolerance ---------------------------------------------------


@pytest.mark.anyio
async def test_fenced_json_response(skill_store):
    raw = '```json\n{"add": ["pg-vacuum-health"], "remove": [], "reason": "ok"}\n```'
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_raw_llm_factory(raw),
    )
    assert result["selector_ok"]
    assert "pg-vacuum-health" in result["active_skills"]


@pytest.mark.anyio
async def test_json_embedded_in_prose(skill_store):
    raw = 'Here is my selection:\n{"add": ["pg-lock-diagnosis"], "remove": [], "reason": "needed"}\nDone.'
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=skill_store,
        llm_client_factory=_make_raw_llm_factory(raw),
    )
    assert result["selector_ok"]
    assert "pg-lock-diagnosis" in result["active_skills"]


@pytest.mark.anyio
async def test_garbage_response_treated_as_no_change(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        current_active_skill_names=["mysql-slow-query-triage"],
        skill_store_instance=skill_store,
        llm_client_factory=_make_raw_llm_factory("I don't understand the question"),
    )
    assert result["selector_ok"]
    assert "mysql-slow-query-triage" in result["active_skills"]


# -- error fallback -----------------------------------------------------------


@pytest.mark.anyio
async def test_llm_error_falls_back_to_current(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        current_active_skill_names=["pg-slow-query-triage"],
        skill_store_instance=skill_store,
        llm_client_factory=_make_error_llm_factory(),
    )
    assert not result["selector_ok"]
    assert "pg-slow-query-triage" in result["active_skills"]
    assert result["reason"] == "selector_failed_fallback_keep_current"


# -- empty store edge case ----------------------------------------------------


@pytest.mark.anyio
async def test_empty_store_returns_no_skills(tmp_path):
    store = SkillStore(skills_dir=str(tmp_path))
    result = await select_skills_for_context(
        prompt="test",
        skill_store_instance=store,
        llm_client_factory=_make_llm_factory(add=["anything"]),
    )
    assert result["selector_ok"]
    assert result["active_skills"] == []
    assert result["candidate_count"] == 0


# -- configured_skill_names ---------------------------------------------------


@pytest.mark.anyio
async def test_configured_names_become_initial_active(skill_store):
    result = await select_skills_for_context(
        prompt="test",
        configured_skill_names=["mysql-slow-query-triage", "mysql-lock-diagnosis"],
        skill_store_instance=skill_store,
        llm_client_factory=_make_llm_factory(),
    )
    assert result["selector_ok"]
    assert "mysql-slow-query-triage" in result["active_skills"]
    assert "mysql-lock-diagnosis" in result["active_skills"]


# -- _extract_selector_payload unit tests -------------------------------------


def test_extract_raw_json():
    assert _extract_selector_payload('{"add": [], "remove": []}') == {"add": [], "remove": []}


def test_extract_fenced_json():
    raw = '```json\n{"add": ["x"], "remove": []}\n```'
    assert _extract_selector_payload(raw) == {"add": ["x"], "remove": []}


def test_extract_embedded_json():
    raw = 'Some text {"add": ["x"]} more text'
    assert _extract_selector_payload(raw) == {"add": ["x"]}


def test_extract_empty_returns_none():
    assert _extract_selector_payload("") is None


def test_extract_garbage_returns_none():
    assert _extract_selector_payload("not json at all") is None
