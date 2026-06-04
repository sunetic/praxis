from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable

from app.core.logging import fmt_kv, get_logger
from app.services.platform.coding_engine import (
    CodingEngineApplyResult,
    CodingEngineEdit,
    CodingEnginePlan,
    _apply_prepared_edits,
)

logger = get_logger("services.external_cli_adapter")

_DEFAULT_TIMEOUT_SECONDS = 300
_LOGIN_SHELL = os.environ.get("SHELL", "/bin/zsh")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SPINNER_PREFIX_RE = re.compile(r"^(?:[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏*●○◦•·▪▸▶→➜✓✔✗✘-]+\s*)+")
_TIMESTAMP_PREFIX_RE = re.compile(r"^(?:\[\d{2}:\d{2}:\d{2}\]\s*|\d{2}:\d{2}:\d{2}\s+)")
_WHITESPACE_RE = re.compile(r"\s+")
_PROMPT_ECHO_MARKERS = (
    "Read CLAUDE.md (or equivalent context file) for runtime contract and rules.",
    "Follow this workflow strictly:",
    "Only modify these files:",
)


class ExternalCliAdapter:
    """
    CodingEngineAdapter that delegates to a user-configured external CLI tool.

    The adapter writes a CLAUDE.md context file to the workspace root so the
    CLI auto-discovers it, then invokes the CLI in non-interactive print mode
    with the build goal as a prompt argument.  After the CLI finishes it diffs
    the workspace to determine which files changed.
    """

    def __init__(
        self,
        *,
        command: str,
        pre_flags: str = "",
        post_flags: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        context_writer: Any | None = None,
    ) -> None:
        self._command = command.strip()
        self._pre_flags = pre_flags.strip()
        self._post_flags = post_flags.strip()
        self._timeout_seconds = timeout_seconds
        self._context_writer = context_writer
        self._build_context: dict[str, Any] = {}
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self._last_stream_summary = ""

    def set_event_callback(self, cb: Callable[[dict[str, Any]], None] | None) -> None:
        """Set a callback for streaming progress events during apply_changes."""
        self._event_callback = cb

    def set_build_context(
        self,
        *,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> None:
        """Store datasource info to forward to context_writer on next apply_changes."""
        self._build_context = {
            "datasource_schema": datasource_schema,
            "datasource_id": datasource_id,
        }

    def plan_changes(
        self,
        *,
        goal: str,
        allowed_files: list[str],
        edits: list[CodingEngineEdit],
    ) -> CodingEnginePlan:
        return CodingEnginePlan(goal=goal, allowed_files=allowed_files, edits=edits)

    def apply_changes(
        self, *, workspace_dir: Path, plan: CodingEnginePlan
    ) -> CodingEngineApplyResult:
        # Deterministic edits: write files directly, skip CLI subprocess.
        if plan.edits:
            changed_files = _apply_prepared_edits(workspace_dir=workspace_dir, plan=plan)
            return CodingEngineApplyResult(
                changed_files=changed_files,
                diff_summary=f"Applied {len(changed_files)} file change(s)",
                tests_suggested=[],
                risk_notes=[],
                assistant_message="Code changes applied to workspace.",
            )

        workspace_resolved = workspace_dir.resolve()

        # Write context (CLAUDE.md) so the CLI auto-discovers it.
        if self._context_writer is not None:
            try:
                self._context_writer.write(
                    workspace_dir=workspace_resolved,
                    goal=plan.goal,
                    **self._build_context,
                )
            except Exception as exc:
                logger.warning(
                    "external_cli_context_write_failed %s",
                    fmt_kv(error=str(exc)),
                )

        snapshot_before = _snapshot_files(workspace_resolved)

        # Build the prompt that instructs the CLI what to do.
        prompt = _build_prompt(plan.goal, plan.allowed_files)

        # Assemble: command + pre_flags + prompt + post_flags.
        # Post-flags (e.g. --allowedTools) are variadic and must come after the prompt.
        parts = [self._command]
        if self._pre_flags:
            parts.append(self._pre_flags)
        parts.append(_shell_quote(prompt))
        if self._post_flags:
            parts.append(_ensure_flag_arg_quoted(self._post_flags))
        full_command = " ".join(parts)
        logger.info(
            "external_cli_start %s",
            fmt_kv(command=self._command, workspace=str(workspace_resolved)),
        )
        self._last_stream_summary = ""

        try:
            stdout, stderr, returncode = self._run_streaming(
                full_command, cwd=str(workspace_resolved),
            )
        except subprocess.TimeoutExpired:
            last_summary = self._last_stream_summary.strip()
            logger.error(
                "external_cli_timeout %s",
                fmt_kv(
                    timeout=self._timeout_seconds,
                    last_summary=last_summary[:200] if last_summary else "none",
                ),
            )
            risk_notes = [f"CLI timed out after {self._timeout_seconds}s"]
            if last_summary:
                risk_notes.append(f"Last observed engine output: {last_summary[:500]}")
            assistant_message = f"External engine timed out after {self._timeout_seconds} seconds."
            if last_summary:
                assistant_message = (
                    f"{assistant_message}\n"
                    f"Last observed engine output: {last_summary[:1000]}"
                )
            return CodingEngineApplyResult(
                changed_files=[],
                diff_summary="External CLI timed out",
                tests_suggested=[],
                risk_notes=risk_notes,
                assistant_message=assistant_message,
            )
        except FileNotFoundError:
            logger.error(
                "external_cli_not_found %s",
                fmt_kv(command=self._command),
            )
            return CodingEngineApplyResult(
                changed_files=[],
                diff_summary="External CLI command not found",
                tests_suggested=[],
                risk_notes=[f"Command not found: {self._command}"],
                assistant_message=f"External engine command not found: {self._command}",
            )

        snapshot_after = _snapshot_files(workspace_resolved)
        changed_files = _diff_snapshots(snapshot_before, snapshot_after)

        # Parse structured JSON output when available.
        cli_result = _parse_cli_json(stdout)
        assistant_message = cli_result.get("result", "") if cli_result else ""

        # Extract result_status from engine output (5-stage workflow protocol).
        # Valid values: completed, needs_clarification, too_complex.
        result_status = "completed"
        if cli_result:
            raw_status = str(cli_result.get("result_status", "")).strip().lower()
            if raw_status in ("needs_clarification", "too_complex"):
                result_status = raw_status

        risk_notes: list[str] = []
        if cli_result and cli_result.get("is_error"):
            risk_notes.append("CLI reported error in JSON output")
        if returncode != 0:
            risk_notes.append(f"CLI exited with code {returncode}")
            if stderr:
                risk_notes.append(f"stderr: {stderr[:500]}")

        if not assistant_message:
            assistant_message = stdout[:2000] if stdout else "External engine completed."
        if returncode != 0 and not cli_result and stderr:
            assistant_message = f"External engine finished with errors:\n{stderr[:1000]}"

        logger.info(
            "external_cli_done %s",
            fmt_kv(
                returncode=returncode,
                changed_files=len(changed_files),
                json_parsed=cli_result is not None,
                result_status=result_status,
            ),
        )

        return CodingEngineApplyResult(
            changed_files=changed_files,
            diff_summary=f"External CLI changed {len(changed_files)} file(s)",
            tests_suggested=[],
            risk_notes=risk_notes,
            assistant_message=assistant_message,
            result_status=result_status,
        )


    def _emit(self, summary: str) -> None:
        """Emit a streaming progress event if callback is set."""
        cb = self._event_callback
        if cb is None:
            return
        try:
            cb({
                "type": "phase",
                "phase": "act",
                "status": "running",
                "summary": summary[:300],
                "created_at": datetime.now(UTC).isoformat(),
            })
        except Exception:
            pass

    def _run_streaming(
        self, command: str, *, cwd: str,
    ) -> tuple[str, str, int]:
        """Run command via Popen, streaming stdout lines as events."""
        process = subprocess.Popen(
            [_LOGIN_SHELL, "-lic", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

        stderr_lines: list[str] = []
        last_emitted = ""
        emission_lock = threading.Lock()

        def _emit_summary_from_line(raw_line: str) -> None:
            nonlocal last_emitted
            summary = _summarize_engine_output_line(raw_line)
            if not summary:
                return
            with emission_lock:
                if summary == last_emitted:
                    return
                last_emitted = summary
                self._last_stream_summary = summary
            self._emit(summary)

        def _read_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_lines.append(line)
                _emit_summary_from_line(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        stdout_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            _emit_summary_from_line(line)

        try:
            process.wait(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
        stderr_thread.join(timeout=5)

        return (
            "".join(stdout_lines).strip(),
            "".join(stderr_lines).strip(),
            process.returncode or 0,
        )


def _ensure_flag_arg_quoted(flag: str) -> str:
    """Quote the argument portion of a flag with space-separated values."""
    flag = flag.strip()
    if '"' in flag or "'" in flag:
        return flag
    if flag.startswith("--"):
        parts = flag.split(" ", 1)
        if len(parts) == 2 and " " in parts[1]:
            return f'{parts[0]} "{parts[1]}"'
    return flag


def _build_prompt(goal: str, allowed_files: list[str]) -> str:
    """Build the prompt passed to the CLI as the positional argument.

    The prompt encodes the 5-stage build workflow so that any external
    engine (Claude Code, Cursor, etc.) receives the instructions via
    the universal prompt channel regardless of tool-specific file
    conventions.
    """
    parts = [
        "Read CLAUDE.md (or equivalent context file) for runtime contract and rules.",
        "Follow this workflow strictly: "
        "(1) COMPLEXITY ASSESSMENT — evaluate if the goal is achievable in a single unit; "
        "if too complex or vague, return result_status=too_complex with decomposition suggestions and do NOT write code; "
        "if clarification needed, return result_status=needs_clarification with specific questions. "
        "(2) REQUIREMENT REFINEMENT — identify exact data/APIs needed and output shape. "
        "(3) IMPLEMENTATION PLAN — plan the approach before coding. "
        "(4) IMPLEMENTATION — write the code. "
        "(5) SELF-TEST — you MUST run a test to verify correctness before finishing.",
        f"Goal: {goal}",
    ]
    if allowed_files:
        parts.append(f"Only modify these files: {', '.join(allowed_files)}")
    return " ".join(parts)


def _clean_engine_output_line(line: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", line)
    cleaned = cleaned.replace("\r", " ").replace("\t", " ").strip()
    cleaned = _TIMESTAMP_PREFIX_RE.sub("", cleaned)
    cleaned = _SPINNER_PREFIX_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" -")
    return cleaned


def _extract_json_progress_summary(line: str) -> str | None:
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if any(key in payload for key in ("result", "result_status", "assistant_message")):
        return None
    for key in ("summary", "message", "content", "text", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _WHITESPACE_RE.sub(" ", value).strip()[:300]
    tool_name = payload.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return f"Calling tool: {tool_name.strip()[:200]}"
    return None


def _summarize_engine_output_line(line: str) -> str | None:
    cleaned = _clean_engine_output_line(line)
    if not cleaned:
        return None
    if any(marker in cleaned for marker in _PROMPT_ECHO_MARKERS):
        return None

    json_summary = _extract_json_progress_summary(cleaned)
    if json_summary:
        return json_summary
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return None

    if len(cleaned) > 1200:
        cleaned = cleaned[:300].rstrip() + "..."

    if not any(ch.isalnum() for ch in cleaned):
        return None

    lowered = cleaned.lower()
    if lowered.startswith(("running ", "executing ", "command: ")):
        _, _, remainder = cleaned.partition(" ")
        command = remainder.strip() or cleaned
        return f"Running command: {command[:240]}"
    if lowered.startswith(("reading ", "read ", "loading ", "loaded ")):
        _, _, remainder = cleaned.partition(" ")
        detail = remainder.strip() or cleaned
        return f"Reading context: {detail[:240]}"
    if lowered.startswith(("searching ", "search ", "scanning ", "scan ")):
        _, _, remainder = cleaned.partition(" ")
        detail = remainder.strip() or cleaned
        return f"Scanning: {detail[:240]}"
    if lowered.startswith(("editing ", "updated ", "writing ", "wrote ")):
        _, _, remainder = cleaned.partition(" ")
        detail = remainder.strip() or cleaned
        return f"Modifying content: {detail[:240]}"

    return cleaned[:300]


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell interpolation."""
    return "'" + s.replace("'", "'\\''") + "'"


def _parse_cli_json(stdout: str) -> dict[str, Any] | None:
    """Try to parse JSON output from --output-format json.

    The CLI may emit non-JSON text (progress bars, ANSI codes) before the
    final JSON object.  We try the full string first, then fall back to
    extracting the last ``{...}`` block.
    """
    cleaned = _ANSI_ESCAPE_RE.sub("", stdout or "").strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    last_match: dict[str, Any] | None = None
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(cleaned[index:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(candidate, dict):
            last_match = candidate
    return last_match


def _snapshot_files(directory: Path) -> dict[str, float]:
    """Return {relative_path: mtime} for all files in directory."""
    snapshot: dict[str, float] = {}
    for path in directory.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            relative = str(path.relative_to(directory))
            snapshot[relative] = path.stat().st_mtime
    return snapshot


def _diff_snapshots(
    before: dict[str, float], after: dict[str, float]
) -> list[str]:
    """Return list of files that were added or modified."""
    changed: list[str] = []
    for path, mtime in after.items():
        if path not in before or before[path] != mtime:
            changed.append(path)
    return sorted(changed)


