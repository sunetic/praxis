"""Deterministic trajectory metrics and profile routing for agent evals.

The evaluator consumes runtime events rather than final prose.  This makes it
possible to compare models/reasoning profiles on recovery, verification, cost,
and latency without trusting a model's own claim that a task succeeded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.agent.task_runtime import TaskContract


@dataclass(frozen=True)
class AgentExecutionProfile:
    name: str
    reasoning_effort: str
    completion_verifier: bool
    adversarial_verifier: bool
    parallel_read_only: bool
    intended_for: str


FAST_PROFILE = AgentExecutionProfile(
    name="fast",
    reasoning_effort="low",
    completion_verifier=False,
    adversarial_verifier=False,
    parallel_read_only=True,
    intended_for="simple questions and low-risk single-step reads",
)
BALANCED_PROFILE = AgentExecutionProfile(
    name="balanced",
    reasoning_effort="medium",
    completion_verifier=True,
    adversarial_verifier=False,
    parallel_read_only=True,
    intended_for="multi-step analysis with explicit acceptance criteria",
)
HIGH_ASSURANCE_PROFILE = AgentExecutionProfile(
    name="high_assurance",
    reasoning_effort="high",
    completion_verifier=True,
    adversarial_verifier=True,
    parallel_read_only=False,
    intended_for="high-value audits, migrations, releases, and security work",
)


@dataclass
class TrajectoryMetrics:
    case_id: str
    profile: str
    completed: bool
    status: str
    iterations: int
    tool_calls: int
    tool_failures: int
    failure_episodes: int
    recovered_failures: int
    verification_attempts: int
    acceptance_criteria_coverage: float
    premature_stop: bool
    resumed: bool
    resume_succeeded: bool
    elapsed_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSuiteReport:
    profile: str
    cases: list[TrajectoryMetrics] = field(default_factory=list)

    @property
    def task_completion_rate(self) -> float:
        return _ratio(sum(case.completed for case in self.cases), len(self.cases))

    @property
    def acceptance_criteria_coverage(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.acceptance_criteria_coverage for case in self.cases) / len(self.cases)

    @property
    def recoverable_failure_recovery_rate(self) -> float:
        total = sum(case.failure_episodes for case in self.cases)
        recovered = sum(case.recovered_failures for case in self.cases)
        return _ratio(recovered, total)

    @property
    def premature_stop_rate(self) -> float:
        return _ratio(sum(case.premature_stop for case in self.cases), len(self.cases))

    @property
    def resume_success_rate(self) -> float:
        resumed = [case for case in self.cases if case.resumed]
        return _ratio(sum(case.resume_succeeded for case in resumed), len(resumed))

    @property
    def average_tool_calls(self) -> float:
        return _average([float(case.tool_calls) for case in self.cases])

    @property
    def average_tokens(self) -> float:
        return _average([float(case.total_tokens) for case in self.cases])

    @property
    def average_latency_ms(self) -> float:
        return _average([case.elapsed_ms for case in self.cases])

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "case_count": len(self.cases),
            "task_completion_rate": self.task_completion_rate,
            "acceptance_criteria_coverage": self.acceptance_criteria_coverage,
            "recoverable_failure_recovery_rate": self.recoverable_failure_recovery_rate,
            "premature_stop_rate": self.premature_stop_rate,
            "resume_success_rate": self.resume_success_rate,
            "average_tool_calls": self.average_tool_calls,
            "average_tokens": self.average_tokens,
            "average_latency_ms": self.average_latency_ms,
            "cases": [case.to_dict() for case in self.cases],
        }


def route_execution_profile(contract: TaskContract) -> AgentExecutionProfile:
    if contract.high_value:
        return HIGH_ASSURANCE_PROFILE
    if contract.complex:
        return BALANCED_PROFILE
    return FAST_PROFILE


def summarize_trajectory(
    events: list[dict[str, Any]],
    *,
    case_id: str,
    profile: str,
    elapsed_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> TrajectoryMetrics:
    done = next((event for event in reversed(events) if event.get("type") == "done"), {})
    done_data = done.get("data") if isinstance(done.get("data"), dict) else {}
    state_event = next(
        (event for event in reversed(events) if event.get("type") == "task_state"),
        {},
    )
    state = state_event.get("data") if isinstance(state_event.get("data"), dict) else {}
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    failure_episodes = [
        item for item in state.get("failure_episodes") or [] if isinstance(item, dict)
    ]
    verification = state.get("verification") if isinstance(state.get("verification"), dict) else {}
    contract = state.get("contract") if isinstance(state.get("contract"), dict) else {}
    criteria = [
        item for item in contract.get("acceptance_criteria") or [] if isinstance(item, dict)
    ]
    criterion_results = [
        item for item in verification.get("criterion_results") or [] if isinstance(item, dict)
    ]
    required_ids = {str(item.get("id") or "") for item in criteria if item.get("required", True)}
    covered_ids = {
        str(item.get("id") or "") for item in criterion_results if bool(item.get("satisfied"))
    }
    completed = bool(done_data.get("completed"))
    if required_ids:
        coverage = _ratio(len(required_ids & covered_ids), len(required_ids))
    else:
        coverage = 1.0 if completed else 0.0
    status = str(done_data.get("status") or state.get("status") or "unknown")
    resumptions = int(metrics.get("resumptions") or 0)
    recovered = sum(item.get("status") == "resolved" for item in failure_episodes)
    premature_stop = status in {"incomplete", "checkpointed", "stalled"} and not completed
    return TrajectoryMetrics(
        case_id=case_id,
        profile=profile,
        completed=completed,
        status=status,
        iterations=int(metrics.get("iterations") or (done.get("meta") or {}).get("iteration") or 0),
        tool_calls=int(metrics.get("tool_calls") or 0),
        tool_failures=int(metrics.get("tool_failures") or 0),
        failure_episodes=len(failure_episodes),
        recovered_failures=int(metrics.get("recovered_failures") or recovered),
        verification_attempts=int(metrics.get("verification_attempts") or 0),
        acceptance_criteria_coverage=coverage,
        premature_stop=premature_stop,
        resumed=resumptions > 0,
        resume_succeeded=resumptions > 0 and completed,
        elapsed_ms=max(0.0, elapsed_ms or float(metrics.get("elapsed_ms") or 0.0)),
        input_tokens=max(0, input_tokens or int(metrics.get("input_tokens") or 0)),
        output_tokens=max(0, output_tokens or int(metrics.get("output_tokens") or 0)),
    )


def compare_profiles(
    baseline: EvalSuiteReport,
    candidate: EvalSuiteReport,
    *,
    min_completion_gain: float = 0.02,
    max_token_multiplier: float = 2.0,
) -> dict[str, Any]:
    token_multiplier = (
        candidate.average_tokens / baseline.average_tokens
        if baseline.average_tokens > 0
        else 1.0
    )
    completion_gain = candidate.task_completion_rate - baseline.task_completion_rate
    coverage_gain = (
        candidate.acceptance_criteria_coverage - baseline.acceptance_criteria_coverage
    )
    recovery_gain = (
        candidate.recoverable_failure_recovery_rate
        - baseline.recoverable_failure_recovery_rate
    )
    quality_improved = completion_gain >= min_completion_gain or (
        completion_gain >= 0 and coverage_gain > 0 and recovery_gain >= 0
    )
    recommend = bool(
        quality_improved
        and candidate.premature_stop_rate <= baseline.premature_stop_rate
        and token_multiplier <= max_token_multiplier
    )
    return {
        "baseline": baseline.profile,
        "candidate": candidate.profile,
        "completion_gain": completion_gain,
        "coverage_gain": coverage_gain,
        "recovery_gain": recovery_gain,
        "premature_stop_delta": (
            candidate.premature_stop_rate - baseline.premature_stop_rate
        ),
        "token_multiplier": token_multiplier,
        "latency_delta_ms": candidate.average_latency_ms - baseline.average_latency_ms,
        "recommend_candidate": recommend,
        "reason": (
            "candidate improves measured quality within cost guardrails"
            if recommend
            else "candidate does not meet measured quality/cost guardrails"
        ),
    }


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
