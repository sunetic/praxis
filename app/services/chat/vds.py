"""Vercel Data Stream Protocol helpers.

https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol

Type codes:
  0  text delta          g  reasoning delta
  9  tool_call start     a  tool_result
  2  data (custom)       e  finish_step    d  finish_message
"""

from __future__ import annotations

import json
from typing import Any


def _vds_text(delta: str) -> str:
    return f"0:{json.dumps(delta, ensure_ascii=False)}\n"


def _vds_reasoning(delta: str) -> str:
    return f"g:{json.dumps(delta, ensure_ascii=False)}\n"


def _vds_tool_call(tool_call_id: str, tool_name: str, args: dict) -> str:
    return f"9:{json.dumps({'toolCallId': tool_call_id, 'toolName': tool_name, 'args': args}, default=str, ensure_ascii=False)}\n"


def _vds_tool_result(tool_call_id: str, result: Any) -> str:
    return f"a:{json.dumps({'toolCallId': tool_call_id, 'result': result}, default=str, ensure_ascii=False)}\n"


def _vds_data(items: list) -> str:
    return f"2:{json.dumps(items, default=str, ensure_ascii=False)}\n"


def _vds_finish_step(finish_reason: str = "tool-calls") -> str:
    return f"e:{json.dumps({'finishReason': finish_reason}, ensure_ascii=False)}\n"


def _vds_finish_message(finish_reason: str = "stop") -> str:
    return f"d:{json.dumps({'finishReason': finish_reason}, ensure_ascii=False)}\n"


def event_to_vds(event: dict[str, Any]) -> str:
    """Convert an internal runtime event to Vercel Data Stream lines."""
    event_type = str(event.get("type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    lines: list[str] = []

    if event_type == "assistant":
        text = str(data.get("text") or "")
        if text:
            lines.append(_vds_text(text))
    elif event_type == "thinking":
        text = str(data.get("text") or data.get("thinking") or "")
        if text:
            lines.append(_vds_reasoning(text))
    elif event_type == "tool_start":
        lines.append(_vds_tool_call(
            str(data.get("tool_call_id") or data.get("id") or ""),
            str(data.get("name") or ""),
            data.get("arguments") or {},
        ))
    elif event_type == "tool_result":
        lines.append(_vds_tool_result(
            str(data.get("tool_call_id") or data.get("id") or ""),
            data.get("result"),
        ))
        lines.append(_vds_finish_step("tool-calls"))
    elif event_type == "done":
        lines.append(_vds_finish_message("stop"))
    elif event_type == "error":
        lines.append(_vds_data([{"type": "error", "message": str(data.get("message") or data.get("error") or "")}]))
        lines.append(_vds_finish_message("error"))
    else:
        lines.append(_vds_data([{"type": event_type, **data}]))

    return "".join(lines)
