"""Persist native request boundaries and project text without exposing thinking."""

import asyncio
from dataclasses import replace
from time import monotonic

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)

from app.services.agent.definitions import RunDependencies
from app.services.agent.persistence import run_db
from app.services.agent.protocol import textual_tool_call
from app.services.agent.runtime import ExecutionBudget
from app.services.agent.store import RunStore


class RunEvents(AbstractCapability[RunDependencies]):
    def __init__(
        self,
        store: RunStore,
        run_id: str,
        owner_id: str,
        budget: ExecutionBudget,
        offset: int,
        tool_names: frozenset[str] = frozenset(),
    ):
        self.store, self.run_id, self.owner_id = store, run_id, owner_id
        self.budget, self.offset = budget, offset
        self.started = monotonic()
        self.initial_seconds = budget.active_seconds
        self.message_id: str | None = None
        self.tool_names = tool_names

    def current_budget(self, *, reserve_request: bool = False) -> ExecutionBudget:
        usage = replace(self.budget.usage)
        if reserve_request:
            usage.requests += 1
        return ExecutionBudget(
            request_limit=self.budget.request_limit,
            active_seconds_limit=self.budget.active_seconds_limit,
            active_seconds=self.initial_seconds + monotonic() - self.started,
            usage=usage,
        )

    async def before_model_request(self, ctx, request_context):
        self.message_id = f"{self.run_id}:{len(ctx.messages) - self.offset}"
        await run_db(
            self.store.checkpoint,
            self.run_id,
            self.owner_id,
            ctx.messages,
            self.current_budget(),
        )
        return request_context

    async def wrap_model_request(self, ctx, *, request_context, handler):
        # Context compaction wraps this capability, so model waiting starts only
        # after the actual capacity work has finished.
        await run_db(
            self.store.checkpoint,
            self.run_id,
            self.owner_id,
            ctx.messages,
            self.current_budget(reserve_request=True),
        )
        await run_db(
            self.store.emit,
            self.run_id,
            self.owner_id,
            "request_started",
            {"message_id": self.message_id},
        )
        requests_before = self.budget.usage.requests
        try:
            return await handler(request_context)
        finally:
            # Streaming SDK requests are counted only after the connection opens.
            # A transport failure before that is still an attempted request.
            if request_context.streaming and self.budget.usage.requests == requests_before:
                self.budget.usage.requests += 1

    async def after_model_request(self, ctx, *, request_context, response: ModelResponse):
        history = list(ctx.messages)
        if not history or history[-1] is not response:
            history.append(response)
        await run_db(
            self.store.checkpoint, self.run_id, self.owner_id, history, self.current_budget()
        )
        await run_db(
            self.store.emit,
            self.run_id,
            self.owner_id,
            "assistant_message_end",
            {"message_id": self.message_id},
        )
        await run_db(
            self.store.emit,
            self.run_id,
            self.owner_id,
            "request_finished",
            {"message_id": self.message_id, "finish_reason": response.finish_reason},
        )
        return response

    async def handle(self, ctx, stream):
        # One consumer preserves native event order. A pending read survives the
        # flush deadline, so a quiet provider cannot strand its last text chunk.
        buffer, full_text, part_id, deadline, discarded = "", "", None, None, False

        async def flush():
            nonlocal buffer, deadline
            if buffer and not discarded:
                await run_db(
                    self.store.emit,
                    self.run_id,
                    self.owner_id,
                    "assistant_delta",
                    {"message_id": self.message_id, "part_id": part_id, "text": buffer},
                )
                buffer, deadline = "", None

        iterator = aiter(stream)
        pending = asyncio.create_task(anext(iterator))
        try:
            while True:
                timeout = max(0, deadline - monotonic()) if deadline is not None else None
                ready, _ = await asyncio.wait({pending}, timeout=timeout)
                if not ready:
                    await flush()
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                text = None
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    text = event.part.content
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    text = event.delta.content_delta
                if text:
                    full_text += text
                    if not discarded and textual_tool_call(full_text, self.tool_names):
                        discarded = True
                        buffer, deadline = "", None
                        await run_db(
                            self.store.emit,
                            self.run_id,
                            self.owner_id,
                            "assistant_message_discarded",
                            {"message_id": self.message_id, "reason": "textual_tool_call"},
                        )
                        pending = asyncio.create_task(anext(iterator))
                        continue
                    if discarded:
                        pending = asyncio.create_task(anext(iterator))
                        continue
                    if part_id != str(event.index):
                        await flush()
                        part_id = str(event.index)
                    if deadline is None:
                        deadline = monotonic() + 0.05
                    buffer += text
                    while len(buffer.encode("utf-8")) >= 4096:
                        chunk = buffer.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
                        remainder = buffer[len(chunk) :]
                        buffer = chunk
                        await flush()
                        buffer = remainder
                        deadline = monotonic() + 0.05 if buffer else None
                else:
                    await flush()
                    if (
                        isinstance(event, FunctionToolResultEvent)
                        and isinstance(event.part, ToolReturnPart)
                        and event.part.outcome == "denied"
                    ):
                        # Native denied approvals skip the executor entirely.
                        await run_db(
                            self.store.finish_call,
                            self.run_id,
                            self.owner_id,
                            event.part.tool_call_id,
                            {"outcome": "denied", "content": event.part.content},
                        )
                pending = asyncio.create_task(anext(iterator))
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await flush()
