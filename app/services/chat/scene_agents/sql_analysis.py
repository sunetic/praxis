from __future__ import annotations

import json

from app.services.chat.agent import ChatCoreAgent
from app.services.platform.prompt_loader import PromptLoader

from .base import SceneAgentPayload


class SqlAnalysisAgent(ChatCoreAgent):
    key = "sql_analysis"
    display_name = "SqlAnalysisAgent"
    default_tools = ("execute_sql", "explain_sql")
    default_skills = ("sql_analysis",)

    def build_prompt_block(self, payload: SceneAgentPayload) -> str:
        return PromptLoader.render(
            "chat/prompts/scene_agents/sql_analysis.tpl",
            key=payload.key,
            context_json=json.dumps(payload.context or {}, ensure_ascii=False),
            focus_object_json=json.dumps(payload.focus_object or {}, ensure_ascii=False),
            tools_block="",
        )
