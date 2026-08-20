"""Task contract domain model and non-semantic message normalization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AcceptanceCriterion:
    id: str
    description: str
    required: bool = True
    requires_tool_evidence: bool = False
    required_tool_outcome: str = "any"
    component_hints: list[str] = field(default_factory=list)
    source_excerpt: str = ""


@dataclass
class TaskContract:
    objective: str
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    output_requirements: list[str] = field(default_factory=list)
    authorization_scope: str = "Use only the tools and scope granted by the current session."
    complex: bool = False
    high_value: bool = False

    @classmethod
    def unclassified(
        cls,
        messages: list[dict[str, Any]],
        *,
        conservative: bool = False,
    ) -> TaskContract:
        """Create a contract without making semantic claims about the request."""
        return cls(
            objective=latest_user_text(messages).strip(),
            complex=conservative,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskContract:
        criteria = [
            AcceptanceCriterion(
                id=str(item.get("id") or f"ac-{index}"),
                description=str(item.get("description") or ""),
                required=bool(item.get("required", True)),
                requires_tool_evidence=bool(item.get("requires_tool_evidence", False)),
                required_tool_outcome=str(item.get("required_tool_outcome") or "any"),
                component_hints=[str(value) for value in item.get("component_hints") or []],
                source_excerpt=str(item.get("source_excerpt") or ""),
            )
            for index, item in enumerate(payload.get("acceptance_criteria") or [], start=1)
            if isinstance(item, dict)
        ]
        return cls(
            objective=str(payload.get("objective") or ""),
            constraints=[str(item) for item in payload.get("constraints") or []],
            acceptance_criteria=criteria,
            output_requirements=[str(item) for item in payload.get("output_requirements") or []],
            authorization_scope=str(
                payload.get("authorization_scope")
                or "Use only the tools and scope granted by the current session."
            ),
            complex=bool(payload.get("complex")),
            high_value=bool(payload.get("high_value")),
        )


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the latest user text without interpreting its meaning."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or item.get("content") or "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        return str(content)
    return ""
