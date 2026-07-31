from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.sql.sqltypes import BIGINT, BOOLEAN, INTEGER, JSON, NUMERIC, TEXT, VARCHAR

from app.core.logging import fmt_kv, get_logger
from app.models import models as db_models
from app.services.llm import LLMClient, get_llm_client
from app.services.platform.prompt_loader import PromptLoader

logger = get_logger("builder.runtime")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_sqlalchemy_type(type_obj: Any) -> str:
    if isinstance(type_obj, (INTEGER, BIGINT)):
        return "integer"
    if isinstance(type_obj, NUMERIC):
        return "number"
    if isinstance(type_obj, BOOLEAN):
        return "boolean"
    if isinstance(type_obj, (VARCHAR, TEXT)):
        return "string"
    if isinstance(type_obj, JSON):
        return "object"
    return str(type_obj).lower()


@dataclass(frozen=True)
class FunctionBuildResult:
    draft_code: str
    draft_dependencies: dict[str, Any]
    summary: str


@dataclass(frozen=True)
class FunctionBuildRunEvent:
    phase: str
    status: str
    summary: str
    created_at: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class FunctionBuildRunResult:
    run_id: str
    status: str
    phase: str
    summary: str
    draft_code: str
    draft_dependencies: dict[str, Any]
    events: list[FunctionBuildRunEvent]
    error_summary: str | None = None


