from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.agent.task_contract import AcceptanceCriterion, TaskContract
from app.services.agent.task_runtime import (
    Observation,
    ProgressDecision,
    TaskJournal,
    build_component_evidence_prompt,
    build_verifier_prompt,
    deterministic_completion_precheck,
    enforce_compound_criterion_audit,
    enforce_failure_episode_audit,
    migrate_task_state,
    parse_verification_result,
)


def _execution(
    *,
    call_id: str,
    sql: str,
    success: bool,
    category: str = "",
    message: str = "",
    error_class: str = "execution_error",
    errno: int | None = None,
    requires_confirmation: bool = False,
    planning_goal: str = "",
) -> dict[str, Any]:
    error: dict[str, Any] | None = None
    if not success:
        error = {
            "code": "sql_execution_error",
            "category": category,
            "db_message": message,
            "message": message,
        }
        if errno is not None:
            error["db_errno"] = errno
    return {
        "tool_call_id": call_id,
        "name": "execute_sql",
        "arguments": {"sql": sql},
        "result": {
            "success": success,
            "data": {"requires_confirmation": requires_confirmation},
            "error": error,
        },
        "error_class": error_class,
        "planning_meta": {"goal": planning_goal},
    }


def _journal() -> TaskJournal:
    return TaskJournal.create(TaskContract(objective="Run the database audit."))


def _evaluate(journal: TaskJournal, item: dict[str, Any], iteration: int) -> dict[str, Any]:
    return journal.evaluate_observations(
        [Observation.from_execution(item)],
        iteration=iteration,
        per_episode_retry_budget=2,
        transient_retry_budget=3,
        max_no_progress_rounds=2,
    )


def test_small_result_summary_keeps_all_rows_for_completion_verification() -> None:
    observation = Observation.from_execution(
        {
            "tool_call_id": "status-summary",
            "name": "execute_sql",
            "arguments": {"sql": "SELECT status, total FROM orders"},
            "result": {
                "success": True,
                "data": {
                    "rows": [{"status": f"status-{index}", "total": index} for index in range(1, 9)]
                },
            },
        }
    )

    assert '"status": "status-8"' in observation.message


def test_generic_evidence_envelope_preserves_nested_content_and_redacts_secrets() -> None:
    observation = Observation.from_execution(
        {
            "tool_call_id": "knowledge-evidence",
            "name": "reference_lookup",
            "arguments": {"query": "incident policy", "api_token": "do-not-store"},
            "result": {
                "success": True,
                "data": {
                    "snippets": [
                        {
                            "source": "policy.md",
                            "section": "Severity",
                            "content": "A customer-facing wait above the stated threshold is critical.",
                        }
                    ]
                },
            },
        }
    )

    assert "incident policy" in observation.request_summary
    assert "do-not-store" not in observation.request_summary
    assert "[REDACTED]" in observation.request_summary
    assert "customer-facing wait" in observation.evidence_excerpt
    assert observation.evidence_truncated is False


def test_evidence_keeps_sql_methodology_and_verifier_rejects_mixed_units() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="revenue-evidence",
            sql="SELECT SUM(total_amount) AS revenue FROM eval_orders",
            success=True,
            error_class="none",
        ),
        1,
    )

    assert journal.evidence[0].request_summary == (
        'execute_sql: {"sql": "SELECT SUM(total_amount) AS revenue FROM eval_orders"}'
    )
    prompt = build_verifier_prompt(
        journal,
        "Metric from entity A divided by a total from entity B is 9.3%.",
        adversarial=True,
        verification_policies=[
            "Check domain-specific population compatibility and source provenance."
        ],
    )
    assert "same population" in prompt
    assert "SELECT SUM(total_amount)" in prompt
    assert "order IDs" not in prompt
    assert "ACTIVE SKILL VERIFICATION POLICIES" in prompt
    assert "source provenance" in prompt
    assert "not inspected means unknown, not absent" in prompt
    assert "compound acceptance criterion as a checklist" in prompt
    assert "Evidence for one named component" in prompt
    arithmetic_prompt = build_verifier_prompt(
        journal,
        "Headline: 10; components: 4 + 5.",
        arithmetic=True,
    )
    assert "forensic arithmetic reconciler" in arithmetic_prompt
    assert "reject any headline that does not equal its stated components" in arithmetic_prompt


