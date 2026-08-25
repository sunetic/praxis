from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.agent.task_contract_agent import TaskContractAgent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ContractLLM:
    def __init__(self, response: dict[str, Any] | None = None, *, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {"messages": messages, "tools": tools, "stream": stream, "kwargs": kwargs}
        )
        if self.error is not None:
            raise self.error
        yield {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.response, ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 31, "completion_tokens": 17},
        }


class SequentialContractLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {"messages": messages, "tools": tools, "stream": stream, "kwargs": kwargs}
        )
        response = self.responses[len(self.calls) - 1]
        yield {
            "choices": [
                {
                    "message": {"content": json.dumps(response, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 31, "completion_tokens": 17},
        }


def _decision(*, complex: bool, high_value: bool = False) -> dict[str, Any]:
    return {
        "constraints": [
            {"text": "Keep the source unchanged", "source_excerpt": "Please handle Z."}
        ],
        "acceptance_criteria": [
            {
                "description": "Establish the requested outcome",
                "required": True,
                "requires_tool_evidence": True,
                "required_tool_outcome": "success",
                "component_hints": ["Observed result", "Recorded evidence"],
                "source_excerpt": "Please handle Z.",
            }
        ],
        "output_requirements": [
            {"text": "Provide a concise result", "source_excerpt": "Please handle Z."}
        ],
        "complex": complex,
        "high_value": high_value,
    }


@pytest.mark.anyio
async def test_contract_semantics_come_from_model_decision_and_template() -> None:
    llm = ContractLLM(_decision(complex=True, high_value=True))

    result = await TaskContractAgent(llm).build([{"role": "user", "content": "Please handle Z."}])
    contract = result.contract

    assert result.llm_calls == 1
    assert result.input_tokens == 31
    assert result.output_tokens == 17
    assert result.source == "llm"
    assert contract.objective == "Please handle Z."
    assert contract.complex is True
    assert contract.high_value is True
    assert contract.constraints == ["Keep the source unchanged"]
    assert contract.output_requirements == ["Provide a concise result"]
    assert contract.acceptance_criteria[0].id == "ac-1"
    assert contract.acceptance_criteria[0].requires_tool_evidence is True
    assert contract.acceptance_criteria[0].required_tool_outcome == "success"
    assert contract.acceptance_criteria[0].component_hints == [
        "Observed result",
        "Recorded evidence",
    ]
    call = llm.calls[0]
    assert call["stream"] is False
    assert call["kwargs"]["response_format"] == {"type": "json_object"}
    assert "text length" in call["messages"][0]["content"]
    assert call["messages"][1] == {"role": "user", "content": "Please handle Z."}


@pytest.mark.anyio
async def test_complex_empty_contract_gets_one_grounded_llm_repair() -> None:
    first = _decision(complex=True)
    first["acceptance_criteria"] = []
    repaired = _decision(complex=True)
    llm = SequentialContractLLM([first, repaired])

    result = await TaskContractAgent(llm).build(
        [{"role": "user", "content": "Please handle Z."}]
    )

    assert result.source == "llm"
    assert result.llm_calls == 2
    assert result.input_tokens == 62
    assert result.output_tokens == 34
    assert len(result.contract.acceptance_criteria) == 1
    repair_prompt = llm.calls[1]["messages"][0]["content"]
    assert "classified this as a complex task" in repair_prompt
    assert "no independently" in repair_prompt


@pytest.mark.anyio
async def test_request_length_does_not_override_model_decision() -> None:
    request = "context " * 200
    decision = _decision(complex=False)
    decision["constraints"][0]["source_excerpt"] = "context"
    decision["acceptance_criteria"][0]["source_excerpt"] = "context"
    decision["output_requirements"][0]["source_excerpt"] = "context"
    llm = ContractLLM(decision)

    contract = (await TaskContractAgent(llm).build([{"role": "user", "content": request}])).contract

    assert contract.complex is False


@pytest.mark.anyio
async def test_contract_agent_uses_observable_conservative_fallback() -> None:
    llm = ContractLLM(error=RuntimeError("provider unavailable"))

    result = await TaskContractAgent(llm).build(
        [{"role": "user", "content": "Preserve this exact objective"}]
    )

    assert result.llm_calls == 1
    assert result.source == "fallback"
    assert result.error_code == "provider_error"
    assert result.contract.objective == "Preserve this exact objective"
    assert result.contract.complex is True
    assert result.contract.high_value is False
    assert result.contract.acceptance_criteria == []


@pytest.mark.anyio
async def test_invalid_structured_decision_cannot_silently_coerce_types() -> None:
    invalid = _decision(complex=False)
    invalid["complex"] = "false"
    llm = ContractLLM(invalid)

    result = await TaskContractAgent(llm).build([{"role": "user", "content": "A request"}])

    assert result.source == "fallback"
    assert result.error_code == "invalid_contract_schema"
    assert result.contract.complex is True
    assert result.contract.acceptance_criteria == []


@pytest.mark.anyio
async def test_contract_items_must_be_grounded_in_exact_user_text() -> None:
    decision = _decision(complex=True)
    decision["acceptance_criteria"][0]["source_excerpt"] = "invented requirement"
    llm = ContractLLM(decision)

    result = await TaskContractAgent(llm).build([{"role": "user", "content": "Please handle Z."}])

    assert result.source == "fallback"
    assert result.error_code == "invalid_contract_provenance"
    assert result.contract.acceptance_criteria == []
