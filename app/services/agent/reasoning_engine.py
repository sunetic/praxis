"""
Unified reasoning engine shared across agent runtimes.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.core.logging import fmt_kv, get_logger
from app.services.agent.context_compressor import ContextCompressor, estimate_messages_tokens
from app.services.llm import RateLimitError, get_llm_client

logger = get_logger("agent.reasoning_engine")

__all__ = [
    "EngineConfig",
    "ReasoningEngine",
    "ReasoningPhase",
    "SimpleToolExecutor",
    "ToolExecutor",
    "VALID_TRANSITIONS",
]


class ReasoningPhase(StrEnum):
    THINKING = "thinking"
    PLANNING = "planning"
    TOOL_RUNNING = "tool_running"
    REFLECTING = "reflecting"
    RESPONDING = "responding"
    ERROR = "error"
    DONE = "done"


VALID_TRANSITIONS: dict[ReasoningPhase, set[ReasoningPhase]] = {
    ReasoningPhase.THINKING: {ReasoningPhase.PLANNING, ReasoningPhase.ERROR},
    ReasoningPhase.PLANNING: {ReasoningPhase.TOOL_RUNNING, ReasoningPhase.RESPONDING, ReasoningPhase.ERROR},
    ReasoningPhase.TOOL_RUNNING: {ReasoningPhase.REFLECTING, ReasoningPhase.ERROR},
    ReasoningPhase.REFLECTING: {
        ReasoningPhase.PLANNING,
        ReasoningPhase.RESPONDING,
        ReasoningPhase.ERROR,
        ReasoningPhase.DONE,
    },
    ReasoningPhase.RESPONDING: {ReasoningPhase.DONE, ReasoningPhase.ERROR},
    ReasoningPhase.ERROR: {ReasoningPhase.DONE},
    ReasoningPhase.DONE: set(),
}


@dataclass(frozen=True)
class EngineConfig:
    max_iterations: int = 50
    max_reflections: int = 2
    max_progress_bonus: int = 8
    max_repeated_tool_rounds: int = 2
    reasoning_config: dict[str, Any] | None = None
    compression_threshold_tokens: int = 60_000
    compression_tail_budget_tokens: int = 20_000


class ToolExecutor(Protocol):
    def preview_tool_start(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        ...

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        ...


class SimpleToolExecutor:
    """Adapter for simple `(tool_name, arguments) -> result` executors."""

    def __init__(
        self,
        executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        self._executor = executor

    def preview_tool_start(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_tool_call(tool_call)
        if not normalized["ok"]:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments_text"],
            }
        return {
            "tool_call_id": normalized["tool_call_id"],
            "name": normalized["name"],
            "arguments": normalized["arguments"],
        }

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_tool_call(tool_call)
        if not normalized["ok"]:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments"],
                "result": {"success": False, "error": normalized["error"]},
                "error_class": "argument_error",
                "batch_boundary_after": False,
            }
        try:
            result = await self._executor(normalized["name"], normalized["arguments"])
        except Exception as exc:
            result = {
                "success": False,
                "error": str(exc),
                "error_class": "execution_error",
            }
        error_class = None
        if isinstance(result, dict):
            error_class = result.get("error_class")
        runtime_meta = normalized["arguments"].get("_runtime") if isinstance(normalized["arguments"].get("_runtime"), dict) else {}
        return {
            "tool_call_id": normalized["tool_call_id"],
            "name": normalized["name"],
            "arguments": normalized["arguments"],
            "result": result,
            "error_class": error_class,
            "batch_boundary_after": bool(runtime_meta.get("batch_boundary_after")),
            "planning_meta": {
                "phase": str(runtime_meta.get("phase") or "").strip(),
                "goal": str(runtime_meta.get("goal") or "").strip(),
                "success_criteria": str(runtime_meta.get("success_criteria") or "").strip(),
            },
        }


class ReasoningEngine:
    def __init__(
        self,
        *,
        config: EngineConfig | None = None,
        llm: Any | None = None,
        tool_executor: ToolExecutor | None = None,
        compressor: ContextCompressor | None = None,
        tool_plan_extractor: Callable[[dict[int, dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.llm = llm or get_llm_client()
        self.tool_executor = tool_executor
        self.compressor = compressor or ContextCompressor(
            threshold_tokens=self.config.compression_threshold_tokens,
            tail_budget_tokens=self.config.compression_tail_budget_tokens,
            llm_client=self.llm,
        )
        self._tool_plan_extractor = tool_plan_extractor

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        chat_messages = list(messages)
        if system_prompt and not any(m.get("role") == "system" for m in chat_messages):
            chat_messages.insert(0, {"role": "system", "content": system_prompt})

        cfg = self.config
        phase = ReasoningPhase.THINKING
        reflection_count = 0
        iteration = 0
        emitted_text = ""
        progress_bonus_iterations = 0
        repeated_tool_rounds = 0
        last_tool_signature = ""
        run_id = str(uuid.uuid4())

        logger.info(
            "reasoning_engine_start %s",
            fmt_kv(run_id=run_id, message_count=len(chat_messages), has_tools=bool(tools)),
        )
        yield _event(
            type_="thinking",
            phase=phase,
            data={"message": "Agent is analyzing the user request"},
            meta={"iteration": 0, "run_id": run_id},
        )

        while iteration < (cfg.max_iterations + progress_bonus_iterations):
            if is_cancelled and is_cancelled():
                logger.info("reasoning_cancelled %s", fmt_kv(run_id=run_id, iteration=iteration))
                break

            iteration += 1

            if self.compressor.should_compress(chat_messages):
                chat_messages = await self.compressor.compress(chat_messages)
                yield _event(
                    type_="context_compressed",
                    phase=phase,
                    data={
                        "message_count": len(chat_messages),
                        "estimated_tokens": estimate_messages_tokens(chat_messages),
                    },
                    meta={"iteration": iteration, "run_id": run_id},
                )

            transition_err = _check_transition(phase, ReasoningPhase.PLANNING)
            if transition_err:
                yield _error_event(
                    phase=phase,
                    message=transition_err,
                    iteration=iteration,
                    run_id=run_id,
                    error_class="invalid_transition",
                )
                phase = ReasoningPhase.ERROR
                break
            phase = ReasoningPhase.PLANNING

            try:
                plan: dict[str, Any] = {}
                async for planner_event in self._planner_step(chat_messages, tools):
                    if planner_event["type"] == "text":
                        emitted_text += planner_event["content"]
                        yield _event(
                            type_="assistant",
                            phase=ReasoningPhase.RESPONDING,
                            data={"text": planner_event["content"], "iteration": iteration},
                            meta={"iteration": iteration, "run_id": run_id},
                        )
                    elif planner_event["type"] == "plan_result":
                        plan = planner_event

                logger.info(
                    "reasoning_engine_planner_complete %s",
                    fmt_kv(
                        run_id=run_id,
                        iteration=iteration,
                        chunk_count=plan.get("chunk_count", 0),
                        has_tool_calls=bool(plan.get("tool_calls")),
                        finish_reason=plan.get("finish_reason"),
                    ),
                )

                yield _event(
                    type_="plan",
                    phase=phase,
                    data={
                        "iteration": iteration,
                        "has_tool_calls": bool(plan["tool_calls"]),
                        "tool_call_count": len(plan["tool_calls"]),
                        "object_action_plan": (
                            self._tool_plan_extractor(plan["tool_calls"])
                            if self._tool_plan_extractor
                            else []
                        ),
                    },
                    meta={"iteration": iteration, "run_id": run_id},
                )

                if plan["tool_calls"]:
                    current_tool_signature = _tool_signature(plan["tool_calls"])
                    if current_tool_signature and current_tool_signature == last_tool_signature:
                        repeated_tool_rounds += 1
                    else:
                        repeated_tool_rounds = 0
                    last_tool_signature = current_tool_signature

                    if repeated_tool_rounds >= cfg.max_repeated_tool_rounds:
                        yield _error_event(
                            phase=phase,
                            message="Detected repeated tool loop without meaningful progress.",
                            iteration=iteration,
                            run_id=run_id,
                            error_class="loop_detected",
                        )
                        phase = ReasoningPhase.ERROR
                        break

                    transition_err = _check_transition(phase, ReasoningPhase.TOOL_RUNNING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.TOOL_RUNNING

                    chat_messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": list(plan["tool_calls"].values()),
                        }
                    )

                    execution_results: list[dict[str, Any]] = []
                    for tool_index, (_, raw_tool_call) in enumerate(sorted(plan["tool_calls"].items(), key=lambda item: item[0])):
                        start_preview = self._preview_tool_start(raw_tool_call)
                        yield _event(
                            type_="tool_start",
                            phase=phase,
                            data={
                                "tool_call_id": start_preview["tool_call_id"],
                                "name": start_preview["name"],
                                "arguments": start_preview["arguments"],
                            },
                            meta={"iteration": iteration, "run_id": run_id},
                        )

                        if is_cancelled and is_cancelled():
                            logger.info("reasoning_cancelled_before_tool %s", fmt_kv(run_id=run_id, iteration=iteration, tool=start_preview["name"]))
                            break

                        prepared_tool_call = (
                            raw_tool_call
                            if raw_tool_call.get("id")
                            else {**raw_tool_call, "id": start_preview["tool_call_id"]}
                        )
                        item = await self._execute_tool(prepared_tool_call)
                        execution_results.append(item)

                        yield _event(
                            type_="tool_result",
                            phase=phase,
                            data={
                                "tool_call_id": item["tool_call_id"],
                                "name": item["name"],
                                "arguments": item["arguments"],
                                "result": item["result"],
                                "error_class": item["error_class"],
                            },
                            meta={
                                "iteration": iteration,
                                "run_id": item.get("run_id") or run_id,
                                "object_id": item.get("object_id"),
                                "release_id": item.get("release_id"),
                            },
                        )

                        chat_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": item["tool_call_id"],
                                "name": item["name"],
                                "content": json.dumps(item["result"], ensure_ascii=False, default=str),
                            }
                        )

                        result_payload = item.get("result") if isinstance(item.get("result"), dict) else {}
                        result_data = (
                            result_payload.get("data")
                            if isinstance(result_payload.get("data"), dict)
                            else {}
                        )
                        if bool(result_data.get("requires_confirmation")):
                            break
                        if bool(item.get("batch_boundary_after")):
                            break

                    if any((item.get("result") or {}).get("success") for item in execution_results):
                        progress_bonus_iterations = min(
                            cfg.max_progress_bonus,
                            progress_bonus_iterations + 1,
                        )

                    transition_err = _check_transition(phase, ReasoningPhase.REFLECTING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.REFLECTING
                    decision = _reflector_step(
                        execution_results=execution_results,
                        reflection_count=reflection_count,
                        max_reflections=cfg.max_reflections,
                    )
                    yield _event(
                        type_="reflect",
                        phase=phase,
                        data=decision,
                        meta={"iteration": iteration, "reflection_count": reflection_count, "run_id": run_id},
                    )

                    if decision["action"] == "retry":
                        reflection_count += 1
                        if reflection_count > cfg.max_reflections:
                            emitted_finalize_text = False
                            async for synth_event in self._synthesize_without_tools(chat_messages):
                                if synth_event["type"] == "text":
                                    emitted_finalize_text = True
                                    emitted_text += synth_event["content"]
                                    yield _event(
                                        type_="assistant",
                                        phase=ReasoningPhase.RESPONDING,
                                        data={"text": synth_event["content"]},
                                        meta={"iteration": iteration, "run_id": run_id},
                                    )
                            if not emitted_finalize_text:
                                fallback_summary = _build_budget_exhausted_summary(chat_messages)
                                if fallback_summary:
                                    emitted_finalize_text = True
                                    emitted_text += fallback_summary
                                    yield _event(
                                        type_="assistant",
                                        phase=ReasoningPhase.RESPONDING,
                                        data={"text": fallback_summary},
                                        meta={
                                            "iteration": iteration,
                                            "run_id": run_id,
                                            "fallback_finalize": True,
                                        },
                                    )
                                    chat_messages.append({"role": "assistant", "content": fallback_summary})
                            phase = ReasoningPhase.RESPONDING if emitted_finalize_text else ReasoningPhase.ERROR
                            break
                        phase = ReasoningPhase.THINKING
                        continue

                    if decision["action"] == "abort":
                        emitted_finalize_text = False
                        async for synth_event in self._synthesize_without_tools(chat_messages):
                            if synth_event["type"] == "text":
                                emitted_finalize_text = True
                                emitted_text += synth_event["content"]
                                yield _event(
                                    type_="assistant",
                                    phase=ReasoningPhase.RESPONDING,
                                    data={"text": synth_event["content"]},
                                    meta={"iteration": iteration, "run_id": run_id},
                                )
                        if not emitted_finalize_text:
                            fallback_summary = _build_budget_exhausted_summary(chat_messages)
                            if fallback_summary:
                                emitted_finalize_text = True
                                emitted_text += fallback_summary
                                yield _event(
                                    type_="assistant",
                                    phase=ReasoningPhase.RESPONDING,
                                    data={"text": fallback_summary},
                                    meta={
                                        "iteration": iteration,
                                        "run_id": run_id,
                                        "fallback_finalize": True,
                                    },
                                )
                                chat_messages.append({"role": "assistant", "content": fallback_summary})
                        phase = ReasoningPhase.RESPONDING if emitted_finalize_text else ReasoningPhase.ERROR
                        break

                    if decision["action"] == "await_confirmation":
                        transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                        if transition_err:
                            raise RuntimeError(transition_err)
                        phase = ReasoningPhase.RESPONDING
                        break

                    phase = ReasoningPhase.THINKING
                    continue

                if plan["assistant_text"]:
                    if plan.get("finish_reason") == "length":
                        continuation_text = ""
                        async for continuation_event in self._continue_truncated_response(
                            chat_messages,
                            plan["assistant_text"],
                        ):
                            if continuation_event["type"] == "text":
                                emitted_text += continuation_event["content"]
                                yield _event(
                                    type_="assistant",
                                    phase=ReasoningPhase.RESPONDING,
                                    data={"text": continuation_event["content"], "iteration": iteration},
                                    meta={"iteration": iteration, "run_id": run_id},
                                )
                            elif continuation_event["type"] == "continuation_result":
                                continuation_text = continuation_event["text"]
                        if continuation_text:
                            plan["assistant_text"] += continuation_text
                        else:
                            truncation_notice = "\n\n(Output may have been truncated due to model length limit)"
                            plan["assistant_text"] += truncation_notice
                            emitted_text += truncation_notice
                            yield _event(
                                type_="assistant",
                                phase=ReasoningPhase.RESPONDING,
                                data={"text": truncation_notice, "iteration": iteration},
                                meta={"iteration": iteration, "run_id": run_id},
                            )

                    transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.RESPONDING
                    chat_messages.append({"role": "assistant", "content": plan["assistant_text"]})
                else:
                    transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.RESPONDING
                break

            except Exception as exc:
                error_class = "rate_limited" if isinstance(exc, RateLimitError) else "runtime_error"
                logger.exception(
                    "reasoning_engine_iteration_error %s error=%s",
                    fmt_kv(run_id=run_id, iteration=iteration),
                    str(exc),
                )
                yield _error_event(
                    phase=phase,
                    message=str(exc),
                    iteration=iteration,
                    run_id=run_id,
                    error_class=error_class,
                )
                phase = ReasoningPhase.ERROR
                break

        active_phases = {
            ReasoningPhase.THINKING,
            ReasoningPhase.PLANNING,
            ReasoningPhase.TOOL_RUNNING,
            ReasoningPhase.REFLECTING,
        }
        if iteration >= (cfg.max_iterations + progress_bonus_iterations) and phase in active_phases:
            synthesis: dict[str, Any] = {}
            async for synth_event in self._synthesize_without_tools(chat_messages):
                if synth_event["type"] == "text":
                    emitted_text += synth_event["content"]
                    yield _event(
                        type_="assistant",
                        phase=ReasoningPhase.RESPONDING,
                        data={"text": synth_event["content"], "iteration": iteration},
                        meta={"iteration": iteration, "forced_finalize": True, "run_id": run_id},
                    )
                elif synth_event["type"] == "synthesis_result":
                    synthesis = synth_event
            if synthesis.get("assistant_text"):
                phase = ReasoningPhase.RESPONDING
                chat_messages.append({"role": "assistant", "content": synthesis["assistant_text"]})
            else:
                fallback_summary = _build_budget_exhausted_summary(chat_messages)
                if fallback_summary:
                    phase = ReasoningPhase.RESPONDING
                    emitted_text += fallback_summary
                    yield _event(
                        type_="assistant",
                        phase=phase,
                        data={"text": fallback_summary, "iteration": iteration},
                        meta={
                            "iteration": iteration,
                            "forced_finalize": True,
                            "fallback_finalize": True,
                            "run_id": run_id,
                        },
                    )
                    chat_messages.append({"role": "assistant", "content": fallback_summary})
                else:
                    yield _error_event(
                        phase=phase,
                        message="Chat iteration limit reached before final response.",
                        iteration=iteration,
                        run_id=run_id,
                        error_class="iteration_limit_exceeded",
                    )
                    phase = ReasoningPhase.ERROR

        if phase not in {ReasoningPhase.ERROR, ReasoningPhase.DONE}:
            transition_err = _check_transition(phase, ReasoningPhase.DONE)
            if transition_err:
                yield _error_event(
                    phase=phase,
                    message=transition_err,
                    iteration=iteration,
                    run_id=run_id,
                    error_class="invalid_transition",
                )
                phase = ReasoningPhase.ERROR
            else:
                phase = ReasoningPhase.DONE

        logger.info(
            "reasoning_engine_complete %s",
            fmt_kv(run_id=run_id, iterations=iteration, final_phase=phase.value),
        )
        yield _event(
            type_="done",
            phase=phase if phase == ReasoningPhase.DONE else ReasoningPhase.ERROR,
            data={"text_emitted": bool(emitted_text.strip())},
            meta={"iteration": iteration, "run_id": run_id},
        )

    async def _planner_step(
        self,
        chat_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        assistant_chunks: list[str] = []
        full_content = ""
        tool_calls_data: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        finish_reason: str | None = None

        kwargs: dict[str, Any] = {}
        if self.config.reasoning_config:
            kwargs["reasoning_config"] = self.config.reasoning_config

        async for chunk in self.llm.chat(chat_messages, tools=tools, stream=True, **kwargs):
            chunk_count += 1
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            choice_finish_reason = choice.get("finish_reason")
            if choice_finish_reason:
                finish_reason = str(choice_finish_reason)
            delta = choice.get("delta", {})

            tool_calls = delta.get("tool_calls")
            content = delta.get("content", "")
            if content and not tool_calls:
                full_content += content
                assistant_chunks.append(content)
                yield {"type": "text", "content": content}

            if tool_calls:
                for tc in tool_calls:
                    index = tc.get("index", 0)
                    if index not in tool_calls_data:
                        tool_calls_data[index] = {
                            "id": tc.get("id"),
                            "type": tc.get("type") or "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.get("id"):
                        tool_calls_data[index]["id"] = tc.get("id")
                    if tc.get("type"):
                        tool_calls_data[index]["type"] = tc.get("type")
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        tool_calls_data[index]["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tool_calls_data[index]["function"]["arguments"] += fn["arguments"]

        if not tool_calls_data and _XML_TOOL_CALL_RE.search(full_content):
            cleaned_text, xml_calls = _extract_xml_tool_calls(full_content)
            if xml_calls:
                logger.info(
                    "xml_tool_calls_extracted %s",
                    fmt_kv(count=len(xml_calls), names=[c["function"]["name"] for c in xml_calls]),
                )
                for i, tc in enumerate(xml_calls):
                    tool_calls_data[len(tool_calls_data) + i] = tc
                full_content = cleaned_text
                assistant_chunks = [cleaned_text] if cleaned_text else []

        yield {
            "type": "plan_result",
            "assistant_chunks": assistant_chunks,
            "assistant_text": full_content,
            "tool_calls": tool_calls_data,
            "chunk_count": chunk_count,
            "finish_reason": finish_reason,
        }

    async def _continue_truncated_response(
        self,
        chat_messages: list[dict[str, Any]],
        partial_text: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        continuation_prompt = (
            "Continue the previous assistant response from exactly where it stopped. "
            "Do not repeat prior content. "
            "Do not call tools. "
            "Return plain text only."
        )
        continuation_messages = list(chat_messages) + [
            {"role": "assistant", "content": partial_text},
            {"role": "system", "content": continuation_prompt},
        ]
        full_text = ""
        async for chunk in self.llm.chat(continuation_messages, tools=None, stream=True):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta", {})
            content = delta.get("content", "")
            if content:
                full_text += content
                yield {"type": "text", "content": content}
        if full_text:
            logger.info("reasoning_engine_continuation_applied extra_chars=%s", len(full_text))
        else:
            logger.warning("reasoning_engine_continuation_empty")
        yield {"type": "continuation_result", "text": full_text}

    async def _synthesize_without_tools(
        self,
        chat_messages: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        guardrail_prompt = (
            "Summarize current findings for the user now. "
            "Do not call any tools. "
            "If data is insufficient, clearly state what is known and what remains unknown."
        )
        synthesis_messages = list(chat_messages) + [{"role": "system", "content": guardrail_prompt}]
        assistant_chunks: list[str] = []
        full_content = ""
        chunk_count = 0
        async for chunk in self.llm.chat(synthesis_messages, tools=None, stream=True):
            chunk_count += 1
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta", {})
            content = delta.get("content", "")
            if content:
                full_content += content
                assistant_chunks.append(content)
                yield {"type": "text", "content": content}
        yield {
            "type": "synthesis_result",
            "assistant_chunks": assistant_chunks,
            "assistant_text": full_content,
            "chunk_count": chunk_count,
        }

    def _preview_tool_start(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        if self.tool_executor is not None:
            return self.tool_executor.preview_tool_start(tool_call)
        normalized = _normalize_tool_call(tool_call)
        if not normalized["ok"]:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments_text"],
            }
        return {
            "tool_call_id": normalized["tool_call_id"],
            "name": normalized["name"],
            "arguments": normalized["arguments"],
        }

    async def _execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        if self.tool_executor is None:
            normalized = _normalize_tool_call(tool_call)
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments"],
                "result": {"success": False, "error": "no tool executor configured"},
                "error_class": "no_executor",
            }
        return await self.tool_executor.execute_tool(tool_call)


_XML_TOOL_CALL_RE = re.compile(
    r"<function=(\w+)>\s*(.*?)\s*</function>\s*(?:</tool_call>)?",
    re.DOTALL,
)
_XML_PARAMETER_RE = re.compile(
    r"<parameter=(\w+)>\s*(.*?)\s*</parameter>",
    re.DOTALL,
)
_TRAILING_TOOL_CALL_RE = re.compile(r"\s*</tool_call>\s*$")


def _extract_xml_tool_calls(
    text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Strip XML-style tool calls from LLM text content and return them as structured calls.

    Some models emit tool invocations as ``<function=name>`` XML in their text
    output instead of using the ``tool_calls`` delta field. This function
    extracts those blocks, returning the cleaned text and a list of tool-call
    dicts compatible with ``tool_calls_data``.
    """
    extracted: list[dict[str, Any]] = []
    for match in _XML_TOOL_CALL_RE.finditer(text):
        func_name = match.group(1)
        body = match.group(2)
        params: dict[str, str] = {}
        for pm in _XML_PARAMETER_RE.finditer(body):
            params[pm.group(1)] = pm.group(2).strip()
        extracted.append({
            "id": f"xmltc_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": func_name,
                "arguments": json.dumps(params, ensure_ascii=False),
            },
        })
    cleaned = _XML_TOOL_CALL_RE.sub("", text)
    cleaned = _TRAILING_TOOL_CALL_RE.sub("", cleaned)
    cleaned = cleaned.rstrip()
    return cleaned, extracted


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") if isinstance(tool_call, dict) else {}
    function = function if isinstance(function, dict) else {}
    name = str(function.get("name") or "")
    arguments_text = function.get("arguments", "")
    tool_call_id = tool_call.get("id") or str(uuid.uuid4())

    if not name:
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": "",
            "arguments": {},
            "arguments_text": arguments_text if isinstance(arguments_text, str) else "{}",
            "error": "Tool call missing function name",
        }

    if not isinstance(arguments_text, str):
        arguments_text = json.dumps(arguments_text, ensure_ascii=False)

    try:
        arguments = json.loads(arguments_text or "{}")
        if not isinstance(arguments, dict):
            return {
                "ok": False,
                "tool_call_id": tool_call_id,
                "name": name,
                "arguments": {},
                "arguments_text": arguments_text,
                "error": "Tool arguments must be a JSON object",
            }
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": {},
            "arguments_text": arguments_text,
            "error": f"Invalid tool arguments: {str(exc)}",
        }

    return {
        "ok": True,
        "tool_call_id": tool_call_id,
        "name": name,
        "arguments": arguments,
        "arguments_text": arguments_text,
        "error": None,
    }


