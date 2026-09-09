from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.function.runtime_contract import get_function_runtime_contract
from app.services.function.runtime_probe import FunctionRuntimeProbe


@dataclass
class FunctionCodingState:
    changed_files: set[str] = field(default_factory=set)
    probe_required: bool = True
    probe_attempts: int = 0
    last_probe_error: str | None = None
    verified_revision: str | None = None
    completion: dict[str, Any] | None = None


class FunctionCodingTools:
    """Run-scoped Function source and verification tools for the shared agent runtime."""

    def __init__(self, *, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.source_path = (self.workspace_dir / "main.py").resolve()
        if self.source_path.parent != self.workspace_dir:
            raise ValueError("Function source path escapes workspace")
        self.state = FunctionCodingState()
        self._probe = FunctionRuntimeProbe()

    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "function_source_read",
                    "description": (
                        "Read the current candidate main.py for this Function. Returns source "
                        "content, line count, and the exact source revision."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "offset": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 1,
                                "description": "First one-based source line to return.",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 1000,
                                "default": 400,
                                "description": "Maximum number of source lines to return.",
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "function_source_edit",
                    "description": (
                        "Replace one exact source fragment in main.py. The old fragment must occur "
                        "exactly once. Use this for small, targeted changes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "old_text": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Exact existing source fragment to replace.",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "Replacement source fragment.",
                            },
                        },
                        "required": ["old_text", "new_text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "function_source_replace",
                    "description": (
                        "Replace the complete candidate main.py. Use for a new Function or when "
                        "targeted editing would be less reliable than a full coherent rewrite."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Complete Python source for main.py.",
                            }
                        },
                        "required": ["code"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_function_runtime_contract",
                    "description": (
                        "Return the authoritative machine-readable Praxis Function runtime contract. "
                        "Use it before relying on a db, platform, or scheduler_history API detail."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "function_runtime_probe",
                    "description": (
                        "Execute the current candidate main.py against the controlled Function "
                        "runtime probe. Returns structured diagnostics and records the verified "
                        "source revision."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "payload": {
                                "type": "object",
                                "description": (
                                    "Representative input derived from the candidate's payload keys "
                                    "and expected value types."
                                ),
                            },
                            "context": {
                                "type": "object",
                                "description": (
                                    "Optional scalar runtime context. Usually omit it so controlled "
                                    "defaults are used."
                                ),
                            },
                        },
                        "required": ["payload"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "function_build_finish",
                    "description": (
                        "Submit the terminal Function Build outcome. completed is accepted only "
                        "when the current source revision passed function_runtime_probe."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "outcome": {
                                "type": "string",
                                "enum": ["completed", "needs_clarification", "too_complex"],
                                "description": "Structured terminal outcome for this build request.",
                            },
                            "assistant_message": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Concise user-facing result or clarification message.",
                            },
                            "diff_summary": {
                                "type": "string",
                                "description": "Short technical summary of the candidate change.",
                            },
                            "tests_suggested": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Useful follow-up checks for the user, if any.",
                            },
                            "risk_notes": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Known residual risks, if any.",
                            },
                            "questions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Concrete questions when clarification is required.",
                            },
                            "suggested_goals": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Independent sub-goals when the request is too broad.",
                            },
                        },
                        "required": ["outcome", "assistant_message"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "function_source_read":
                return self._read(arguments)
            if name == "function_source_edit":
                return self._edit(arguments)
            if name == "function_source_replace":
                return self._replace(arguments)
            if name == "get_function_runtime_contract":
                return {"success": True, "data": get_function_runtime_contract()}
            if name == "function_runtime_probe":
                return self._runtime_probe(arguments)
            if name == "function_build_finish":
                return self._finish(arguments)
            return self._error("unknown_tool", f"Unknown Function tool: {name}")
        except Exception as exc:
            return self._error("tool_execution_error", str(exc), category="execution_error")

    def current_revision(self) -> str:
        content = self.source_path.read_bytes() if self.source_path.exists() else b""
        return "sha256:" + hashlib.sha256(content).hexdigest()

    def _read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        content = self.source_path.read_text(encoding="utf-8") if self.source_path.exists() else ""
        lines = content.splitlines()
        offset = max(1, int(arguments.get("offset") or 1))
        limit = min(1000, max(1, int(arguments.get("limit") or 400)))
        selected = lines[offset - 1 : offset - 1 + limit]
        return {
            "success": True,
            "data": {
                "content": "\n".join(selected),
                "total_lines": len(lines),
                "revision": self.current_revision(),
            },
        }

    def _edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.source_path.exists():
            return self._error("source_missing", "main.py does not exist; replace the source first")
        old_text = str(arguments.get("old_text") or "")
        new_text = str(arguments.get("new_text") or "")
        content = self.source_path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if not old_text or occurrences != 1:
            return self._error(
                "edit_match_error",
                f"function_source_edit requires exactly one match, found {occurrences}",
            )
        self.source_path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return self._mark_changed()

    def _replace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return self._error("invalid_source", "Complete Function source cannot be empty")
        self.source_path.parent.mkdir(parents=True, exist_ok=True)
        self.source_path.write_text(code, encoding="utf-8")
        return self._mark_changed()

    def _mark_changed(self) -> dict[str, Any]:
        self.state.changed_files.add("main.py")
        self.state.probe_required = True
        self.state.last_probe_error = None
        self.state.verified_revision = None
        self.state.completion = None
        return {
            "success": True,
            "data": {"changed_file": "main.py", "revision": self.current_revision()},
        }

    def _runtime_probe(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = arguments.get("payload")
        context = arguments.get("context")
        if not isinstance(payload, dict):
            return self._error("invalid_probe_payload", "payload must be an object")
        probe_context = context if isinstance(context, dict) else self._probe.default_context()
        self.state.probe_attempts += 1
        ok, error, result_type = self._probe.run(
            workspace_dir=self.workspace_dir,
            payload=payload,
            context=probe_context,
        )
        self.state.probe_required = not ok
        self.state.last_probe_error = error
        revision = self.current_revision()
        self.state.verified_revision = revision if ok else None
        data = {
            "ok": ok,
            "error": error,
            "result_type": result_type,
            "probe_attempt": self.state.probe_attempts,
            "revision": revision,
        }
        if ok:
            return {"success": True, "data": data}
        repair_hint = self._probe.repair_hint(str(error or ""))
        if repair_hint:
            data["repair_hint"] = repair_hint
        return self._error(
            "runtime_probe_failed",
            str(error or "Function runtime probe failed"),
            category="verification_error",
            data=data,
        )

    def _finish(self, arguments: dict[str, Any]) -> dict[str, Any]:
        outcome = str(arguments.get("outcome") or "").strip().lower()
        message = str(arguments.get("assistant_message") or "").strip()
        if outcome not in {"completed", "needs_clarification", "too_complex"}:
            return self._error("invalid_outcome", "Unsupported Function Build outcome")
        if not message:
            return self._error("missing_message", "assistant_message is required")
        if outcome == "completed":
            revision = self.current_revision()
            if self.state.probe_required or self.state.verified_revision != revision:
                return self._error(
                    "verification_required",
                    "The current main.py revision must pass function_runtime_probe before completion",
                    category="verification_error",
                    data={
                        "current_revision": revision,
                        "verified_revision": self.state.verified_revision,
                        "last_probe_error": self.state.last_probe_error,
                    },
                )
        if outcome == "needs_clarification" and not self._string_list(arguments.get("questions")):
            return self._error(
                "clarification_questions_required",
                "needs_clarification requires at least one concrete question",
            )
        if outcome == "too_complex" and not self._string_list(arguments.get("suggested_goals")):
            return self._error(
                "suggested_goals_required",
                "too_complex requires independently buildable suggested_goals",
            )
        completion = {
            "outcome": outcome,
            "assistant_message": message,
            "diff_summary": str(arguments.get("diff_summary") or "").strip(),
            "tests_suggested": self._string_list(arguments.get("tests_suggested")),
            "risk_notes": self._string_list(arguments.get("risk_notes")),
            "questions": self._string_list(arguments.get("questions")),
            "suggested_goals": self._string_list(arguments.get("suggested_goals")),
            "revision": self.current_revision(),
        }
        self.state.completion = completion
        return {"success": True, "data": completion}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        category: str = "validation_error",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "data": data,
            "error": {
                "code": code,
                "category": category,
                "message": message,
                "phase": "execution",
            },
            "error_class": code,
        }
