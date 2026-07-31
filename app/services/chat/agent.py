from __future__ import annotations

import inspect
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.services.agent.core import AgentCore
from app.services.platform.prompt_loader import PromptLoader
from app.tools.registry import registry as tool_registry

from . import ChatService, get_chat_service

logger = get_logger("chat.agent")

if TYPE_CHECKING:
    from app.services.chat.scene_agents.base import SceneAgentPayload


class ChatScope(StrEnum):
    FUNCTION_BUILD = "function.build"
    PAGE_BUILD = "page.build"
    GENERAL_CHAT = "general.chat"


@dataclass(frozen=True)
class ScopedChatContext:
    scope: ChatScope
    conversation_id: int | None = None
    metadata: dict[str, Any] | None = None


class ChatCoreAgent:
    """
    System Agent base class — zero domain dependencies.

    Provides generic chat capabilities: streaming, tool/skill resolution,
    and prompt block injection. Domain-specific agents (FunctionChatAgent,
    StatsAnalysisAgent, SqlAnalysisAgent, etc.) inherit this class and
    override only scene-specific configuration.
    """

    key: str = ""
    default_tools: tuple[str, ...] = ()
    default_skills: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        chat_service: ChatService | None = None,
        agent_core: AgentCore | None = None,
    ) -> None:
        self._chat_service: ChatService = chat_service or get_chat_service()
        self._agent_core = agent_core or AgentCore()

    # ── Tool / Skill resolution ──────────────────────────────────────────

    def resolve_tools(self, payload: SceneAgentPayload) -> list[str]:
        requested = [item for item in payload.requested_tools if item]
        names = requested if requested else list(self.default_tools)
        valid = [n for n in names if tool_registry.get(n) is not None]
        unknown = set(names) - set(valid)
        if unknown:
            logger.warning(
                "resolve_tools[%s]: declared=%s valid=%s unknown=%s",
                self.key or "default",
                names,
                valid,
                unknown,
            )
        return valid

    def resolve_skills(self, payload: SceneAgentPayload) -> list[str]:
        requested = [item for item in payload.requested_skills if item]
        merged = list(dict.fromkeys([*self.default_skills, *requested]))
        return merged

    # ── Prompt block injection ───────────────────────────────────────────

    def build_prompt_block(self, payload: SceneAgentPayload) -> str:
        return PromptLoader.render(
            "chat/prompts/scene_agents/default.tpl",
            key=payload.key,
            context_json=json.dumps(payload.context or {}, ensure_ascii=False),
            focus_object_json=json.dumps(payload.focus_object or {}, ensure_ascii=False),
        )

    # ── Streaming ────────────────────────────────────────────────────────

    @property
    def display_name(self) -> str:
        """Human-friendly agent name for UI display."""
        _display_names: dict[str, str] = {
            "general": "ChatAgent",
            "function": "FunctionPlannerAgent",
            "function_build": "FunctionChatAgent",
            "page_build": "PageChatAgent",
            "session_transaction": "SessionAnalysisAgent",
            "stats_analysis": "StatsAnalysisAgent",
            "sql_analysis": "SqlAnalysisAgent",
        }
        return _display_names.get(self.key, self.key or "Assistant")

    async def stream_general_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        system_prompt: str | None,
        default_datasource_id: int | None,
        conversation_id: int | None,
        scope_context: dict[str, Any] | None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        chat_with_tools = self._chat_service.chat_with_tools
        chat_signature = inspect.signature(chat_with_tools)
        kwargs: dict[str, Any] = {
            "tools": tools,
            "system_prompt": system_prompt,
            "default_datasource_id": default_datasource_id,
            "conversation_id": conversation_id,
            "scope_context": scope_context,
            "use_state_machine": True,
            "agent_name": self.display_name,
        }
        if "is_cancelled" in chat_signature.parameters:
            kwargs["is_cancelled"] = is_cancelled
        async for event in chat_with_tools(messages, **kwargs):
            yield event


class ChatAgent(ChatCoreAgent):
    """
    Default System Agent for general chat — no domain-specific overrides.
    """

    pass
