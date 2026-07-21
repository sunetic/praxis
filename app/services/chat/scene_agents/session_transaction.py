from __future__ import annotations

import json

from app.services.chat.agent import ChatCoreAgent
from app.services.platform.prompt_loader import PromptLoader
from .base import SceneAgentPayload


class SessionTransactionAgent(ChatCoreAgent):
    key = "session_transaction"
    default_tools = ("execute_sql",)
    default_skills = ("ob-session-ops",)

    def build_prompt_block(self, payload: SceneAgentPayload) -> str:
        return PromptLoader.render(
            "chat/prompts/scene_agents/session_transaction.tpl",
            key=payload.key,
            context_json=json.dumps(payload.context or {}, ensure_ascii=False),
            focus_object_json=json.dumps(payload.focus_object or {}, ensure_ascii=False),
            tools_block="",
        )
