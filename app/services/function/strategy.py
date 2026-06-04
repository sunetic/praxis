from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import models
from app.services.function.runtime_contract import get_function_runtime_contract

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_RUNTIME_CONTRACT = get_function_runtime_contract()
_DB_SCHEMAS = (((_RUNTIME_CONTRACT.get("db_api") or {}).get("schemas")) or {})
_DB_ROLE_ENUM = set(((_RUNTIME_CONTRACT.get("db_api") or {}).get("role_enum")) or [])
_PLATFORM_CONTRACT = (_RUNTIME_CONTRACT.get("platform_api") or {})
_PLATFORM_LIST_FILTER_SCHEMAS = (_PLATFORM_CONTRACT.get("list_filter_schemas") or {})
_PLATFORM_CRUD_PAYLOAD_SCHEMAS = (_PLATFORM_CONTRACT.get("crud_payload_schemas") or {})
_PLATFORM_OPERATE_PAYLOAD_SCHEMAS = (_PLATFORM_CONTRACT.get("operate_payload_schemas") or {})


def _resolve_platform_schema_ref(ref: str) -> dict[str, Any]:
    current: Any = _RUNTIME_CONTRACT
    for part in str(ref or "").split("."):
        if not isinstance(current, dict):
            return {}
        current = current.get(part)
    return current if isinstance(current, dict) else {}


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "")}


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


@dataclass(frozen=True)
class StrategyThresholds:
    reuse: float = 0.82
    extend: float = 0.45

    def normalized(self) -> StrategyThresholds:
        reuse = min(max(self.reuse, 0.0), 1.0)
        extend = min(max(self.extend, 0.0), 1.0)
        if extend > reuse:
            extend = reuse
        return StrategyThresholds(reuse=reuse, extend=extend)