def test_verifier_requires_dispatched_tool_result_for_explicit_action() -> None:
    criterion = AcceptanceCriterion(
        id="ac-1",
        description="运行给定检查并保留失败记录",
        requires_tool_evidence=True,
        required_tool_outcome="failure",
    )
    journal = TaskJournal.create(
        TaskContract(
            objective="运行给定检查。\n```text\nprobe --bad\n```",
            acceptance_criteria=[criterion],
            complex=True,
        )
    )

    prompt = build_verifier_prompt(journal, "检查已经完成。")

    assert '"requires_tool_evidence": true' in prompt
    assert "malformed arguments do not satisfy it" in prompt
    assert "failure result from the intended tool" in prompt


def test_compound_criterion_requires_component_level_verifier_results() -> None:
    journal = TaskJournal.create(
        TaskContract(
            objective="生成检查报告",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="ac-1",
                    description="报告覆盖队列和事件异常",
                    component_hints=["最终报告必须覆盖队列", "事件异常"],
                )
            ],
            complex=True,
        )
    )
    criterion = journal.contract.acceptance_criteria[0]
    shallow = parse_verification_result(
        '{"satisfied":true,"criterion_results":['
        f'{{"id":"{criterion.id}","satisfied":true,"evidence_refs":["queue"]}}]}}'
    )

    rejected = enforce_compound_criterion_audit(journal, shallow)

    assert rejected.satisfied is False
    assert rejected.evaluator == "compound_criterion_audit"
    assert criterion.id in rejected.missing[0]

    complete = parse_verification_result(
        json.dumps(
            {
                "satisfied": True,
                "criterion_results": [
                    {
                        "id": criterion.id,
                        "satisfied": True,
                        "component_results": [
                            {
                                "component": criterion.component_hints[0],
                                "satisfied": True,
                                "evidence_refs": ["queue"],
                            },
                            {
                                "component": criterion.component_hints[1],
                                "satisfied": True,
                                "evidence_refs": ["event"],
                            },
                        ],
                    }
                ],
            }
        )
    )
    assert enforce_compound_criterion_audit(journal, complete).satisfied is True
    assert criterion.component_hints == ["最终报告必须覆盖队列", "事件异常"]
    component_prompt = build_component_evidence_prompt(journal, complete, "候选报告正文")
    assert "narrow evidence-to-component verifier" in component_prompt
    assert "related but different named entity" in component_prompt
    assert "Normal semantic equivalence is allowed" in component_prompt
    assert "事件异常" in component_prompt
    assert '"component": "最终报告必须覆盖队列"' not in component_prompt
    assert "CANDIDATE ANSWER:\n候选报告正文" in component_prompt


def test_explicit_action_criterion_uses_action_gate_not_component_audit() -> None:
    journal = TaskJournal.create(
        TaskContract(
            objective="执行检查。\n```text\nprobe --bad\n```",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="ac-1",
                    description="执行检查并观察结果",
                    requires_tool_evidence=True,
                    required_tool_outcome="failure",
                )
            ],
            complex=True,
        )
    )
    criterion = next(
        item for item in journal.contract.acceptance_criteria if item.requires_tool_evidence
    )
    result = parse_verification_result(
        json.dumps(
            {
                "satisfied": True,
                "criterion_results": [{"id": criterion.id, "satisfied": True}],
            }
        )
    )

    assert enforce_compound_criterion_audit(journal, result).satisfied is True


def test_completion_precheck_requires_real_failure_for_requested_failing_action() -> None:
    journal = TaskJournal.create(
        TaskContract(
            objective=(
                "执行给定检查。\n```sql\nSELECT unsupported_function(created_at) FROM records\n```"
            ),
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="ac-1",
                    description="执行给定检查并保留失败记录",
                    requires_tool_evidence=True,
                    required_tool_outcome="failure",
                )
            ],
            complex=True,
        )
    )

    missing = deterministic_completion_precheck(journal)
    assert missing.satisfied is False
    assert missing.evaluator == "deterministic_action_evidence"
    assert "outcome=failure" in missing.missing[0]

    _evaluate(
        journal,
        _execution(
            call_id="expected-failure",
            sql="SELECT unsupported_function(created_at) FROM records",
            success=False,
            category="execution_error",
            message="FUNCTION unsupported_function does not exist",
        ),
        1,
    )

    assert deterministic_completion_precheck(journal).satisfied is True
    assert journal.failure_episodes == []
    assert journal.expected_action_evidence_refs


