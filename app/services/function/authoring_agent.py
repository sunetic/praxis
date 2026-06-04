from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.models import models
from app.services.function.builder import FunctionBuildRunResult, FunctionBuilderService
from app.services.function.runtime import FunctionRuntimeResult, FunctionRuntimeService


@dataclass(frozen=True)
class FunctionBuildCommand:
    prompt: str
    ambiguity_mode: str = "default"


@dataclass(frozen=True)
class FunctionSuggestInputCommand:
    prompt: str
    conversation_context: str = ""


@dataclass(frozen=True)
class FunctionInvokeCommand:
    payload: dict[str, Any]
    runtime_path: str
    datasource_id: int | None
    scope_metadata: dict[str, Any]
    timeout_seconds: float
    trace_id: str


class FunctionAuthoringAgent:
    """
    Domain agent for Function authoring workflows.

    This agent isolates Function build/suggest/invoke context and keeps API handlers
    focused on transport and validation only.
    """

    def __init__(
        self,
        *,
        builder_factory: Callable[[], FunctionBuilderService] | None = None,
        runtime_factory: Callable[[sessionmaker[Session]], FunctionRuntimeService] | None = None,
    ) -> None:
        self._builder_factory = builder_factory or FunctionBuilderService
        self._runtime_factory = runtime_factory or (lambda sf: FunctionRuntimeService(session_factory=sf))

    def build_draft(
        self,
        *,
        function: models.Function,
        command: FunctionBuildCommand,
    ) -> FunctionBuildRunResult:
        builder = self._builder_factory()
        return builder.build_run(
            current_code=function.draft_code,
            current_dependencies=function.draft_dependencies,
            prompt=command.prompt,
            function_name=function.name,
            ambiguity_mode=command.ambiguity_mode,
        )

    def suggest_input(
        self,
        *,
        function: models.Function,
        command: FunctionSuggestInputCommand,
    ) -> dict[str, Any]:
        builder = self._builder_factory()
        return builder.suggest_invocation_input(
            prompt=command.prompt,
            function_name=function.name,
            current_dependencies=function.draft_dependencies,
            current_code=function.draft_code,
            conversation_context=command.conversation_context,
        )

    async def invoke(
        self,
        *,
        function: models.Function,
        runtime_session_factory: sessionmaker[Session],
        command: FunctionInvokeCommand,
    ) -> FunctionRuntimeResult:
        runtime = self._runtime_factory(runtime_session_factory)
        try:
            return await runtime.invoke(
                function,
                payload=command.payload,
                runtime_path=command.runtime_path,
                datasource_id=command.datasource_id,
                scope_metadata=command.scope_metadata,
                timeout_seconds=command.timeout_seconds,
                trace_id=command.trace_id,
            )
        finally:
            runtime._executor.shutdown(cancel_futures=True)
