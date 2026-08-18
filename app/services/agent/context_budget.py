"""Model-aware context budgeting shared by chat and agent runtimes."""

from __future__ import annotations

import json
import math
import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_text_tokens(value: Any) -> int:
    """Conservatively estimate tokens for mixed CJK and Latin content.

    OpenAI-compatible providers may use different tokenizers. CJK characters
    are therefore counted approximately one-for-one while the remaining text
    uses the common four-characters-per-token approximation.
    """

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    non_cjk_count = max(0, len(text) - cjk_count)
    return cjk_count + math.ceil(non_cjk_count / 4)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate one OpenAI-format chat message including tool metadata."""

    total = 4  # role and message framing overhead
    total += estimate_text_tokens(message.get("role") or "")
    total += estimate_text_tokens(message.get("content") or "")
    total += estimate_text_tokens(message.get("name") or "")
    total += estimate_text_tokens(message.get("tool_call_id") or "")
    if message.get("tool_calls"):
        total += estimate_text_tokens(message["tool_calls"])
    return total


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate tokens for a complete message list."""

    return sum(estimate_message_tokens(message) for message in messages) + 3


def estimate_payload_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate the full request payload, including tool schemas."""

    total = estimate_messages_tokens(messages)
    if tools:
        total += estimate_text_tokens(tools)
    return total
