from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BuildAttemptContext:
    prompt: str = ""
    status: str = ""
    error: str = ""
    summary: str = ""


@dataclass(frozen=True)
class BuildGoalRequest:
    user_prompt: str
    recent_contexts: list[BuildAttemptContext]
    conversation_context: str = ""
    skill_context: str = ""


class BuildScopeAdapter(Protocol):
    def resolve_primary_requirement(self, *, prompt: str, history: list[BuildAttemptContext]) -> str:
        ...

    def guardrails(self) -> str:
        ...


def normalize_build_attempts(raw_contexts: list[dict[str, Any]] | None) -> list[BuildAttemptContext]:
    normalized: list[BuildAttemptContext] = []
    for item in raw_contexts or []:
        if not isinstance(item, dict):
            continue
        normalized.append(
            BuildAttemptContext(
                prompt=str(item.get("prompt") or "").strip(),
                status=str(item.get("status") or "").strip(),
                error=str(item.get("error") or "").strip(),
                summary=str(item.get("summary") or "").strip(),
            )
        )
    return normalized


def extract_primary_requirement(goal: str) -> str:
    lines = [str(item).strip() for item in str(goal or "").splitlines()]
    for index, line in enumerate(lines):
        normalized = line.lower()
        if not normalized.startswith("primary requirement:"):
            continue
        inline = line.split(":", 1)[1].strip() if ":" in line else ""
        if inline:
            return inline
        for candidate in lines[index + 1 :]:
            if candidate:
                return candidate
        return ""
    for line in lines:
        if line:
            return line
    return ""


def summarize_build_goal(goal: str, *, max_len: int = 160) -> str:
    summary = extract_primary_requirement(goal) or str(goal or "").strip()
    return summary[:max_len].strip()


class AgentCore:
    def compose_build_goal(
        self,
        *,
        adapter: BuildScopeAdapter,
        request: BuildGoalRequest,
    ) -> str:
        prompt = str(request.user_prompt or "").strip()
        history = request.recent_contexts or []
        latest = history[0] if history else BuildAttemptContext()
        effective_prompt = adapter.resolve_primary_requirement(prompt=prompt, history=history) or prompt

        sections = [f"Primary Requirement:\n{effective_prompt}" if effective_prompt else ""]
        compact_context = str(request.conversation_context or "").strip()
        if compact_context:
            sections.append(f"Current Build Conversation (latest turns):\n{compact_context}")
        if latest.prompt:
            sections.append(f"Previous Build Requirement:\n{latest.prompt}")
        if latest.status:
            sections.append(f"Previous Build Status:\n{latest.status}")
        if latest.error:
            sections.append(f"Previous Build Error:\n{latest.error}")

        history_lines: list[str] = []
        for idx, context in enumerate(history[1:4], start=2):
            if not any([context.prompt, context.status, context.error]):
                continue
            history_lines.append(
                f"{idx}. requirement={context.prompt or '-'}; "
                f"status={context.status or '-'}; "
                f"error={context.error or '-'}"
            )
        if history_lines:
            sections.append("Recent Build Attempts (newest->older):\n" + "\n".join(history_lines))

        skill_ctx = str(request.skill_context or "").strip()
        if skill_ctx:
            sections.append(skill_ctx)

        sections.append(adapter.guardrails())
        return "\n\n".join(item for item in sections if item)
