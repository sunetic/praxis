from __future__ import annotations

import json

from app.services.chat.agent import ChatCoreAgent
from app.services.chat.scene_agents.base import SceneAgentPayload
from app.services.platform.prompt_loader import PromptLoader


class SkillBuilderAgent(ChatCoreAgent):
    key = "skill_builder"
    default_tools: tuple[str, ...] = ()
    default_skills: tuple[str, ...] = ()

    def build_prompt_block(self, payload: SceneAgentPayload) -> str:
        return PromptLoader.render(
            "chat/prompts/scene_agents/skill_builder.tpl",
            key=payload.key,
            context_json=json.dumps(payload.context or {}, ensure_ascii=False),
        )
