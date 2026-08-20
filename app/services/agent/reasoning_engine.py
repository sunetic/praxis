"""
Unified reasoning engine shared across agent runtimes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from textwrap import dedent
from typing import Any, Protocol

from app.core.logging import fmt_kv, get_logger
from app.services.agent.context_compressor import ContextCompressor, estimate_messages_tokens
from app.services.agent.task_contract import TaskContract
from app.services.agent.task_contract_agent import TaskContractAgent, TaskContractBuilder
from app.services.agent.task_runtime import (
    Observation,
    ProgressDecision,
    TaskJournal,
    VerificationResult,
    build_component_evidence_prompt,
    build_verifier_prompt,
    deterministic_completion_precheck,
    enforce_compound_criterion_audit,
    enforce_failure_episode_audit,
    parse_verification_result,
)
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
    ReasoningPhase.PLANNING: {
        ReasoningPhase.TOOL_RUNNING,
        ReasoningPhase.RESPONDING,
        ReasoningPhase.ERROR,
    },
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
    context_window_tokens: int = 128_000
    failure_episode_enabled: bool = True
    task_contract_enabled: bool = True
    completion_verifier_enabled: bool = True
    persistent_journal_enabled: bool = True
    parallel_read_only_enabled: bool = True
    adversarial_verification_enabled: bool = False
    max_transient_retries: int = 3
    max_no_progress_rounds: int = 3
    max_verification_retries: int = 5
    max_parallel_tools: int = 4
    transient_backoff_base_seconds: float = 0.5
    transient_backoff_max_seconds: float = 4.0
    max_elapsed_seconds: float = 900.0


class ToolExecutor(Protocol):
    def preview_tool_start(self, tool_call: dict[str, Any]) -> dict[str, Any]: ...

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]: ...


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
        runtime_meta = (
            normalized["arguments"].get("_runtime")
            if isinstance(normalized["arguments"].get("_runtime"), dict)
            else {}
        )
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
        task_contract_builder: TaskContractBuilder | None = None,
        tool_plan_extractor: Callable[[dict[int, dict[str, Any]]], list[dict[str, Any]]]
        | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.llm = llm or get_llm_client()
        self.tool_executor = tool_executor
        self.task_contract_builder = task_contract_builder or TaskContractAgent(self.llm)
        self.compressor = compressor or ContextCompressor(
            threshold_tokens=self.config.compression_threshold_tokens,
            tail_budget_tokens=self.config.compression_tail_budget_tokens,
            llm_client=self.llm,
        )
        self._tool_plan_extractor = tool_plan_extractor
        self._run_usage: dict[str, int] = {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "max_input_tokens": 0,
        }

    async def run(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        task_state: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        started_at = time.monotonic()
        self._run_usage = {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "max_input_tokens": 0,
        }
        chat_messages = list(messages)
        if system_prompt and not any(
            m.get("role") == "system" and m.get("content") == system_prompt for m in chat_messages
        ):
            chat_messages.insert(0, {"role": "system", "content": system_prompt})
        verification_policies = _extract_completion_verification_policies(system_prompt)

        cfg = self.config
        resumed = cfg.persistent_journal_enabled and isinstance(task_state, dict)
        contract_source = "restored" if resumed else "disabled"
        contract_error_code: str | None = None
        if resumed:
            journal = TaskJournal.from_dict(task_state)
        else:
            if cfg.task_contract_enabled:
                contract_build = await self.task_contract_builder.build(messages)
                contract = contract_build.contract
                contract_source = contract_build.source
                contract_error_code = contract_build.error_code
                self._run_usage["llm_calls"] += contract_build.llm_calls
                self._run_usage["input_tokens"] += contract_build.input_tokens
                self._run_usage["output_tokens"] += contract_build.output_tokens
                self._run_usage["max_input_tokens"] = max(
                    self._run_usage["max_input_tokens"], contract_build.input_tokens
                )
            else:
                contract = TaskContract.unclassified(messages)
            journal = TaskJournal.create(contract)
        if resumed:
            journal.apply_user_correction(messages)
            if journal.status in {"checkpointed", "stalled", "incomplete"}:
                journal.status = "running"
            chat_messages.append({"role": "system", "content": journal.context_block()})
        if journal.contract.complex:
            chat_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Evidence discipline for this complex task: every material claim in the final answer "
                        "must be traceable to tool evidence, explicit task input, or a clearly labelled assumption. "
                        "Show non-obvious derivations, keep observations separate from interpretations, and state "
                        "unknowns instead of inventing facts. The final answer must be self-contained: failed drafts "
                        "are hidden, so never say that an original report or all other findings remain."
                        " For every tool call, keep intent and _runtime.goal concise, user-visible, and in the "
                        "user's language. Describe the immediate objective, not private reasoning."
                    ),
                }
            )
        if tools:
            chat_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Evidence discipline for every tool-backed task: make each material claim traceable "
                        "to the returned evidence or explicit task input. If you derive a value or judgment, "
                        "show the method and distinguish it from a directly observed fact. Treat unsupported "
                        "possibilities as hypotheses, label uncertainty, and do not claim that something is "
                        "absent unless the relevant source was actually inspected."
                    ),
                }
            )
            narration_length = (
                "For a complex task, make the first plan summary roughly 60-120 Chinese "
                "characters (or one to three compact sentences in the user's language). "
                if journal.contract.complex
                else "For a simple task, keep it to one short sentence. "
            )
            chat_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Visible action narration: whenever you call a tool, include a concise, "
                        "natural user-visible transition in assistant content before the tool call. "
                        "Explain what you understand, why this action is useful, and its immediate "
                        "goal. Mention errors and the adjustment when recovering. Do not expose "
                        "private chain-of-thought, raw SQL, JSON, or hidden policy text. "
                        + narration_length
                    ),
                }
            )
        phase = ReasoningPhase.THINKING
        reflection_count = 0
        iteration = 0
        emitted_text = ""
        progress_bonus_iterations = 0
        repeated_tool_rounds = 0
        last_tool_signature = ""
        emitted_progress_notes: set[str] = set()
        initial_progress_emitted = False
        force_evidence_collection = bool(
            tools
            and resumed
            and journal.verification is not None
            and not journal.verification.satisfied
            and journal.verification.repair_type == "new_evidence"
        )
        run_id = str(uuid.uuid4())
        final_status = "running"
        previous_elapsed_ms = journal.metrics.elapsed_ms

        logger.info(
            "reasoning_engine_start %s",
            fmt_kv(
                run_id=run_id,
                task_run_id=journal.task_run_id,
                message_count=len(chat_messages),
                has_tools=bool(tools),
                resumed=resumed,
                contract_source=contract_source,
                contract_error_code=contract_error_code or "",
            ),
        )
        yield _event(
            type_="thinking",
            phase=phase,
            data={"message": "Agent is analyzing the user request"},
            meta={"iteration": 0, "run_id": run_id, "task_run_id": journal.task_run_id},
        )
        if cfg.task_contract_enabled and journal.contract.complex:
            yield _event(
                type_="task_contract",
                phase=phase,
                data={
                    "task_run_id": journal.task_run_id,
                    "contract": journal.contract.to_dict(),
                    "resumed": resumed,
                    "source": contract_source,
                    "degraded": contract_source == "fallback",
                },
                meta={"iteration": 0, "run_id": run_id, "task_run_id": journal.task_run_id},
            )
        while iteration < (cfg.max_iterations + progress_bonus_iterations):
            if is_cancelled and is_cancelled():
                logger.info("reasoning_cancelled %s", fmt_kv(run_id=run_id, iteration=iteration))
                journal.status = "cancelled"
                final_status = "cancelled"
                yield _checkpoint_event(
                    journal=journal,
                    phase=phase,
                    iteration=iteration,
                    run_id=run_id,
                    reason_code="cancelled",
                    reason="执行已取消；当前进度已保存，可在后续继续。",
                )
                phase = ReasoningPhase.DONE
                break
            elapsed_seconds = time.monotonic() - started_at
            if cfg.max_elapsed_seconds > 0 and elapsed_seconds >= cfg.max_elapsed_seconds:
                journal.status = "checkpointed"
                final_status = "incomplete"
                yield _checkpoint_event(
                    journal=journal,
                    phase=phase,
                    iteration=iteration,
                    run_id=run_id,
                    reason_code="time_limit",
                    reason="达到全局执行时限，当前进度已保存。",
                )
                phase = ReasoningPhase.DONE
                break

            iteration += 1
            journal.record_iteration(iteration)

            if self.compressor.should_compress(chat_messages):
                before_compression_tokens = estimate_messages_tokens(chat_messages)
                yield _event(
                    type_="context_status",
                    phase=phase,
                    data={
                        "context_window_tokens": cfg.context_window_tokens,
                        "estimated_tokens": before_compression_tokens,
                        "used_percent": round(
                            min(
                                100.0,
                                before_compression_tokens / cfg.context_window_tokens * 100,
                            ),
                            1,
                        ),
                        "compression_progress_percent": 100.0,
                        "compression_threshold_tokens": cfg.compression_threshold_tokens,
                        "compression_threshold_percent": round(
                            cfg.compression_threshold_tokens / cfg.context_window_tokens * 100
                        ),
                        "remaining_tokens": max(
                            0,
                            cfg.context_window_tokens - before_compression_tokens,
                        ),
                        "token_source": "estimate",
                        "state": "compressing",
                    },
                    meta={
                        "iteration": iteration,
                        "run_id": run_id,
                        "task_run_id": journal.task_run_id,
                    },
                )
                chat_messages = await self.compressor.compress(chat_messages)
                chat_messages.append({"role": "system", "content": journal.context_block()})
                after_compression_tokens = estimate_messages_tokens(chat_messages)
                yield _event(
                    type_="context_compressed",
                    phase=phase,
                    data={
                        "message_count": len(chat_messages),
                        "estimated_tokens": after_compression_tokens,
                        "task_state_preserved": True,
                    },
                    meta={
                        "iteration": iteration,
                        "run_id": run_id,
                        "task_run_id": journal.task_run_id,
                    },
                )
                yield _event(
                    type_="context_status",
                    phase=phase,
                    data={
                        "context_window_tokens": cfg.context_window_tokens,
                        "estimated_tokens": after_compression_tokens,
                        "used_percent": round(
                            min(
                                100.0,
                                after_compression_tokens / cfg.context_window_tokens * 100,
                            ),
                            1,
                        ),
                        "compression_progress_percent": round(
                            min(
                                100.0,
                                after_compression_tokens / cfg.compression_threshold_tokens * 100,
                            ),
                            1,
                        ),
                        "compression_threshold_tokens": cfg.compression_threshold_tokens,
                        "compression_threshold_percent": round(
                            cfg.compression_threshold_tokens / cfg.context_window_tokens * 100
                        ),
                        "remaining_tokens": max(
                            0,
                            cfg.context_window_tokens - after_compression_tokens,
                        ),
                        "token_source": "estimate",
                        "state": "ready",
                    },
                    meta={
                        "iteration": iteration,
                        "run_id": run_id,
                        "task_run_id": journal.task_run_id,
                    },
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
                journal.status = "error"
                final_status = "error"
                phase = ReasoningPhase.ERROR
                break
            phase = ReasoningPhase.PLANNING

            try:
                plan: dict[str, Any] = {}
                planner_chunks: list[str] = []
                async for planner_event in self._planner_step(
                    chat_messages,
                    tools,
                    require_tool_call=force_evidence_collection,
                ):
                    if planner_event["type"] == "text":
                        planner_chunks.append(planner_event["content"])
                    elif planner_event["type"] == "plan_result":
                        plan = planner_event

                if not plan:
                    raise RuntimeError("Planner returned no result")

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
                    meta={
                        "iteration": iteration,
                        "run_id": run_id,
                        "task_run_id": journal.task_run_id,
                    },
                )

                if force_evidence_collection and not plan["tool_calls"]:
                    verification_no_progress_rounds = journal.record_verification_outcome(
                        satisfied=False
                    )
                    chat_messages.extend(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Evidence collection was required, but no tool was called. "
                                    "Do not write or revise the report. Your next response must invoke one or "
                                    "more read-only tools that directly address the unresolved verification gaps. "
                                    "The tool request must measure or inspect the missing fact rather than merely "
                                    "restate the desired conclusion."
                                ),
                            },
                        ]
                    )
                    if cfg.persistent_journal_enabled:
                        yield _task_state_event(journal, phase, iteration, run_id)
                    if verification_no_progress_rounds < cfg.max_verification_retries:
                        phase = ReasoningPhase.THINKING
                        continue

                    journal.status = "checkpointed"
                    final_status = "incomplete"
                    yield _checkpoint_event(
                        journal=journal,
                        phase=phase,
                        iteration=iteration,
                        run_id=run_id,
                        reason_code="verification_evidence_stalled",
                        reason="验证失败后连续多轮未调用工具补充证据。",
                    )
                    verification = journal.verification or VerificationResult(
                        satisfied=False,
                        reason="验证缺口尚未补齐。",
                        missing=["Collect new tool evidence for the verification gaps."],
                    )
                    fallback_summary = _build_verification_checkpoint_summary(
                        verification,
                        journal,
                    )
                    emitted_text += fallback_summary
                    yield _event(
                        type_="assistant",
                        phase=ReasoningPhase.RESPONDING,
                        data={"text": fallback_summary, "incomplete": True},
                        meta={
                            "iteration": iteration,
                            "run_id": run_id,
                            "task_run_id": journal.task_run_id,
                            "checkpointed": True,
                        },
                    )
                    phase = ReasoningPhase.ERROR
                    break

                if plan["tool_calls"]:
                    force_evidence_collection = False
                    current_tool_signature = _tool_signature(plan["tool_calls"])
                    if current_tool_signature and current_tool_signature == last_tool_signature:
                        repeated_tool_rounds += 1
                    else:
                        repeated_tool_rounds = 0
                    last_tool_signature = current_tool_signature

                    if repeated_tool_rounds >= cfg.max_repeated_tool_rounds:
                        journal.status = "stalled"
                        final_status = "stalled"
                        yield _checkpoint_event(
                            journal=journal,
                            phase=phase,
                            iteration=iteration,
                            run_id=run_id,
                            reason_code="repeated_tool_loop",
                            reason="检测到相同工具调用连续重复且没有新进展。",
                        )
                        yield _error_event(
                            phase=phase,
                            message="检测到相同工具调用连续重复且没有新进展，已保存检查点。",
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
                            "content": plan.get("assistant_text") or "",
                            "tool_calls": [
                                _sanitize_tool_call_for_history(tool_call)
                                for tool_call in plan["tool_calls"].values()
                            ],
                        }
                    )

                    ordered_calls = [
                        raw_tool_call
                        for _, raw_tool_call in sorted(
                            plan["tool_calls"].items(), key=lambda item: item[0]
                        )
                    ]
                    if not initial_progress_emitted:
                        initial_progress_note = _build_initial_progress_note(journal)
                        emitted_progress_notes.add(initial_progress_note)
                        initial_progress_emitted = True
                        yield _event(
                            type_="assistant_progress",
                            phase=phase,
                            data={"text": initial_progress_note, "stage": "planning"},
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )
                    progress_note = _build_plan_progress_note(
                        journal,
                        ordered_calls,
                        iteration=iteration,
                        model_narration=plan.get("assistant_text"),
                    )
                    if progress_note and progress_note not in emitted_progress_notes:
                        emitted_progress_notes.add(progress_note)
                        yield _event(
                            type_="assistant_progress",
                            phase=phase,
                            data={"text": progress_note, "stage": "acting"},
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )
                    execution_results: list[dict[str, Any]] = []
                    run_parallel = cfg.parallel_read_only_enabled and _can_parallelize_tool_calls(
                        ordered_calls, max_parallel_tools=cfg.max_parallel_tools
                    )

                    prepared_calls: list[dict[str, Any]] = []
                    for raw_tool_call in ordered_calls:
                        start_preview = self._preview_tool_start(raw_tool_call)
                        yield _event(
                            type_="tool_start",
                            phase=phase,
                            data={
                                "tool_call_id": start_preview["tool_call_id"],
                                "name": start_preview["name"],
                                "arguments": start_preview["arguments"],
                                "parallel": run_parallel,
                            },
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )

                        if is_cancelled and is_cancelled():
                            logger.info(
                                "reasoning_cancelled_before_tool %s",
                                fmt_kv(
                                    run_id=run_id, iteration=iteration, tool=start_preview["name"]
                                ),
                            )
                            break

                        prepared_tool_call = (
                            raw_tool_call
                            if raw_tool_call.get("id")
                            else {**raw_tool_call, "id": start_preview["tool_call_id"]}
                        )
                        prepared_calls.append(prepared_tool_call)

                        if run_parallel:
                            continue

                        item = await self._execute_tool(prepared_tool_call)
                        execution_results.append(item)

                        async for result_event in self._emit_tool_result(
                            item=item,
                            chat_messages=chat_messages,
                            iteration=iteration,
                            run_id=run_id,
                            task_run_id=journal.task_run_id,
                            parallel=False,
                        ):
                            yield result_event

                        if _requires_confirmation(item) or bool(item.get("batch_boundary_after")):
                            break

                    if run_parallel and prepared_calls:
                        execution_results = list(
                            await asyncio.gather(
                                *(self._execute_tool(tool_call) for tool_call in prepared_calls)
                            )
                        )
                        for item in execution_results:
                            async for result_event in self._emit_tool_result(
                                item=item,
                                chat_messages=chat_messages,
                                iteration=iteration,
                                run_id=run_id,
                                task_run_id=journal.task_run_id,
                                parallel=True,
                            ):
                                yield result_event

                    observations = [Observation.from_execution(item) for item in execution_results]
                    if observations and journal.metrics.time_to_first_evidence_ms is None:
                        journal.metrics.time_to_first_evidence_ms = (
                            time.monotonic() - started_at
                        ) * 1000

                    transition_err = _check_transition(phase, ReasoningPhase.REFLECTING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.REFLECTING
                    journal_decision = journal.evaluate_observations(
                        observations,
                        iteration=iteration,
                        per_episode_retry_budget=cfg.max_reflections,
                        transient_retry_budget=cfg.max_transient_retries,
                        max_no_progress_rounds=cfg.max_no_progress_rounds,
                    )
                    if cfg.failure_episode_enabled:
                        decision = journal_decision
                    else:
                        decision = _reflector_step(
                            execution_results=execution_results,
                            reflection_count=reflection_count,
                            max_reflections=cfg.max_reflections,
                        )
                    decision["remaining_global_budget"] = max(
                        0, cfg.max_iterations + progress_bonus_iterations - iteration
                    )
                    if decision.get("decision") == ProgressDecision.TRANSIENT_FAILURE:
                        episode_attempt = max(1, int(decision.get("failure_episode_attempts") or 1))
                        decision["retry_after_seconds"] = min(
                            cfg.transient_backoff_max_seconds,
                            cfg.transient_backoff_base_seconds * (2 ** (episode_attempt - 1)),
                        )
                    if decision.get("decision") == ProgressDecision.PROGRESS:
                        progress_bonus_iterations = min(
                            cfg.max_progress_bonus,
                            progress_bonus_iterations + 1,
                        )
                    yield _event(
                        type_="progress",
                        phase=phase,
                        data=decision,
                        meta={
                            "iteration": iteration,
                            "run_id": run_id,
                            "task_run_id": journal.task_run_id,
                        },
                    )
                    yield _event(
                        type_="reflect",
                        phase=phase,
                        data=decision,
                        meta={
                            "iteration": iteration,
                            "reflection_count": reflection_count,
                            "run_id": run_id,
                            "task_run_id": journal.task_run_id,
                        },
                    )
                    observation_note = _build_observation_progress_note(
                        journal,
                        observations,
                        decision,
                    )
                    if observation_note and observation_note not in emitted_progress_notes:
                        emitted_progress_notes.add(observation_note)
                        yield _event(
                            type_="assistant_progress",
                            phase=phase,
                            data={"text": observation_note, "stage": "reflecting"},
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )
                    if cfg.persistent_journal_enabled:
                        yield _task_state_event(journal, phase, iteration, run_id)

                    if decision["action"] == "retry":
                        reflection_count += 1
                        episode_attempt = int(decision.get("failure_episode_attempts") or 1)
                        retry_hint = _build_retry_system_hint(execution_results, episode_attempt)
                        if retry_hint:
                            chat_messages.append({"role": "system", "content": retry_hint})
                        retry_after = float(decision.get("retry_after_seconds") or 0.0)
                        if retry_after > 0:
                            await asyncio.sleep(retry_after)
                        phase = ReasoningPhase.THINKING
                        continue

                    if decision["action"] == "abort":
                        final_status = journal.status
                        yield _checkpoint_event(
                            journal=journal,
                            phase=phase,
                            iteration=iteration,
                            run_id=run_id,
                            reason_code=str(decision.get("reason_code") or "stalled"),
                            reason=str(decision.get("reason") or "当前故障链无法继续。"),
                        )
                        fallback_summary = _build_checkpoint_summary(journal, decision)
                        emitted_text += fallback_summary
                        yield _event(
                            type_="assistant",
                            phase=ReasoningPhase.RESPONDING,
                            data={"text": fallback_summary, "incomplete": True},
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                                "checkpointed": True,
                            },
                        )
                        chat_messages.append({"role": "assistant", "content": fallback_summary})
                        phase = ReasoningPhase.RESPONDING
                        break

                    if decision["action"] == "await_confirmation":
                        pending_item = next(
                            (
                                item
                                for item in execution_results
                                if isinstance((item.get("result") or {}).get("data"), dict)
                                and (item.get("result") or {})["data"].get("requires_confirmation")
                            ),
                            None,
                        )
                        if pending_item:
                            args_raw = pending_item.get("arguments") or {}
                            if isinstance(args_raw, str):
                                try:
                                    args: dict[str, Any] = json.loads(args_raw)
                                except (json.JSONDecodeError, ValueError):
                                    args = {}
                            elif isinstance(args_raw, dict):
                                args = args_raw
                            else:
                                args = {}
                            result_data = (pending_item.get("result") or {}).get("data") or {}
                            intent = str(args.get("intent") or "").strip()
                            sql_preview = str(
                                result_data.get("sql_preview") or args.get("sql") or ""
                            ).strip()
                            if sql_preview:
                                preview_parts = []
                                if intent:
                                    preview_parts.append(intent)
                                preview_parts.append(f"```sql\n{sql_preview}\n```")
                                preview_text = "\n\n".join(preview_parts)
                                yield _event(
                                    type_="assistant",
                                    phase=ReasoningPhase.RESPONDING,
                                    data={"text": preview_text},
                                    meta={
                                        "iteration": iteration,
                                        "run_id": run_id,
                                        "task_run_id": journal.task_run_id,
                                    },
                                )
                                emitted_text += preview_text
                        final_status = journal.status
                        yield _checkpoint_event(
                            journal=journal,
                            phase=phase,
                            iteration=iteration,
                            run_id=run_id,
                            reason_code=str(decision.get("reason_code") or "await_confirmation"),
                            reason=str(decision.get("reason") or "等待用户确认。"),
                        )
                        transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                        if transition_err:
                            raise RuntimeError(transition_err)
                        phase = ReasoningPhase.RESPONDING
                        break

                    phase = ReasoningPhase.THINKING
                    continue

                candidate_text = str(plan.get("assistant_text") or "")
                if candidate_text:
                    if plan.get("finish_reason") == "length":
                        continuation_text = ""
                        async for continuation_event in self._continue_truncated_response(
                            chat_messages,
                            candidate_text,
                        ):
                            if continuation_event["type"] == "continuation_result":
                                continuation_text = continuation_event["text"]
                        if continuation_text:
                            candidate_text += continuation_text
                        else:
                            truncation_notice = (
                                "\n\n(Output may have been truncated due to model length limit)"
                            )
                            candidate_text += truncation_notice

                    should_verify = bool(
                        cfg.completion_verifier_enabled
                        and (
                            (cfg.task_contract_enabled and journal.contract.complex)
                            or bool(verification_policies)
                            or journal.unresolved_failure_episodes()
                            or journal.unresolved_steps()
                            or journal.user_corrections
                        )
                    )
                    if should_verify:
                        verification_note = _build_verification_progress_note(
                            journal,
                            satisfied=None,
                        )
                        if verification_note not in emitted_progress_notes:
                            emitted_progress_notes.add(verification_note)
                            yield _event(
                                type_="assistant_progress",
                                phase=phase,
                                data={"text": verification_note, "stage": "verifying"},
                                meta={
                                    "iteration": iteration,
                                    "run_id": run_id,
                                    "task_run_id": journal.task_run_id,
                                },
                            )
                        yield _event(
                            type_="progress",
                            phase=phase,
                            data={
                                "action": "verify",
                                "decision": ProgressDecision.CANDIDATE_COMPLETE,
                                "reason_code": "candidate_complete",
                                "reason": "模型已生成候选答案，正在核对任务验收条件。",
                                "task_run_id": journal.task_run_id,
                                "evidence_refs": [item.ref for item in journal.evidence[-20:]],
                            },
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )
                        verification = await self._verify_candidate(
                            journal,
                            candidate_text,
                            verification_policies=verification_policies,
                        )
                        journal.verification = verification
                        verification_no_progress_rounds = journal.record_verification_outcome(
                            satisfied=verification.satisfied
                        )
                        yield _event(
                            type_="verification",
                            phase=phase,
                            data={
                                "task_run_id": journal.task_run_id,
                                **verification.to_dict(),
                                "evidence_count": len(journal.evidence),
                                "no_progress_rounds": verification_no_progress_rounds,
                            },
                            meta={
                                "iteration": iteration,
                                "run_id": run_id,
                                "task_run_id": journal.task_run_id,
                            },
                        )
                        if cfg.persistent_journal_enabled:
                            yield _task_state_event(journal, phase, iteration, run_id)
                        if not verification.satisfied:
                            if verification_no_progress_rounds < cfg.max_verification_retries:
                                verification_note = _build_verification_progress_note(
                                    journal,
                                    satisfied=False,
                                )
                                if verification_note not in emitted_progress_notes:
                                    emitted_progress_notes.add(verification_note)
                                    yield _event(
                                        type_="assistant_progress",
                                        phase=phase,
                                        data={
                                            "text": verification_note,
                                            "stage": "recovering",
                                        },
                                        meta={
                                            "iteration": iteration,
                                            "run_id": run_id,
                                            "task_run_id": journal.task_run_id,
                                        },
                                    )
                                feedback = _build_verification_feedback(verification)
                                if verification.evaluator == "deterministic_action_evidence":
                                    chat_messages.append(
                                        {
                                            "role": "system",
                                            "content": (
                                                "IMMEDIATE NEXT ACTION: satisfy the explicit action-evidence gap "
                                                "before any further diagnosis. Your next tool call must dispatch "
                                                "the user's original fenced payload verbatim through the relevant "
                                                "tool exactly once. Do not issue a smaller probe, test individual "
                                                "parts, rewrite the payload, or submit another candidate first."
                                            ),
                                        }
                                    )
                                # Keep the rejected draft hidden from the user, but retain it in
                                # an explicitly internal revision block.  Adding it as a normal
                                # assistant turn makes some models answer the verifier as though
                                # it were a user; the system wrapper makes the required operation
                                # unambiguous while still giving the rewriter the exact draft.
                                chat_messages.append(
                                    {
                                        "role": "system",
                                        "content": _build_private_revision_context(
                                            candidate_text,
                                            feedback,
                                        ),
                                    }
                                )
                                if journal.metrics.verification_attempts % 3 == 0:
                                    chat_messages.append(
                                        {"role": "system", "content": journal.context_block()}
                                    )
                                force_evidence_collection = bool(
                                    tools and _verification_requires_new_evidence(verification)
                                )
                                phase = ReasoningPhase.THINKING
                                continue

                            journal.status = "checkpointed"
                            final_status = "incomplete"
                            yield _checkpoint_event(
                                journal=journal,
                                phase=phase,
                                iteration=iteration,
                                run_id=run_id,
                                reason_code="verification_incomplete",
                                reason=verification.reason or "候选答案未覆盖全部验收条件。",
                            )
                            fallback_summary = _build_verification_checkpoint_summary(
                                verification,
                                journal,
                            )
                            emitted_text += fallback_summary
                            yield _event(
                                type_="assistant",
                                phase=ReasoningPhase.RESPONDING,
                                data={"text": fallback_summary, "incomplete": True},
                                meta={
                                    "iteration": iteration,
                                    "run_id": run_id,
                                    "task_run_id": journal.task_run_id,
                                    "checkpointed": True,
                                },
                            )
                            chat_messages.append({"role": "assistant", "content": fallback_summary})
                            phase = ReasoningPhase.RESPONDING
                            break

                        verification_note = _build_verification_progress_note(
                            journal,
                            satisfied=True,
                        )
                        if verification_note not in emitted_progress_notes:
                            emitted_progress_notes.add(verification_note)
                            yield _event(
                                type_="assistant_progress",
                                phase=phase,
                                data={"text": verification_note, "stage": "verified"},
                                meta={
                                    "iteration": iteration,
                                    "run_id": run_id,
                                    "task_run_id": journal.task_run_id,
                                },
                            )

                    transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.RESPONDING
                    emitted_text += candidate_text
                    yield _event(
                        type_="assistant",
                        phase=phase,
                        data={"text": candidate_text, "iteration": iteration},
                        meta={
                            "iteration": iteration,
                            "run_id": run_id,
                            "task_run_id": journal.task_run_id,
                            "verified": should_verify,
                        },
                    )
                    chat_messages.append({"role": "assistant", "content": candidate_text})
                    journal.status = "completed"
                    final_status = "completed"
                else:
                    transition_err = _check_transition(phase, ReasoningPhase.RESPONDING)
                    if transition_err:
                        raise RuntimeError(transition_err)
                    phase = ReasoningPhase.RESPONDING
                    journal.status = "completed"
                    final_status = "completed"
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
                journal.status = "error"
                final_status = "error"
                phase = ReasoningPhase.ERROR
                break

        active_phases = {
            ReasoningPhase.THINKING,
            ReasoningPhase.PLANNING,
            ReasoningPhase.TOOL_RUNNING,
            ReasoningPhase.REFLECTING,
        }
        if iteration >= (cfg.max_iterations + progress_bonus_iterations) and phase in active_phases:
            journal.status = "checkpointed"
            final_status = "incomplete"
            yield _checkpoint_event(
                journal=journal,
                phase=phase,
                iteration=iteration,
                run_id=run_id,
                reason_code="iteration_limit",
                reason="达到全局迭代上限，当前进度已保存。",
            )
            synthesis: dict[str, Any] = {}
            async for synth_event in self._synthesize_without_tools(chat_messages):
                if synth_event["type"] == "text":
                    emitted_text += synth_event["content"]
                    yield _event(
                        type_="assistant",
                        phase=ReasoningPhase.RESPONDING,
                        data={
                            "text": synth_event["content"],
                            "iteration": iteration,
                            "incomplete": True,
                        },
                        meta={
                            "iteration": iteration,
                            "forced_finalize": True,
                            "checkpointed": True,
                            "run_id": run_id,
                            "task_run_id": journal.task_run_id,
                        },
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
                            "task_run_id": journal.task_run_id,
                            "checkpointed": True,
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
                    journal.status = "error"
                    final_status = "error"

        if not emitted_text.strip():
            if final_status == "completed":
                journal.status = "checkpointed"
                final_status = "incomplete"
            terminal_summary = _build_terminal_summary(journal, final_status)
            emitted_text += terminal_summary
            yield _event(
                type_="assistant",
                phase=ReasoningPhase.RESPONDING,
                data={
                    "text": terminal_summary,
                    "incomplete": final_status != "completed",
                    "status": final_status,
                },
                meta={
                    "iteration": iteration,
                    "run_id": run_id,
                    "task_run_id": journal.task_run_id,
                    "terminal_fallback": True,
                },
            )

        journal.metrics.llm_calls += self._run_usage["llm_calls"]
        journal.metrics.input_tokens += self._run_usage["input_tokens"]
        journal.metrics.output_tokens += self._run_usage["output_tokens"]
        journal.metrics.elapsed_ms = previous_elapsed_ms + (time.monotonic() - started_at) * 1000

        max_input_tokens = self._run_usage["max_input_tokens"]
        if max_input_tokens > 0:
            used_percent = round(
                min(100.0, max_input_tokens / cfg.context_window_tokens * 100),
                1,
            )
            yield _event(
                type_="context_status",
                phase=phase,
                data={
                    "context_window_tokens": cfg.context_window_tokens,
                    "estimated_tokens": max_input_tokens,
                    "used_percent": used_percent,
                    "compression_progress_percent": round(
                        min(
                            100.0,
                            max_input_tokens / cfg.compression_threshold_tokens * 100,
                        ),
                        1,
                    ),
                    "compression_threshold_tokens": cfg.compression_threshold_tokens,
                    "compression_threshold_percent": round(
                        cfg.compression_threshold_tokens / cfg.context_window_tokens * 100
                    ),
                    "remaining_tokens": max(0, cfg.context_window_tokens - max_input_tokens),
                    "token_source": "provider",
                    "state": "ready",
                },
                meta={
                    "iteration": iteration,
                    "run_id": run_id,
                    "task_run_id": journal.task_run_id,
                },
            )

        if cfg.persistent_journal_enabled:
            yield _task_state_event(journal, phase, iteration, run_id)

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
                journal.status = "error"
                final_status = "error"
            else:
                phase = ReasoningPhase.DONE

        logger.info(
            "reasoning_engine_complete %s",
            fmt_kv(run_id=run_id, iterations=iteration, final_phase=phase.value),
        )
        yield _event(
            type_="done",
            phase=phase if phase == ReasoningPhase.DONE else ReasoningPhase.ERROR,
            data={
                "text_emitted": bool(emitted_text.strip()),
                "status": final_status if final_status != "running" else journal.status,
                "completed": journal.status == "completed",
                "task_run_id": journal.task_run_id,
                "metrics": journal.to_dict()["metrics"],
            },
            meta={
                "iteration": iteration,
                "run_id": run_id,
                "task_run_id": journal.task_run_id,
            },
        )

    async def _emit_tool_result(
        self,
        *,
        item: dict[str, Any],
        chat_messages: list[dict[str, Any]],
        iteration: int,
        run_id: str,
        task_run_id: str,
        parallel: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield _event(
            type_="tool_result",
            phase=ReasoningPhase.TOOL_RUNNING,
            data={
                "tool_call_id": item["tool_call_id"],
                "name": item["name"],
                "arguments": item["arguments"],
                "result": item["result"],
                "error_class": item["error_class"],
                "parallel": parallel,
            },
            meta={
                "iteration": iteration,
                "run_id": item.get("run_id") or run_id,
                "engine_run_id": run_id,
                "task_run_id": task_run_id,
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

    async def _verify_candidate(
        self,
        journal: TaskJournal,
        candidate_text: str,
        *,
        verification_policies: list[str] | None = None,
    ) -> VerificationResult:
        journal.metrics.verification_attempts += 1
        self_containment = _candidate_self_containment_precheck(journal, candidate_text)
        if self_containment is not None:
            return self_containment
        precheck = deterministic_completion_precheck(journal)
        if not precheck.satisfied:
            return precheck

        primary = await self._run_verifier(
            build_verifier_prompt(
                journal,
                candidate_text,
                verification_policies=verification_policies,
            ),
            evaluator="llm",
        )
        journal.apply_failure_assessments(primary)
        primary = enforce_failure_episode_audit(journal, primary)
        if not primary.satisfied:
            return primary
        compound_audit = enforce_compound_criterion_audit(journal, primary)
        if (
            not compound_audit.satisfied
            and compound_audit.evaluator == "compound_criterion_audit"
            and compound_audit.reason == "复合验收项没有逐项完成审计。"
        ):
            retry_prompt = build_verifier_prompt(
                journal,
                candidate_text,
                verification_policies=verification_policies,
            )
            retry_prompt += (
                "\n\nVERIFIER FORMAT REPAIR: The previous verifier returned satisfied=true but "
                "omitted mandatory component_results for one or more compound criteria. Re-audit the candidate "
                "from the evidence. For every compound non-action criterion, enumerate each named component in "
                "component_results and judge it independently. If any component lacks evidence, set the top-level "
                "and criterion satisfied fields to false and name that component in missing. Do not ask the task "
                "executor for work merely to repair verifier JSON."
            )
            primary = await self._run_verifier(
                retry_prompt,
                evaluator="llm_compound_retry",
            )
            if not primary.satisfied:
                return primary
            compound_audit = enforce_compound_criterion_audit(journal, primary)
        if not compound_audit.satisfied:
            return compound_audit
        primary = compound_audit
        if any(
            item.required and not item.requires_tool_evidence and len(item.component_hints) >= 2
            for item in journal.contract.acceptance_criteria
        ):
            component_evidence = await self._run_verifier(
                build_component_evidence_prompt(journal, primary, candidate_text),
                evaluator="component_evidence_llm",
            )
            if not component_evidence.satisfied:
                return component_evidence
        if self.config.adversarial_verification_enabled and journal.contract.high_value:
            arithmetic = await self._run_verifier(
                build_verifier_prompt(
                    journal,
                    candidate_text,
                    arithmetic=True,
                    verification_policies=verification_policies,
                ),
                evaluator="arithmetic_llm",
            )
            if not arithmetic.satisfied:
                return arithmetic
            adversarial = await self._run_verifier(
                build_verifier_prompt(
                    journal,
                    candidate_text,
                    adversarial=True,
                    verification_policies=verification_policies,
                ),
                evaluator="adversarial_llm",
            )
            if not adversarial.satisfied:
                return adversarial
            primary.evaluator = "llm+arithmetic_llm+adversarial_llm"
        return primary

    async def _run_verifier(self, prompt: str, *, evaluator: str) -> VerificationResult:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a completion verifier, not the task executor. "
                    "Tools are disabled. Return strict JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        content = ""
        try:
            self._run_usage["llm_calls"] += 1
            async for chunk in self.llm.chat(messages, tools=None, stream=True):
                self._record_chunk_usage(chunk)
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                content += str(delta.get("content") or message.get("content") or "")
        except Exception as exc:
            logger.warning("completion_verifier_failed evaluator=%s error=%s", evaluator, str(exc))
            return VerificationResult(
                satisfied=False,
                reason=f"完成验证器调用失败：{exc}",
                missing=["Retry completion verification."],
                repair_type="blocked",
                evaluator=evaluator,
                malformed=True,
            )
        return parse_verification_result(content, evaluator=evaluator)

    def _record_chunk_usage(self, chunk: dict[str, Any]) -> None:
        usage = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self._run_usage["input_tokens"] += input_tokens
        self._run_usage["max_input_tokens"] = max(self._run_usage["max_input_tokens"], input_tokens)
        self._run_usage["output_tokens"] += int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )

    async def _planner_step(
        self,
        chat_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        require_tool_call: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        assistant_chunks: list[str] = []
        full_content = ""
        tool_calls_data: dict[int, dict[str, Any]] = {}
        chunk_count = 0
        finish_reason: str | None = None

        kwargs: dict[str, Any] = {}
        if self.config.reasoning_config:
            kwargs["reasoning_config"] = self.config.reasoning_config
        if require_tool_call and tools:
            kwargs["tool_choice"] = "required"

        self._run_usage["llm_calls"] += 1
        async for chunk in self.llm.chat(chat_messages, tools=tools, stream=True, **kwargs):
            self._record_chunk_usage(chunk)
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
        self._run_usage["llm_calls"] += 1
        async for chunk in self.llm.chat(continuation_messages, tools=None, stream=True):
            self._record_chunk_usage(chunk)
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
        self._run_usage["llm_calls"] += 1
        async for chunk in self.llm.chat(synthesis_messages, tools=None, stream=True):
            self._record_chunk_usage(chunk)
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


def _requires_confirmation(item: dict[str, Any]) -> bool:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return bool(data.get("requires_confirmation"))


def _can_parallelize_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    max_parallel_tools: int,
) -> bool:
    if len(tool_calls) < 2 or len(tool_calls) > max(1, max_parallel_tools):
        return False
    return all(_is_read_only_tool_call(tool_call) for tool_call in tool_calls)


def _is_read_only_tool_call(tool_call: dict[str, Any]) -> bool:
    normalized = _normalize_tool_call(tool_call)
    if not normalized["ok"]:
        return False
    name = str(normalized["name"] or "").lower()
    arguments = normalized["arguments"]
    runtime = arguments.get("_runtime") if isinstance(arguments.get("_runtime"), dict) else {}
    if runtime.get("batch_boundary_after"):
        return False
    if name == "execute_sql":
        sql = str(arguments.get("sql") or "").strip()
        if not re.match(r"^(?:select|show|describe|desc|explain|with)\b", sql, re.I):
            return False
        return not bool(
            re.search(
                r"\b(?:insert|update|delete|replace|merge|alter|drop|truncate|create|grant|revoke|call)\b",
                sql,
                re.I,
            )
        )
    if name == "call_praxis_service":
        return str(arguments.get("method") or "GET").upper() in {"GET", "HEAD", "OPTIONS"}
    if name.startswith(("get_", "list_", "search_", "describe_", "inspect_", "query_")):
        return True
    return False


def _task_state_event(
    journal: TaskJournal,
    phase: ReasoningPhase,
    iteration: int,
    run_id: str,
) -> dict[str, Any]:
    return _event(
        type_="task_state",
        phase=phase,
        data=journal.to_dict(),
        meta={
            "iteration": iteration,
            "run_id": run_id,
            "task_run_id": journal.task_run_id,
            "task_state_version": journal.version,
        },
    )


def _checkpoint_event(
    *,
    journal: TaskJournal,
    phase: ReasoningPhase,
    iteration: int,
    run_id: str,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    return _event(
        type_="checkpoint",
        phase=phase,
        data={
            "task_run_id": journal.task_run_id,
            "status": journal.status,
            "reason_code": reason_code,
            "reason": reason,
            "resumable": journal.status not in {"completed", "error"},
            "remaining_work": _remaining_work(journal),
            "task_state": journal.to_dict(),
        },
        meta={
            "iteration": iteration,
            "run_id": run_id,
            "task_run_id": journal.task_run_id,
            "checkpointed": True,
        },
    )


def _remaining_work(journal: TaskJournal) -> list[str]:
    remaining = [step.goal for step in journal.steps if step.status != "completed"]
    verified_ids: set[str] = set()
    if journal.verification:
        verified_ids = {
            str(item.get("id") or "")
            for item in journal.verification.criterion_results
            if bool(item.get("satisfied"))
        }
    remaining.extend(
        criterion.description
        for criterion in journal.contract.acceptance_criteria
        if criterion.required and criterion.id not in verified_ids
    )
    remaining.extend(
        f"解决故障链 {episode.id}：{episode.category} {episode.target_object}".strip()
        for episode in journal.unresolved_failure_episodes()
    )
    if journal.verification and not journal.verification.satisfied:
        remaining.extend(journal.verification.missing)
    return list(dict.fromkeys(item for item in remaining if item))[:20]


def _prefers_chinese(journal: TaskJournal) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", journal.contract.objective or ""))


def _build_initial_progress_note(journal: TaskJournal) -> str:
    chinese = _prefers_chinese(journal)
    if chinese:
        if journal.metrics.resumptions > 0 or journal.evidence:
            return "我会沿用已经拿到的证据，从尚未确认的部分继续检查，不会重头再来。"
        if not journal.contract.complex:
            return "我先确认实际范围和可用信息，再用最直接的查询取得证据，然后给你简洁结论。"
        criteria = _planning_criteria_summary(journal, chinese=True)
        if criteria:
            return (
                f"我先把任务拆开：重点核对{criteria}。执行时会先确认真实结构和数据范围，再按依赖顺序逐项取证；"
                "每完成一部分就复核口径和结果，遇到失败会根据返回信息调整，最后只给出有直接证据支撑的结论。"
            )
        return (
            "我先梳理任务目标、约束和验收条件，再确认真实结构与可用数据。接下来会按依赖顺序逐项取证，"
            "每完成一部分就复核口径和结果；遇到失败会依据返回信息调整方法，最后只提交有直接证据支撑的结论。"
        )
    if journal.metrics.resumptions > 0 or journal.evidence:
        return "I’ll continue from the evidence already collected and focus on the remaining gaps."
    if not journal.contract.complex:
        return "I’ll confirm the actual scope, run the most direct check, and give you a concise evidence-based answer."
    criteria = _planning_criteria_summary(journal, chinese=False)
    focus = f" The main checks are {criteria}." if criteria else ""
    return (
        "I’ll first map the objective, constraints, and acceptance criteria, then confirm the real "
        f"structure and available data.{focus} I’ll gather evidence in dependency order, validate "
        "each result, adapt to failures, and only present conclusions supported by direct evidence."
    )


def _planning_criteria_summary(journal: TaskJournal, *, chinese: bool) -> str:
    items: list[str] = []
    candidates = [
        *journal.contract.output_requirements[:1],
        *(criterion.description for criterion in journal.contract.acceptance_criteria[:2]),
    ]
    for candidate in candidates:
        text = " ".join(str(candidate or "").split()).strip("。.;；")
        if not text:
            continue
        if "```" in text or re.search(
            r"\b(?:select|insert|update|delete|alter|create|drop)\b", text, re.I
        ):
            continue
        limit = 42 if chinese else 80
        if len(text) > limit:
            text = text[:limit].rstrip("，,、 ") + "…"
        if text not in items:
            items.append(text)
        if len(items) >= 2:
            break
    separator = "、" if chinese else "; "
    return separator.join(items)


def _clean_progress_narration(value: Any, *, chinese: bool) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) < 8 or len(text) > 260:
        return ""
    lowered = text.lower()
    if any(token in lowered for token in ("select ", "insert ", "update ", "delete ")):
        return ""
    if any(token in text for token in ("{", "}", "```", "<tool_call", "<function=")):
        return ""
    contains_chinese = bool(re.search(r"[\u3400-\u9fff]", text))
    if chinese != contains_chinese:
        return ""
    return text


def _clean_progress_objective(value: Any, *, chinese: bool) -> str:
    text = " ".join(str(value or "").split()).strip(" `\"'。，,.!！?？:：;")
    if not text or len(text) > 160:
        return ""
    if any(token in text.lower() for token in ("select ", "insert ", "update ", "delete ")):
        return ""
    if any(token in text for token in ("{", "}", "```")):
        return ""
    contains_chinese = bool(re.search(r"[\u3400-\u9fff]", text))
    if chinese != contains_chinese:
        return ""
    return text


def _tool_call_details(
    tool_calls: list[dict[str, Any]],
    *,
    chinese: bool,
) -> tuple[list[str], list[str], list[str]]:
    names: list[str] = []
    sql_statements: list[str] = []
    objectives: list[str] = []
    for tool_call in tool_calls:
        normalized = _normalize_tool_call(tool_call)
        name = str(normalized.get("name") or "").strip().lower()
        if name:
            names.append(name)
        arguments = normalized.get("arguments")
        if isinstance(arguments, dict):
            runtime = arguments.get("_runtime")
            runtime = runtime if isinstance(runtime, dict) else {}
            objective = _clean_progress_objective(
                arguments.get("intent") or runtime.get("goal"),
                chinese=chinese,
            )
            if objective and objective not in objectives:
                objectives.append(objective)
        if name == "execute_sql" and isinstance(arguments, dict):
            sql = str(arguments.get("sql") or "").strip()
            if sql:
                sql_statements.append(sql)
    return names, sql_statements, objectives


def _build_plan_progress_note(
    journal: TaskJournal,
    tool_calls: list[dict[str, Any]],
    *,
    iteration: int,
    model_narration: Any = None,
) -> str:
    chinese = _prefers_chinese(journal)
    names, sql_statements, objectives = _tool_call_details(tool_calls, chinese=chinese)
    sql = "\n".join(sql_statements).lower()
    unresolved = journal.unresolved_failure_episodes()
    narration = _clean_progress_narration(model_narration, chinese=chinese)

    if narration:
        return narration

    if unresolved:
        latest_failure = unresolved[-1]
        category = latest_failure.category
        if category in {"argument_error", "invalid_arguments", "validation_error"}:
            return (
                "刚才的工具参数格式不完整，调用没有真正执行。我会重新组织参数后再发起同一项检查。"
                if chinese
                else "The tool arguments were malformed, so the action never ran. I’ll rebuild the arguments and retry the same check."
            )
        if category == "result_shape_error":
            return (
                "刚才返回的数据结构不符合当前计算要求。我会拆分或重新聚合这一步，再继续验证。"
                if chinese
                else "The returned data shape did not match the calculation. I’ll split or re-aggregate this step before continuing."
            )
        if category in {"unknown_table", "schema_error", "unknown_column"}:
            return (
                "刚才的查询和实际表结构不一致。我先核对真实字段，再按确认后的结构调整查询。"
                if chinese
                else "The last query did not match the actual schema. I’ll confirm the real fields before adjusting it."
            )
        return (
            "刚才这一步没有按预期完成。我会根据返回的错误换一种做法，不会原样重复。"
            if chinese
            else "That step did not complete as expected. I’ll adapt to the error instead of repeating it unchanged."
        )

    if objectives:
        if len(objectives) == 1:
            return f"接下来：{objectives[0]}。" if chinese else f"Next: {objectives[0]}."
        return (
            f"接下来会并行完成 {len(objectives)} 项检查，包括：{objectives[0]}。"
            if chinese
            else f"Next I’ll run {len(objectives)} checks in parallel, including: {objectives[0]}."
        )

    if sql_statements:
        if re.search(
            r"\b(show\s+tables|show\s+(?:full\s+)?columns|describe|desc\s+|information_schema)\b",
            sql,
        ):
            return (
                "我先查看实际有哪些表和字段，避免基于猜测直接下结论。"
                if chinese
                else "I’m checking the actual tables and columns first so the analysis is grounded in the schema."
            )
        if re.search(r"\b(left\s+join|right\s+join|not\s+exists)\b", sql):
            return (
                "我正在核对不同数据对象之间的关联是否完整。"
                if chinese
                else "I’m checking whether relationships among the relevant data objects are complete."
            )
        if re.search(r"\b(group\s+by|date|day|month|status|created_at)\b", sql):
            return (
                "我正在补充分组、时间或状态维度的证据。"
                if chinese
                else "I’m gathering evidence across grouping, time, or status dimensions."
            )
        if re.search(r"\b(sum|avg|min|max|count)\s*\(", sql):
            return (
                "我正在汇总关键指标，为后续判断建立可核对的基准。"
                if chinese
                else "I’m aggregating the key metrics to establish a verifiable baseline."
            )
        return (
            "我正在补查下一组数据证据，用来验证前面的判断。"
            if chinese
            else "I’m collecting the next set of data evidence to validate the findings so far."
        )

    if "datasource_switch" in names:
        return (
            "我先切换到任务对应的数据源。"
            if chinese
            else "I’m switching to the datasource for this task."
        )
    if len(names) > 1:
        return (
            "接下来会并行完成几项互不影响的只读检查。"
            if chinese
            else "Next I’ll run several independent read-only checks in parallel."
        )
    if names:
        return (
            "我正在执行下一项检查，并会根据结果决定后续动作。"
            if chinese
            else "I’m running the next check and will adapt the following step to its result."
        )
    return "" if iteration > 0 else _build_initial_progress_note(journal)


def _build_observation_progress_note(
    journal: TaskJournal,
    observations: list[Observation],
    decision: dict[str, Any],
) -> str:
    chinese = _prefers_chinese(journal)
    failures = [item for item in observations if not item.success]
    if failures:
        error_classes = {item.error_class for item in failures}
        if error_classes.intersection({"argument_error", "invalid_arguments", "validation_error"}):
            return (
                "这次调用的参数格式不完整，所以工具没有真正执行；我会重新整理参数，再继续原来的检查。"
                if chinese
                else "The tool arguments were malformed, so the action never ran. I’ll rebuild them and continue the original check."
            )
        if "result_shape_error" in error_classes:
            return (
                "这次返回的数据结构不符合当前计算要求；我会拆分或重新聚合这一步。"
                if chinese
                else "The returned data shape did not match the calculation; I’ll split or re-aggregate this step."
            )
        if error_classes.intersection({"schema_error", "unknown_table", "unknown_column"}):
            return (
                "这次查询碰到了不存在的表或字段。我先回到结构检查，确认后再继续，不会盲目试列名。"
                if chinese
                else "This query hit a missing table or column. I’ll verify the schema before trying again."
            )
        if error_classes.intersection({"timeout_error", "rate_limit_error", "connection_error"}):
            return (
                "这一步遇到了临时连接问题。我会稍后重试，并保留前面已经完成的结果。"
                if chinese
                else "This step hit a temporary connection issue. I’ll retry without losing the completed work."
            )
        return (
            "这一步没有成功。我已经记下错误，接下来会调整查询或检查路径，而不是重复同一个动作。"
            if chinese
            else "This step failed. I’ve recorded the error and will change the query or inspection path instead of repeating it."
        )

    if any(item.is_discovery for item in observations):
        return (
            "这一步需要的基础信息已经确认，后续会只使用已经验证过的内容。"
            if chinese
            else "The foundational information for this step is confirmed; the next actions will use only verified details."
        )
    if str(decision.get("decision") or "") == ProgressDecision.AWAIT_CONFIRMATION:
        return (
            "这里涉及需要确认的操作，我先等你决定后再继续。"
            if chinese
            else "This step requires confirmation, so I’ll wait for your decision before continuing."
        )
    return ""


def _first_failed_criterion_description(journal: TaskJournal) -> str:
    verification = journal.verification
    if verification is None:
        return ""
    criteria_by_id = {item.id: item.description for item in journal.contract.acceptance_criteria}
    for result in verification.criterion_results:
        if bool(result.get("satisfied")):
            continue
        criterion_id = str(result.get("id") or "").strip()
        description = " ".join(criteria_by_id.get(criterion_id, "").split())
        if description:
            return description[:100]
    return ""


def _build_verification_progress_note(
    journal: TaskJournal,
    *,
    satisfied: bool | None,
) -> str:
    chinese = _prefers_chinese(journal)
    if satisfied is None:
        return (
            "主要证据已经收集完成。我先逐项复核事实、约束和结论，确认没有遗漏再给结果。"
            if chinese
            else "The main evidence is collected. I’m checking the facts, constraints, and conclusions before reporting."
        )
    if satisfied:
        return (
            "复核已经通过，关键结论都有对应证据。我正在整理最终报告。"
            if chinese
            else "The review passed and the key claims are supported. I’m preparing the final report."
        )
    verification = journal.verification
    needs_evidence = bool(verification and _verification_requires_new_evidence(verification))
    if needs_evidence:
        return (
            "复核发现还有部分事实或结论缺少直接证据。我会继续补查，暂时不把草稿当成结论。"
            if chinese
            else "The review found facts or conclusions without direct evidence. I’ll gather more evidence before concluding."
        )
    verification_reason = str(verification.reason if verification else "").lower()
    evidence_alignment_tokens = (
        "numeric",
        "number",
        "calculation",
        "recomput",
        "unsupported",
        "direct evidence",
        "evidence",
        "数字",
        "计算",
        "复算",
        "推导",
        "证据",
        "支撑",
        "依据",
    )
    if any(token in verification_reason for token in evidence_alignment_tokens):
        return (
            "复核时发现有些数字、推导或判断还不能从现有查询结果直接得到。我会修正计算或删去无法支持的表述，不做无关查询。"
            if chinese
            else "The review found numbers, inferences, or judgments that cannot be derived from the current results. I’ll correct or remove them without running unrelated checks."
        )
    failed_criterion = _first_failed_criterion_description(journal)
    if failed_criterion:
        return (
            f"复核发现“{failed_criterion}”的结论和现有证据还没对齐。我会修正推导或删除无法支持的表述，不做无关查询。"
            if chinese
            else f"The conclusion for “{failed_criterion}” does not align with the current evidence. I’ll correct or remove unsupported inferences without running unrelated checks."
        )
    if verification and verification.contradictions:
        return (
            "复核发现部分结论对现有证据的解读不一致。我会修正推导或删除无法支持的表述，不做无关查询。"
            if chinese
            else "The review found that some conclusions do not align with the existing evidence. I’ll correct or remove unsupported inferences without running unrelated checks."
        )
    return (
        "证据已经够了，但这版表达还不完整。我会重新整理成一份可以独立阅读的完整报告。"
        if chinese
        else "The evidence is sufficient, but this draft is incomplete. I’ll rewrite it as a self-contained report."
    )


def _build_checkpoint_summary(journal: TaskJournal, decision: dict[str, Any]) -> str:
    chinese = _prefers_chinese(journal)
    reason = str(
        decision.get("reason")
        or ("当前执行无法继续。" if chinese else "Execution cannot continue right now.")
    )
    remaining = _remaining_work(journal)
    if chinese:
        suffix = (
            "\n\n还需要处理：\n" + "\n".join(f"- {item}" for item in remaining[:8])
            if remaining
            else ""
        )
        return (
            f"我在这里遇到了一个暂时无法自行解决的问题：{reason}\n\n"
            f"前面拿到的 {len(journal.evidence)} 条查询证据都已保留。"
            "等条件具备后，可以直接从这里继续，不需要重头开始。"
            f"{suffix}"
        )
    suffix = (
        "\n\nStill to resolve:\n" + "\n".join(f"- {item}" for item in remaining[:8])
        if remaining
        else ""
    )
    return (
        f"I ran into an issue I cannot resolve safely on my own yet: {reason}\n\n"
        f"The {len(journal.evidence)} query results collected so far are preserved, so a later run can continue here without starting over."
        f"{suffix}"
    )


def _build_terminal_summary(journal: TaskJournal, status: str) -> str:
    """Guarantee a readable terminal artifact for every non-text stream outcome."""
    chinese = _prefers_chinese(journal)
    remaining = _remaining_work(journal)
    if chinese:
        heading = {
            "cancelled": "本次执行已取消，当前进度已经保存。",
            "stalled": "本次执行因连续没有取得新进展而停止，当前进度已经保存。",
            "error": "本次执行遇到运行错误，未能形成可靠的最终结论。",
        }.get(status, "本次执行尚未完成，当前进度已经保存。")
        suffix = (
            "\n\n仍需处理：\n" + "\n".join(f"- {item}" for item in remaining[:8])
            if remaining
            else ""
        )
        return f"{heading}\n\n已保留 {len(journal.evidence)} 条工具证据，可从当前状态继续。{suffix}"
    heading = {
        "cancelled": "This run was cancelled and its progress was saved.",
        "stalled": "This run stopped after making no further progress, and its progress was saved.",
        "error": "This run encountered a runtime error and did not produce a reliable final conclusion.",
    }.get(status, "This run is incomplete and its progress was saved.")
    suffix = (
        "\n\nStill to resolve:\n" + "\n".join(f"- {item}" for item in remaining[:8])
        if remaining
        else ""
    )
    return f"{heading}\n\n{len(journal.evidence)} tool evidence records were retained.{suffix}"


def _build_verification_feedback(verification: VerificationResult) -> str:
    gaps = verification.missing or [
        verification.reason or "Acceptance criteria are not yet covered."
    ]
    contradictions = verification.contradictions
    action_instruction = (
        "This is an explicit action-evidence gap. Dispatch the exact fenced payload requested by the user once "
        "through the relevant tool and retain that tool result. Do not replace it with a smaller probe, a rewritten "
        "payload, or separate tests of its individual parts. After the requested dispatch returns, continue the "
        "remaining acceptance criteria; an expected failure from that exact action is evidence, not a reason to "
        "repeat it.\n"
        if verification.evaluator == "deterministic_action_evidence"
        else ""
    )
    return (
        "Completion verification failed. Resolve the specific defect before submitting another candidate.\n"
        f"Repair type: {verification.repair_type}. Follow it exactly: rewrite means use existing evidence; "
        "new_evidence means collect only the missing source facts; blocked means stop and report the external "
        "condition.\n"
        f"{action_instruction}"
        "First distinguish a missing fact from a wording, calculation, or inclusion-rule defect. If a missing "
        "item requires facts not already present in the evidence journal, call tools and collect evidence that "
        "directly addresses it. If the evidence is already sufficient, do not run an unrelated query merely to "
        "show activity: remove the unsupported derived claim, correct the calculation, or state the evidenced "
        "facts separately. Tool evidence must actually measure or inspect a claim rather than merely restating "
        "the desired conclusion. If a claim cannot be established, remove it or label it uncertain instead of "
        "inventing a value. The next candidate must restate "
        "the complete self-contained answer; failed drafts are hidden from the user, so never refer to an original "
        "or previous report.\n"
        f"Missing: {json.dumps(gaps, ensure_ascii=False)}\n"
        f"Contradictions: {json.dumps(contradictions, ensure_ascii=False)}"
    )


def _build_verification_checkpoint_summary(
    verification: VerificationResult,
    journal: TaskJournal,
) -> str:
    chinese = _prefers_chinese(journal)
    missing = verification.missing or [
        verification.reason
        or ("验收条件尚未完全满足" if chinese else "Some acceptance criteria are still unresolved.")
    ]
    if chinese:
        return (
            "我重新核对了一遍，发现这版回答还有几处证据不够扎实，所以先不把它作为最终结论。\n\n"
            "还需要确认：\n"
            + "\n".join(f"- {item}" for item in missing[:10])
            + "\n\n前面已经完成的查询和结果都保留着。后续继续时，我会直接从这些缺口补查，不需要重头开始。"
        )
    return (
        "I reviewed the draft again and found a few conclusions that are not supported well enough yet, so I’m holding back the final answer.\n\n"
        "Still to confirm:\n"
        + "\n".join(f"- {item}" for item in missing[:10])
        + "\n\nThe completed queries and results are preserved. A later run can continue from these gaps without starting over."
    )


def _candidate_self_containment_precheck(
    journal: TaskJournal,
    candidate_text: str,
) -> VerificationResult | None:
    if not journal.contract.complex or journal.metrics.verification_attempts <= 1:
        return None
    delta_reference = re.search(
        r"(?:\ball other (?:findings|sections|details).{0,40}\bremain\b|"
        r"\b(?:original|previous|prior) (?:audit|report|answer|draft)\b|"
        r"(?:其余|其他).{0,16}(?:不变|保持|沿用)|(?:上一版|前一版|原报告|原审计))",
        candidate_text,
        re.I | re.S,
    )
    if delta_reference is None:
        delta_reference = re.search(
            r"(?:(?:您的|你的).{0,18}(?:复核|审核|审查|审阅|结论|报告|反馈)"
            r".{0,20}(?:准确|正确|全面|指出|提到)|"
            r"\b(?:your|the)\s+(?:review|feedback|correction|audit)\b"
            r".{0,30}\b(?:correct|accurate|right|noted|pointed out)\b)",
            candidate_text,
            re.I | re.S,
        )
    if delta_reference is None:
        return None
    return VerificationResult(
        satisfied=False,
        reason="候选答案引用了未展示的失败稿，不是自包含的最终答案。",
        missing=[
            "Restate a complete self-contained final answer and cover every acceptance criterion."
        ],
        repair_type="rewrite",
        evaluator="deterministic_candidate",
    )


def _extract_completion_verification_policies(system_prompt: str | None) -> list[str]:
    """Load optional verifier policy extensions without coupling the loop to a domain.

    Skills may contribute one or more ``completion_verification_policy`` blocks.
    The reasoning engine treats their contents as opaque verifier instructions;
    it does not inspect domain terms or branch on a particular skill name.
    """
    if not system_prompt:
        return []
    matches = re.findall(
        r"<completion_verification_policy>\s*(.*?)\s*</completion_verification_policy>",
        system_prompt,
        re.I | re.S,
    )
    policies: list[str] = []
    seen: set[str] = set()
    for match in matches:
        normalized = "\n".join(line.rstrip() for line in dedent(match).strip().splitlines()).strip()
        if not normalized or normalized in seen:
            continue
        policies.append(normalized[:4000])
        seen.add(normalized)
        if len(policies) >= 8:
            break
    return policies


def _verification_requires_new_evidence(verification: VerificationResult) -> bool:
    """Follow the verifier's structured semantic repair decision."""
    return verification.repair_type == "new_evidence"


