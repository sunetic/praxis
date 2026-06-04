from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.logging import fmt_kv, get_logger
from app.services.datasource.router import normalize_role
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.function.runtime_contract import get_function_runtime_contract
from app.services.llm import get_llm_client

logger = get_logger("app.services.pi_lite_engine")


ChatCompletionFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]]],
    Awaitable[dict[str, Any]],
]


@dataclass
class _PiLiteState:
    changed_files: set[str] = field(default_factory=set)
    probe_required: bool = False
    probe_attempts: int = 0
    last_probe_error: str | None = None
    intermediate_messages: int = 0


class PiLiteEngine:
    """
    Minimal Python coding agent loop:
    - model -> tool_calls -> tool_results -> model
    - bounded by `max_steps`
    - final assistant must output JSON result envelope
    """

    def __init__(
        self,
        *,
        max_steps: int = 10,
        chat_completion: ChatCompletionFn | None = None,
    ) -> None:
        self.max_steps = max_steps
        self._chat_completion = chat_completion or self._default_chat_completion

    async def run(
        self,
        *,
        goal: str,
        workspace_dir: Path,
        allowed_files: list[str],
    ) -> CodingEngineApplyResult:
        workspace_resolved = workspace_dir.resolve()
        allowed = {str(item or "").strip() for item in allowed_files if str(item or "").strip()}
        if not allowed:
            raise ValueError("allowed_files cannot be empty for pi-lite engine")
        logger.info(
            "pi_lite_run_start %s",
            fmt_kv(
                workspace_dir=workspace_resolved,
                allowed_files=",".join(sorted(allowed)),
                max_steps=self.max_steps,
            ),
        )

        state = _PiLiteState()
        if self._requires_function_runtime_probe(allowed):
            state.probe_required = True
        tools = self._build_tools(include_function_tools=self._requires_function_runtime_probe(allowed))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(workspace_resolved, sorted(allowed))},
            {"role": "user", "content": goal},
        ]

        for step in range(1, self.max_steps + 1):
            response = await self._chat_completion(messages, tools)
            message = (((response.get("choices") or [{}])[0] or {}).get("message") or {})
            tool_calls = message.get("tool_calls") or []
            content = message.get("content")
            logger.info(
                "pi_lite_step %s",
                fmt_kv(step=step, has_tool_calls=bool(tool_calls), content_len=len(str(content or ""))),
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": content if isinstance(content, str) else "",
                    "tool_calls": tool_calls if isinstance(tool_calls, list) and tool_calls else None,
                }
            )

            if isinstance(tool_calls, list) and tool_calls:
                for call in tool_calls:
                    tool_result = self._execute_tool_call(
                        call=call,
                        workspace_dir=workspace_resolved,
                        allowed=allowed,
                        state=state,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id") or ""),
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        }
                    )
                continue

            content_text = content if isinstance(content, str) else ""
            try:
                final = self._parse_final_json(content_text)
            except ValueError as exc:
                if self._should_continue_after_intermediate_message(content=content_text, state=state):
                    state.intermediate_messages += 1
                    logger.info(
                        "pi_lite_intermediate_message %s",
                        fmt_kv(step=step, intermediate_messages=state.intermediate_messages),
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Plan acknowledged. Continue to Step 1/2 now.\n"
                                "Use tool calls to inspect, edit, or verify files.\n"
                                "Only return raw final JSON when edits and verification are complete.\n"
                                "Do not stop at another natural-language progress update."
                            ),
                        }
                    )
                    continue
                raise exc
            if self._requires_page_preview_sync(allowed, state):
                logger.warning(
                    "pi_lite_page_preview_sync_required %s",
                    fmt_kv(
                        step=step,
                        changed_files=",".join(sorted(state.changed_files)),
                    ),
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You updated main.tsx but did not update preview.html in this run.\n"
                            "Update preview.html so runtime behavior matches main.tsx, then return final JSON."
                        ),
                    }
                )
                continue
            if self._requires_function_runtime_probe(allowed) and state.probe_required:
                logger.warning(
                    "pi_lite_probe_required_before_finalize %s",
                    fmt_kv(step=step, probe_attempts=state.probe_attempts, last_probe_error=str(state.last_probe_error or "")),
                )
                repair_hint = self._build_probe_repair_hint(str(state.last_probe_error or ""))
                hint_block = f"Repair hint: {repair_hint}\n" if repair_hint else ""
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Before final JSON, call `function_runtime_probe` on the current main.py and confirm `ok=true`.\n"
                            f"Last probe error: {state.last_probe_error or 'none'}\n"
                            f"{hint_block}"
                            "Read the error, consult `get_function_runtime_contract` if needed, fix main.py, then probe again.\n"
                            "Do not finalize until probe passes."
                        ),
                    }
                )
                continue

            logger.info(
                "pi_lite_run_done %s",
                fmt_kv(
                    step=step,
                    changed_files=",".join(sorted(state.changed_files)),
                    assistant_message=str(final.get("assistant_message") or ""),
                ),
            )
            return CodingEngineApplyResult(
                changed_files=sorted(state.changed_files),
                diff_summary=str(final.get("diff_summary") or f"Applied {len(state.changed_files)} file change(s)"),
                tests_suggested=self._normalize_string_list(final.get("tests_suggested")),
                risk_notes=self._normalize_string_list(final.get("risk_notes")),
                assistant_message=str(final.get("assistant_message") or "Code changes applied; please verify."),
                generated_title=str(final.get("generated_title") or "").strip(),
                generated_description=str(final.get("generated_description") or "").strip(),
            )

        logger.error("pi_lite_run_failed %s", fmt_kv(reason="max_steps_exceeded", max_steps=self.max_steps))
        raise ValueError(f"pi-lite reached max_steps={self.max_steps} without final JSON response")

    def _should_continue_after_intermediate_message(self, *, content: str, state: _PiLiteState) -> bool:
        text = str(content or "").strip()
        if not text:
            return False
        return state.intermediate_messages < 2

    async def _default_chat_completion(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        client = get_llm_client()
        async for payload in client.chat(messages=messages, tools=tools, stream=False, temperature=0):
            if isinstance(payload, dict):
                return payload
        raise ValueError("LLM returned no response payload")

    def _build_tools(self, *, include_function_tools: bool = False) -> list[dict[str, Any]]:
        base_tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read text file content in workspace",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "offset": {"type": "integer"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create or overwrite file content in workspace",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Replace exact text once in file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_bash",
                    "description": "Run shell command in workspace (for validation only)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout_seconds": {"type": "integer"},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]
        if not include_function_tools:
            return base_tools
        return [
            *base_tools,
            {
                "type": "function",
                "function": {
                    "name": "get_function_runtime_contract",
                    "description": "Get machine-readable function runtime contract (entry, context, db API).",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "function_runtime_probe",
                    "description": "Execute current main.py with platform runtime context and return structured probe result.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "payload": {"type": "object"},
                            "context": {"type": "object"},
                        },
                    },
                },
            },
        ]

    def _build_system_prompt(self, workspace_dir: Path, allowed_files: list[str]) -> str:
        allowed_text = "\n".join(f"- {item}" for item in allowed_files)
        function_contract = ""
        if "main.py" in allowed_files:
            function_contract = (
                "Function workspace rules:\n"
                "- Entry must be `main(payload, context)` with exactly 2 positional args.\n"
                "- `context` is a plain dict; use `context.get(...)` only.\n"
                "- No project-private imports. Pass `datasource_id` explicitly for user SQL.\n"
                "- `db.query(...)` / `db.query_by_id(...)` return a mapping; read `result.get('rows', [])` before iterating rows.\n"
                "- For scheduler cleanup and retention logic, use `scheduler_history.list(...)` / `scheduler_history.delete(...)` helpers.\n"
                "- `scheduler_history.delete(...)` must use structured `where=...` and `policy=...`; put retention in `policy.retention_seconds`.\n"
                "- In plan mode, `scheduler_history.delete(...)` requires `dry_run=True` before any apply-mode delete.\n"
                "- Call `get_function_runtime_contract` if uncertain about db/platform API.\n"
                "- Before final JSON: call `function_runtime_probe` with a realistic payload "
                "(derive keys and types from the actual `main(payload, context)` implementation) "
                "and confirm `ok=true`.\n"
            )
        page_contract = ""
        if "main.tsx" in allowed_files and "preview.html" in allowed_files:
            page_contract = (
                "Page workspace rules:\n"
                "- main.tsx is source of truth; preview.html must stay in sync.\n"
                "- If main.tsx changes, update preview.html in the same run.\n"
                "- preview.html must be fully self-contained (all CSS defined inline).\n"
            )
        return (
            "You are pi-lite, a focused coding engine.\n"
            "Workflow (follow in order):\n"
            "  Step 0 — Understand: read the relevant existing files, then output a brief change plan "
            "(one message, no tool call) describing what exists and what you will change and why. "
            "Do not skip this step.\n"
            "  Step 1 — Edit: make minimal, targeted edits. Prefer `edit_file` over full file rewrites.\n"
            "  Step 2 — Verify: run probe/checks, then return the final JSON.\n"
            "Hard rules:\n"
            "1) Only modify files in allowed_files.\n"
            "2) Re-raise DB/platform exceptions; never return mock/fake data.\n"
            "3) Final response: raw JSON only (no markdown fences) — keys: assistant_message, diff_summary, tests_suggested, risk_notes.\n"
            "4) assistant_message: 3–5 sentences, user-facing business language — no platform internals "
            "(get_session_by_id / SQLAlchemy / execution_mode / plan mode / runtime_path).\n"
            f"{function_contract}"
            f"{page_contract}"
            f"workspace_dir: {workspace_dir}\n"
            f"allowed_files:\n{allowed_text}\n"
        )

    def _execute_tool_call(
        self,
        *,
        call: dict[str, Any],
        workspace_dir: Path,
        allowed: set[str],
        state: _PiLiteState,
    ) -> dict[str, Any]:
        fn = (((call.get("function") or {}) if isinstance(call.get("function"), dict) else {}))
        name = str(fn.get("name") or "").strip()
        arguments_text = str(fn.get("arguments") or "").strip()
        args = self._safe_json_load(arguments_text)
        logger.info("pi_lite_tool_call %s", fmt_kv(tool=name))

        try:
            if name == "read_file":
                path = self._normalize_allowed_path(args.get("path"), workspace_dir, allowed)
                content = path.read_text(encoding="utf-8") if path.exists() else ""
                lines = content.splitlines()
                offset = int(args.get("offset") or 1)
                limit = int(args.get("limit") or 200)
                start = max(offset - 1, 0)
                end = start + max(limit, 1)
                sliced = lines[start:end]
                return {"ok": True, "content": "\n".join(sliced), "total_lines": len(lines)}

            if name == "write_file":
                path = self._normalize_allowed_path(args.get("path"), workspace_dir, allowed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(args.get("content") or ""), encoding="utf-8")
                relative = str(path.relative_to(workspace_dir))
                state.changed_files.add(relative)
                if relative == "main.py":
                    state.probe_required = True
                    state.last_probe_error = None
                return {"ok": True, "changed_file": relative}

            if name == "edit_file":
                path = self._normalize_allowed_path(args.get("path"), workspace_dir, allowed)
                if not path.exists():
                    raise ValueError(f"File not found: {path.relative_to(workspace_dir)}")
                old_text = str(args.get("old_text") or "")
                new_text = str(args.get("new_text") or "")
                content = path.read_text(encoding="utf-8")
                occurrences = content.count(old_text)
                if occurrences != 1:
                    raise ValueError(f"edit_file requires exactly one match, found {occurrences}")
                path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
                relative = str(path.relative_to(workspace_dir))
                state.changed_files.add(relative)
                if relative == "main.py":
                    state.probe_required = True
                    state.last_probe_error = None
                return {"ok": True, "changed_file": relative}

            if name == "run_bash":
                command = str(args.get("command") or "").strip()
                if not command:
                    raise ValueError("run_bash command is required")
                timeout_seconds = int(args.get("timeout_seconds") or 30)
                completed = subprocess.run(
                    command,
                    cwd=str(workspace_dir),
                    shell=True,
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=max(timeout_seconds, 1),
                )
                output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
                if len(output) > 8000:
                    output = output[:8000] + "\n...[truncated]"
                return {"ok": completed.returncode == 0, "returncode": completed.returncode, "output": output}

            if name == "get_function_runtime_contract":
                return {"ok": True, "contract": get_function_runtime_contract()}

            if name == "function_runtime_probe":
                if "main.py" not in allowed:
                    raise ValueError("function_runtime_probe is only available when main.py is in allowed_files")
                state.probe_attempts += 1
                payload = args.get("payload")
                context = args.get("context")
                probe_payload = payload if isinstance(payload, dict) else self._default_probe_payload()
                probe_context = context if isinstance(context, dict) else self._default_probe_context()
                ok, error, result_type = self._run_function_runtime_probe(
                    workspace_dir=workspace_dir,
                    payload=probe_payload,
                    context=probe_context,
                )
                state.probe_required = not ok
                state.last_probe_error = error
                if ok:
                    logger.info(
                        "pi_lite_runtime_probe_passed %s",
                        fmt_kv(probe_attempt=state.probe_attempts, result_type=result_type),
                    )
                else:
                    logger.warning(
                        "pi_lite_runtime_probe_failed %s",
                        fmt_kv(probe_attempt=state.probe_attempts, error=str(error or "")),
                    )
                return {
                    "ok": ok,
                    "error": error,
                    "result_type": result_type,
                    "probe_attempt": state.probe_attempts,
                }

            raise ValueError(f"Unknown tool: {name}")
        except Exception as exc:
            logger.warning("pi_lite_tool_error %s", fmt_kv(tool=name, error=str(exc)))
            return {"ok": False, "error": str(exc), "tool": name}

    def _normalize_allowed_path(self, raw_path: Any, workspace_dir: Path, allowed: set[str]) -> Path:
        relative = str(raw_path or "").strip()
        if not relative:
            raise ValueError("path is required")
        if relative not in allowed:
            raise ValueError(f"Path '{relative}' is outside allowed_files")
        target = (workspace_dir / relative).resolve()
        if not str(target).startswith(str(workspace_dir)):
            raise ValueError("Path escapes workspace directory")
        return target

    def _safe_json_load(self, text: str) -> dict[str, Any]:
        if not text:
            return {}
        try:
            loaded = json.loads(text)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _parse_final_json(self, content: str) -> dict[str, Any]:
        text = content.strip()
        if not text:
            raise ValueError("pi-lite final response is empty")
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - input-specific
            raise ValueError(f"pi-lite final response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("pi-lite final response must be a JSON object")
        return data

    def _normalize_string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized

    def _requires_function_runtime_probe(self, allowed: set[str]) -> bool:
        return "main.py" in allowed

    def _requires_page_preview_sync(self, allowed: set[str], state: _PiLiteState) -> bool:
        if not {"main.tsx", "preview.html"}.issubset(allowed):
            return False
        if "main.tsx" not in state.changed_files:
            return False
        return "preview.html" not in state.changed_files

    def _default_probe_payload(self) -> dict[str, Any]:
        return {
            "datasource_id": 1,
            "datasourceId": 1,
            "datasource_ids": [1, 2],
            "datasourceIds": [1, 2],
            "rows": [],
            "items": [],
            "params": {},
        }

    def _default_probe_context(self) -> dict[str, Any]:
        return {
            "datasource_id": 1,
            "scope": {},
            "trace_id": "pi-lite-probe",
            "execution_mode": "plan",
        }

    def _run_function_runtime_probe(
        self,
        *,
        workspace_dir: Path,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[bool, str | None, str | None]:
        main_path = workspace_dir / "main.py"
        if not main_path.exists():
            return False, "main.py not found after apply", None
        code_snapshot = main_path.read_text(encoding="utf-8")
        static_guard_error = self._probe_static_guard_error(code_snapshot)
        if static_guard_error:
            return False, static_guard_error, None

        def _sample_rows_for_sql(sql_text: str) -> list[dict[str, Any]]:
            normalized = re.sub(r"\s+", " ", str(sql_text or "")).strip().lower()
            if "information_schema.schemata" in normalized:
                return [
                    {"schema_name": "crm"},
                    {"schema_name": "analytics"},
                ]
            if normalized.startswith("show databases"):
                return [
                    {"Database": "crm"},
                    {"Database": "analytics"},
                ]
            if normalized.startswith("select"):
                return [{"value": 1}]
            return []

        def _sample_query_result(sql_text: str) -> "_FakeQueryResult":
            rows = _sample_rows_for_sql(sql_text)
            columns = list(rows[0].keys()) if rows else []
            return _FakeQueryResult(columns=columns, rows=rows, row_count=len(rows))

        class _FakeResult:
            def __init__(self, rows: list[dict[str, Any]] | None = None):
                self._rows = rows if isinstance(rows, list) else []

            def mappings(self):
                return self

            def all(self):
                return self._rows

        class _FakeSession:
            def execute(self, *args: Any, **_kwargs: Any) -> _FakeResult:
                sql_text = str(args[0]) if args else ""
                return _FakeResult(_sample_rows_for_sql(sql_text))

            def close(self) -> None:
                return None

        class _FakeConnection:
            def query(self, _sql: str, *, params: list[Any] | None = None) -> _FakeQueryResult:
                _ = params
                return _sample_query_result(_sql)

            def explain(self, _sql: str) -> _FakeQueryResult:
                return _sample_query_result(_sql)

        class _FakeQueryResult(dict):
            def __iter__(self):  # type: ignore[override]
                message = (
                    "db.query(" + "..." + ") returns a " + "mapping; use " + "result.get('rows', []) "
                    "before iterating rows"
                )
                raise ValueError(message)

        class _FakeDB:
            def query(self, _sql: str, *, datasource: Any = None, role: str = "user", params: list[Any] | None = None) -> _FakeQueryResult:
                _ = datasource, params
                _ = normalize_role(role)
                return _sample_query_result(_sql)

            def explain(self, _sql: str, *, datasource: Any = None, role: str = "user") -> _FakeQueryResult:
                _ = datasource
                _ = normalize_role(role)
                return _sample_query_result(_sql)

            def query_by_id(self, *, sql: str, datasource_id: int, params: list[Any] | None = None) -> _FakeQueryResult:
                _ = datasource_id, params
                return _sample_query_result(sql)

            def explain_by_id(self, *, sql: str, datasource_id: int) -> _FakeQueryResult:
                _ = datasource_id
                return _sample_query_result(sql)

            def get_conn_by_id(self, *, datasource_id: int) -> _FakeConnection:
                _ = datasource_id
                return _FakeConnection()

            def get_session_by_id(self, *, datasource_id: int) -> _FakeSession:
                _ = datasource_id
                return _FakeSession()

            def close_opened_sessions(self) -> None:
                return None

        class _FakePlatform:
            def __init__(self, execution_mode: str) -> None:
                self._execution_mode = str(execution_mode or "apply").strip().lower()

            def list(self, _object_type: str, *, filters: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
                _ = filters, limit
                if str(_object_type or "").strip().lower() == "datasource":
                    return [
                        {"id": 1, "name": "mock-ds-1"},
                        {"id": 2, "name": "mock-ds-2"},
                    ]
                return []

            def get(self, _object_type: str, _object_id: Any) -> dict[str, Any]:
                return {}

            def crud(self, *, object_type: str, action: str, object_id: Any = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
                _ = object_type, object_id, payload
                if self._execution_mode == "plan" and str(action or "").strip().lower() in {"create", "update", "delete"}:
                    raise ValueError("Plan mode does not allow control-plane write operations; confirm first and use apply mode")
                return {"ok": True}

            def operate(self, *, object_type: str, action: str, object_id: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
                _ = object_type, action, object_id, payload
                if self._execution_mode == "plan":
                    raise ValueError("Plan mode does not allow control-plane operate actions; confirm first and use apply mode")
                return {"ok": True}

        try:
            from app.services.function.runtime import _execute_code_snapshot

            runtime_result = _execute_code_snapshot(
                code_snapshot=code_snapshot,
                payload=payload,
                context=context,
                runtime_services={
                    "db_capability": _FakeDB(),
                    "platform_capability": _FakePlatform(str(context.get("execution_mode") or "plan")),
                },
            )
            return True, None, type(runtime_result).__name__ if runtime_result is not None else "NoneType"
        except Exception as exc:
            return False, f"{exc.__class__.__name__}: {exc}", None

    def _build_probe_repair_hint(self, error: str) -> str:
        if not str(error or "").strip():
            return ""
        return (
            "Recheck the runtime contract before probing again: "
            "use `main(payload, context)` as the entry signature; "
            "treat `context` as a plain dict and read via `context.get(...)`; "
            "use an explicit datasource_id or discover one via `platform.list('datasource')`; "
            "read query rows from `result.get('rows', [])`; "
            "treat row values as mappings such as `row.get('Database')`; "
            "call `query_by_id` / `explain_by_id` with `datasource_id=...`; "
            "and re-raise DB/platform exceptions after adding context."
        )

    def _probe_static_guard_error(self, code_snapshot: str) -> str | None:
        try:
            syntax_tree = ast.parse(code_snapshot)
        except SyntaxError:
            return None

        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            method = str(func.attr)
            if method in {"query_by_id", "explain_by_id"} and len(node.args) > 1:
                line = int(getattr(node, "lineno", 0) or 0)
                return (
                    f"ValueError: Line {line or '?'} `{method}` requires keyword-only datasource_id; "
                    "use datasource_id=..."
                )

        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Try):
                continue
            if not self._probe_try_block_has_db_call(node.body):
                continue
            for handler in node.handlers:
                if self._probe_handler_has_raise(handler.body):
                    continue
                line = int(getattr(handler, "lineno", 0) or getattr(node, "lineno", 0) or 0)
                return (
                    f"ValueError: Line {line or '?'} DB exceptions cannot be swallowed; "
                    "raise after handling context"
                )
        return None

    def _probe_try_block_has_db_call(self, body: list[ast.stmt]) -> bool:
        if not body:
            return False
        root = ast.Module(body=body, type_ignores=[])
        for child in ast.walk(root):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Attribute) and str(func.attr) in {
                "query",
                "explain",
                "query_by_id",
                "explain_by_id",
                "get_conn_by_id",
                "get_session_by_id",
            }:
                return True
        return False

    def _probe_handler_has_raise(self, body: list[ast.stmt]) -> bool:
        for stmt in body:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Raise):
                    return True
        return False
