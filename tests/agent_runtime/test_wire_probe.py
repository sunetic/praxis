import json
import sqlite3

from app.services.agent.models import ModelConnectionConfig
from tools.agent_runtime_wire_probe import candidate_config, conversation_payloads, wire_summary


def test_wire_observer_accepts_both_sse_spacing_forms_without_relabeling_content():
    body = "\n".join(
        [
            'data:{"choices":[{"delta":{"content":"self talk </think>"}}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"actual reasoning"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data:[DONE]",
        ]
    )
    facts = wire_summary(body)
    assert facts["content"] == "self talk </think>"
    assert facts["reasoning"] == "actual reasoning"
    assert facts["finish_reasons"] == ["stop"]


def test_candidate_model_is_an_explicit_probe_only_change_without_other_overrides():
    original = ModelConnectionConfig(
        model_name="source",
        base_url="https://example.invalid/v1",
        api_key="test-secret",
        temperature=0.3,
    )
    assert candidate_config(original, None) is original
    candidate = candidate_config(original, "candidate")
    assert original.model_name == "source"
    assert candidate.model_name == "candidate"
    assert candidate.model_dump(exclude={"model_name"}) == original.model_dump(
        exclude={"model_name"}
    )
    assert candidate.api_key == original.api_key
    assert "test-secret" not in candidate.model_dump_json()


def test_wire_probe_loads_prior_run_messages_in_conversation_order():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        create table agent_runs(id text primary key, conversation_id text, seq integer);
        create table agent_messages(run_id text, "index" integer, payload text);
        """
    )
    db.executemany(
        "insert into agent_runs values(?,?,?)",
        [("other", "other", 1), ("first", "target", 1), ("second", "target", 2)],
    )
    for run_id, index, marker in (
        ("other", 0, "ignore"),
        ("first", 1, "first-1"),
        ("first", 0, "first-0"),
        ("second", 0, "second-0"),
    ):
        db.execute(
            "insert into agent_messages values(?,?,?)",
            (run_id, index, json.dumps({"marker": marker})),
        )
    assert conversation_payloads(db, "target", 2) == [
        {"marker": "first-0"},
        {"marker": "first-1"},
        {"marker": "second-0"},
    ]
