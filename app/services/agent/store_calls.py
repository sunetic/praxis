"""Approval and tool-call persistence operations."""

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
)
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.models import agent_runs as tables
from app.services.agent.store_base import (
    CallChangedError,
    OutcomeUnknownError,
    ResourceBusyError,
    RunConflictError,
    RunNotFoundError,
    StoreBase,
    fingerprint,
)


class ToolCallStoreMixin(StoreBase):
    def prepare_call(
        self,
        run_id: str,
        owner_id: str,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        target: dict[str, Any],
        mutating: bool,
        resource_key: str | None,
        needs_approval: bool,
        history: Sequence[ModelMessage],
        auto_approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        digest = fingerprint(
            {
                "tool": name,
                "arguments": arguments,
                "target": target,
                "mutating": mutating,
                "resource_key": resource_key,
            }
        )
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            if row["cancel_requested"]:
                raise RunConflictError("Cancellation requested; no more tools may be dispatched")
            self._save_messages(db, row, history)
            key = and_(tables.tool_calls.c.run_id == run_id, tables.tool_calls.c.call_id == call_id)
            existing = db.execute(select(tables.tool_calls).where(key)).mappings().first()
            if existing:
                if existing["fingerprint"] != digest:
                    if existing["status"] in {"executing", "outcome_unknown"}:
                        raise OutcomeUnknownError(
                            "A dispatched call changed identity; reconciliation is required"
                        )
                    raise CallChangedError(
                        "Tool target or arguments changed after the call was recorded"
                    )
                return dict(existing)
            rejected = db.scalar(
                select(tables.approvals.c.call_id)
                .where(
                    tables.approvals.c.run_id == run_id,
                    tables.approvals.c.fingerprint == digest,
                    tables.approvals.c.decision == "denied",
                )
                .limit(1)
            )
            state = "denied" if rejected else ("waiting_approval" if needs_approval else "ready")
            result = (
                {"outcome": "denied", "content": "The same action was already denied in this run."}
                if rejected
                else None
            )
            db.execute(
                insert(tables.tool_calls).values(
                    run_id=run_id,
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    target=target,
                    fingerprint=digest,
                    mutating=mutating,
                    resource_key=resource_key,
                    status=state,
                    result=result,
                )
            )
            if state == "waiting_approval":
                db.execute(
                    insert(tables.approvals).values(
                        run_id=run_id, call_id=call_id, fingerprint=digest, decision="pending"
                    )
                )
            elif auto_approval is not None and not rejected:
                decided_at = self.clock()
                db.execute(
                    insert(tables.approvals).values(
                        run_id=run_id,
                        call_id=call_id,
                        fingerprint=digest,
                        decision="approved",
                        decided_by=auto_approval["decided_by"],
                        decided_at=decided_at,
                    )
                )
                self._event(
                    db,
                    run_id,
                    "approval_decided",
                    {
                        "call_id": call_id,
                        "decision": "approved",
                        "automatic": True,
                        "policy": {
                            key: value
                            for key, value in auto_approval.items()
                            if key != "decided_by"
                        },
                    },
                )
            elif result is not None:
                self._event(
                    db, run_id, "tool_result", {"call_id": call_id, "status": state, **result}
                )
            return dict(db.execute(select(tables.tool_calls).where(key)).mappings().one())

    def decide(
        self, run_id: str, actor_id: str, call_id: str, expected_fingerprint: str, approved: bool
    ) -> dict[str, Any]:
        with self.sessions.begin() as db:
            self._access(db, run_id, actor_id)
            row = (
                db.execute(
                    update(tables.runs)
                    .where(tables.runs.c.id == run_id)
                    .values(event_seq=tables.runs.c.event_seq)
                    .returning(tables.runs)
                )
                .mappings()
                .one()
            )
            key = and_(tables.approvals.c.run_id == run_id, tables.approvals.c.call_id == call_id)
            approval = db.execute(select(tables.approvals).where(key)).mappings().first()
            if approval is None:
                raise RunNotFoundError("Approval not found")
            if approval["fingerprint"] != expected_fingerprint:
                raise RunConflictError("Approval target version does not match")
            decision = "approved" if approved else "denied"
            if approval["decision"] == decision:
                return dict(approval)
            if approval["decision"] != "pending" or row["status"] != "waiting_approval":
                raise RunConflictError("Approval is no longer pending")
            db.execute(
                update(tables.approvals)
                .where(key)
                .values(decision=decision, decided_by=actor_id, decided_at=self.clock())
            )
            self._event(db, run_id, "approval_decided", {"call_id": call_id, "decision": decision})
            return dict(db.execute(select(tables.approvals).where(key)).mappings().one())

    def approval_decisions(self, run_id: str, owner_id: str) -> dict[str, bool]:
        with self.sessions.begin() as db:
            self._owned(db, run_id, owner_id)
            rows = list(
                db.execute(
                    select(tables.approvals).where(tables.approvals.c.run_id == run_id)
                ).mappings()
            )
            if any(row["decision"] == "pending" for row in rows):
                raise RunConflictError("The approval batch is not resolved")
            return {row["call_id"]: row["decision"] == "approved" for row in rows}

    def claim_call(
        self, run_id: str, owner_id: str, call_id: str, *, approved_by_framework: bool
    ) -> dict[str, Any]:
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            if row["cancel_requested"]:
                raise RunConflictError("Cancellation requested")
            key = and_(tables.tool_calls.c.run_id == run_id, tables.tool_calls.c.call_id == call_id)
            call = dict(db.execute(select(tables.tool_calls).where(key)).mappings().one())
            if call["status"] in {"succeeded", "failed", "denied"}:
                return call
            if call["status"] in {"executing", "outcome_unknown"}:
                raise OutcomeUnknownError(
                    "This call was already dispatched; its outcome must be reconciled"
                )
            if call["status"] == "waiting_approval":
                approval = (
                    db.execute(
                        select(tables.approvals).where(
                            tables.approvals.c.run_id == run_id,
                            tables.approvals.c.call_id == call_id,
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    not approved_by_framework
                    or approval["decision"] != "approved"
                    or approval["fingerprint"] != call["fingerprint"]
                ):
                    raise RunConflictError("Call has no matching approval")
            if call["resource_key"]:
                try:
                    with db.begin_nested():
                        db.execute(
                            insert(tables.resource_locks).values(
                                resource_key=call["resource_key"], run_id=run_id, call_id=call_id
                            )
                        )
                except IntegrityError as exc:
                    raise ResourceBusyError(
                        "Resource is executing or has an unresolved outcome"
                    ) from exc
            db.execute(update(tables.tool_calls).where(key).values(status="executing"))
            self._event(
                db,
                run_id,
                "tool_start",
                {
                    "call_id": call_id,
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "target": call["target"],
                    "fingerprint": call["fingerprint"],
                },
            )
            return {**call, "status": "executing"}

    def finish_call(
        self,
        run_id: str,
        owner_id: str,
        call_id: str,
        result: dict[str, Any],
        *,
        unknown: bool = False,
    ) -> None:
        with self.sessions.begin() as db:
            self._owned(db, run_id, owner_id)
            key = and_(tables.tool_calls.c.run_id == run_id, tables.tool_calls.c.call_id == call_id)
            call = db.execute(select(tables.tool_calls).where(key)).mappings().first()
            if call is None:
                raise RunNotFoundError("Tool call not found")
            if call["status"] == "outcome_unknown":
                raise OutcomeUnknownError(
                    "Only explicit reconciliation may resolve an unknown outcome"
                )
            if call["status"] in {"succeeded", "failed", "denied"}:
                return
            state = (
                "outcome_unknown"
                if unknown
                else {"success": "succeeded", "failed": "failed", "denied": "denied"}[
                    result["outcome"]
                ]
            )
            db.execute(update(tables.tool_calls).where(key).values(status=state, result=result))
            if not unknown:
                db.execute(
                    delete(tables.resource_locks).where(
                        tables.resource_locks.c.run_id == run_id,
                        tables.resource_locks.c.call_id == call_id,
                    )
                )
            self._event(db, run_id, "tool_result", {"call_id": call_id, "status": state, **result})
