from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.llm import LLMClient, get_llm_client

logger = get_logger("services.engine_probe_agent")

_HELP_TIMEOUT_SECONDS = 10
_TEST_TIMEOUT_SECONDS = 60

# Use the user's login shell so CLI tools see the same env as the user's terminal.
_LOGIN_SHELL = os.environ.get("SHELL", "/bin/zsh")

_SYSTEM_PROMPT = """\
You are an expert at CLI tools. Given the --help output of a CLI tool, identify the flags \
needed to run it in **non-interactive single-shot mode** with structured output.

You must return a JSON object with exactly these fields:
{
  "print_flag": "<flag for single-shot/print mode, e.g. '-p' or '--print'>",
  "output_format_flag": "<flag for JSON output, e.g. '--output-format json', or '' if not available>",
  "permission_flags": "<flags to bypass permission prompts, e.g. '--permission-mode bypassPermissions', or ''>",
  "tool_restriction_flags": "<complete flag WITH its argument value for restricting tools, e.g. '--allowedTools \"Edit Read Write Bash\"'. Must include the argument, not just the flag name. Set to '' if not needed.>",
  "confidence": "high|medium|low",
  "reasoning": "<brief explanation of why you chose these flags>"
}

Rules:
- Only include flags that actually appear in the help text.
- If no print/single-shot flag exists, set print_flag to "" — do NOT invent flags.
- The goal is: the CLI runs once with a prompt argument, outputs a result, and exits.
- IMPORTANT: tool_restriction_flags often accept variadic arguments (e.g. --allowedTools <tools...>). \
Such variadic flags consume all subsequent positional arguments. They must be placed AFTER the prompt \
argument in the final command. The caller handles ordering — just identify the flags correctly.
- Return ONLY the JSON object, no markdown fences, no extra text.\
"""


