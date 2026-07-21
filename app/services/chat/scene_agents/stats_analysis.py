from __future__ import annotations

import json

from app.services.chat.agent import ChatCoreAgent
from app.services.platform.prompt_loader import PromptLoader
from .base import SceneAgentPayload


class StatsAnalysisAgent(ChatCoreAgent):
    key = "stats_analysis"
    default_tools = (
        "execute_sql",
        "explain_sql",
        "object_crud",
        "exec_command",
        "call_praxis_service",
    )
    default_skills = ("ob-stats-ops", "ocp-api-guide")

    def build_prompt_block(self, payload: SceneAgentPayload) -> str:
        focus = payload.focus_object if isinstance(payload.focus_object, dict) else {}
        context = payload.context if isinstance(payload.context, dict) else {}
        datasource = context.get("datasource") if isinstance(context.get("datasource"), dict) else {}
        has_locked_datasource = isinstance(datasource.get("id"), int) and datasource.get("id") > 0
        has_locked_table = bool(str(focus.get("table_name") or "").strip())
        has_locked_object = has_locked_datasource and has_locked_table
        return PromptLoader.render(
            "chat/prompts/scene_agents/stats_analysis.tpl",
            key=payload.key,
            context_json=json.dumps(context, ensure_ascii=False),
            focus_object_json=json.dumps(focus, ensure_ascii=False),
            has_locked_object=has_locked_object,
            tools_block="",
        )