def _build_private_revision_context(candidate_text: str, feedback: str) -> str:
    return (
        "INTERNAL REVISION CONTEXT — this is not a user message and neither block below was shown to the user.\n"
        "Rewrite the rejected draft into a complete, self-contained replacement. Do not thank, agree with, "
        "or reply to the verifier. Do not describe edits or refer to a previous draft. Do not call tools unless "
        "the verification feedback explicitly says new source evidence is missing. Apply every correction, then "
        "output the full final answer covering the original task.\n\n"
        "<rejected_draft>\n"
        f"{candidate_text}\n"
        "</rejected_draft>\n\n"
        "<verification_feedback>\n"
        f"{feedback}\n"
        "</verification_feedback>"
    )


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
        extracted.append(
            {
                "id": f"xmltc_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": json.dumps(params, ensure_ascii=False),
                },
            }
        )
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


def _sanitize_tool_call_for_history(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Keep provider protocol history valid while raw evidence stays in events."""
    normalized = _normalize_tool_call(tool_call)
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    arguments = normalized["arguments"] if normalized["ok"] else {}
    return {
        "id": str(tool_call.get("id") or normalized["tool_call_id"]),
        "type": str(tool_call.get("type") or "function"),
        "function": {
            "name": str(function.get("name") or normalized["name"] or "unknown_tool"),
            "arguments": json.dumps(arguments, ensure_ascii=False, default=str),
        },
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
        f"阶段性结论：当前任务尚未完成，已执行 {total} 次工具调用，"
        f"其中记录到 {failures} 次失败。已保存当前进度；"
        "你可以直接要求继续，我会从检查点恢复并处理剩余事项。"
    )


def _reflector_step(
    execution_results: list[dict[str, Any]],
    reflection_count: int,
    max_reflections: int,
) -> dict[str, Any]:
    for item in execution_results:
        result_payload = item.get("result") if isinstance(item.get("result"), dict) else {}
        result_data = (
            result_payload.get("data") if isinstance(result_payload.get("data"), dict) else {}
        )
        if bool(result_data.get("requires_confirmation")):
            return {
                "action": "await_confirmation",
                "reason": "pending_confirmation",
                "reason_code": "pending_confirmation",
                "strategy_reason_code": _extract_strategy_reason_code(execution_results),
            }

    failures = [
        result for result in execution_results if not (result.get("result") or {}).get("success")
    ]
    strategy_reason_code = _extract_strategy_reason_code(execution_results)
    if not failures:
        return {
            "action": "continue",
            "reason": "all_tools_succeeded",
            "reason_code": "all_tools_succeeded",
            "strategy_reason_code": strategy_reason_code,
        }

    schema_failures = [
        failure for failure in failures if str(failure.get("error_class") or "") == "schema_error"
    ]
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
    schema_failures = [
        failure for failure in failures if str(failure.get("error_class") or "") == "schema_error"
    ]
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
        f"- {planning_summary}" + ("\n" if planning_summary else "") + f"- {summary}"
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
        planning_meta = (
            item.get("planning_meta") if isinstance(item.get("planning_meta"), dict) else {}
        )
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
