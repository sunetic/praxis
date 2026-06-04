from __future__ import annotations

from typing import Any

from .contract import CapabilityBuildInput, CapabilityContext, CapabilitySummary


def normalize_declared_tool_names(tool_names: list[str] | None) -> list[str]:
    from app.tools.registry import registry

    normalized: list[str] = []
    for name in tool_names or []:
        text = str(name or "").strip()
        if not text or text in normalized or registry.get(text) is None:
            continue
        normalized.append(text)
    return normalized


def summarize_tool_description(name: str) -> str:
    from app.tools.registry import registry

    tool = registry.get(name)
    description = str(getattr(tool, "description", "") or "").replace("\n", " ").strip()
    if not description:
        return "Platform tool capability"
    if len(description) > 80:
        return description[:79].rstrip() + "…"
    return description


def _collect_tool_capabilities(tools: list[dict], declared_tool_names: list[str]) -> tuple[list[CapabilitySummary], list[CapabilitySummary]]:
    available_names: list[str] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        name = fn.get("name") if isinstance(fn, dict) else None
        if isinstance(name, str) and name not in available_names:
            available_names.append(name)

    tool_summaries = [
        CapabilitySummary(
            kind="tool",
            name=name,
            purpose=summarize_tool_description(name),
            availability="available",
            source="tool_registry",
        )
        for name in available_names
    ]

    declared = [name for name in normalize_declared_tool_names(declared_tool_names) if name in available_names]
    hint_summaries = [
        CapabilitySummary(
            kind="tool_hint",
            name=name,
            purpose=summarize_tool_description(name),
            availability="contextual",
            status="baseline_hint",
            hints=("These are baseline capability hints, not a hard whitelist.",),
            source="agent_or_scene",
        )
        for name in declared
    ]
    return tool_summaries, hint_summaries


def _collect_datasource_capabilities(datasource: Any | None) -> list[CapabilitySummary]:
    if datasource is None:
        return [
            CapabilitySummary(
                kind="datasource",
                name="datasource",
                purpose="Datasource context for the current conversation",
                availability="unavailable",
                status="not_selected",
                hints=("Ask user to select datasource before running database tools.",),
                source="conversation",
            )
        ]

    attrs = datasource.attributes if isinstance(getattr(datasource, "attributes", None), dict) else {}
    attr_keys = [str(key) for key in list(attrs.keys())[:4]]
    hints = [f"cluster_key={getattr(datasource, 'cluster_key', '') or 'unknown'}", f"tenant_role={getattr(datasource, 'tenant_role', '') or 'unknown'}"]
    if attr_keys:
        hints.append(f"metadata={', '.join(attr_keys)}")
    return [
        CapabilitySummary(
            kind="datasource",
            name=f"datasource#{getattr(datasource, 'id', 'unknown')}",
            purpose="Default datasource and runtime metadata for the current conversation",
            availability="active",
            status=getattr(datasource, "name", None),
            hints=tuple(hints[:3]),
            source="conversation",
        )
    ]


def _collect_service_capabilities(services: list[Any]) -> list[CapabilitySummary]:
    if not services:
        return []
    if len(services) == 1:
        service = services[0]
        return [
            CapabilitySummary(
                kind="service",
                name=str(getattr(service, "name", "PraxisService") or "PraxisService"),
                purpose="Platform-registered service callable in the current context",
                availability="available",
                status="auto_bindable",
                hints=(f"service_type={getattr(service, 'service_type', 'unknown')}",),
                source="service_binding",
            )
        ]
    return [
        CapabilitySummary(
            kind="service",
            name="PraxisService",
            purpose="Multiple platform-registered services callable in the current context",
            availability="available",
            status="multiple_candidates",
            hints=(f"count={len(services)}",),
            source="service_binding",
        )
    ]


def _collect_knowledge_capabilities(knowledge_bases: list[Any]) -> list[CapabilitySummary]:
    if not knowledge_bases:
        return []
    names = [str(getattr(kb, "name", "") or "").strip() for kb in knowledge_bases[:3] if str(getattr(kb, "name", "") or "").strip()]
    hints: list[str] = [f"count={len(knowledge_bases)}"]
    if names:
        hints.append(f"examples={', '.join(names)}")
    hints.append("Use platform knowledge objects to discover documents.")
    return [
        CapabilitySummary(
            kind="knowledge",
            name="knowledge_base",
            purpose="Searchable knowledge bases and documents on the platform",
            availability="available",
            status="indexed",
            hints=tuple(hints[:3]),
            source="knowledge_store",
        )
    ]


def _collect_skill_capabilities(active_skills: list[Any]) -> list[CapabilitySummary]:
    summaries: list[CapabilitySummary] = []
    for skill in active_skills:
        name = str(getattr(skill, "name", "") or "").strip()
        if not name:
            continue
        description = str(getattr(skill, "description", "") or "").strip() or "Currently active skill rule"
        summaries.append(
            CapabilitySummary(
                kind="skill",
                name=name,
                purpose=description,
                availability="active",
                source="skill_store",
            )
        )
    return summaries


def _collect_scene_capabilities(scene_key: str | None, scene_focus: dict[str, Any] | None) -> list[CapabilitySummary]:
    normalized_key = str(scene_key or "").strip()
    if not normalized_key:
        return []
    hints: list[str] = []
    if isinstance(scene_focus, dict):
        focus_type = str(scene_focus.get("type") or scene_focus.get("kind") or "").strip()
        if focus_type:
            hints.append(f"focus_type={focus_type}")
    return [
        CapabilitySummary(
            kind="scene",
            name=normalized_key,
            purpose="Current scene context and business focus object",
            availability="active",
            hints=tuple(hints[:2]),
            source="scene_agent",
        )
    ]


def _collect_scope_capabilities(scope_context: dict[str, Any] | None) -> list[CapabilitySummary]:
    if not scope_context:
        return []
    scope_type = str(scope_context.get("scope_type") or "").strip()
    if not scope_type:
        return []
    hints: list[str] = []
    object_type = str(scope_context.get("scope_object_type") or "").strip()
    object_id = str(scope_context.get("scope_object_id") or "").strip()
    if object_type and object_id:
        hints.append(f"target={object_type}:{object_id}")
    hints.append("Execution constraints are enforced at tool runtime.")
    return [
        CapabilitySummary(
            kind="scope",
            name=scope_type,
            purpose="Current runtime scope and execution context",
            availability="active",
            hints=tuple(hints[:2]),
            source="runtime_scope",
        )
    ]


def build_capability_context(payload: CapabilityBuildInput) -> CapabilityContext:
    tools, tool_hints = _collect_tool_capabilities(payload.tools, payload.declared_tool_names)
    sections = {
        "tools": tools,
        "tool_hints": tool_hints,
        "datasource": _collect_datasource_capabilities(payload.datasource),
        "services": _collect_service_capabilities(payload.services),
        "knowledge": _collect_knowledge_capabilities(payload.knowledge_bases),
        "skills": _collect_skill_capabilities(payload.active_skills),
        "scene": _collect_scene_capabilities(payload.scene_key, payload.scene_focus),
        "scope": _collect_scope_capabilities(payload.scope_context),
    }
    return CapabilityContext(sections=sections)
