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


def _bound_service_contract(service: models.Service, datasource: models.DataSource) -> str:
    """Return a concise provider contract for the currently bound Service."""
    service_type = str(service.service_type or "").strip().casefold()
    if service_type != "prometheus":
        return ""

    lines = [
        "Bound Prometheus read contract:",
        "- Readiness: GET /-/ready.",
        "- Current PromQL: GET /api/v1/query with query_params.query.",
        "- Historical PromQL: GET /api/v1/query_range with query, start, end, and step.",
        "- Current alerts: GET /api/v1/alerts; active scrape targets: GET /api/v1/targets with state=active.",
        "- Do not guess identifiers or enumerate the full /api/v1/label/__name__/values catalog. Use one focused knowledge_search against the linked knowledge base when this contract does not name the required metric.",
        "- Stop discovery once the endpoint, metric, labels, and time window needed for the user's claim are known.",
    ]
    if str(datasource.db_type or "").strip().casefold() == "mysql":
        lines.extend(
            [
                "MySQL exporter metric mapping:",
                "- connections: mysql_global_status_threads_connected",
                "- active threads: mysql_global_status_threads_running",
                "- connection limit: mysql_global_variables_max_connections",
                "- failed connection counter: mysql_global_status_aborted_connects",
            ]
        )
    return "\n".join(lines)


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
        tool_name = fn.get("name")
        if tool_name == "knowledge_search" and len(services) == 1:
            kb_ids = [str(item.id) for item in services[0].knowledge_bases]
            kb_ids_prop = fn.get("parameters", {}).get("properties", {}).get("kb_ids")
            if kb_ids and isinstance(kb_ids_prop, dict):
                kb_ids_prop["description"] = (
                    f"For the bound Service, search only linked knowledge base IDs: "
                    f"{', '.join(kb_ids)}. Use one focused lookup when its provider contract "
                    "does not already supply the required endpoint or identifier."
                )
            continue
        if tool_name != "call_praxis_service":
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
            provider_contract = _bound_service_contract(svc, datasource)
            if provider_contract:
                base_description = str(fn.get("description") or "").strip()
                fn["description"] = "\n\n".join(
                    item for item in (base_description, provider_contract) if item
                )
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