def _check_transition(current: ReasoningPhase, target: ReasoningPhase) -> str:
    if target in VALID_TRANSITIONS.get(current, set()):
        return ""
    return f"Invalid transition: {current.value} -> {target.value}"


def _tool_signature(tool_calls: dict[int, dict[str, Any]]) -> str:
    normalized: list[str] = []
    for _, call in sorted(tool_calls.items(), key=lambda item: item[0]):
        fn = call.get("function") or {}
        normalized.append(f"{str(fn.get('name') or '')}:{str(fn.get('arguments') or '')}")
    return "|".join(normalized)


def _build_budget_exhausted_summary(chat_messages: list[dict[str, Any]]) -> str:
    tool_messages = [message for message in chat_messages if message.get("role") == "tool"]
    if not tool_messages:
        return ""
    total = len(tool_messages)
    failures = 0
    for item in tool_messages:
        try:
            payload = json.loads(str(item.get("content") or "{}"))
            if isinstance(payload, dict) and payload.get("success") is False:
                failures += 1
        except Exception:
            continue
    return (
        f"This task has executed {total} tool call(s). "
        f"Current interim conclusion: partial results obtained, but some items remain incomplete ({failures} failure(s)). "
        "Consider narrowing your analysis scope or specifying a priority target, and I will continue investigating."
    )


