import asyncio

from pydantic_ai import Tool
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
)
from test_run_safety import service_for
from test_runtime import Script, call
from test_service import allow_read, settled, submit

from app.services.agent.definitions import AgentDefinition
from app.services.agent.events import RunEvents
from app.services.agent.execution import RegisteredTool
from app.services.agent.runtime import ExecutionBudget


def observer_for(store):
    row = store.submit(
        "conversation", "user", "one", "你好", AgentDefinition(name="test", tool_names=frozenset())
    )
    store.claim(row["id"], "owner")
    observer = RunEvents(store, row["id"], "owner", ExecutionBudget(), 0)
    observer.message_id = "message-1"
    return row, observer


async def test_textual_tool_markup_is_streamed_without_protocol_classification(store):
    row, observer = observer_for(store)

    async def stream():
        yield PartStartEvent(index=0, part=TextPart("我先处理。"))
        await asyncio.sleep(0.06)
        yield PartDeltaEvent(index=0, delta=TextPartDelta("<inv"))
        yield PartDeltaEvent(
            index=0,
            delta=TextPartDelta('oke name="request_database_change"><parameter name="sql">'),
        )
        yield PartDeltaEvent(index=0, delta=TextPartDelta("DROP TABLE users</parameter></invoke>"))

    await observer.handle(None, stream())
    events = store.read_events(row["id"], "user")
    deltas = [event for event in events if event["kind"] == "assistant_delta"]
    assert "".join(event["payload"]["text"] for event in deltas) == (
        '我先处理。<invoke name="request_database_change"><parameter name="sql">'
        "DROP TABLE users</parameter></invoke>"
    )


async def test_quiet_provider_flushes_pending_text_before_stream_finishes(store):
    row, observer = observer_for(store)
    release = asyncio.Event()

    async def stream():
        yield PartStartEvent(index=0, part=TextPart("首段文字"))
        await release.wait()
        yield PartDeltaEvent(index=0, delta=TextPartDelta("继续"))

    task = asyncio.create_task(observer.handle(None, stream()))
    try:
        async with asyncio.timeout(1):
            while not any(
                e["kind"] == "assistant_delta" for e in store.read_events(row["id"], "user")
            ):
                await asyncio.sleep(0.005)
        assert not task.done()
        release.set()
        await task
        deltas = [e for e in store.read_events(row["id"], "user") if e["kind"] == "assistant_delta"]
        assert [e["payload"]["text"] for e in deltas] == ["首段文字", "继续"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_text_batching_preserves_unicode_part_ids_and_omits_thinking(store):
    row, observer = observer_for(store)
    content = "中文🙂" * 2000

    async def stream():
        yield PartStartEvent(index=0, part=ThinkingPart("private thinking"))
        yield PartStartEvent(index=1, part=TextPart(content))
        yield PartStartEvent(index=2, part=TextPart("第二部分"))

    await observer.handle(None, stream())
    deltas = [
        e["payload"] for e in store.read_events(row["id"], "user") if e["kind"] == "assistant_delta"
    ]
    assert "".join(e["text"] for e in deltas) == content + "第二部分"
    assert all(len(e["text"].encode("utf-8")) <= 4096 for e in deltas)
    assert {e["part_id"] for e in deltas[:-1]} == {"1"}
    assert deltas[-1]["part_id"] == "2"
    assert {e["message_id"] for e in deltas} == {"message-1"}


async def test_text_is_persisted_before_message_end_tool_dispatch_and_terminal_event(store):
    async def read() -> str:
        return "42"

    script = Script(["我先查看。", call("read")], ["结果", "是 42。"])
    service = service_for(
        store, script, {"read": RegisteredTool(tool=Tool(read), authorize=allow_read)}
    )
    await service.start()
    try:
        row = await settled(store, submit(service)["id"], status="finished")
        events = store.read_events(row["id"], "user")
        ends = [e for e in events if e["kind"] == "assistant_message_end"]
        assert len(ends) == 2
        for end in ends:
            chunks = [
                e
                for e in events
                if e["kind"] == "assistant_delta"
                and e["payload"]["message_id"] == end["payload"]["message_id"]
            ]
            assert chunks
            assert all(e["seq"] < end["seq"] for e in chunks)
        assert ends[0]["seq"] < next(e["seq"] for e in events if e["kind"] == "tool_start")
        assert ends[1]["seq"] < events[-1]["seq"]
        assert events[-1]["kind"] == "run_finished"
        assert (
            "".join(e["payload"]["text"] for e in events if e["kind"] == "assistant_delta")
            == "我先查看。结果是 42。"
        )
    finally:
        await service.close()