def test_structured_sql_category_overrides_legacy_execution_error_class() -> None:
    observation = Observation.from_execution(
        {
            **_execution(
                call_id="unknown-table",
                sql="SELECT * FROM missing_orders",
                success=False,
                category="unknown_table",
                message="Table 'db.missing_orders' does not exist",
                error_class="execution_error",
                errno=1146,
            ),
            "arguments": {"sql": "SELECT * FROM missing_orders", "datasource_id": 2},
        }
    )

    assert observation.error_class == "schema_error"
    assert observation.category == "unknown_table"
    assert observation.target_object == "sql:missing_orders"


def test_independent_failures_receive_independent_episode_budgets() -> None:
    journal = _journal()
    failures = [
        _execution(
            call_id="column",
            sql="SELECT customer_email FROM eval_customers",
            success=False,
            category="unknown_column",
            message="Unknown column 'customer_email' in 'field list'",
            errno=1054,
        ),
        _execution(
            call_id="dialect",
            sql="SELECT DATE_TRUNC(created_at) FROM eval_orders",
            success=False,
            category="execution_error",
            message="FUNCTION DATE_TRUNC does not exist",
            errno=1305,
        ),
        _execution(
            call_id="table",
            sql="SELECT * FROM eval_order_lines",
            success=False,
            category="unknown_table",
            message="Table 'db.eval_order_lines' does not exist",
            errno=1146,
        ),
    ]

    decisions = [_evaluate(journal, item, index) for index, item in enumerate(failures, start=1)]

    assert [item["decision"] for item in decisions] == [
        ProgressDecision.RECOVERABLE_FAILURE,
        ProgressDecision.RECOVERABLE_FAILURE,
        ProgressDecision.RECOVERABLE_FAILURE,
    ]
    assert len(journal.failure_episodes) == 3
    assert {episode.attempts for episode in journal.failure_episodes} == {1}


def test_same_failure_episode_stalls_only_after_its_own_budget() -> None:
    journal = _journal()
    item = _execution(
        call_id="same",
        sql="SELECT missing_col FROM eval_orders",
        success=False,
        category="unknown_column",
        message="Unknown column 'missing_col' in 'field list'",
        errno=1054,
    )

    decisions = [
        _evaluate(journal, {**item, "tool_call_id": f"same-{index}"}, index)
        for index in range(1, 4)
    ]

    assert [decision["decision"] for decision in decisions] == [
        ProgressDecision.RECOVERABLE_FAILURE,
        ProgressDecision.RECOVERABLE_FAILURE,
        ProgressDecision.STALLED,
    ]
    assert len(journal.failure_episodes) == 1
    assert journal.failure_episodes[0].attempts == 3


def test_schema_discovery_preserves_failure_until_corrected_query_succeeds() -> None:
    journal = _journal()
    failure = _execution(
        call_id="bad-column",
        sql="SELECT customer_email FROM eval_customers",
        success=False,
        category="unknown_column",
        message="Unknown column 'customer_email' in 'field list'",
        errno=1054,
    )
    discovery = _execution(
        call_id="describe",
        sql="DESCRIBE eval_customers",
        success=True,
        error_class="none",
    )
    corrected = _execution(
        call_id="corrected",
        sql="SELECT email FROM eval_customers",
        success=True,
        error_class="none",
    )

    _evaluate(journal, failure, 1)
    _evaluate(journal, discovery, 2)
    assert journal.failure_episodes[0].status == "diagnosing"
    assert deterministic_completion_precheck(journal).satisfied is True

    _evaluate(journal, corrected, 3)
    assert journal.failure_episodes[0].status == "resolved"
    assert journal.metrics.recovered_failures == 1
    assert not journal.unresolved_steps()
    assert deterministic_completion_precheck(journal).satisfied is True