def _reflector_step(
    execution_results: list[dict[str, Any]],
    reflection_count: int,
    max_reflections: int,
) -> dict[str, Any]:
    for item in execution_results:
        result_payload = item.get("result") if isinstance(item.get("result"), dict) else {}
        result_data = result_payload.get("data") if isinstance(result_payload.get("data"), dict) else {}
        if bool(result_data.get("requires_confirmation")):
            return {
                "action": "await_confirmation",
                "reason": "pending_confirmation",
                "reason_code": "pending_confirmation",
                "strategy_reason_code": _extract_strategy_reason_code(execution_results),
            }

    failures = [result for result in execution_results if not (result.get("result") or {}).get("success")]
    strategy_reason_code = _extract_strategy_reason_code(execution_results)
    if not failures:
        return {
            "action": "continue",
            "reason": "all_tools_succeeded",
            "reason_code": "all_tools_succeeded",
            "strategy_reason_code": strategy_reason_code,
        }

    schema_failures = [failure for failure in failures if str(failure.get("error_class") or "") == "schema_error"]
    has_unknown_column = any(_is_unknown_column_failure(failure) for failure in schema_failures)
    if has_unknown_column and reflection_count >= 1:
        failure_summary = _summarize_failures(failures)
        return {
            "action": "abort",
            "reason": (
                "Schema recovery did not converge after repeated unknown-column failures."
                + (f" {failure_summary}" if failure_summary else "")
            ),
            "reason_code": "schema_recovery_exhausted",
            "strategy_reason_code": strategy_reason_code,
        }

    if reflection_count >= max_reflections:
        failure_summary = _summarize_failures(failures)
        return {
            "action": "abort",
            "reason": (
                "Retry budget exhausted after tool failures."
                + (f" {failure_summary}" if failure_summary else "")
            ),
            "reason_code": "retry_budget_exhausted",
            "strategy_reason_code": strategy_reason_code,
        }
    return {
        "action": "retry",
        "reason": "tool_failure_detected",
        "reason_code": "tool_failure_detected",
        "strategy_reason_code": strategy_reason_code,
        "failed_tools": [str(failure.get("name") or "unknown_tool") for failure in failures],
    }


