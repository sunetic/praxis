import json
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.chat_agent_draft import _stream_save_agent_workflow
from app.api.chat_handoff import (
    HANDOFF_STATUS_PENDING,
    _get_handoff_event,
    _handoff_status,
    _mark_handoff_consumed,
)
from app.api.chat_history import ensure_stream_user_message, load_chat_messages
from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas.schemas import ChatCompleteRequest, ChatStreamRequest
from app.services.chat import get_chat_service
from app.services.chat.agent import ChatAgent
from app.services.chat.capabilities import (
    list_active_skill_models,
)
from app.services.chat.scene_agents import SceneAgentPayload, SceneAgentRegistry
from app.services.chat.stream_helpers import (
    _annotate_runtime_event,
    _build_builder_scene_conversation_context,
    _build_cross_tenant_scope_guard_message,
    _extract_error_message,
    _extract_scene_agent_payload,
    _infer_datasource_id_from_scene_agent_payload,
    _is_all_tenants_request,
    _json_dumps_safe,
    _map_tool_event_to_step_event,
    _normalize_json_payload,
    _normalize_scene_agent_payload_datasource,
    _normalize_string_list,
    _resolve_builder_scene_target,
    _safe_parse_arguments,
    _save_chat_events_to_db,
    _save_messages_to_db,
    _select_dynamic_skills,
    _stream_builder_scene_proxy,
    _stream_scope_guard_reply,
)
from app.services.chat.tool_binding import (  # noqa: I001
    bind_default_datasource_to_tools as _bind_default_datasource_to_tools,
)
from app.services.chat.tool_binding import (
    filter_tools_by_agent as _filter_tools_by_agent,
)
from app.services.chat.tool_binding import (
    filter_tools_by_scope as _filter_tools_by_scope,
)
from app.services.chat.tool_binding import (
    filter_tools_for_handoff_turn as _filter_tools_for_handoff_turn,
)
from app.services.chat.tool_binding import (
    inject_service_tools as _inject_service_tools,
)
from app.services.chat.tool_binding import (
    normalize_declared_tool_names as _normalize_declared_tool_names,
)
from app.services.chat.tool_binding import (
    resolve_active_build_scope as _resolve_active_build_scope,
)
from app.services.chat.turn_context import TurnContextExtras, build_agent_turn_context
from app.services.chat.vds import event_to_vds as _event_to_vds
from app.skills.store import skill_store

_XML_TOOL_CALL_STRIP_RE = re.compile(
    r"<function=\w+>\s*.*?\s*</function>\s*(?:</tool_call>)?",
    re.DOTALL,
)
_TRAILING_TOOL_CALL_STRIP_RE = re.compile(r"\s*</tool_call>\s*$")
_RESUME_REQUEST_RE = re.compile(
    r"^(?:请)?(?:继续执行|继续|接着|续跑|恢复|从上次继续|continue|resume)(?:\b|[\s，,。.！!:：]|$)",
    re.I,
)


def _strip_xml_tool_calls(text: str) -> str:
    """Remove XML-style tool call blocks that some models emit as text."""
    text = _XML_TOOL_CALL_STRIP_RE.sub("", text)
    text = _TRAILING_TOOL_CALL_STRIP_RE.sub("", text)
    return text.rstrip()


def _load_resumable_task_state(
    db: Session,
    *,
    conversation_id: int,
    user_input: str,
) -> dict[str, Any] | None:
    if not _RESUME_REQUEST_RE.search(str(user_input or "").strip()):
        return None
    events = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type.in_(["task_state", "checkpoint"]),
        )
        .order_by(models.ChatEvent.id.desc())
        .limit(20)
        .all()
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        state = payload.get("task_state") if event.event_type == "checkpoint" else payload
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or payload.get("status") or "")
        if status in {"completed", "error"}:
            return None
        if not state.get("task_run_id") or not isinstance(state.get("contract"), dict):
            continue
        return state
    return None


router = APIRouter(prefix="/chat", tags=["Chat"])
logger = get_logger("chat.api")
settings = get_settings()


