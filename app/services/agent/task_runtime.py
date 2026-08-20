"""Structured task state for long-running agent executions.

The reasoning loop deliberately keeps this module independent from transport and
database concerns.  A :class:`TaskJournal` can therefore be serialized into an
existing chat event, restored after a restart, and exercised by trajectory evals
without booting the API service.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.services.agent.task_contract import TaskContract, latest_user_text

TASK_STATE_VERSION = 2

_EVIDENCE_EXCERPT_MAX_CHARS = 10_000
_VERIFIER_EVIDENCE_BUDGET_CHARS = 90_000
_SENSITIVE_ARGUMENT_KEY_RE = re.compile(
    r"(?:authorization|cookie|credential|password|passwd|secret|token|api[_-]?key)", re.I
)


class ProgressDecision(StrEnum):
    PROGRESS = "progress"
    RECOVERABLE_FAILURE = "recoverable_failure"
    TRANSIENT_FAILURE = "transient_failure"
    AWAIT_CONFIRMATION = "await_confirmation"
    BLOCKED = "blocked"
    STALLED = "stalled"
    CANDIDATE_COMPLETE = "candidate_complete"


@dataclass
class Observation:
    evidence_ref: str
    tool_name: str
    success: bool
    category: str
    code: str
    errno: int | None
    message: str
    retry_hint: str
    error_class: str
    target_object: str
    normalized_signature: str
    strategy_signature: str
    is_discovery: bool
    requires_confirmation: bool
    planning_goal: str = ""
    planning_success_criteria: str = ""
    request_summary: str = ""
    evidence_excerpt: str = ""
    evidence_chars: int = 0
    evidence_truncated: bool = False

    @classmethod
    def from_execution(cls, item: dict[str, Any]) -> Observation:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        success = bool(result.get("success"))
        error = result.get("error")
        error_payload = error if isinstance(error, dict) else {}
        message = str(
            error_payload.get("db_message")
            or error_payload.get("message")
            or (error if isinstance(error, str) else "")
        ).strip()
        if success and not message:
            message = _summarize_success_data(result.get("data"))
        category = str(error_payload.get("category") or "").strip().lower()
        code = str(error_payload.get("code") or "").strip().lower()
        errno_raw = error_payload.get("db_errno", error_payload.get("errno"))
        try:
            errno = int(errno_raw) if errno_raw is not None else None
        except (TypeError, ValueError):
            errno = None

        arguments = _parse_arguments(item.get("arguments"))
        tool_name = str(item.get("name") or "unknown_tool")
        target_object = _extract_target_object(tool_name, arguments, message)
        error_class = _normalize_error_class(
            explicit=str(item.get("error_class") or ""),
            category=category,
            code=code,
            message=message,
            errno=errno,
        )
        normalized_error = _normalize_error(message or code or error_class)
        signature_source = "|".join(
            [tool_name.lower(), error_class, category or code, target_object, normalized_error]
        )
        normalized_signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:20]
        canonical_args = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        strategy_signature = hashlib.sha256(
            f"{tool_name.lower()}|{canonical_args}".encode()
        ).hexdigest()[:20]
        raw_data = result.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        evidence_excerpt, evidence_chars, evidence_truncated = _build_evidence_excerpt(raw_data)
        planning = item.get("planning_meta") if isinstance(item.get("planning_meta"), dict) else {}
        return cls(
            evidence_ref=str(item.get("tool_call_id") or f"evidence-{uuid.uuid4().hex[:12]}"),
            tool_name=tool_name,
            success=success,
            category=category or ("success" if success else error_class),
            code=code,
            errno=errno,
            message=message,
            retry_hint=str(error_payload.get("retry_hint") or "").strip(),
            error_class=error_class,
            target_object=target_object,
            normalized_signature=normalized_signature,
            strategy_signature=strategy_signature,
            is_discovery=_is_discovery_call(tool_name, arguments),
            requires_confirmation=bool(data.get("requires_confirmation")),
            planning_goal=str(planning.get("goal") or "").strip(),
            planning_success_criteria=str(planning.get("success_criteria") or "").strip(),
            request_summary=_summarize_tool_request(tool_name, arguments),
            evidence_excerpt=evidence_excerpt,
            evidence_chars=evidence_chars,
            evidence_truncated=evidence_truncated,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceRecord:
    ref: str
    tool_name: str
    target_object: str
    outcome: str
    summary: str
    signature: str
    iteration: int
    request_summary: str = ""
    evidence_excerpt: str = ""
    evidence_chars: int = 0
    evidence_truncated: bool = False


@dataclass
class TaskStep:
    id: str
    goal: str
    success_criteria: str = ""
    status: str = "running"
    evidence_refs: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)


@dataclass
class FailureEpisode:
    id: str
    signature: str
    tool_name: str
    category: str
    target_object: str
    normalized_error: str
    first_seen_at: str
    last_seen_at: str
    attempts: int = 0
    attempted_strategies: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "open"
    resolution_evidence_ref: str | None = None
    semantic_assessment: str | None = None

    def record_failure(self, observation: Observation) -> None:
        self.attempts += 1
        self.last_seen_at = _utc_now()
        if observation.strategy_signature not in self.attempted_strategies:
            self.attempted_strategies.append(observation.strategy_signature)
        if observation.evidence_ref not in self.evidence_refs:
            self.evidence_refs.append(observation.evidence_ref)
        self.status = "open"


@dataclass
class TaskMetrics:
    iterations: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    recovered_failures: int = 0
    no_progress_rounds: int = 0
    verification_attempts: int = 0
    verification_no_progress_rounds: int = 0
    last_verification_evidence_count: int = 0
    resumptions: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float = 0.0
    time_to_first_evidence_ms: float | None = None


@dataclass
class VerificationResult:
    satisfied: bool
    reason: str
    missing: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    criterion_results: list[dict[str, Any]] = field(default_factory=list)
    repair_type: str = "none"
    failure_assessments: list[dict[str, Any]] = field(default_factory=list)
    evaluator: str = "deterministic"
    malformed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskJournal:
    task_run_id: str
    contract: TaskContract
    status: str = "running"
    version: int = TASK_STATE_VERSION
    steps: list[TaskStep] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    failure_episodes: list[FailureEpisode] = field(default_factory=list)
    active_failure_episode_id: str | None = None
    verification: VerificationResult | None = None
    metrics: TaskMetrics = field(default_factory=TaskMetrics)
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())
    seen_success_signatures: list[str] = field(default_factory=list)
    user_corrections: list[str] = field(default_factory=list)
    expected_action_evidence_refs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, contract: TaskContract) -> TaskJournal:
        return cls(task_run_id=str(uuid.uuid4()), contract=contract)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskJournal:
        payload = migrate_task_state(payload)
        contract_payload = (
            payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
        )
        journal = cls(
            task_run_id=str(payload.get("task_run_id") or uuid.uuid4()),
            contract=TaskContract.from_dict(contract_payload),
            status=str(payload.get("status") or "running"),
            version=int(payload.get("version") or TASK_STATE_VERSION),
            created_at=str(payload.get("created_at") or _utc_now()),
            updated_at=str(payload.get("updated_at") or _utc_now()),
            active_failure_episode_id=(
                str(payload.get("active_failure_episode_id"))
                if payload.get("active_failure_episode_id")
                else None
            ),
            seen_success_signatures=[
                str(item) for item in payload.get("seen_success_signatures") or []
            ],
            user_corrections=[str(item) for item in payload.get("user_corrections") or []],
            expected_action_evidence_refs={
                str(key): str(value)
                for key, value in (payload.get("expected_action_evidence_refs") or {}).items()
            },
        )
        journal.steps = [
            TaskStep(
                id=str(item.get("id") or f"step-{index}"),
                goal=str(item.get("goal") or ""),
                success_criteria=str(item.get("success_criteria") or ""),
                status=str(item.get("status") or "running"),
                evidence_refs=[str(ref) for ref in item.get("evidence_refs") or []],
                unresolved_questions=[str(q) for q in item.get("unresolved_questions") or []],
            )
            for index, item in enumerate(payload.get("steps") or [], start=1)
            if isinstance(item, dict)
        ]
        journal.evidence = [
            EvidenceRecord(
                ref=str(item.get("ref") or ""),
                tool_name=str(item.get("tool_name") or ""),
                target_object=str(item.get("target_object") or ""),
                outcome=str(item.get("outcome") or ""),
                summary=str(item.get("summary") or ""),
                signature=str(item.get("signature") or ""),
                iteration=int(item.get("iteration") or 0),
                request_summary=str(item.get("request_summary") or ""),
                evidence_excerpt=str(item.get("evidence_excerpt") or ""),
                evidence_chars=int(item.get("evidence_chars") or 0),
                evidence_truncated=bool(item.get("evidence_truncated")),
            )
            for item in payload.get("evidence") or []
            if isinstance(item, dict)
        ]
        journal.failure_episodes = [
            FailureEpisode(
                id=str(item.get("id") or f"failure-{uuid.uuid4().hex[:12]}"),
                signature=str(item.get("signature") or ""),
                tool_name=str(item.get("tool_name") or "unknown_tool"),
                category=str(item.get("category") or "execution_error"),
                target_object=str(item.get("target_object") or ""),
                normalized_error=str(item.get("normalized_error") or ""),
                first_seen_at=str(item.get("first_seen_at") or _utc_now()),
                last_seen_at=str(item.get("last_seen_at") or _utc_now()),
                attempts=int(item.get("attempts") or 0),
                attempted_strategies=[str(s) for s in item.get("attempted_strategies") or []],
                evidence_refs=[str(ref) for ref in item.get("evidence_refs") or []],
                status=str(item.get("status") or "open"),
                resolution_evidence_ref=(
                    str(item.get("resolution_evidence_ref"))
                    if item.get("resolution_evidence_ref")
                    else None
                ),
                semantic_assessment=(
                    str(item.get("semantic_assessment"))
                    if item.get("semantic_assessment")
                    else None
                ),
            )
            for item in payload.get("failure_episodes") or []
            if isinstance(item, dict)
        ]
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        journal.metrics = TaskMetrics(
            iterations=int(metrics.get("iterations") or 0),
            tool_calls=int(metrics.get("tool_calls") or 0),
            tool_failures=int(metrics.get("tool_failures") or 0),
            recovered_failures=int(metrics.get("recovered_failures") or 0),
            no_progress_rounds=int(metrics.get("no_progress_rounds") or 0),
            verification_attempts=int(metrics.get("verification_attempts") or 0),
            verification_no_progress_rounds=int(
                metrics.get("verification_no_progress_rounds") or 0
            ),
            last_verification_evidence_count=int(
                metrics.get("last_verification_evidence_count") or 0
            ),
            resumptions=int(metrics.get("resumptions") or 0) + 1,
            llm_calls=int(metrics.get("llm_calls") or 0),
            input_tokens=int(metrics.get("input_tokens") or 0),
            output_tokens=int(metrics.get("output_tokens") or 0),
            elapsed_ms=float(metrics.get("elapsed_ms") or 0.0),
            time_to_first_evidence_ms=(
                float(metrics["time_to_first_evidence_ms"])
                if metrics.get("time_to_first_evidence_ms") is not None
                else None
            ),
        )
        verification = payload.get("verification")
        if isinstance(verification, dict):
            journal.verification = VerificationResult(
                satisfied=bool(verification.get("satisfied")),
                reason=str(verification.get("reason") or ""),
                missing=[str(item) for item in verification.get("missing") or []],
                contradictions=[str(item) for item in verification.get("contradictions") or []],
                criterion_results=[
                    dict(item)
                    for item in verification.get("criterion_results") or []
                    if isinstance(item, dict)
                ],
                repair_type=str(verification.get("repair_type") or "none"),
                failure_assessments=[
                    dict(item)
                    for item in verification.get("failure_assessments") or []
                    if isinstance(item, dict)
                ],
                evaluator=str(verification.get("evaluator") or "deterministic"),
                malformed=bool(verification.get("malformed")),
            )
        return journal

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_run_id": self.task_run_id,
            "status": self.status,
            "contract": self.contract.to_dict(),
            "steps": [asdict(item) for item in self.steps],
            "evidence": [asdict(item) for item in self.evidence],
            "failure_episodes": [asdict(item) for item in self.failure_episodes],
            "active_failure_episode_id": self.active_failure_episode_id,
            "verification": self.verification.to_dict() if self.verification else None,
            "metrics": asdict(self.metrics),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "seen_success_signatures": list(self.seen_success_signatures),
            "user_corrections": list(self.user_corrections),
            "expected_action_evidence_refs": dict(self.expected_action_evidence_refs),
        }

    def context_block(self) -> str:
        unresolved = self.unresolved_failure_episodes()
        state = {
            "task_run_id": self.task_run_id,
            "objective": self.contract.objective,
            "constraints": self.contract.constraints,
            "user_corrections": self.user_corrections,
            "acceptance_criteria": [asdict(item) for item in self.contract.acceptance_criteria],
            "completed_steps": [asdict(step) for step in self.steps if step.status == "completed"],
            "unresolved_steps": [asdict(step) for step in self.steps if step.status != "completed"],
            "failure_history": [
                {
                    "id": episode.id,
                    "signature": episode.signature,
                    "category": episode.category,
                    "target_object": episode.target_object,
                    "attempts": episode.attempts,
                    "attempted_strategies": episode.attempted_strategies,
                    "status": episode.status,
                }
                for episode in self.failure_episodes
            ],
            "unresolved_failure_ids": [episode.id for episode in unresolved],
            "last_verification": (
                self.verification.to_dict() if self.verification is not None else None
            ),
            "recent_evidence": [asdict(item) for item in self.evidence[-12:]],
        }
        return (
            "[Structured Task Journal — authoritative resume state]\n"
            "Continue the task from this state. Do not repeat a failed strategy unless new evidence justifies it.\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
        )

    def record_iteration(self, iteration: int) -> None:
        self.metrics.iterations = max(self.metrics.iterations, iteration)
        self.updated_at = _utc_now()

    def apply_user_correction(self, messages: list[dict[str, Any]]) -> None:
        correction = latest_user_text(messages).strip()
        if not correction or re.fullmatch(
            r"(?:请)?(?:继续执行|继续|接着|续跑|恢复|continue|resume)[，,。.！!\s]*",
            correction,
            re.I,
        ):
            return
        if correction not in self.user_corrections:
            self.user_corrections.append(correction)
            self.updated_at = _utc_now()

    def evaluate_observations(
        self,
        observations: list[Observation],
        *,
        iteration: int,
        per_episode_retry_budget: int,
        transient_retry_budget: int,
        max_no_progress_rounds: int,
    ) -> dict[str, Any]:
        self.record_iteration(iteration)
        self.metrics.tool_calls += len(observations)
        for observation in observations:
            self._record_evidence(observation, iteration)
            self._record_step(observation)

        confirmation = next((item for item in observations if item.requires_confirmation), None)
        if confirmation:
            self.status = "awaiting_confirmation"
            return self._decision(
                ProgressDecision.AWAIT_CONFIRMATION,
                "pending_confirmation",
                "工具调用需要用户确认后才能继续。",
                observations,
                per_episode_retry_budget,
            )

        all_failures = [item for item in observations if not item.success]
        self.metrics.tool_failures += len(all_failures)
        expected_failures = [
            item for item in all_failures if self._record_expected_failure_evidence(item)
        ]
        expected_refs = {item.evidence_ref for item in expected_failures}
        for step in self.steps:
            if set(step.evidence_refs).intersection(expected_refs):
                step.status = "completed"
                step.unresolved_questions = []
        failures = [item for item in all_failures if item.evidence_ref not in expected_refs]
        if failures:
            episodes = [self._record_failure(item) for item in failures]
            primary = episodes[0]
            self.active_failure_episode_id = primary.id

            permission_failure = next(
                (
                    item
                    for item in failures
                    if item.error_class in {"permission_error", "authorization_error"}
                ),
                None,
            )
            if permission_failure:
                self.status = "awaiting_authority"
                return self._decision(
                    ProgressDecision.AWAIT_CONFIRMATION,
                    "authorization_required",
                    "当前权限不足，需要用户授权或切换到已授权的数据源。",
                    observations,
                    per_episode_retry_budget,
                    primary,
                )

            blocked_failure = next(
                (
                    item
                    for item in failures
                    if item.error_class in {"scope_violation", "no_executor"}
                ),
                None,
            )
            if blocked_failure:
                self.status = "blocked"
                return self._decision(
                    ProgressDecision.BLOCKED,
                    blocked_failure.error_class,
                    "当前任务缺少可自行恢复的执行条件。",
                    observations,
                    per_episode_retry_budget,
                    primary,
                )

            transient = all(
                item.error_class
                in {"timeout_error", "rate_limit_error", "rate_limited", "connection_error"}
                for item in failures
            )
            budget = transient_retry_budget if transient else per_episode_retry_budget
            exhausted = next((episode for episode in episodes if episode.attempts > budget), None)
            if exhausted:
                exhausted.status = "stalled"
                self.status = "stalled"
                return self._decision(
                    ProgressDecision.STALLED,
                    "failure_episode_exhausted",
                    (
                        f"同一故障链连续 {exhausted.attempts} 次未取得新进展，"
                        "已保存检查点并停止当前故障链。"
                    ),
                    observations,
                    budget,
                    exhausted,
                )

            self.status = "recovering"
            return self._decision(
                ProgressDecision.TRANSIENT_FAILURE
                if transient
                else ProgressDecision.RECOVERABLE_FAILURE,
                "transient_retry" if transient else "tool_failure_detected",
                "已将本次失败记录为独立故障链，将基于错误证据调整策略。",
                observations,
                budget,
                primary,
            )

        new_evidence = False
        for observation in observations:
            if observation.normalized_signature not in self.seen_success_signatures:
                self.seen_success_signatures.append(observation.normalized_signature)
                new_evidence = True

        if new_evidence:
            self.metrics.no_progress_rounds = 0
        else:
            self.metrics.no_progress_rounds += 1

        if self.metrics.no_progress_rounds > max_no_progress_rounds:
            self.status = "stalled"
            return self._decision(
                ProgressDecision.STALLED,
                "no_progress_limit",
                "连续多轮没有产生新证据，已保存检查点。",
                observations,
                per_episode_retry_budget,
            )

        self._resolve_active_episode(observations)
        self.status = "running"
        return self._decision(
            ProgressDecision.PROGRESS,
            (
                "expected_failure_observed"
                if expected_failures
                else "new_evidence"
                if new_evidence
                else "tools_succeeded"
            ),
            (
                "已观察到验收要求中的预期失败并保留证据，将继续其余任务。"
                if expected_failures
                else "工具执行成功，已记录新的可追溯证据。"
                if new_evidence
                else "工具执行成功。"
            ),
            observations,
            per_episode_retry_budget,
        )

    def _record_expected_failure_evidence(self, observation: Observation) -> bool:
        if observation.success or not observation.request_summary.strip():
            return False
        if observation.error_class in {
            "argument_error",
            "invalid_arguments",
            "validation_error",
            "connection_error",
            "timeout_error",
            "rate_limit_error",
        }:
            return False
        payloads = _fenced_action_payloads(self.contract.objective)
        if not payloads:
            return False
        for criterion in self.contract.acceptance_criteria:
            if (
                not criterion.required
                or not criterion.requires_tool_evidence
                or criterion.required_tool_outcome != "failure"
                or criterion.id in self.expected_action_evidence_refs
            ):
                continue
            if any(
                _action_payload_matches(observation.request_summary, payload)
                for payload in payloads
            ):
                self.expected_action_evidence_refs[criterion.id] = observation.evidence_ref
                self.updated_at = _utc_now()
                return True
        return False

    def unresolved_failure_episodes(self) -> list[FailureEpisode]:
        return [
            episode
            for episode in self.failure_episodes
            if episode.status in {"open", "diagnosing", "stalled"}
        ]

    def unresolved_steps(self) -> list[TaskStep]:
        return [step for step in self.steps if step.status != "completed"]

    def record_verification_outcome(self, *, satisfied: bool) -> int:
        """Track consecutive verifier retries that add no tool evidence.

        ``verification_attempts`` remains a lifetime observability counter.  The
        retry safety valve is intentionally separate: a long task may fail
        verification many times while still making useful progress.  Only
        consecutive candidate rewrites without new evidence consume the budget.
        """
        evidence_count = len(self.evidence)
        if satisfied or evidence_count > self.metrics.last_verification_evidence_count:
            self.metrics.verification_no_progress_rounds = 0
        else:
            self.metrics.verification_no_progress_rounds += 1
        self.metrics.last_verification_evidence_count = evidence_count
        self.updated_at = _utc_now()
        return self.metrics.verification_no_progress_rounds

    def apply_failure_assessments(self, verification: VerificationResult) -> None:
        """Apply LLM semantic judgments without encoding domain recovery rules."""
        by_id = {
            str(item.get("id") or ""): item
            for item in verification.failure_assessments
            if isinstance(item, dict)
        }
        for episode in self.unresolved_failure_episodes():
            assessment = by_id.get(episode.id)
            if assessment is None:
                continue
            episode.semantic_assessment = str(assessment.get("reason") or "").strip() or None
            if bool(assessment.get("blocking", True)):
                continue
            episode.status = "superseded"
            refs = [str(item) for item in assessment.get("evidence_refs") or [] if str(item)]
            episode.resolution_evidence_ref = refs[0] if refs else None
            self.metrics.recovered_failures += 1
        if not self.unresolved_failure_episodes():
            self.active_failure_episode_id = None
        self.updated_at = _utc_now()

    def _record_failure(self, observation: Observation) -> FailureEpisode:
        episode = next(
            (
                item
                for item in self.failure_episodes
                if item.signature == observation.normalized_signature
            ),
            None,
        )
        if episode is None:
            now = _utc_now()
            failure_category = observation.category
            if failure_category in {"", "execution_error", "unknown"}:
                failure_category = observation.error_class
            episode = FailureEpisode(
                id=f"failure-{uuid.uuid4().hex[:12]}",
                signature=observation.normalized_signature,
                tool_name=observation.tool_name,
                category=failure_category,
                target_object=observation.target_object,
                normalized_error=_normalize_error(observation.message),
                first_seen_at=now,
                last_seen_at=now,
            )
            self.failure_episodes.append(episode)
        episode.record_failure(observation)
        return episode

    def _resolve_active_episode(self, observations: list[Observation]) -> None:
        if not observations:
            return
        unresolved = self.unresolved_failure_episodes()
        if not unresolved:
            self.active_failure_episode_id = None
            return

        # A turn can contain several independent failures, and recovery evidence
        # may arrive in any order.  Resolve every episode that the new evidence
        # actually addresses instead of considering only the most recently active
        # one; otherwise an older failure can remain orphaned forever and force the
        # verifier into a needless retry loop.
        for episode in unresolved:
            resolution = next(
                (
                    item
                    for item in observations
                    if item.success
                    and (
                        not item.is_discovery
                        or episode.category
                        in {"argument_error", "invalid_arguments", "validation_error"}
                    )
                    and item.tool_name == episode.tool_name
                    and (
                        item.target_object == episode.target_object
                        or episode.category
                        in {"argument_error", "invalid_arguments", "validation_error"}
                        or (
                            (
                                episode.target_object == "global"
                                or episode.target_object.startswith("datasource_id:")
                            )
                            and episode.category
                            in {
                                "execution_error",
                                "schema_error",
                                "syntax_error",
                                "function_error",
                                "timeout_error",
                                "connection_error",
                            }
                        )
                        or (
                            episode.status == "diagnosing"
                            and episode.category in {"unknown_table", "schema_error"}
                        )
                    )
                ),
                None,
            )
            if resolution is None:
                if (
                    episode.status == "open"
                    and episode.category in {"unknown_table", "unknown_column", "schema_error"}
                    and any(item.success and item.is_discovery for item in observations)
                ):
                    episode.status = "diagnosing"
                continue

            episode.status = "resolved"
            episode.resolution_evidence_ref = resolution.evidence_ref
            self.metrics.recovered_failures += 1
            for step in self.steps:
                if step.status == "completed" or not set(step.evidence_refs).intersection(
                    episode.evidence_refs
                ):
                    continue
                step.status = "completed"
                step.unresolved_questions = []
                if resolution.evidence_ref not in step.evidence_refs:
                    step.evidence_refs.append(resolution.evidence_ref)

        remaining = self.unresolved_failure_episodes()
        if not remaining:
            self.active_failure_episode_id = None
        elif self._episode_by_id(self.active_failure_episode_id or "") not in remaining:
            self.active_failure_episode_id = remaining[0].id

    def _episode_by_id(self, episode_id: str) -> FailureEpisode | None:
        return next((item for item in self.failure_episodes if item.id == episode_id), None)

    def _record_evidence(self, observation: Observation, iteration: int) -> None:
        if any(item.ref == observation.evidence_ref for item in self.evidence):
            return
        summary = observation.message or (
            "success" if observation.success else observation.error_class or "tool failure"
        )
        self.evidence.append(
            EvidenceRecord(
                ref=observation.evidence_ref,
                tool_name=observation.tool_name,
                target_object=observation.target_object,
                outcome="success" if observation.success else "failure",
                summary=summary[:2000],
                signature=observation.normalized_signature,
                iteration=iteration,
                request_summary=observation.request_summary,
                evidence_excerpt=observation.evidence_excerpt,
                evidence_chars=observation.evidence_chars,
                evidence_truncated=observation.evidence_truncated,
            )
        )

    def _record_step(self, observation: Observation) -> None:
        goal = observation.planning_goal or f"Execute {observation.tool_name}"
        step = next((item for item in self.steps if item.goal == goal), None)
        if step is None:
            step = TaskStep(
                id=f"step-{len(self.steps) + 1}",
                goal=goal,
                success_criteria=observation.planning_success_criteria,
            )
            self.steps.append(step)
        if observation.evidence_ref not in step.evidence_refs:
            step.evidence_refs.append(observation.evidence_ref)
        step.status = "completed" if observation.success else "recovering"
        if not observation.success and observation.message:
            step.unresolved_questions = [observation.message[:300]]
        elif observation.success:
            step.unresolved_questions = []

    def _decision(
        self,
        decision: ProgressDecision,
        reason_code: str,
        reason: str,
        observations: list[Observation],
        budget: int,
        episode: FailureEpisode | None = None,
    ) -> dict[str, Any]:
        episode = episode or (
            self._episode_by_id(self.active_failure_episode_id)
            if self.active_failure_episode_id
            else None
        )
        action_map = {
            ProgressDecision.PROGRESS: "continue",
            ProgressDecision.RECOVERABLE_FAILURE: "retry",
            ProgressDecision.TRANSIENT_FAILURE: "retry",
            ProgressDecision.AWAIT_CONFIRMATION: "await_confirmation",
            ProgressDecision.BLOCKED: "abort",
            ProgressDecision.STALLED: "abort",
            ProgressDecision.CANDIDATE_COMPLETE: "verify",
        }
        return {
            "action": action_map[decision],
            "decision": decision.value,
            "reason": reason,
            "reason_code": reason_code,
            "task_run_id": self.task_run_id,
            "step_id": self.steps[-1].id if self.steps else None,
            "failure_episode_id": episode.id if episode else None,
            "failure_signature": episode.signature if episode else None,
            "failure_episode_attempts": episode.attempts if episode else 0,
            "evidence_refs": [item.evidence_ref for item in observations],
            "remaining_failure_budget": max(0, budget - episode.attempts) if episode else budget,
            "remaining_global_budget": None,
        }


def deterministic_completion_precheck(journal: TaskJournal) -> VerificationResult:
    """Enforce protocol facts only; semantic completeness belongs to the LLM verifier."""
    if journal.status in {"blocked", "stalled", "awaiting_authority", "awaiting_confirmation"}:
        return VerificationResult(
            satisfied=False,
            reason=f"任务当前状态为 {journal.status}，不能声明完成。",
            missing=["Resolve the current task status before completion."],
            repair_type="blocked",
            evaluator="deterministic",
        )
    for criterion in journal.contract.acceptance_criteria:
        if not criterion.required or not criterion.requires_tool_evidence:
            continue
        dispatched = [
            item
            for item in journal.evidence
            if item.request_summary.strip()
            and not re.search(
                r"(?:invalid tool arguments|malformed arguments|argument pars|transport failure)",
                item.summary,
                re.I,
            )
        ]
        if criterion.required_tool_outcome == "failure":
            evidence_ref = journal.expected_action_evidence_refs.get(criterion.id)
            dispatched = [
                item
                for item in dispatched
                if item.outcome == "failure" and item.ref == evidence_ref
            ]
        elif criterion.required_tool_outcome == "success":
            dispatched = [item for item in dispatched if item.outcome == "success"]
        if dispatched:
            continue
        expected = (
            f" with outcome={criterion.required_tool_outcome}"
            if criterion.required_tool_outcome != "any"
            else ""
        )
        return VerificationResult(
            satisfied=False,
            reason="显式动作验收项缺少真实工具执行证据。",
            missing=[
                f"Dispatch the action required by {criterion.id}{expected} and retain its tool result: "
                f"{criterion.description}"
            ],
            criterion_results=[
                {
                    "id": criterion.id,
                    "satisfied": False,
                    "evidence_refs": [],
                    "reason": "No dispatched tool result satisfies the required action outcome.",
                }
            ],
            repair_type="new_evidence",
            evaluator="deterministic_action_evidence",
        )
    return VerificationResult(satisfied=True, reason="确定性前置检查通过。")


def migrate_task_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade persisted task state to the current schema without mutating input."""
    migrated = dict(payload)
    version = int(migrated.get("version") or 0)
    if version <= 0:
        contract = migrated.get("contract") if isinstance(migrated.get("contract"), dict) else {}
        if not contract:
            contract = {
                "objective": str(migrated.get("objective") or ""),
                "constraints": migrated.get("constraints") or [],
                "acceptance_criteria": migrated.get("acceptance_criteria") or [],
                "output_requirements": migrated.get("output_requirements") or [],
            }
        migrated["contract"] = contract
        migrated.setdefault("failure_episodes", migrated.get("failures") or [])
        migrated.setdefault("evidence", [])
        migrated.setdefault("steps", [])
        migrated.setdefault("metrics", {})
    migrated["version"] = TASK_STATE_VERSION
    return migrated


