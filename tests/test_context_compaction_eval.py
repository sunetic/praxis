import pytest

from evals.context_compaction.run import (
    load_scenarios,
    score_answer,
    score_compaction,
    score_scenario,
)


def test_context_compaction_eval_catalog_covers_distinct_failure_modes() -> None:
    scenarios = load_scenarios()

    assert {scenario["id"] for scenario in scenarios} == {
        "constraints_vs_digressions",
        "latest_correction_wins",
        "failed_attempts_and_artifacts",
    }
    assert all(len(scenario["questions"]) >= 3 for scenario in scenarios)
    assert all(scenario["forbidden_memory_terms"] for scenario in scenarios)


def test_context_compaction_eval_scorer_requires_all_groups_and_no_leakage() -> None:
    question = {
        "expected_groups": [["sales_prod"], ["orders_v3"], ["500ms", "500 ms"]],
        "forbidden": ["牛肉面"],
    }

    passed = score_answer("sales_prod.orders_v3 的目标是 500 ms。", question)
    leaked = score_answer("sales_prod.orders_v3 的目标是 500ms，午饭是牛肉面。", question)
    missing = score_answer("目标表是 sales_prod.orders_v3。", question)

    assert passed["passed"] is True
    assert leaked["passed"] is False
    assert leaked["distractor_leaks"] == ["牛肉面"]
    assert missing["passed"] is False
    assert missing["recall"] == 0.667


@pytest.mark.parametrize(
    "failure",
    [None, "missing_fact", "distractor", "no_reduction", "missing_event", "unused_snapshot"],
)
def test_native_compaction_requires_submitted_summary_facts_and_token_reduction(failure):
    scenario = {"required_memory_terms": ["orders", "500ms"], "forbidden_memory_terms": ["noodles"]}
    summary = "orders 500ms"
    if failure == "missing_fact":
        summary = "orders"
    if failure == "distractor":
        summary += " noodles"
    snapshots = [{"id": "snapshot", "summary": summary}]
    events = (
        [
            {
                "seq": 3,
                "kind": "context_compacted",
                "payload": {
                    "snapshot_id": "snapshot" if failure != "unused_snapshot" else None,
                    "before_tokens": 4000,
                    "after_tokens": 4000 if failure == "no_reduction" else 2000,
                },
            }
        ]
        if failure != "missing_event"
        else []
    )
    scored = score_compaction(scenario, snapshots, events)
    assert scored["passed"] is (failure is None)


def test_perfect_recall_does_not_mask_missing_baseline_or_failed_memory_gate():
    question = {"baseline": {"recall": 1}, "compacted": {"recall": 1}, "passed": True}
    assert score_scenario([question])["passed"]
    assert not score_scenario([{**question, "baseline": {"recall": 0}}])["passed"]
    assert not score_scenario([{**question, "passed": False}])["passed"]
    assert not score_scenario([])["passed"]
