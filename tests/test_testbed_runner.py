from types import SimpleNamespace
from typing import Any

import pymysql

from testbed import runner


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_run_workload_loop_retries_after_broken_reconnect(monkeypatch: Any, capsys: Any) -> None:
    datasource = SimpleNamespace(database="test")
    first_conn = _FakeConn()
    second_conn = _FakeConn()
    connect_attempts = iter(
        [
            first_conn,
            pymysql.err.OperationalError(2013, "timed out"),
            second_conn,
        ]
    )
    step_results = iter(
        [
            pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query"),
            runner.RuntimeStats(total=1, success=1, failure=0, slow=0, total_ms=12.0, max_ms=12.0),
        ]
    )
    times = iter([0.0, 0.0, 0.1, 0.3, 0.6, 0.8, 1.2, 1.4, 1.6])

    monkeypatch.setattr(runner, "get_datasource", lambda datasource_id, database_override: datasource)

    def fake_connect_datasource(ds: Any) -> Any:
        value = next(connect_attempts)
        if isinstance(value, Exception):
            raise value
        return value

    def fake_execute_workload_once(conn: Any, **kwargs: Any) -> runner.RuntimeStats:
        value = next(step_results)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(runner, "connect_datasource", fake_connect_datasource)
    monkeypatch.setattr(runner, "execute_workload_once", fake_execute_workload_once)
    monkeypatch.setattr(runner.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.logger, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.time, "time", lambda: next(times))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)

    runner.run_workload_loop(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        interval_seconds=0.1,
        problem_ratio=0.2,
        slow_threshold_ms=500,
        duration_seconds=1,
        scenario_enabled=False,
    )

    assert first_conn.closed is True
    assert second_conn.closed is True
    assert "{'total': 1, 'success': 1, 'failure': 0, 'slow': 0, 'avg_ms': 12.0, 'max_ms': 12.0}" in capsys.readouterr().out


def test_build_parser_supports_case_command() -> None:
    parser = runner.build_parser()

    args = parser.parse_args(["--datasource-id", "2", "case", "--case-name", "multi_plan_sql"])

    assert args.command == "case"
    assert args.case_name == "multi_plan_sql"


def test_build_parser_case_default_is_all() -> None:
    parser = runner.build_parser()
    args = parser.parse_args(["--datasource-id", "2", "case"])
    assert args.case_name == "all"


def test_build_parser_run_defaults_include_scenario_maintenance() -> None:
    parser = runner.build_parser()
    args = parser.parse_args(["--datasource-id", "2", "run"])
    assert args.scenario_enabled == "true"
    assert args.scenario_case == "all"
    assert args.scenario_refresh_seconds == 300


def test_rotate_disabled_windows_skips_when_three_windows_already_disabled(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(
        runner,
        "_fetch_window_rows",
        lambda connection: [
            {"job_name": "MONDAY_WINDOW", "enabled": "FALSE"},
            {"job_name": "TUESDAY_WINDOW", "enabled": "FALSE"},
            {"job_name": "WEDNESDAY_WINDOW", "enabled": "FALSE"},
            {"job_name": "THURSDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "FRIDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "SATURDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "SUNDAY_WINDOW", "enabled": "TRUE"},
        ],
    )
    monkeypatch.setattr(runner, "execute", lambda *args, **kwargs: execute_calls.append(args[1]))

    disabled, enabled, skipped = runner.rotate_disabled_windows(conn)

    assert skipped is True
    assert disabled == []
    assert enabled == []
    assert execute_calls == []



def test_rotate_disabled_windows_disables_up_to_three_total(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(
        runner,
        "_fetch_window_rows",
        lambda connection: [
            {"job_name": "MONDAY_WINDOW", "enabled": "FALSE"},
            {"job_name": "TUESDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "WEDNESDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "THURSDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "FRIDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "SATURDAY_WINDOW", "enabled": "TRUE"},
            {"job_name": "SUNDAY_WINDOW", "enabled": "TRUE"},
        ],
    )
    monkeypatch.setattr(runner.random, "sample", lambda population, k: ["TUESDAY_WINDOW", "WEDNESDAY_WINDOW"])

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)

    disabled, enabled, skipped = runner.rotate_disabled_windows(conn)

    assert skipped is False
    assert disabled == ["TUESDAY_WINDOW", "WEDNESDAY_WINDOW"]
    assert enabled == []
    assert execute_calls == [
        "CALL dbms_scheduler.disable('TUESDAY_WINDOW')",
        "CALL dbms_scheduler.disable('WEDNESDAY_WINDOW')",
    ]



def test_run_multi_plan_case_uses_flush_path(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(
        runner,
        "run_init",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"sql_id": "sql-plan", "plan_hash": "1001", "sample_count": 2, "latest_request_time_us": 10},
            {"sql_id": "sql-plan", "plan_hash": "2002", "sample_count": 1, "latest_request_time_us": 11},
        ],
    )

    result = runner.run_multi_plan_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=2,
    )

    assert result.gather_stats_status == "ok"
    assert result.reparse_mode == "flush_plan_cache"
    assert result.multiple_plans is True
    assert "ALTER SYSTEM FLUSH PLAN CACHE" in execute_calls
    assert conn.closed is True


