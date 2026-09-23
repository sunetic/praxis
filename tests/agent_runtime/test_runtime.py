"""Execution contracts, not a claim that scripted models demonstrate intelligence."""

import asyncio
from copy import deepcopy

import pytest
from pydantic import TypeAdapter
from pydantic_ai import (
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    Tool,
    ToolDenied,
    capture_run_messages,
)
from pydantic_ai.exceptions import (
    ApprovalRequired,
    RunCancelled,
    ToolFailed,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.runtime import ActiveTimeLimitExceeded, ExecutionBudget, run_agent
from app.services.agent.tools import dynamic_tool


def call(name, arguments="{}", call_id="call-1", index=0):
    return {index: DeltaToolCall(name=name, json_args=arguments, tool_call_id=call_id)}


class Script:
    def __init__(self, *responses):
        self.responses = responses
        self.requests = []
        self.events = []
        self.model = FunctionModel(stream_function=self.stream, model_name="contract-model")

    async def factory(self, _row):
        return self.model

    async def stream(self, messages, info):
        index = len(self.requests)
        self.requests.append((deepcopy(messages), info))
        assert index < len(self.responses), "Unexpected extra model request"
        for chunk in self.responses[index]:
            yield chunk

    async def record(self, _ctx, events):
        async for event in events:
            self.events.append(event)


def returns(messages):
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def displayed_text(events):
    pieces = []
    for event in events:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            pieces.append(event.part.content)
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            pieces.append(event.delta.content_delta)
    return "".join(pieces)


async def invoke(script, *, tools=(), configured=None, authorized=None, **kwargs):
    catalog = {tool.name: tool for tool in tools}
    names = frozenset(catalog)
    return await run_agent(
        definition=AgentDefinition(
            name="contract-agent", tool_names=names if configured is None else configured
        ),
        model=script.model,
        deps=kwargs.pop(
            "deps", RunDependencies(run_id="logical-1", conversation_id="conv-1", actor_id="user-1")
        ),
        tools=catalog,
        authorized_tool_names=names if authorized is None else authorized,
        budget=kwargs.pop("budget", ExecutionBudget()),
        prompt=kwargs.pop("prompt", "帮我处理这个请求。"),
        event_stream_handler=script.record,
        **kwargs,
    )


async def test_natural_answer_finishes_without_an_extra_model_call_or_output_tool():
    script = Script(["可以，", "直接回答。"])
    result = await invoke(script)
    assert result.output == "可以，直接回答。"
    assert displayed_text(script.events) == result.output
    assert len(script.requests) == 1
    assert script.requests[0][1].output_tools == []
    assert script.requests[0][1].function_tools == []


async def test_mixed_preamble_and_tool_call_does_not_end_the_run_early():
    executed = []

    async def lookup(ctx: RunContext[RunDependencies], key: str) -> str:
        executed.append((ctx.tool_call_id, ctx.deps.actor_id, key))
        return "实测值：42"

    script = Script(["我查一下这个值。", call("lookup", '{"key":"count"}')], ["结果是 42。"])
    result = await invoke(script, tools=[Tool(lookup)])
    assert result.output == "结果是 42。"
    assert executed == [("call-1", "user-1", "count")]
    assert displayed_text(script.events) == "我查一下这个值。结果是 42。"
    assert len(script.requests) == 2
    assert returns(script.requests[1][0])[0].tool_call_id == "call-1"
    assert any(isinstance(event, FunctionToolCallEvent) for event in script.events)
    assert any(isinstance(event, FunctionToolResultEvent) for event in script.events)
    schema = script.requests[0][1].function_tools[0].parameters_json_schema
    assert set(schema["properties"]) == {"key"}
    assert "_runtime" not in schema["properties"]


async def test_textual_known_tool_call_gets_one_native_protocol_retry():
    executed = []

    async def lookup(key: str) -> str:
        executed.append(key)
        return "42"

    script = Script(
        ['我来查询。<invoke name="lookup"><parameter name="key">count</parameter></invoke>'],
        [call("lookup", '{"key":"count"}')],
        ["结果是 42。"],
    )
    result = await invoke(script, tools=[Tool(lookup)])

    assert result.output == "结果是 42。"
    assert executed == ["count"]
    assert len(script.requests) == 3
    assert any(
        isinstance(part, RetryPromptPart)
        and "native structured tool interface" in str(part.content)
        for part in script.requests[1][0][-1].parts
    )


async def test_textual_known_tool_call_protocol_retry_is_bounded():
    async def lookup(key: str) -> str:
        pytest.fail("Textual tool markup must never execute")

    leaked = ['<invoke name="lookup"><parameter name="key">count</parameter></invoke>']
    script = Script(leaked, leaked)
    with pytest.raises(UnexpectedModelBehavior, match="maximum output retries"):
        await invoke(script, tools=[Tool(lookup)])
    assert len(script.requests) == 2


async def test_business_failures_do_not_use_parameter_retry_budget_or_abort_task():
    attempts = 0

    async def query(column: str) -> str:
        nonlocal attempts
        attempts += 1
        if column != "real_column":
            raise ToolFailed(f"Unknown column: {column}")
        return "42"

    script = Script(
        *[[call("query", f'{{"column":"wrong_{i}"}}', f"bad-{i}")] for i in range(4)],
        [call("query", '{"column":"real_column"}', "corrected")],
        ["真实字段的结果是 42。"],
    )
    result = await invoke(script, tools=[Tool(query)])
    assert result.output == "真实字段的结果是 42。"
    assert attempts == 5
    assert [part.outcome for part in returns(result.all_messages())] == ["failed"] * 4 + ["success"]


async def test_identical_successful_results_are_not_a_stop_condition():
    calls = []

    async def poll() -> str:
        calls.append(1)
        return "pending"

    script = Script(*[[call("poll", call_id=f"poll-{i}")] for i in range(5)], ["当前仍未就绪。"])
    result = await invoke(script, tools=[Tool(poll)])
    assert result.output == "当前仍未就绪。"
    assert len(calls) == 5


@pytest.mark.parametrize(
    "configured,authorized,expected",
    [
        (frozenset(), frozenset({"read", "write"}), []),
        (frozenset({"read", "write"}), frozenset(), []),
        (frozenset({"read", "write"}), frozenset({"read"}), ["read"]),
    ],
)
async def test_tool_selection_is_a_real_intersection(configured, authorized, expected):
    script = Script(["仅使用已授权能力。"])
    await invoke(
        script,
        tools=[Tool(lambda: None, name="read"), Tool(lambda: None, name="write")],
        configured=configured,
        authorized=authorized,
    )
    assert [tool.name for tool in script.requests[0][1].function_tools] == expected


async def test_unadvertised_tool_cannot_be_executed_even_if_the_model_names_it():
    writes = []

    def write() -> str:
        writes.append(1)
        return "written"

    script = Script([call("write")], ["没有这项能力。"])
    result = await invoke(script, tools=[Tool(write)], authorized=frozenset())
    assert result.output == "没有这项能力。"
    assert writes == []
    assert any(
        isinstance(part, RetryPromptPart)
        for message in script.requests[1][0]
        for part in message.parts
    )


async def test_unknown_configured_tool_fails_before_any_model_request():
    script = Script(["must not run"])
    with pytest.raises(ValueError, match="Unknown configured tools"):
        await invoke(script, configured=frozenset({"typo"}))
    assert script.requests == []


async def test_typed_argument_validation_prevents_execution_until_corrected():
    seen = []

    async def lookup(count: int) -> int:
        seen.append(count)
        return count

    script = Script(
        [call("lookup", '{"count":"not-a-number"}', "invalid")],
        [call("lookup", '{"count":3}', "valid")],
        ["3"],
    )
    await invoke(script, tools=[Tool(lookup)])
    assert seen == [3]


async def test_invalid_arguments_have_bounded_protocol_retries():
    async def lookup(count: int) -> int:
        pytest.fail("Invalid arguments reached the business function")

    script = Script(*[[call("lookup", '{"count":"bad"}', f"invalid-{i}")] for i in range(5)])
    with pytest.raises(UnexpectedModelBehavior):
        await invoke(script, tools=[Tool(lookup)])
    assert len(script.requests) == 3  # initial attempt plus two parameter corrections


async def test_dynamic_schema_validation_is_not_silently_skipped():
    seen = []

    async def service(ctx: RunContext[RunDependencies], count: int) -> int:
        seen.append((ctx.deps.actor_id, count))
        return count

    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
        "additionalProperties": False,
    }
    tool = dynamic_tool(service, name="service", description="Read count", schema=schema)
    schema["properties"]["count"]["type"] = "string"
    script = Script(
        [call("service", '{"count":0}', "invalid")],
        [call("service", '{"count":2,"actor_id":"admin"}', "spoofed")],
        [call("service", '{"count":2}', "valid")],
        ["2"],
    )
    await invoke(script, tools=[tool])
    assert seen == [("user-1", 2)]