def _build_retry_system_hint(
    execution_results: list[dict[str, Any]],
    reflection_count: int,
) -> str:
    failures = [item for item in execution_results if not (item.get("result") or {}).get("success")]
    if not failures:
        return ""
    summary = _summarize_failures(failures, limit=3)
    planning_summary = _summarize_planning_objectives(execution_results)
    schema_failures = [failure for failure in failures if str(failure.get("error_class") or "") == "schema_error"]
    schema_rules = ""
    if schema_failures:
        schema_rules = (
            "- Schema recovery rule: if the last failure is unknown table/unknown column, the next step must ground on confirmed schema first.\n"
            "- After unknown-column failure, do not write a new multi-column or joined SQL based on guessed fields.\n"
            "- First inspect the exact table/view shape with DESCRIBE / SHOW COLUMNS / INFORMATION_SCHEMA, then reuse only confirmed column names.\n"
            "- If schema was just inspected and required columns still do not exist, stop querying and explain the limitation instead of guessing another SQL variant.\n"
        )
    return (
        "Retry Guidance (internal):\n"
        f"- reflection_round: {reflection_count}\n"
        "- At least one tool failed in the previous round.\n"
        "- Analyze failure cause first, then adjust strategy before next tool call.\n"
        "- Re-evaluate whether the last step actually achieved its planning goal; tool success alone is not enough.\n"
        "- Do NOT repeat the exact same failed tool call (same name + same arguments).\n"
        "- If failure indicates unknown table/column, discover available schema first "
        "(SHOW TABLES / INFORMATION_SCHEMA / DESCRIBE), then retry with adapted SQL.\n"
        f"{schema_rules}"
        "- If failure indicates permission/role issue, switch datasource or explain limitation.\n"
        f"- {planning_summary}" + ("\n" if planning_summary else "") +
        f"- {summary}"
    )


