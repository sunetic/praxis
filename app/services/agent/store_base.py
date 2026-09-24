"""Shared transaction primitives, errors, and serialization for run persistence.

No session escapes a method. Database writes, not Python locks, serialize workers.
This module never executes tools and never interprets whether a user goal is met.
"""

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import TypeAdapter
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from sqlalchemy import and_, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import agent_runs as tables
from app.services.agent.definitions import AgentDefinition
from app.services.agent.runtime import ExecutionBudget

TERMINAL_STATUSES = frozenset({"finished", "cancelled", "failed", "limited", "interrupted"})
BUDGET_ADAPTER = TypeAdapter(ExecutionBudget)
DEFERRED_ADAPTER = TypeAdapter(DeferredToolRequests)


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class LeaseLostError(RunConflictError):
    pass


class OutcomeUnknownError(RunConflictError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        # Only adapters may supply public, already-sanitized evidence here.
        self.details = details


class CallChangedError(RunConflictError):
    pass


class ResourceBusyError(RunConflictError):
    pass


def fingerprint(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def definition_json(definition: AgentDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "tool_names": sorted(definition.tool_names),
        "instructions": definition.instructions,
        "scope": definition.scope,
    }


class StoreBase:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = 30,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.sessions = sessions
        self.clock = clock
        self.lease_seconds = lease_seconds

    def _access(self, db: Session, run_id: str, actor_id: str | None = None) -> dict[str, Any]:
        query = select(tables.runs).where(tables.runs.c.id == run_id)
        if actor_id is not None:
            query = query.where(tables.runs.c.actor_id == actor_id)
        row = db.execute(query).mappings().first()
        if row is None:
            raise RunNotFoundError("Run not found")
        return dict(row)

    def _owned(self, db: Session, run_id: str, owner_id: str) -> dict[str, Any]:
        initial = self._access(db, run_id)
        db.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == initial["conversation_id"])
            .values(next_run_seq=tables.conversations.c.next_run_seq)
        )
        row = (
            db.execute(
                update(tables.runs)
                .where(
                    tables.runs.c.id == run_id,
                    tables.runs.c.status == "running",
                    tables.runs.c.owner_id == owner_id,
                    tables.runs.c.lease_until > self.clock(),
                )
                .values(owner_id=owner_id)
                .returning(tables.runs)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LeaseLostError("Run execution lease is no longer owned")
        return dict(row)

    def _event(self, db: Session, run_id: str, kind: str, payload: dict[str, Any]) -> int:
        seq = db.execute(
            update(tables.runs)
            .where(tables.runs.c.id == run_id)
            .values(
                event_seq=tables.runs.c.event_seq + 1,
            )
            .returning(tables.runs.c.event_seq)
        ).scalar_one()
        db.execute(
            insert(tables.events).values(
                run_id=run_id, seq=seq, kind=kind, payload=payload, created_at=self.clock()
            )
        )
        return int(seq)

    def _locked_access(self, db: Session, run_id: str, actor_id: str) -> dict[str, Any]:
        initial = self._access(db, run_id, actor_id)
        db.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == initial["conversation_id"])
            .values(next_run_seq=tables.conversations.c.next_run_seq)
        )
        return dict(
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(event_seq=tables.runs.c.event_seq)
                .returning(tables.runs)
            )
            .mappings()
            .one()
        )

    def _history(self, db: Session, conversation_id: str, through_seq: int) -> list[ModelMessage]:
        payloads = list(
            db.scalars(
                select(tables.messages.c.payload)
                .join(tables.runs, tables.messages.c.run_id == tables.runs.c.id)
                .where(
                    tables.runs.c.conversation_id == conversation_id,
                    tables.runs.c.seq <= through_seq,
                )
                .order_by(tables.runs.c.seq, tables.messages.c.index)
            )
        )
        return ModelMessagesTypeAdapter.validate_python(payloads)

    def _save_messages(
        self, db: Session, row: dict[str, Any], history: Sequence[ModelMessage]
    ) -> None:
        payloads = ModelMessagesTypeAdapter.dump_python(list(history), mode="json")
        offset = row["message_offset"]
        if offset is None or len(payloads) < offset:
            raise RunConflictError("Native history does not match the run's message boundary")
        for index, payload in enumerate(payloads[offset:]):
            key = and_(tables.messages.c.run_id == row["id"], tables.messages.c.index == index)
            existing = db.execute(select(tables.messages).where(key)).mappings().first()
            if existing is None:
                db.execute(
                    insert(tables.messages).values(run_id=row["id"], index=index, payload=payload)
                )
            elif len(payload["parts"]) >= len(existing["payload"]["parts"]):
                db.execute(update(tables.messages).where(key).values(payload=payload))

    def _cancel_unused_approvals(self, db: Session, run_id: str) -> None:
        unused = select(tables.tool_calls.c.call_id).where(
            tables.tool_calls.c.run_id == run_id,
            tables.tool_calls.c.status.in_(["ready", "waiting_approval"]),
        )
        db.execute(
            update(tables.approvals)
            .where(
                tables.approvals.c.run_id == run_id,
                tables.approvals.c.call_id.in_(unused),
                tables.approvals.c.decision.in_(["pending", "approved"]),
            )
            .values(decision="cancelled")
        )

    def _cancel_idle(self, db: Session, row: dict[str, Any]) -> None:
        self._cancel_unused_approvals(db, row["id"])
        if row["status"] == "waiting_approval":
            self._settle_unresolved(db, row)
        db.execute(
            update(tables.runs)
            .where(tables.runs.c.id == row["id"])
            .values(status="cancelled", cancel_requested=True, pending=None)
        )
        db.execute(
            update(tables.conversations)
            .where(
                tables.conversations.c.id == row["conversation_id"],
                tables.conversations.c.active_run_id == row["id"],
            )
            .values(active_run_id=None)
        )
        self._event(db, row["id"], "run_cancelled", {"status": "cancelled"})

    def _settle_unresolved(self, db: Session, row: dict[str, Any]) -> None:
        """Close original calls with execution facts on cancellation/failure, never replay.

        This repairs the message protocol from recorded outcomes, not from a task
        summary. An unknown dispatched write remains unknown and retains its lock.
        """
        history = self._history(db, row["conversation_id"], row["seq"])
        current_messages = history[row["message_offset"] :]
        calls = {
            part.tool_call_id: part
            for message in current_messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        answered = {
            part.tool_call_id
            for message in current_messages
            for part in message.parts
            if isinstance(part, (ToolReturnPart, RetryPromptPart))
        }
        results = []
        for call_id, part in calls.items():
            if call_id in answered:
                continue
            key = and_(
                tables.tool_calls.c.run_id == row["id"], tables.tool_calls.c.call_id == call_id
            )
            record = db.execute(select(tables.tool_calls).where(key)).mappings().first()
            outcome: Literal["success", "failed", "denied", "interrupted"] = "denied"
            content: Any = "Run stopped before this action was dispatched."
            if record and record["status"] in {"executing", "outcome_unknown"}:
                outcome, content = (
                    "interrupted",
                    "This operation was dispatched, but its outcome is unknown. Reconcile it before retrying.",
                )
                db.execute(update(tables.tool_calls).where(key).values(status="outcome_unknown"))
            elif record and record["result"]:
                outcome, content = record["result"]["outcome"], record["result"]["content"]
            elif record:
                db.execute(
                    update(tables.tool_calls)
                    .where(key)
                    .values(status="denied", result={"outcome": outcome, "content": content})
                )
            results.append(
                ToolReturnPart(
                    tool_name=part.tool_name, tool_call_id=call_id, content=content, outcome=outcome
                )
            )
            self._event(
                db,
                row["id"],
                "tool_result",
                {"call_id": call_id, "outcome": outcome, "content": content},
            )
        if results:
            self._save_messages(db, row, [*history, ModelRequest(parts=results)])
