"""Short database transactions for run ownership, native history and tool dispatch.

No session escapes a method. Database writes, not Python locks, serialize workers.
This module never executes tools and never interprets whether a user goal is met.
"""

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
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
    def __init__(self, message: str, *, details: dict | None = None):
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


class RunStore:
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

    def create_conversation(
        self,
        conversation_id: str,
        actor_id: str,
        *,
        title: str = "New conversation",
        scene: dict | None = None,
    ) -> dict:
        with self.sessions.begin() as db:
            db.execute(
                insert(tables.conversations).values(
                    id=conversation_id,
                    actor_id=actor_id,
                    title=title,
                    scene=scene or {},
                    created_at=self.clock(),
                )
            )
        return self.get_conversation(conversation_id, actor_id)

    def get_conversation(self, conversation_id: str, actor_id: str) -> dict:
        with self.sessions() as db:
            row = (
                db.execute(
                    select(tables.conversations).where(
                        tables.conversations.c.id == conversation_id,
                        tables.conversations.c.actor_id == actor_id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RunNotFoundError("Conversation not found")
            return dict(row)

    def list_conversations(self, actor_id: str) -> list[dict]:
        with self.sessions() as db:
            return [
                dict(row)
                for row in db.execute(
                    select(tables.conversations)
                    .where(tables.conversations.c.actor_id == actor_id)
                    .order_by(tables.conversations.c.created_at.desc())
                ).mappings()
            ]

    def update_conversation(self, conversation_id: str, actor_id: str, *, scene: dict) -> dict:
        """Change the default for future submissions, never an existing run's scope."""
        with self.sessions.begin() as db:
            row = (
                db.execute(
                    update(tables.conversations)
                    .where(
                        tables.conversations.c.id == conversation_id,
                        tables.conversations.c.actor_id == actor_id,
                    )
                    .values(scene=scene)
                    .returning(tables.conversations)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RunNotFoundError("Conversation not found")
            return dict(row)

    def list_runs(self, conversation_id: str, actor_id: str) -> list[dict]:
        self.get_conversation(conversation_id, actor_id)
        with self.sessions() as db:
            return [
                dict(row)
                for row in db.execute(
                    select(tables.runs)
                    .where(
                        tables.runs.c.conversation_id == conversation_id,
                        tables.runs.c.actor_id == actor_id,
                    )
                    .order_by(tables.runs.c.seq)
                ).mappings()
            ]

    def _access(self, db: Session, run_id: str, actor_id: str | None = None) -> dict:
        query = select(tables.runs).where(tables.runs.c.id == run_id)
        if actor_id is not None:
            query = query.where(tables.runs.c.actor_id == actor_id)
        row = db.execute(query).mappings().first()
        if row is None:
            raise RunNotFoundError("Run not found")
        return dict(row)

    def _owned(self, db: Session, run_id: str, owner_id: str) -> dict:
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

    def _event(self, db: Session, run_id: str, kind: str, payload: dict) -> int:
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
        return seq

    def submit(
        self,
        conversation_id: str,
        actor_id: str,
        client_request_id: str,
        prompt: str,
        definition: AgentDefinition,
        budget: ExecutionBudget | None = None,
        stop_and_modify: bool = False,
        model_snapshot_factory: Callable[[], dict] | None = None,
    ) -> dict:
        if not client_request_id or not prompt.strip():
            raise ValueError("client_request_id and prompt are required")
        config = definition_json(definition)
        budget_json = BUDGET_ADAPTER.dump_python(budget or ExecutionBudget(), mode="json")
        digest = fingerprint(
            {
                "prompt": prompt,
                "definition": config,
                "budget": budget_json,
                "stop_and_modify": stop_and_modify,
            }
        )
        # Resolve model settings without holding the run-creation transaction:
        # the platform settings reader has its own short-lived session. Holding
        # a connection here while it acquires another can exhaust a small pool.
        # A second check under the conversation lock below handles racing posts.
        with self.sessions() as db:
            existing = (
                db.execute(
                    select(tables.runs)
                    .join(
                        tables.conversations,
                        tables.runs.c.conversation_id == tables.conversations.c.id,
                    )
                    .where(
                        tables.runs.c.conversation_id == conversation_id,
                        tables.runs.c.client_request_id == client_request_id,
                        tables.conversations.c.actor_id == actor_id,
                    )
                )
                .mappings()
                .first()
            )
        if existing:
            if existing["input_fingerprint"] != digest:
                raise RunConflictError("Client request ID was already used for different input")
            return dict(existing)
        model_snapshot = model_snapshot_factory() if model_snapshot_factory else None
        with self.sessions.begin() as db:
            # Lock the conversation before checking the idempotency key or assigning a sequence.
            conversation = (
                db.execute(
                    update(tables.conversations)
                    .where(
                        tables.conversations.c.id == conversation_id,
                        tables.conversations.c.actor_id == actor_id,
                    )
                    .values(next_run_seq=tables.conversations.c.next_run_seq)
                    .returning(tables.conversations)
                )
                .mappings()
                .first()
            )
            if conversation is None:
                raise RunNotFoundError("Conversation not found")
            existing = (
                db.execute(
                    select(tables.runs).where(
                        tables.runs.c.conversation_id == conversation_id,
                        tables.runs.c.client_request_id == client_request_id,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["input_fingerprint"] != digest:
                    raise RunConflictError("Client request ID was already used for different input")
                return dict(existing)
            if conversation["active_run_id"]:
                active = self._access(db, conversation["active_run_id"])
                if active["status"] == "waiting_approval":
                    self._cancel_idle(db, active)
                elif stop_and_modify and active["status"] == "running":
                    db.execute(
                        update(tables.runs)
                        .where(tables.runs.c.id == active["id"])
                        .values(cancel_requested=True)
                    )
            run_id = uuid.uuid4().hex
            seq = conversation["next_run_seq"] + 1
            db.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == conversation_id)
                .values(next_run_seq=seq)
            )
            db.execute(
                insert(tables.runs).values(
                    id=run_id,
                    conversation_id=conversation_id,
                    actor_id=actor_id,
                    seq=seq,
                    client_request_id=client_request_id,
                    input_fingerprint=digest,
                    prompt=prompt,
                    definition=config,
                    model_snapshot=model_snapshot,
                    status="queued",
                    budget=budget_json,
                    created_at=self.clock(),
                )
            )
            self._event(db, run_id, "run_queued", {})
            return self._access(db, run_id)

    def get(self, run_id: str, actor_id: str) -> dict:
        with self.sessions() as db:
            row = self._access(db, run_id, actor_id)
            row["tool_calls"] = [
                dict(item)
                for item in db.execute(
                    select(tables.tool_calls).where(tables.tool_calls.c.run_id == run_id)
                ).mappings()
            ]
            row["approvals"] = [
                dict(item)
                for item in db.execute(
                    select(tables.approvals).where(tables.approvals.c.run_id == run_id)
                ).mappings()
            ]
            return row

    def candidates(self) -> list[str]:
        with self.sessions() as db:
            return list(
                db.scalars(
                    select(tables.runs.c.id)
                    .where(tables.runs.c.status.in_(["queued", "waiting_approval"]))
                    .order_by(tables.runs.c.created_at, tables.runs.c.seq)
                )
            )

    def _locked_access(self, db: Session, run_id: str, actor_id: str) -> dict:
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

    def reconcile(
        self,
        run_id: str,
        actor_id: str,
        call_id: str,
        digest: str,
        *,
        resolution: str,
        evidence: str,
        execution_stopped: bool,
    ) -> dict:
        """Record an owner's external check, not a tool execution or platform verification."""
        if resolution not in {"succeeded", "failed", "not_executed"}:
            raise ValueError("Invalid reconciliation result")
        if execution_stopped is not True or not evidence.strip() or len(evidence) > 10000:
            raise ValueError("Confirm external execution has ended and supply evidence")
        submitted = dict(
            resolution=resolution,
            evidence=evidence.strip(),
            execution_stopped=True,
            reported_by=actor_id,
            fingerprint=digest,
        )
        with self.sessions.begin() as db:
            row = self._locked_access(db, run_id, actor_id)
            key = and_(tables.tool_calls.c.run_id == run_id, tables.tool_calls.c.call_id == call_id)
            call = db.execute(select(tables.tool_calls).where(key)).mappings().first()
            if call is None:
                raise RunNotFoundError("Tool call not found")
            if digest != call["fingerprint"]:
                raise RunConflictError("The recorded action does not match this reconciliation")
            previous = (call["result"] or {}).get("reconciliation")
            if previous:
                if any(previous.get(key) != value for key, value in submitted.items()):
                    raise RunConflictError("A different reconciliation is already recorded")
                return previous
            if row["status"] not in TERMINAL_STATUSES or call["status"] != "outcome_unknown":
                raise RunConflictError("Only a stopped run's unknown call can be reconciled")
            receipt = {
                **submitted,
                "reported_at": self.clock(),
                "source": "user_reported",
                "verified_by_platform": False,
            }
            result = {
                "outcome": {"succeeded": "success", "failed": "failed", "not_executed": "denied"}[
                    resolution
                ],
                "content": {"reconciliation": receipt},
                "reconciliation": receipt,
            }
            status = "denied" if resolution == "not_executed" else resolution
            db.execute(update(tables.tool_calls).where(key).values(status=status, result=result))
            db.execute(
                delete(tables.resource_locks).where(
                    tables.resource_locks.c.run_id == run_id,
                    tables.resource_locks.c.call_id == call_id,
                )
            )
            self._event(
                db,
                run_id,
                "tool_reconciled",
                {
                    "call_id": call_id,
                    "status": status,
                    "fingerprint": digest,
                    **result,
                },
            )
            return receipt

    def resume(self, run_id: str, actor_id: str, expected_event_seq: int) -> dict:
        """Explicit continuation from recorded results. Never redispatch the interrupted batch."""
        with self.sessions.begin() as db:
            row = self._locked_access(db, run_id, actor_id)
            resumed = list(
                db.scalars(
                    select(tables.events.c.payload).where(
                        tables.events.c.run_id == run_id,
                        tables.events.c.kind == "run_resumed",
                    )
                )
            )
            if any(event["expected_event_seq"] == expected_event_seq for event in resumed):
                return row
            if row["event_seq"] != expected_event_seq:
                raise RunConflictError("Run changed; refresh before resuming")
            if row["status"] != "interrupted" or row["cancel_requested"]:
                raise RunConflictError("Only an interrupted, uncancelled run can resume")
            calls = list(
                db.execute(
                    select(tables.tool_calls).where(
                        tables.tool_calls.c.run_id == run_id,
                    )
                ).mappings()
            )
            if any(call["status"] in {"executing", "outcome_unknown"} for call in calls):
                raise RunConflictError("Reconcile every unknown call before resuming")
            self._cancel_unused_approvals(db, run_id)
            if row["message_offset"] is not None:
                self._settle_unresolved(db, row)
                receipts = [
                    {"call_id": call["call_id"], **call["result"]["reconciliation"]}
                    for call in calls
                    if (call["result"] or {}).get("reconciliation")
                ]
                if receipts:
                    history = self._history(db, row["conversation_id"], row["seq"])
                    self._save_messages(
                        db,
                        row,
                        [
                            *history,
                            ModelRequest(
                                parts=[
                                    UserPromptPart(
                                        "User-reported external reconciliation (not independently verified by "
                                        "the platform):\n"
                                        + json.dumps(receipts, ensure_ascii=False)
                                    )
                                ]
                            ),
                        ],
                    )
            db.execute(
                update(tables.runs)
                .where(tables.runs.c.id == run_id)
                .values(
                    status="queued",
                    pending=None,
                    owner_id=None,
                    lease_until=None,
                    error_code=None,
                    output=None,
                )
            )
            self._event(
                db,
                run_id,
                "run_resumed",
                {
                    "status": "queued",
                    "expected_event_seq": expected_event_seq,
                },
            )
            return self._access(db, run_id)

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

    def history(self, run_id: str, actor_id: str) -> list[ModelMessage]:
        with self.sessions() as db:
            row = self._access(db, run_id, actor_id)
            return self._history(db, row["conversation_id"], row["seq"])

    def claim(self, run_id: str, owner_id: str) -> dict | None:
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
            return row["cancel_requested"]

    def _save_messages(self, db: Session, row: dict, history: Sequence[ModelMessage]) -> None:
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

    def emit(self, run_id: str, owner_id: str, kind: str, payload: dict) -> int:
        with self.sessions.begin() as db:
            self._owned(db, run_id, owner_id)
            return self._event(db, run_id, kind, payload)

    def context_snapshot(
        self, run_id: str, owner_id: str, source_fingerprint: str | None = None
    ) -> dict | None:
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

    def read_events(
        self, run_id: str, actor_id: str, after: int = 0, limit: int = 256
    ) -> list[dict]:
        with self.sessions() as db:
            self._access(db, run_id, actor_id)
            return [
                dict(row)
                for row in db.execute(
                    select(tables.events)
                    .where(tables.events.c.run_id == run_id, tables.events.c.seq > after)
                    .order_by(tables.events.c.seq)
                    .limit(limit)
                ).mappings()
            ]

    def prepare_call(
        self,
        run_id: str,
        owner_id: str,
        *,
        call_id: str,
        name: str,
        arguments: dict,
        target: dict,
        mutating: bool,
        resource_key: str | None,
        needs_approval: bool,
        history: Sequence[ModelMessage],
        auto_approval: dict | None = None,
    ) -> dict:
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
    ) -> dict:
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
    ) -> dict:
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
        self, run_id: str, owner_id: str, call_id: str, result: dict, *, unknown: bool = False
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

    def cancel(self, run_id: str, actor_id: str) -> dict:
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

    def _cancel_idle(self, db: Session, row: dict) -> None:
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

    def _settle_unresolved(self, db: Session, row: dict) -> None:
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
            outcome, content = "denied", "Run stopped before this action was dispatched."
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