async def test_multiple_approvals_resume_original_calls_without_replaying_completed_read():
    reads, writes = [], []

    async def read() -> str:
        reads.append(1)
        return "current"

    async def write(ctx: RunContext[RunDependencies], target: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"target": target})
        writes.append((ctx.tool_call_id, target))
        return f"updated {target}"

    tools = [Tool(read), Tool(write, sequential=True)]
    script = Script(
        [
            call("read", call_id="read-1"),
            call("write", '{"target":"a"}', "write-a", 1),
            call("write", '{"target":"b"}', "write-b", 2),
        ],
        ["两项操作已执行。"],
    )
    budget = ExecutionBudget()
    paused = await invoke(script, tools=tools, budget=budget)
    assert isinstance(paused.output, DeferredToolRequests)
    assert {part.tool_call_id for part in paused.output.approvals} == {"write-a", "write-b"}
    assert paused.output.metadata["write-a"] == {"target": "a"}
    assert reads == [1]
    assert writes == []
    assert budget.usage.requests == 1

    history = ModelMessagesTypeAdapter.validate_json(paused.all_messages_json())
    restored_budget = TypeAdapter(ExecutionBudget).validate_json(
        TypeAdapter(ExecutionBudget).dump_json(budget)
    )
    resumed = await invoke(
        script,
        tools=tools,
        prompt=None,
        message_history=history,
        budget=restored_budget,
        deferred_tool_results=DeferredToolResults(approvals={"write-a": True, "write-b": True}),
    )
    assert resumed.output == "两项操作已执行。"
    assert writes == [("write-a", "a"), ("write-b", "b")]
    assert reads == [1]
    assert restored_budget.usage.requests == 2
    assert restored_budget.active_seconds >= budget.active_seconds
    assert [part.tool_call_id for part in returns(resumed.all_messages())] == [
        "read-1",
        "write-a",
        "write-b",
    ]