@router.post("/{conversation_id}/stream")
async def chat_stream(
    conversation_id: int,
    message: ChatStreamRequest,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Stream chat with tool calling support."""
    logger.info("stream_endpoint_called %s", fmt_kv(conversation_id=conversation_id))
    trace_id = str(uuid.uuid4())

    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    db.refresh(conversation)
    incoming_content = message.content.strip()
    run_datasource_ids: list[int] = [x for x in (message.run_datasource_ids or []) if x > 0]
    scene_agent_payload = _extract_scene_agent_payload(message.model_dump())
    locale = message.locale
    inferred_scene_datasource_id = _infer_datasource_id_from_scene_agent_payload(
        scene_agent_payload
    )
    if (
        conversation.datasource_id is None
        and isinstance(inferred_scene_datasource_id, int)
        and inferred_scene_datasource_id > 0
    ):
        inferred_datasource = (
            db.query(models.DataSource)
            .filter(models.DataSource.id == inferred_scene_datasource_id)
            .first()
        )
        if inferred_datasource is not None:
            conversation.datasource_id = inferred_datasource.id
            db.add(conversation)
            db.flush()
    if scene_agent_payload and scene_agent_payload.get("source") == "page_agent_compat":
        logger.info(
            "chat_scene_agent_compat_used %s",
            fmt_kv(
                conversation_id=conversation_id,
                trace_id=trace_id,
                key=scene_agent_payload.get("key"),
            ),
        )

    handoff_id: int | None = (
        message.handoff_id if (message.handoff_id and message.handoff_id > 0) else None
    )

    pending_handoff_turn = False
    if handoff_id is not None:
        precheck_handoff_event = _get_handoff_event(
            db,
            conversation_id=conversation_id,
            handoff_id=handoff_id,
        )
        if not precheck_handoff_event:
            raise HTTPException(status_code=404, detail="Handoff not found")
        precheck_payload = (
            precheck_handoff_event.payload
            if isinstance(precheck_handoff_event.payload, dict)
            else {}
        )
        pending_handoff_turn = _handoff_status(precheck_payload) == HANDOFF_STATUS_PENDING

    ensure_stream_user_message(db, conversation_id, incoming_content)

    selected_datasource = None
    if conversation.datasource_id is not None:
        selected_datasource = (
            db.query(models.DataSource)
            .filter(models.DataSource.id == conversation.datasource_id)
            .first()
        )
    scene_agent_payload = _normalize_scene_agent_payload_datasource(
        scene_agent_payload, selected_datasource
    )
    if (
        not pending_handoff_turn
        and selected_datasource
        and str(selected_datasource.tenant_role or "").lower() != "sys"
        and _is_all_tenants_request(incoming_content)
    ):
        guard_payload = {
            "trace_id": trace_id,
            "datasource_id": selected_datasource.id,
            "datasource_name": selected_datasource.name,
            "tenant_role": selected_datasource.tenant_role,
            "reason": "cross_tenant_requires_sys_scope",
            "message": _build_cross_tenant_scope_guard_message(selected_datasource),
        }
        logger.info(
            "chat_scope_guard_blocked %s",
            fmt_kv(
                conversation_id=conversation_id,
                trace_id=trace_id,
                datasource_id=selected_datasource.id,
                tenant_role=selected_datasource.tenant_role,
                reason=guard_payload["reason"],
            ),
        )
        return StreamingResponse(
            _stream_scope_guard_reply(
                conversation_id=conversation_id,
                trace_id=trace_id,
                guard_payload=guard_payload,
            ),
            media_type="text/plain; charset=utf-8",
        )

    chat_messages, messages = load_chat_messages(db, conversation_id, incoming_content)
    resumable_task_state = _load_resumable_task_state(
        db,
        conversation_id=conversation_id,
        user_input=incoming_content,
    )

    tools = _filter_tools_by_agent(conversation.agent)
    tools = _bind_default_datasource_to_tools(tools, conversation.datasource_id)
    scope_context = _resolve_active_build_scope(db, conversation_id)
    if scope_context:
        scope_context = {**scope_context, "trace_id": trace_id}
    tools = _filter_tools_by_scope(tools, scope_context)
    tools = _inject_service_tools(tools, conversation.datasource_id, db)
    declared_tool_names = _normalize_declared_tool_names(
        conversation.agent.tools
        if conversation.agent and isinstance(conversation.agent.tools, list)
        else None
    )
    scene_agent_registry = SceneAgentRegistry()
    resolved_scene_agent = None
    normalized_scene_payload: SceneAgentPayload | None = None
    scene_agent_fallback_payload: dict[str, Any] | None = None
    if scene_agent_payload:
        resolved_scene_agent = scene_agent_registry.resolve(
            str(scene_agent_payload.get("key") or "")
        )
        if resolved_scene_agent is not None:
            normalized_scene_payload = SceneAgentPayload(
                key=str(scene_agent_payload.get("key") or "").strip(),
                context=scene_agent_payload.get("context")
                if isinstance(scene_agent_payload.get("context"), dict)
                else {},
                focus_object=scene_agent_payload.get("focus_object")
                if isinstance(scene_agent_payload.get("focus_object"), dict)
                else None,
                requested_tools=_normalize_string_list(scene_agent_payload.get("tools"), limit=64),
                requested_skills=_normalize_string_list(
                    scene_agent_payload.get("skills"), limit=64
                ),
                source=str(scene_agent_payload.get("source") or "scene_agent").strip()
                or "scene_agent",
            )
            declared_tool_names = list(
                dict.fromkeys(
                    declared_tool_names
                    + resolved_scene_agent.resolve_tools(normalized_scene_payload)
                )
            )
        elif scene_agent_payload.get("tools"):
            declared_tool_names = list(
                dict.fromkeys(
                    declared_tool_names
                    + _normalize_declared_tool_names(
                        _normalize_string_list(scene_agent_payload.get("tools"), limit=64)
                    )
                )
            )
        else:
            scene_agent_fallback_payload = {
                "key": scene_agent_payload.get("key"),
                "source": scene_agent_payload.get("source") or "scene_agent",
                "reason": "scene_agent_not_registered",
                "recoverable_hint": "Please upgrade the frontend scene_key, or pass tools/skills explicitly in the request.",
            }
    pending_actions = (
        db.query(models.PendingAction)
        .filter(
            models.PendingAction.conversation_id == conversation_id,
            models.PendingAction.status == "pending",
        )
        .order_by(models.PendingAction.created_at.asc())
        .all()
    )
    handoff_event: models.ChatEvent | None = None
    handoff_payload: dict[str, Any] | None = None
    if handoff_id is not None:
        handoff_event = _get_handoff_event(
            db,
            conversation_id=conversation_id,
            handoff_id=handoff_id,
        )
        if not handoff_event:
            raise HTTPException(status_code=404, detail="Handoff not found")
        event_payload = handoff_event.payload if isinstance(handoff_event.payload, dict) else {}
        if _handoff_status(event_payload) == HANDOFF_STATUS_PENDING:
            handoff_payload = event_payload
        logger.info(
            "chat_handoff_resolved %s",
            fmt_kv(
                conversation_id=conversation_id,
                handoff_id=handoff_id,
                handoff_status=_handoff_status(event_payload),
            ),
        )
    builder_scene_target = _resolve_builder_scene_target(
        scope_context=scope_context,
        resolved_scene_agent=resolved_scene_agent,
        scene_agent_payload=scene_agent_payload,
    )
    if builder_scene_target:
        if scope_context:
            db.add(
                models.ChatEvent(
                    conversation_id=conversation_id,
                    event_type="builder_scope",
                    phase="planning",
                    payload=_normalize_json_payload(scope_context),
                )
            )
        if scene_agent_payload:
            db.add(
                models.ChatEvent(
                    conversation_id=conversation_id,
                    event_type="scene_agent_context",
                    phase="planning",
                    payload=_normalize_json_payload(
                        {
                            "key": scene_agent_payload.get("key"),
                            "context": scene_agent_payload.get("context") or {},
                            "focus_object": scene_agent_payload.get("focus_object") or {},
                            "source": scene_agent_payload.get("source") or "scene_agent",
                        }
                    ),
                )
            )
        db.commit()
        return await _stream_builder_scene_proxy(
            conversation_id=conversation_id,
            db=db,
            scope_context=scope_context,
            builder_target=builder_scene_target,
            incoming_content=incoming_content,
            conversation_context=_build_builder_scene_conversation_context(
                messages,
                latest_user_input=incoming_content,
                explicit_context=message.conversation_context or "",
            ),
            scene_agent_payload=scene_agent_payload,
        )
    latest_user_input = incoming_content
    skill_selection = await _select_dynamic_skills(
        conversation=conversation,
        messages=messages,
        latest_user_input=latest_user_input,
    )
    if resolved_scene_agent and normalized_scene_payload:
        existing = list(skill_selection.get("active_skills") or [])
        merged = list(
            dict.fromkeys(
                existing + list(resolved_scene_agent.resolve_skills(normalized_scene_payload))
            )
        )
        skill_selection["active_skills"] = merged
    elif scene_agent_payload and scene_agent_payload.get("skills"):
        existing = list(skill_selection.get("active_skills") or [])
        merged = list(dict.fromkeys(existing + list(scene_agent_payload["skills"])))
        skill_selection["active_skills"] = merged
    conversation.active_skills = skill_selection["active_skills"]
    db.commit()
    db.refresh(conversation)

    loaded_skills = skill_store.list_skills()
    selected_skills = list_active_skill_models(skill_selection["active_skills"], loaded_skills)

    scene_fallback_payload = None
    if scene_agent_payload and not resolved_scene_agent:
        scene_fallback_payload = {
            "key": scene_agent_payload.get("key"),
            "context": scene_agent_payload.get("context") or {},
            "focus_object": scene_agent_payload.get("focus_object") or {},
        }

    system_prompt, tools, declared_tool_names = build_agent_turn_context(
        conversation,
        db,
        scope_context=scope_context,
        declared_tool_names=declared_tool_names,
        selected_skills=selected_skills,
        extra=TurnContextExtras(
            pending_actions=pending_actions,
            handoff_payload=handoff_payload,
            resolved_scene_agent=resolved_scene_agent,
            scene_payload=normalized_scene_payload,
            scene_fallback_payload=scene_fallback_payload,
            selected_skills=selected_skills,
            locale=locale,
        ),
    )

    if handoff_payload:
        tools = _filter_tools_for_handoff_turn(tools)

    chat_agent = ChatAgent(chat_service=get_chat_service())
    logger.info(
        "chat_agent_start %s",
        fmt_kv(
            conversation_id=conversation_id,
            datasource_id=conversation.datasource_id,
            tool_count=len(tools),
            skill_count=len(selected_skills),
            selector_ok=skill_selection["selector_ok"],
            added=len(skill_selection["added"]),
            removed=len(skill_selection["removed"]),
        ),
    )

    assistant_content = ""
    pending_parts: list[dict] = []
    turn_message: models.Message | None = None
    display_agent_name = (
        resolved_scene_agent.display_name
        if resolved_scene_agent
        else (conversation.agent.name if conversation.agent else "ChatAgent")
    )
    skill_delta_payload = {
        "trace_id": trace_id,
        "active_skills": skill_selection["active_skills"],
        "added": skill_selection["added"],
        "removed": skill_selection["removed"],
        "reason": skill_selection["reason"],
        "selector_ok": skill_selection["selector_ok"],
    }
    db.add(
        models.ChatEvent(
            conversation_id=conversation_id,
            event_type="skill_delta",
            phase="planning",
            payload=_normalize_json_payload(skill_delta_payload),
        )
    )
    if scope_context:
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="builder_scope",
                phase="planning",
                payload=_normalize_json_payload(scope_context),
            )
        )
    if scene_agent_payload:
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="scene_agent_context",
                phase="planning",
                payload=_normalize_json_payload(
                    {
                        "key": scene_agent_payload.get("key"),
                        "context": scene_agent_payload.get("context") or {},
                        "focus_object": scene_agent_payload.get("focus_object") or {},
                        "source": scene_agent_payload.get("source") or "scene_agent",
                    }
                ),
            )
        )
    if scene_agent_fallback_payload:
        logger.info(
            "chat_scene_agent_fallback %s",
            fmt_kv(
                conversation_id=conversation_id,
                trace_id=trace_id,
                key=scene_agent_fallback_payload.get("key"),
                reason=scene_agent_fallback_payload.get("reason"),
            ),
        )
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="scene_agent_fallback",
                phase="planning",
                payload=_normalize_json_payload(scene_agent_fallback_payload),
            )
        )
    if handoff_event and handoff_payload:
        _mark_handoff_consumed(
            db,
            handoff_event,
            consumed_with_message=incoming_content,
        )
    db.commit()

    latest_user_turn_event = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type == "user_message",
            models.ChatEvent.turn_seq.is_not(None),
        )
        .order_by(models.ChatEvent.turn_seq.desc(), models.ChatEvent.id.desc())
        .first()
    )
    latest_turn_event = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.turn_seq.is_not(None),
        )
        .order_by(
            models.ChatEvent.turn_seq.desc(),
            models.ChatEvent.part_seq.desc(),
            models.ChatEvent.id.desc(),
        )
        .first()
    )
    current_turn_seq = (
        int(latest_user_turn_event.turn_seq)
        if latest_user_turn_event and latest_user_turn_event.turn_seq is not None
        else (
            int(latest_turn_event.turn_seq) + 1
            if latest_turn_event and latest_turn_event.turn_seq is not None
            else 1
        )
    )
    current_turn_id = str(
        (
            latest_user_turn_event.turn_id
            if latest_user_turn_event and latest_user_turn_event.turn_id
            else None
        )
        or f"turn-{conversation_id}-{current_turn_seq}"
    )

    async def generate():
        nonlocal assistant_content, pending_parts, turn_message
        _cancelled = False
        terminal_event_persisted = False
        latest_task_state: dict[str, Any] | None = None
        next_part_seq = 1

        def _is_cancelled() -> bool:
            return _cancelled

        def _allocate_part_seq() -> int:
            nonlocal next_part_seq
            value = next_part_seq
            next_part_seq += 1
            return value

        def _flush_text_buffer() -> None:
            nonlocal assistant_content
            # Strip XML-style tool calls that some models emit as text content
            if assistant_content and _XML_TOOL_CALL_STRIP_RE.search(assistant_content):
                assistant_content = _strip_xml_tool_calls(assistant_content)
            if str(assistant_content or "").strip():
                last = pending_parts[-1] if pending_parts else None
                if last and last.get("type") == "text":
                    last["text"] = (last["text"] or "") + assistant_content
                else:
                    pending_parts.append({"type": "text", "text": assistant_content})
                assistant_content = ""

        async def _persist_turn_snapshot() -> None:
            """Durably upsert the logical assistant message at its current state."""
            nonlocal turn_message
            _flush_text_buffer()
            if not pending_parts:
                return
            text_parts = [
                p["text"] for p in pending_parts if p.get("type") == "text" and p.get("text")
            ]
            tool_use_parts = [p for p in pending_parts if p.get("type") == "tool_use"]
            legacy_content = "".join(text_parts)
            legacy_tool_calls = (
                [
                    {
                        "id": p.get("id", ""),
                        "name": p.get("name", ""),
                        "input": p.get("input"),
                        "result": p.get("result"),
                        "pending_action_token": p.get("pending_action_token"),
                        "pending_action_status": p.get("pending_action_status"),
                    }
                    for p in tool_use_parts
                ]
                if tool_use_parts
                else None
            )
            normalized_parts = json.loads(_json_dumps_safe(pending_parts))
            normalized_tool_calls = (
                json.loads(_json_dumps_safe(legacy_tool_calls)) if legacy_tool_calls else None
            )
            if turn_message is None:
                turn_message = models.Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=legacy_content,
                    agent_name=display_agent_name,
                    tool_calls=normalized_tool_calls,
                    content_parts=normalized_parts,
                    created_at=datetime.utcnow(),
                )
            else:
                turn_message.content = legacy_content
                turn_message.agent_name = display_agent_name
                turn_message.tool_calls = normalized_tool_calls
                turn_message.content_parts = normalized_parts
            await _save_messages_to_db([turn_message])

        async def _persist_runtime_event(
            event_type: str, phase: str | None, payload: dict | None
        ) -> None:
            normalized_payload = dict(payload or {})
            if event_type == "assistant":
                text = str(
                    normalized_payload.get("content") or normalized_payload.get("text") or ""
                )
                normalized_payload["content"] = text
                normalized_payload.setdefault("event_kind", "assistant_text")
                if turn_message is not None and turn_message.id is not None:
                    normalized_payload.setdefault("message_id", turn_message.id)
            if "trace_id" not in normalized_payload:
                normalized_payload["trace_id"] = trace_id
            normalized_payload.setdefault("turn_id", current_turn_id)
            normalized_payload.setdefault("turn_seq", current_turn_seq)
            normalized_payload.setdefault("part_seq", _allocate_part_seq())
            await _save_chat_events_to_db(
                [
                    models.ChatEvent(
                        conversation_id=conversation_id,
                        event_type=event_type,
                        phase=phase,
                        turn_id=current_turn_id,
                        turn_seq=current_turn_seq,
                        part_seq=int(normalized_payload["part_seq"]),
                        role="assistant",
                        agent_name=display_agent_name,
                        payload=_normalize_json_payload(normalized_payload),
                    )
                ]
            )

        try:
            skill_delta_event = {
                "type": "skill_delta",
                "data": skill_delta_payload,
            }
            yield _event_to_vds(
                _annotate_runtime_event(skill_delta_event, agent_name=display_agent_name)
            )
            async for event in chat_agent.stream_general_chat(
                messages=chat_messages,
                tools=tools if tools else None,
                system_prompt=system_prompt,
                default_datasource_id=conversation.datasource_id,
                conversation_id=conversation_id,
                scope_context=scope_context,
                is_cancelled=_is_cancelled,
                task_state=resumable_task_state,
            ):
                event_type = event.get("type")
                event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
                event_meta = event.get("meta") or {}

                if event_type == "assistant_progress":
                    progress_text = str(event_data.get("text") or "").strip()
                    if progress_text:
                        last = pending_parts[-1] if pending_parts else None
                        if not (
                            last
                            and last.get("type") == "progress"
                            and last.get("text") == progress_text
                        ):
                            pending_parts.append(
                                {
                                    "type": "progress",
                                    "text": progress_text,
                                    "stage": str(event_data.get("stage") or "working"),
                                }
                            )
                            await _persist_turn_snapshot()
                elif event_type == "tool_start":
                    # Flush any accumulated text as a text part before the tool
                    _flush_text_buffer()
                    tc_id = event_data.get("tool_call_id") or ""
                    pending_parts.append(
                        {
                            "type": "tool_use",
                            "id": tc_id,
                            "name": event_data.get("name") or "",
                            "input": _safe_parse_arguments(event_data.get("arguments")),
                            "result": None,
                            "pending_action_token": None,
                            "pending_action_status": None,
                        }
                    )
                    await _persist_turn_snapshot()
                elif event_type == "tool_result":
                    tc_id = event_data.get("tool_call_id") or ""
                    result_payload = event_data.get("result") or {}
                    result_full = (
                        result_payload
                        if isinstance(result_payload, dict)
                        else {"data": result_payload}
                    )
                    result_data = (
                        result_full.get("data") if isinstance(result_full, dict) else result_full
                    )
                    pending_action_token = None
                    pending_action_status = None
                    if isinstance(result_data, dict) and result_data.get("requires_confirmation"):
                        pending_action_token = (
                            result_data.get("action_token")
                            or result_data.get("pending_action_token")
                            or result_data.get("token")
                        )
                        pending_action_status = "pending"
                    matched = next(
                        (
                            p
                            for p in pending_parts
                            if p.get("type") == "tool_use" and p.get("id") == tc_id
                        ),
                        None,
                    )
                    if matched:
                        matched["result"] = result_full
                        if pending_action_token:
                            matched["pending_action_token"] = pending_action_token
                            matched["pending_action_status"] = pending_action_status
                    else:
                        pending_parts.append(
                            {
                                "type": "tool_use",
                                "id": tc_id,
                                "name": event_data.get("name") or "",
                                "input": _safe_parse_arguments(event_data.get("arguments")),
                                "result": result_full,
                                "pending_action_token": pending_action_token,
                                "pending_action_status": pending_action_status,
                            }
                        )
                    await _persist_turn_snapshot()

                if event_type == "assistant":
                    if event.get("phase") != "responding":
                        logger.info(
                            "skip_non_responding_assistant_event %s",
                            fmt_kv(conversation_id=conversation_id, phase=event.get("phase")),
                        )
                        continue
                    raw_piece = event_data.get("text", "")
                    if raw_piece:
                        assistant_content += raw_piece
                        await _persist_turn_snapshot()
                elif event_type == "tool_result":
                    event_result = event_data.get("result") or {}
                    event_result_data = event_result.get("data") or {}
                    resolved_datasource_id = event_result_data.get("resolved_datasource_id")
                    resolved_role = event_result_data.get("resolved_role")
                    route_reason = event_result_data.get("route_reason")
                    cluster_key = event_result_data.get("cluster_key")
                    event_data["resolved_datasource_id"] = resolved_datasource_id
                    event_data["resolved_role"] = resolved_role
                    event_data["route_reason"] = route_reason
                    event_data["cluster_key"] = cluster_key
                    event_data["trace_id"] = event_meta.get("trace_id") or trace_id
                    if event_meta.get("run_id") and not event_data.get("run_id"):
                        event_data["run_id"] = event_meta.get("run_id")
                    if event_meta.get("object_id") and not event_data.get("object_id"):
                        event_data["object_id"] = event_meta.get("object_id")
                    if event_meta.get("release_id") and not event_data.get("release_id"):
                        event_data["release_id"] = event_meta.get("release_id")

                    if event_result_data.get("action") == "save_agent":
                        user_input = str(event_result_data.get("user_input") or "").strip()
                        mapped_event = _map_tool_event_to_step_event(
                            event, trace_id=trace_id, route_source="chat_stream"
                        )
                        mapped_data_raw = (
                            mapped_event.get("data")
                            if isinstance(mapped_event.get("data"), dict)
                            else None
                        )
                        if mapped_data_raw is not None:
                            mapped_data_raw.setdefault("turn_id", current_turn_id)
                            mapped_data_raw.setdefault("turn_seq", current_turn_seq)
                            mapped_data_raw.setdefault("part_seq", _allocate_part_seq())
                        annotated = _annotate_runtime_event(
                            mapped_event, agent_name=display_agent_name
                        )
                        await _persist_runtime_event(
                            "step_result",
                            "tool_running",
                            annotated.get("data")
                            if isinstance(annotated.get("data"), dict)
                            else None,
                        )
                        yield _event_to_vds(annotated)
                        async for chunk in _stream_save_agent_workflow(
                            conversation_id=conversation_id,
                            conversation=conversation,
                            db=db,
                            user_input=user_input,
                            route_source="chat_stream",
                        ):
                            yield chunk
                        await _persist_turn_snapshot()
                        await _persist_runtime_event(
                            "done",
                            "done",
                            {
                                "status": "completed",
                                "completed": True,
                                "reason_code": "save_agent_workflow_completed",
                            },
                        )
                        terminal_event_persisted = True
                        return
                    elif event_result_data.get("action") == "run_agent":
                        mapped_event = _map_tool_event_to_step_event(
                            event, trace_id=trace_id, route_source="chat_stream"
                        )
                        mapped_data_raw = (
                            mapped_event.get("data")
                            if isinstance(mapped_event.get("data"), dict)
                            else None
                        )
                        if mapped_data_raw is not None:
                            mapped_data_raw.setdefault("turn_id", current_turn_id)
                            mapped_data_raw.setdefault("turn_seq", current_turn_seq)
                            mapped_data_raw.setdefault("part_seq", _allocate_part_seq())
                        annotated = _annotate_runtime_event(
                            mapped_event, agent_name=display_agent_name
                        )
                        await _persist_runtime_event(
                            "step_result",
                            "tool_running",
                            annotated.get("data")
                            if isinstance(annotated.get("data"), dict)
                            else None,
                        )
                        yield _event_to_vds(annotated)
                        agent_system_prompt = (
                            str(event_result_data.get("agent_prompt") or "").strip()
                            or system_prompt
                        )
                        agent_datasource_id = (
                            run_datasource_ids[0]
                            if run_datasource_ids
                            else conversation.datasource_id
                        )
                        agent_tools = [
                            t
                            for t in (tools or [])
                            if (t.get("function", {}).get("name") or t.get("name")) != "agent_run"
                        ]
                        async for event in chat_agent.stream_general_chat(
                            messages=chat_messages,
                            tools=agent_tools if agent_tools else None,
                            system_prompt=agent_system_prompt,
                            default_datasource_id=agent_datasource_id,
                            conversation_id=conversation_id,
                            scope_context=scope_context,
                            is_cancelled=_is_cancelled,
                        ):
                            inner_type = event.get("type")
                            inner_data = (
                                event.get("data") if isinstance(event.get("data"), dict) else {}
                            )
                            if inner_type == "assistant":
                                if event.get("phase") == "responding":
                                    assistant_content += str(inner_data.get("text") or "")
                                    await _persist_turn_snapshot()
                                    annotated_inner = _annotate_runtime_event(
                                        event, agent_name=display_agent_name
                                    )
                                    await _persist_runtime_event(
                                        "assistant", "responding", inner_data
                                    )
                                    yield _event_to_vds(annotated_inner)
                            elif inner_type in {"tool_start", "tool_result"}:
                                mapped = _map_tool_event_to_step_event(
                                    event, trace_id=trace_id, route_source="chat_stream"
                                )
                                mapped_inner_data = (
                                    mapped.get("data")
                                    if isinstance(mapped.get("data"), dict)
                                    else {}
                                )
                                tool_call_id = str(
                                    inner_data.get("tool_call_id")
                                    or mapped_inner_data.get("step_id")
                                    or ""
                                )
                                if inner_type == "tool_start":
                                    _flush_text_buffer()
                                    pending_parts.append(
                                        {
                                            "type": "tool_use",
                                            "id": tool_call_id,
                                            "name": inner_data.get("name") or "",
                                            "input": _safe_parse_arguments(
                                                inner_data.get("arguments")
                                            ),
                                            "result": None,
                                            "pending_action_token": None,
                                            "pending_action_status": None,
                                        }
                                    )
                                else:
                                    result_payload = inner_data.get("result") or {}
                                    result_full = (
                                        result_payload
                                        if isinstance(result_payload, dict)
                                        else {"data": result_payload}
                                    )
                                    matched = next(
                                        (
                                            part
                                            for part in pending_parts
                                            if part.get("type") == "tool_use"
                                            and part.get("id") == tool_call_id
                                        ),
                                        None,
                                    )
                                    if matched:
                                        matched["result"] = result_full
                                    else:
                                        pending_parts.append(
                                            {
                                                "type": "tool_use",
                                                "id": tool_call_id,
                                                "name": inner_data.get("name") or "",
                                                "input": _safe_parse_arguments(
                                                    inner_data.get("arguments")
                                                ),
                                                "result": result_full,
                                                "pending_action_token": None,
                                                "pending_action_status": None,
                                            }
                                        )
                                await _persist_turn_snapshot()
                                annotated_inner = _annotate_runtime_event(
                                    mapped, agent_name=display_agent_name
                                )
                                await _persist_runtime_event(
                                    str(mapped.get("type") or "step_result"),
                                    str(mapped.get("phase") or event.get("phase") or "tool_running"),
                                    mapped_inner_data,
                                )
                                yield _event_to_vds(annotated_inner)
                            elif inner_type == "error":
                                annotated_inner = _annotate_runtime_event(
                                    event, agent_name=display_agent_name
                                )
                                await _persist_runtime_event("error", "error", inner_data)
                                yield _event_to_vds(annotated_inner)
                            elif inner_type == "done":
                                await _persist_turn_snapshot()
                                annotated_inner = _annotate_runtime_event(
                                    event, agent_name=display_agent_name
                                )
                                await _persist_runtime_event("done", "done", inner_data)
                                terminal_event_persisted = True
                                yield _event_to_vds(annotated_inner)
                        return
                elif event_type == "error":
                    error_payload = event.get("data") or {}
                    error_class = ""
                    if isinstance(error_payload, dict):
                        error_class = str(error_payload.get("error_class") or "")
                    raw_message = _extract_error_message(error_payload)
                    event["data"] = {
                        "message": raw_message,
                        "user_message": raw_message,
                        "error_class": error_class or "runtime_error",
                    }
                elif event_type == "done":
                    await _persist_turn_snapshot()

                mapped_event = _map_tool_event_to_step_event(
                    event,
                    trace_id=trace_id,
                    route_source="chat_stream",
                )
                mapped_data_raw = (
                    mapped_event.get("data") if isinstance(mapped_event.get("data"), dict) else None
                )
                if mapped_data_raw is not None and mapped_event.get("type") in {
                    "step_start",
                    "step_result",
                    "assistant",
                    "assistant_progress",
                    "done",
                    "error",
                    "task_contract",
                    "progress",
                    "verification",
                    "task_state",
                    "checkpoint",
                    "context_compressed",
                }:
                    mapped_data_raw.setdefault("turn_id", current_turn_id)
                    mapped_data_raw.setdefault("turn_seq", current_turn_seq)
                    if mapped_event.get("type") in {"step_start", "step_result", "done", "error"}:
                        mapped_data_raw.setdefault("part_seq", _allocate_part_seq())
                annotated_event = _annotate_runtime_event(
                    mapped_event, agent_name=display_agent_name
                )
                mapped_type = annotated_event.get("type")
                mapped_phase = annotated_event.get("phase")
                mapped_data = (
                    annotated_event.get("data")
                    if isinstance(annotated_event.get("data"), dict)
                    else None
                )
                if mapped_type == "task_state" and isinstance(mapped_data, dict):
                    latest_task_state = dict(mapped_data)

                if mapped_type in {
                    "thinking",
                    "plan",
                    "assistant",
                    "assistant_progress",
                    "step_start",
                    "step_result",
                    "reflect",
                    "error",
                    "task_contract",
                    "progress",
                    "verification",
                    "task_state",
                    "checkpoint",
                    "context_compressed",
                    "done",
                }:
                    await _persist_runtime_event(
                        str(mapped_type),
                        str(mapped_phase) if mapped_phase else None,
                        mapped_data,
                    )
                    if mapped_type == "done":
                        terminal_event_persisted = True
                yield _event_to_vds(annotated_event)

                if request is not None and await request.is_disconnected():
                    _cancelled = True
                    logger.info("client_disconnected %s", fmt_kv(conversation_id=conversation_id))
                    await _persist_turn_snapshot()
                    resumable_state = dict(latest_task_state or {})
                    if resumable_state:
                        resumable_state["status"] = "checkpointed"
                    await _persist_runtime_event(
                        "checkpoint",
                        "reflecting",
                        {
                            "status": "checkpointed",
                            "reason_code": "client_disconnected",
                            "reason": "客户端连接已结束；已保存当前执行进度。",
                            "task_state": resumable_state or None,
                        },
                    )
                    await _persist_runtime_event(
                        "done",
                        "done",
                        {
                            "status": "incomplete",
                            "completed": False,
                            "reason_code": "client_disconnected",
                        },
                    )
                    terminal_event_persisted = True
                    break

        except Exception as e:
            logger.exception(
                "stream_generation_error %s error=%s",
                fmt_kv(conversation_id=conversation_id),
                str(e),
            )
            error_event = _annotate_runtime_event(
                {
                    "type": "error",
                    "data": {
                        "message": _extract_error_message(
                            {"message": str(e), "error_class": "runtime_error"}
                        ),
                        "user_message": _extract_error_message(
                            {"message": str(e), "error_class": "runtime_error"}
                        ),
                        "error_class": "runtime_error",
                    },
                },
                agent_name=display_agent_name,
            )
            yield _event_to_vds(error_event)
            await _persist_runtime_event(
                "error",
                "error",
                {
                    "message": _extract_error_message(
                        {"message": str(e), "error_class": "runtime_error"}
                    ),
                    "error_class": "runtime_error",
                },
            )
            await _persist_turn_snapshot()
            await _persist_runtime_event(
                "done",
                "done",
                {
                    "status": "error",
                    "completed": False,
                    "reason_code": "runtime_error",
                },
            )
            terminal_event_persisted = True
        finally:
            await _persist_turn_snapshot()
            if not terminal_event_persisted:
                await _persist_runtime_event(
                    "done",
                    "done",
                    {
                        "status": "incomplete",
                        "completed": False,
                        "reason_code": "stream_ended_without_terminal_event",
                    },
                )

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@router.post("/complete")
async def chat_complete(
    message: ChatCompleteRequest,
    db: Session = Depends(get_db),
):
    """Non-streaming version for simple testing."""
    content = message.content

    messages = [{"role": "user", "content": content}]

    chat_agent = ChatAgent(chat_service=get_chat_service())
    full_response = ""

    async for event in chat_agent.stream_general_chat(
        messages=messages,
        tools=None,
        system_prompt=None,
        default_datasource_id=None,
        conversation_id=None,
        scope_context=None,
    ):
        if event.get("type") == "assistant":
            full_response += (event.get("data") or {}).get("text", "")

    return {"content": full_response}
