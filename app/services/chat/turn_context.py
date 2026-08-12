"""Shared agent turn context builder used by both Chat and Scheduler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jinja2.exceptions import TemplateNotFound
from sqlalchemy.orm import Session

from app.models import models
from app.services.chat.capabilities import (
    CapabilityBuildInput,
    build_prompt_capability_context,
    list_active_skill_models,
    list_bound_services,
    list_knowledge_bases,
)
from app.services.chat.scene_agents import SceneAgentPayload
from app.services.chat.tool_binding import inject_service_tools
from app.services.platform.prompt_loader import PromptLoader
from app.services.response_style import build_response_style_prompt
from app.skills.store import skill_store
from app.tools.registry import registry


def _filter_tools_by_agent(agent: models.Agent | None) -> list[dict]:
    del agent
    return registry.get_openai_functions()


def _bind_default_datasource_to_tools(tools: list[dict], datasource_id: int | None) -> list[dict]:
    import copy

    if datasource_id is None:
        return tools
    patched = copy.deepcopy(tools)
    for tool in patched:
        fn = tool.get("function", {})
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])
        if "datasource_id" in props:
            props["datasource_id"]["description"] = (
                f"Datasource ID (defaults to current session datasource {datasource_id}; can be omitted)"
            )
            if isinstance(required, list) and "datasource_id" in required:
                params["required"] = [x for x in required if x != "datasource_id"]
    return patched


@dataclass(frozen=True)
class TurnContextExtras:
    pending_actions: list[models.PendingAction] | None = None
    handoff_payload: dict[str, Any] | None = None
    resolved_scene_agent: Any | None = None
    scene_payload: SceneAgentPayload | None = None
    scene_fallback_payload: dict[str, Any] | None = None
    selected_skills: list[Any] | None = None
    locale: str | None = None


def _build_pending_confirmation_block(pending_actions: list[models.PendingAction] | None) -> str:
    if not pending_actions:
        return ""
    previews: list[str] = []
    action_types: list[str] = []
    for action in pending_actions[:5]:
        payload = action.payload or {}
        sql_preview = str(payload.get("sql_preview") or payload.get("sql") or "").strip()
        object_preview = str(payload.get("preview") or "").strip()
        previews.append((sql_preview or object_preview)[:120])
        action_types.append(str(action.action_type or "").strip())
    return PromptLoader.render(
        "chat/prompts/pending_confirmation.tpl",
        pending_action_count=len(pending_actions),
        pending_action_types=action_types,
        pending_previews=previews,
    )


def _build_handoff_context_block(handoff_payload: dict[str, Any] | None) -> str:
    if not isinstance(handoff_payload, dict):
        return ""
    source = (
        handoff_payload.get("source") if isinstance(handoff_payload.get("source"), dict) else {}
    )
    source_label = (
        str(source.get("label") or "").strip() or str(source.get("page") or "").strip() or "unknown"
    )
    source_entry = str(source.get("entry") or "").strip() or "unknown"
    facts = handoff_payload.get("facts") if isinstance(handoff_payload.get("facts"), list) else []
    context = (
        handoff_payload.get("context") if isinstance(handoff_payload.get("context"), dict) else {}
    )
    datasource = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
    focus = context.get("focus") if isinstance(context.get("focus"), dict) else {}
    signals = context.get("signals") if isinstance(context.get("signals"), list) else []
    current_plan = (
        context.get("current_plan") if isinstance(context.get("current_plan"), dict) else {}
    )

    datasource_line = ""
    if datasource:
        datasource_line = (
            f"id={datasource.get('id')}, name={datasource.get('name') or '-'}, "
            f"cluster_key={datasource.get('cluster_key') or '-'}"
        )

    focus_parts: list[str] = []
    for key in ("kind", "sql_id", "db_name", "user_name"):
        value = focus.get(key)
        if value in (None, ""):
            continue
        focus_parts.append(f"{key}={value}")
    focus_line = ", ".join(focus_parts)

    current_plan_line = ""
    if current_plan:
        current_plan_line = (
            f"plan_id={current_plan.get('plan_id') or '-'}, "
            f"plan_hash={current_plan.get('plan_hash') or '-'}, "
            f"table_scan={current_plan.get('table_scan') if current_plan.get('table_scan') is not None else '-'}"
        )

    signal_lines: list[str] = []
    for signal in signals[:6]:
        if not isinstance(signal, dict):
            continue
        signal_parts = [
            str(signal.get("key") or "").strip() or "unknown",
            str(signal.get("severity") or "").strip() or "info",
            str(signal.get("summary") or "").strip() or "",
        ]
        evidence = str(signal.get("evidence") or "").strip()
        line = " | ".join(part for part in signal_parts if part)
        if evidence:
            line += f" | evidence={evidence}"
        signal_lines.append(line)

    investigation_steps = (
        context.get("investigation_steps")
        if isinstance(context.get("investigation_steps"), list)
        else []
    )
    trimmed_steps = [
        str(item or "").strip() for item in investigation_steps[:6] if str(item or "").strip()
    ]

    normalized_facts = [
        item
        for item in facts[:6]
        if isinstance(item, dict)
        and str(item.get("label") or "").strip()
        and str(item.get("value") or "").strip()
    ]
    return PromptLoader.render(
        "chat/prompts/handoff_context.tpl",
        source_label=source_label,
        source_entry=source_entry,
        handoff_type=str(handoff_payload.get("type") or "").strip() or "unknown",
        title=str(handoff_payload.get("title") or "").strip(),
        summary=str(handoff_payload.get("summary") or "").strip(),
        datasource_line=datasource_line,
        focus_line=focus_line,
        facts=normalized_facts,
        sql_text=str(context.get("sql_text") or "").strip(),
        current_plan_line=current_plan_line,
        signals=signal_lines,
        ai_summary=str(context.get("ai_summary") or "").strip(),
        investigation_steps=trimmed_steps,
    )


def _build_handoff_policy_block(handoff_payload: dict[str, Any] | None) -> str:
    if not isinstance(handoff_payload, dict):
        return ""
    return PromptLoader.render("chat/prompts/handoff_policy.tpl")


def _build_scope_block(scope_context: dict[str, Any] | None) -> str:
    if not scope_context:
        return ""
    return PromptLoader.render(
        "chat/prompts/scope_context.tpl",
        scope_type=scope_context["scope_type"],
        scope_object_type=scope_context["scope_object_type"],
        scope_object_id=scope_context["scope_object_id"],
    )


def _build_knowledge_block(knowledge_bases: list[Any], *, current_db_type: str) -> str:
    if not knowledge_bases:
        return ""
    from app.services.knowledge.search_tools import read_kb_meta

    kb_items: list[dict[str, Any]] = []
    for kb in knowledge_bases:
        meta = read_kb_meta(int(kb.id)) or {}
        versions = meta.get("versions") if isinstance(meta.get("versions"), list) else []
        doc_count = len(getattr(kb, "documents", []) or [])
        kb_items.append(
            {
                "id": kb.id,
                "name": kb.name,
                "tags": list(kb.tags or []),
                "doc_count": doc_count,
                "pack_id": getattr(kb, "pack_id", None),
                "db_type": meta.get("db_type"),
                "default_version": getattr(kb, "version", None) or meta.get("version"),
                "versions": [
                    str(item.get("label") or item.get("branch") or "").strip()
                    for item in versions
                    if isinstance(item, dict)
                    and str(item.get("label") or item.get("branch") or "").strip()
                ],
            }
        )
    return PromptLoader.render(
        "chat/prompts/knowledge_base.tpl",
        knowledge_bases=kb_items,
        current_db_type=current_db_type,
    )


def _build_scene_block(
    *,
    resolved_scene_agent: Any | None,
    scene_payload: SceneAgentPayload | None,
    scene_fallback_payload: dict[str, Any] | None,
) -> str:
    if resolved_scene_agent and scene_payload:
        return resolved_scene_agent.build_prompt_block(scene_payload)
    if scene_fallback_payload:
        return PromptLoader.render(
            "chat/prompts/scene_agents/default.tpl",
            key=str(scene_fallback_payload.get("key") or "").strip(),
            context_json=json.dumps(
                scene_fallback_payload.get("context") or {}, ensure_ascii=False
            ),
            focus_object_json=json.dumps(
                scene_fallback_payload.get("focus_object") or {}, ensure_ascii=False
            ),
        )
    return ""


def _build_db_type_block(db_type: str) -> str:
    try:
        return PromptLoader.render(f"chat/prompts/db_type/{db_type}.tpl")
    except TemplateNotFound:
        return ""


def build_agent_turn_context(
    conversation: models.Conversation,
    db: Session,
    *,
    scope_context: dict[str, Any] | None = None,
    declared_tool_names: list[str] | None = None,
    selected_skills: list[Any] | None = None,
    extra: TurnContextExtras | None = None,
) -> tuple[str, list[dict], list[str]]:
    """Build system_prompt, tools, and declared_tool_names for an agent turn.

    Used by both Chat (chat_stream) and Scheduler (ScheduledAgentRunner) to
    ensure identical context construction.

    Returns (system_prompt, tools, declared_tool_names).
    """
    agent = conversation.agent
    datasource_id = conversation.datasource_id
    extra = extra or TurnContextExtras()

    selected_datasource = None
    if datasource_id is not None:
        selected_datasource = (
            db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
        )

    tools = _filter_tools_by_agent(agent)
    tools = _bind_default_datasource_to_tools(tools, datasource_id)
    tools = inject_service_tools(tools, datasource_id, db)

    if declared_tool_names is None:
        declared_tool_names = []
        if agent and isinstance(agent.tools, list):
            from app.services.chat.capabilities.collectors import (
                normalize_declared_tool_names as _norm,
            )

            declared_tool_names = _norm(agent.tools)

    loaded_skills = skill_store.list_skills()
    if selected_skills is None:
        active_skill_names = (
            list(conversation.active_skills or [])
            if isinstance(conversation.active_skills, list)
            else []
        )
        if not active_skill_names and agent and isinstance(agent.skills, list):
            active_skill_names = list(agent.skills)
        selected_skills = list_active_skill_models(active_skill_names, loaded_skills)

    knowledge_bases = list_knowledge_bases(db)
    _, capability_prompt = build_prompt_capability_context(
        CapabilityBuildInput(
            tools=tools,
            declared_tool_names=declared_tool_names,
            datasource=selected_datasource,
            services=list_bound_services(db, selected_datasource),
            knowledge_bases=knowledge_bases,
            active_skills=selected_skills,
            scene_key=(
                extra.resolved_scene_agent.key
                if extra.resolved_scene_agent
                else extra.scene_fallback_payload.get("key")
                if isinstance(extra.scene_fallback_payload, dict)
                else None
            ),
            scene_focus=(
                extra.scene_payload.focus_object
                if extra.scene_payload and isinstance(extra.scene_payload.focus_object, dict)
                else extra.scene_fallback_payload.get("focus_object")
                if isinstance(extra.scene_fallback_payload, dict)
                and isinstance(extra.scene_fallback_payload.get("focus_object"), dict)
                else None
            ),
            scope_context=scope_context,
        )
    )

    skills_block = ""
    if selected_skills:
        skills_block = "Loaded Skills:\n"
        for skill in selected_skills:
            skills_block += f"\n## {skill.name}\n{skill.rules_prompt or skill.prompt}\n"
        skills_block += (
            "\nLoaded Skill Execution Priority:\n"
            "- When an active skill defines a domain workflow or stop condition, follow that workflow before falling back to generic SQL-first exploration.\n"
            "- Do not bypass an active skill's required discovery path just because a generic database tool is available.\n"
            "- If an active skill explicitly requires service/API or knowledge lookup first, complete that path before trying direct SQL alternatives.\n"
        )

    ds_attrs_json = ""
    if selected_datasource:
        ds_attrs = {
            k: v
            for k, v in (selected_datasource.attributes or {}).items()
            if v is not None and v != ""
        }
        if ds_attrs:
            ds_attrs_json = json.dumps(ds_attrs, ensure_ascii=False)

    agent_base_prompt = "You are a helpful database assistant."
    if agent:
        agent_base_prompt = str(agent.prompt or "").strip() or agent_base_prompt

    db_type = selected_datasource.db_type if selected_datasource else "unknown"

    turn_context_block = PromptLoader.render(
        "chat/prompts/chat_core_agent.tpl",
        response_style_block=build_response_style_prompt(locale=extra.locale if extra else None),
        datasource_id=datasource_id,
        db_type=db_type,
        db_type_block=_build_db_type_block(db_type),
        cluster_key=(selected_datasource.cluster_key if selected_datasource else "unknown"),
        tenant_role=(selected_datasource.tenant_role if selected_datasource else "unknown"),
        datasource_attributes_json=ds_attrs_json,
        pending_confirmation_block=_build_pending_confirmation_block(extra.pending_actions),
        handoff_context_block=_build_handoff_context_block(extra.handoff_payload),
        handoff_policy_block=_build_handoff_policy_block(extra.handoff_payload),
        scope_block=_build_scope_block(scope_context),
        scene_block=_build_scene_block(
            resolved_scene_agent=extra.resolved_scene_agent,
            scene_payload=extra.scene_payload,
            scene_fallback_payload=extra.scene_fallback_payload,
        ),
        knowledge_block=_build_knowledge_block(knowledge_bases, current_db_type=db_type),
        capability_block=capability_prompt,
        skills_block=skills_block,
    )

    system_prompt = agent_base_prompt + "\n\n" + turn_context_block

    return system_prompt, tools, declared_tool_names
