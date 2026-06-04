from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilitySummary:
    kind: str
    name: str
    purpose: str
    availability: str
    status: str | None = None
    hints: tuple[str, ...] = ()
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityContext:
    sections: dict[str, list[CapabilitySummary]]


@dataclass(frozen=True)
class CapabilityBuildInput:
    tools: list[dict]
    declared_tool_names: list[str]
    datasource: Any | None = None
    services: list[Any] = field(default_factory=list)
    knowledge_bases: list[Any] = field(default_factory=list)
    active_skills: list[Any] = field(default_factory=list)
    scene_key: str | None = None
    scene_focus: dict[str, Any] | None = None
    scope_context: dict[str, Any] | None = None
