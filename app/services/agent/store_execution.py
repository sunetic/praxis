"""Run ownership, checkpoint, completion, and recovery operations."""

import uuid
from collections.abc import Sequence
from typing import Any

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
)
from sqlalchemy import insert, select, update

from app.models import agent_runs as tables
from app.services.agent.runtime import ExecutionBudget
from app.services.agent.store_base import (
    BUDGET_ADAPTER,
    DEFERRED_ADAPTER,
    TERMINAL_STATUSES,
    StoreBase,
)


class ExecutionStoreMixin(StoreBase):
    def claim(self, run_id: str, owner_id: str) -> dict[str, Any] | None:
        with self.sessions.begin() as db:
            initial = self._access(db, run_id)
            # Lock ordering: conversation, run. All workers use the same order.
            conversation = (
                db.execute(
                    update(tables.conversations)
                    .where(tables.conversations.c.id == initial["conversation_id"])
                    .values(next_run_seq=tables.conversations.c.next_run_seq)
                    .returning(tables.conversations)
                )
                .mappings()
                .one()
            )
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
            if row["status"] not in {"queued", "waiting_approval"} or row["cancel_requested"]:
                return None
            if conversation["active_run_id"] not in {None, run_id}:
                return None
            earlier = db.scalar(
                select(tables.runs.c.id)
                .where(
                    tables.runs.c.conversation_id == row["conversation_id"],
                    tables.runs.c.seq < row["seq"],
                    tables.runs.c.status.in_(
                        ["queued", "running", "waiting_approval", "interrupted"]
                    ),
                )
                .limit(1)
            )
            if earlier is not None:
                return None
            if row["status"] == "waiting_approval":
                undecided = db.scalar(
                    select(tables.approvals.c.call_id)
                    .where(
                        tables.approvals.c.run_id == run_id,
                        tables.approvals.c.decision == "pending",
                    )
                    .limit(1)
                )
                if undecided is not None:
                    return None
            offset = row["message_offset"]
            if offset is None:
                offset = len(self._history(db, row["conversation_id"], row["seq"] - 1))
            db.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == row["conversation_id"])
                .values(active_run_id=run_id)
            )
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(
                    status="running",
                    owner_id=owner_id,
                    lease_until=self.clock() + self.lease_seconds,
                    message_offset=offset,
                )
            )
            self._event(db, run_id, "run_started", {})
            return self._access(db, run_id)

    def heartbeat(self, run_id: str, owner_id: str) -> bool:
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(lease_until=self.clock() + self.lease_seconds)
            )
            return bool(row["cancel_requested"])

    def checkpoint(
        self, run_id: str, owner_id: str, history: Sequence[ModelMessage], budget: ExecutionBudget
    ) -> None:
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            self._save_messages(db, row, history)
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(budget=BUDGET_ADAPTER.dump_python(budget, mode="json"))
            )

    def emit(self, run_id: str, owner_id: str, kind: str, payload: dict[str, Any]) -> int:
        with self.sessions.begin() as db:
            self._owned(db, run_id, owner_id)
            return self._event(db, run_id, kind, payload)

    def context_snapshot(
        self, run_id: str, owner_id: str, source_fingerprint: str | None = None
    ) -> dict[str, Any] | None:
        with self.sessions() as db:
            row = self._owned(db, run_id, owner_id)
            query = select(tables.context_snapshots).where(
                tables.context_snapshots.c.conversation_id == row["conversation_id"]
            )
            if source_fingerprint is not None:
                query = query.where(
                    tables.context_snapshots.c.source_fingerprint == source_fingerprint
                )
            snapshot = (
                db.execute(query.order_by(tables.context_snapshots.c.created_at.desc()).limit(1))
                .mappings()
                .first()
            )
            return dict(snapshot) if snapshot else None

    def save_context_snapshot(
        self,
        run_id: str,
        owner_id: str,
        *,
        source_indices: list[int],
        source_fingerprint: str,
        summary: str,
        references: list[int],
        model_name: str,
        prompt_version: str,
    ) -> str:
        snapshot_id = uuid.uuid4().hex
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            db.execute(
                insert(tables.context_snapshots).values(
                    id=snapshot_id,
                    conversation_id=row["conversation_id"],
                    run_id=run_id,
                    source_indices=source_indices,
                    source_fingerprint=source_fingerprint,
                    summary=summary,
                    references=references,
                    model_name=model_name,
                    prompt_version=prompt_version,
                    created_at=self.clock(),
                )
            )
        return snapshot_id

    def finish(
        self,
        run_id: str,
        owner_id: str,
        *,
        status: str,
        history: Sequence[ModelMessage],
        budget: ExecutionBudget,
        output: str | None = None,
        pending: DeferredToolRequests | None = None,
        error_code: str | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES | {"waiting_approval"}:
            raise ValueError("Invalid final execution state")
        with self.sessions.begin() as db:
            row = self._owned(db, run_id, owner_id)
            self._save_messages(db, row, history)
            if row["cancel_requested"]:
                status = "cancelled"
                pending = None
            if status != "waiting_approval":
                self._cancel_unused_approvals(db, run_id)
                self._settle_unresolved(db, row)
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(
                    status=status,
                    owner_id=None,
                    lease_until=None,
                    budget=BUDGET_ADAPTER.dump_python(budget, mode="json"),
                    pending=DEFERRED_ADAPTER.dump_python(pending, mode="json") if pending else None,
                    output=output,
                    error_code=error_code,
                )
            )
            if status == "waiting_approval":
                if pending is None:
                    raise ValueError(
                        "Pending tool requests are required while waiting for approval"
                    )
                for call in pending.approvals:
                    approval = (
                        db.execute(
                            select(tables.approvals).where(
                                tables.approvals.c.run_id == run_id,
                                tables.approvals.c.call_id == call.tool_call_id,
                            )
                        )
                        .mappings()
                        .one()
                    )
                    recorded = (
                        db.execute(
                            select(tables.tool_calls).where(
                                tables.tool_calls.c.run_id == run_id,
                                tables.tool_calls.c.call_id == call.tool_call_id,
                            )
                        )
                        .mappings()
                        .one()
                    )
                    self._event(
                        db,
                        run_id,
                        "approval_required",
                        {
                            "call_id": call.tool_call_id,
                            "fingerprint": approval["fingerprint"],
                            "name": recorded["name"],
                            "arguments": recorded["arguments"],
                            "target": recorded["target"],
                        },
                    )
                self._event(db, run_id, "run_paused", {"status": status})
            else:
                db.execute(
                    update(tables.conversations)
                    .where(
                        tables.conversations.c.id == row["conversation_id"],
                        tables.conversations.c.active_run_id == run_id,
                    )
                    .values(active_run_id=None)
                )
                self._event(
                    db,
                    run_id,
                    "run_finished" if status == "finished" else f"run_{status}",
                    {"status": status, "error_code": error_code},
                )

    def cancel(self, run_id: str, actor_id: str) -> dict[str, Any]:
        with self.sessions.begin() as db:
            initial = self._access(db, run_id, actor_id)
            db.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == initial["conversation_id"])
                .values(next_run_seq=tables.conversations.c.next_run_seq)
            )
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
            if row["status"] in TERMINAL_STATUSES:
                return dict(row)
            if row["status"] == "running":
                db.execute(
                    update(tables.runs)
                    .where(tables.runs.c.id == run_id)
                    .values(cancel_requested=True)
                )
            else:
                self._cancel_idle(db, dict(row))
            return self._access(db, run_id)

    def recover_expired(self) -> list[str]:
        recovered = []
        with self.sessions.begin() as db:
            expired = list(
                db.scalars(
                    select(tables.runs.c.id).where(
                        tables.runs.c.status == "running", tables.runs.c.lease_until <= self.clock()
                    )
                )
            )
            for run_id in expired:
                row = (
                    db.execute(
                        update(tables.runs)
                        .where(
                            tables.runs.c.id == run_id,
                            tables.runs.c.status == "running",
                            tables.runs.c.lease_until <= self.clock(),
                        )
                        .values(
                            status="interrupted",
                            owner_id=None,
                            lease_until=None,
                            error_code="execution_owner_expired",
                        )
                        .returning(tables.runs)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    continue
                unknown_calls = (
                    db.execute(
                        update(tables.tool_calls)
                        .where(
                            tables.tool_calls.c.run_id == run_id,
                            tables.tool_calls.c.status == "executing",
                        )
                        .values(status="outcome_unknown")
                        .returning(tables.tool_calls)
                    )
                    .mappings()
                    .all()
                )
                for call in unknown_calls:
                    self._event(
                        db,
                        run_id,
                        "tool_result",
                        {
                            "call_id": call["call_id"],
                            "status": "outcome_unknown",
                            "fingerprint": call["fingerprint"],
                            "outcome": "interrupted",
                            "content": "Execution stopped without a recorded result. External reconciliation is required.",
                        },
                    )
                # Keep the conversation/resource fences. No implicit crash replay.
                self._event(
                    db,
                    run_id,
                    "run_interrupted",
                    {"status": "interrupted", "error_code": "execution_owner_expired"},
                )
                recovered.append(run_id)
        return recovered
