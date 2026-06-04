from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models import models
from app.services.agent.core import AgentCore, BuildGoalRequest, normalize_build_attempts
from app.services.chat import ChatService
from app.services.chat.agent import ChatCoreAgent, ScopedChatContext
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.function.authoring_agent import (
    FunctionAuthoringAgent,
    FunctionBuildCommand,
    FunctionInvokeCommand,
    FunctionSuggestInputCommand,
)
from app.services.function.builder import FunctionBuildRunResult
from app.services.function.runtime import FunctionRuntimeResult
from app.services.function.scope_adapter import FunctionBuildScopeAdapter
from app.services.platform.workspace_store import WorkspaceStore


class FunctionChatAgent(ChatCoreAgent):
    """
    System Agent for Function domain — build, invoke, suggest input.
    """

    key = "function_build"

    def __init__(
        self,
        *,
        function_authoring_agent: FunctionAuthoringAgent | None = None,
        chat_service: ChatService | None = None,
        agent_core: AgentCore | None = None,
        function_scope_adapter: FunctionBuildScopeAdapter | None = None,
    ) -> None:
        super().__init__(chat_service=chat_service, agent_core=agent_core)
        self._function_authoring_agent = function_authoring_agent or FunctionAuthoringAgent()
        self._function_scope_adapter = function_scope_adapter or FunctionBuildScopeAdapter()

    def compose_function_build_goal(
        self,
        *,
        prompt: str,
        recent_contexts: list[dict[str, Any]] | None = None,
        conversation_context: str = "",
        skill_context: str = "",
    ) -> str:
        return self._agent_core.compose_build_goal(
            adapter=self._function_scope_adapter,
            request=BuildGoalRequest(
                user_prompt=prompt,
                recent_contexts=normalize_build_attempts(recent_contexts),
                conversation_context=conversation_context,
                skill_context=skill_context,
            ),
        )

    def build_function_draft(
        self,
        *,
        function: models.Function,
        prompt: str,
        ambiguity_mode: str,
        context: ScopedChatContext | None = None,
    ) -> FunctionBuildRunResult:
        _ = context
        return self._function_authoring_agent.build_draft(
            function=function,
            command=FunctionBuildCommand(prompt=prompt, ambiguity_mode=ambiguity_mode),
        )

    def apply_function_goal(
        self,
        *,
        function: models.Function,
        goal: str,
        workspace_store: WorkspaceStore | None = None,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        store = workspace_store or WorkspaceStore()
        return self._function_scope_adapter.apply_goal(
            workspace_store=store,
            target=function,
            goal=goal,
            datasource_schema=datasource_schema,
            datasource_id=datasource_id,
        )

    def suggest_function_input(
        self,
        *,
        function: models.Function,
        prompt: str,
        conversation_context: str,
        context: ScopedChatContext | None = None,
    ) -> dict[str, Any]:
        _ = context
        return self._function_authoring_agent.suggest_input(
            function=function,
            command=FunctionSuggestInputCommand(
                prompt=prompt,
                conversation_context=conversation_context,
            ),
        )

    async def invoke_function(
        self,
        *,
        function: models.Function,
        runtime_session_factory: sessionmaker[Session],
        command: FunctionInvokeCommand,
        context: ScopedChatContext | None = None,
    ) -> FunctionRuntimeResult:
        _ = context
        return await self._function_authoring_agent.invoke(
            function=function,
            runtime_session_factory=runtime_session_factory,
            command=command,
        )