def parse_verification_result(text: str, *, evaluator: str = "llm") -> VerificationResult:
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.I)
    candidate = fenced.group(1) if fenced else raw
    if not candidate.startswith("{"):
        embedded = re.search(r"\{.*\}", candidate, re.DOTALL)
        candidate = embedded.group(0) if embedded else candidate
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        return VerificationResult(
            satisfied=False,
            reason="验证器未返回有效的结构化结果。",
            missing=["Run completion verification again with valid JSON."],
            evaluator=evaluator,
            malformed=True,
        )
    if not isinstance(payload, dict):
        return VerificationResult(
            satisfied=False,
            reason="验证结果不是 JSON object。",
            missing=["Return an object-shaped verification result."],
            evaluator=evaluator,
            malformed=True,
        )
    satisfied = bool(payload.get("satisfied"))
    reason = str(payload.get("reason") or "")
    missing = [str(item) for item in payload.get("missing") or []]
    repair_type = str(payload.get("repair_type") or ("none" if satisfied else "rewrite"))
    if repair_type not in {"none", "rewrite", "new_evidence", "blocked"}:
        repair_type = "rewrite"
    if satisfied:
        repair_type = "none"
    elif not missing:
        missing = [reason or "Return an actionable repair instruction for the rejected candidate."]
    return VerificationResult(
        satisfied=satisfied,
        reason=reason,
        missing=missing,
        contradictions=[str(item) for item in payload.get("contradictions") or []],
        criterion_results=[
            dict(item) for item in payload.get("criterion_results") or [] if isinstance(item, dict)
        ],
        repair_type=repair_type,
        failure_assessments=[
            dict(item)
            for item in payload.get("failure_assessments") or []
            if isinstance(item, dict)
        ],
        evaluator=evaluator,
    )


