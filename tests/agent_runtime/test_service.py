import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel
from pydantic_ai import RunContext, Tool
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from test_runtime import Script, call

from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.execution import RegisteredTool, ToolAccess
from app.services.agent.service import AgentRunService
from app.services.agent.store import RunConflictError, RunNotFoundError, RunStore


def submit(service, *, key="request-1", prompt="执行测试", conversation="conversation"):
    return service.submit(
        conversation,
        "user",
        key,
        prompt,
        AgentDefinition(name="test", tool_names=frozenset(service.tools)),
    )


async def settled(store, run_id, *, status=None):
    async with asyncio.timeout(5):
        while True:
            row = store.get(run_id, "user")
            done = row["status"] == status if status else row["status"] not in {"queued", "running"}
            if done:
                return row
            if status and row["status"] in {
                "finished",
                "failed",
                "cancelled",
                "limited",
                "interrupted",
            }:
                raise AssertionError(
                    f"Expected {status}, got terminal status {row['status']} ({row['error_code']})"
                )
            await asyncio.sleep(0.01)


async def allow_read(_ctx, _args):
    return ToolAccess(allowed=True, target={"fixture": "sample"})


async def approve_write(_ctx, args):
    return ToolAccess(
        allowed=True,
        requires_approval=True,
        target={"fixture": args.get("target", "sample"), "version": 1},
        resource_key=f"fixture:{args.get('target', 'sample')}",
    )


