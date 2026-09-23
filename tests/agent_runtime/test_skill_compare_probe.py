"""Offline report invariants, not model quality assertions."""

from copy import deepcopy

from tools.agent_runtime_skill_compare import outcome, request_parity


def turns():
    first = {
        "content": {
            "name": "harness-review",
            "database": "postgresql",
            "version": "1.0.0",
            "always_apply": False,
            "prompt": "真实日志分析",
            "description": "说明",
        },
        "revision": "first",
    }
    second = {"content": {**first["content"], "database": "mysql"}, "revision": "second"}
    return [{"status": "finished", "draft": deepcopy(draft)} for draft in (first, second, second)]


def test_report_requires_actual_field_update_and_unchanged_explanation_revision():
    actual = turns()
    assert all(outcome(actual).values())
    actual[1]["draft"] = deepcopy(actual[0]["draft"])
    actual[1]["visible_text"] = "已经改成 MySQL。"
    assert not outcome(actual)["only_database_changed"]
    actual = turns()
    actual[2]["draft"]["revision"] = "unrequested-save"
    assert not outcome(actual)["explanation_did_not_edit"]


def test_partial_failed_runs_are_not_complete_even_with_a_draft():
    assert outcome(turns()[:2]) == {"complete": False}
    actual = turns()
    actual[1]["status"] = "failed"
    assert outcome(actual) == {"complete": False}
    actual = turns()
    actual[1]["error_type"] = "TypeError"
    assert outcome(actual) == {"complete": False}


def test_parity_ignores_only_new_resource_identity_not_instructions_or_tools():
    samples = [
        {
            "sample": 1,
            "mode": mode,
            "draft_id": draft_id,
            "turns": [
                {
                    "http": [
                        {
                            "request": {
                                "messages": [
                                    {"role": "system", "content": f"Authorized draft: {draft_id}"}
                                ],
                                "tools": [{"function": {"name": "skill_draft_write"}}],
                                "max_completion_tokens": 8192,
                            }
                        }
                    ]
                }
            ],
        }
        for mode, draft_id in (("minimal", "a" * 32), ("http", "b" * 32))
    ]
    assert request_parity(samples)[0]["first_requests_equal"]
    samples[1]["turns"][0]["http"][0]["request"]["messages"][0]["content"] += " extra instruction"
    result = request_parity(samples)[0]
    assert not result["first_requests_equal"]
    assert result["different_fields"] == ["messages"]