def enforce_failure_episode_audit(
    journal: TaskJournal,
    result: VerificationResult,
) -> VerificationResult:
    """Require the LLM verifier to judge every unresolved failure semantically."""
    unresolved = journal.unresolved_failure_episodes()
    if not unresolved:
        return result
    by_id = {
        str(item.get("id") or ""): item
        for item in result.failure_assessments
        if isinstance(item, dict)
    }
    missing_ids = [item.id for item in unresolved if item.id not in by_id]
    if missing_ids:
        return VerificationResult(
            satisfied=False,
            reason="The verifier did not assess every unresolved tool failure.",
            missing=[
                "Assess whether each unresolved failure blocks a user-stated acceptance criterion: "
                + ", ".join(missing_ids)
            ],
            criterion_results=result.criterion_results,
            repair_type="rewrite",
            failure_assessments=result.failure_assessments,
            evaluator="failure_episode_audit",
        )
    blocking = [item for item in unresolved if bool(by_id[item.id].get("blocking", True))]
    if blocking and result.satisfied:
        return VerificationResult(
            satisfied=False,
            reason="The verifier identified unresolved failures that still block required outcomes.",
            missing=[
                str(by_id[item.id].get("reason") or f"Resolve {item.id}") for item in blocking
            ],
            criterion_results=result.criterion_results,
            repair_type="new_evidence",
            failure_assessments=result.failure_assessments,
            evaluator="failure_episode_audit",
        )
    return result