def test_recovery_evidence_resolves_independent_failures_in_any_order() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="bad-customer-column",
            sql="SELECT missing_customer_column FROM eval_customers",
            success=False,
            category="unknown_column",
            message="Unknown column 'missing_customer_column' in 'field list'",
            errno=1054,
        ),
        1,
    )
    _evaluate(
        journal,
        _execution(
            call_id="bad-order-column",
            sql="SELECT missing_order_column FROM eval_orders",
            success=False,
            category="unknown_column",
            message="Unknown column 'missing_order_column' in 'field list'",
            errno=1054,
        ),
        2,
    )

    _evaluate(
        journal,
        _execution(
            call_id="customer-recovery",
            sql="SELECT id FROM eval_customers",
            success=True,
            error_class="none",
        ),
        3,
    )

    customer_episode, order_episode = journal.failure_episodes
    assert customer_episode.status == "resolved"
    assert order_episode.status == "open"
    assert journal.active_failure_episode_id == order_episode.id

    _evaluate(
        journal,
        _execution(
            call_id="order-recovery",
            sql="SELECT id FROM eval_orders",
            success=True,
            error_class="none",
        ),
        4,
    )

    assert {episode.status for episode in journal.failure_episodes} == {"resolved"}
    assert journal.metrics.recovered_failures == 2
    assert journal.active_failure_episode_id is None
    assert deterministic_completion_precheck(journal).satisfied is True


def test_successful_sql_resolves_coarse_datasource_failure_target() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="unsupported-function",
            sql="SELECT UNSUPPORTED_FN(NOW())",
            success=False,
            category="execution_error",
            message="FUNCTION sample.UNSUPPORTED_FN does not exist",
            errno=1305,
        ),
        1,
    )

    assert journal.failure_episodes[0].target_object == "global"
    assert journal.failure_episodes[0].status == "open"

    _evaluate(
        journal,
        _execution(
            call_id="supported-function",
            sql="SELECT CURRENT_DATE",
            success=True,
            error_class="none",
        ),
        2,
    )

    assert journal.failure_episodes[0].status == "resolved"
    assert journal.metrics.recovered_failures == 1
    assert deterministic_completion_precheck(journal).satisfied is True


def test_unrelated_success_does_not_resolve_active_failure_episode() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="bad-column",
            sql="SELECT missing_column FROM eval_customers",
            success=False,
            category="unknown_column",
            message="Unknown column 'missing_column' in 'field list'",
            errno=1054,
            planning_goal="audit customer columns",
        ),
        1,
    )

    _evaluate(
        journal,
        _execution(
            call_id="unrelated-success",
            sql="SELECT COUNT(*) FROM eval_orders",
            success=True,
            error_class="none",
            planning_goal="count orders",
        ),
        2,
    )

    assert journal.failure_episodes[0].status == "open"
    assert journal.unresolved_steps()
    assert deterministic_completion_precheck(journal).satisfied is True


def test_llm_failure_assessment_can_supersede_non_blocking_attempt() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="optional-attempt",
            sql="SELECT missing_column FROM eval_customers",
            success=False,
            category="unknown_column",
            message="Unknown column 'missing_column' in 'field list'",
            errno=1054,
        ),
        1,
    )
    episode = journal.failure_episodes[0]
    result = parse_verification_result(
        json.dumps(
            {
                "satisfied": True,
                "reason": "The failed attempt is not required by the user's requested outcome.",
                "missing": [],
                "repair_type": "none",
                "failure_assessments": [
                    {
                        "id": episode.id,
                        "blocking": False,
                        "reason": "Alternative evidence already establishes the requested outcome.",
                        "evidence_refs": [],
                    }
                ],
            }
        )
    )

    journal.apply_failure_assessments(result)

    assert enforce_failure_episode_audit(journal, result).satisfied is True
    assert episode.status == "superseded"
    assert journal.unresolved_failure_episodes() == []


def test_failure_audit_rejects_unassessed_open_episode_without_domain_rules() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="failed-attempt",
            sql="SELECT missing_column FROM eval_customers",
            success=False,
            category="unknown_column",
            message="Unknown column 'missing_column' in 'field list'",
            errno=1054,
        ),
        1,
    )

    result = enforce_failure_episode_audit(
        journal,
        parse_verification_result(
            '{"satisfied":true,"reason":"done","missing":[],"repair_type":"none"}'
        ),
    )

    assert result.satisfied is False
    assert result.repair_type == "rewrite"
    assert journal.failure_episodes[0].id in result.missing[0]


def test_successful_retry_resolves_global_tool_argument_failure() -> None:
    journal = _journal()
    malformed_call = {
        "tool_call_id": "malformed-arguments",
        "name": "execute_sql",
        "arguments": '{"sql":"SHOW TABLES","_runtime":,}',
        "result": {
            "success": False,
            "error": "Invalid tool arguments: Expecting value",
        },
        "error_class": "argument_error",
    }

    _evaluate(journal, malformed_call, 1)
    assert journal.failure_episodes[0].target_object == "global"
    assert journal.failure_episodes[0].status == "open"

    _evaluate(
        journal,
        _execution(
            call_id="valid-retry",
            sql="SHOW TABLES",
            success=True,
            error_class="none",
        ),
        2,
    )

    assert journal.failure_episodes[0].status == "resolved"
    assert journal.metrics.recovered_failures == 1
    assert deterministic_completion_precheck(journal).satisfied is True


