"""Explicit recovery records external evidence; it never replays a dispatched action."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import select
from test_runtime import Script
from test_service import settled

from app.models import agent_runs as tables
from app.services.agent.definitions import AgentDefinition
from app.services.agent.service import AgentRunService
from app.services.agent.store import RunConflictError, RunNotFoundError


def interrupted(store, count=1):
    clock = [100.0]
    store.clock = lambda: clock[0]
    row = store.submit(
        "conversation",
        "user",
        "original",
        "Do the authorized work",
        AgentDefinition(name="test", tool_names=frozenset()),
    )
    store.claim(row["id"], "dead-worker")
    history = [
        ModelRequest(parts=[UserPromptPart(row["prompt"])]),
        ModelResponse(
            parts=[
                ToolCallPart("write", {"row": i}, tool_call_id=f"call-{i}") for i in range(count)
            ]
        ),
    ]
    for i in range(count):
        store.prepare_call(
            row["id"],
            "dead-worker",
            call_id=f"call-{i}",
            name="write",
            arguments={"row": i},
            target={"table": "isolated"},
            mutating=True,
            resource_key=f"resource-{i}",
            needs_approval=False,
            history=history,
        )
        store.claim_call(row["id"], "dead-worker", f"call-{i}", approved_by_framework=False)
    clock[0] += store.lease_seconds + 1
    assert store.recover_expired() == [row["id"]]
    return store.get(row["id"], "user")


def reconcile(store, row, i=0, resolution="succeeded", **overrides):
    call = next(call for call in row["tool_calls"] if call["call_id"] == f"call-{i}")
    return store.reconcile(
        row["id"],
        "user",
        call["call_id"],
        call["fingerprint"],
        **{
            "resolution": resolution,
            "evidence": "Independent check: external operation has ended; row value is 1.",
            "execution_stopped": True,
            **overrides,
        },
    )


@pytest.mark.parametrize(
    "resolution,status",
    [("succeeded", "succeeded"), ("failed", "failed"), ("not_executed", "denied")],
)
async def test_original_history_continues_once_without_redispatch_or_duplicate_prompt(
    store, resolution, status
):
    row = interrupted(store)
    before = row["budget"]
    with pytest.raises(RunConflictError, match="Reconcile"):
        store.resume(row["id"], "user", row["event_seq"])
    receipt = reconcile(store, row, resolution=resolution)
    assert receipt["verified_by_platform"] is False
    assert receipt["source"] == "user_reported"
    checked = store.get(row["id"], "user")
    assert checked["status"] == "interrupted"  # A check alone never restarts the agent.
    assert checked["tool_calls"][0]["status"] == status
    with store.sessions() as db:
        assert db.scalar(select(tables.resource_locks.c.resource_key)) is None
    with pytest.raises(RunConflictError, match="refresh"):
        store.resume(row["id"], "user", row["event_seq"])
    script = Script(["核对记录已收到。"])
    service = AgentRunService(
        store, script.factory, {}, capabilities_for_run=lambda _: frozenset(), poll_seconds=0.01
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: store.resume(row["id"], "user", checked["event_seq"]), range(4))
        )
    assert all(result["status"] == "queued" for result in results)
    assert store.get(row["id"], "user")["budget"] == before
    await service.start()
    try:
        final = await settled(store, row["id"], status="finished")
        assert final["output"] == "核对记录已收到。"
        assert len(script.requests) == 1
        assert service.resume(row["id"], "user", checked["event_seq"])["status"] == "finished"
    finally:
        await service.close()
    parts = [part for message in store.history(row["id"], "user") for part in message.parts]
    assert (
        sum(isinstance(part, UserPromptPart) and part.content == row["prompt"] for part in parts)
        == 1
    )
    assert (
        sum(isinstance(part, ToolReturnPart) and part.tool_call_id == "call-0" for part in parts)
        == 1
    )
    events = store.read_events(row["id"], "user")
    assert sum(event["kind"] == "tool_start" for event in events) == 1
    assert sum(event["kind"] == "run_resumed" for event in events) == 1


def test_all_unknown_calls_must_be_resolved_and_each_resource_stays_fenced(store):
    row = interrupted(store, 2)
    first = reconcile(store, row)
    assert reconcile(store, row) == first
    with pytest.raises(RunConflictError, match="different reconciliation"):
        reconcile(store, row, resolution="not_executed")
    checked = store.get(row["id"], "user")
    with pytest.raises(RunConflictError, match="Reconcile"):
        store.resume(row["id"], "user", checked["event_seq"])
    with store.sessions() as db:
        assert list(db.scalars(select(tables.resource_locks.c.resource_key))) == ["resource-1"]
    reconcile(store, row, 1)
    checked = store.get(row["id"], "user")
    assert store.resume(row["id"], "user", checked["event_seq"])["status"] == "queued"


@pytest.mark.parametrize(
    "overrides", [{"evidence": " "}, {"execution_stopped": False}, {"resolution": "unknown"}]
)
def test_incomplete_external_check_does_not_release_lock(store, overrides):
    row = interrupted(store)
    with pytest.raises(ValueError):
        reconcile(store, row, **overrides)
    assert store.get(row["id"], "user")["tool_calls"][0]["status"] == "outcome_unknown"
    with store.sessions() as db:
        assert db.scalar(select(tables.resource_locks.c.resource_key)) == "resource-0"


def test_owner_and_action_identity_are_checked(store):
    row = interrupted(store)
    with pytest.raises(RunNotFoundError):
        store.resume(row["id"], "other", row["event_seq"])
    for actor, digest, error in [
        ("other", row["tool_calls"][0]["fingerprint"], RunNotFoundError),
        ("user", "0" * 64, RunConflictError),
    ]:
        with pytest.raises(error):
            store.reconcile(
                row["id"],
                actor,
                "call-0",
                digest,
                resolution="succeeded",
                evidence="External check",
                execution_stopped=True,
            )