def test_run_multi_plan_case_falls_back_when_flush_unavailable(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)
        if sql == "ALTER SYSTEM FLUSH PLAN CACHE":
            raise pymysql.err.OperationalError(1227, "access denied")

    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"sql_id": "sql-a", "plan_hash": "1001", "sample_count": 2, "latest_request_time_us": 10},
            {"sql_id": "sql-b", "plan_hash": "2002", "sample_count": 1, "latest_request_time_us": 11},
        ],
    )

    result = runner.run_multi_plan_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
    )

    assert result.reparse_mode == "use_plan_cache_none"
    assert result.multiple_plans is False
    assert "multiple_plan_hashes_seen_across_sql_variants" in result.notes
    assert any("USE_PLAN_CACHE(NONE)" in sql for sql in execute_calls)
    assert conn.closed is True


def test_run_true_multi_plan_case_keeps_same_sql_and_requires_distinct_hash(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"sql_id": "sql-plan", "plan_hash": "1001", "sample_count": 2, "latest_request_time_us": 10},
            {"sql_id": "sql-plan", "plan_hash": "1001", "sample_count": 1, "latest_request_time_us": 11},
        ],
    )

    result = runner.run_true_multi_plan_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=2,
    )

    assert result.reparse_mode == "flush_plan_cache"
    assert result.multiple_plans is False
    assert "true_multi_plan_not_observed" in result.notes
    assert not any("USE_PLAN_CACHE(NONE)" in sql for sql in execute_calls)
    assert any("data_shift_injected=mod4_selectivity_rewrite" in note for note in result.notes)
    assert conn.closed is True


def test_run_case_rejects_unknown_case(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override=None: SimpleNamespace(database="test"),
    )
    try:
        runner.run_case(
            datasource_id=2,
            database_override="test",
            prefix="tb_",
            target_rows=1000,
            batch_size=100,
            iterations=1,
            case_name="unknown_case",
        )
    except runner.TestbedError as exc:
        assert "unsupported case" in str(exc)
    else:
        raise AssertionError("expected TestbedError for unknown case")


def test_run_stats_stale_tables_case(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)

    call_count = {"fetch_one": 0}

    def fake_fetch_one(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, int]:
        call_count["fetch_one"] += 1
        return {"cnt": 42}

    monkeypatch.setattr(runner, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"owner": "test", "table_name": "tb_customers", "last_analyzed": "2026-01-01", "stats_state": "STALE_STATS"},
            {"owner": "test", "table_name": "tb_orders", "last_analyzed": None, "stats_state": "MISSING_STATS"},
        ],
    )

    result = runner.run_stats_stale_tables_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
    )

    assert result.case_name == "stats_stale_tables"
    assert len(result.evidence_rows) == 2
    assert result.multiple_plans is True  # Indicates stale tables found.
    assert any("gathered:tb_customers" in n for n in result.notes)
    assert any("no_gather:tb_orders" in n for n in result.notes)
    assert conn.closed is True


def test_run_stats_dml_heavy_case(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(runner, "fetch_one", lambda connection, sql, params=None: {"cnt": 150000})
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"table_name": "tb_orders", "inserts": 0, "updates": 150000, "deletes": 0, "total_changes": 150000},
            {"table_name": "tb_customers", "inserts": 0, "updates": 60000, "deletes": 0, "total_changes": 60000},
        ],
    )

    result = runner.run_stats_dml_heavy_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
    )

    assert result.case_name == "stats_dml_heavy"
    assert len(result.evidence_rows) == 2
    assert result.multiple_plans is True  # Indicates DML changes detected.
    assert any("dml_mutation:tb_orders" in n for n in result.notes)
    assert any("dml_mutation:tb_customers" in n for n in result.notes)
    assert conn.closed is True


