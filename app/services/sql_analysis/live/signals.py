from __future__ import annotations

from typing import Any


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_live_signals(
    *,
    sql_text: str | None,
    current_plans: list[dict[str, Any]],
    plan_explain: list[dict[str, Any]],
    explain_source: str,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    explain_rows = [dict(item) for item in plan_explain]
    operators = [str(item.get("operator") or "").lower() for item in explain_rows]
    properties = [str(item.get("property") or "").lower() for item in explain_rows]
    object_names = [
        str(item.get("object_name") or "").strip()
        for item in explain_rows
        if item.get("object_name")
    ]
    text = str(sql_text or "").lower()

    has_table_scan = any(
        "table scan" in operator
        or "phy_table_scan" in operator
        or "full scan" in property
        or "table scan" in property
        for operator, property in zip(operators, properties, strict=False)
    ) or any(_safe_int(item.get("table_scan")) > 0 for item in current_plans)
    if has_table_scan:
        signals.append(
            {
                "key": "table_scan_risk",
                "severity": "warning",
                "summary": "Current plan contains a table scan path, which may cause read amplification.",
                "evidence": ",".join(object_names[:3]) or "table_scan_detected",
            }
        )

    has_index_signal = any(
        "index" in operator or "index" in property
        for operator, property in zip(operators, properties, strict=False)
    )
    if has_table_scan and not has_index_signal:
        signals.append(
            {
                "key": "index_miss_risk",
                "severity": "warning",
                "summary": "Current realtime plan does not show an index access path; verify filter columns and index coverage.",
                "evidence": "no_index_operator_in_explain",
            }
        )

    has_sort_or_hash = any(
        "sort" in operator or "hash" in operator or "sort" in property or "hash" in property
        for operator, property in zip(operators, properties, strict=False)
    )
    if has_sort_or_hash:
        signals.append(
            {
                "key": "sort_or_hash_heavy",
                "severity": "info",
                "summary": "Current plan contains sort or hash operators; consider intermediate result size and memory cost.",
                "evidence": "sort/hash operator detected",
            }
        )

    join_count = sum(1 for operator in operators if "join" in operator)
    if join_count >= 2:
        signals.append(
            {
                "key": "join_expansion_risk",
                "severity": "warning",
                "summary": "Current plan has multiple JOIN layers; verify join order, filter conditions, and cardinality expansion risk.",
                "evidence": f"join_count={join_count}",
            }
        )

    if explain_source == "unavailable":
        signals.append(
            {
                "key": "plan_explain_unavailable",
                "severity": "info",
                "summary": "Realtime explain is unavailable; diagnosis can only proceed with limited evidence.",
                "evidence": "explain_source=unavailable",
            }
        )

    if not current_plans:
        signals.append(
            {
                "key": "plan_cache_missing",
                "severity": "info",
                "summary": "No readable plan cache records found; plan facts may be incomplete.",
                "evidence": "current_plan_count=0",
            }
        )

    if any(
        func in text for func in ("substr(", "substring(", "lower(", "upper(", "date(", "cast(")
    ):
        signals.append(
            {
                "key": "predicate_not_sargable",
                "severity": "info",
                "summary": "SQL text contains function-wrapped predicates, which may reduce index usability.",
                "evidence": "function_wrapped_predicate_detected",
            }
        )

    signals.append(
        {
            "key": "history_unavailable",
            "severity": "info",
            "summary": "Current stage provides realtime profiling only; historical trends, execution counts, and regression detection are unavailable.",
            "evidence": "live_mode_only",
        }
    )
    return signals
