from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PageCodingState:
    changed_files: set[str] = field(default_factory=set)
    check_required: bool = True
    check_attempts: int = 0
    last_check_error: str | None = None
    verified_revisions: dict[str, str] | None = None
    completion: dict[str, Any] | None = None


class PageCodingTools:
    """Run-scoped Page source and deterministic verification tools."""

    allowed_files = frozenset({"main.tsx", "preview.html"})

    def __init__(self, *, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir.resolve()
        self.state = PageCodingState()

    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        path_schema = {
            "type": "string",
            "enum": ["main.tsx", "preview.html"],
            "description": "Page source file to inspect or change.",
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "page_source_read",
                    "description": "Read main.tsx or preview.html with its exact revision.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": path_schema,
                            "offset": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 1,
                                "description": "First one-based line to return.",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 2000,
                                "default": 800,
                                "description": "Maximum source lines to return.",
                            },
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "page_source_edit",
                    "description": (
                        "Replace one exact source fragment. The old fragment must occur once."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": path_schema,
                            "old_text": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Exact existing fragment.",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "Replacement fragment.",
                            },
                        },
                        "required": ["path", "old_text", "new_text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "page_source_replace",
                    "description": "Replace all content in main.tsx or preview.html coherently.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": path_schema,
                            "content": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Complete source content.",
                            },
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "page_workspace_check",
                    "description": (
                        "Validate that Page source and self-contained preview are present and "
                        "synchronized in this build. Records the verified revisions."
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
                    "name": "page_build_finish",
                    "description": (
                        "Submit the terminal Page Build outcome. completed requires the current "
                        "revisions to pass page_workspace_check."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "outcome": {
                                "type": "string",
                                "enum": ["completed", "needs_clarification", "too_complex"],
                                "description": "Structured terminal build outcome.",
                            },
                            "assistant_message": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Concise user-facing outcome.",
                            },
                            "diff_summary": {
                                "type": "string",
                                "description": "Short technical change summary.",
                            },
                            "tests_suggested": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Useful follow-up checks, if any.",
                            },
                            "risk_notes": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Known residual risks, if any.",
                            },
                            "questions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Required questions for clarification outcomes.",
                            },
                            "suggested_goals": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Independent Page goals for too-complex outcomes.",
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
            if name == "page_source_read":
                return self._read(arguments)
            if name == "page_source_edit":
                return self._edit(arguments)
            if name == "page_source_replace":
                return self._replace(arguments)
            if name == "page_workspace_check":
                return self._check()
            if name == "page_build_finish":
                return self._finish(arguments)
            return self._error("unknown_tool", f"Unknown Page tool: {name}")
        except Exception as exc:
            return self._error("tool_execution_error", str(exc), category="execution_error")

    def current_revisions(self) -> dict[str, str]:
        revisions: dict[str, str] = {}
        for relative in sorted(self.allowed_files):
            path = self._path(relative)
            content = path.read_bytes() if path.exists() else b""
            revisions[relative] = "sha256:" + hashlib.sha256(content).hexdigest()
        return revisions

    def _path(self, raw_path: Any) -> Path:
        relative = str(raw_path or "").strip()
        if relative not in self.allowed_files:
            raise ValueError(f"Unsupported Page source path: {relative or '<empty>'}")
        target = (self.workspace_dir / relative).resolve()
        if target.parent != self.workspace_dir:
            raise ValueError("Page source path escapes workspace")
        return target

    def _read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._path(arguments.get("path"))
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = content.splitlines()
        offset = max(1, int(arguments.get("offset") or 1))
        limit = min(2000, max(1, int(arguments.get("limit") or 800)))
        return {
            "success": True,
            "data": {
                "path": path.name,
                "content": "\n".join(lines[offset - 1 : offset - 1 + limit]),
                "total_lines": len(lines),
                "revision": self.current_revisions()[path.name],
            },
        }

    def _edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._path(arguments.get("path"))
        if not path.exists():
            return self._error("source_missing", f"{path.name} does not exist")
        old_text = str(arguments.get("old_text") or "")
        new_text = str(arguments.get("new_text") or "")
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if not old_text or occurrences != 1:
            return self._error(
                "edit_match_error",
                f"page_source_edit requires exactly one match, found {occurrences}",
            )
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return self._mark_changed(path.name)

    def _replace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._path(arguments.get("path"))
        content = str(arguments.get("content") or "")
        if not content.strip():
            return self._error("invalid_source", f"Complete {path.name} cannot be empty")
        path.write_text(content, encoding="utf-8")
        return self._mark_changed(path.name)

    def _mark_changed(self, relative: str) -> dict[str, Any]:
        self.state.changed_files.add(relative)
        self.state.check_required = True
        self.state.last_check_error = None
        self.state.verified_revisions = None
        self.state.completion = None
        return {
            "success": True,
            "data": {
                "changed_file": relative,
                "revision": self.current_revisions()[relative],
            },
        }

    def _check(self) -> dict[str, Any]:
        self.state.check_attempts += 1
        diagnostics: list[str] = []
        source_path = self._path("main.tsx")
        preview_path = self._path("preview.html")
        source = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
        preview = preview_path.read_text(encoding="utf-8") if preview_path.exists() else ""
        if not source.strip():
            diagnostics.append("main.tsx must not be empty")
        if not preview.strip():
            diagnostics.append("preview.html must not be empty")
        if (
            "main.tsx" in self.state.changed_files
            and "preview.html" not in self.state.changed_files
        ):
            diagnostics.append("preview.html must be updated when main.tsx changes")
        lowered_preview = preview.lower()
        if preview.strip() and not all(
            marker in lowered_preview for marker in ("<html", "<body", "</html>")
        ):
            diagnostics.append("preview.html must be a complete HTML document")
        external_asset = re.search(
            r"<(?:script|link)[^>]+(?:src|href)=[\"']https?://",
            preview,
            flags=re.IGNORECASE,
        )
        if external_asset:
            diagnostics.append("preview.html must not load external script or stylesheet assets")

        revisions = self.current_revisions()
        ok = not diagnostics
        self.state.check_required = not ok
        self.state.last_check_error = "; ".join(diagnostics) if diagnostics else None
        self.state.verified_revisions = revisions if ok else None
        data = {
            "ok": ok,
            "diagnostics": diagnostics,
            "check_attempt": self.state.check_attempts,
            "revisions": revisions,
        }
        if ok:
            return {"success": True, "data": data}
        return self._error(
            "page_workspace_check_failed",
            self.state.last_check_error or "Page workspace check failed",
            category="verification_error",
            data=data,
        )

    def _finish(self, arguments: dict[str, Any]) -> dict[str, Any]:
        outcome = str(arguments.get("outcome") or "").strip().lower()
        message = str(arguments.get("assistant_message") or "").strip()
        if outcome not in {"completed", "needs_clarification", "too_complex"}:
            return self._error("invalid_outcome", "Unsupported Page Build outcome")
        if not message:
            return self._error("missing_message", "assistant_message is required")
        if outcome == "completed":
            revisions = self.current_revisions()
            if self.state.check_required or self.state.verified_revisions != revisions:
                return self._error(
                    "verification_required",
                    "Current Page source revisions must pass page_workspace_check",
                    category="verification_error",
                    data={
                        "revisions": revisions,
                        "verified_revisions": self.state.verified_revisions,
                        "last_check_error": self.state.last_check_error,
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
            "revisions": self.current_revisions(),
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
