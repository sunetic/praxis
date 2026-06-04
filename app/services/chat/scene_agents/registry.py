from __future__ import annotations

from typing import Iterable

from app.services.function.chat_agent import FunctionChatAgent
from app.services.chat.agent import ChatCoreAgent
from .sql_analysis import SqlAnalysisAgent

_extra_agents: list[ChatCoreAgent] = []


def register_scene_agent(agent: ChatCoreAgent) -> None:
    _extra_agents.append(agent)


class SceneAgentRegistry:
    def __init__(self, agents: Iterable[ChatCoreAgent] | None = None) -> None:
        resolved_agents = list(agents or [FunctionChatAgent(), SqlAnalysisAgent(), *_extra_agents])
        self._agent_map = {agent.key: agent for agent in resolved_agents if agent.key}

    def resolve(self, key: str) -> ChatCoreAgent | None:
        normalized = str(key or "").strip()
        if not normalized:
            return None
        return self._agent_map.get(normalized)