def test_run_stats_gather_verify_case(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)

    fetch_call_count = {"n": 0}

    def fake_fetch_all(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        fetch_call_count["n"] += 1
        if fetch_call_count["n"] == 1:
            # col stats
            return [
                {"owner": "test", "table_name": "tb_customers", "column_name": "city", "num_distinct": 5, "num_buckets": 5, "histogram": "FREQUENCY", "sample_size": 100000, "last_analyzed": "2026-04-04"},
                {"owner": "test", "table_name": "tb_customers", "column_name": "status", "num_distinct": 3, "num_buckets": 0, "histogram": "NONE", "sample_size": 100000, "last_analyzed": "2026-04-04"},
            ]
        # histogram
        return [
            {"owner": "test", "table_name": "tb_customers", "column_name": "city", "bucket_cnt": 5, "max_bucket_repeat": 40000, "total_repeat": 100000, "top_bucket_ratio": 0.4},
        ]

    monkeypatch.setattr(runner, "fetch_all", fake_fetch_all)

    result = runner.run_stats_gather_verify_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
    )

    assert result.case_name == "stats_gather_verify"
    assert result.gather_stats_status == "ok"
    assert len(result.evidence_rows) == 2
    assert result.evidence_rows[0]["type"] == "col_stats"
    assert result.evidence_rows[1]["type"] == "histogram"
    assert any("col_stats_rows=2" in n for n in result.notes)
    assert any("histogram_rows=1" in n for n in result.notes)
    assert any("cols_with_histogram=1" in n for n in result.notes)
    assert result.multiple_plans is True  # Indicates col stats found.
    assert conn.closed is True


def test_resolve_environment_spec_supports_legacy_alias() -> None:
    spec = runner.resolve_environment_spec("stats_stale_tables")

    assert spec.environment_name == "stats_stale_after_10pct_dml"
    assert spec.case_name == "stats_stale_tables"
    assert spec.stats_profile == "stale_stats"


def test_resolve_environment_spec_rejects_invalid_profile() -> None:
    try:
        runner.resolve_environment_spec("multi_plan_sql", scheduler_profile="bad_profile")
    except runner.TestbedError as exc:
        assert "invalid scheduler_profile" in str(exc)
    else:
        raise AssertionError("expected TestbedError for invalid scheduler_profile")


def test_resolve_environment_spec_supports_new_timeout_and_window_templates() -> None:
    timeout_spec = runner.resolve_environment_spec("stats_large_table_timeout_4012")
    window_spec = runner.resolve_environment_spec("stats_window_insufficient")
    true_multi_spec = runner.resolve_environment_spec("stats_true_multi_plan")

    assert timeout_spec.case_name == "stats_large_table_timeout_4012"
    assert timeout_spec.table_profile == "transactions_skew"
    assert window_spec.case_name == "stats_window_insufficient"
    assert window_spec.scheduler_profile == "window_insufficient"
    assert true_multi_spec.case_name == "true_multi_plan_sql"
    assert true_multi_spec.ops_profile == "plan_cache_reparse"


def test_run_case_requires_plan_cache_side_effect_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override=None: SimpleNamespace(database="test"),
    )
    try:
        runner.run_case(
            datasource_id=2,
            database_override="test",
            prefix="tb_",
            target_rows=1000,
            batch_size=100,
            iterations=1,
            case_name="stats_plan_cache_reparse",
            allow_plan_cache_side_effect=False,
        )
    except runner.TestbedError as exc:
        assert "requires --allow-plan-cache-side-effect=true" in str(exc)
    else:
        raise AssertionError("expected TestbedError when plan-cache side effect is not explicitly allowed")


def test_run_case_prints_environment_verification_payload(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override=None: SimpleNamespace(database="test"),
    )
    fake_handler = lambda **kwargs: runner.CaseResult(
        case_name="stats_stale_tables",
        target_sql="SELECT 1",
        evidence_rows=[{"k": "v"}],
        notes=["ok"],
    )
    monkeypatch.setitem(runner.CASE_HANDLERS, "stats_stale_tables", fake_handler)

    runner.run_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
        case_name="stats_stale_tables",
    )

    out = capsys.readouterr().out
    assert "environment_name" in out
    assert "verification_status" in out
    assert "stats_stale_after_10pct_dml" in out


def test_case_handlers_include_new_stats_environments() -> None:
    assert "stats_large_table_timeout_4012" in runner.CASE_HANDLERS
    assert "stats_window_insufficient" in runner.CASE_HANDLERS
    assert "true_multi_plan_sql" in runner.CASE_HANDLERS