class FunctionBuilderService:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or get_llm_client()

    def build_run(
        self,
        *,
        current_code: str | None,
        current_dependencies: dict[str, Any] | None,
        prompt: str,
        function_name: str,
        ambiguity_mode: str = "clarify",
    ) -> FunctionBuildRunResult:
        normalized_prompt = _compact_whitespace(prompt)
        run_id = f"fbr_{uuid4().hex[:16]}"
        events: list[FunctionBuildRunEvent] = []
        run_started_at = time.perf_counter()
        last_elapsed_ms = 0
        fallback_code = str(current_code or "")
        fallback_dependencies = (
            copy.deepcopy(current_dependencies) if isinstance(current_dependencies, dict) else {}
        )

        def push_event(
            phase: str,
            *,
            status: str = "running",
            summary: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            nonlocal last_elapsed_ms
            elapsed_ms = int((time.perf_counter() - run_started_at) * 1000)
            phase_duration_ms = elapsed_ms - last_elapsed_ms
            last_elapsed_ms = elapsed_ms
            combined_payload = {"elapsed_ms": elapsed_ms, "phase_duration_ms": phase_duration_ms}
            if isinstance(payload, dict):
                combined_payload.update(payload)
            events.append(
                FunctionBuildRunEvent(
                    phase=phase,
                    status=status,
                    summary=summary,
                    created_at=_utc_now_iso(),
                    payload=combined_payload,
                )
            )

        ambiguities = self._collect_ambiguities(normalized_prompt)
        push_event(
            "intent_parsed",
            summary="Function requirement parsed.",
            payload={"ambiguity_count": len(ambiguities)},
        )

        try:
            spec = self._normalize_spec(
                current_dependencies,
                function_name=function_name,
                current_code=current_code,
            )
            evidence_pack = self._build_evidence_pack(
                prompt=normalized_prompt,
                spec=spec,
                current_code=current_code,
            )
            generated = self._generate_function_spec_with_retry(
                prompt=normalized_prompt,
                current_spec=self._project_spec_for_prompt(spec),
                ambiguities=ambiguities,
                evidence_pack=evidence_pack,
            )
            self._merge_generated_spec(spec, generated, prompt=normalized_prompt)
            spec["meta"]["updated_at"] = _utc_now_iso()
            spec["meta"]["last_prompt"] = normalized_prompt

            raw_clarification_questions = [
                str(item).strip()
                for item in (generated.get("clarification_questions") or [])
                if str(item).strip()
            ]
            clarification_questions = self._material_clarification_questions(
                questions=raw_clarification_questions,
                evidence_pack=evidence_pack,
                ambiguities=ambiguities,
            )
            default_strategy = [
                str(item).strip()
                for item in (generated.get("default_strategy") or [])
                if str(item).strip()
            ]
            missing_information = [
                str(item).strip()
                for item in (generated.get("missing_information") or [])
                if str(item).strip()
            ]
            assumptions = [
                str(item).strip()
                for item in (generated.get("assumptions") or [])
                if str(item).strip()
            ]
            verification_checks = [
                str(item).strip()
                for item in (generated.get("verification_checks") or [])
                if str(item).strip()
            ]
            follow_up_message = str(generated.get("follow_up_message") or "").strip()
            if ambiguities or clarification_questions or default_strategy:
                push_event(
                    "clarification",
                    status="noted",
                    summary="Ambiguity detected; LLM proceeded with defaults and provided clarification hints.",
                    payload={
                        "ambiguity_codes": [item["code"] for item in ambiguities],
                        "questions": clarification_questions[:3],
                        "default_strategy": default_strategy[:3],
                        "missing_information": missing_information,
                        "assumptions": assumptions,
                        "verification_checks": verification_checks,
                        "follow_up_message": follow_up_message,
                    },
                )
            push_event(
                "draft_built",
                summary=str(spec["meta"].get("summary") or "Function draft generated."),
                payload={
                    "output_field_count": len(spec.get("output_fields") or []),
                    "uses_db": bool(spec.get("uses_db")),
                },
            )
            draft_code = self._render_code(spec)
            dependencies = {"builder_spec": spec}
            push_event(
                "verified",
                status="done",
                summary="Function draft verification passed.",
                payload={"code_length": len(draft_code)},
            )
            return FunctionBuildRunResult(
                run_id=run_id,
                status="done",
                phase="verified",
                summary=str(
                    spec["meta"].get("summary")
                    or spec.get("summary")
                    or "Function draft generated."
                ),
                draft_code=draft_code,
                draft_dependencies=dependencies,
                events=events,
            )
        except Exception as err:
            push_event(
                "failed",
                status="failed",
                summary=f"Function build failed: {err}",
                payload={"error_type": err.__class__.__name__},
            )
            logger.exception("function_build_run_failed %s", fmt_kv(run_id=run_id))
            return FunctionBuildRunResult(
                run_id=run_id,
                status="failed",
                phase="failed",
                summary="Function build failed",
                draft_code=fallback_code,
                draft_dependencies=fallback_dependencies,
                events=events,
                error_summary=str(err),
            )

    def apply_prompt(
        self,
        *,
        current_code: str | None,
        current_dependencies: dict[str, Any] | None,
        prompt: str,
        function_name: str,
    ) -> FunctionBuildResult:
        run = self.build_run(
            current_code=current_code,
            current_dependencies=current_dependencies,
            prompt=prompt,
            function_name=function_name,
            ambiguity_mode="default",
        )
        return FunctionBuildResult(
            draft_code=run.draft_code,
            draft_dependencies=run.draft_dependencies,
            summary=run.summary,
        )

    def suggest_invocation_input(
        self,
        *,
        prompt: str,
        function_name: str,
        current_dependencies: dict[str, Any] | None,
        current_code: str | None,
        conversation_context: str | None = None,
    ) -> dict[str, Any]:
        spec = self._normalize_spec(
            current_dependencies,
            function_name=function_name,
            current_code=current_code,
        )
        evidence_pack = self._build_evidence_pack(
            prompt=prompt,
            spec=spec,
            current_code=current_code,
        )
        payload = {
            "user_prompt": _compact_whitespace(prompt),
            "conversation_context": _compact_whitespace(conversation_context or ""),
            "function_spec": self._project_spec_for_prompt(spec),
            "function_definition": self._build_function_definition(spec),
            "retrieved_evidence": evidence_pack,
            "schema": {
                "payload": "object",
                "rationale": "string",
                "missing_information": ["string"],
                "assumptions": ["string"],
            },
            "constraints": [
                "Return JSON only.",
                "payload must be directly invokable test input.",
                "Use parameter names defined in function_spec.input_contract exactly.",
                "Prefer snake_case parameter names from input_contract; do not invent new naming styles.",
                "Never add keys outside function_spec.input_contract.",
                "If required information is unknown, keep payload fields but use explicit placeholder values and list missing_information.",
                "Do not ask for parameter types that are already defined in retrieved_evidence.",
                "rationale must use natural Chinese, concise and direct (prefer <= 60 Chinese chars), include: generated input summary + next step.",
                "Do not repeat the user prompt in rationale.",
                "Avoid meta narration; speak as a direct collaborator.",
            ],
        }
        messages = [
            {
                "role": "system",
                "content": PromptLoader.render(
                    "function/prompts/invocation_suggest.tpl",
                    tools_block="",
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = self._run_async_safely(self._call_llm_for_json(messages=messages))
            parsed = self._parse_json_patch(raw)
            payload_obj = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
            contract_fields = [
                item for item in (spec.get("input_contract") or []) if isinstance(item, dict)
            ]
            contract_names = {
                str(item.get("name") or "").strip()
                for item in contract_fields
                if str(item.get("name") or "").strip()
            }
            required_names = {
                str(item.get("name") or "").strip()
                for item in contract_fields
                if str(item.get("name") or "").strip() and bool(item.get("required"))
            }
            invalid_keys = sorted(
                str(key) for key in payload_obj.keys() if str(key) not in contract_names
            )
            missing_required = sorted(name for name in required_names if name not in payload_obj)
            protocol_missing: list[str] = []
            if invalid_keys:
                protocol_missing.append(
                    "payload contains undeclared fields: " + ", ".join(invalid_keys)
                )
            if missing_required:
                protocol_missing.append(
                    "payload is missing required fields: " + ", ".join(missing_required)
                )
            return {
                "payload": payload_obj,
                "rationale": str(parsed.get("rationale") or "").strip() or "Test input generated.",
                "missing_information": [
                    str(item).strip()
                    for item in (parsed.get("missing_information") or [])
                    if str(item).strip()
                ]
                + protocol_missing,
                "assumptions": [
                    str(item).strip()
                    for item in (parsed.get("assumptions") or [])
                    if str(item).strip()
                ],
            }
        except Exception:
            return {
                "payload": {"rows": [1, 2, 3]},
                "rationale": "Provided minimal runnable input sample; adjust as needed before execution.",
                "missing_information": [],
                "assumptions": [
                    "No specific business context provided; using generic sample input."
                ],
            }

    def _generate_function_spec_with_retry(
        self,
        *,
        prompt: str,
        current_spec: dict[str, Any],
        ambiguities: list[dict[str, str]],
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        first = self._normalize_generated_spec_payload(
            self._generate_function_spec_payload(
                prompt=prompt,
                current_spec=current_spec,
                ambiguities=ambiguities,
                evidence_pack=evidence_pack,
                previous_error=None,
            )
        )
        try:
            self._validate_generated_spec_payload(first)
            return first
        except Exception as err:
            retry = self._normalize_generated_spec_payload(
                self._generate_function_spec_payload(
                    prompt=prompt,
                    current_spec=current_spec,
                    ambiguities=ambiguities,
                    evidence_pack=evidence_pack,
                    previous_error=str(err),
                )
            )
            self._validate_generated_spec_payload(retry)
            return retry

    def _generate_function_spec_payload(
        self,
        *,
        prompt: str,
        current_spec: dict[str, Any],
        ambiguities: list[dict[str, str]],
        evidence_pack: dict[str, Any],
        previous_error: str | None,
    ) -> dict[str, Any]:
        payload = {
            "user_prompt": prompt,
            "current_function_spec": current_spec,
            "retrieved_evidence": evidence_pack,
            "ambiguity_hints": ambiguities,
            "collection_checklist": [
                "function_purpose",
                "input_contract",
                "output_contract",
                "datasource_scope",
                "error_semantics",
                "verification_expectation",
            ],
            "schema": {
                "intent_summary": "string",
                "plan": {"goal": "string", "todos": ["string"]},
                "uses_db": "boolean",
                "sql": "string",
                "input_contract": [
                    {
                        "name": "string",
                        "type": "string|integer|number|boolean|array|object",
                        "required": "boolean",
                        "description": "string",
                    }
                ],
                "output_fields": [
                    {
                        "name": "string",
                        "kind": "constant|context|payload_len",
                        "value": "any",
                        "path": "string",
                        "key": "string",
                    }
                ],
                "clarification_questions": ["string"],
                "default_strategy": ["string"],
                "missing_information": ["string"],
                "assumptions": ["string"],
                "verification_checks": ["string"],
                "follow_up_message": "string",
            },
            "constraints": [
                "You must respond with JSON only.",
                "Use Plan -> Act -> Reflect style outputs in fields: plan / output_fields / intent_summary.",
                "When information is insufficient to define executable behavior, ask targeted clarification_questions and provide a direct follow_up_message for the user.",
                "Before generating output, collect enough information for purpose/input/output/error handling verification.",
                "Use retrieved_evidence as first-class facts; do not ask clarification for facts already present there.",
                "Clarification is allowed only for evidence gaps that materially change behavior.",
                "If information is missing, include high-signal follow-up questions in clarification_questions and list items in missing_information.",
                "Prefer concise, concrete questions that can be answered by product users (not engineers only).",
                "Do not fabricate domain specifics when information is missing; ask instead.",
                "If user asks to proceed directly (e.g. '直接生成', '都不需要', '按默认'), proceed with explicit assumptions and provide a concise follow_up_message that states what is assumed.",
                "At most one clarification question in a single turn.",
                "When default strategy is applied, explicitly describe it in default_strategy and record assumptions.",
                "verification_checks must be executable acceptance checks (1 success + 1 failure minimum).",
                "follow_up_message must be a direct user-facing natural Chinese chat message, concise and direct (prefer <= 80 Chinese chars), and should be non-empty whenever clarification_questions, missing_information, or assumptions exist.",
                "Do not paraphrase user intent in a meta tone like '用户希望...' or '用户当前...'; speak as a direct collaborator.",
                "Do not repeat the user prompt in follow_up_message.",
                "input_contract must declare each input parameter with name/type/required/description, and include enum/range/default when those constraints exist.",
                "When runtime capability contracts declare enums, ranges, defaults, or nested object schemas, you must reuse those exact canonical fields and must not invent aliases or undeclared params.",
                "Ensure output_fields are executable by runtime renderer and keep existing useful fields.",
                "Datasource usage must rely on FunctionBase helper get_session_by_id(datasource_id).",
                "Do not design or request datasource credentials/host/user/password in function spec or follow-up.",
                "Use SQLAlchemy session style: session.execute(text(sql), params).mappings().all().",
                "Rows are mapping/dict-style objects; never access row values with positional indexes like row[0].",
            ],
            "previous_error": previous_error,
        }
        messages = [
            {
                "role": "system",
                "content": PromptLoader.render(
                    "function/prompts/spec_builder.tpl",
                    tools_block="",
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = self._run_async_safely(self._call_llm_for_json(messages=messages))
        return self._parse_json_patch(raw)

    def _normalize_generated_spec_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(payload) if isinstance(payload, dict) else {}
        normalized["intent_summary"] = str(normalized.get("intent_summary") or "").strip()
        plan = normalized.get("plan") if isinstance(normalized.get("plan"), dict) else {}
        todos = plan.get("todos")
        normalized["plan"] = {
            "goal": str(plan.get("goal") or normalized["intent_summary"] or "").strip(),
            "todos": [str(item).strip() for item in todos if str(item).strip()]
            if isinstance(todos, list)
            else [],
        }
        normalized["uses_db"] = bool(normalized.get("uses_db"))
        normalized["sql"] = str(normalized.get("sql") or "").strip()
        raw_input_contract = normalized.get("input_contract")
        normalized_input_contract: list[dict[str, Any]] = []
        if isinstance(raw_input_contract, list):
            for field in raw_input_contract:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "").strip()
                if not name:
                    continue
                field_type = str(field.get("type") or "string").strip().lower()
                if field_type not in {"string", "integer", "number", "boolean", "array", "object"}:
                    field_type = "string"
                normalized_input_contract.append(
                    {
                        "name": name,
                        "type": field_type,
                        "required": bool(field.get("required")),
                        "description": str(field.get("description") or "").strip(),
                        "enum": [str(item) for item in field.get("enum", []) if str(item).strip()]
                        if isinstance(field.get("enum"), list)
                        else [],
                        "minimum": field.get("minimum")
                        if isinstance(field.get("minimum"), (int, float))
                        else None,
                        "maximum": field.get("maximum")
                        if isinstance(field.get("maximum"), (int, float))
                        else None,
                        "default": field.get("default"),
                    }
                )
        normalized["input_contract"] = normalized_input_contract
        raw_fields = normalized.get("output_fields")
        normalized_fields: list[dict[str, Any]] = []
        if isinstance(raw_fields, list):
            for field in raw_fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "").strip()
                if not name:
                    continue
                kind = str(field.get("kind") or "constant").strip()
                if kind not in {"constant", "context", "payload_len"}:
                    kind = "constant"
                normalized_fields.append(
                    {
                        "name": name,
                        "kind": kind,
                        "value": field.get("value"),
                        "path": str(field.get("path") or "").strip(),
                        "key": str(field.get("key") or "").strip(),
                    }
                )
        normalized["output_fields"] = normalized_fields
        normalized["clarification_questions"] = [
            str(item).strip()
            for item in (normalized.get("clarification_questions") or [])
            if str(item).strip()
        ]
        normalized["default_strategy"] = [
            str(item).strip()
            for item in (normalized.get("default_strategy") or [])
            if str(item).strip()
        ]
        normalized["missing_information"] = [
            str(item).strip()
            for item in (normalized.get("missing_information") or [])
            if str(item).strip()
        ]
        normalized["assumptions"] = [
            str(item).strip() for item in (normalized.get("assumptions") or []) if str(item).strip()
        ]
        normalized["verification_checks"] = [
            str(item).strip()
            for item in (normalized.get("verification_checks") or [])
            if str(item).strip()
        ]
        normalized["follow_up_message"] = str(normalized.get("follow_up_message") or "").strip()
        return normalized

    def _validate_generated_spec_payload(self, payload: dict[str, Any]) -> None:
        if not str(payload.get("intent_summary") or "").strip():
            raise ValueError("function.intent_summary must not be empty")
        if not isinstance(payload.get("plan"), dict):
            raise ValueError("function.plan must be an object")
        if not str((payload.get("plan") or {}).get("goal") or "").strip():
            raise ValueError("function.plan.goal must not be empty")
        fields = payload.get("output_fields")
        if not isinstance(fields, list):
            raise ValueError("function.output_fields must be an array")

    def _merge_generated_spec(
        self, spec: dict[str, Any], generated: dict[str, Any], *, prompt: str
    ) -> None:
        # Reset volatile intent tags on each turn to avoid stale domain bias
        # leaking from previous builds (e.g. list_databases from older prompts).
        spec["meta"]["intent_tags"] = []
        generated_input_contract = generated.get("input_contract")
        if isinstance(generated_input_contract, list):
            spec["input_contract"] = [
                copy.deepcopy(item) for item in generated_input_contract if isinstance(item, dict)
            ]
        for field in generated.get("output_fields") or []:
            if not isinstance(field, dict):
                continue
            normalized = {"name": field.get("name"), "kind": field.get("kind")}
            if field.get("kind") == "context":
                normalized["path"] = field.get("path") or "trace_id"
            elif field.get("kind") == "payload_len":
                normalized["key"] = field.get("key") or "rows"
            else:
                normalized["value"] = field.get("value")
            self._ensure_output_field(spec, normalized)
        if not any(field.get("name") == "ok" for field in spec["output_fields"]):
            spec["output_fields"].insert(0, {"name": "ok", "kind": "constant", "value": True})
        uses_db = bool(generated.get("uses_db"))
        spec["uses_db"] = uses_db
        if uses_db:
            sql = str(generated.get("sql") or "").strip()
            if sql:
                spec["sql"] = sql
        summary = str(generated.get("intent_summary") or "").strip()
        if not summary:
            summary = self._derive_summary(prompt, spec)
        spec["summary"] = summary
        spec["meta"]["summary"] = summary

    def _project_spec_for_prompt(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": str(spec.get("summary") or ""),
            "uses_db": bool(spec.get("uses_db")),
            "sql": str(spec.get("sql") or ""),
            "input_contract": [
                copy.deepcopy(item)
                for item in (spec.get("input_contract") or [])
                if isinstance(item, dict)
            ],
            "output_fields": [
                copy.deepcopy(item)
                for item in (spec.get("output_fields") or [])
                if isinstance(item, dict)
            ],
        }

    def _build_function_definition(self, spec: dict[str, Any]) -> str:
        input_contract = [
            item for item in (spec.get("input_contract") or []) if isinstance(item, dict)
        ]
        lines = [
            f"Function: {spec.get('function_name') or 'generated_function'}",
            "Signature: run(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]",
            "Runtime Helpers: self.get_session_by_id(datasource_id) -> SQLAlchemy Session",
            "Rows Contract: query rows are mapping/dict-style.",
            "Input Parameters:",
        ]
        if not input_contract:
            lines.append("- (none declared)")
        else:
            for field in input_contract:
                required = "required" if bool(field.get("required")) else "optional"
                field_type = str(field.get("type") or "string")
                description = str(field.get("description") or "").strip() or "no description"
                extras: list[str] = []
                enum = field.get("enum")
                if isinstance(enum, list) and enum:
                    extras.append(f"enum={enum}")
                if isinstance(field.get("minimum"), (int, float)):
                    extras.append(f"min={field.get('minimum')}")
                if isinstance(field.get("maximum"), (int, float)):
                    extras.append(f"max={field.get('maximum')}")
                if field.get("default") is not None:
                    extras.append(f"default={field.get('default')}")
                suffix = f" [{' ; '.join(extras)}]" if extras else ""
                lines.append(
                    f"- {field.get('name')} ({field_type}, {required}): {description}{suffix}"
                )
        return "\n".join(lines)

    def _build_evidence_pack(
        self,
        *,
        prompt: str,
        spec: dict[str, Any],
        current_code: str | None,
    ) -> dict[str, Any]:
        input_contract = [
            item for item in (spec.get("input_contract") or []) if isinstance(item, dict)
        ]
        output_fields = [
            item for item in (spec.get("output_fields") or []) if isinstance(item, dict)
        ]
        return {
            "retrieval_version": "function-evidence-v1",
            "prompt_focus": _compact_whitespace(prompt),
            "current_function_spec": self._project_spec_for_prompt(spec),
            "current_function_definition": self._build_function_definition(spec),
            "current_code_excerpt": str(current_code or "")[:1200],
            "object_contracts": [
                self._extract_model_contract("datasource", db_models.DataSource),
                self._extract_model_contract("function", db_models.Function),
                self._extract_model_contract("schedule", db_models.Schedule),
            ],
            "runtime_capabilities": {
                "query_tool_contract": {
                    "required_params": ["sql", "datasource_id"],
                    "param_types": {"sql": "string", "datasource_id": "integer", "role": "string"},
                },
                "function_base_helpers": {
                    "get_session_by_id": "self.get_session_by_id(datasource_id) -> SQLAlchemy Session",
                    "query_pattern": "session.execute(text(sql), params).mappings().all()",
                    "rows_contract": "mapping/dict-style rows",
                    "forbidden": [
                        "raw host/user/password",
                        "manual DB client creation",
                        "row[0] positional row access",
                    ],
                },
                "builder_execution": {
                    "supports_draft_runtime_path": True,
                    "supports_plan_apply_modes": True,
                },
            },
            "known_input_contract": [
                {
                    "name": str(item.get("name") or "").strip(),
                    "type": str(item.get("type") or "").strip().lower(),
                    "required": bool(item.get("required")),
                    "description": str(item.get("description") or "").strip(),
                }
                for item in input_contract
                if str(item.get("name") or "").strip()
            ],
            "known_output_contract": output_fields,
        }

    def _extract_model_contract(self, object_name: str, model_cls: Any) -> dict[str, Any]:
        fields: list[dict[str, Any]] = []
        table = getattr(model_cls, "__table__", None)
        if table is None:
            return {"object": object_name, "fields": fields}
        for column in table.columns:
            fields.append(
                {
                    "name": str(column.name),
                    "type": _normalize_sqlalchemy_type(column.type),
                    "nullable": bool(column.nullable),
                    "primary_key": bool(column.primary_key),
                }
            )
        return {"object": object_name, "fields": fields}

    def _material_clarification_questions(
        self,
        *,
        questions: list[str],
        evidence_pack: dict[str, Any],
        ambiguities: list[dict[str, str]],
    ) -> list[str]:
        ambiguity_lookup = {
            str(item.get("question") or "").strip()
            for item in ambiguities
            if isinstance(item, dict)
        }
        material: list[str] = []
        for question in questions:
            text = str(question or "").strip()
            if not text:
                continue
            if self._question_hits_known_fact(text, evidence_pack=evidence_pack):
                continue
            # Preserve ambiguity-hint questions only; avoid keyword-triggered shaping.
            if text in ambiguity_lookup:
                material.append(text)
        if not material and ambiguities and questions:
            material.append(str(questions[0]).strip())
        return material[:1]

    def _question_hits_known_fact(self, question: str, *, evidence_pack: dict[str, Any]) -> bool:
        normalized = _compact_whitespace(question).lower()
        if not normalized:
            return False
        known_types: dict[str, str] = {}
        for item in evidence_pack.get("known_input_contract") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            field_type = str(item.get("type") or "").strip().lower()
            if name and field_type:
                known_types[name] = field_type
        if not known_types:
            return False
        asks_type = any(
            token in normalized for token in ["type", "int", "integer", "string", "number"]
        )
        if not asks_type:
            return False
        for name in known_types:
            if name and name in normalized:
                return True
        return False

    async def _call_llm_for_json(self, *, messages: list[dict[str, str]]) -> str:
        response: dict[str, Any] | None = None
        async for chunk in self.llm.chat(
            messages=messages,
            tools=None,
            stream=False,
            temperature=0.0,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            raise ValueError("LLM returned empty response")
        content = (
            ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if not content:
            raise ValueError("LLM did not return a structured patch")
        return content

    def _run_async_safely(self, coro: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except BaseException as exc:  # pragma: no cover - forwarded to caller
                error["value"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "value" in error:
            raise error["value"]
        return result.get("value")

    def _parse_json_patch(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception as err:
            raise ValueError(f"LLM patch is not valid JSON: {err}") from err
        if not isinstance(data, dict):
            raise ValueError("LLM patch must be a JSON object")
        return data

    def _collect_ambiguities(self, prompt: str) -> list[dict[str, str]]:
        _ = prompt
        return []

    def _normalize_spec(
        self,
        current_dependencies: dict[str, Any] | None,
        *,
        function_name: str,
        current_code: str | None,
    ) -> dict[str, Any]:
        builder_spec = {}
        if isinstance(current_dependencies, dict) and isinstance(
            current_dependencies.get("builder_spec"), dict
        ):
            builder_spec = copy.deepcopy(current_dependencies["builder_spec"])

        output_fields = builder_spec.get("output_fields")
        if not isinstance(output_fields, list):
            output_fields = [{"name": "ok", "kind": "constant", "value": True}]
        input_contract = builder_spec.get("input_contract")
        if not isinstance(input_contract, list):
            input_contract = []

        meta = builder_spec.get("meta") if isinstance(builder_spec.get("meta"), dict) else {}
        return {
            "version": "function-builder-v1",
            "class_name": "GeneratedFunction",
            "function_name": function_name,
            "summary": str(builder_spec.get("summary") or function_name or "generated function"),
            "uses_db": bool(builder_spec.get("uses_db")),
            "sql": str(builder_spec.get("sql") or "SELECT 1 AS value"),
            "input_contract": [field for field in input_contract if isinstance(field, dict)],
            "output_fields": [field for field in output_fields if isinstance(field, dict)],
            "meta": {
                "updated_at": str(meta.get("updated_at") or ""),
                "last_prompt": str(meta.get("last_prompt") or ""),
                "summary": str(meta.get("summary") or "basic function"),
                "intent_tags": [str(item) for item in (meta.get("intent_tags") or []) if str(item)],
            },
        }

    def _ensure_output_field(self, spec: dict[str, Any], field: dict[str, Any]) -> None:
        existing = next(
            (item for item in spec["output_fields"] if item.get("name") == field["name"]), None
        )
        if existing is None:
            spec["output_fields"].append(field)
            return
        existing.update(field)

    def _derive_summary(self, prompt: str, spec: dict[str, Any]) -> str:
        _ = prompt
        if spec["uses_db"]:
            return "database query function"
        return "basic runtime function"

    def _render_code(self, spec: dict[str, Any]) -> str:
        lines = [
            "from typing import Any",
            "from sqlalchemy import text",
            "",
            "",
            f"class {spec['class_name']}(FunctionBase):",
            "    def run(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:",
            '        """',
            "        Input parameter contract:",
        ]
        input_contract = [
            item for item in (spec.get("input_contract") or []) if isinstance(item, dict)
        ]
        if input_contract:
            for field in input_contract:
                required = "required" if bool(field.get("required")) else "optional"
                field_type = str(field.get("type") or "string")
                description = str(field.get("description") or "").strip() or "no description"
                lines.append(
                    f"        - {field.get('name')} ({field_type}, {required}): {description}"
                )
        else:
            lines.append("        - no explicit parameter constraints")
        lines.extend(
            [
                '        """',
                "        result: dict[str, Any] = {}",
            ]
        )

        for field in spec["output_fields"]:
            name = field.get("name")
            kind = field.get("kind")
            if kind == "constant":
                lines.append(f"        result[{name!r}] = {field.get('value')!r}")
            elif kind == "context":
                path = str(field.get("path") or "")
                lines.append(f"        result[{name!r}] = context.get({path!r})")
            elif kind == "payload_len":
                key = str(field.get("key") or "rows")
                lines.append(f"        result[{name!r}] = len(payload.get({key!r}, []))")

        if spec["uses_db"]:
            intent_tags = [
                str(item)
                for item in ((spec.get("meta") or {}).get("intent_tags") or [])
                if str(item)
            ]
            lines.extend(
                [
                    "        datasource_id = payload.get('datasource_id') or payload.get('datasourceId') or 'default'",
                    "        session = self.get_session_by_id(int(datasource_id))",
                    f"        sql_text = {spec['sql']!r}",
                    "        sql_params: dict[str, Any] | None = None",
                    "        if '?' in sql_text:",
                    "            if datasource_id in (None, '', 'default'):",
                    "                raise ValueError('SQL contains ? placeholder but no datasource_id parameter was provided')",
                    "            sql_text = sql_text.replace('?', ':datasource_id', 1)",
                    "            sql_params = {'datasource_id': int(datasource_id)}",
                    "        result_rows = session.execute(text(sql_text), sql_params or {}).mappings().all()",
                    "        query_result = {'rows': [dict(row) for row in result_rows]}",
                ]
            )
            if "list_databases" in intent_tags:
                lines.extend(
                    [
                        "        rows = query_result.get('rows', []) or []",
                        "        databases: list[str] = []",
                        "        for row in rows:",
                        "            if not isinstance(row, dict):",
                        "                continue",
                        "            name = row.get('Database') or row.get('database') or row.get('database_name') or row.get('name')",
                        "            if isinstance(name, str) and name:",
                        "                databases.append(name)",
                        "        result['datasource_id'] = datasource_id",
                        "        result['databases'] = databases",
                        "        result['count'] = len(databases)",
                    ]
                )
            else:
                lines.extend(
                    [
                        "        result['query_result'] = query_result",
                        "        result['rows'] = query_result.get('rows', [])",
                        "        result['datasource_id'] = datasource_id",
                    ]
                )

        lines.append("        return result")
        return "\n".join(lines) + "\n"