async def test_nested_typed_arguments_remain_typed_after_durable_approval_resume(store):
    class Change(BaseModel):
        labels: list[str]

    writes = []

    async def write(change: Change) -> str:
        assert isinstance(change, Change)
        writes.append(change.labels)
        return "written"

    script = Script(
        [call("write", '{"change":{"labels":["中文","review"]}}', "typed")], ["已保存。"]
    )
    service = AgentRunService(
        store,
        script.factory,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        run = submit(service)
        paused = await settled(store, run["id"])
        assert paused["status"] == "waiting_approval", paused
        assert writes == []
        assert paused["tool_calls"][0]["arguments"] == {"change": {"labels": ["中文", "review"]}}
        approval = paused["approvals"][0]
        for _ in range(2):
            service.approve(run["id"], "user", "typed", approval["fingerprint"], True)
        final = await settled(store, run["id"], status="finished")
        assert writes == [["中文", "review"]]
        assert final["tool_calls"][0]["fingerprint"] == paused["tool_calls"][0]["fingerprint"]
    finally:
        await service.close()


async def test_background_run_persists_stream_and_native_history(store):
    script = Script(["可以，", "直接回答。"])
    service = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = submit(service)
        final = await settled(store, row["id"])
        assert final["status"] == "finished"
        assert final["output"] == "可以，直接回答。"
        events = store.read_events(row["id"], "user")
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert (
            "".join(e["payload"]["text"] for e in events if e["kind"] == "assistant_delta")
            == final["output"]
        )
        assert len([e for e in events if e["kind"] == "assistant_message_end"]) == 1
        assert len(store.history(row["id"], "user")) == 2
        assert final["budget"]["usage"]["requests"] == 1
    finally:
        await service.close()


async def test_textual_tool_markup_is_retried_then_becomes_a_real_approval(store):
    writes = []

    async def write(target: str) -> str:
        writes.append(target)
        return "written"

    script = Script(
        ['准备执行。<invoke name="write"><parameter name="target">sample</parameter></invoke>'],
        [call("write", '{"target":"sample"}', "native-call")],
        ["已完成。"],
    )
    service = AgentRunService(
        store,
        script.factory,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        run = submit(service)
        paused = await settled(store, run["id"])
        assert paused["status"] == "waiting_approval"
        assert writes == []
        events = store.read_events(run["id"], "user")
        assert (
            len([event for event in events if event["kind"] == "assistant_message_discarded"]) == 1
        )
        assert "<invoke" not in "".join(
            event["payload"].get("text", "")
            for event in events
            if event["kind"] == "assistant_delta"
        )
        approval = paused["approvals"][0]
        service.approve(run["id"], "user", "native-call", approval["fingerprint"], True)
        final = await settled(store, run["id"], status="finished")
        assert final["output"] == "已完成。"
        assert writes == ["sample"]
        assert final["budget"]["usage"]["requests"] == 3
    finally:
        await service.close()


async def test_request_and_result_are_durable_on_each_side_of_a_tool(store):
    async def read(ctx: RunContext[RunDependencies]) -> str:
        history = store.history(ctx.deps.run_id, "user")
        assert any(
            isinstance(part, ToolCallPart) and part.tool_call_id == ctx.tool_call_id
            for message in history
            for part in message.parts
        )
        assert store.get(ctx.deps.run_id, "user")["tool_calls"][0]["status"] == "executing"
        return "42"

    script = Script(["读取。", call("read")], ["42"])
    service = AgentRunService(
        store,
        script.factory,
        {"read": RegisteredTool(tool=Tool(read), authorize=allow_read)},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = submit(service)
        final = await settled(store, row["id"])
        assert final["status"] == "finished"
        assert final["tool_calls"][0]["status"] == "succeeded"
        assert final["tool_calls"][0]["result"]["content"] == "42"
        kinds = [e["kind"] for e in store.read_events(row["id"], "user")]
        assert kinds.index("tool_result") < kinds.index(
            "request_started", kinds.index("tool_start")
        )
        assert len(store.history(row["id"], "user")) == 4
    finally:
        await service.close()


async def test_approval_only_records_a_decision_and_duplicate_decisions_do_not_reexecute(store):
    writes = []

    async def write() -> str:
        writes.append(1)
        return "written"

    script = Script([call("write", call_id="original-call")], ["已经执行。"])
    service = AgentRunService(
        store,
        script.factory,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = submit(service)
        paused = await settled(store, row["id"])
        assert paused["status"] == "waiting_approval"
        assert writes == []
        approval = paused["approvals"][0]
        with ThreadPoolExecutor(max_workers=4) as pool:
            decisions = list(
                pool.map(
                    lambda _: store.decide(
                        row["id"], "user", "original-call", approval["fingerprint"], True
                    ),
                    range(4),
                )
            )
        assert all(item["decision"] == "approved" for item in decisions)
        assert writes == []  # No yield to the worker; approval APIs did not execute.
        final = await settled(store, row["id"], status="finished")
        assert writes == [1]
        service.approve(row["id"], "user", "original-call", approval["fingerprint"], True)
        await asyncio.sleep(0.03)
        assert writes == [1]
        assert final["budget"]["usage"]["requests"] == 2
    finally:
        await service.close()


async def test_batch_waits_for_all_decisions_and_retains_completed_reads(store):
    reads, writes = [], []

    async def read() -> str:
        reads.append(1)
        return "current"

    async def write(target: str) -> str:
        writes.append(target)
        return target

    script = Script(
        [
            call("read", call_id="read"),
            call("write", '{"target":"a"}', "a", 1),
            call("write", '{"target":"b"}', "b", 2),
        ],
        ["a 已更新，b 没有执行。"],
    )
    service = AgentRunService(
        store,
        script.factory,
        {
            "read": RegisteredTool(tool=Tool(read), authorize=allow_read),
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            ),
        },
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = submit(service)
        paused = await settled(store, row["id"])
        assert paused["status"] == "waiting_approval"
        approvals = {item["call_id"]: item for item in paused["approvals"]}
        service.approve(row["id"], "user", "a", approvals["a"]["fingerprint"], True)
        await asyncio.sleep(0.04)
        assert writes == []
        service.approve(row["id"], "user", "b", approvals["b"]["fingerprint"], False)
        final = await settled(store, row["id"], status="finished")
        assert reads == [1] and writes == ["a"]
        assert {call["call_id"]: call["status"] for call in final["tool_calls"]} == {
            "read": "succeeded",
            "a": "succeeded",
            "b": "denied",
        }
        history = store.history(row["id"], "user")
        results = [
            part.tool_call_id
            for message in history
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert sorted(results) == ["a", "b", "read"]
    finally:
        await service.close()


async def test_two_service_instances_cannot_dispatch_the_same_run(store):
    script = Script(["一次即可。"])
    other_store = RunStore(store.sessions, lease_seconds=1)
    first = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    second = AgentRunService(
        other_store,
        script.factory,
        {},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await first.start()
    await second.start()
    try:
        row = submit(first)
        duplicate = submit(second)
        assert row["id"] == duplicate["id"]
        await settled(store, row["id"])
        assert len(script.requests) == 1
        assert (
            len([e for e in store.read_events(row["id"], "user") if e["kind"] == "run_started"])
            == 1
        )
    finally:
        await first.close()
        await second.close()


async def test_followup_is_queued_and_receives_the_previous_native_history(store):
    script = Script(["第一轮"], ["第二轮"])
    service = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        first = submit(service)
        second = submit(service, key="second", prompt="继续解释")
        final = await settled(store, second["id"])
        assert final["status"] == "finished"
        assert store.get(first["id"], "user")["status"] == "finished"
        assert len(script.requests[1][0]) == 3
        assert len(store.history(second["id"], "user")) == 4
    finally:
        await service.close()


async def test_subscription_disconnect_does_not_cancel_run_and_replay_has_no_duplicates(store):
    release = asyncio.Event()
    script = Script(["已有", "输出"])
    original = script.stream

    async def delayed(messages, info):
        yield "开始。"
        await release.wait()
        async for item in original(messages, info):
            yield item

    from pydantic_ai.models.function import FunctionModel

    script.model = FunctionModel(stream_function=delayed)
    service = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda row: frozenset(row["definition"]["tool_names"]),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = submit(service)
        stream = service.subscribe(row["id"], "user")
        received = []
        async for event in stream:
            received.append(event)
            if event["kind"] == "assistant_delta":
                break
        await stream.aclose()
        assert store.get(row["id"], "user")["status"] == "running"
        release.set()
        await settled(store, row["id"])
        replay = [e async for e in service.subscribe(row["id"], "user", received[-1]["seq"])]
        assert replay[0]["seq"] == received[-1]["seq"] + 1
        assert len(script.requests) == 1
    finally:
        release.set()
        await service.close()


def test_idempotency_keys_and_actor_isolation(store):
    definition = AgentDefinition(name="test", tool_names=frozenset())
    row = store.submit("conversation", "user", "request", "hello", definition)
    with pytest.raises(RunConflictError):
        store.submit("conversation", "user", "request", "different", definition)
    for operation in [
        lambda: store.get(row["id"], "other"),
        lambda: store.cancel(row["id"], "other"),
        lambda: store.read_events(row["id"], "other"),
        lambda: store.submit("conversation", "other", "new", "hello", definition),
    ]:
        with pytest.raises(RunNotFoundError):
            operation()