def _run_in_login_shell(
    command: str,
    *,
    timeout: float,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command inside the user's login shell.

    This ensures the subprocess inherits the same environment (PATH, auth
    tokens, config dirs, etc.) the user has when they type commands in their
    terminal — regardless of how the server process was started.
    """
    return subprocess.run(
        [_LOGIN_SHELL, "-lic", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str
    suggested_command: str = ""
    flags_added: list[str] = field(default_factory=list)
    pre_flags: list[str] = field(default_factory=list)
    post_flags: list[str] = field(default_factory=list)
    env_issues: list[str] = field(default_factory=list)


class EngineProbeAgent:
    """System agent that discovers CLI non-interactive flags via help parsing."""

    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    def probe(self, command: str) -> ProbeResult:
        command = command.strip()
        if not command:
            return ProbeResult(ok=False, message="No command provided")

        # Step 1: Run --help
        help_text = self._run_help(command)
        if help_text is None:
            return ProbeResult(
                ok=False,
                message=f"Command not found or --help failed: {command}",
            )

        # Step 2: LLM parses help text to discover flags
        flags = self._parse_flags_with_llm(help_text)
        if flags is None:
            # Fallback: try raw command without flags
            return self._test_raw_command(command)

        # Step 3: Build suggested command and test it
        pre_flags, post_flags = self._build_flag_parts(flags)
        all_flags = pre_flags + post_flags
        suggested = f"{command} {' '.join(all_flags)}".strip()

        test_result = self._test_command_with_flags(command, pre_flags, post_flags)
        if test_result.ok:
            return ProbeResult(
                ok=True,
                message=test_result.message,
                suggested_command=suggested,
                flags_added=all_flags,
                pre_flags=pre_flags,
                post_flags=post_flags,
            )

        # Flags were found but test failed
        return ProbeResult(
            ok=False,
            message=test_result.message,
            suggested_command=suggested,
            flags_added=all_flags,
            pre_flags=pre_flags,
            post_flags=post_flags,
        )

    def _run_help(self, command: str) -> str | None:
        """Run {command} --help and return stdout+stderr."""
        for flag in ("--help", "-h"):
            try:
                result = _run_in_login_shell(
                    f"{command} {flag}",
                    timeout=_HELP_TIMEOUT_SECONDS,
                )
                output = (result.stdout or "") + (result.stderr or "")
                output = output.strip()
                if output and len(output) > 50:
                    return output
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
        return None

    def _parse_flags_with_llm(self, help_text: str) -> dict[str, str] | None:
        """Use LLM to extract non-interactive flags from help text."""
        if len(help_text) > 8000:
            help_text = help_text[:8000] + "\n... (truncated)"

        import json

        async def _call() -> dict[str, str] | None:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Here is the --help output:\n\n{help_text}"},
            ]
            response: dict[str, Any] | None = None
            async for chunk in self._llm.chat(
                messages=messages,
                tools=None,
                stream=False,
                temperature=0.0,
                response_format={"type": "json_object"},
            ):
                response = chunk
                break
            if response is None:
                return None
            content = (
                ((response.get("choices") or [{}])[0].get("message") or {})
                .get("content", "")
                .strip()
            )
            if not content:
                return None
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return None
            return parsed

        from app.services.platform.coding_engine import _run_async_safely

        try:
            return _run_async_safely(_call())
        except Exception as exc:
            logger.warning("engine_probe_llm_failed %s", fmt_kv(error=str(exc)))
            return None

    def _build_flag_parts(self, flags: dict[str, str]) -> tuple[list[str], list[str]]:
        """Build pre-prompt and post-prompt flag lists from LLM result.

        Variadic flags (tool_restriction_flags) go after the prompt to avoid
        consuming it as an argument.
        """
        pre: list[str] = []
        post: list[str] = []
        for key in ("print_flag", "output_format_flag", "permission_flags"):
            value = str(flags.get(key) or "").strip()
            if value:
                pre.append(value)
        tool_flags = str(flags.get("tool_restriction_flags") or "").strip()
        if tool_flags:
            post.append(tool_flags)
        return pre, post

    def _assemble_command(self, command: str, pre_flags: list[str], post_flags: list[str], prompt: str) -> str:
        """Assemble: command pre_flags prompt post_flags."""
        parts = [command]
        parts.extend(pre_flags)
        parts.append(f'"{prompt}"')
        for flag in post_flags:
            parts.append(_ensure_flag_arg_quoted(flag))
        return " ".join(parts)

    def _test_command_with_flags(self, command: str, pre_flags: list[str], post_flags: list[str]) -> ProbeResult:
        """Test a command with proper prompt ordering."""
        full = self._assemble_command(command, pre_flags, post_flags, "respond with just: ok")
        return self._run_probe(full)

    def _run_probe(self, shell_command: str) -> ProbeResult:
        """Run a fully assembled probe command."""
        try:
            result = _run_in_login_shell(shell_command, timeout=_TEST_TIMEOUT_SECONDS)
            if result.returncode == 0:
                stdout = result.stdout.strip()
                return ProbeResult(ok=True, message=stdout[:300] if stdout else "ok")
            stderr = result.stderr.strip()
            return ProbeResult(
                ok=False,
                message=f"Exit code {result.returncode}: {stderr[:300]}",
            )
        except FileNotFoundError:
            return ProbeResult(ok=False, message=f"Command not found: {shell_command}")
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, message=f"Timed out after {_TEST_TIMEOUT_SECONDS}s")
        except Exception as exc:
            return ProbeResult(ok=False, message=str(exc)[:300])

    def _test_raw_command(self, command: str) -> ProbeResult:
        """Fallback: test command without any discovered flags."""
        result = self._run_probe(f'{command} "respond with just: ok"')
        if result.ok:
            return ProbeResult(
                ok=True,
                message=result.message,
                suggested_command=command,
            )
        return ProbeResult(
            ok=False,
            message=f"Could not discover non-interactive flags. Raw test: {result.message}",
        )


def _ensure_flag_arg_quoted(flag: str) -> str:
    """Quote the argument portion of a flag like '--allowedTools Edit Read Write Bash'."""
    flag = flag.strip()
    if '"' in flag or "'" in flag:
        return flag
    if flag.startswith("--"):
        parts = flag.split(" ", 1)
        if len(parts) == 2 and " " in parts[1]:
            return f'{parts[0]} "{parts[1]}"'
    return flag


def get_engine_probe_agent() -> EngineProbeAgent:
    return EngineProbeAgent()
