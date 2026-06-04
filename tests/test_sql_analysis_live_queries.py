import pytest

import app.services.sql_analysis.live.queries as live_queries
from app.schemas import schemas
from app.services.sql_analysis.live.queries import (
    LiveCategoryQuery,
    LiveDbNamesQuery,
    LiveSqlProfileQuery,
)
from app.services.sql_analysis.live.queries_ob import (
    _audit_filters,
    _parse_last_active_time_us,
    _profile_filters,
)
from app.services.sql_analysis.live.queries import (
    get_live_plan_explain,
    list_live_plan_history,
    list_live_db_names,
    list_live_sql_profiles,
)


def test_profile_filters_exclude_blank_db_and_sql_text_by_default():
    clauses, params = _profile_filters(
        "a",
        LiveSqlProfileQuery(
            start_time_us=1,
            end_time_us=2,
        ),
    )

    assert "a.query_sql IS NOT NULL" in clauses
    assert "TRIM(a.query_sql) <> ''" in clauses
    assert "a.db_name IS NOT NULL" in clauses
    assert "TRIM(a.db_name) <> ''" in clauses
    assert any("LOWER(a.db_name) NOT IN" in clause for clause in clauses)
    assert "oceanbase" in params


def test_audit_filters_keep_non_empty_sql_text_even_with_explicit_db():
    clauses, params = _audit_filters(
        "a",
        LiveCategoryQuery(
            category=schemas.SqlMonitorCategory.TOP_SQL,
            start_time_us=1,
            end_time_us=2,
            db_name="test",
        ),
    )

    assert "a.query_sql IS NOT NULL" in clauses
    assert "TRIM(a.query_sql) <> ''" in clauses
    assert "a.db_name = %s" in clauses
    assert "TRIM(a.db_name) <> ''" not in clauses
    assert params[-1] == "test"


def test_parse_last_active_time_us_accepts_mysql_datetime_text():
    value = _parse_last_active_time_us("2026-03-28 09:00:00.000000")

    assert isinstance(value, int)
    assert value > 0


class _DummyDatasource:
    db_type = "oceanbase"
    tenant_role = "user"
    id = 1
    cluster_key = "cluster-live"
    host = "127.0.0.1"
    port = 2881


class _RecordingPool:
    def __init__(self):
        self.query_calls: list[tuple[str, str, list | None]] = []
        self.explain_calls: list[tuple[str, str]] = []

    async def execute_query(self, datasource, sql, role="user", params=None):  # noqa: ANN001, ARG002
        self.query_calls.append((role, params))
        if "sql_analysis_live:recent_sql_metadata" in sql:
            return {
                "rows": [
                    {
                        "tenant_id": 1002,
                        "sql_id": "sql-top-1",
                        "db_name": "app_db",
                        "user_name": "app_user",
                        "sql_text": "select * from app_db.t1",
                        "latest_request_time_us": 1711616400000000,
                    }
                ],
                "columns": [],
                "row_count": 1,
            }
        if "sql_analysis_live:recent_sql" in sql:
            return {
                "rows": [
                    {
                        "tenant_id": 1002,
                        "tenant_name": None,
                        "db_name": None,
                        "user_name": None,
                        "sql_id": "sql-top-1",
                        "sql_text": "select * from t1",
                        "latest_last_active_time": "2026-03-28 09:00:00.000000",
                        "plan_count": 2,
                    }
                ],
                "columns": [],
                "row_count": 1,
            }
        if "sql_analysis_live:db_names" in sql:
            return {"rows": [{"db_name": "biz"}], "columns": ["db_name"], "row_count": 1}
        return {"rows": [], "columns": [], "row_count": 0}

    async def execute_explain(self, datasource, sql, role="user", database=None):  # noqa: ANN001, ARG002
        self.explain_calls.append(role)
        return {"rows": [{"select_type": "SIMPLE", "table": "t1", "rows": 1, "Extra": "Using where"}]}


@pytest.mark.anyio
async def test_list_live_db_names_uses_sql_analysis_live_pool(monkeypatch):
    pool = _RecordingPool()
    seen_groups: list[str] = []

    def _fake_get_db_pool(group="default"):
        seen_groups.append(group)
        return pool

    monkeypatch.setattr("app.db.connection.get_db_pool", _fake_get_db_pool)

    result = await list_live_db_names(
        _DummyDatasource(),
        LiveDbNamesQuery(start_time_us=1, end_time_us=2),
    )

    assert result == ["biz"]
    assert seen_groups == [live_queries.SQL_ANALYSIS_LIVE_POOL_GROUP]


@pytest.mark.anyio
async def test_list_live_sql_profiles_hydrates_db_and_user_from_audit(monkeypatch):
    pool = _RecordingPool()
    seen_groups: list[str] = []

    def _fake_get_db_pool(group="default"):
        seen_groups.append(group)
        return pool

    monkeypatch.setattr("app.db.connection.get_db_pool", _fake_get_db_pool)

    result = await list_live_sql_profiles(
        _DummyDatasource(),
        LiveSqlProfileQuery(start_time_us=1, end_time_us=2),
    )

    assert result[0]["db_name"] == "app_db"
    assert result[0]["user_name"] == "app_user"
    assert result[0]["latest_request_time_us"] == 1711616400000000
    assert seen_groups == [
        live_queries.SQL_ANALYSIS_LIVE_POOL_GROUP,
        live_queries.SQL_ANALYSIS_LIVE_POOL_GROUP,
    ]


@pytest.mark.anyio
async def test_get_live_plan_explain_uses_sql_analysis_live_pool_for_explain(monkeypatch):
    pool = _RecordingPool()
    seen_groups: list[str] = []

    def _fake_get_db_pool(group="default"):
        seen_groups.append(group)
        return pool

    monkeypatch.setattr("app.db.connection.get_db_pool", _fake_get_db_pool)

    source, rows = await get_live_plan_explain(
        _DummyDatasource(),
        sql_id="sql-1",
        sql_text="select * from t1",
    )

    assert source == "explain_sql"
    assert len(rows) == 1
    assert seen_groups == [live_queries.SQL_ANALYSIS_LIVE_POOL_GROUP]




@pytest.mark.anyio
async def test_list_live_plan_history_maps_zero_plan_hash_to_none(monkeypatch):
    class _PlanHistoryPool:
        async def execute_query(self, datasource, sql, role="user", params=None):  # noqa: ANN001, ARG002
            normalized_sql = " ".join(sql.split()).lower()
            if "from oceanbase.gv$ob_sql_audit" in normalized_sql and "group by tenant_id, sql_id, plan_id" in normalized_sql:
                return {
                    "rows": [
                        {
                            "tenant_id": 1002,
                            "sql_id": "sql-1",
                            "plan_id": 10001,
                            "plan_hash": 0,
                            "executions": 2,
                            "avg_exe_usec": 12.0,
                            "elapsed_time": 120,
                            "execute_time": 80,
                            "table_scan": 0,
                            "last_active_time": "2026-03-28 09:00:00.000000",
                            "query_sql": "select * from t1",
                        }
                    ],
                    "columns": [],
                    "row_count": 1,
                }
            return {"rows": [], "columns": [], "row_count": 0}

    monkeypatch.setattr("app.db.connection.get_db_pool", lambda group="default": _PlanHistoryPool())

    rows = await list_live_plan_history(
        _DummyDatasource(),
        sql_id="sql-1",
        start_time_us=1,
        end_time_us=2,
        tenant_id=1002,
    )

    assert len(rows) == 1
    assert rows[0]["plan_hash"] is None


