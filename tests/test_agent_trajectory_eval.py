from __future__ import annotations

from app.services.agent.task_runtime import TaskContract
from app.services.agent.trajectory_eval import (
    BALANCED_PROFILE,
    FAST_PROFILE,
    HIGH_ASSURANCE_PROFILE,
    EvalSuiteReport,
    compare_profiles,
    route_execution_profile,
    summarize_trajectory,
)


def _trajectory(
    *,
    completed: bool,
    status: str,
    failures: list[str],
    criteria: list[tuple[str, bool]],
    resumptions: int = 0,
    tool_calls: int = 0,
) -> list[dict]:
    contract_criteria = [
        {"id": criterion_id, "description": criterion_id, "required": True}
        for criterion_id, _ in criteria
    ]
    criterion_results = [
        {"id": criterion_id, "satisfied": satisfied, "evidence_refs": [f"ev-{criterion_id}"]}
        for criterion_id, satisfied in criteria
    ]
    failure_episodes = [
        {"id": f"failure-{index}", "status": episode_status}
        for index, episode_status in enumerate(failures, start=1)
    ]
    return [
        {
            "type": "task_state",
            "data": {
                "status": status,
                "contract": {"acceptance_criteria": contract_criteria},
                "failure_episodes": failure_episodes,
                "verification": {"criterion_results": criterion_results},
                "metrics": {
                    "iterations": 8,
                    "tool_calls": tool_calls,
                    "tool_failures": len(failures),
                    "recovered_failures": sum(item == "resolved" for item in failures),
                    "verification_attempts": 1,
                    "resumptions": resumptions,
                },
            },
        },
        {
            "type": "done",
            "data": {"completed": completed, "status": status},
            "meta": {"iteration": 8},
        },
    ]


def test_profile_routing_is_driven_by_contract_risk_and_complexity() -> None:
    simple = TaskContract(objective="1+1")
    complex_contract = TaskContract(objective="audit", complex=True)
    high_value = TaskContract(objective="production audit", complex=True, high_value=True)

    assert route_execution_profile(simple) == FAST_PROFILE
    assert route_execution_profile(complex_contract) == BALANCED_PROFILE
    assert route_execution_profile(high_value) == HIGH_ASSURANCE_PROFILE


def test_trajectory_metrics_use_state_and_verification_not_final_prose() -> None:
    metrics = summarize_trajectory(
        _trajectory(
            completed=True,
            status="completed",
            failures=["resolved", "resolved", "resolved"],
            criteria=[("ac-1", True), ("ac-2", True), ("ac-3", False)],
            resumptions=1,
            tool_calls=11,
        ),
        case_id="three-independent-errors",
        profile="candidate",
        elapsed_ms=1250,
        input_tokens=1000,
        output_tokens=500,
    )

    assert metrics.completed is True
    assert metrics.failure_episodes == 3
    assert metrics.recovered_failures == 3
    assert metrics.acceptance_criteria_coverage == 2 / 3
    assert metrics.resume_succeeded is True
    assert metrics.total_tokens == 1500


def test_eval_suite_compares_completion_recovery_cost_and_latency() -> None:
    baseline = EvalSuiteReport(
        profile="baseline",
        cases=[
            summarize_trajectory(
                _trajectory(
                    completed=False,
                    status="stalled",
                    failures=["resolved", "resolved", "stalled"],
                    criteria=[("ac-1", True), ("ac-2", False)],
                    tool_calls=6,
                ),
                case_id="sql-recovery",
                profile="baseline",
                elapsed_ms=800,
                input_tokens=500,
                output_tokens=300,
            ),
            summarize_trajectory(
                _trajectory(
                    completed=True,
                    status="completed",
                    failures=[],
                    criteria=[("ac-1", True)],
                    tool_calls=1,
                ),
                case_id="simple-read",
                profile="baseline",
                elapsed_ms=200,
                input_tokens=100,
                output_tokens=50,
            ),
        ],
    )
    candidate = EvalSuiteReport(
        profile="candidate",
        cases=[
            summarize_trajectory(
                _trajectory(
                    completed=True,
                    status="completed",
                    failures=["resolved", "resolved", "resolved"],
                    criteria=[("ac-1", True), ("ac-2", True)],
                    tool_calls=9,
                ),
                case_id="sql-recovery",
                profile="candidate",
                elapsed_ms=1100,
                input_tokens=800,
                output_tokens=400,
            ),
            summarize_trajectory(
                _trajectory(
                    completed=True,
                    status="completed",
                    failures=[],
                    criteria=[("ac-1", True)],
                    tool_calls=1,
                ),
                case_id="simple-read",
                profile="candidate",
                elapsed_ms=210,
                input_tokens=120,
                output_tokens=60,
            ),
        ],
    )

    comparison = compare_profiles(baseline, candidate, max_token_multiplier=2.0)

    assert baseline.task_completion_rate == 0.5
    assert candidate.task_completion_rate == 1.0
    assert candidate.recoverable_failure_recovery_rate == 1.0
    assert comparison["recommend_candidate"] is True
    assert comparison["completion_gain"] == 0.5
    assert comparison["token_multiplier"] < 2.0


def test_profile_is_not_recommended_when_cost_guardrail_is_exceeded() -> None:
    baseline_case = summarize_trajectory(
        _trajectory(
            completed=False,
            status="checkpointed",
            failures=[],
            criteria=[("ac-1", False)],
        ),
        case_id="case",
        profile="base",
        input_tokens=100,
    )
    candidate_case = summarize_trajectory(
        _trajectory(
            completed=True,
            status="completed",
            failures=[],
            criteria=[("ac-1", True)],
        ),
        case_id="case",
        profile="expensive",
        input_tokens=1000,
    )

    comparison = compare_profiles(
        EvalSuiteReport(profile="base", cases=[baseline_case]),
        EvalSuiteReport(profile="expensive", cases=[candidate_case]),
        max_token_multiplier=2.0,
    )

    assert comparison["recommend_candidate"] is False
    assert comparison["token_multiplier"] == 10.0
