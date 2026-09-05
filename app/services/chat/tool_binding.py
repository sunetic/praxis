"""Tool filtering and binding helpers for chat stream."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import models
from app.services.chat.capabilities import (
    normalize_declared_tool_names as capability_normalize_declared_tool_names,
)
from app.services.integration.bindings import list_bound_services
from app.tools.registry import registry

settings = get_settings()


def filter_tools_by_agent(agent: models.Agent | None) -> list[dict]:
    del agent
    return registry.get_openai_functions()


def normalize_declared_tool_names(tool_names: list[str] | None) -> list[str]:
    return capability_normalize_declared_tool_names(tool_names)


def bind_default_datasource_to_tools(tools: list[dict], datasource_id: int | None) -> list[dict]:
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
                f"Datasource ID (defaults to the current session datasource {datasource_id}; can be omitted)"
            )
            if isinstance(required, list) and "datasource_id" in required:
                params["required"] = [x for x in required if x != "datasource_id"]
    return patched


def resolve_active_build_scope(db: Session, conversation_id: int) -> dict | None:
    if not settings.builder_runtime_enabled:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.status == "active",
            models.BuildSession.expires_at <= now,
        )
        .update({"status": "closed", "updated_at": now}, synchronize_session="fetch")
    )
    db.flush()
    session = (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.conversation_id == conversation_id,
            models.BuildSession.status == "active",
            models.BuildSession.expires_at > now,
        )
        .order_by(models.BuildSession.updated_at.desc())
        .first()
    )
    if not session:
        return None
    return {
        "scope_type": "builder",
        "scope_object_type": session.scope_object_type,
        "scope_object_id": session.scope_object_id,
        "build_session_id": session.id,
    }


def filter_tools_by_scope(tools: list[dict], scope_context: dict | None) -> list[dict]:
    del scope_context
    return tools


def inject_service_tools(tools: list[dict], datasource_id: int | None, db: Session) -> list[dict]:
    if datasource_id is None:
        return tools
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        return tools
    services = list_bound_services(db, datasource)
    if not services:
        return tools
    tool_names = {t.get("function", {}).get("name") for t in tools}
    if "call_praxis_service" not in tool_names:
        return tools

    patched = copy.deepcopy(list(tools))
    for tool in patched:
        fn = tool.get("function", {})
        if fn.get("name") != "call_praxis_service":
            continue
        props = fn.get("parameters", {}).get("properties", {})
        if "service_id" not in props:
            continue

        if len(services) == 1:
            svc = services[0]
            description = (
                f"Service ID (auto-bound to service_id={svc.id}; can be omitted). "
                f"The bound service is '{svc.name}' (service_type={svc.service_type}). "
                "Determine the API path and parameters from loaded skills and linked knowledge-base evidence."
            )
            kb_ids = [str(item.id) for item in svc.knowledge_bases]
            if kb_ids:
                description += f" Linked knowledge base IDs: {', '.join(kb_ids)}."
            props["service_id"]["description"] = description
            req = fn.get("parameters", {}).get("required", [])
            if isinstance(req, list) and "service_id" in req:
                fn["parameters"]["required"] = [x for x in req if x != "service_id"]
        else:
            candidates = ", ".join(
                f"{item.id}:{item.name}({item.service_type})" for item in services[:8]
            )
            props["service_id"]["description"] = (
                "Multiple external Services are associated with the current datasource. "
                f"Choose the documented provider and specify service_id. Candidates: {candidates}"
            )
    return patched


def filter_tools_for_handoff_turn(tools: list[dict]) -> list[dict]:
    del tools
    return []
