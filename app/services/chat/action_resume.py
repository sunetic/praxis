"""Restore an Agent task after a user-confirmed action reaches a terminal result."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import models

TERMINAL_ACTION_STATUSES = {"executed", "failed"}


@dataclass(frozen=True)
class ActionResumeContext:
    """Persisted context required to continue the original Agent task."""

    token: str
    action_type: str
    status: str
    user_request: str
    task_state: dict[str, Any] | None
    execution: dict[str, Any]

    def prompt_payload(self) -> dict[str, str]:
        return {
            "action_type": self.action_type,
            "action_status": self.status,
        }


def _event_action_token(payload: dict[str, Any]) -> str:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    for candidate in (
        payload.get("confirmed_action_token"),
        payload.get("action_token"),
        data.get("confirmed_action_token"),
        data.get("action_token"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _task_state_from_event(event: models.ChatEvent) -> dict[str, Any] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    state = payload.get("task_state") if event.event_type == "checkpoint" else payload
    if not isinstance(state, dict) or not state.get("task_run_id"):
        return None
    return copy.deepcopy(state)


def load_action_resume_context(
    db: Session,
    *,
    conversation_id: int,
    token: str,
) -> ActionResumeContext | None:
    """Load a terminal action result together with the task state it paused."""

    normalized_token = str(token or "").strip()
    if not normalized_token:
        return None
    action = (
        db.query(models.PendingAction)
        .filter(
            models.PendingAction.conversation_id == conversation_id,
            models.PendingAction.token == normalized_token,
        )
        .first()
    )
    if action is None or action.status not in TERMINAL_ACTION_STATUSES:
        return None

    result_event: models.ChatEvent | None = None
    events = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type.in_(["step_result", "tool_result"]),
        )
        .order_by(models.ChatEvent.id.desc())
        .limit(500)
        .all()
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if _event_action_token(payload) == normalized_token:
            result_event = event
            break
    if result_event is None:
        return None

    event_payload = result_event.payload if isinstance(result_event.payload, dict) else {}
    result = event_payload.get("result")
    if not isinstance(result, dict) or bool(result.get("success")) != (action.status == "executed"):
        return None

    task_state = None
    if result_event.turn_id:
        state_events = (
            db.query(models.ChatEvent)
            .filter(
                models.ChatEvent.conversation_id == conversation_id,
                models.ChatEvent.turn_id == result_event.turn_id,
                models.ChatEvent.event_type.in_(["task_state", "checkpoint"]),
            )
            .order_by(models.ChatEvent.id.desc())
            .all()
        )
        for state_event in state_events:
            task_state = _task_state_from_event(state_event)
            if task_state is not None:
                break

    latest_user_message = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.role == "user",
        )
        .order_by(models.Message.created_at.desc(), models.Message.id.desc())
        .first()
    )
    user_request = str(latest_user_message.content or "").strip() if latest_user_message else ""
    execution = {
        "tool_call_id": f"confirmed-{normalized_token}",
        "name": str(event_payload.get("name") or action.action_type or "unknown_tool"),
        "arguments": event_payload.get("arguments") or action.payload or {},
        "result": copy.deepcopy(result),
        "error_class": str(
            event_payload.get("error_class")
            or ("none" if result.get("success") else "execution_error")
        ),
    }
    return ActionResumeContext(
        token=normalized_token,
        action_type=str(action.action_type or "unknown"),
        status=str(action.status),
        user_request=user_request,
        task_state=task_state,
        execution=execution,
    )