async def test_denial_becomes_a_tool_result_and_allows_a_natural_answer():
    writes = []

    async def write() -> str:
        writes.append(1)
        return "written"

    tool = Tool(write, requires_approval=True)
    script = Script([call("write", call_id="denied-call")], ["没有执行修改。"])
    paused = await invoke(script, tools=[tool])
    resumed = await invoke(
        script,
        tools=[tool],
        prompt=None,
        message_history=paused.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={"denied-call": ToolDenied("用户拒绝了该操作。")}
        ),
    )
    assert resumed.output == "没有执行修改。"
    assert writes == []
    assert returns(resumed.all_messages())[-1].outcome == "denied"


async def test_new_call_with_identical_arguments_needs_a_new_approval():
    writes = []

    async def write() -> str:
        writes.append(1)
        return "written"

    tool = Tool(write, requires_approval=True)
    script = Script([call("write", call_id="first")], [call("write", call_id="second")])
    paused = await invoke(script, tools=[tool])
    resumed = await invoke(
        script,
        tools=[tool],
        prompt=None,
        message_history=paused.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals={"first": True}),
    )
    assert isinstance(resumed.output, DeferredToolRequests)
    assert [part.tool_call_id for part in resumed.output.approvals] == ["second"]
    assert writes == [1]


async def test_resource_permission_is_rechecked_after_approval():
    allowed = True
    writes = []

    async def write(ctx: RunContext[RunDependencies]) -> str:
        if not allowed:
            raise ToolFailed("Resource authorization revoked")
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        writes.append(1)
        return "written"

    script = Script([call("write")], ["当前权限已撤销，没有执行。"])
    paused = await invoke(script, tools=[Tool(write)])
    allowed = False
    resumed = await invoke(
        script,
        tools=[Tool(write)],
        prompt=None,
        message_history=paused.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals={"call-1": True}),
    )
    assert resumed.output == "当前权限已撤销，没有执行。"
    assert writes == []
    assert returns(resumed.all_messages())[-1].outcome == "failed"


async def test_request_budget_is_not_reset_when_resuming_approval():
    writes = []

    async def write() -> str:
        writes.append(1)
        return "written"

    tool = Tool(write, requires_approval=True)
    script = Script([call("write")], ["must not request a second response"])
    budget = ExecutionBudget(request_limit=1)
    paused = await invoke(script, tools=[tool], budget=budget)
    with pytest.raises(UsageLimitExceeded):
        await invoke(
            script,
            tools=[tool],
            prompt=None,
            budget=budget,
            message_history=paused.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={"call-1": True}),
        )
    assert len(script.requests) == 1
    assert writes == []


async def test_request_limit_covers_failed_tool_calls():
    async def query() -> str:
        raise ToolFailed("temporary failure")

    script = Script(*[[call("query", call_id=f"q-{i}")] for i in range(5)])
    budget = ExecutionBudget(request_limit=3)
    with capture_run_messages() as messages:
        with pytest.raises(UsageLimitExceeded):
            await invoke(script, tools=[Tool(query)], budget=budget)
    assert len(script.requests) == budget.usage.requests == 3
    assert len(returns(messages)) == 3


