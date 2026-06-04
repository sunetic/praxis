"""Chat handoff endpoints — split from app/api/chat.py for module size."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.services.datasource.router import (
    DataSourceRoutingError,
    resolve_preferred_execution_datasource,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

HANDOFF_EVENT_TYPE = "handoff"
HANDOFF_STATUS_PENDING = "pending"
HANDOFF_STATUS_CONSUMED = "consumed"
HANDOFF_MAX_TEXT_LENGTH = 2000
HANDOFF_MAX_FACTS = 6
HANDOFF_MAX_PROMPTS = 4
HANDOFF_MAX_LIST_ITEMS = 6


def _normalize_json_payload(payload: dict | None) -> dict | None:
    import json
    if payload is None:
        return None
    return json.loads(json.dumps(payload, default=str, ensure_ascii=False))


def _truncate_handoff_text(value: Any, *, limit: int = HANDOFF_MAX_TEXT_LENGTH) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


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


def _trim_handoff_context(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return _truncate_handoff_text(value, limit=256)
    if isinstance(value, str):
        return _truncate_handoff_text(value)
    if isinstance(value, list):
        return [
            _trim_handoff_context(item, depth=depth + 1)
            for item in value[:HANDOFF_MAX_LIST_ITEMS]
        ]
    if isinstance(value, dict):
        trimmed: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            trimmed[key] = _trim_handoff_context(raw_value, depth=depth + 1)
        return trimmed
    return value


def _normalize_handoff_packet(packet: dict[str, Any]) -> dict[str, Any]:
    source = packet.get("source") if isinstance(packet.get("source"), dict) else {}
    facts = packet.get("facts") if isinstance(packet.get("facts"), list) else []
    normalized_facts: list[dict[str, str]] = []
    for item in facts[:HANDOFF_MAX_FACTS]:
        if not isinstance(item, dict):
            continue
        label = _truncate_handoff_text(item.get("label"), limit=64)
        value = _truncate_handoff_text(item.get("value"), limit=160)
        if not label or not value:
            continue
        normalized_facts.append({"label": label, "value": value})

    return {
        "type": _truncate_handoff_text(packet.get("type"), limit=64),
        "version": int(packet.get("version") or 1),
        "source": {
            "page": _truncate_handoff_text(source.get("page"), limit=64),
            "entry": _truncate_handoff_text(source.get("entry"), limit=64),
            "label": _truncate_handoff_text(source.get("label"), limit=120) or None,
        },
        "title": _truncate_handoff_text(packet.get("title"), limit=160),
        "summary": _truncate_handoff_text(packet.get("summary"), limit=320) or None,
        "facts": normalized_facts,
        "suggested_prompts": _normalize_string_list(
            packet.get("suggested_prompts"),
            limit=HANDOFF_MAX_PROMPTS,
        ),
        "context": _trim_handoff_context(
            packet.get("context") if isinstance(packet.get("context"), dict) else {}
        ),
    }


def _handoff_status(payload: dict[str, Any] | None) -> str:
    raw = ""
    if isinstance(payload, dict):
        raw = str(payload.get("status") or "").strip().lower()
    return raw if raw in {HANDOFF_STATUS_PENDING, HANDOFF_STATUS_CONSUMED} else HANDOFF_STATUS_PENDING


def _handoff_consumed_at(payload: dict[str, Any] | None) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("consumed_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _serialize_handoff_event(event: models.ChatEvent) -> schemas.ChatHandoffResponse:
    payload = event.payload if isinstance(event.payload, dict) else {}
    packet = {
        "type": payload.get("type") or "",
        "version": payload.get("version") or 1,
        "source": payload.get("source") or {},
        "title": payload.get("title") or "",
        "summary": payload.get("summary"),
        "facts": payload.get("facts") or [],
        "suggested_prompts": payload.get("suggested_prompts") or [],
        "context": payload.get("context") or {},
    }
    return schemas.ChatHandoffResponse(
        id=event.id,
        conversation_id=event.conversation_id,
        status=_handoff_status(payload),
        consumed_at=_handoff_consumed_at(payload),
        packet=schemas.ChatHandoffPacket.model_validate(packet),
        created_at=event.created_at,
    )


def _get_handoff_event(
    db: Session,
    *,
    conversation_id: int,
    handoff_id: int,
) -> models.ChatEvent | None:
    return (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.id == handoff_id,
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type == HANDOFF_EVENT_TYPE,
        )
        .first()
    )


def _mark_handoff_consumed(
    db: Session,
    event: models.ChatEvent,
    *,
    consumed_with_message: str = "",
) -> models.ChatEvent:
    payload = dict(event.payload or {})
    if _handoff_status(payload) == HANDOFF_STATUS_CONSUMED:
        return event
    payload["status"] = HANDOFF_STATUS_CONSUMED
    payload["consumed_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    if consumed_with_message.strip():
        payload["consumed_with_message"] = _truncate_handoff_text(
            consumed_with_message,
            limit=280,
        )
    event.phase = HANDOFF_STATUS_CONSUMED
    event.payload = _normalize_json_payload(payload)
    db.add(event)
    return event


def _format_handoff_context_for_prompt(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_label = (
        str(source.get("label") or "").strip()
        or str(source.get("page") or "").strip()
        or "unknown"
    )
    source_entry = str(source.get("entry") or "").strip() or "unknown"
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    datasource = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
    focus = context.get("focus") if isinstance(context.get("focus"), dict) else {}
    signals = context.get("signals") if isinstance(context.get("signals"), list) else []
    current_plan = context.get("current_plan") if isinstance(context.get("current_plan"), dict) else {}

    lines = [
        "Handoff Context (first turn only):",
        f"- source: {source_label} / {source_entry}",
        f"- type: {str(payload.get('type') or '').strip() or 'unknown'}",
    ]
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if title:
        lines.append(f"- title: {title}")
    if summary:
        lines.append(f"- summary: {summary}")
    if datasource:
        lines.append(
            "- datasource: "
            f"id={datasource.get('id')}, "
            f"name={datasource.get('name') or '-'}, "
            f"cluster_key={datasource.get('cluster_key') or '-'}"
        )
    if focus:
        focus_parts = []
        for key in ("kind", "sql_id", "db_name", "user_name"):
            value = focus.get(key)
            if value in (None, ""):
                continue
            focus_parts.append(f"{key}={value}")
        if focus_parts:
            lines.append(f"- focus: {', '.join(focus_parts)}")
    if facts:
        lines.append("- key_facts:")
        for item in facts[:HANDOFF_MAX_FACTS]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if label and value:
                lines.append(f"  - {label}: {value}")
    sql_text = str(context.get("sql_text") or "").strip()
    if sql_text:
        lines.append("SQL Text:")
        lines.append(sql_text)
    if current_plan:
        lines.append(
            "- current_plan: "
            f"plan_id={current_plan.get('plan_id') or '-'}, "
            f"plan_hash={current_plan.get('plan_hash') or '-'}, "
            f"table_scan={current_plan.get('table_scan') if current_plan.get('table_scan') is not None else '-'}"
        )
    if signals:
        lines.append("- signals:")
        for signal in signals[:HANDOFF_MAX_LIST_ITEMS]:
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
            lines.append(f"  - {line}")
    ai_summary = str(context.get("ai_summary") or "").strip()
    if ai_summary:
        lines.append(f"- ai_summary: {ai_summary}")
    investigation_steps = (
        context.get("investigation_steps")
        if isinstance(context.get("investigation_steps"), list)
        else []
    )
    if investigation_steps:
        lines.append("- suggested_investigation:")
        for item in investigation_steps[:HANDOFF_MAX_LIST_ITEMS]:
            text = str(item or "").strip()
            if text:
                lines.append(f"  - {text}")
    lines.append(
        "- Treat these as page-provided facts for the first turn. Continue naturally without asking the user to restate them."
    )
    lines.append(
        "- For this first handoff turn, answer directly from these facts/signals/ai_summary. Do not call tools in this turn."
    )
    lines.append(
        "- If the current evidence is still insufficient, say what should be verified next instead of launching a broad investigation."
    )
    return "\n".join(lines)


def _resolve_handoff_preferred_execution_datasource_id(
    db: Session,
    *,
    source_datasource_id: int | None,
    explicit_preferred_execution_datasource_id: int | None,
    packet_context: dict[str, Any] | None,
) -> int | None:
    if isinstance(explicit_preferred_execution_datasource_id, int) and explicit_preferred_execution_datasource_id > 0:
        return explicit_preferred_execution_datasource_id
    if not isinstance(source_datasource_id, int) or source_datasource_id <= 0:
        return None
    context = packet_context if isinstance(packet_context, dict) else {}
    datasource = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
    try:
        routed = resolve_preferred_execution_datasource(
            db,
            source_datasource_id,
            tenant_id=datasource.get("tenant_id") if isinstance(datasource.get("tenant_id"), int) else None,
            db_name=str(datasource.get("db_name") or "").strip() or None,
        )
        return routed.datasource.id
    except DataSourceRoutingError:
        return source_datasource_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{conversation_id}/events", response_model=list[schemas.ChatEventResponse])
def list_chat_events(conversation_id: int, db: Session = Depends(get_db)):
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    records = (
        db.query(models.ChatEvent)
        .filter(models.ChatEvent.conversation_id == conversation_id)
        .all()
    )
    return sorted(
        records,
        key=lambda event: (
            event.turn_seq if event.turn_seq is not None else 10**9,
            event.part_seq if event.part_seq is not None else 10**9,
            event.created_at,
            event.id,
        ),
    )


@router.post("/handoffs", response_model=schemas.ChatHandoffCreateResponse)
def create_chat_handoff(
    request: schemas.ChatHandoffCreate,
    db: Session = Depends(get_db),
):
    conversation = None
    if request.conversation_id is not None:
        conversation = (
            db.query(models.Conversation)
            .filter(models.Conversation.id == request.conversation_id)
            .first()
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

    packet_data = _normalize_handoff_packet(request.packet.model_dump(mode="json"))
    context = packet_data.get("context") if isinstance(packet_data.get("context"), dict) else {}
    datasource = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
    inferred_datasource_id = datasource.get("id")
    source_datasource_id = request.datasource_id or (
        inferred_datasource_id if isinstance(inferred_datasource_id, int) else None
    )
    preferred_execution_datasource_id = _resolve_handoff_preferred_execution_datasource_id(
        db,
        source_datasource_id=source_datasource_id,
        explicit_preferred_execution_datasource_id=request.preferred_execution_datasource_id,
        packet_context=context,
    )
    datasource_id = preferred_execution_datasource_id or source_datasource_id
    if context:
        execution_context = context.get("execution") if isinstance(context.get("execution"), dict) else {}
        execution_context.update(
            {
                "source_datasource_id": source_datasource_id,
                "preferred_execution_datasource_id": preferred_execution_datasource_id,
                "preferred_role": "user",
            }
        )
        context["execution"] = execution_context
        packet_data["context"] = context

    if conversation is None:
        conversation = models.Conversation(
            title=(request.title or packet_data.get("title") or "New Chat"),
            datasource_id=datasource_id,
        )
        db.add(conversation)
        db.flush()
    elif datasource_id and conversation.datasource_id is None:
        conversation.datasource_id = datasource_id
        db.add(conversation)

    packet_data["status"] = HANDOFF_STATUS_PENDING
    event = models.ChatEvent(
        conversation_id=conversation.id,
        event_type=HANDOFF_EVENT_TYPE,
        phase=HANDOFF_STATUS_PENDING,
        payload=_normalize_json_payload(packet_data),
    )
    db.add(event)
    db.commit()
    db.refresh(conversation)
    db.refresh(event)
    return schemas.ChatHandoffCreateResponse(
        conversation=schemas.ConversationResponse.model_validate(conversation),
        handoff=_serialize_handoff_event(event),
    )


@router.get(
    "/{conversation_id}/handoffs/{handoff_id}",
    response_model=schemas.ChatHandoffResponse,
)
def get_chat_handoff(
    conversation_id: int,
    handoff_id: int,
    db: Session = Depends(get_db),
):
    event = _get_handoff_event(db, conversation_id=conversation_id, handoff_id=handoff_id)
    if not event:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return _serialize_handoff_event(event)


@router.post(
    "/{conversation_id}/handoffs/{handoff_id}/consume",
    response_model=schemas.ChatHandoffResponse,
)
def consume_chat_handoff(
    conversation_id: int,
    handoff_id: int,
    db: Session = Depends(get_db),
):
    event = _get_handoff_event(db, conversation_id=conversation_id, handoff_id=handoff_id)
    if not event:
        raise HTTPException(status_code=404, detail="Handoff not found")
    _mark_handoff_consumed(db, event)
    db.commit()
    db.refresh(event)
    return _serialize_handoff_event(event)