def test_verification_payload_requires_signal_for_negative_templates() -> None:
    spec = runner.EnvironmentSpec(
        environment_name="stats_window_insufficient",
        case_name="stats_window_insufficient",
    )
    setattr(spec, "_runtime_datasource_id", 1)
    setattr(spec, "_runtime_db_name", "test")
    setattr(spec, "_runtime_l2_table", "tb_customers")
    result = runner.CaseResult(
        case_name="stats_window_insufficient",
        target_sql="SELECT 1",
        evidence_rows=[{"job_name": "MONDAY_WINDOW"}],
        notes=["window_rows=7", "disabled_windows=0"],
        errors=[],
    )
    result.multiple_plans = False

    payload = runner._build_verification_payload(spec, result)

    assert payload["verification_status"] == "fail"
    assert any(
        "expected_environment_signal_missing:stats_window_insufficient" in e
        for e in payload["errors"]
    )

    multi_plan_spec = runner.EnvironmentSpec(
        environment_name="stats_true_multi_plan",
        case_name="true_multi_plan_sql",
    )
    setattr(multi_plan_spec, "_runtime_datasource_id", 1)
    setattr(multi_plan_spec, "_runtime_db_name", "test")
    setattr(multi_plan_spec, "_runtime_l2_table", "tb_customers")
    multi_plan_result = runner.CaseResult(
        case_name="true_multi_plan_sql",
        target_sql="SELECT 1",
        evidence_rows=[{"sql_id": "sql-plan", "plan_hash": "1001"}],
        notes=[],
        errors=[],
    )
    multi_plan_result.multiple_plans = False

    multi_plan_payload = runner._build_verification_payload(multi_plan_spec, multi_plan_result)

    assert multi_plan_payload["verification_status"] == "fail"
    assert any(
        "expected_environment_signal_missing:stats_true_multi_plan" in e
        for e in multi_plan_payload["errors"]
    )


def test_run_distributed_plan_case_detects_plan_type_3(monkeypatch: Any) -> None:
    conn = _FakeConn()
    execute_calls: list[str] = []

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)

    def fake_execute(connection: Any, sql: str, params: tuple[Any, ...] | None = None) -> None:
        execute_calls.append(sql)

    monkeypatch.setattr(runner, "execute", fake_execute)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"sql_id": "sql-dist", "plan_hash": "3001", "plan_type": 3, "sample_count": 5, "latest_request_time_us": 100, "max_elapsed_us": 5000},
            {"sql_id": "sql-dist", "plan_hash": "3002", "plan_type": 3, "sample_count": 2, "latest_request_time_us": 200, "max_elapsed_us": 3000},
        ],
    )

    result = runner.run_distributed_plan_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=2,
    )

    assert result.case_name == "distributed_plan"
    assert result.multiple_plans is True
    assert any("distributed_plan_rows=2" in n for n in result.notes)
    assert len(result.sql_ids) == 1
    assert len(result.plan_hashes) == 2
    assert conn.closed is True


def test_run_distributed_plan_case_no_distributed_plan_observed(monkeypatch: Any) -> None:
    conn = _FakeConn()

    monkeypatch.setattr(runner, "run_init", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "get_datasource",
        lambda datasource_id, database_override: SimpleNamespace(database="test"),
    )
    monkeypatch.setattr(runner, "connect_datasource", lambda ds: conn)
    monkeypatch.setattr(runner, "execute", lambda connection, sql, params=None: None)
    monkeypatch.setattr(
        runner,
        "fetch_all",
        lambda connection, sql, params=None: [
            {"sql_id": "sql-local", "plan_hash": "1001", "plan_type": 1, "sample_count": 3, "latest_request_time_us": 100, "max_elapsed_us": 2000},
        ],
    )

    result = runner.run_distributed_plan_case(
        datasource_id=2,
        database_override="test",
        prefix="tb_",
        target_rows=1000,
        batch_size=100,
        iterations=1,
    )

    assert result.case_name == "distributed_plan"
    assert result.multiple_plans is False
    assert any("distributed_plan_not_observed" in n for n in result.notes)
    assert any("distributed_plan_rows=0" in n for n in result.notes)
    assert conn.closed is True


def test_resolve_environment_spec_supports_distributed_template() -> None:
    spec = runner.resolve_environment_spec("distributed_plan_cross_join")

    assert spec.environment_name == "distributed_plan_cross_join"
    assert spec.case_name == "distributed_plan"
    assert spec.table_profile == "distributed"
    assert spec.ops_profile == "safe"
    assert spec.allow_plan_cache_side_effect is False
