"""Chat pending-action endpoints — split from app/api/chat.py for module size."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.db.database import get_db
from app.models import models
from app.services.datasource.access import normalize_access_level
from app.services.datasource.sql_guard import (
    build_execution_fingerprint,
    compare_tenant_fingerprint,
    probe_tenant_fingerprint,
)
from app.services.platform.object_tools import ObjectToolError, ObjectToolService

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = get_logger("chat.pending")


def _json_dumps_safe(payload: dict) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def _normalize_json_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return json.loads(_json_dumps_safe(payload))


def _serialize_pending_action(action: models.PendingAction) -> dict:
    payload = action.payload or {}
    tenant_fingerprint = payload.get("tenant_fingerprint")
    serialized = {
        "token": action.token,
        "action_type": action.action_type,
        "status": action.status,
        "batch_id": payload.get("batch_id"),
        "sql": payload.get("sql"),
        "sql_preview": payload.get("sql_preview"),
        "intent": payload.get("intent"),
        "resolved_datasource_id": payload.get("resolved_datasource_id"),
        "resolved_role": payload.get("resolved_role"),
        "resolved_access_level": payload.get("resolved_access_level"),
        "cluster_key": payload.get("cluster_key"),
        "tenant_fingerprint": tenant_fingerprint if isinstance(tenant_fingerprint, dict) else {},
        "execution_fingerprint": payload.get("execution_fingerprint"),
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }
    if action.action_type == "object_action":
        serialized.update(
            {
                "mode": payload.get("mode"),
                "object_type": payload.get("object_type"),
                "object_action": payload.get("action"),
                "object_id": payload.get("object_id"),
                "preview": payload.get("preview"),
                "risk_level": payload.get("risk_level"),
                "confirmation_policy": payload.get("confirmation_policy"),
                "idempotency_key": payload.get("idempotency_key"),
                "source_text": payload.get("source_text"),
            }
        )
    return serialized


def _find_pending_tool_result_event(
    db: Session,
    *,
    conversation_id: int,
    token: str,
) -> models.ChatEvent | None:
    records = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type == "step_result",
        )
        .order_by(models.ChatEvent.id.desc())
        .limit(500)
        .all()
    )
    for record in records:
        payload = record.payload if isinstance(record.payload, dict) else {}
        if str(payload.get("name") or "") != "execute_sql":
            continue
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        action_token = str(data.get("action_token") or "")
        if action_token == token:
            return record
    return None


def _find_message_with_pending_token(
    db: Session,
    *,
    conversation_id: int,
    token: str,
) -> tuple[models.Message, int] | None:
    """Find the assistant Message containing a pending_action_token in tool_calls.
    Returns (message, index_in_tool_calls) or None if not found.
    """
    messages = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.role == "assistant",
        )
        .order_by(models.Message.id.desc())
        .limit(50)
        .all()
    )
    for msg in messages:
        # Prefer content_parts (new format), fall back to tool_calls (legacy)
        parts = msg.content_parts if isinstance(msg.content_parts, list) else []
        for idx, part in enumerate(parts):
            if (
                isinstance(part, dict)
                and part.get("type") == "tool_use"
                and part.get("pending_action_token") == token
            ):
                return (msg, idx)
        if not parts:
            tool_calls = msg.tool_calls if isinstance(msg.tool_calls, list) else []
            for idx, tc in enumerate(tool_calls):
                if isinstance(tc, dict) and tc.get("pending_action_token") == token:
                    return (msg, idx)
    return None


def _update_pending_message_tool_result(
    db: Session,
    *,
    conversation_id: int,
    token: str,
    status: str,
    result: dict[str, Any],
) -> None:
    """Persist the final tool outcome on the message that rendered the approval UI."""
    msg_result = _find_message_with_pending_token(
        db,
        conversation_id=conversation_id,
        token=token,
    )
    if not msg_result:
        return

    pending_msg, tool_index = msg_result
    normalized_result = _normalize_json_payload(result)
    if isinstance(pending_msg.content_parts, list):
        updated_parts = list(pending_msg.content_parts)
        updated_parts[tool_index] = {
            **updated_parts[tool_index],
            "pending_action_status": status,
            "result": normalized_result,
        }
        pending_msg.content_parts = updated_parts
        tool_use_parts = [
            part
            for part in updated_parts
            if isinstance(part, dict) and part.get("type") == "tool_use"
        ]
        if tool_use_parts:
            pending_msg.tool_calls = [
                {key: value for key, value in part.items() if key != "type"}
                for part in tool_use_parts
            ]
    else:
        updated_tool_calls = list(pending_msg.tool_calls or [])
        updated_tool_calls[tool_index] = {
            **updated_tool_calls[tool_index],
            "pending_action_status": status,
            "result": normalized_result,
        }
        pending_msg.tool_calls = updated_tool_calls
    db.add(pending_msg)


def _build_object_tool_session_factory(db: Session) -> sessionmaker[Session]:
    bind = db.get_bind()
    return sessionmaker(bind=bind, autocommit=False, autoflush=False, expire_on_commit=False)


def _find_pending_action_result_event(
    db: Session,
    *,
    conversation_id: int,
    token: str,
) -> models.ChatEvent | None:
    records = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == conversation_id,
            models.ChatEvent.event_type == "step_result",
        )
        .order_by(models.ChatEvent.id.desc())
        .limit(500)
        .all()
    )
    for record in records:
        payload = record.payload if isinstance(record.payload, dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        action_token = str(data.get("action_token") or payload.get("action_token") or "")
        if action_token == token:
            return record
    return None


@router.get("/{conversation_id}/actions/pending")
def list_pending_actions(conversation_id: int, db: Session = Depends(get_db)):
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    actions = (
        db.query(models.PendingAction)
        .filter(
            models.PendingAction.conversation_id == conversation_id,
            models.PendingAction.status == "pending",
        )
        .order_by(models.PendingAction.created_at.asc())
        .all()
    )
    return [_serialize_pending_action(action) for action in actions]


@router.post("/{conversation_id}/actions/{token}/cancel")
def cancel_pending_action(conversation_id: int, token: str, db: Session = Depends(get_db)):
    action = (
        db.query(models.PendingAction)
        .filter(
            models.PendingAction.conversation_id == conversation_id,
            models.PendingAction.token == token,
            models.PendingAction.status == "pending",
        )
        .first()
    )
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")

    now = datetime.now(UTC).replace(tzinfo=None)
    action.status = "cancelled"
    action.cancelled_at = now
    action.updated_at = now
    db.add(action)
    cancelled_result: dict[str, Any] = {
        "success": False,
        "data": {
            "requires_confirmation": False,
            "action_token": token,
            "cancelled_action_token": token,
            "cancelled": True,
        },
        "error": {
            "code": "action_cancelled",
            "message": "Pending action cancelled by user.",
        },
    }
    if action.action_type == "execute_sql":
        pending_tool_event = _find_pending_tool_result_event(
            db,
            conversation_id=conversation_id,
            token=token,
        )
        if pending_tool_event:
            base_payload = (
                pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
            )
            payload = dict(base_payload)
            payload["result"] = cancelled_result
            payload["error_class"] = "cancelled"
            payload["cancelled_action_token"] = token
            pending_tool_event.payload = _normalize_json_payload(payload)
            db.add(pending_tool_event)
    elif action.action_type == "object_action":
        pending_action_event = _find_pending_action_result_event(
            db,
            conversation_id=conversation_id,
            token=token,
        )
        if pending_action_event:
            base_payload = (
                pending_action_event.payload
                if isinstance(pending_action_event.payload, dict)
                else {}
            )
            payload = dict(base_payload)
            if pending_action_event.event_type == "step_result":
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
                payload["result"] = {
                    "success": False,
                    "data": {
                        **result_data,
                        "requires_confirmation": False,
                        "cancelled_action_token": token,
                        "cancelled": True,
                    },
                    "error": {
                        "code": "action_cancelled",
                        "message": "Pending action cancelled by user.",
                    },
                }
            else:
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                payload["data"] = {
                    **data,
                    "requires_confirmation": False,
                    "cancelled_action_token": token,
                    "cancelled": True,
                    "error": {
                        "code": "action_cancelled",
                        "message": "Pending action cancelled by user.",
                    },
                }
            payload["message"] = "Pending action cancelled."
            pending_action_event.payload = _normalize_json_payload(payload)
            db.add(pending_action_event)
            db.add(
                models.Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="Pending action cancelled.",
                )
            )
    _update_pending_message_tool_result(
        db,
        conversation_id=conversation_id,
        token=token,
        status="cancelled",
        result=cancelled_result,
    )
    db.commit()
    return {"success": True, "token": token, "status": "cancelled"}


@router.post("/{conversation_id}/actions/{token}/confirm")
async def confirm_pending_action(conversation_id: int, token: str, db: Session = Depends(get_db)):
    from app.db.connection import get_db_pool

    action = (
        db.query(models.PendingAction)
        .filter(
            models.PendingAction.conversation_id == conversation_id,
            models.PendingAction.token == token,
            models.PendingAction.status == "pending",
        )
        .first()
    )
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")

    payload = action.payload or {}
    if action.action_type == "object_action":
        mode = str(payload.get("mode") or "").strip().lower()
        object_type = str(payload.get("object_type") or "").strip().lower()
        operation = str(payload.get("action") or "").strip().lower()
        object_id_raw = payload.get("object_id")
        object_id = int(object_id_raw) if isinstance(object_id_raw, int) else None
        raw_object_payload = payload.get("payload")
        object_payload = raw_object_payload if isinstance(raw_object_payload, dict) else {}
        capability_key = str(payload.get("capability_key") or "object.crud").strip()

        if not mode or not object_type or not operation:
            raise HTTPException(
                status_code=400, detail="Pending object action payload is incomplete"
            )

        object_service = ObjectToolService(session_factory=_build_object_tool_session_factory(db))
        now = datetime.now(UTC).replace(tzinfo=None)
        action.confirmed_at = now
        try:
            if mode == "crud":
                result = await object_service.crud(
                    object_type=object_type,
                    action=operation,
                    object_id=object_id,
                    payload=object_payload,
                    actor="user_confirmed",
                )
            elif mode == "operate":
                if object_id is None:
                    raise HTTPException(
                        status_code=400, detail="object_id is required for operate confirmation"
                    )
                result = await object_service.operate(
                    object_type=object_type,
                    action=operation,
                    object_id=object_id,
                    payload=object_payload,
                    actor="user_confirmed",
                )
            else:
                raise HTTPException(
                    status_code=400, detail=f"Unsupported object action mode: {mode}"
                )

            action.status = "executed"
            action.executed_at = now
            action.updated_at = now
            db.add(action)

            assistant_message = _build_action_result_text(capability_key, result)
            pending_action_event = _find_pending_action_result_event(
                db,
                conversation_id=conversation_id,
                token=token,
            )
            if pending_action_event:
                base_payload = (
                    pending_action_event.payload
                    if isinstance(pending_action_event.payload, dict)
                    else {}
                )
                event_payload = dict(base_payload)
                if pending_action_event.event_type == "step_result":
                    existing_result = (
                        event_payload.get("result")
                        if isinstance(event_payload.get("result"), dict)
                        else {}
                    )
                    existing_data = (
                        existing_result.get("data")
                        if isinstance(existing_result.get("data"), dict)
                        else {}
                    )
                    event_payload["result"] = {
                        "success": True,
                        "data": {
                            **existing_data,
                            "requires_confirmation": False,
                            "action_type": "object_action",
                            "action_token": token,
                            "confirmed_action_token": token,
                            "mode": mode,
                            "object_type": object_type,
                            "action": operation,
                            "object_id": object_id,
                            "result": result,
                            "error": None,
                        },
                        "error": None,
                    }
                else:
                    existing_data = (
                        event_payload.get("data")
                        if isinstance(event_payload.get("data"), dict)
                        else {}
                    )
                    event_payload["data"] = {
                        **existing_data,
                        "requires_confirmation": False,
                        "action_type": "object_action",
                        "action_token": token,
                        "confirmed_action_token": token,
                        "mode": mode,
                        "object_type": object_type,
                        "action": operation,
                        "object_id": object_id,
                        "result": result,
                        "error": None,
                    }
                event_payload["message"] = assistant_message
                pending_action_event.payload = _normalize_json_payload(event_payload)
                db.add(pending_action_event)
            else:
                db.add(
                    models.ChatEvent(
                        conversation_id=conversation_id,
                        event_type="step_result",
                        phase="tool_running",
                        payload={
                            "step_id": f"confirm-{token}",
                            "kind": "action",
                            "name": capability_key,
                            "arguments": json.dumps(
                                {
                                    "mode": mode,
                                    "object_type": object_type,
                                    "action": operation,
                                    "object_id": object_id,
                                },
                                ensure_ascii=False,
                            ),
                            "result": {
                                "success": True,
                                "data": {
                                    "requires_confirmation": False,
                                    "action_type": "object_action",
                                    "action_token": token,
                                    "confirmed_action_token": token,
                                    "mode": mode,
                                    "object_type": object_type,
                                    "action": operation,
                                    "object_id": object_id,
                                    "result": result,
                                    "error": None,
                                },
                                "error": None,
                            },
                            "message": assistant_message,
                            "trace_id": str(uuid.uuid4()),
                            "route_source": "confirm_pending_action",
                        },
                    )
                )
            db.add(
                models.Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_message,
                )
            )
            _update_pending_message_tool_result(
                db,
                conversation_id=conversation_id,
                token=token,
                status="confirmed",
                result={
                    "success": True,
                    "data": {
                        "requires_confirmation": False,
                        "action_type": "object_action",
                        "action_token": token,
                        "confirmed_action_token": token,
                        "mode": mode,
                        "object_type": object_type,
                        "action": operation,
                        "object_id": object_id,
                        "result": result,
                    },
                    "error": None,
                },
            )
            db.commit()
            return {
                "success": True,
                "token": token,
                "status": "executed",
                "result": result,
            }
        except HTTPException:
            raise
        except ObjectToolError as exc:
            action.status = "failed"
            action.updated_at = now
            db.add(action)
            pending_action_event = _find_pending_action_result_event(
                db,
                conversation_id=conversation_id,
                token=token,
            )
            error_payload = {"code": exc.code, "message": str(exc), "details": exc.details or {}}
            if pending_action_event:
                base_payload = (
                    pending_action_event.payload
                    if isinstance(pending_action_event.payload, dict)
                    else {}
                )
                event_payload = dict(base_payload)
                if pending_action_event.event_type == "step_result":
                    existing_result = (
                        event_payload.get("result")
                        if isinstance(event_payload.get("result"), dict)
                        else {}
                    )
                    existing_data = (
                        existing_result.get("data")
                        if isinstance(existing_result.get("data"), dict)
                        else {}
                    )
                    event_payload["result"] = {
                        "success": False,
                        "data": {
                            **existing_data,
                            "requires_confirmation": False,
                            "action_type": "object_action",
                            "action_token": token,
                            "confirmed_action_token": token,
                        },
                        "error": error_payload,
                    }
                else:
                    existing_data = (
                        event_payload.get("data")
                        if isinstance(event_payload.get("data"), dict)
                        else {}
                    )
                    event_payload["data"] = {
                        **existing_data,
                        "requires_confirmation": False,
                        "action_type": "object_action",
                        "action_token": token,
                        "confirmed_action_token": token,
                        "error": error_payload,
                    }
                event_payload["message"] = f"Object action execution failed: {str(exc)}"
                pending_action_event.payload = _normalize_json_payload(event_payload)
                db.add(pending_action_event)
            _update_pending_message_tool_result(
                db,
                conversation_id=conversation_id,
                token=token,
                status="failed",
                result={
                    "success": False,
                    "data": {
                        "requires_confirmation": False,
                        "action_type": "object_action",
                        "action_token": token,
                        "confirmed_action_token": token,
                    },
                    "error": error_payload,
                },
            )
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            action.status = "failed"
            action.updated_at = now
            db.add(action)
            _update_pending_message_tool_result(
                db,
                conversation_id=conversation_id,
                token=token,
                status="failed",
                result={
                    "success": False,
                    "data": {
                        "requires_confirmation": False,
                        "action_type": "object_action",
                        "action_token": token,
                        "confirmed_action_token": token,
                    },
                    "error": {
                        "code": "object_action_execution_error",
                        "message": str(exc),
                    },
                },
            )
            db.commit()
            raise HTTPException(
                status_code=500, detail=f"Object action execution error: {str(exc)}"
            ) from exc

    if action.action_type != "execute_sql":
        raise HTTPException(
            status_code=400, detail=f"Unsupported action type: {action.action_type}"
        )

    sql = str(payload.get("sql") or "").strip()
    resolved_role = str(payload.get("resolved_role") or "").strip().lower()
    resolved_access_level = normalize_access_level(
        str(payload.get("resolved_access_level") or resolved_role)
    )
    fingerprint_access_level = (
        resolved_access_level if payload.get("resolved_access_level") else resolved_role
    )
    resolved_datasource_id = payload.get("resolved_datasource_id")
    raw_expected_fingerprint = payload.get("tenant_fingerprint")
    expected_fingerprint = (
        raw_expected_fingerprint if isinstance(raw_expected_fingerprint, dict) else {}
    )
    expected_execution_fingerprint = str(payload.get("execution_fingerprint") or "")

    if not sql or not isinstance(resolved_datasource_id, int) or not resolved_role:
        raise HTTPException(status_code=400, detail="Pending action payload is incomplete")

    datasource = (
        db.query(models.DataSource)
        .filter(
            models.DataSource.id == resolved_datasource_id,
            models.DataSource.status == "active",
        )
        .first()
    )
    if not datasource:
        raise HTTPException(status_code=404, detail="Resolved datasource is unavailable")

    pool = get_db_pool()
    current_fingerprint = await probe_tenant_fingerprint(pool, datasource, resolved_role)
    mismatches = compare_tenant_fingerprint(expected_fingerprint, current_fingerprint)
    if mismatches:
        now = datetime.now(UTC).replace(tzinfo=None)
        action.status = "rejected"
        action.updated_at = now
        db.add(action)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Target tenant fingerprint changed. Refuse to execute pending action.",
        )

    current_execution_fingerprint = build_execution_fingerprint(
        sql=sql,
        resolved_datasource_id=resolved_datasource_id,
        resolved_role=fingerprint_access_level,
        tenant_fingerprint=expected_fingerprint,
    )
    if (
        expected_execution_fingerprint
        and current_execution_fingerprint != expected_execution_fingerprint
    ):
        raise HTTPException(status_code=409, detail="Execution fingerprint mismatch")

    now = datetime.now(UTC).replace(tzinfo=None)
    action.confirmed_at = now
    try:
        result = await pool.execute_query(datasource, sql, role=resolved_role)
        result["resolved_datasource_id"] = datasource.id
        result["resolved_role"] = resolved_role
        result["resolved_access_level"] = resolved_access_level
        result["route_reason"] = "confirmed_pending_action"
        result["cluster_key"] = datasource.cluster_key

        action.status = "executed"
        action.executed_at = now
        action.updated_at = now
        db.add(action)
        pending_tool_event = _find_pending_tool_result_event(
            db,
            conversation_id=conversation_id,
            token=token,
        )
        if pending_tool_event:
            base_payload = (
                pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
            )
            event_payload = dict(base_payload)
            event_payload["result"] = {
                "success": True,
                "data": result,
                "error": None,
            }
            event_payload["error_class"] = "none"
            event_payload["confirmed_action_token"] = token
            pending_tool_event.payload = _normalize_json_payload(event_payload)
            db.add(pending_tool_event)
        else:
            db.add(
                models.ChatEvent(
                    conversation_id=conversation_id,
                    event_type="step_result",
                    phase="tool_running",
                    payload={
                        "step_id": f"confirm-{token}",
                        "kind": "tool",
                        "name": "execute_sql",
                        "arguments": json.dumps(
                            {
                                "sql": sql,
                                "datasource_id": resolved_datasource_id,
                                "access_level": resolved_access_level,
                                "intent": payload.get("intent") or "",
                            },
                            ensure_ascii=False,
                        ),
                        "result": {
                            "success": True,
                            "data": result,
                            "error": None,
                        },
                        "error_class": "none",
                        "confirmed_action_token": token,
                        "trace_id": str(uuid.uuid4()),
                        "route_source": "confirm_pending_action",
                    },
                )
            )
        _update_pending_message_tool_result(
            db,
            conversation_id=conversation_id,
            token=token,
            status="confirmed",
            result={"success": True, "data": result, "error": None},
        )
        db.commit()
        return {
            "success": True,
            "token": token,
            "status": "executed",
            "result": result,
            "should_resume": True,
        }
    except Exception as exc:
        action.status = "failed"
        action.updated_at = now
        db.add(action)
        error_message = str(exc)
        pending_tool_event = _find_pending_tool_result_event(
            db,
            conversation_id=conversation_id,
            token=token,
        )
        error_result_payload = {
            "success": False,
            "data": {
                "requires_confirmation": False,
                "action_type": "execute_sql",
                "action_token": token,
                "confirmed_action_token": token,
                "resolved_datasource_id": datasource.id,
                "resolved_role": resolved_role,
                "resolved_access_level": resolved_access_level,
                "cluster_key": datasource.cluster_key,
                "tenant_fingerprint": current_fingerprint,
                "execution_fingerprint": current_execution_fingerprint,
            },
            "error": {
                "code": "sql_execution_error",
                "message": f"SQL execution error: {error_message}",
                "db_message": error_message,
            },
        }
        if pending_tool_event:
            base_payload = (
                pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
            )
            event_payload = dict(base_payload)
            existing_result = (
                event_payload.get("result") if isinstance(event_payload.get("result"), dict) else {}
            )
            existing_data = (
                existing_result.get("data") if isinstance(existing_result.get("data"), dict) else {}
            )
            error_result_payload["data"] = {
                **existing_data,
                **(
                    error_result_payload.get("data")
                    if isinstance(error_result_payload.get("data"), dict)
                    else {}
                ),
            }
            event_payload["result"] = error_result_payload
            event_payload["message"] = f"SQL execution failed: {error_message}"
            event_payload["error_class"] = "execution_error"
            event_payload["confirmed_action_token"] = token
            pending_tool_event.payload = _normalize_json_payload(event_payload)
            db.add(pending_tool_event)
        else:
            db.add(
                models.ChatEvent(
                    conversation_id=conversation_id,
                    event_type="step_result",
                    phase="tool_running",
                    payload={
                        "step_id": f"confirm-{token}",
                        "kind": "tool",
                        "name": "execute_sql",
                        "arguments": json.dumps(
                            {
                                "sql": sql,
                                "datasource_id": resolved_datasource_id,
                                "role": resolved_role,
                                "intent": payload.get("intent") or "",
                            },
                            ensure_ascii=False,
                        ),
                        "result": error_result_payload,
                        "error_class": "execution_error",
                        "confirmed_action_token": token,
                        "trace_id": str(uuid.uuid4()),
                        "route_source": "confirm_pending_action",
                    },
                )
            )
        _update_pending_message_tool_result(
            db,
            conversation_id=conversation_id,
            token=token,
            status="failed",
            result=error_result_payload,
        )
        db.commit()
        return {
            "success": False,
            "token": token,
            "status": "failed",
            "error": error_message,
            "should_resume": True,
        }


def _build_action_result_text(action: str, data: dict[str, Any]) -> str:
    if action.endswith(".crud"):
        object_type = str(data.get("object_type") or "object")
        operation = str(data.get("action") or "unknown")
        if operation == "list":
            count = data.get("count")
            return f"{object_type} list retrieved ({count} item(s))."
        if operation == "read":
            item_id = data.get("id") or data.get("object_id")
            return f"{object_type} #{item_id} details retrieved."
        if operation == "create":
            item_id = data.get("id") or data.get("object_id")
            return f"{object_type} created (#{item_id})."
        if operation == "update":
            item_id = data.get("id") or data.get("object_id")
            return f"{object_type} updated (#{item_id})."
        if operation == "delete":
            item_id = data.get("object_id")
            return f"{object_type} deleted (#{item_id})."
    if action.endswith(".operate"):
        object_type = str(data.get("object_type") or "object")
        operation = str(data.get("action") or "unknown")
        item_id = data.get("id") or data.get("object_id")
        return f"{object_type} action `{operation}` executed (#{item_id})."
    return "Action executed."
