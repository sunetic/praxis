"""Message history loading and context window management for chat stream."""

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.models import models

logger = get_logger("chat.history")
settings = get_settings()


def ensure_stream_user_message(
    db: Session,
    conversation_id: int,
    incoming_content: str,
) -> models.Message | None:
    """Idempotently persist the user turn accepted by the stream endpoint.

    The web client normally creates the user message immediately before opening
    the stream. Direct API clients may call the stream endpoint on its own. This
    helper supports both protocols without duplicating the web client's row.
    """
    normalized_content = str(incoming_content or "").strip()
    if not normalized_content:
        return None

    latest_message = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.desc(), models.Message.id.desc())
        .first()
    )
    if (
        latest_message is not None
        and latest_message.role == "user"
        and str(latest_message.content or "").strip() == normalized_content
    ):
        user_message = latest_message
    else:
        user_message = models.Message(
            conversation_id=conversation_id,
            role="user",
            content=normalized_content,
        )
        db.add(user_message)
        db.flush()

    latest_user_event = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type == "user_message",
        )
        .order_by(models.ChatEvent.id.desc())
        .first()
    )
    latest_user_payload = (
        latest_user_event.payload
        if latest_user_event is not None and isinstance(latest_user_event.payload, dict)
        else {}
    )
    if latest_user_payload.get("message_id") != user_message.id:
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
        next_turn_seq = int(latest_turn_event.turn_seq or 0) + 1 if latest_turn_event else 1
        db.add(
            models.ChatEvent(
                conversation_id=conversation_id,
                event_type="user_message",
                phase="message_created",
                turn_id=f"message-{user_message.id}-{uuid.uuid4().hex[:8]}",
                turn_seq=next_turn_seq,
                part_seq=0,
                role="user",
                payload={
                    "content": normalized_content,
                    "message_id": user_message.id,
                    "event_kind": "user_message",
                },
                created_at=user_message.created_at or datetime.utcnow(),
            )
        )

    conversation = db.get(models.Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = user_message.created_at or datetime.utcnow()
    db.commit()
    db.refresh(user_message)
    return user_message


def load_chat_messages(
    db: Session,
    conversation_id: int,
    incoming_content: str,
) -> tuple[list[dict[str, Any]], list]:
    """Load message history, format for LLM, and apply sliding window.

    Returns (formatted_chat_messages, raw_db_messages).
    """
    logger.info("fetch_messages_start %s", fmt_kv(conversation_id=conversation_id))
    raw_messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.asc())
        .all()
    )

    chat_messages = _format_messages_for_llm(raw_messages)
    latest_persisted_user_matches = bool(
        raw_messages
        and raw_messages[-1].role == "user"
        and str(raw_messages[-1].content or "").strip() == str(incoming_content or "").strip()
    )
    if incoming_content and not latest_persisted_user_matches:
        chat_messages.append({"role": "user", "content": incoming_content})

    _apply_sliding_window(chat_messages, settings.ai_context_char_limit)

    logger.info(
        "fetch_messages_done %s",
        fmt_kv(conversation_id=conversation_id, message_count=len(chat_messages)),
    )
    return chat_messages, raw_messages


def _format_messages_for_llm(messages: list) -> list[dict[str, Any]]:
    """Convert DB messages into LLM-consumable dicts."""
    chat_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.role not in {"user", "assistant"}:
            continue
        parts = m.content_parts if isinstance(m.content_parts, list) else None
        if m.role == "assistant" and parts:
            tool_calls_openai = []
            text_parts = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("text") or "").strip():
                    text_parts.append(part["text"])
                elif part.get("type") == "tool_use":
                    tc_id = part.get("id") or f"tool_{part.get('name', '')}"
                    tool_calls_openai.append(
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": part.get("name") or "",
                                "arguments": json.dumps(
                                    part.get("input") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    )
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if text_parts:
                assistant_msg["content"] = "\n".join(text_parts)
            if tool_calls_openai:
                assistant_msg["tool_calls"] = tool_calls_openai
            if text_parts or tool_calls_openai:
                chat_messages.append(assistant_msg)
            for part in parts:
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                tc_id = part.get("id") or f"tool_{part.get('name', '')}"
                result = part.get("result")
                result_text = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False, default=str)
                )
                chat_messages.append(
                    {"role": "tool", "tool_call_id": tc_id, "content": result_text}
                )
        elif m.role == "assistant" and m.tool_calls:
            tool_calls_openai = []
            text_content = str(m.content or "").strip()
            for tc in m.tool_calls:
                tc_id = tc.get("id") or f"tool_{tc.get('name', '')}"
                tool_calls_openai.append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": tc.get("name") or "",
                            "arguments": json.dumps(tc.get("input") or {}, ensure_ascii=False),
                        },
                    }
                )
            assistant_msg = {"role": "assistant"}
            if text_content:
                assistant_msg["content"] = text_content
            if tool_calls_openai:
                assistant_msg["tool_calls"] = tool_calls_openai
            if text_content or tool_calls_openai:
                chat_messages.append(assistant_msg)
            for tc in m.tool_calls:
                tc_id = tc.get("id") or f"tool_{tc.get('name', '')}"
                result = tc.get("result")
                result_text = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False, default=str)
                )
                chat_messages.append(
                    {"role": "tool", "tool_call_id": tc_id, "content": result_text}
                )
        else:
            chat_messages.append({"role": m.role, "content": m.content})
    return chat_messages


def _apply_sliding_window(chat_messages: list[dict], context_char_limit: int) -> None:
    """Drop oldest messages (keeping first) until under char limit."""
    while len(chat_messages) > 2:
        total = sum(
            len(json.dumps(m.get("content", ""), ensure_ascii=False, default=str))
            for m in chat_messages
        )
        if total <= context_char_limit:
            break
        chat_messages.pop(1)
