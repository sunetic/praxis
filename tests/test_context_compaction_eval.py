from evals.context_compaction.run import load_scenarios, score_answer


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
