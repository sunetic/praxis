from __future__ import annotations

from typing import Any

from app.schemas import schemas
from app.services.sql_analysis.history.queries import MonitorCategoryQuery, list_monitor_category


async def detect_monitor_matched_categories(
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    datasource_id: int | None = None,
) -> list[str]:
    matched_categories: list[str] = []
    for current_category in schemas.SqlMonitorCategory:
        query = MonitorCategoryQuery(
            category=current_category,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            limit=50,
            datasource_id=datasource_id,
            sql_id=sql_id,
        )
        try:
            rows, _, _ = await list_monitor_category(query)
        except Exception:
            rows = []
        if any(str(row.get("sql_id") or "") == sql_id for row in rows):
            matched_categories.append(current_category.value)
    return matched_categories


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_monitor_signals(
    *,
    category: schemas.SqlMonitorCategory,
    matched_categories: list[str],
    facts: dict[str, Any] | None,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    execution = dict((facts or {}).get("execution") or {})
    plan = dict((facts or {}).get("plan") or {})

    if category == schemas.SqlMonitorCategory.SLOW_SQL or "slow_sql" in matched_categories:
        signals.append(
            {
                "key": "slow_sql",
                "severity": "warning",
                "summary": "当前 SQL 命中 SlowSQL 视角，平均耗时偏高。",
                "evidence": f"avg_elapsed_time_us={execution.get('avg_elapsed_time_us')}",
            }
        )
    if category == schemas.SqlMonitorCategory.REGRESSED_SQL or "regressed_sql" in matched_categories:
        signals.append(
            {
                "key": "regressed_sql",
                "severity": "warning",
                "summary": "当前 SQL 命中回归视角，与历史窗口相比性能退化。",
                "evidence": "matched_category=regressed_sql",
            }
        )
    if category == schemas.SqlMonitorCategory.NEW_SQL or "new_sql" in matched_categories:
        signals.append(
            {
                "key": "new_sql",
                "severity": "info",
                "summary": "当前 SQL 命中新 SQL 视角，建议结合发布时间与变更记录核对来源。",
                "evidence": "matched_category=new_sql",
            }
        )
    if category == schemas.SqlMonitorCategory.TOP_SQL or "top_sql" in matched_categories:
        signals.append(
            {
                "key": "top_sql",
                "severity": "info",
                "summary": "当前 SQL 位于高频或高耗时观察集内，适合作为优先分析对象。",
                "evidence": f"executions={execution.get('executions')}",
            }
        )
    if _safe_int(plan.get("plan_count")) > 1 or "plan_changed_sql" in matched_categories:
        signals.append(
            {
                "key": "plan_changed",
                "severity": "warning",
                "summary": "当前 SQL 在观察窗口内存在多个执行计划。",
                "evidence": f"plan_count={plan.get('plan_count')}",
            }
        )
    if plan.get("latest_plan_id") is None:
        signals.append(
            {
                "key": "no_plan_history",
                "severity": "info",
                "summary": "当前 SQL 在监控库中未查询到 plan history。",
                "evidence": "latest_plan_id=None",
            }
        )
    if _safe_int(plan.get("explain_item_count")) == 0:
        signals.append(
            {
                "key": "no_plan_explain",
                "severity": "info",
                "summary": "当前 SQL 在监控库中未查询到 plan explain。",
                "evidence": "explain_item_count=0",
            }
        )
    return signals
