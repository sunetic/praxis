"""The single native model/tool execution entry point.

This kernel has no task-semantic state machine. The application run service owns
durable history, approval records, tool execution claims, events and cancellation.
Do not attach side-effecting domain tools until those durable boundaries are wired.
"""

import asyncio
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import monotonic

from pydantic_ai import (
    Agent,
    AgentRunResult,
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    ModelRetry,
    Tool,
)
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.protocol import textual_tool_call
from app.services.agent.tools import select_tools


@dataclass(kw_only=True)
class ExecutionBudget:
    """Consumed resources of one logical run, including approval continuations.

    The application must serialize these values alongside native messages. Waiting
    for a user consumes no active time. Concurrent runs must not share this object.
    """

    request_limit: int = 50
    active_seconds_limit: float = 900
    active_seconds: float = 0
    usage: RunUsage = field(default_factory=RunUsage)

    def __post_init__(self) -> None:
        if self.request_limit < 1:
            raise ValueError("request_limit must be positive")
        if not math.isfinite(self.active_seconds_limit) or self.active_seconds_limit <= 0:
            raise ValueError("active_seconds_limit must be finite and positive")
        if not math.isfinite(self.active_seconds) or self.active_seconds < 0:
            raise ValueError("active_seconds must be finite and nonnegative")


class ActiveTimeLimitExceeded(UsageLimitExceeded):
    """The logical run's active-time budget has been consumed."""


async def run_agent(
    *,
    definition: AgentDefinition,
    model: Model,
    deps: RunDependencies,
    tools: Mapping[str, Tool[RunDependencies]],
    authorized_tool_names: frozenset[str],
    budget: ExecutionBudget,
    prompt: str | None = None,
    message_history: Sequence[ModelMessage] = (),
    deferred_tool_results: DeferredToolResults | None = None,
    event_stream_handler: EventStreamHandler[RunDependencies] | None = None,
    cancellation_token: CancellationToken | None = None,
    model_settings: ModelSettings | None = None,
    toolset_wrapper: Callable[[AbstractToolset[RunDependencies]], AbstractToolset[RunDependencies]]
    | None = None,
    capabilities: Sequence[AbstractCapability[RunDependencies]] = (),
) -> AgentRunResult[str | DeferredToolRequests]:
    """Run the framework to completion or deferral, including mixed text/tool output.

    Always create an agent per invocation. Dependencies, history, and configuration
    cannot leak between conversations. The sole output retry is reserved for a provider
    protocol violation where a known tool call arrives as visible XML text instead of
    a native tool call; ordinary answer quality is never retried.
    """
    remaining = budget.active_seconds_limit - budget.active_seconds
    if remaining <= 0:
        raise ActiveTimeLimitExceeded("Active execution time limit exceeded")
    if budget.usage.requests >= budget.request_limit:
        # Deferred approvals can execute before the next model request. Check here
        # as well so a spent budget cannot dispatch a write during continuation.
        raise UsageLimitExceeded("Model request limit exceeded")
    selected = select_tools(tools, definition.tool_names, authorized_tool_names)
    selected_names = frozenset(tool.name for tool in selected)
    toolset = FunctionToolset(selected)
    agent = Agent(
        model,
        name=definition.name,
        deps_type=RunDependencies,
        instructions=definition.instructions,
        toolsets=[toolset_wrapper(toolset) if toolset_wrapper else toolset],
        output_type=[str, DeferredToolRequests],
        end_strategy="graceful",
        retries={"tools": 2, "output": 1},
        model_settings=model_settings,
        capabilities=capabilities,
    )

    @agent.output_validator
    def require_native_tool_calls(output: str | DeferredToolRequests):
        if isinstance(output, str) and textual_tool_call(output, selected_names):
            raise ModelRetry(
                "You serialized a function call as <invoke> text. Do not write tool-call markup. "
                "If a tool is needed, call it through the native structured tool interface supplied "
                "with this request; otherwise answer normally."
            )
        return output

    started = monotonic()
    timeout = asyncio.timeout(remaining)
    try:
        async with timeout:
            return await agent.run(
                prompt,
                deps=deps,
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
                conversation_id=deps.conversation_id,
                usage=budget.usage,
                usage_limits=UsageLimits(request_limit=budget.request_limit),
                event_stream_handler=event_stream_handler,
                cancellation_token=cancellation_token,
            )
    except TimeoutError as exc:
        if timeout.expired():
            raise ActiveTimeLimitExceeded("Active execution time limit exceeded") from exc
        raise
    finally:
        budget.active_seconds += monotonic() - started