def _summarize_failures(failures: list[dict[str, Any]], limit: int = 2) -> str:
    snippets: list[str] = []
    for item in failures[:limit]:
        name = str(item.get("name") or "unknown_tool")
        error_class = str(item.get("error_class") or "execution_error")
        payload = item.get("result") or {}
        error_payload = payload.get("error")
        result_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

        if isinstance(error_payload, dict):
            message = str(error_payload.get("message") or error_payload.get("db_message") or "")
            code = str(error_payload.get("code") or "")
            if code and message:
                detail = f"{code}: {message}"
            else:
                detail = code or message
        else:
            detail = str(error_payload or "")

        # exec_command failures: key info is in data.exit_code / data.stderr, not in error
        if not detail and result_data:
            exit_code = result_data.get("exit_code")
            stderr = str(result_data.get("stderr") or "").strip()
            stdout = str(result_data.get("stdout") or "").strip()
            parts = []
            if exit_code is not None:
                parts.append(f"exit_code={exit_code}")
            if stderr:
                parts.append(f"stderr={stderr[:120]}")
            elif stdout:
                parts.append(f"stdout={stdout[:120]}")
            detail = " ".join(parts)

        detail = " ".join(detail.split())
        if len(detail) > 160:
            detail = f"{detail[:160]}..."
        snippets.append(f"{name}({error_class}) -> {detail or 'unknown error'}")
    return "Last failures: " + " | ".join(snippets) if snippets else ""


