from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.core.logging import fmt_kv, get_logger
from app.services.llm import get_llm_client
from app.skills.store import Skill, skill_store

logger = get_logger("services.skill_selector")


def format_skill_context(skill_names: list[str]) -> str:
    """Format selected skills into a prompt-injectable context block.

    Uses the same format as Chat's system_prompt injection so all build
    contexts receive identical Skill content representation.
    """
    if not skill_names:
        return ""
    loaded = skill_store.list_skills()
    name_set = set(skill_names)
    selected: list[Skill] = [s for s in loaded if s.name in name_set]
    if not selected:
        return ""
    lines = ["Loaded Skills:"]
    for skill in selected:
        content = skill.rules_prompt or skill.prompt
        lines.append(f"\n## {skill.name}\n{content}")
    return "\n".join(lines) + "\n"


def _json_loads_safe(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_selector_payload(raw_text: str) -> dict[str, Any] | None:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
        cleaned = cleaned.strip()
    direct = _json_loads_safe(cleaned)
    if direct:
        return direct
    matched = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if matched:
        return _json_loads_safe(matched.group(0))
    return None


async def select_skills_for_context(
    *,
    prompt: str,
    recent_context: list[dict[str, str]] | None = None,
    configured_skill_names: list[str] | None = None,
    current_active_skill_names: list[str] | None = None,
    context_label: str = "unknown",
    llm_client_factory: Callable[[], Any] | None = None,
    skill_store_instance: Any | None = None,
) -> dict[str, Any]:
    """Select relevant skills for a given prompt context using LLM reasoning.

    This is the shared core logic extracted from Chat's _select_dynamic_skills.
    Works for Chat, Function Build, and Page Build contexts identically.

    Args:
        prompt: The latest user message or build prompt.
        recent_context: Recent conversation turns (list of {role, content} dicts).
        configured_skill_names: Skills configured on the agent (candidate priority).
        current_active_skill_names: Currently active skills; defaults to configured.
        context_label: Logging label (conversation_id, function_id, etc.).
    """
    store = skill_store_instance or skill_store
    loaded_skills = store.load()
    skill_by_name = {item.name: item for item in loaded_skills}
    always_apply_names = sorted([item.name for item in loaded_skills if item.always_apply])

    configured_names = [n for n in (configured_skill_names or []) if n in skill_by_name]
    base_active = current_active_skill_names if current_active_skill_names is not None else configured_names
    current_active = sorted(set(n for n in base_active if n in skill_by_name) | set(always_apply_names))

    candidates = loaded_skills

    if not candidates:
        return {
            "active_skills": always_apply_names,
            "added": sorted(set(always_apply_names) - set(current_active)),
            "removed": sorted(set(current_active) - set(always_apply_names)),
            "reason": "no_skills_available",
            "selector_ok": True,
            "candidate_count": 0,
        }

    llm_factory = llm_client_factory or get_llm_client
    llm = llm_factory()
    selector_payload = {
        "latest_user_message": prompt,
        "recent_conversation": recent_context or [],
        "current_active_skills": current_active,
        "candidates": [
            {"name": item.name, "description": item.description, "database": item.database}
            for item in candidates
        ],
    }
    selector_system_prompt = (
        "You are a strict skill selector for a database assistant. "
        "Goal: pick minimum useful skills for current topic progression. "
        "Prefer precision over recall. If uncertain, choose none. "
        "Return JSON only with keys: add (array), remove (array), reason (string). "
        "Rules: add/remove must use candidate skill names only."
    )

    logger.info(
        "selector_start %s",
        fmt_kv(
            context=context_label,
            candidate_count=len(candidates),
            current_active_count=len(current_active),
        ),
    )
    try:
        response = None
        async for chunk in llm.chat(
            [
                {"role": "system", "content": selector_system_prompt},
                {"role": "user", "content": json.dumps(selector_payload, ensure_ascii=False)},
            ],
            tools=None,
            stream=False,
        ):
            response = chunk
            break

        raw_text = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                raw_text = message.get("content") or ""

        parsed = _extract_selector_payload(raw_text) or {}
        valid_candidate_names = {item.name for item in candidates}
        add = [
            name
            for name in parsed.get("add", [])
            if isinstance(name, str) and name in valid_candidate_names
        ]
        remove = [
            name
            for name in parsed.get("remove", [])
            if isinstance(name, str) and name in valid_candidate_names
        ]
        removed_by_selector = set(remove)
        forced_keep = set(always_apply_names)
        effective_remove = sorted(removed_by_selector - forced_keep)
        next_active = sorted((set(current_active) - set(effective_remove)) | set(add) | forced_keep)
        reason = parsed.get("reason") if isinstance(parsed.get("reason"), str) else ""
        logger.info(
            "selector_success %s",
            fmt_kv(
                context=context_label,
                add_count=len(add),
                remove_count=len(effective_remove),
                next_active_count=len(next_active),
            ),
        )
        return {
            "active_skills": next_active,
            "added": sorted(set(next_active) - set(current_active)),
            "removed": sorted(set(current_active) - set(next_active)),
            "reason": reason,
            "selector_ok": True,
            "candidate_count": len(candidates),
        }
    except Exception as e:
        logger.exception(
            "selector_error %s error=%s",
            fmt_kv(context=context_label),
            str(e),
        )
        return {
            "active_skills": current_active,
            "added": [],
            "removed": [],
            "reason": "selector_failed_fallback_keep_current",
            "selector_ok": False,
            "candidate_count": len(candidates),
        }
