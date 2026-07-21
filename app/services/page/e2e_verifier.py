from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PageE2EVerificationResult:
    passed: bool
    diagnostics: list[str]
    checks: list[dict[str, Any]]


class PageE2EVerifier:
    def verify(
        self,
        *,
        draft_payload: dict[str, Any] | None,
        dependency_plan: dict[str, Any] | None,
    ) -> PageE2EVerificationResult:
        payload = draft_payload if isinstance(draft_payload, dict) else {}
        plan = dependency_plan if isinstance(dependency_plan, dict) else {}
        dependencies = plan.get("dependencies") if isinstance(plan.get("dependencies"), list) else []
        planned_dependencies = [
            item for item in dependencies if isinstance(item, dict) and bool(item.get("planned"))
        ]

        checks: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        checks.append({"name": "planned_dependency_count", "passed": True, "count": len(planned_dependencies)})
        if not planned_dependencies:
            return PageE2EVerificationResult(passed=True, diagnostics=diagnostics, checks=checks)

        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        dependencies_meta = meta.get("dependencies") if isinstance(meta.get("dependencies"), dict) else {}
        function_bindings = (
            dependencies_meta.get("functions")
            if isinstance(dependencies_meta.get("functions"), list)
            else []
        )
        source_code = str(source.get("code") or "")
        preview_html = str(runtime.get("preview_html") or "")

        for item in planned_dependencies:
            function_id = int(item.get("function_id") or 0)
            endpoint = str(item.get("endpoint") or "").strip()
            key = str(item.get("key") or "")

            binding_exists = any(
                isinstance(binding, dict)
                and int(binding.get("id") or 0) == function_id
                and str(binding.get("invoke_endpoint") or "").strip() == endpoint
                for binding in function_bindings
            )
            checks.append(
                {
                    "name": "binding_exists",
                    "dependency": key,
                    "function_id": function_id,
                    "endpoint": endpoint,
                    "passed": binding_exists,
                }
            )
            if not binding_exists:
                diagnostics.append(f"依赖 {key} 未正确写入页面 bindings（function_id={function_id}）")

            endpoint_in_source = bool(endpoint) and endpoint in source_code
            endpoint_in_preview = bool(endpoint) and endpoint in preview_html
            checks.append(
                {
                    "name": "endpoint_referenced_in_source",
                    "dependency": key,
                    "passed": endpoint_in_source,
                }
            )
            checks.append(
                {
                    "name": "endpoint_referenced_in_preview",
                    "dependency": key,
                    "passed": endpoint_in_preview,
                }
            )
            if not endpoint_in_source:
                diagnostics.append(f"依赖 {key} 的 endpoint 未出现在 source.code")
            if not endpoint_in_preview:
                diagnostics.append(f"依赖 {key} 的 endpoint 未出现在 runtime.preview_html")

            verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
            if verification:
                invocation_ok = bool(verification.get("ok"))
                checks.append(
                    {
                        "name": "dependency_invoke_verified",
                        "dependency": key,
                        "passed": invocation_ok,
                        "status": str(verification.get("status") or ""),
                    }
                )
                if not invocation_ok:
                    diagnostics.append(f"依赖 {key} 的 invoke 验证未通过")

        passed = all(bool(item.get("passed")) for item in checks if "passed" in item)
        return PageE2EVerificationResult(passed=passed, diagnostics=diagnostics, checks=checks)
