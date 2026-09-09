from __future__ import annotations

from collections.abc import Callable
from typing import Any


def emit_coding_event(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
    *,
    domain_label: str,
) -> None:
    """Translate shared reasoning events into the stable build phase envelope."""
    if callback is None:
        return
    event_type = str(event.get("type") or "").strip()
    if event_type not in {
        "thinking",
        "plan",
        "tool_start",
        "tool_result",
        "progress",
        "reflect",
        "context_status",
        "error",
        "done",
    }:
        return
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    safe_data = sanitize_coding_event_data(event_type, data, domain_label=domain_label)
    summary = coding_event_summary(event_type, safe_data, domain_label=domain_label)
    tool_failed = event_type == "tool_result" and safe_data.get("success") is False
    status = (
        "failed"
        if event_type == "error" or tool_failed
        else "done"
        if event_type in {"tool_result", "done"}
        else "running"
    )
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    safe_meta = {key: meta[key] for key in ("iteration", "run_id", "task_run_id") if key in meta}
    try:
        callback(
            {
                "type": "phase",
                "phase": str(event.get("phase") or event_type),
                "status": status,
                "summary": summary[:500],
                "payload": {
                    "agent_event_type": event_type,
                    "data": safe_data,
                    "meta": safe_meta,
                },
            }
        )
    except Exception:
        return


def sanitize_coding_event_data(
    event_type: str, data: dict[str, Any], *, domain_label: str
) -> dict[str, Any]:
    if event_type == "thinking":
        return {"message": str(data.get("message") or "")[:300]}
    if event_type == "plan":
        return {
            "iteration": data.get("iteration"),
            "has_tool_calls": bool(data.get("has_tool_calls")),
            "tool_call_count": int(data.get("tool_call_count") or 0),
        }
    if event_type == "tool_start":
        return {
            "tool_call_id": str(data.get("tool_call_id") or ""),
            "name": str(data.get("name") or ""),
            "parallel": bool(data.get("parallel")),
        }
    if event_type == "tool_result":
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        safe_result_data = {
            key: result_data[key]
            for key in (
                "changed_file",
                "changed_files",
                "revision",
                "revisions",
                "ok",
                "result_type",
                "probe_attempt",
                "check_attempt",
                "outcome",
            )
            if key in result_data
        }
        safe_error = {
            key: str(error[key])[:300]
            for key in ("code", "category", "message")
            if error.get(key) is not None
        }
        return {
            "tool_call_id": str(data.get("tool_call_id") or ""),
            "name": str(data.get("name") or ""),
            "success": bool(result.get("success")),
            "result": safe_result_data,
            "error": safe_error or None,
            "error_class": data.get("error_class"),
            "parallel": bool(data.get("parallel")),
        }
    if event_type in {"progress", "reflect"}:
        return {
            key: data[key] for key in ("action", "decision", "reason", "reason_code") if key in data
        }
    if event_type == "context_status":
        return {
            key: data[key] for key in ("state", "used_percent", "remaining_tokens") if key in data
        }
    if event_type == "error":
        return {
            "message": str(data.get("message") or f"{domain_label} build failed")[:500],
            "error_class": str(data.get("error_class") or "")[:100],
        }
    if event_type == "done":
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        return {
            "status": str(data.get("status") or ""),
            "completed": bool(data.get("completed")),
            "metrics": {
                key: metrics[key]
                for key in ("iterations", "tool_calls", "tool_failures", "elapsed_ms")
                if key in metrics
            },
        }
    return {}


def coding_event_summary(event_type: str, data: dict[str, Any], *, domain_label: str) -> str:
    if event_type == "thinking":
        return str(data.get("message") or f"Analyzing the {domain_label} request")
    if event_type == "plan":
        return f"Planning the next {domain_label} change"
    if event_type == "tool_start":
        return f"Running {data.get('name') or f'{domain_label} tool'}"
    if event_type == "tool_result":
        suffix = "completed" if data.get("success") else "failed"
        return f"{data.get('name') or f'{domain_label} tool'} {suffix}"
    if event_type in {"progress", "reflect"}:
        return str(data.get("reason") or f"Reviewing {domain_label} build progress")
    if event_type == "context_status":
        return f"{domain_label} build context updated"
    if event_type == "error":
        return str(data.get("message") or f"{domain_label} build failed")
    if event_type == "done":
        return f"{domain_label} agent run completed"
    return event_type.replace("_", " ")