async def test_active_time_limit_is_enforced_without_calling_the_model_again():
    started = asyncio.Event()

    async def stream(_messages, _info):
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    script = Script()
    script.model = FunctionModel(stream_function=stream)
    budget = ExecutionBudget(active_seconds_limit=0.02)
    with pytest.raises(ActiveTimeLimitExceeded):
        await invoke(script, budget=budget)
    assert started.is_set()
    assert budget.active_seconds >= 0.02
    with pytest.raises(ActiveTimeLimitExceeded):
        await invoke(script, budget=budget)


async def test_an_unrelated_timeout_is_not_mislabeled_as_budget_exhaustion():
    async def stream(_messages, _info):
        raise TimeoutError("provider timed out")
        yield "unreachable"

    script = Script()
    script.model = FunctionModel(stream_function=stream)
    with pytest.raises(TimeoutError, match="provider timed out"):
        await invoke(script)


async def test_expired_active_budget_dispatches_no_requests():
    script = Script(["must not run"])
    budget = ExecutionBudget(active_seconds_limit=1, active_seconds=1)
    with pytest.raises(ActiveTimeLimitExceeded):
        await invoke(script, budget=budget)
    assert script.requests == []


async def test_native_cancellation_token_interrupts_a_pending_request():
    started = asyncio.Event()

    async def stream(_messages, _info):
        started.set()
        await asyncio.Event().wait()
        yield "unreachable"

    script = Script()
    script.model = FunctionModel(stream_function=stream)
    token = CancellationToken()
    task = asyncio.create_task(invoke(script, cancellation_token=token))
    await asyncio.wait_for(started.wait(), timeout=2)
    token.cancel()
    token.cancel()
    with pytest.raises(RunCancelled) as cancelled:
        await asyncio.wait_for(task, timeout=2)
    assert cancelled.value.all_messages()


async def test_cancellation_propagates_and_keeps_partial_native_messages():
    emitted = asyncio.Event()

    async def stream(_messages, _info):
        yield "已有部分输出。"
        emitted.set()
        await asyncio.Event().wait()

    script = Script()
    script.model = FunctionModel(stream_function=stream)
    budget = ExecutionBudget()
    with capture_run_messages() as messages:
        task = asyncio.create_task(invoke(script, budget=budget))
        await asyncio.wait_for(emitted.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert budget.active_seconds > 0
    assert any(message.state == "interrupted" for message in messages)
    assert displayed_text(script.events) == "已有部分输出。"


async def test_concurrent_runs_do_not_share_identity_or_history():
    async def identity(ctx: RunContext[RunDependencies]) -> str:
        await asyncio.sleep(0)
        return ctx.deps.actor_id

    first = Script([call("identity", call_id="one")], ["甲"])
    second = Script([call("identity", call_id="two")], ["乙"])
    results = await asyncio.gather(
        *[
            invoke(
                script,
                tools=[Tool(identity)],
                deps=RunDependencies(run_id=name, conversation_id=name, actor_id=name),
            )
            for script, name in [(first, "alice"), (second, "bob")]
        ]
    )
    assert [returns(result.all_messages())[0].content for result in results] == ["alice", "bob"]


async def test_thinking_content_is_not_confused_with_display_text():
    script = Script(
        [{0: DeltaThinkingPart(content="private reasoning", signature="sig")}, "结论。"]
    )
    result = await invoke(script)
    assert result.output == "结论。"
    assert displayed_text(script.events) == "结论。"
    assert any(
        isinstance(part, ThinkingPart)
        for message in result.all_messages()
        for part in message.parts
    )


def test_native_history_round_trip_keeps_provider_fields_and_call_identity():
    messages = [
        ModelResponse(
            [
                ThinkingPart("thinking", signature="signed", provider_name="provider"),
                ToolCallPart(
                    "read",
                    {"key": "x"},
                    tool_call_id="original",
                    provider_details={"opaque": "value"},
                ),
            ],
            provider_response_id="response-id",
            provider_details={"other": "preserve"},
        )
    ]
    encoded = ModelMessagesTypeAdapter.dump_json(messages)
    restored = ModelMessagesTypeAdapter.validate_json(encoded)
    assert ModelMessagesTypeAdapter.dump_json(restored) == encoded


@pytest.mark.parametrize(
    "kwargs", [{"request_limit": 0}, {"active_seconds_limit": float("inf")}, {"active_seconds": -1}]
)
def test_invalid_budgets_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ExecutionBudget(**kwargs)
