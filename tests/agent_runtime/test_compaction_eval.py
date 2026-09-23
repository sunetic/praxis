"""Evaluation wiring contract; synthetic answers do not demonstrate recall quality."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from test_models import config

from app.services.agent.models import ModelFactory
from evals.context_compaction.run import load_scenarios, run_scenario


@pytest.mark.parametrize("summary_has_facts", [True, False])
async def test_native_eval_uses_real_projection_and_does_not_grade_only_answers(
    monkeypatch,
    summary_has_facts,
):
    scenario = load_scenarios()[0]
    expected = " ".join(
        group[0] for question in scenario["questions"] for group in question["expected_groups"]
    )
    calls = {"baseline": 0, "summary": 0, "answer": 0}

    def direct(messages, info):
        if len(messages[-1].parts) == 1:
            calls["baseline"] += 1
            return ModelResponse(parts=[TextPart(expected)])
        calls["summary"] += 1
        material = json.loads(messages[-1].parts[-2].content)
        facts = " ".join(scenario["required_memory_terms"]) if summary_has_facts else "没有提供事实"
        return ModelResponse(
            parts=[TextPart(f"[m{material[0]['message_index']}] {facts}")], finish_reason="stop"
        )

    async def stream(messages, info):
        calls["answer"] += 1
        yield expected

    factory = ModelFactory(
        lambda: config(
            context_window_tokens=8192,
            context_compression_threshold_percent=50,
            max_output_tokens=1024,
            summary_output_tokens=2048,
        )
    )
    monkeypatch.setattr(
        factory, "get_model", AsyncMock(return_value=FunctionModel(direct, stream_function=stream))
    )
    try:
        result = await run_scenario(scenario, factory)
    finally:
        await factory.close()
    assert calls == {"baseline": 3, "summary": 3, "answer": 3}
    assert result["metrics"]["compacted_recall"] == 1
    assert result["passed"] is summary_has_facts
    for question in result["questions"]:
        assert question["compaction_executed"]
        assert question["run_status"] == "finished"
        assert question["snapshots"]
        assert question["memory_checks"]["passed"] is summary_has_facts
