from __future__ import annotations

import inspect
import json
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.services.platform.prompt_loader import PromptLoader
from app.tools.registry import registry as tool_registry

from . import ChatService, get_chat_service

logger = get_logger("chat.agent")

if TYPE_CHECKING:
    from app.services.chat.scene_agents.base import SceneAgentPayload


class ChatCoreAgent:
    """
    System Agent base class — zero domain dependencies.

    Provides generic chat capabilities: streaming, tool/skill resolution,
    and prompt block injection. Domain-specific agents (FunctionChatAgent,
    StatsAnalysisAgent, SqlAnalysisAgent, etc.) inherit this class and
    override only scene-specific configuration.
    """

    key: str = ""
    display_name: str = "Assistant"
    default_tools: tuple[str, ...] = ()
    default_skills: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        chat_service: ChatService | None = None,
    ) -> None:
        self._chat_service: ChatService = chat_service or get_chat_service()

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

    async def stream_general_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        system_prompt: str | None,
        default_datasource_id: int | None,
        conversation_id: int | None,
        scope_context: dict[str, Any] | None,
        context_window_tokens: int | None = None,
        compression_threshold_tokens: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        task_state: dict[str, Any] | None = None,
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
        if "context_window_tokens" in chat_signature.parameters:
            kwargs["context_window_tokens"] = context_window_tokens
        if "compression_threshold_tokens" in chat_signature.parameters:
            kwargs["compression_threshold_tokens"] = compression_threshold_tokens
        if "is_cancelled" in chat_signature.parameters:
            kwargs["is_cancelled"] = is_cancelled
        if "task_state" in chat_signature.parameters:
            kwargs["task_state"] = task_state
        async for event in chat_with_tools(messages, **kwargs):
            yield event


class ChatAgent(ChatCoreAgent):
    """
    Default System Agent for general chat — no domain-specific overrides.
    """

    display_name = "Assistant"
