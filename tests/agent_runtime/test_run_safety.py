"""Durability and failure contracts with real temporary SQLite transactions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic_ai import Tool
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from sqlalchemy import select
from test_runtime import Script, call, returns
from test_service import allow_read, approve_write, settled, submit

from app.models import agent_runs as tables
from app.services.agent.definitions import AgentDefinition
from app.services.agent.execution import RegisteredTool, ToolAccess
from app.services.agent.service import AgentRunService
from app.services.agent.store import LeaseLostError, OutcomeUnknownError, RunConflictError, RunStore


def service_for(store, script, tools):
    return AgentRunService(
        store,
        script.factory,
        tools,
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )


def decide(service, row, approved=True):
    for approval in row["approvals"]:
        if approval["decision"] == "pending":
            service.approve(
                row["id"], "user", approval["call_id"], approval["fingerprint"], approved
            )


@pytest.mark.parametrize("change", ["permission", "target"])
async def test_approval_revalidates_permissions_and_target_without_aborting_reasoning(
    store, change
):
    writes, changed = [], False

    async def write() -> str:
        writes.append("write")
        return "saved"

    async def authorize(_ctx, _args):
        return ToolAccess(
            allowed=not (changed and change == "permission"),
            target={"version": 2 if changed and change == "target" else 1},
            requires_approval=True,
            resource_key="sample",
        )

    script = Script([call("write")], ["条件已变化，这次没有写入。"])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=authorize, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        changed = True
        decide(service, row)
        final = await settled(store, row["id"], status="finished")
        assert writes == []
        assert final["tool_calls"][0]["status"] == "failed"
        assert returns(script.requests[-1][0])[0].outcome == "failed"
    finally:
        await service.close()


async def test_denied_action_cannot_create_another_approval_in_the_same_run(store):
    writes = []

    async def write() -> str:
        writes.append(1)
        return "saved"

    script = Script(
        [call("write", call_id="first")], [call("write", call_id="second")], ["不执行。"]
    )
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        decide(service, row, False)
        final = await settled(store, row["id"], status="finished")
        assert writes == []
        assert len(final["approvals"]) == 1
        assert {c["status"] for c in final["tool_calls"]} == {"denied"}
        assert len(returns(script.requests[-1][0])) == 2
    finally:
        await service.close()


async def test_identical_arguments_with_distinct_call_ids_need_distinct_approvals(store):
    writes = []

    async def write() -> str:
        writes.append(1)
        return "saved"

    script = Script([call("write", call_id="first")], [call("write", call_id="second")], ["完成。"])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        decide(service, row)
        async with asyncio.timeout(5):
            while True:
                second = store.get(row["id"], "user")
                if len(second["approvals"]) == 2 and second["status"] == "waiting_approval":
                    break
                await asyncio.sleep(0.01)
        assert writes == [1]
        decide(service, second)
        await settled(store, row["id"], status="finished")
        assert writes == [1, 1]
    finally:
        await service.close()


@pytest.mark.parametrize("action", ["new_input", "cancel"])
async def test_paused_run_cancellation_pairs_original_calls_and_invalidates_approvals(
    store, action
):
    async def write() -> str:
        pytest.fail("Cancelled action must never execute")

    script = Script([call("write")], ["按照新要求，只解释。"])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        if action == "cancel":
            service.cancel(row["id"], "user")
        followup = submit(service, key="followup", prompt="不要执行，只解释")
        final = await settled(store, followup["id"], status="finished")
        cancelled = store.get(row["id"], "user")
        assert cancelled["status"] == "cancelled"
        assert cancelled["approvals"][0]["decision"] == "cancelled"
        with pytest.raises(RunConflictError):
            decide(service, row)
        result = returns(script.requests[-1][0])[0]
        assert result.tool_call_id == "call-1"
        assert result.outcome == "denied"
        assert any(
            isinstance(p, UserPromptPart) and p.content == "不要执行，只解释"
            for m in script.requests[-1][0]
            for p in m.parts
        )
        assert final["seq"] == 2
    finally:
        await service.close()


@pytest.mark.parametrize("action", ["cancel", "stop_and_modify"])
async def test_cancel_running_read_stops_it_and_keeps_followup_history_valid(store, action):
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def read() -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    script = Script([call("read")], ["收到新的要求。"])
    service = service_for(
        store, script, {"read": RegisteredTool(tool=Tool(read), authorize=allow_read)}
    )
    await service.start()
    try:
        row = submit(service)
        await asyncio.wait_for(entered.wait(), 3)
        if action == "cancel":
            service.cancel(row["id"], "user")
        followup = service.submit(
            "conversation",
            "user",
            "followup",
            "停止原任务，回答新问题",
            AgentDefinition(name="test", tool_names=frozenset(service.tools)),
            stop_and_modify=action == "stop_and_modify",
        )
        final = await settled(store, followup["id"], status="finished")
        assert stopped.is_set()
        assert store.get(row["id"], "user")["status"] == "cancelled"
        assert len(returns(script.requests[-1][0])) == 1
        assert final["output"] == "收到新的要求。"
    finally:
        await service.close()


@pytest.mark.parametrize("failure", ["write_error", "result_commit"])
async def test_uncertain_write_is_fenced_and_never_replayed(store, monkeypatch, failure):
    writes = []

    async def write() -> str:
        writes.append(1)
        if failure == "write_error":
            raise OSError("Connection dropped after submission")
        return "saved"

    original = store.finish_call

    def fail_result_commit(*args, **kwargs):
        if args[3]["outcome"] == "success":
            raise OSError("Synthetic commit failure")
        return original(*args, **kwargs)

    if failure == "result_commit":
        monkeypatch.setattr(store, "finish_call", fail_result_commit)
    script = Script([call("write")])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        decide(service, row)
        final = await settled(store, row["id"], status="interrupted")
        assert writes == [1]
        assert final["tool_calls"][0]["status"] == "outcome_unknown"
        assert final["error_code"] == "reconciliation_required"
        with store.sessions() as db:
            assert db.scalar(select(tables.resource_locks.c.run_id)) == row["id"]
        # Repeated UI submissions/approval decisions cannot replay a side effect.
        decide(service, row)
        assert store.claim(row["id"], "another-owner") is None
        next_run = submit(service, key="followup")
        assert store.claim(next_run["id"], "another-owner") is None
        assert writes == [1]
    finally:
        await service.close()


def claimed_call(store, *, conversation="conversation", owner="owner", key="one"):
    row = store.submit(
        conversation,
        "user",
        key,
        "执行",
        AgentDefinition(name="test", tool_names=frozenset({"write"})),
    )
    store.claim(row["id"], owner)
    history = [
        ModelRequest(parts=[UserPromptPart("执行")]),
        ModelResponse(parts=[ToolCallPart("write", {}, "call-1")]),
    ]
    store.prepare_call(
        row["id"],
        owner,
        call_id="call-1",
        name="write",
        arguments={},
        target={"version": 1},
        mutating=True,
        resource_key="fixture:sample",
        needs_approval=False,
        history=history,
    )
    return row


def test_expired_worker_cannot_dispatch_or_report_success_and_recovery_does_not_replay(store):
    now = [100.0]
    store.clock = lambda: now[0]
    row = claimed_call(store)
    store.claim_call(row["id"], "owner", "call-1", approved_by_framework=False)
    now[0] += 2
    with pytest.raises(LeaseLostError):
        store.claim_call(row["id"], "owner", "call-1", approved_by_framework=False)
    assert store.recover_expired() == [row["id"]]
    assert store.recover_expired() == []
    with pytest.raises(LeaseLostError):
        store.finish_call(row["id"], "owner", "call-1", {"outcome": "success", "content": "late"})
    assert store.get(row["id"], "user")["tool_calls"][0]["status"] == "outcome_unknown"
    assert store.claim(row["id"], "replacement") is None


def test_unknown_outcome_cannot_be_overwritten_by_normal_completion(store):
    row = claimed_call(store)
    store.claim_call(row["id"], "owner", "call-1", approved_by_framework=False)
    store.finish_call(
        row["id"], "owner", "call-1", {"outcome": "failed", "content": "unknown"}, unknown=True
    )
    with pytest.raises(OutcomeUnknownError):
        store.finish_call(row["id"], "owner", "call-1", {"outcome": "success", "content": "late"})
    with store.sessions() as db:
        assert db.scalar(select(tables.resource_locks.c.run_id)) == row["id"]


def test_database_claim_is_exclusive_across_threads(store):
    row = store.submit(
        "conversation", "user", "one", "你好", AgentDefinition(name="test", tool_names=frozenset())
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda owner: store.claim(row["id"], str(owner)), range(4)))
    assert sum(claim is not None for claim in claims) == 1


async def test_resource_lock_blocks_another_conversation_but_model_can_explain(store):
    locked = claimed_call(store)
    store.claim_call(locked["id"], "owner", "call-1", approved_by_framework=False)
    store.finish_call(
        locked["id"], "owner", "call-1", {"outcome": "failed", "content": "unknown"}, unknown=True
    )
    store.create_conversation("second", "user")

    async def write() -> str:
        pytest.fail("Resource with unknown outcome must not be written again")

    script = Script([call("write")], ["该资源有待核实的操作，暂时不能修改。"])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service, conversation="second")["id"])
        decide(service, row)
        final = await settled(store, row["id"], status="finished")
        assert final["tool_calls"][0]["status"] == "failed"
        assert returns(script.requests[-1][0])[0].outcome == "failed"
        with store.sessions() as db:
            assert db.scalar(select(tables.resource_locks.c.run_id)) == locked["id"]
    finally:
        await service.close()


async def test_fresh_service_resumes_persisted_approval_with_original_call_id(store):
    writes = []

    async def write() -> str:
        writes.append(1)
        return "saved"

    script = Script([call("write", call_id="original")], ["完成。"])
    tools = {
        "write": RegisteredTool(
            tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
        )
    }
    first = service_for(store, script, tools)
    await first.start()
    row = await settled(store, submit(first)["id"])
    await first.close()
    second_store = RunStore(store.sessions, lease_seconds=1)
    second = service_for(second_store, script, tools)
    decide(second, row)
    assert writes == []
    await second.start()
    try:
        final = await settled(second_store, row["id"], status="finished")
        assert writes == [1]
        assert final["tool_calls"][0]["call_id"] == "original"
        assert returns(script.requests[-1][0])[0].tool_call_id == "original"
    finally:
        await second.close()


async def test_empty_server_capability_set_does_not_advertise_configured_tools(store):
    async def read() -> str:
        pytest.fail("Not authorized")

    script = Script(["直接回答。"])
    service = service_for(
        store, script, {"read": RegisteredTool(tool=Tool(read), authorize=allow_read)}
    )
    service.capabilities_for_run = lambda _: frozenset()
    await service.start()
    try:
        await settled(store, submit(service)["id"], status="finished")
        assert script.requests[0][1].function_tools == []
    finally:
        await service.close()


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), 0, -1])
def test_tool_timeout_must_be_finite_positive(timeout):
    async def read() -> str:
        return "data"

    with pytest.raises(ValueError, match="finite and positive"):
        RegisteredTool(tool=Tool(read), authorize=allow_read, timeout_seconds=timeout)


def test_sync_mutating_tool_is_rejected():
    def write() -> str:
        return "saved"

    with pytest.raises(ValueError, match="must be async"):
        RegisteredTool(tool=Tool(write, sequential=True), authorize=approve_write, mutating=True)


@pytest.mark.parametrize("revoke", [False, True])
async def test_same_call_identity_replays_known_result_only_after_authorization(store, revoke):
    reads, checks = [], []

    async def read() -> str:
        reads.append(1)
        return "protected data"

    async def authorize(ctx, args):
        checks.append(1)
        return ToolAccess(allowed=not (revoke and len(checks) > 1), target={"resource": "sample"})

    script = Script([call("read")], [call("read")], ["完成。"])
    service = service_for(
        store, script, {"read": RegisteredTool(tool=Tool(read), authorize=authorize)}
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"], status="finished")
        assert reads == [1]
        assert checks == [1, 1]
        assert len(row["tool_calls"]) == 1
        latest = returns(script.requests[-1][0])[-1]
        assert latest.outcome == ("failed" if revoke else "success")
        if revoke:
            assert "protected data" not in latest.content
    finally:
        await service.close()


@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_read_failures_are_tool_results_and_model_can_continue(store, failure):
    async def read() -> str:
        if failure == "exception":
            raise OSError("Internal credential or infrastructure detail must not reach the model")
        await asyncio.Event().wait()

    script = Script([call("read")], ["读取失败，暂时无法核实。"])
    service = service_for(
        store,
        script,
        {"read": RegisteredTool(tool=Tool(read), authorize=allow_read, timeout_seconds=0.02)},
    )
    await service.start()
    try:
        await settled(store, submit(service)["id"], status="finished")
        result = returns(script.requests[-1][0])[0]
        assert result.outcome == "failed"
        assert "credential" not in result.content
    finally:
        await service.close()


async def test_cancel_inflight_write_preserves_unknown_result_and_resource_fence(store):
    entered = asyncio.Event()

    async def write() -> str:
        entered.set()
        await asyncio.Event().wait()

    script = Script([call("write")])
    service = service_for(
        store,
        script,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"])
        decide(service, row)
        await asyncio.wait_for(entered.wait(), 3)
        service.cancel(row["id"], "user")
        final = await settled(store, row["id"], status="cancelled")
        assert final["tool_calls"][0]["status"] == "outcome_unknown"
        with store.sessions() as db:
            assert db.scalar(select(tables.resource_locks.c.run_id)) == row["id"]
        assert "unknown" in returns(store.history(row["id"], "user"))[-1].content
    finally:
        await service.close()
