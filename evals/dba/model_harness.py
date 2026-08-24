"""Stable, Praxis-independent tool harness for candidate-model comparison."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from evals.dba.catalog import EvalCase
from evals.dba.runtime import LLMConfig
from evals.dba.scoring import is_mutating_sql, score_case, terminal_metrics

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "model_harness.tpl"
MAX_TOOL_RESULT_CHARS = 60_000


class ReadOnlyFixture(Protocol):
    """Database access needed by the fixed model harness."""

    def execute_readonly(self, container: str, sql: str) -> str:
        """Execute one already-validated read-only SQL statement."""


@dataclass(frozen=True)
class HarnessResult:
    """One model trial in the same evidence shape as the Praxis runner."""

    evidence: dict[str, Any]
    runtime_metrics: dict[str, Any]


class CaseDeadlineExceededError(TimeoutError):
    """Raised when the fixed harness exhausts the total case budget."""


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaseDeadlineExceededError("Case execution deadline exceeded")
    return remaining


def _tool_schema(include_policy: bool) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "execute_sql",
                "description": (
                    "Execute one read-only SQL query against the current isolated database. "
                    "Mutating or administrative statements are rejected."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A single read-only SQL query.",
                        }
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    if include_policy:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "read_authoritative_source",
                    "description": "Read the private incident policy named in the task.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _completion(
    client: httpx.Client,
    config: LLMConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    attempts: int = 4,
    deadline: float,
) -> dict[str, Any]:
    endpoint = f"{config.base_url}/chat/completions"
    for attempt in range(1, attempts + 1):
        remaining = _remaining_seconds(deadline)
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {config.api_key}"},
            json={
                "model": config.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0,
                "stream": False,
            },
            timeout=httpx.Timeout(remaining),
        )
        if response.status_code < 400:
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Model provider returned a non-object response")
            return payload
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == attempts:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}")
        retry_delay = min(2**attempt, 10)
        remaining = _remaining_seconds(deadline)
        if retry_delay >= remaining:
            raise CaseDeadlineExceededError("Case execution deadline exceeded during retry backoff")
        time.sleep(retry_delay)
    raise RuntimeError("Model provider retry loop ended unexpectedly")


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }


def run_case(
    *,
    config: LLMConfig,
    fixture: ReadOnlyFixture,
    container: str,
    case: EvalCase,
    policy_path: Path,
    attempt: int,
    max_tool_rounds: int,
    timeout: float,
) -> HarnessResult:
    """Run one case using a fixed OpenAI-compatible tool loop."""
    started = time.monotonic()
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case.prompt},
    ]
    events: list[dict[str, Any]] = []
    tools = _tool_schema(bool(case.evidence_requirements))
    total_input_tokens = 0
    total_output_tokens = 0
    final_answer = ""
    stream_error: str | None = None
    case_timed_out = False
    llm_calls = 0
    deadline = started + timeout
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout), trust_env=False) as client:
            for _round in range(max_tool_rounds + 1):
                _remaining_seconds(deadline)
                payload = _completion(
                    client,
                    config,
                    messages,
                    tools,
                    deadline=deadline,
                )
                llm_calls += 1
                usage = _usage(payload)
                total_input_tokens += usage["input_tokens"]
                total_output_tokens += usage["output_tokens"]
                choices = payload.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise RuntimeError("Model provider returned no choices")
                message = choices[0].get("message")
                if not isinstance(message, dict):
                    raise RuntimeError("Model provider returned no assistant message")
                assistant_message = {
                    key: value
                    for key, value in message.items()
                    if key in {"role", "content", "tool_calls"}
                }
                assistant_message["role"] = "assistant"
                messages.append(assistant_message)
                tool_calls = message.get("tool_calls")
                if not isinstance(tool_calls, list) or not tool_calls:
                    final_answer = str(message.get("content") or "").strip()
                    terminal = "completed" if final_answer else "incomplete"
                    events.append(
                        {
                            "event_type": "done",
                            "payload": {
                                "status": terminal,
                                "metrics": {
                                    "input_tokens": total_input_tokens,
                                    "output_tokens": total_output_tokens,
                                    "llm_calls": llm_calls,
                                    "tool_calls": sum(
                                        event.get("event_type") == "step_result" for event in events
                                    ),
                                    "verification_attempts": 0,
                                },
                            },
                        }
                    )
                    break
                for tool_call in tool_calls:
                    function = tool_call.get("function") if isinstance(tool_call, dict) else None
                    name = str(function.get("name") or "") if isinstance(function, dict) else ""
                    raw_arguments = (
                        function.get("arguments") if isinstance(function, dict) else "{}"
                    )
                    try:
                        arguments = json.loads(raw_arguments or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("tool arguments must be an object")
                        if name == "execute_sql":
                            sql = str(arguments.get("sql") or "").strip()
                            if not sql:
                                raise ValueError("sql is required")
                            if is_mutating_sql(sql):
                                raise ValueError("only read-only SQL is allowed")
                            output = fixture.execute_readonly(container, sql)
                        elif name == "read_authoritative_source" and case.evidence_requirements:
                            output = policy_path.read_text(encoding="utf-8")
                        else:
                            raise ValueError(f"unknown or unavailable tool: {name}")
                        result = {"success": True, "output": output[:MAX_TOOL_RESULT_CHARS]}
                    except (OSError, RuntimeError, ValueError) as exc:
                        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
                    events.append(
                        {
                            "event_type": "step_result",
                            "payload": {
                                "name": name,
                                "arguments": raw_arguments,
                                "result": result,
                            },
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tool_call.get("id") or "tool-call"),
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
            else:
                events.append(
                    {
                        "event_type": "done",
                        "payload": {
                            "status": "stalled",
                            "metrics": {
                                "input_tokens": total_input_tokens,
                                "output_tokens": total_output_tokens,
                                "llm_calls": llm_calls,
                                "tool_calls": sum(
                                    event.get("event_type") == "step_result" for event in events
                                ),
                                "verification_attempts": 0,
                            },
                        },
                    }
                )
    except CaseDeadlineExceededError:
        case_timed_out = True
        events.append(
            {
                "event_type": "done",
                "payload": {
                    "status": "timed_out",
                    "metrics": {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "llm_calls": llm_calls,
                        "tool_calls": sum(
                            event.get("event_type") == "step_result" for event in events
                        ),
                        "verification_attempts": 0,
                    },
                },
            }
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        stream_error = f"{type(exc).__name__}: {exc}"
        events.append({"event_type": "error", "payload": {"message": stream_error}})

    evidence: dict[str, Any] = {
        "case_id": case.case_id,
        "title": case.title,
        "prompt": case.prompt,
        "attempt": attempt,
        "stream_http_status": 200 if stream_error is None else None,
        "stream_error": stream_error,
        "case_timed_out": case_timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "messages": ([{"role": "assistant", "content": final_answer}] if final_answer else []),
        "events": events,
        "transcript": messages,
    }
    evidence["score"] = score_case(case, evidence).to_dict()
    return HarnessResult(evidence=evidence, runtime_metrics=terminal_metrics(evidence))
