"""Message history loading and context window management for chat stream."""

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.models import models

logger = get_logger("chat.history")
settings = get_settings()


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
    if incoming_content:
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
                    tool_calls_openai.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": part.get("name") or "",
                            "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                        },
                    })
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
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                chat_messages.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})
        elif m.role == "assistant" and m.tool_calls:
            tool_calls_openai = []
            text_content = str(m.content or "").strip()
            for tc in m.tool_calls:
                tc_id = tc.get("id") or f"tool_{tc.get('name', '')}"
                tool_calls_openai.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name") or "",
                        "arguments": json.dumps(tc.get("input") or {}, ensure_ascii=False),
                    },
                })
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
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                chat_messages.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})
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
