"""Conversation, submission, and event persistence operations."""

import json
import uuid
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    UserPromptPart,
)
from sqlalchemy import and_, delete, insert, select, update

from app.models import agent_runs as tables
from app.services.agent.definitions import AgentDefinition
from app.services.agent.runtime import ExecutionBudget
from app.services.agent.store_base import (
    BUDGET_ADAPTER,
    TERMINAL_STATUSES,
    RunConflictError,
    RunNotFoundError,
    StoreBase,
    definition_json,
    fingerprint,
)


class ConversationStoreMixin(StoreBase):
    def create_conversation(
        self,
        conversation_id: str,
        actor_id: str,
        *,
        title: str = "New conversation",
        scene: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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

    def get_conversation(self, conversation_id: str, actor_id: str) -> dict[str, Any]:
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

    def list_conversations(self, actor_id: str) -> list[dict[str, Any]]:
        with self.sessions() as db:
            return [
                dict(row)
                for row in db.execute(
                    select(tables.conversations)
                    .where(tables.conversations.c.actor_id == actor_id)
                    .order_by(tables.conversations.c.created_at.desc())
                ).mappings()
            ]

    def update_conversation(
        self, conversation_id: str, actor_id: str, *, scene: dict[str, Any]
    ) -> dict[str, Any]:
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

    def list_runs(self, conversation_id: str, actor_id: str) -> list[dict[str, Any]]:
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

    def submit(
        self,
        conversation_id: str,
        actor_id: str,
        client_request_id: str,
        prompt: str,
        definition: AgentDefinition,
        budget: ExecutionBudget | None = None,
        stop_and_modify: bool = False,
        model_snapshot_factory: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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

    def get(self, run_id: str, actor_id: str) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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
                return dict(previous)
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

    def resume(self, run_id: str, actor_id: str, expected_event_seq: int) -> dict[str, Any]:
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

    def history(self, run_id: str, actor_id: str) -> list[ModelMessage]:
        with self.sessions() as db:
            row = self._access(db, run_id, actor_id)
            return self._history(db, row["conversation_id"], row["seq"])

    def read_events(
        self, run_id: str, actor_id: str, after: int = 0, limit: int = 256
    ) -> list[dict[str, Any]]:
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