def test_permission_failure_waits_for_authority_instead_of_retrying() -> None:
    journal = _journal()
    decision = _evaluate(
        journal,
        _execution(
            call_id="permission",
            sql="SELECT * FROM protected_table",
            success=False,
            category="permission_denied",
            message="Access denied for user",
            error_class="execution_error",
        ),
        1,
    )

    assert decision["decision"] == ProgressDecision.AWAIT_CONFIRMATION
    assert decision["action"] == "await_confirmation"
    assert decision["reason_code"] == "authorization_required"


def test_transient_failure_has_separate_retry_budget() -> None:
    journal = _journal()
    item = _execution(
        call_id="timeout",
        sql="SELECT SLEEP(10)",
        success=False,
        category="timeout",
        message="query timed out",
        error_class="timeout_error",
    )

    decisions = [
        _evaluate(journal, {**item, "tool_call_id": f"timeout-{index}"}, index)
        for index in range(1, 5)
    ]

    assert [decision["decision"] for decision in decisions[:3]] == [
        ProgressDecision.TRANSIENT_FAILURE,
        ProgressDecision.TRANSIENT_FAILURE,
        ProgressDecision.TRANSIENT_FAILURE,
    ]
    assert decisions[3]["decision"] == ProgressDecision.STALLED


def test_journal_round_trip_preserves_failures_evidence_and_resume_count() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="bad-table",
            sql="SELECT * FROM missing",
            success=False,
            category="unknown_table",
            message="Unknown table 'missing'",
            errno=1146,
        ),
        1,
    )

    restored = TaskJournal.from_dict(journal.to_dict())

    assert restored.task_run_id == journal.task_run_id
    assert restored.failure_episodes[0].signature == journal.failure_episodes[0].signature
    assert restored.evidence[0].ref == "bad-table"
    assert restored.metrics.resumptions == 1
    assert "failed strategy" in restored.context_block()


def test_verification_retry_budget_resets_when_new_evidence_arrives() -> None:
    journal = _journal()
    _evaluate(
        journal,
        _execution(
            call_id="initial-evidence",
            sql="SELECT COUNT(*) FROM eval_orders",
            success=True,
            error_class="none",
        ),
        1,
    )

    assert journal.record_verification_outcome(satisfied=False) == 0
    assert journal.record_verification_outcome(satisfied=False) == 1

    _evaluate(
        journal,
        _execution(
            call_id="gap-evidence",
            sql="SELECT SUM(total_amount) FROM eval_orders",
            success=True,
            error_class="none",
        ),
        2,
    )

    assert journal.record_verification_outcome(satisfied=False) == 0
    assert journal.metrics.verification_attempts == 0
    restored = TaskJournal.from_dict(journal.to_dict())
    assert restored.metrics.last_verification_evidence_count == 2
    assert restored.metrics.verification_no_progress_rounds == 0


def test_task_state_v0_migrates_and_resume_correction_is_preserved() -> None:
    migrated = migrate_task_state(
        {
            "task_run_id": "legacy-task",
            "objective": "audit all tables",
            "status": "checkpointed",
            "failures": [],
        }
    )
    restored = TaskJournal.from_dict(migrated)
    restored.apply_user_correction(
        [{"role": "user", "content": "继续执行，但不要检查 payments 表"}]
    )

    assert migrated["version"] == 2
    assert restored.contract.objective == "audit all tables"
    assert restored.user_corrections == ["继续执行，但不要检查 payments 表"]
    assert "payments" in restored.context_block()


@pytest.mark.parametrize(
    ("text", "satisfied", "malformed"),
    [
        ('{"satisfied": true, "reason": "ok", "missing": []}', True, False),
        ('```json\n{"satisfied": false, "missing": ["step 2"]}\n```', False, False),
        ("looks good", False, True),
    ],
)
def test_verification_parser_is_conservative(text: str, satisfied: bool, malformed: bool) -> None:
    result = parse_verification_result(text)

    assert result.satisfied is satisfied
    assert result.malformed is malformed
