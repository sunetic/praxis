from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SceneAgentPayload:
    key: str
    context: dict[str, Any]
    focus_object: dict[str, Any] | None
    requested_tools: list[str]
    requested_skills: list[str]
    source: str = "scene_agent"
