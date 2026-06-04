"""Chat stream helper functions extracted from app/api/chat.py."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import models
from app.services.chat.vds import event_to_vds as _event_to_vds
from app.services.llm import get_llm_client
from app.services.platform.skill_selector import select_skills_for_context
from app.skills.store import skill_store

logger = get_logger("chat.stream_helpers")

async def _save_messages_to_db(messages: list[models.Message]) -> None:
    """Save messages to database in thread pool to avoid blocking event loop."""
    from app.db.database import SessionLocal

    def _save():
        db = SessionLocal()
        try:
            for msg in messages:
                db.add(msg)
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_save)


async def _save_chat_events_to_db(events: list[models.ChatEvent]) -> None:
    """Save chat runtime events in thread pool."""
    from app.db.database import SessionLocal

    def _save():
        db = SessionLocal()
        try:
            for event in events:
                db.add(event)
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_save)


def _extract_error_message(event_data: dict | str | None) -> str:
    if isinstance(event_data, dict):
        message = str(event_data.get("message") or "").strip()
        return message or "runtime error"
    if isinstance(event_data, str):
        text = event_data.strip()
        return text or "runtime error"
    return "runtime error"


def _json_dumps_safe(payload: dict) -> str:
    """Serialize JSON payload safely for SSE/log storage."""
    return json.dumps(payload, default=str, ensure_ascii=False)


def _normalize_json_payload(payload: dict | None) -> dict | None:
    """Ensure payload can be stored in JSON columns safely."""
    if payload is None:
        return None
    # Round-trip through JSON encoder with default=str to normalize non-serializable values.
    return json.loads(_json_dumps_safe(payload))


def _safe_parse_arguments(arguments: str | dict | None) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {"_raw": arguments}
        except Exception:
            return {"_raw": arguments}
    return {}




def _build_knowledge_base_prompt() -> str:
    try:
        from app.db.database import SessionLocal
        from app.models import models as m
        db = SessionLocal()
        try:
            kbs = db.query(m.KnowledgeBase).order_by(m.KnowledgeBase.id).all()
            if not kbs:
                return ""
            lines = [
                "\n\nKnowledge Base:",
                "When you need to look up documentation, you can access the platform knowledge bases:",
                "1. Use object_crud(object_type=\"knowledge_base\", action=\"list\") to list all knowledge bases",
                "2. Use object_crud(object_type=\"knowledge_document\", action=\"list\", payload={\"kb_id\": N}) to get document list and file paths",
                "3. First use exec_command(command=\"ls\", args=[\"data/knowledge/N/\"]) to inspect directory structure; narrow down to relevant subdirectories before searching content",
                "4. Break the user question into several retrieval categories and then search: target object / operation / metrics or attributes / API domain terms / abbreviations and full names",
                "  - For each category, dynamically generate English technical keywords, synonyms, related operations, and object names; do not reuse fixed keyword templates or search only single terms",
                "  - Form 2-3 keyword combinations first, then preferably use exec_command with rg for multi-keyword search; use alternation or multiple -e flags with dynamically generated keywords",
                "  - Alternation example: exec_command(command=\"rg\", args=[\"-n\", \"-i\", \"-e\", \"kw1|kw2|kw3\", \"data/knowledge/1/target-dir/\"])",
                "  - Multi-pattern example: exec_command(command=\"rg\", args=[\"-n\", \"-i\", \"-e\", \"kw1\", \"-e\", \"kw2\", \"-e\", \"kw3\", \"data/knowledge/1/\"])",
                "  - After a match, use exec_command(command=\"sed\", args=[\"-n\", \"20,80p\", \"data/knowledge/1/some.md\"]) or cat to read the surrounding context",
                "  - grep can still serve as a fallback, but prefer rg for complex searches; use sed only for reading, not for modifying files",
                "5. After finding relevant documents, you must read the content to confirm API path, method, and query/body parameter format before proceeding",
                "",
                "Available knowledge bases:",
            ]
            for kb in kbs:
                doc_count = db.query(m.KnowledgeDocument).filter(m.KnowledgeDocument.kb_id == kb.id).count()
                tags = ", ".join(kb.tags) if kb.tags else ""
                tag_str = f" (tags: {tags})" if tags else ""
                lines.append(f"- [id={kb.id}] {kb.name}{tag_str} — {doc_count} document(s) — data/knowledge/{kb.id}/")
            return "\n".join(lines) + "\n"
        finally:
            db.close()
    except Exception:
        return ""



def _json_loads_safe(raw: str) -> dict | None:
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_selector_payload(raw_text: str) -> dict | None:
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


def _build_recent_conversation_context(messages: list[models.Message], limit: int = 8) -> list[dict]:
    context: list[dict] = []
    for msg in messages[-limit:]:
        if msg.role not in {"user", "assistant"}:
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        context.append({"role": msg.role, "content": content[:280]})
    return context


def _normalize_string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
        if len(normalized) >= limit:
            break
    return normalized


def _infer_scene_key_from_legacy_page_agent(raw_page_agent: dict[str, Any]) -> str:
    profile = str(raw_page_agent.get("profile") or "").strip()
    page = str(raw_page_agent.get("page") or "").strip()
    if profile == "stats_analysis_agent":
        return "stats_analysis"
    if page == "stats-analysis":
        return "stats_analysis"
    return ""


def _extract_scene_agent_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    raw_scene_agent = message.get("scene_agent")
    if isinstance(raw_scene_agent, dict):
        scene_payload = {
            "key": str(raw_scene_agent.get("key") or "").strip(),
            "context": raw_scene_agent.get("context")
            if isinstance(raw_scene_agent.get("context"), dict)
            else {},
            "focus_object": raw_scene_agent.get("focus_object")
            if isinstance(raw_scene_agent.get("focus_object"), dict)
            else None,
            "tools": _normalize_string_list(raw_scene_agent.get("tools"), limit=64),
            "skills": _normalize_string_list(raw_scene_agent.get("skills"), limit=64),
            "source": "scene_agent",
        }
        return scene_payload if scene_payload["key"] else None

    raw_page_agent = message.get("page_agent")
    if isinstance(raw_page_agent, dict):
        inferred_key = _infer_scene_key_from_legacy_page_agent(raw_page_agent)
        if not inferred_key:
            return None
        return {
            "key": inferred_key,
            "context": raw_page_agent.get("context")
            if isinstance(raw_page_agent.get("context"), dict)
            else {},
            "focus_object": raw_page_agent.get("focus_object")
            if isinstance(raw_page_agent.get("focus_object"), dict)
            else None,
            "tools": _normalize_string_list(raw_page_agent.get("tools"), limit=64),
            "skills": _normalize_string_list(raw_page_agent.get("skills"), limit=64),
            "source": "page_agent_compat",
        }

    return None


def _extract_latest_active_skills(events: list[models.ChatEvent], fallback: list[str]) -> list[str]:
    for event in reversed(events):
        if event.event_type != "skill_delta":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        active = _normalize_string_list(payload.get("active_skills"), limit=16)
        if active:
            return active
    return _normalize_string_list(fallback, limit=16)



_META_TOOL_NAMES = {"agent_save"}  # lifecycle tools, not domain tools


def _extract_tool_call_trace(messages: list[models.Message], *, limit: int = 16) -> list[dict]:
    trace: list[dict] = []
    for msg in messages:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "").strip()
            if not name or name in _META_TOOL_NAMES:
                continue
            trace.append({
                "name": name,
                "input": tc.get("input") or {},
                "result": tc.get("result"),
            })
    return trace[-limit:]



def _normalize_positive_int_list(value: Any, *, limit: int = 64) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        if not isinstance(item, int):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _infer_datasource_id_from_scene_agent_payload(scene_agent_payload: dict[str, Any] | None) -> int | None:
    if not isinstance(scene_agent_payload, dict):
        return None
    context = scene_agent_payload.get("context")
    if not isinstance(context, dict):
        return None
    datasource = context.get("datasource")
    if not isinstance(datasource, dict):
        return None
    inferred = datasource.get("id")
    return inferred if isinstance(inferred, int) and inferred > 0 else None


def _normalize_scene_agent_payload_datasource(
    scene_agent_payload: dict[str, Any] | None,
    datasource: models.DataSource | None,
) -> dict[str, Any] | None:
    if not isinstance(scene_agent_payload, dict):
        return scene_agent_payload
    if datasource is None:
        return scene_agent_payload
    normalized = copy.deepcopy(scene_agent_payload)
    context = normalized.get("context") if isinstance(normalized.get("context"), dict) else {}
    datasource_context = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
    datasource_context.update(
        {
            "id": datasource.id,
            "name": datasource.name,
            "cluster_key": datasource.cluster_key,
            "tenant_role": datasource.tenant_role,
            "host": datasource.host,
            "port": datasource.port,
            "db_type": datasource.db_type,
            "database": datasource.database,
        }
    )
    context["datasource"] = datasource_context
    normalized["context"] = context
    return normalized


def _build_step_start_event(
    *,
    step_id: str,
    name: str,
    kind: str,
    arguments: Any,
    message: str,
    trace_id: str,
    route_source: str,
) -> dict[str, Any]:
    arguments_text = (
        arguments
        if isinstance(arguments, str)
        else json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
    )
    return {
        "type": "step_start",
        "phase": "tool_running",
        "data": {
            "step_id": step_id,
            "kind": kind,
            "name": name,
            "arguments": arguments_text,
            "message": message,
            "trace_id": trace_id,
            "route_source": route_source,
        },
    }


def _build_step_result_event(
    *,
    step_id: str,
    name: str,
    kind: str,
    arguments: Any,
    result: dict[str, Any],
    message: str,
    trace_id: str,
    route_source: str,
    context_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arguments_text = (
        arguments
        if isinstance(arguments, str)
        else json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
    )
    payload = {
        "step_id": step_id,
        "kind": kind,
        "name": name,
        "arguments": arguments_text,
        "result": result,
        "message": message,
        "trace_id": trace_id,
        "route_source": route_source,
    }
    if isinstance(context_delta, dict) and context_delta:
        payload["context_delta"] = context_delta
    return {
        "type": "step_result",
        "phase": "tool_running",
        "data": payload,
    }


def _map_tool_event_to_step_event(
    event: dict[str, Any],
    *,
    trace_id: str,
    route_source: str,
) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    phase = str(event.get("phase") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event_type == "tool_start":
        step_id = str(data.get("tool_call_id") or uuid.uuid4())
        return {
            "type": "step_start",
            "phase": phase or "tool_running",
            "data": {
                "step_id": step_id,
                "kind": "tool",
                "name": str(data.get("name") or ""),
                "arguments": str(data.get("arguments") or ""),
                "message": f"Executing {str(data.get('name') or 'tool')}...",
                "trace_id": trace_id,
                "route_source": route_source,
            },
            "meta": event.get("meta") or {},
        }
    if event_type == "tool_result":
        step_id = str(data.get("tool_call_id") or uuid.uuid4())
        tool_name = str(data.get("name") or "")
        result_obj = data.get("result") if isinstance(data.get("result"), dict) else {"success": True, "data": data.get("result")}
        payload: dict[str, Any] = {
            "step_id": step_id,
            "kind": "tool",
            "name": tool_name,
            "arguments": str(data.get("arguments") or ""),
            "result": result_obj,
            "message": "Tool execution completed.",
            "trace_id": trace_id,
            "route_source": route_source,
        }
        if tool_name == "datasource_switch" and isinstance(result_obj, dict) and result_obj.get("success"):
            result_data = result_obj.get("data") if isinstance(result_obj.get("data"), dict) else {}
            ds_id = result_data.get("datasource_id")
            if isinstance(ds_id, int) and ds_id > 0:
                payload["context_delta"] = {"datasource_id": ds_id}
        return {
            "type": "step_result",
            "phase": phase or "tool_running",
            "data": payload,
            "meta": event.get("meta") or {},
        }
    return event


def _annotate_runtime_event(event: dict[str, Any], *, agent_name: str = "") -> dict[str, Any]:
    enriched = dict(event)
    event_type = str(enriched.get("type") or "").strip().lower()
    data = enriched.get("data") if isinstance(enriched.get("data"), dict) else {}
    data = dict(data)

    def with_origin(*, source: str, agent: str) -> None:
        data.setdefault("source", source)
        data.setdefault("agent", agent)
        enriched["data"] = data

    if event_type in {"thinking", "plan"}:
        with_origin(source="llm", agent=agent_name or "ChatPlanner")
        enriched["event_group"] = "core"
        enriched["event_name"] = "plan"
        return enriched
    if event_type == "reflect":
        action = str(data.get("action") or "").strip().lower()
        with_origin(source="llm", agent=agent_name or "ChatReflector")
        enriched["event_group"] = "core"
        enriched["event_name"] = "retry" if action == "retry" else "reflect"
        return enriched
    if event_type == "done":
        with_origin(source="runtime", agent=agent_name or "ChatOrchestrator")
        enriched["event_group"] = "core"
        enriched["event_name"] = "done"
        return enriched
    if event_type == "error":
        with_origin(source="runtime", agent=agent_name or "ChatOrchestrator")
        enriched["event_group"] = "core"
        enriched["event_name"] = "error"
        return enriched
    if event_type == "assistant":
        with_origin(source="llm", agent=agent_name or "ChatAgent")
        enriched["event_group"] = "core"
        enriched["event_name"] = "assistant"
        return enriched
    if event_type in {"tool_start", "step_start"}:
        with_origin(source="tool", agent="ToolExecutor")
        enriched["event_group"] = "extension"
        enriched["event_name"] = "tool_start"
        return enriched
    if event_type in {"tool_result", "step_result"}:
        with_origin(source="tool", agent="ToolExecutor")
        enriched["event_group"] = "extension"
        enriched["event_name"] = "tool_result"
        return enriched

    with_origin(source="runtime", agent=agent_name or "ChatOrchestrator")
    enriched["event_group"] = "extension"
    enriched["event_name"] = event_type or "extension"
    return enriched



async def _select_dynamic_skills(
    conversation: models.Conversation,
    messages: list[models.Message],
    latest_user_input: str,
) -> dict:
    configured_names = (
        conversation.agent.skills
        if conversation.agent and isinstance(conversation.agent.skills, list)
        else []
    )
    current_active = (
        conversation.active_skills
        if isinstance(conversation.active_skills, list)
        else configured_names
    )
    return await select_skills_for_context(
        prompt=latest_user_input,
        recent_context=_build_recent_conversation_context(messages),
        configured_skill_names=configured_names,
        current_active_skill_names=list(current_active) if isinstance(current_active, list) else [],
        context_label=f"conversation:{conversation.id}",
        llm_client_factory=get_llm_client,
        skill_store_instance=skill_store,
    )


ALL_TENANTS_REQUEST_PATTERNS = (
    re.compile(r"\b(all|across)\s+tenants?\b", flags=re.IGNORECASE),
)


def _is_all_tenants_request(user_input: str) -> bool:
    text = str(user_input or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in ALL_TENANTS_REQUEST_PATTERNS)


def _build_cross_tenant_scope_guard_message(datasource: models.DataSource) -> str:
    datasource_name = str(datasource.name or f"Datasource#{datasource.id}").strip()
    return (
        f"The current session is bound to {datasource_name} (#{datasource.id}), which has user scope and can only see the current tenant. "
        "Your request involves all tenants, which requires sys scope. "
        "Please switch to a sys-scope datasource and retry, or explicitly confirm that only the current tenant should be checked."
    )


async def _stream_scope_guard_reply(
    *,
    conversation_id: int,
    trace_id: str,
    guard_payload: dict[str, Any],
):
    await _save_chat_events_to_db(
        [
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="scope_guard",
                phase="planning",
                payload=_normalize_json_payload(guard_payload),
            )
        ]
    )
    assistant_text = str(guard_payload.get("message") or "").strip()
    if assistant_text:
        await _save_messages_to_db(
            [
                models.Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_text,
                )
            ]
        )
        yield _event_to_vds({'type': 'assistant', 'phase': 'responding', 'data': {'text': assistant_text}})
    yield _event_to_vds({'type': 'done', 'data': {'trace_id': trace_id}})


def _build_builder_scene_conversation_context(
    messages: list[models.Message],
    *,
    latest_user_input: str,
    explicit_context: str = "",
    limit: int = 10,
) -> str:
    if explicit_context.strip():
        return explicit_context.strip()
    rows: list[str] = []
    normalized_input = latest_user_input.strip()
    for msg in messages:
        if msg.role not in {"user", "assistant"}:
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        rows.append(f"{msg.role}: {content}")
    if normalized_input and rows and rows[-1] == f"user: {normalized_input}":
        rows = rows[:-1]
    return "\n".join(rows[-max(1, limit) :])


def _resolve_builder_scene_target(
    *,
    scope_context: dict[str, Any] | None,
    resolved_scene_agent: Any,
    scene_agent_payload: dict[str, Any] | None,
) -> str | None:
    if not scope_context or scope_context.get("scope_type") != "builder":
        return None
    scene_key = str(getattr(resolved_scene_agent, "key", "") or "").strip()
    if not scene_key:
        return None
    expected_scope_type = {
        "function_build": "function",
        "page_build": "page",
    }.get(scene_key)
    if not expected_scope_type:
        return None
    actual_scope_type = str(scope_context.get("scope_object_type") or "").strip().lower()
    if actual_scope_type != expected_scope_type:
        raise HTTPException(
            status_code=400,
            detail=f"scene_agent.key '{scene_key}' must match builder scope '{actual_scope_type}'",
        )

    payload = scene_agent_payload or {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    focus_object = payload.get("focus_object") if isinstance(payload.get("focus_object"), dict) else {}
    scope_object_id = str(scope_context.get("scope_object_id") or "").strip()
    key_candidates = (
        ["function_id"] if expected_scope_type == "function" else ["page_id"]
    )
    for key in key_candidates:
        for source in (context, focus_object):
            candidate = source.get(key)
            if candidate is None:
                continue
            if str(candidate).strip() != scope_object_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"scene_agent.{key} must match builder scope object id",
                )
    return expected_scope_type


def _extract_sse_payloads(raw_chunk: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in str(raw_chunk or "").splitlines():
        normalized = line.strip()
        if not normalized.startswith("data:"):
            continue
        body = normalized.replace("data:", "", 1).strip()
        if not body:
            continue
        parsed = _json_loads_safe(body)
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


async def _stream_builder_scene_proxy(
    *,
    conversation_id: int,
    db: Session,
    scope_context: dict[str, Any],
    builder_target: str,
    incoming_content: str,
    conversation_context: str,
    scene_agent_payload: dict[str, Any] | None,
) -> StreamingResponse:
    target_id = int(str(scope_context.get("scope_object_id") or "0").strip() or "0")
    if target_id <= 0:
        raise HTTPException(status_code=400, detail="builder scope object id is invalid")

    if builder_target == "function":
        from app.api import functions as functions_api

        proxy_response = await functions_api.run_function_chat_action_stream(
            function_id=target_id,
            payload={
                "action": "build",
                "prompt": incoming_content,
                "conversation_context": conversation_context,
                "scene_agent": scene_agent_payload or None,
            },
            db=db,
        )
    elif builder_target == "page":
        from app.api import pages as pages_api

        proxy_response = await pages_api.create_page_build_run_stream(
            page_id=target_id,
            payload={
                "prompt": incoming_content,
                "conversation_context": conversation_context,
            },
            db=db,
        )
    else:  # pragma: no cover - guarded by caller
        raise HTTPException(status_code=400, detail="unsupported builder target")

    async def generate():
        assistant_content = ""
        async for raw_chunk in proxy_response.body_iterator:
            text_chunk = raw_chunk.decode("utf-8") if isinstance(raw_chunk, bytes) else str(raw_chunk)
            for event in _extract_sse_payloads(text_chunk):
                event_type = str(event.get("type") or "").strip().lower()
                event_phase = str(event.get("phase") or "").strip() or None
                event_payload = event.get("data") if isinstance(event.get("data"), dict) else {}
                if event_type == "assistant":
                    text = str(event_payload.get("text") or "").strip()
                    if text:
                        assistant_content += text
                elif event_type == "done":
                    final_text = (
                        assistant_content
                        or str(event_payload.get("assistant_message") or "").strip()
                        or str(event_payload.get("build_summary") or "").strip()
                    )
                    if final_text:
                        await _save_messages_to_db(
                            [
                                models.Message(
                                    conversation_id=conversation_id,
                                    role="assistant",
                                    content=final_text,
                                )
                            ]
                        )
                if event_type in {"phase", "extension", "error", "done"}:
                    await _save_chat_events_to_db(
                        [
                            models.ChatEvent(
                                conversation_id=conversation_id,
                                event_type=event_type,
                                phase=event_phase,
                                payload=_normalize_json_payload(event_payload),
                            )
                        ]
                    )
            yield text_chunk

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

