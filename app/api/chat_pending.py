"""Chat pending-action endpoints — split from app/api/chat.py for module size."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.api.chat_handoff import _truncate_handoff_text
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.datasource.sql_guard import (
    build_execution_fingerprint,
    compare_tenant_fingerprint,
    normalize_sql,
    probe_tenant_fingerprint,
)
from app.services.llm import get_llm_client
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
            if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("pending_action_token") == token:
                return (msg, idx)
        if not parts:
            tool_calls = msg.tool_calls if isinstance(msg.tool_calls, list) else []
            for idx, tc in enumerate(tool_calls):
                if isinstance(tc, dict) and tc.get("pending_action_token") == token:
                    return (msg, idx)
    return None


def _build_object_tool_session_factory(db: Session) -> sessionmaker[Session]:
    bind = db.get_bind()
    return sessionmaker(bind=bind, autocommit=False, autoflush=False, expire_on_commit=False)


def _build_confirmed_sql_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    columns = result.get("columns") if isinstance(result.get("columns"), list) else []
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    sample_rows: list[dict[str, Any]] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        sample_rows.append(
            {
                str(key): _truncate_handoff_text(value, limit=120)
                for key, value in row.items()
            }
        )
    return {
        "row_count": int(result.get("row_count") or 0),
        "columns": [str(column) for column in columns[:8]],
        "sample_rows": sample_rows,
    }


def _build_confirmed_sql_resume_fallback(*, intent: str, sql: str, result: dict[str, Any]) -> str:
    summary = _build_confirmed_sql_result_summary(result)
    row_count = int(summary.get("row_count") or 0)
    sample_rows = summary.get("sample_rows") if isinstance(summary.get("sample_rows"), list) else []
    prefix = f"Executed as requested: {intent}." if intent else "Confirmed action executed."
    if row_count <= 0:
        return f"{prefix} SQL executed successfully, but no results were returned."
    if sample_rows:
        return f"{prefix} SQL executed successfully, returning {row_count} result(s). Sample: {json.dumps(sample_rows[0], ensure_ascii=False)}"
    normalized_sql = normalize_sql(sql)
    return f"{prefix} SQL executed successfully, returning {row_count} result(s). SQL: {normalized_sql}"


async def _render_confirmed_sql_followup(
    *,
    db: Session,
    conversation_id: int,
    prompt_payload: dict[str, Any],
    system_prompt: str,
    fallback: str,
    log_key: str,
) -> str:
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
    llm_payload = {
        "user_request": user_request,
        **prompt_payload,
    }
    llm = get_llm_client()
    try:
        response = None
        async for chunk in llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(llm_payload, ensure_ascii=False)},
            ],
            tools=None,
            stream=False,
            temperature=0.2,
        ):
            response = chunk
            break
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                content = str(message.get("content") or "").strip()
                if content:
                    return content
    except Exception as exc:
        logger.warning(
            "%s %s error=%s",
            log_key,
            fmt_kv(conversation_id=conversation_id),
            str(exc),
        )
    return fallback


async def _build_confirmed_sql_resume_message(
    *,
    db: Session,
    conversation_id: int,
    intent: str,
    sql: str,
    result: dict[str, Any],
) -> str:
    fallback = _build_confirmed_sql_resume_fallback(intent=intent, sql=sql, result=result)
    prompt_payload = {
        "intent": intent,
        "sql": normalize_sql(sql),
        "result_summary": _build_confirmed_sql_result_summary(result),
        "result_route_reason": str(result.get("route_reason") or ""),
        "cluster_key": str(result.get("cluster_key") or ""),
        "resolved_role": str(result.get("resolved_role") or ""),
    }
    system_prompt = (
        "You are a helpful database assistant. "
        "A previously pending SQL action has now been confirmed and executed. "
        "Continue the conversation in plain text only. "
        "Do not call tools. Do not ask for confirmation again. "
        "Summarize the execution result directly for the user in 2-4 short sentences."
    )
    return await _render_confirmed_sql_followup(
        db=db,
        conversation_id=conversation_id,
        prompt_payload=prompt_payload,
        system_prompt=system_prompt,
        fallback=fallback,
        log_key="confirmed_sql_resume_message_failed",
    )


def _build_confirmed_sql_failure_resume_fallback(*, intent: str, sql: str, error_message: str) -> str:
    prefix = f"Executed as confirmed: {intent}." if intent else "Confirmed action executed."
    normalized_sql = normalize_sql(sql)
    return (
        f"{prefix} However, execution failed: {error_message}. "
        f"I will not resubmit the write operation; if you would like, I can analyze the cause of this error and suggest next steps for correction."
        f" SQL: {normalized_sql}"
    )


async def _build_confirmed_sql_failure_resume_message(
    *,
    db: Session,
    conversation_id: int,
    intent: str,
    sql: str,
    error_message: str,
    resolved_role: str,
    cluster_key: str,
) -> str:
    fallback = _build_confirmed_sql_failure_resume_fallback(intent=intent, sql=sql, error_message=error_message)
    prompt_payload = {
        "intent": intent,
        "sql": normalize_sql(sql),
        "error_message": error_message,
        "cluster_key": cluster_key,
        "resolved_role": resolved_role,
    }
    system_prompt = (
        "You are a helpful database assistant. "
        "A previously pending SQL action was confirmed by the user, but execution failed. "
        "Continue the conversation in plain text only. "
        "Do not call tools. Do not ask for confirmation again. Do not claim success. "
        "Briefly explain that execution failed, surface the key error, and suggest the next diagnostic direction in 2-4 short sentences."
    )
    return await _render_confirmed_sql_followup(
        db=db,
        conversation_id=conversation_id,
        prompt_payload=prompt_payload,
        system_prompt=system_prompt,
        fallback=fallback,
        log_key="confirmed_sql_failure_resume_message_failed",
    )


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
    if action.action_type == "execute_sql":
        pending_tool_event = _find_pending_tool_result_event(
            db,
            conversation_id=conversation_id,
            token=token,
        )
        if pending_tool_event:
            base_payload = pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
            payload = dict(base_payload)
            payload["result"] = {
                "success": False,
                "data": None,
                "error": {
                    "code": "action_cancelled",
                    "message": "Pending action cancelled by user.",
                },
            }
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
            base_payload = pending_action_event.payload if isinstance(pending_action_event.payload, dict) else {}
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
            raise HTTPException(status_code=400, detail="Pending object action payload is incomplete")

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
                    raise HTTPException(status_code=400, detail="object_id is required for operate confirmation")
                result = await object_service.operate(
                    object_type=object_type,
                    action=operation,
                    object_id=object_id,
                    payload=object_payload,
                    actor="user_confirmed",
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported object action mode: {mode}")

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
                base_payload = pending_action_event.payload if isinstance(pending_action_event.payload, dict) else {}
                event_payload = dict(base_payload)
                if pending_action_event.event_type == "step_result":
                    existing_result = event_payload.get("result") if isinstance(event_payload.get("result"), dict) else {}
                    existing_data = (
                        existing_result.get("data") if isinstance(existing_result.get("data"), dict) else {}
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
                    existing_data = event_payload.get("data") if isinstance(event_payload.get("data"), dict) else {}
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
            msg_result = _find_message_with_pending_token(db, conversation_id=conversation_id, token=token)
            if msg_result:
                pending_msg, tc_idx = msg_result
                if isinstance(pending_msg.content_parts, list):
                    updated_parts = list(pending_msg.content_parts)
                    updated_parts[tc_idx] = {**updated_parts[tc_idx], "pending_action_status": "confirmed"}
                    pending_msg.content_parts = updated_parts
                    # Keep legacy tool_calls in sync
                    tool_use_parts = [p for p in updated_parts if isinstance(p, dict) and p.get("type") == "tool_use"]
                    if tool_use_parts:
                        pending_msg.tool_calls = [{k: v for k, v in p.items() if k != "type"} for p in tool_use_parts]
                else:
                    updated_tool_calls = list(pending_msg.tool_calls or [])
                    updated_tool_calls[tc_idx] = {**updated_tool_calls[tc_idx], "pending_action_status": "confirmed"}
                    pending_msg.tool_calls = updated_tool_calls
                db.add(pending_msg)
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
                base_payload = pending_action_event.payload if isinstance(pending_action_event.payload, dict) else {}
                event_payload = dict(base_payload)
                if pending_action_event.event_type == "step_result":
                    existing_result = event_payload.get("result") if isinstance(event_payload.get("result"), dict) else {}
                    existing_data = (
                        existing_result.get("data") if isinstance(existing_result.get("data"), dict) else {}
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
                    existing_data = event_payload.get("data") if isinstance(event_payload.get("data"), dict) else {}
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
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            action.status = "failed"
            action.updated_at = now
            db.add(action)
            db.commit()
            raise HTTPException(status_code=500, detail=f"Object action execution error: {str(exc)}") from exc

    if action.action_type != "execute_sql":
        raise HTTPException(status_code=400, detail=f"Unsupported action type: {action.action_type}")

    sql = str(payload.get("sql") or "").strip()
    resolved_role = str(payload.get("resolved_role") or "").strip().lower()
    resolved_datasource_id = payload.get("resolved_datasource_id")
    raw_expected_fingerprint = payload.get("tenant_fingerprint")
    expected_fingerprint = raw_expected_fingerprint if isinstance(raw_expected_fingerprint, dict) else {}
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
        resolved_role=resolved_role,
        tenant_fingerprint=expected_fingerprint,
    )
    if expected_execution_fingerprint and current_execution_fingerprint != expected_execution_fingerprint:
        raise HTTPException(status_code=409, detail="Execution fingerprint mismatch")

    now = datetime.now(UTC).replace(tzinfo=None)
    action.confirmed_at = now
    try:
        result = await pool.execute_query(datasource, sql, role=resolved_role)
        result["resolved_datasource_id"] = datasource.id
        result["resolved_role"] = resolved_role
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
            base_payload = pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
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
                                "role": resolved_role,
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
        assistant_message = await _build_confirmed_sql_resume_message(
            db=db,
            conversation_id=conversation_id,
            intent=str(payload.get("intent") or "").strip(),
            sql=sql,
            result=result,
        )
        if assistant_message:
            db.add(
                models.Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_message,
                )
            )
        msg_result = _find_message_with_pending_token(db, conversation_id=conversation_id, token=token)
        if msg_result:
            pending_msg, tc_idx = msg_result
            if isinstance(pending_msg.content_parts, list):
                updated_parts = list(pending_msg.content_parts)
                updated_parts[tc_idx] = {**updated_parts[tc_idx], "pending_action_status": "confirmed"}
                pending_msg.content_parts = updated_parts
                tool_use_parts = [p for p in updated_parts if isinstance(p, dict) and p.get("type") == "tool_use"]
                if tool_use_parts:
                    pending_msg.tool_calls = [{k: v for k, v in p.items() if k != "type"} for p in tool_use_parts]
            else:
                updated_tool_calls = list(pending_msg.tool_calls or [])
                updated_tool_calls[tc_idx] = {**updated_tool_calls[tc_idx], "pending_action_status": "confirmed"}
                pending_msg.tool_calls = updated_tool_calls
            db.add(pending_msg)
        db.commit()
        return {
            "success": True,
            "token": token,
            "status": "executed",
            "result": result,
            "assistant_message": assistant_message,
        }
    except Exception as exc:
        action.status = "failed"
        action.updated_at = now
        db.add(action)
        error_message = str(exc)
        assistant_message = await _build_confirmed_sql_failure_resume_message(
            db=db,
            conversation_id=conversation_id,
            intent=str(payload.get("intent") or "").strip(),
            sql=sql,
            error_message=error_message,
            resolved_role=resolved_role,
            cluster_key=str(datasource.cluster_key or ""),
        )
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
            base_payload = pending_tool_event.payload if isinstance(pending_tool_event.payload, dict) else {}
            event_payload = dict(base_payload)
            existing_result = event_payload.get("result") if isinstance(event_payload.get("result"), dict) else {}
            existing_data = existing_result.get("data") if isinstance(existing_result.get("data"), dict) else {}
            error_result_payload["data"] = {
                **existing_data,
                **(error_result_payload.get("data") if isinstance(error_result_payload.get("data"), dict) else {}),
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
        db.add(
            models.Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_message,
            )
        )
        msg_result = _find_message_with_pending_token(db, conversation_id=conversation_id, token=token)
        if msg_result:
            pending_msg, tc_idx = msg_result
            if isinstance(pending_msg.content_parts, list):
                updated_parts = list(pending_msg.content_parts)
                updated_parts[tc_idx] = {
                    **updated_parts[tc_idx],
                    "pending_action_status": "cancelled",
                    "result": error_result_payload,
                }
                pending_msg.content_parts = updated_parts
                tool_use_parts = [p for p in updated_parts if isinstance(p, dict) and p.get("type") == "tool_use"]
                if tool_use_parts:
                    pending_msg.tool_calls = [{k: v for k, v in p.items() if k != "type"} for p in tool_use_parts]
            else:
                updated_tool_calls = list(pending_msg.tool_calls or [])
                updated_tool_calls[tc_idx] = {
                    **updated_tool_calls[tc_idx],
                    "pending_action_status": "cancelled",
                    "result": error_result_payload,
                }
                pending_msg.tool_calls = updated_tool_calls
            db.add(pending_msg)
        db.commit()
        return {
            "success": False,
            "token": token,
            "status": "failed",
            "error": error_message,
            "assistant_message": assistant_message,
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

