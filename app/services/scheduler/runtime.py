from __future__ import annotations

import inspect
import json
import os
import signal
from datetime import datetime
from typing import Any, Callable, Awaitable

from sqlalchemy.orm import Session, sessionmaker

from app.models import models
from app.services.agent.scheduled_runner import ScheduledAgentRunner
from app.services.function.runtime import FunctionRuntimeResult, FunctionRuntimeService
from app.services.scheduler.result import ScheduleRuntimeResult

ScheduleHandler = Callable[..., Awaitable[ScheduleRuntimeResult]]
_schedule_type_handlers: dict[str, ScheduleHandler] = {}


def register_schedule_type_handler(target_type: str, handler: ScheduleHandler) -> None:
    _schedule_type_handlers[target_type] = handler


class ScheduleTargetRuntimeService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Any,
        function_runtime_service: FunctionRuntimeService | None = None,
        agent_runtime_service: ScheduledAgentRunner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._function_runtime = function_runtime_service or FunctionRuntimeService(session_factory=session_factory)
        self._agent_runtime = agent_runtime_service or ScheduledAgentRunner(session_factory=session_factory)

    async def invoke_schedule(
        self,
        schedule: models.Schedule,
        *,
        trigger_type: str,
        trace_id: str | None = None,
    ) -> ScheduleRuntimeResult:
        target_type = str(schedule.target_type or "function").strip().lower()
        payload = dict(schedule.input_payload or {})
        effective_datasource_id = self._resolve_schedule_datasource_id(schedule, payload=payload)

        handler = _schedule_type_handlers.get(target_type)
        if handler is not None:
            return await handler(
                self,
                schedule,
                payload=payload,
                trigger_type=trigger_type,
                trace_id=trace_id,
            )

        if target_type == "agent":
            agent = self._load_agent(schedule)
            if agent is None:
                return ScheduleRuntimeResult(
                    run_id="",
                    status="failed",
                    output=None,
                    output_summary=None,
                    error_class="validation",
                    error_message=f"Agent {schedule.target_id} not found",
                    duration_ms=0,
                )
            return await self._agent_runtime.invoke(
                agent=agent,
                prompt=str(schedule.input_prompt or ""),
                trace_id=trace_id,
                datasource_id=effective_datasource_id,
            )

        if target_type != "function":
            return ScheduleRuntimeResult(
                run_id="",
                status="failed",
                output=None,
                output_summary=None,
                error_class="validation",
                error_message=f"Unknown schedule target_type: {target_type}",
                duration_ms=0,
            )

        function = self._load_function(schedule)
        if function is None:
            return ScheduleRuntimeResult(
                run_id="",
                status="failed",
                output=None,
                output_summary=None,
                error_class="validation",
                error_message=f"Function {schedule.target_id or schedule.function_id} not found",
                duration_ms=0,
            )
        payload.update(
            {
                "schedule_id": schedule.id,
                "trigger_type": trigger_type,
                "trace_id": trace_id,
            }
        )
        function_result = await self._invoke_function_runtime(
            function=function,
            payload=payload,
            datasource_id=effective_datasource_id,
            trigger_type=trigger_type,
            trace_id=trace_id,
            schedule_id=schedule.id,
        )
        return self._normalize_function_result(function_result)

    def shutdown(self) -> None:
        executor = getattr(self._function_runtime, "_executor", None)
        if executor is None:
            return
        # Kill worker processes immediately so Ctrl-C / SIGINT is not blocked
        # by in-flight subprocess work. ProcessPoolExecutor.shutdown(wait=True)
        # blocks until workers finish; cancel_futures=True only prevents new
        # submissions but does not terminate running processes.
        pids = [p.pid for p in getattr(executor, "_processes", {}).values() if p.is_alive()]
        executor.shutdown(wait=False, cancel_futures=True)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _load_function(self, schedule: models.Schedule) -> models.Function | None:
        preloaded_function = getattr(schedule, "__dict__", {}).get("function")
        if preloaded_function is not None:
            return preloaded_function
        function_id = schedule.function_id or schedule.target_id
        if function_id is None:
            return None
        db = self._session_factory()
        try:
            return db.query(models.Function).filter(models.Function.id == function_id).first()
        finally:
            db.close()

    def _load_agent(self, schedule: models.Schedule) -> models.Agent | None:
        if schedule.target_id is None:
            return None
        db = self._session_factory()
        try:
            return db.query(models.Agent).filter(models.Agent.id == schedule.target_id).first()
        finally:
            db.close()

    def _normalize_function_result(self, result: FunctionRuntimeResult) -> ScheduleRuntimeResult:
        return ScheduleRuntimeResult(
            run_id=result.run_id,
            status=result.status,
            output=result.output,
            output_summary=self._summarize_output(result.output),
            error_class=result.error_class,
            error_message=result.error_message,
            duration_ms=result.duration_ms,
        )

    async def _invoke_function_runtime(
        self,
        *,
        function: models.Function,
        payload: dict[str, Any],
        datasource_id: int | None,
        trigger_type: str,
        trace_id: str | None,
        schedule_id: int,
    ) -> FunctionRuntimeResult:
        invoke_kwargs: dict[str, Any] = {
            "payload": payload,
            "datasource_id": datasource_id,
            "scope_metadata": {
                "scope_type": "scheduler",
                "schedule_id": schedule_id,
                "trigger_type": trigger_type,
                "execution_mode": "apply",
            },
            "timeout_seconds": 30.0,
            "trace_id": trace_id,
        }
        try:
            signature = inspect.signature(self._function_runtime.invoke)
            allowed = set(signature.parameters.keys())
        except (TypeError, ValueError):
            allowed = set(invoke_kwargs.keys()) | {"function"}
        filtered_kwargs = {key: value for key, value in invoke_kwargs.items() if key in allowed}
        return await self._function_runtime.invoke(function, **filtered_kwargs)

    def _summarize_output(self, output: Any | None) -> str | None:
        if output is None:
            return None
        if isinstance(output, str):
            return output[:1000]
        try:
            return json.dumps(output, ensure_ascii=False, default=str)[:1000]
        except Exception:
            return str(output)[:1000]

    def _resolve_schedule_datasource_id(
        self,
        schedule: models.Schedule,
        *,
        payload: dict[str, Any] | None = None,
    ) -> int | None:
        schedule_datasource_id = getattr(schedule, "datasource_id", None)
        if isinstance(schedule_datasource_id, int):
            return schedule_datasource_id
        effective_payload = payload if isinstance(payload, dict) else {}
        raw_payload_datasource_id = effective_payload.get("datasource_id")
        if isinstance(raw_payload_datasource_id, int):
            return raw_payload_datasource_id
        return None
