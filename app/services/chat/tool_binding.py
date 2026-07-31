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
    if not datasource or not datasource.cluster_key:
        return tools
    resource_ref = f"cluster:{datasource.cluster_key}"
    services = (
        db.query(models.Service)
        .filter(
            models.Service.resource_ref == resource_ref,
            models.Service.status == "active",
        )
        .all()
    )
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
                "Determine domain parameters based on datasource_attributes, loaded skills, and knowledge base evidence."
            )
            attrs = (
                datasource.attributes
                if isinstance(getattr(datasource, "attributes", None), dict)
                else {}
            )
            ocp_cluster_id = attrs.get("ocp_cluster_id")
            ocp_tenant_id = attrs.get("ocp_tenant_id")
            if svc.service_type == "ocp_api":
                description += (
                    " OCP parameter mapping rules: OCP `{clusterId}` must use datasource_attributes.ocp_cluster_id; "
                    "OCP `{tenantId}` must use datasource_attributes.ocp_tenant_id. "
                    "In monitoring interfaces, when target=OBCLUSTER, targetId must use ocp_cluster_id; "
                    "when target=OBTENANT, targetId must use ocp_tenant_id; "
                    "do not use ob_cluster_id / ob_tenant_id as OCP targetId."
                )
                if ocp_cluster_id is not None:
                    description += f" Current ocp_cluster_id={ocp_cluster_id}."
                if ocp_tenant_id is not None:
                    description += f" Current ocp_tenant_id={ocp_tenant_id}."
            props["service_id"]["description"] = description
            req = fn.get("parameters", {}).get("required", [])
            if isinstance(req, list) and "service_id" in req:
                fn["parameters"]["required"] = [x for x in req if x != "service_id"]
        else:
            candidate_ids = ", ".join(str(item.id) for item in services[:5])
            props["service_id"]["description"] = (
                "Multiple PraxisServices are associated with the current datasource; auto-binding is not possible. "
                f"Please specify service_id explicitly. Candidate IDs: {candidate_ids}"
            )
    return patched


def filter_tools_for_handoff_turn(tools: list[dict]) -> list[dict]:
    del tools
    return []