def _summarize_planning_objectives(execution_results: list[dict[str, Any]], limit: int = 2) -> str:
    snippets: list[str] = []
    for item in execution_results[:limit]:
        planning_meta = item.get("planning_meta") if isinstance(item.get("planning_meta"), dict) else {}
        goal = str(planning_meta.get("goal") or "").strip()
        criteria = str(planning_meta.get("success_criteria") or "").strip()
        phase = str(planning_meta.get("phase") or "").strip()
        if not goal and not criteria:
            continue
        parts = []
        if phase:
            parts.append(f"phase={phase}")
        if goal:
            parts.append(f"goal={goal}")
        if criteria:
            parts.append(f"success={criteria}")
        snippets.append("; ".join(parts))
    return "Planning objectives: " + " | ".join(snippets) if snippets else ""


def _is_unknown_column_failure(item: dict[str, Any]) -> bool:
    if str(item.get("error_class") or "") != "schema_error":
        return False
    payload = item.get("result") or {}
    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
        text = " ".join(
            [
                str(error_payload.get("code") or ""),
                str(error_payload.get("category") or ""),
                str(error_payload.get("message") or ""),
                str(error_payload.get("db_message") or ""),
            ]
        ).lower()
    else:
        text = str(error_payload or "").lower()
    return "unknown column" in text


