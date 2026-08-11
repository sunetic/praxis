from __future__ import annotations

from typing import Any

from app.models import models
from app.services.agent.core import AgentCore, BuildGoalRequest, normalize_build_attempts
from app.services.chat import ChatService
from app.services.chat.agent import ChatCoreAgent
from app.services.page.scope_adapter import PageBuildScopeAdapter
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


class PageChatAgent(ChatCoreAgent):
    """
    System Agent for Page domain build conversations.

    Phase 1 keeps the page build workflow kernel in PageBuilderOrchestrator /
    BuildVerifyLoop and extracts only the page-scoped chat facade so build
    planning/apply logic no longer bypasses the System Agent layer entirely.
    """

    key = "page_build"
    display_name = "PageChatAgent"

    def __init__(
        self,
        *,
        chat_service: ChatService | None = None,
        agent_core: AgentCore | None = None,
        page_scope_adapter: PageBuildScopeAdapter | None = None,
    ) -> None:
        super().__init__(chat_service=chat_service)
        self._agent_core = agent_core or AgentCore()
        self._page_scope_adapter = page_scope_adapter or PageBuildScopeAdapter()

    def compose_page_build_goal(
        self,
        *,
        prompt: str,
        recent_contexts: list[dict[str, Any]] | None = None,
        conversation_context: str = "",
    ) -> str:
        return self._agent_core.compose_build_goal(
            adapter=self._page_scope_adapter,
            request=BuildGoalRequest(
                user_prompt=prompt,
                recent_contexts=normalize_build_attempts(recent_contexts),
                conversation_context=conversation_context,
            ),
        )

    def apply_page_goal(
        self,
        *,
        page: models.Page,
        goal: str,
        workspace_store: WorkspaceStore | None = None,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> CodingEngineApplyResult:
        store = workspace_store or WorkspaceStore()
        return self._page_scope_adapter.apply_goal(
            workspace_store=store,
            target=page,
            goal=goal,
            existing_functions=existing_functions,
        )
