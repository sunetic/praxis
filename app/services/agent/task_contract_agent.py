"""LLM-driven task contract construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import fmt_kv, get_logger
from app.services.agent.task_contract import AcceptanceCriterion, TaskContract, latest_user_text
from app.services.platform.prompt_loader import PromptLoader

logger = get_logger("agent.task_contract")


@dataclass(frozen=True)
class TaskContractBuild:
    contract: TaskContract
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    source: Literal["llm", "fallback", "disabled"] = "llm"
    error_code: str | None = None


class TaskContractBuilder(Protocol):
    async def build(self, messages: list[dict[str, Any]]) -> TaskContractBuild: ...


class _CriterionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1)
    required: bool
    requires_tool_evidence: bool
    required_tool_outcome: Literal["any", "success", "failure"]
    component_hints: list[str]
    source_excerpt: str = Field(min_length=1)


class _GroundedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1)
    source_excerpt: str = Field(min_length=1)


class _ContractDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    constraints: list[_GroundedRequirement]
    acceptance_criteria: list[_CriterionDecision]
    output_requirements: list[_GroundedRequirement]
    complex: bool
    high_value: bool


class TaskContractAgent:
    """Interpret task semantics with the configured model, without keyword routing."""

    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def build(self, messages: list[dict[str, Any]]) -> TaskContractBuild:
        objective = latest_user_text(messages).strip()
        input_tokens = 0
        output_tokens = 0
        llm_calls = 0
        try:
            content, used_input, used_output = await self._request_contract(
                objective,
                PromptLoader.render("agent/prompts/task_contract.tpl"),
            )
            llm_calls += 1
            input_tokens += used_input
            output_tokens += used_output
            contract = _contract_from_response(objective, content)
            if contract.complex and not contract.acceptance_criteria:
                try:
                    repair_prompt = PromptLoader.render(
                        "agent/prompts/task_contract_repair.tpl",
                        first_contract_json=json.dumps(
                            contract.to_dict(),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    repaired_content, used_input, used_output = await self._request_contract(
                        objective,
                        repair_prompt,
                    )
                    llm_calls += 1
                    input_tokens += used_input
                    output_tokens += used_output
                    repaired = _contract_from_response(objective, repaired_content)
                    if repaired.acceptance_criteria:
                        contract = repaired
                    else:
                        logger.warning("task_contract_repair_empty")
                except Exception as repair_exc:
                    llm_calls += 1
                    logger.warning(
                        "task_contract_repair_degraded %s",
                        fmt_kv(error_code=_error_code(repair_exc)),
                    )
            logger.info(
                "task_contract_build_succeeded %s",
                fmt_kv(
                    complex=contract.complex,
                    high_value=contract.high_value,
                    criteria_count=len(contract.acceptance_criteria),
                ),
            )
            return TaskContractBuild(
                contract=contract,
                llm_calls=llm_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as exc:
            error_code = _error_code(exc)
            logger.warning("task_contract_build_degraded %s", fmt_kv(error_code=error_code))
            return TaskContractBuild(
                contract=TaskContract.unclassified(messages, conservative=True),
                llm_calls=max(1, llm_calls),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                source="fallback",
                error_code=error_code,
            )

    async def _request_contract(
        self,
        objective: str,
        system_prompt: str,
    ) -> tuple[str, int, int]:
        response: dict[str, Any] | None = None
        async for chunk in self._llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": objective},
            ],
            tools=None,
            stream=False,
            temperature=0,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            raise ValueError("empty_response")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        return _response_content(response), input_tokens, output_tokens


def _response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("missing_choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("missing_message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("missing_content")
    return content


def _contract_from_response(objective: str, content: str) -> TaskContract:
    payload = _parse_json_object(content)
    decision = _ContractDecision.model_validate(payload)
    return TaskContract(
        objective=objective,
        constraints=_grounded_requirement_texts(objective, decision.constraints),
        acceptance_criteria=[
            AcceptanceCriterion(
                id=f"ac-{index}",
                description=item.description.strip(),
                required=item.required,
                requires_tool_evidence=item.requires_tool_evidence,
                required_tool_outcome=item.required_tool_outcome,
                component_hints=_dedupe_strings(item.component_hints),
                source_excerpt=_require_grounded_excerpt(objective, item.source_excerpt),
            )
            for index, item in enumerate(decision.acceptance_criteria, start=1)
        ],
        output_requirements=_grounded_requirement_texts(objective, decision.output_requirements),
        complex=decision.complex,
        high_value=decision.high_value,
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("invalid_json") from None
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_root_type")
    return payload


def _dedupe_strings(values: list[str]) -> list[str]:
    normalized = [item.strip() for item in values if item.strip()]
    return list(dict.fromkeys(normalized))


def _grounded_requirement_texts(
    objective: str,
    values: list[_GroundedRequirement],
) -> list[str]:
    grounded = [
        item.text.strip()
        for item in values
        if _require_grounded_excerpt(objective, item.source_excerpt)
    ]
    return _dedupe_strings(grounded)


def _require_grounded_excerpt(objective: str, excerpt: str) -> str:
    grounded = excerpt.strip()
    if not grounded or grounded not in objective:
        raise ValueError("invalid_contract_provenance")
    return grounded


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "invalid_contract_schema"
    if isinstance(exc, ValueError):
        return str(exc) or "invalid_contract_response"
    return "provider_error"
