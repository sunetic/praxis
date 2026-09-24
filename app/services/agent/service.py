"""Application-owned run tasks. Subscriptions never own or restart execution."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Any

from pydantic_ai import (
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    ToolDenied,
    capture_run_messages,
)
from pydantic_ai.exceptions import RunCancelled, UsageLimitExceeded
from pydantic_ai.models import Model

from app.services.agent.context import ContextLimitExceeded, ContextManager
from app.services.agent.context_budget import ContextPolicy
from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.events import RunEvents
from app.services.agent.execution import DurableToolset, RegisteredTool
from app.services.agent.models import ModelSnapshot
from app.services.agent.persistence import run_db
from app.services.agent.runtime import run_agent
from app.services.agent.store import (
    BUDGET_ADAPTER,
    DEFERRED_ADAPTER,
    TERMINAL_STATUSES,
    LeaseLostError,
    OutcomeUnknownError,
    RunStore,
)

logger = logging.getLogger(__name__)


class AgentRunService:
    def __init__(
        self,
        store: RunStore,
        model_factory: Callable[[dict[str, Any]], Awaitable[Model]],
        tools: dict[str, RegisteredTool],
        *,
        capabilities_for_run: Callable[[dict[str, Any]], frozenset[str]],
        model_snapshot_factory: Callable[[], dict[str, Any]] | None = None,
        poll_seconds: float = 0.1,
    ):
        self.store, self.model_factory, self.tools = store, model_factory, dict(tools)
        self.poll_seconds = poll_seconds
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.capabilities_for_run = capabilities_for_run
        self.model_snapshot_factory = model_snapshot_factory
        self._wake = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._closing = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._dispatcher is not None:
            return
        self._closing = False
        self._loop = asyncio.get_running_loop()
        await run_db(self.store.recover_expired)
        self._dispatcher = asyncio.create_task(self._dispatch(), name="agent-run-dispatcher")

    async def close(self) -> None:
        self._closing = True
        if self._dispatcher:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
            self._dispatcher = None
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop = None

    def _notify(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def submit(
        self,
        conversation_id: str,
        actor_id: str,
        client_request_id: str,
        prompt: str,
        definition: AgentDefinition,
        *,
        stop_and_modify: bool = False,
    ) -> dict[str, Any]:
        row = self.store.submit(
            conversation_id,
            actor_id,
            client_request_id,
            prompt,
            definition,
            stop_and_modify=stop_and_modify,
            model_snapshot_factory=self.model_snapshot_factory,
        )
        self._notify()
        return row

    def approve(
        self, run_id: str, actor_id: str, call_id: str, fingerprint: str, approved: bool
    ) -> dict[str, Any]:
        decision = self.store.decide(run_id, actor_id, call_id, fingerprint, approved)
        self._notify()
        return decision

    def cancel(self, run_id: str, actor_id: str) -> dict[str, Any]:
        row = self.store.cancel(run_id, actor_id)
        if token := self._tokens.get(run_id):
            if self._loop is not None:
                self._loop.call_soon_threadsafe(token.cancel)
        self._notify()
        return row

    def resume(self, run_id: str, actor_id: str, expected_event_seq: int) -> dict[str, Any]:
        row = self.store.resume(run_id, actor_id, expected_event_seq)
        self._notify()
        return row

    async def subscribe(
        self, run_id: str, actor_id: str, after: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        await run_db(self.store.get, run_id, actor_id)
        while True:
            batch = await run_db(self.store.read_events, run_id, actor_id, after)
            for event in batch:
                after = event["seq"]
                yield event
            row = await run_db(self.store.get, run_id, actor_id)
            if row["status"] in TERMINAL_STATUSES and after >= row["event_seq"]:
                return
            await asyncio.sleep(self.poll_seconds)

    async def _dispatch(self) -> None:
        while not self._closing:
            self._wake.clear()
            try:
                await run_db(self.store.recover_expired)
                for run_id in await run_db(self.store.candidates):
                    if run_id in self._tasks:
                        continue
                    owner_id = uuid.uuid4().hex
                    if row := await run_db(self.store.claim, run_id, owner_id):
                        token = self._tokens[run_id] = CancellationToken()
                        self._tasks[run_id] = asyncio.create_task(
                            self._execute(row, owner_id, token), name=f"agent-run:{run_id}"
                        )
            except Exception:
                logger.exception("agent_run_dispatch_failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _heartbeat(self, run_id: str, owner_id: str, token: CancellationToken) -> None:
        while True:
            await asyncio.sleep(min(self.poll_seconds, self.store.lease_seconds / 3))
            try:
                if await run_db(self.store.heartbeat, run_id, owner_id):
                    token.cancel()
            except LeaseLostError:
                token.cancel()
                return
            except Exception:
                token.cancel()
                logger.error("agent_run_heartbeat_failed run_id=%s", run_id)
                return

    async def _execute(self, row: dict[str, Any], owner_id: str, token: CancellationToken) -> None:
        run_id = row["id"]
        heartbeat = asyncio.create_task(self._heartbeat(run_id, owner_id, token))
        budget, history = None, []
        status, output, pending, error_code = "failed", None, None, None
        captured = []
        try:
            budget = BUDGET_ADAPTER.validate_python(row["budget"])
            history = await run_db(self.store.history, run_id, row["actor_id"])
            definition = AgentDefinition(
                **{**row["definition"], "tool_names": frozenset(row["definition"]["tool_names"])}
            )
            # SDK construction lazily imports provider resources and may load
            # SSL roots. Keep cold starts off the loop that renews run leases.
            model = await self.model_factory(row)
            deps = RunDependencies(
                run_id=run_id,
                conversation_id=row["conversation_id"],
                actor_id=row["actor_id"],
                scope=definition.scope,
            )
            authorized_tool_names = await run_db(self.capabilities_for_run, row)
            observer = RunEvents(
                self.store,
                run_id,
                owner_id,
                budget,
                row["message_offset"],
            )
            profile = (
                ModelSnapshot.model_validate(row["model_snapshot"]).configuration
                if row.get("model_snapshot")
                else None
            )
            context = ContextManager(
                policy=profile or ContextPolicy(),
                store=self.store,
                run_id=run_id,
                owner_id=owner_id,
                budget=budget,
                budget_snapshot=observer.current_budget,
            )
            deferred = None
            if row["pending"]:
                previous = DEFERRED_ADAPTER.validate_python(row["pending"])
                decisions = await run_db(self.store.approval_decisions, run_id, owner_id)
                deferred = DeferredToolResults(
                    approvals={
                        part.tool_call_id: True
                        if decisions[part.tool_call_id]
                        else ToolDenied("The user denied this action.")
                        for part in previous.approvals
                    }
                )
            with capture_run_messages() as captured:
                result = await run_agent(
                    definition=definition,
                    model=model,
                    deps=deps,
                    tools={name: entry.tool for name, entry in self.tools.items()},
                    authorized_tool_names=authorized_tool_names,
                    budget=budget,
                    prompt=None
                    if deferred or len(history) > row["message_offset"]
                    else row["prompt"],
                    message_history=history,
                    deferred_tool_results=deferred,
                    event_stream_handler=observer.handle,
                    cancellation_token=token,
                    capabilities=[context, observer],
                    model_settings=profile.request_settings() if profile else None,
                    toolset_wrapper=partial(
                        DurableToolset,
                        store=self.store,
                        run_id=run_id,
                        owner_id=owner_id,
                        registered=self.tools,
                    ),
                )
            history = result.all_messages()
            if isinstance(result.output, DeferredToolRequests):
                if result.output.calls:
                    raise ValueError("External deferred execution is not configured")
                status, pending = "waiting_approval", result.output
            else:
                status, output = "finished", result.output
        except (RunCancelled, asyncio.CancelledError):
            status = "interrupted" if self._closing else "cancelled"
        except ContextLimitExceeded:
            status, error_code = "limited", "context_limit"
        except UsageLimitExceeded:
            status, error_code = "limited", "resource_limit"
        except (OutcomeUnknownError, LeaseLostError):
            status, error_code = "interrupted", "reconciliation_required"
        except Exception:
            status, error_code = "failed", "runtime_error"
            logger.exception("agent_run_failed run_id=%s", run_id)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                if budget is None:
                    # Corrupt persisted state must expire into interruption, not
                    # acquire a fresh budget or overwrite the original history.
                    raise ValueError("Stored execution budget is invalid")
                await run_db(
                    self.store.finish,
                    run_id,
                    owner_id,
                    status=status,
                    history=captured or history,
                    budget=budget,
                    output=output,
                    pending=pending,
                    error_code=error_code,
                )
            except LeaseLostError:
                pass
            except Exception:
                # Leave the lease to expire; never report success without persistence.
                logger.error("agent_run_final_persistence_failed run_id=%s", run_id)
            finally:
                self._tasks.pop(run_id, None)
                self._tokens.pop(run_id, None)
                self._wake.set()