def enforce_compound_criterion_audit(
    journal: TaskJournal,
    result: VerificationResult,
) -> VerificationResult:
    """Require the LLM verifier to account for every part of compound criteria."""
    by_id = {
        str(item.get("id") or ""): item
        for item in result.criterion_results
        if isinstance(item, dict)
    }
    for criterion in journal.contract.acceptance_criteria:
        if (
            not criterion.required
            or criterion.requires_tool_evidence
            or len(criterion.component_hints) < 2
        ):
            continue
        criterion_result = by_id.get(criterion.id) or {}
        components = [
            item
            for item in criterion_result.get("component_results") or []
            if isinstance(item, dict)
        ]
        covered_hints = [
            hint
            for hint in criterion.component_hints
            if any(hint in str(item.get("component") or "") for item in components)
        ]
        if len(components) < 2 or len(covered_hints) != len(criterion.component_hints):
            uncovered = [item for item in criterion.component_hints if item not in covered_hints]
            return VerificationResult(
                satisfied=False,
                reason="复合验收项没有逐项完成审计。",
                missing=[
                    f"Audit every named component of {criterion.id} separately and copy each component_hint "
                    f"verbatim into component_results.component. Uncovered hints: {uncovered}"
                ],
                criterion_results=result.criterion_results,
                evaluator="compound_criterion_audit",
            )
        missing_components = [
            str(item.get("component") or "unnamed component")
            for item in components
            if not item.get("satisfied")
        ]
        if missing_components:
            result.satisfied = False
            result.reason = "复合验收项仍有未满足的组成部分。"
            result.missing.extend(
                f"Complete {criterion.id} component: {item}" for item in missing_components
            )
            result.evaluator = "compound_criterion_audit"
            return result
    return result