def _extract_strategy_reason_code(execution_results: list[dict[str, Any]]) -> str:
    for item in execution_results:
        result = item.get("result") or {}
        result_data = result.get("data")
        if isinstance(result_data, dict):
            for key in ("strategy", "strategy_decision", "decision"):
                value = result_data.get(key)
                if isinstance(value, str) and value in {"reuse", "extend", "create"}:
                    return value
        arguments = item.get("arguments")
        if isinstance(arguments, dict):
            arguments_text = json.dumps(arguments, ensure_ascii=False, default=str).lower()
        else:
            arguments_text = str(arguments or "").lower()
        for candidate in ("reuse", "extend", "create"):
            if candidate in arguments_text:
                return candidate
    return "none"


def _event(
    *,
    type_: str,
    phase: ReasoningPhase,
    data: Any,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": type_,
        "phase": phase.value,
        "data": data,
        "meta": meta or {},
    }


def _error_event(
    *,
    phase: ReasoningPhase,
    message: str,
    iteration: int,
    run_id: str,
    error_class: str,
) -> dict[str, Any]:
    return _event(
        type_="error",
        phase=ReasoningPhase.ERROR if phase != ReasoningPhase.DONE else phase,
        data={"message": message, "error_class": error_class},
        meta={"iteration": iteration, "run_id": run_id, "error_class": error_class},
    )
