"""Durable tool dispatch, wrapping the SDK's public toolset interface."""

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import RunContext, Tool, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry, ToolFailed
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from app.services.agent.definitions import RunDependencies
from app.services.agent.persistence import run_db
from app.services.agent.store import (
    CallChangedError,
    OutcomeUnknownError,
    ResourceBusyError,
    RunConflictError,
    RunStore,
)

JSON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Any)
TOOL_RETURN_ADAPTER = TypeAdapter(ToolReturn)


@dataclass(frozen=True, kw_only=True)
class ToolAccess:
    allowed: bool
    target: dict[str, Any]
    requires_approval: bool = False
    resource_key: str | None = None


@dataclass(frozen=True, kw_only=True)
class RegisteredTool:
    tool: Tool[RunDependencies]
    authorize: Callable[[RunContext[RunDependencies], dict[str, Any]], Awaitable[ToolAccess]]
    mutating: bool = False
    timeout_seconds: float = 120

    def __post_init__(self) -> None:
        if self.tool.requires_approval or self.tool.timeout is not None:
            raise ValueError("Durable tools use authorize/timeout_seconds, not native Tool flags")
        if self.mutating and not self.tool.sequential:
            raise ValueError("Mutating tools must be sequential")
        if self.mutating and not inspect.iscoroutinefunction(self.tool.function):
            raise ValueError(
                "Mutating tools must be async; background threads cannot be cancelled safely"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be finite and positive")


@dataclass
class DurableToolset(WrapperToolset[RunDependencies]):
    store: RunStore
    run_id: str
    owner_id: str
    registered: dict[str, RegisteredTool]

    @staticmethod
    def _replay(result: dict[str, Any]) -> Any:
        if result["outcome"] != "success":
            raise ToolFailed(result["content"])
        if result.get("native_tool_return"):
            return TOOL_RETURN_ADAPTER.validate_python(result["content"])
        return result["content"]

    @staticmethod
    def _auto_approval(
        scope: dict[str, Any], name: str, target: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Revalidate the exact conversation grant at dispatch time."""
        grant = scope.get("auto_approval")
        if not isinstance(grant, dict) or grant.get("tool_name") != name:
            return None
        datasource_id = grant.get("datasource_id")
        if (
            name != "request_database_change"
            or grant.get("expires_at", 0) <= time.time()
            or grant.get("agent_id") != scope.get("agent_id")
            or scope.get("datasource_ids") != [datasource_id]
            or target.get("datasource_id") != datasource_id
        ):
            return None
        return {
            "decided_by": "conversation_auto_approval",
            "expires_at": grant["expires_at"],
            "tool_name": name,
            "agent_id": grant.get("agent_id"),
            "datasource_id": datasource_id,
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunDependencies],
        tool: ToolsetTool[RunDependencies],
    ) -> Any:
        entry = self.registered[name]
        access = await entry.authorize(ctx, tool_args)
        if entry.mutating and access.allowed and not access.resource_key:
            raise RunConflictError("Mutating tool has no resolved resource lock key")
        if not ctx.tool_call_id:
            raise RunConflictError("Native call identity is required")
        auto_approval = (
            self._auto_approval(ctx.deps.scope, name, access.target)
            if entry.mutating and access.allowed and access.requires_approval
            else None
        )
        try:
            call = await run_db(
                self.store.prepare_call,
                self.run_id,
                self.owner_id,
                call_id=ctx.tool_call_id,
                name=name,
                # The SDK has already validated nested arguments into Python
                # objects (including BaseModel). Persist their JSON values for
                # fingerprints/replay while passing the typed values to tools.
                arguments=JSON_ADAPTER.dump_python(tool_args, mode="json"),
                target=JSON_ADAPTER.dump_python(access.target, mode="json"),
                mutating=entry.mutating,
                resource_key=access.resource_key,
                needs_approval=access.requires_approval and access.allowed and not auto_approval,
                history=ctx.messages,
                auto_approval=auto_approval,
            )
        except CallChangedError as exc:
            result = {
                "outcome": "failed",
                "content": "The resolved target changed; the recorded approval is invalid. No new operation was dispatched.",
            }
            await run_db(
                self.store.finish_call, self.run_id, self.owner_id, ctx.tool_call_id, result
            )
            raise ToolFailed(result["content"]) from exc
        if call["status"] in {"executing", "outcome_unknown"}:
            raise OutcomeUnknownError("Dispatched call has no known result; do not replay")
        if not access.allowed:
            result = {
                "outcome": "failed",
                "content": "Resource authorization denied; no operation was dispatched.",
            }
            await run_db(
                self.store.finish_call, self.run_id, self.owner_id, ctx.tool_call_id, result
            )
            raise ToolFailed(result["content"])
        if call["result"] is not None:
            return self._replay(call["result"])
        if call["status"] == "waiting_approval" and not ctx.tool_call_approved:
            raise ApprovalRequired(
                metadata={"fingerprint": call["fingerprint"], "target": access.target}
            )
        try:
            claimed = await run_db(
                self.store.claim_call,
                self.run_id,
                self.owner_id,
                ctx.tool_call_id,
                approved_by_framework=ctx.tool_call_approved,
            )
        except ResourceBusyError as exc:
            result = {
                "outcome": "failed",
                "content": "This resource is busy or has an unresolved operation. No operation was dispatched; do not automatically retry.",
            }
            await run_db(
                self.store.finish_call, self.run_id, self.owner_id, ctx.tool_call_id, result
            )
            raise ToolFailed(result["content"]) from exc
        if claimed["result"] is not None:
            return self._replay(claimed["result"])
        try:
            async with asyncio.timeout(entry.timeout_seconds):
                result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
            stored = {
                "outcome": "success",
                "content": JSON_ADAPTER.dump_python(result, mode="json"),
                "native_tool_return": isinstance(result, ToolReturn),
            }
            await run_db(
                self.store.finish_call, self.run_id, self.owner_id, ctx.tool_call_id, stored
            )
            return result
        except (ToolFailed, ModelRetry) as exc:
            await run_db(
                self.store.finish_call,
                self.run_id,
                self.owner_id,
                ctx.tool_call_id,
                {"outcome": "failed", "content": str(exc)},
            )
            raise
        except BaseException as exc:
            # The external operation may have happened, even when cancellation or
            # result serialization/commit failed. Never retry such a write here.
            failure: dict[str, Any] = {
                "outcome": "failed",
                "content": "Operation outcome is unknown; reconciliation is required."
                if entry.mutating
                else "Tool execution was interrupted or failed.",
            }
            if isinstance(exc, OutcomeUnknownError) and exc.details is not None:
                failure["diagnostics"] = exc.details
            try:
                await run_db(
                    self.store.finish_call,
                    self.run_id,
                    self.owner_id,
                    ctx.tool_call_id,
                    failure,
                    unknown=entry.mutating,
                )
            except Exception:
                # A lost lease must not let the previous worker overwrite recovery.
                raise exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            if entry.mutating:
                raise OutcomeUnknownError(failure["content"]) from exc
            if isinstance(exc, TimeoutError):
                raise ToolFailed("Read-only tool timed out.") from exc
            if isinstance(exc, Exception):
                raise ToolFailed("Read-only tool failed. No write was dispatched.") from exc
            raise