class FunctionCandidateRetriever:
    def retrieve(
        self,
        db: Session,
        *,
        requirement_text: str,
        contract: dict[str, Any] | None = None,
        exclude_function_id: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        requirement_tokens = _tokenize(requirement_text)
        query = (
            db.query(models.Function)
            .filter(
                models.Function.status == "released",
                models.Function.current_release_id.isnot(None),
            )
            .all()
        )
        candidates: list[dict[str, Any]] = []
        for function in query:
            if exclude_function_id is not None and function.id == exclude_function_id:
                continue
            release = function.current_release
            if release is None:
                continue
            release_metadata = release.release_metadata or {}
            profile_tokens = _tokenize(f"{function.name or ''} {function.slug or ''} {function.description or ''}")
            metadata_keywords = release_metadata.get("keywords")
            metadata_tokens = _tokenize(" ".join(metadata_keywords)) if isinstance(metadata_keywords, list) else set()
            similarity = max(
                _jaccard_similarity(requirement_tokens, profile_tokens),
                _jaccard_similarity(requirement_tokens, metadata_tokens),
            )
            contract_score = self._contract_compatibility(contract, release_metadata)
            score = min(1.0, similarity * 0.8 + contract_score * 0.2)
            candidates.append(
                {
                    "function_id": function.id,
                    "release_id": release.id,
                    "version": release.version,
                    "name": function.name,
                    "slug": function.slug,
                    "score": round(score, 4),
                    "similarity_score": round(similarity, 4),
                    "contract_score": round(contract_score, 4),
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[: max(1, limit)]

    def _contract_compatibility(
        self,
        expected: dict[str, Any] | None,
        release_metadata: dict[str, Any] | None,
    ) -> float:
        if not expected:
            return 0.0
        if not isinstance(release_metadata, dict):
            return 0.0
        actual = release_metadata.get("contract")
        if not isinstance(actual, dict):
            return 0.0
        expected_tokens = self._contract_signature_tokens(expected)
        actual_tokens = self._contract_signature_tokens(actual)
        base_score = _jaccard_similarity(expected_tokens, actual_tokens)
        expected_profile = expected.get("capability_profile")
        actual_profile = actual.get("capability_profile")
        profile_score = self._capability_profile_compatibility(expected_profile, actual_profile)
        if profile_score > 0:
            return min(1.0, base_score * 0.5 + profile_score * 0.5)
        return base_score

    def _contract_signature_tokens(self, payload: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                for key in sorted(node.keys()):
                    key_text = str(key)
                    next_prefix = f"{prefix}.{key_text}" if prefix else key_text
                    tokens.add(next_prefix.lower())
                    walk(node.get(key), next_prefix)
                return
            if isinstance(node, list):
                for item in node:
                    walk(item, prefix)
                return
            if node is None:
                return
            leaf = " ".join(str(node).split())
            if leaf:
                tokens.add(f"{prefix}={leaf}".lower())

        walk(payload, "")
        return tokens

    def _capability_profile_compatibility(self, expected: Any, actual: Any) -> float:
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return 0.0
        expected_tokens = self._contract_signature_tokens(expected)
        actual_tokens = self._contract_signature_tokens(actual)
        return _jaccard_similarity(expected_tokens, actual_tokens)


class FunctionStrategyDecider:
    def __init__(self, retriever: FunctionCandidateRetriever | None = None):
        self._retriever = retriever or FunctionCandidateRetriever()

    def decide(
        self,
        db: Session,
        *,
        requirement_text: str,
        contract: dict[str, Any] | None = None,
        exclude_function_id: int | None = None,
        force_strategy: str | None = None,
        thresholds: StrategyThresholds | None = None,
    ) -> dict[str, Any]:
        normalized_thresholds = (thresholds or StrategyThresholds()).normalized()
        candidates = self._retriever.retrieve(
            db,
            requirement_text=requirement_text,
            contract=contract,
            exclude_function_id=exclude_function_id,
        )
        top = candidates[0] if candidates else None

        if force_strategy in {"reuse", "extend", "create"}:
            strategy = force_strategy
            reason = "forced_by_input"
        elif top and top["score"] >= normalized_thresholds.reuse:
            strategy = "reuse"
            reason = "top_candidate_above_reuse_threshold"
        elif top and top["score"] >= normalized_thresholds.extend:
            strategy = "extend"
            reason = "top_candidate_above_extend_threshold"
        else:
            strategy = "create"
            reason = "no_candidate_above_threshold"

        return {
            "strategy": strategy,
            "reason": reason,
            "thresholds": {
                "reuse": normalized_thresholds.reuse,
                "extend": normalized_thresholds.extend,
            },
            "top_candidate": top,
            "candidates": candidates,
        }


class FunctionVerificationHarness:
    _DB_QUERY_METHODS = {"query", "query_by_id"}
    _DB_BY_ID_METHODS = {"query_by_id", "explain_by_id"}
    _DB_OPERATION_METHODS = {"query", "explain", "query_by_id", "explain_by_id", "get_conn_by_id", "get_session_by_id"}
    _SCHEDULER_HISTORY_STATUS_ENUM = {"queued", "running", "retrying", "success", "failed"}
    _BUSINESS_SUCCESS_TOKENS = ("success", "成功", "通过", "返回", "命中", "输出", "ok")
    _BUSINESS_FAILURE_TOKENS = ("fail", "失败", "异常", "error", "invalid", "缺少", "拒绝", "阻断")

    def verify_draft(
        self,
        *,
        code_snapshot: str,
        dependency_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add_check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})

        stripped = (code_snapshot or "").strip()
        has_code = bool(stripped)
        add_check("code_present", has_code, "Code snapshot must be non-empty")

        syntax_ok = False
        syntax_tree = None
        if has_code:
            try:
                syntax_tree = ast.parse(code_snapshot)
                syntax_ok = True
                add_check("syntax_valid", True, "Python syntax is valid")
            except SyntaxError as exc:
                add_check("syntax_valid", False, f"Syntax error: {exc.msg} at line {exc.lineno}")
        else:
            add_check("syntax_valid", False, "Syntax check skipped due to empty code")

        has_main = "def main(" in code_snapshot
        has_result = "result" in code_snapshot
        has_class_runner = False
        if syntax_tree is not None:
            has_class_runner = any(
                isinstance(node, ast.ClassDef)
                and any(
                    isinstance(member, ast.FunctionDef) and member.name == "run"
                    for member in node.body
                )
                for node in ast.walk(syntax_tree)
            )
        entrypoint_ok = bool(has_main or has_result or has_class_runner)
        add_check(
            "entrypoint_detected",
            entrypoint_ok,
            "Draft must define `main(payload, context)`, assign `result`, or implement a class `run(...)` entrypoint",
        )

        dependency_ok = dependency_manifest is None or isinstance(dependency_manifest, dict)
        add_check(
            "dependency_manifest_valid",
            dependency_ok,
            "dependency_manifest must be an object or null",
        )

        query_result_issues: list[str] = []
        if syntax_tree is not None:
            query_result_issues = self._detect_db_query_result_usage_issues(syntax_tree)
        add_check(
            "db_query_result_usage_valid",
            not query_result_issues,
            (
                "db.query/query_by_id returns mapping objects; iterate over result.get('rows', []) first, "
                "do not iterate over the query result object directly."
            ),
        )
        for issue in query_result_issues:
            add_check("db_query_result_usage_detail", False, issue)

        row_index_issues: list[str] = []
        if syntax_tree is not None:
            row_index_issues = self._detect_mapping_row_positional_index_issues(syntax_tree)
        add_check(
            "mapping_row_access_valid",
            not row_index_issues,
            "Mapping rows must use key access (e.g. row.get('Database')); positional indexing row[0] is forbidden.",
        )
        for issue in row_index_issues:
            add_check("mapping_row_access_detail", False, issue)

        db_by_id_call_issues: list[str] = []
        if syntax_tree is not None:
            db_by_id_call_issues = self._detect_db_by_id_calling_issues(syntax_tree)
        add_check(
            "db_by_id_calling_valid",
            not db_by_id_call_issues,
            "db.query_by_id/explain_by_id must be called with keyword argument datasource_id=...",
        )
        for issue in db_by_id_call_issues:
            add_check("db_by_id_calling_detail", False, issue)

        swallowed_db_exception_issues: list[str] = []
        if syntax_tree is not None:
            swallowed_db_exception_issues = self._detect_swallowed_db_exception_issues(syntax_tree)
        add_check(
            "db_exception_not_swallowed",
            not swallowed_db_exception_issues,
            "Database call exceptions must be raised; swallowing exceptions and returning empty results or defaults is forbidden.",
        )
        for issue in swallowed_db_exception_issues:
            add_check("db_exception_not_swallowed_detail", False, issue)

        capability_schema_issues: list[str] = []
        if syntax_tree is not None:
            capability_schema_issues = [
                *self._detect_db_contract_issues(syntax_tree),
                *self._detect_platform_contract_issues(syntax_tree),
                *self._detect_scheduler_history_contract_issues(syntax_tree),
            ]
        add_check(
            "capability_schema_valid",
            not capability_schema_issues,
            "LLM-facing capability calls must use declared parameters; inventing undeclared fields or illegal enum values is forbidden.",
        )
        for issue in capability_schema_issues:
            add_check("capability_schema_detail", False, issue)

        business_verification_issues = self._detect_business_verification_issues(dependency_manifest)
        add_check(
            "business_verification_valid",
            not business_verification_issues,
            "Business verification checklist should include at least 1 success check and 1 failure check to ensure result usability.",
        )
        for issue in business_verification_issues:
            add_check("business_verification_detail", False, issue)

        capability_profile = self._collect_capability_profile(
            syntax_tree=syntax_tree,
            dependency_manifest=dependency_manifest,
        )

        passed = all(item["passed"] for item in checks)
        diagnostics = [item["detail"] for item in checks if not item["passed"]]
        return {
            "passed": passed,
            "checks": checks,
            "diagnostics": diagnostics,
            "verified_at": datetime.now(UTC).isoformat(),
            "verification_type": "pre_release_harness",
            "syntax_valid": syntax_ok,
            "capability_profile_version": "function-capability-profile-v1",
            "capability_profile": capability_profile,
        }

    def _extract_business_verification_checks(self, dependency_manifest: dict[str, Any] | None) -> list[str]:
        if not isinstance(dependency_manifest, dict):
            return []
        business = dependency_manifest.get("business_verification")
        if isinstance(business, dict):
            checks = business.get("checks")
            if isinstance(checks, list):
                return [str(item).strip() for item in checks if str(item).strip()]
        builder_spec = dependency_manifest.get("builder_spec")
        if isinstance(builder_spec, dict):
            checks = builder_spec.get("verification_checks")
            if isinstance(checks, list):
                return [str(item).strip() for item in checks if str(item).strip()]
        return []

    def _detect_business_verification_issues(self, dependency_manifest: dict[str, Any] | None) -> list[str]:
        checks = self._extract_business_verification_checks(dependency_manifest)
        if not checks:
            # Backward compatible: legacy/manual function drafts may not provide checks yet.
            return []
        issues: list[str] = []
        if len(checks) < 2:
            issues.append("Business verification checklist requires at least 2 items (success + failure).")
            return issues
        normalized = [item.casefold() for item in checks]
        has_success = any(any(token in item for token in self._BUSINESS_SUCCESS_TOKENS) for item in normalized)
        has_failure = any(any(token in item for token in self._BUSINESS_FAILURE_TOKENS) for item in normalized)
        if not has_success:
            issues.append("Business verification checklist is missing a success path check.")
        if not has_failure:
            issues.append("Business verification checklist is missing a failure path check.")
        return issues

    def _collect_capability_profile(
        self,
        *,
        syntax_tree: ast.AST | None,
        dependency_manifest: dict[str, Any] | None,
    ) -> dict[str, Any]:
        db_methods: set[str] = set()
        platform_calls: list[dict[str, Any]] = []
        scheduler_history_calls: list[str] = []
        platform_seen: set[tuple[str, str, str]] = set()
        if syntax_tree is not None:
            for node in ast.walk(syntax_tree):
                if not isinstance(node, ast.Call):
                    continue
                db_method = self._db_helper_method(node)
                if db_method is not None:
                    db_methods.add(db_method)
                platform_method = self._platform_helper_method(node)
                if platform_method is not None:
                    object_type = self._literal_string(self._keyword_or_positional(node, keyword="object_type", position=0)) or ""
                    action = ""
                    if platform_method in {"crud", "operate"}:
                        action = self._literal_string(self._keyword_or_positional(node, keyword="action", position=1)) or ""
                    marker = (platform_method, object_type, action)
                    if marker not in platform_seen:
                        platform_seen.add(marker)
                        platform_calls.append(
                            {
                                "method": platform_method,
                                "object_type": object_type or None,
                                "action": action or None,
                            }
                        )
                scheduler_helper = self._scheduler_history_helper_method(node)
                if scheduler_helper is not None:
                    scheduler_history_calls.append(scheduler_helper)
        input_contract: list[dict[str, Any]] = []
        output_fields: list[dict[str, Any]] = []
        verification_checks = self._extract_business_verification_checks(dependency_manifest)
        if isinstance(dependency_manifest, dict):
            builder_spec = dependency_manifest.get("builder_spec")
            if isinstance(builder_spec, dict):
                raw_input_contract = builder_spec.get("input_contract")
                if isinstance(raw_input_contract, list):
                    for item in raw_input_contract:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        if not name:
                            continue
                        input_contract.append(
                            {
                                "name": name,
                                "type": str(item.get("type") or "string").strip().lower(),
                                "required": bool(item.get("required")),
                            }
                        )
                raw_output_fields = builder_spec.get("output_fields")
                if isinstance(raw_output_fields, list):
                    for item in raw_output_fields:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        if not name:
                            continue
                        output_fields.append(
                            {
                                "name": name,
                                "kind": str(item.get("kind") or "constant").strip(),
                            }
                        )
        return {
            "db_methods": sorted(db_methods),
            "platform_calls": sorted(
                platform_calls,
                key=lambda item: (
                    str(item.get("method") or ""),
                    str(item.get("object_type") or ""),
                    str(item.get("action") or ""),
                ),
            ),
            "scheduler_history_calls": sorted(set(scheduler_history_calls)),
            "input_contract": input_contract,
            "output_fields": output_fields,
            "business_verification_checks": verification_checks,
        }

    def _detect_db_query_result_usage_issues(self, syntax_tree: ast.AST) -> list[str]:
        assignment_states: dict[str, list[tuple[int, bool]]] = {}
        issues: list[str] = []

        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Assign) and self._is_db_query_call(node.value):
                for target in node.targets:
                    for name in self._extract_assigned_names(target):
                        assignment_states.setdefault(name, []).append((int(getattr(node, "lineno", 0) or 0), True))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in self._extract_assigned_names(target):
                        assignment_states.setdefault(name, []).append((int(getattr(node, "lineno", 0) or 0), False))
            elif isinstance(node, ast.AnnAssign) and self._is_db_query_call(node.value):
                for name in self._extract_assigned_names(node.target):
                    assignment_states.setdefault(name, []).append((int(getattr(node, "lineno", 0) or 0), True))
            elif isinstance(node, ast.AnnAssign):
                for name in self._extract_assigned_names(node.target):
                    assignment_states.setdefault(name, []).append((int(getattr(node, "lineno", 0) or 0), False))

        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.For):
                if isinstance(node.iter, ast.Call) and self._is_db_query_call(node.iter):
                    line = getattr(node, "lineno", "?")
                    issues.append(
                        f"Line {line}: directly iterating db.query(...) return value; save the result first and iterate over result.get('rows', [])."
                    )
                iter_name = self._name_from_expr(node.iter)
                if iter_name and self._latest_assignment_is_db_query(
                    assignment_states.get(iter_name, []),
                    before_line=int(getattr(node, "lineno", 0) or 0),
                ):
                    line = getattr(node, "lineno", "?")
                    issues.append(
                        f"Line {line}: variable `{iter_name}` comes from db.query(...), cannot iterate directly; use `{iter_name}.get('rows', [])` instead."
                    )
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for generator in node.generators:
                    if isinstance(generator.iter, ast.Call) and self._is_db_query_call(generator.iter):
                        line = getattr(node, "lineno", "?")
                        issues.append(
                            f"Line {line}: comprehension directly iterates db.query(...) return value; use result.get('rows', []) first."
                        )
                    iter_name = self._name_from_expr(generator.iter)
                    if iter_name and self._latest_assignment_is_db_query(
                        assignment_states.get(iter_name, []),
                        before_line=int(getattr(node, "lineno", 0) or 0),
                    ):
                        line = getattr(node, "lineno", "?")
                        issues.append(
                            f"Line {line}: comprehension directly iterates db.query return variable `{iter_name}`; use `{iter_name}.get('rows', [])` first."
                        )

        return issues

    def _is_db_query_call(self, node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call):
            return False
        method = self._call_method_name(node)
        return method in self._DB_QUERY_METHODS

    def _is_db_operation_call(self, node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call):
            return False
        method = self._call_method_name(node)
        return method in self._DB_OPERATION_METHODS

    def _call_method_name(self, node: ast.Call) -> str | None:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        return str(func.attr)

    def _detect_db_by_id_calling_issues(self, syntax_tree: ast.AST) -> list[str]:
        issues: list[str] = []
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call):
                continue
            method = self._call_method_name(node)
            if method not in self._DB_BY_ID_METHODS:
                continue
            keyword_names = {str(item.arg) for item in node.keywords if item.arg}
            line = int(getattr(node, "lineno", 0) or 0)
            if len(node.args) > 1:
                issues.append(
                    f"Line {line or '?'}: `{method}` datasource_id must not use positional argument; use `datasource_id=...` keyword argument."
                )
                continue
            if "datasource_id" not in keyword_names:
                issues.append(
                    f"Line {line or '?'}: `{method}` is missing required keyword argument `datasource_id=...`."
                )
        return issues

    def _detect_swallowed_db_exception_issues(self, syntax_tree: ast.AST) -> list[str]:
        issues: list[str] = []
        seen_lines: set[int] = set()
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Try):
                continue
            if not self._try_block_has_db_operation(node.body):
                continue
            for handler in node.handlers:
                if self._handler_reraises(handler.body):
                    continue
                line = int(getattr(handler, "lineno", 0) or getattr(node, "lineno", 0) or 0)
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                handler_type = "except Exception"
                if handler.type is None:
                    handler_type = "bare except"
                elif isinstance(handler.type, ast.Name):
                    handler_type = f"except {handler.type.id}"
                issues.append(
                    f"Line {line or '?'}: DB call inside try block; {handler_type} must not swallow exceptions; explicitly raise after catching."
                )
        return issues

    def _try_block_has_db_operation(self, body: list[ast.stmt]) -> bool:
        if not body:
            return False
        probe_root = ast.Module(body=body, type_ignores=[])
        for child in ast.walk(probe_root):
            if self._is_db_operation_call(child):
                return True
        return False

    def _handler_reraises(self, body: list[ast.stmt]) -> bool:
        for stmt in body:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Raise):
                    return True
        return False

    def _extract_assigned_names(self, node: ast.AST | None) -> set[str]:
        if node is None:
            return set()
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for item in node.elts:
                names.update(self._extract_assigned_names(item))
            return names
        return set()

    def _name_from_expr(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        return None

    def _detect_mapping_row_positional_index_issues(self, syntax_tree: ast.AST) -> list[str]:
        rows_var_names: set[str] = set()
        issues: list[str] = []
        seen: set[tuple[int, str]] = set()

        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.Assign) and self._is_rows_get_call(node.value):
                for target in node.targets:
                    rows_var_names.update(self._extract_assigned_names(target))
            elif isinstance(node, ast.AnnAssign) and self._is_rows_get_call(node.value):
                rows_var_names.update(self._extract_assigned_names(node.target))

        if not rows_var_names:
            return issues

        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.For):
                iter_name = self._name_from_expr(node.iter)
                if iter_name not in rows_var_names:
                    continue
                aliases = self._extract_assigned_names(node.target)
                for alias in aliases:
                    for child in ast.walk(node):
                        if self._is_positional_subscript_on_name(child, alias):
                            line = int(getattr(child, "lineno", 0) or 0)
                            marker = (line, alias)
                            if marker in seen:
                                continue
                            seen.add(marker)
                            issues.append(
                                f"Line {line or '?'}: row variable `{alias}` comes from mapping rows; positional indexing `{alias}[0]` is forbidden; use key access instead."
                            )
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                aliases: set[str] = set()
                for generator in node.generators:
                    iter_name = self._name_from_expr(generator.iter)
                    if iter_name in rows_var_names:
                        aliases.update(self._extract_assigned_names(generator.target))
                if not aliases:
                    continue
                for alias in aliases:
                    for child in ast.walk(node):
                        if self._is_positional_subscript_on_name(child, alias):
                            line = int(getattr(child, "lineno", 0) or 0)
                            marker = (line, alias)
                            if marker in seen:
                                continue
                            seen.add(marker)
                            issues.append(
                                f"Line {line or '?'}: `{alias}` in comprehension comes from mapping rows; positional indexing `{alias}[0]` is forbidden; use key access instead."
                            )

        return issues

    def _is_rows_get_call(self, node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if not isinstance(func, ast.Attribute) or str(func.attr) != "get":
            return False
        if not node.args:
            return False
        first = node.args[0]
        return isinstance(first, ast.Constant) and first.value == "rows"

    def _is_positional_subscript_on_name(self, node: ast.AST, name: str) -> bool:
        if not isinstance(node, ast.Subscript):
            return False
        value = node.value
        if not isinstance(value, ast.Name) or value.id != name:
            return False
        index = node.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, int):
            return True
        return False

    def _latest_assignment_is_db_query(self, assignments: list[tuple[int, bool]], *, before_line: int) -> bool:
        if not assignments:
            return False
        ordered = sorted(assignments, key=lambda item: item[0])
        latest: bool | None = None
        for line, is_db_query in ordered:
            if line >= before_line:
                break
            latest = is_db_query
        return bool(latest)

    def _detect_db_contract_issues(self, syntax_tree: ast.AST) -> list[str]:
        issues: list[str] = []
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call):
                continue
            method = self._db_helper_method(node)
            if method is None:
                continue
            line = int(getattr(node, "lineno", 0) or 0)
            if method in {"query", "explain"}:
                if len(node.args) > 1:
                    issues.append(
                        f"Line {line or '?'}: `db.{method}` accepts only one positional argument sql; all other arguments must use keywords."
                    )
            elif node.args:
                issues.append(
                    f"Line {line or '?'}: `db.{method}` only accepts keyword arguments."
                )
                continue
            schema = _DB_SCHEMAS.get(method)
            allowed_keywords = set((((schema or {}).get("properties")) or {}).keys())
            keyword_names = {str(item.arg) for item in node.keywords if item.arg}
            unknown = sorted(keyword_names - allowed_keywords)
            if unknown:
                issues.append(
                    f"Line {line or '?'}: `db.{method}` contains undeclared parameters: {', '.join(unknown)}."
                )
            required = set((((schema or {}).get("constraints")) or {}).get("required") or [])
            if method in {"query", "explain"}:
                required -= {"sql"}
            missing = sorted(name for name in required if name not in keyword_names)
            if missing:
                issues.append(
                    f"Line {line or '?'}: `db.{method}` is missing required parameters: {', '.join(missing)}."
                )
            for keyword in node.keywords:
                if keyword.arg == "role":
                    issues.extend(self._validate_literal_enum_value(keyword.value, _DB_ROLE_ENUM, f"`db.{method}.role`", line))
        return issues

    def _db_helper_method(self, node: ast.Call) -> str | None:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        method = str(func.attr)
        if method not in _DB_SCHEMAS:
            return None
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "db":
            return method
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
            if owner.value.id == "self" and owner.attr == "db":
                return method
        return None

    def _detect_platform_contract_issues(self, syntax_tree: ast.AST) -> list[str]:
        issues: list[str] = []
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call):
                continue
            method = self._platform_helper_method(node)
            if method is None:
                continue
            line = int(getattr(node, "lineno", 0) or 0)
            object_type_node = self._keyword_or_positional(node, keyword="object_type", position=0)
            action_node = None
            object_id_node = None
            payload_node = None
            filters_node = None
            if method == "list":
                filters_node = self._keyword_or_positional(node, keyword="filters", position=1)
            elif method == "get":
                object_id_node = self._keyword_or_positional(node, keyword="object_id", position=1)
            elif method == "crud":
                action_node = self._keyword_or_positional(node, keyword="action", position=1)
                object_id_node = self._keyword_or_positional(node, keyword="object_id", position=2)
                payload_node = self._keyword_or_positional(node, keyword="payload", position=3)
            elif method == "operate":
                action_node = self._keyword_or_positional(node, keyword="action", position=1)
                object_id_node = self._keyword_or_positional(node, keyword="object_id", position=2)
                payload_node = self._keyword_or_positional(node, keyword="payload", position=3)

            object_type = self._literal_string(object_type_node)
            if object_type is not None and object_type not in set(_PLATFORM_CONTRACT.get("object_types") or []):
                issues.append(
                    f"Line {line or '?'}: `platform.{method}` uses undeclared object_type: {object_type}."
                )

            if method == "list" and object_type and isinstance(filters_node, ast.Dict):
                issues.extend(
                    self._validate_platform_object_dict(
                        node=filters_node,
                        allowed_schema=_PLATFORM_LIST_FILTER_SCHEMAS.get(object_type),
                        label=f"`platform.list({object_type}).filters`",
                        line=line,
                    )
                )
                continue

            if method == "get" and object_id_node is None:
                issues.append(f"Line {line or '?'}: `platform.get` is missing object_id.")
                continue

            if method == "crud":
                action = self._literal_string(action_node)
                allowed_actions = set(((_PLATFORM_CONTRACT.get("action_enums") or {}).get("crud")) or [])
                if action is not None and action not in allowed_actions:
                    issues.append(
                        f"Line {line or '?'}: `platform.crud` uses undeclared action: {action}."
                    )
                if action in {"read", "update", "delete"} and object_id_node is None:
                    issues.append(f"Line {line or '?'}: `platform.crud` is missing object_id for `{action}` action.")
                if object_type and action and isinstance(payload_node, ast.Dict):
                    issues.extend(
                        self._validate_platform_object_dict(
                            node=payload_node,
                            allowed_schema=((_PLATFORM_CRUD_PAYLOAD_SCHEMAS.get(object_type) or {}).get(action)),
                            label=f"`platform.crud({object_type}.{action}).payload`",
                            line=line,
                        )
                    )
                continue

            if method == "operate":
                action = self._literal_string(action_node)
                allowed_actions = set(((_PLATFORM_CONTRACT.get("action_enums") or {}).get("operate")) or [])
                if action is not None and action not in allowed_actions:
                    issues.append(
                        f"Line {line or '?'}: `platform.operate` uses undeclared action: {action}."
                    )
                if object_id_node is None:
                    issues.append(f"Line {line or '?'}: `platform.operate` is missing object_id.")
                if object_type and action and isinstance(payload_node, ast.Dict):
                    issues.extend(
                        self._validate_platform_object_dict(
                            node=payload_node,
                            allowed_schema=((_PLATFORM_OPERATE_PAYLOAD_SCHEMAS.get(object_type) or {}).get(action)),
                            label=f"`platform.operate({object_type}.{action}).payload`",
                            line=line,
                        )
                    )
        return issues

    def _platform_helper_method(self, node: ast.Call) -> str | None:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        method = str(func.attr)
        if method not in {"list", "get", "crud", "operate"}:
            return None
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "platform":
            return method
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
            if owner.value.id == "self" and owner.attr == "platform":
                return method
        return None

    def _keyword_or_positional(self, node: ast.Call, *, keyword: str, position: int) -> ast.AST | None:
        for item in node.keywords:
            if item.arg == keyword:
                return item.value
        if len(node.args) > position:
            return node.args[position]
        return None

    def _literal_string(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return str(node.value)
        return None

    def _validate_platform_object_dict(
        self,
        *,
        node: ast.Dict,
        allowed_schema: dict[str, Any] | None,
        label: str,
        line: int,
    ) -> list[str]:
        schema = allowed_schema or {}
        if "$ref" in schema:
            schema = _resolve_platform_schema_ref(str(schema.get("$ref") or ""))
        if not schema:
            return []
        issues: list[str] = []
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        keys = {
            str(key.value)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if schema.get("additional_properties") is False:
            unknown = sorted(keys - set(properties.keys()))
            if unknown:
                issues.append(f"Line {line or '?'}: {label} contains undeclared fields: {', '.join(unknown)}.")
        constraints = schema.get("constraints") if isinstance(schema.get("constraints"), dict) else {}
        required = constraints.get("required") if isinstance(constraints.get("required"), list) else []
        missing = sorted(str(name) for name in required if str(name) not in keys)
        if missing:
            issues.append(f"Line {line or '?'}: {label} is missing required fields: {', '.join(missing)}.")
        if label.endswith(".payload`") and "at_least_one_of" in constraints:
            declared = set(str(item) for item in constraints.get("at_least_one_of") or [])
            if declared and not (keys & declared):
                issues.append(f"Line {line or '?'}: {label} requires at least one of: {', '.join(sorted(declared))}.")
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                continue
            child_schema = properties.get(str(key_node.value))
            issues.extend(self._validate_literal_node_against_schema(value_node, child_schema, f"{label}.{key_node.value}", line))
        return issues

    def _validate_literal_node_against_schema(
        self,
        node: ast.AST,
        schema: dict[str, Any] | None,
        label: str,
        line: int,
    ) -> list[str]:
        if not isinstance(schema, dict):
            return []
        if "$ref" in schema:
            return self._validate_literal_node_against_schema(node, _resolve_platform_schema_ref(str(schema.get("$ref") or "")), label, line)
        if "one_of" in schema:
            options = schema.get("one_of")
            if isinstance(options, list):
                for option in options:
                    candidate = self._validate_literal_node_against_schema(node, option if isinstance(option, dict) else {}, label, line)
                    if not candidate:
                        return []
                return []
        schema_type = schema.get("type")
        if schema_type == "string":
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if isinstance(schema.get("enum"), list) and str(node.value) not in set(schema.get("enum") or []):
                    return [f"Line {line or '?'}: {label} contains undeclared value: {node.value}."]
            return []
        if schema_type == "integer":
            if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
                minimum = schema.get("minimum")
                maximum = schema.get("maximum")
                if isinstance(minimum, int) and int(node.value) < minimum:
                    return [f"Line {line or '?'}: {label} must be >= {minimum}."]
                if isinstance(maximum, int) and int(node.value) > maximum:
                    return [f"Line {line or '?'}: {label} must be <= {maximum}."]
            return []
        if schema_type == "number":
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                minimum = schema.get("minimum")
                maximum = schema.get("maximum")
                if isinstance(minimum, (int, float)) and float(node.value) < float(minimum):
                    return [f"Line {line or '?'}: {label} must be >= {minimum}."]
                if isinstance(maximum, (int, float)) and float(node.value) > float(maximum):
                    return [f"Line {line or '?'}: {label} must be <= {maximum}."]
            return []
        if schema_type == "array" and isinstance(node, (ast.List, ast.Tuple)):
            item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
            issues: list[str] = []
            for item in node.elts:
                issues.extend(self._validate_literal_node_against_schema(item, item_schema, label, line))
            return issues
        if schema_type == "object" and isinstance(node, ast.Dict):
            return self._validate_platform_object_dict(node=node, allowed_schema=schema, label=label, line=line)
        return []

    def _validate_literal_enum_value(self, node: ast.AST, allowed: set[str], label: str, line: int) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            normalized = str(node.value).strip()
            if normalized not in allowed:
                return [f"Line {line or '?'}: {label} contains undeclared value: {normalized}."]
        return []

    def _detect_scheduler_history_contract_issues(self, syntax_tree: ast.AST) -> list[str]:
        issues: list[str] = []
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call):
                continue
            helper = self._scheduler_history_helper_method(node)
            if helper is None:
                continue
            line = int(getattr(node, "lineno", 0) or 0)
            allowed_keywords = {"where", "limit"} if helper == "list" else {"where", "policy", "dry_run"}
            if node.args:
                issues.append(
                    f"Line {line or '?'}: `scheduler_history.{helper}` only accepts keyword arguments; positional arguments are not allowed."
                )
                continue
            keyword_names = {str(item.arg) for item in node.keywords if item.arg}
            unknown_keywords = sorted(keyword_names - allowed_keywords)
            if unknown_keywords:
                issues.append(
                    f"Line {line or '?'}: `scheduler_history.{helper}` contains undeclared parameters: {', '.join(unknown_keywords)}."
                )
            if helper == "delete" and "policy" not in keyword_names:
                issues.append(
                    f"Line {line or '?'}: `scheduler_history.delete` is missing `policy=...`; retention parameters must not be passed directly."
                )
            for keyword in node.keywords:
                if not keyword.arg:
                    continue
                if helper in {"list", "delete"} and keyword.arg == "where":
                    issues.extend(self._validate_scheduler_history_where(keyword.value, line=line))
                if helper == "delete" and keyword.arg == "policy":
                    issues.extend(self._validate_scheduler_history_policy(keyword.value, line=line))
        return issues

    def _scheduler_history_helper_method(self, node: ast.Call) -> str | None:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        method = str(func.attr)
        if method not in {"list", "delete"}:
            return None
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "scheduler_history":
            return method
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
            if owner.value.id == "self" and owner.attr == "scheduler_history":
                return method
        return None

    def _validate_scheduler_history_where(self, node: ast.AST, *, line: int) -> list[str]:
        if not isinstance(node, ast.Dict):
            return []
        issues: list[str] = []
        keys = {
            str(key.value)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        unknown = sorted(keys - {"schedule_id", "statuses"})
        if unknown:
            issues.append(
                f"Line {line or '?'}: `scheduler_history.where` contains undeclared fields: {', '.join(unknown)}."
            )
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                continue
            if key_node.value != "statuses" or not isinstance(value_node, (ast.List, ast.Tuple)):
                continue
            invalid = []
            for item in value_node.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    normalized = item.value.strip().lower()
                    if normalized not in self._SCHEDULER_HISTORY_STATUS_ENUM:
                        invalid.append(item.value)
            if invalid:
                issues.append(
                    f"Line {line or '?'}: `where.statuses` contains undeclared status values: {', '.join(sorted(set(invalid)))}."
                )
        return issues

    def _validate_scheduler_history_policy(self, node: ast.AST, *, line: int) -> list[str]:
        if not isinstance(node, ast.Dict):
            return []
        issues: list[str] = []
        keys = {
            str(key.value)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        unknown = sorted(keys - {"retention_seconds", "keep_latest"})
        if unknown:
            issues.append(
                f"Line {line or '?'}: `scheduler_history.policy` contains undeclared fields: {', '.join(unknown)}."
            )
        if "retention_seconds" not in keys and "keep_latest" not in keys:
            issues.append(
                f"Line {line or '?'}: `scheduler_history.policy` requires at least `retention_seconds` or `keep_latest`."
            )
        return issues