def build_component_evidence_prompt(
    journal: TaskJournal,
    result: VerificationResult,
    candidate_text: str = "",
) -> str:
    evidence_by_ref = {item.ref: asdict(item) for item in journal.evidence}
    audits: list[dict[str, Any]] = []
    criteria_by_id = {item.id: item for item in journal.contract.acceptance_criteria}
    for criterion_result in result.criterion_results:
        criterion = criteria_by_id.get(str(criterion_result.get("id") or ""))
        if (
            criterion is None
            or criterion.requires_tool_evidence
            or len(criterion.component_hints) < 2
        ):
            continue
        for component in criterion_result.get("component_results") or []:
            if not isinstance(component, dict):
                continue
            component_text = str(component.get("component") or "")
            if re.search(
                r"(?:报告|输出|总结|answer|report|output|summary).{0,16}(?:覆盖|包含|包括|cover|include)",
                component_text,
                re.I,
            ):
                continue
            refs = [str(item) for item in component.get("evidence_refs") or []]
            if not refs:
                continue
            audits.append(
                {
                    "criterion_id": criterion.id,
                    "component": component_text,
                    "claimed_satisfied": bool(component.get("satisfied")),
                    "evidence": [evidence_by_ref[ref] for ref in refs if ref in evidence_by_ref],
                }
            )
    return (
        "Act as a narrow evidence-to-component verifier. Tools are disabled. Judge only whether each supplied "
        "evidence request and result actually establishes the component's meaning. Normal semantic equivalence is "
        "allowed: schema inspection can establish available fields, grouping expressions can establish dimensions, "
        "and computed aggregates can establish named metrics even when SQL and prose use different words. Reject "
        "only when the evidence method cannot establish the component, especially when it inspects a related but "
        "different named entity, action, population, field, relationship, or time scope. A narrative reason, intent "
        "label, alias, or broad topical similarity cannot substitute for inspecting the entity or relationship that "
        "the component actually names. Inspect both the claimed evidence and all available journal evidence. If a "
        "claimed reference is incomplete but another available evidence item directly establishes the component, "
        "treat the component as supported; request new evidence only when the whole journal lacks a suitable method. "
        "Use the candidate answer to judge presentation requirements such as whether a report covers or distinguishes "
        "a topic, but never use candidate prose as a substitute for tool evidence behind a factual claim. "
        "For factual components, empty evidence is insufficient. Return JSON only with satisfied (boolean), reason "
        "(string), missing (string[]), contradictions (string[]), and criterion_results (array). Name each unsupported "
        "component verbatim in missing.\n\n"
        "COMPONENT EVIDENCE AUDIT:\n"
        + json.dumps(
            {
                "components": audits,
                "available_evidence": list(evidence_by_ref.values()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nCANDIDATE ANSWER:\n"
        + candidate_text
    )


def build_verifier_prompt(
    journal: TaskJournal,
    candidate_text: str,
    *,
    adversarial: bool = False,
    arithmetic: bool = False,
    verification_policies: list[str] | None = None,
) -> str:
    if arithmetic:
        mode = (
            "Act as a forensic arithmetic reconciler. Ignore rhetorical quality and audit every count, total, "
            "subtotal, average, date span, ratio, and percentage in the candidate. Recompute displayed component "
            "lists even when a separate query returned the headline value, and reject any headline that does not "
            "equal its stated components. Verify denominators, rounding, signs, "
            "deduplication, and that the same inclusion rule is used in the headline and breakdown."
        )
    elif adversarial:
        mode = (
            "Act as an adversarial evidence auditor. Assume polished prose may hide unsupported claims. "
            "Actively recompute arithmetic from the supplied evidence, challenge every number, percentage, "
            "range, score, and categorical conclusion, and search for omissions, contradictions, unsafe side "
            "effects, or evidence references that do not actually contain the claimed fact. Inspect each evidence "
            "request as executable proof rather than trusting its title, intent, or output label. Reject circular "
            "evidence that merely restates the desired conclusion. Check population, units, scope, inclusion rules, "
            "and derivation steps for accidental or circular results."
        )
    else:
        mode = "Act as an independent completion verifier."
    evidence = _verification_evidence_payload(journal.evidence)
    active_policies = [item.strip() for item in verification_policies or [] if item.strip()]
    policy_block = (
        "\nACTIVE SKILL VERIFICATION POLICIES:\n"
        + "\n\n".join(f"Policy {index + 1}:\n{item}" for index, item in enumerate(active_policies))
        + "\n"
        if active_policies
        else ""
    )
    return (
        f"{mode}\n"
        "Tools are disabled. Judge only from the task contract, journal evidence, and candidate answer.\n"
        "The candidate must be a self-contained final answer because failed drafts are withheld from the user. "
        "Reject delta-only text that refers to an original/previous report, says all other findings remain, or "
        "only lists corrections without restating every required acceptance criterion.\n"
        "Return JSON only with: satisfied (boolean), reason (string), missing (string[]), "
        "contradictions (string[]), repair_type ('none'|'rewrite'|'new_evidence'|'blocked'), "
        "failure_assessments ([{id, blocking, reason, evidence_refs}]), criterion_results "
        "([{id, satisfied, evidence_refs, reason, component_results: "
        "[{component, satisfied, evidence_refs, reason}]}]).\n"
        "Every required acceptance criterion needs concrete evidence. Natural-language confidence is not evidence. "
        "A failed tool attempt is historical evidence, not automatically an unfinished user requirement. For every "
        "failure episode whose status is open, diagnosing, or stalled, include one failure_assessments entry. Set "
        "blocking=true only when that failure still prevents a user-stated acceptance criterion from being met. "
        "When alternative successful evidence establishes the requested outcome, mark the failed attempt non-blocking "
        "and cite those evidence refs. Never invent a tool, lookup, or output requirement merely because it was tried. "
        "Treat a compound acceptance criterion as a checklist: every component joined by conjunctions, slashes, "
        "commas, semicolons, or enumerations must be satisfied independently. Evidence for one named component "
        "must not be used to mark its siblings satisfied; criterion_results must cite evidence for each factual "
        "component or report the uncovered component in missing. For every compound criterion, component_results "
        "is mandatory and must contain at least two separately judged entries. The task contract supplies "
        "component_hints for these criteria; copy every hint verbatim into a component_results.component value, "
        "then judge that exact component. Do not translate, merge, omit, or rename component_hints. "
        "When an acceptance criterion has requires_tool_evidence=true, require a journal evidence item showing "
        "that the requested action was actually dispatched to the relevant tool. Match the action semantically "
        "against tool_name and request_summary; planning text, later substitute actions, and malformed arguments "
        "do not satisfy it. A requested failing execution is satisfied only by a failure result from the intended "
        "tool for the requested action, not by argument parsing or transport failure before dispatch. "
        "Every material claim must be traceable to journal evidence, explicit task input, or a clearly labelled "
        "assumption. Reject unsupported labels, ratings, causal conclusions, and absence claims.\n"
        "For every numeric claim, verify that it is directly present in evidence or recompute it from complete "
        "evidence using a stated formula. Numerators and denominators in percentages or impact totals must use "
        "compatible units, the same population and entity grain, and a clearly stated inclusion rule; reject any "
        "calculation that combines incomparable measures. A separately evidenced headline total does not excuse "
        "an unexplained mismatch with the candidate's displayed breakdown: require the answer to reconcile the "
        "difference and state any different filters, populations, or grains. If the available "
        "evidence is sampled or truncated, reject claims that "
        "depend on unseen items and request evidence at the complete population level. Do not accept an evidence "
        "item merely because its title or output label matches the claim; its method must actually establish the "
        "claim. One unsupported material claim makes satisfied=false. Treat every absence statement as a material "
        "claim: not inspected means unknown, not absent.\n"
        "If satisfied=false, missing must contain actionable repairs. Set repair_type=new_evidence only when the "
        "journal truly lacks source facts; use rewrite when the available evidence is sufficient but the candidate "
        "misstates, omits, or overclaims it; use blocked only when progress needs new authority or an unavailable "
        "external condition. Set repair_type=none only when satisfied=true.\n"
        f"{policy_block}\n"
        f"TASK CONTRACT:\n{json.dumps(journal.contract.to_dict(), ensure_ascii=False, indent=2)}\n\n"
        f"USER CORRECTIONS (newest instructions override conflicting earlier details):\n"
        f"{json.dumps(journal.user_corrections, ensure_ascii=False, indent=2)}\n\n"
        f"TASK STATUS: {journal.status}\n"
        f"FAILURE EPISODES:\n{json.dumps([asdict(item) for item in journal.failure_episodes], ensure_ascii=False, indent=2)}\n\n"
        f"EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        f"CANDIDATE ANSWER:\n{candidate_text}"
    )


def _fenced_action_payloads(text: str) -> list[str]:
    return [
        item.strip() for item in re.findall(r"```(?:[^\n`]*)\n(.*?)```", text, re.S) if item.strip()
    ]


def _action_payload_matches(request_summary: str, payload: str) -> bool:
    def normalize(value: str) -> str:
        value = re.sub(r"^\s*[a-z_][\w -]{0,30}:\s*", "", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip().rstrip(";").lower()

    normalized_request = normalize(request_summary)
    normalized_payload = normalize(payload)
    return bool(
        normalized_payload
        and (normalized_payload in normalized_request or normalized_request in normalized_payload)
    )


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def _extract_target_object(tool_name: str, arguments: dict[str, Any], message: str) -> str:
    for key in ("object_id", "path", "url", "service_id", "table", "collection"):
        value = arguments.get(key)
        if value not in {None, ""}:
            return f"{key}:{value}"
    sql = str(arguments.get("sql") or "")
    if sql:
        match = re.search(
            r"\b(?:from|join|update|into|table|describe|desc)\s+[`\"']?([\w.]+)",
            sql,
            re.I,
        )
        if match:
            return f"sql:{match.group(1).lower()}"
    quoted = re.search(r"(?:table|column|function)\s+['`\"]([^'`\"]+)", message, re.I)
    if quoted:
        return f"db:{quoted.group(1).lower()}"
    if arguments.get("datasource_id") not in {None, ""}:
        return f"datasource_id:{arguments['datasource_id']}"
    return "global"


def _normalize_error_class(
    *,
    explicit: str,
    category: str,
    code: str,
    message: str,
    errno: int | None,
) -> str:
    text = " ".join([category, code, message]).lower()
    normalized_explicit = explicit.lower().strip()
    if normalized_explicit in {"argument_error", "invalid_arguments", "validation_error"}:
        return "argument_error"
    if category in {"result_shape", "result_shape_error", "cardinality_error"} or errno in {
        1241,
        1242,
    }:
        return "result_shape_error"
    if category in {"unknown_table", "unknown_column", "schema_error"} or errno in {1054, 1146}:
        return "schema_error"
    if category in {"permission", "permission_denied", "access_denied", "authorization"}:
        return "permission_error"
    if category in {"timeout", "query_timeout"}:
        return "timeout_error"
    if category in {"rate_limit", "rate_limited"}:
        return "rate_limit_error"
    if category in {"connection", "connection_error", "unavailable"}:
        return "connection_error"
    if "unknown table" in text or "unknown column" in text:
        return "schema_error"
    if "permission" in text or "access denied" in text or "not authorized" in text:
        return "permission_error"
    if "timeout" in text or "timed out" in text:
        return "timeout_error"
    if "rate limit" in text or "too many requests" in text:
        return "rate_limit_error"
    if "connection" in text and ("refused" in text or "failed" in text or "lost" in text):
        return "connection_error"
    return (
        normalized_explicit
        if normalized_explicit and normalized_explicit != "none"
        else ("none" if not text.strip() else "execution_error")
    )


def _normalize_error(text: str) -> str:
    normalized = " ".join(str(text or "").lower().split())
    normalized = re.sub(r"\b\d{4,}\b", "<n>", normalized)
    return normalized[:500]


def _summarize_success_data(value: Any) -> str:
    excerpt, _, _ = _build_evidence_excerpt(value, max_chars=2000)
    return " ".join(excerpt.split()) or "tool completed successfully"


def _summarize_tool_request(tool_name: str, arguments: dict[str, Any]) -> str:
    """Preserve a generic, redacted tool request for semantic verification."""
    safe_arguments = _redact_sensitive_arguments(arguments)
    excerpt, _, _ = _build_evidence_excerpt(safe_arguments, max_chars=2000)
    return f"{tool_name}: {excerpt}" if excerpt else tool_name


def _redact_sensitive_arguments(value: Any, *, key: str = "") -> Any:
    if key and _SENSITIVE_ARGUMENT_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_sensitive_arguments(item_value, key=str(item_key))
            for item_key, item_value in value.items()
            if str(item_key) != "_runtime"
        }
    if isinstance(value, list):
        return [_redact_sensitive_arguments(item) for item in value]
    return value


def _build_evidence_excerpt(
    value: Any,
    *,
    max_chars: int = _EVIDENCE_EXCERPT_MAX_CHARS,
) -> tuple[str, int, bool]:
    """Bound arbitrary structured evidence without selecting domain-specific fields."""
    if value is None:
        return "", 0, False
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    original_chars = len(rendered)
    if original_chars <= max_chars:
        return rendered, original_chars, False
    marker = f"...<truncated {original_chars - max_chars} chars>..."
    head_chars = max(1, int(max_chars * 0.72))
    tail_chars = max(1, max_chars - head_chars - len(marker))
    excerpt = rendered[:head_chars] + marker + rendered[-tail_chars:]
    return excerpt, original_chars, True


def _verification_evidence_payload(evidence: list[EvidenceRecord]) -> list[dict[str, Any]]:
    """Expose every evidence record while sharing one bounded verifier context budget."""
    if not evidence:
        return []
    per_record_budget = max(
        800,
        min(_EVIDENCE_EXCERPT_MAX_CHARS, _VERIFIER_EVIDENCE_BUDGET_CHARS // len(evidence)),
    )
    payload: list[dict[str, Any]] = []
    for item in evidence:
        record = asdict(item)
        excerpt = item.evidence_excerpt
        if len(excerpt) > per_record_budget:
            marker = f"...<verifier excerpt shortened from {len(excerpt)} chars>..."
            head_chars = max(1, int(per_record_budget * 0.72))
            tail_chars = max(1, per_record_budget - head_chars - len(marker))
            record["evidence_excerpt"] = excerpt[:head_chars] + marker + excerpt[-tail_chars:]
            record["evidence_truncated"] = True
        payload.append(record)
    return payload


def _is_discovery_call(tool_name: str, arguments: dict[str, Any]) -> bool:
    name = tool_name.lower()
    if any(token in name for token in ("list", "describe", "inspect", "search", "schema")):
        return True
    if name == "execute_sql":
        sql = str(arguments.get("sql") or "").strip().lower()
        return bool(
            re.match(r"^(?:show\b|describe\b|desc\b|explain\b)", sql) or "information_schema" in sql
        )
    return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
