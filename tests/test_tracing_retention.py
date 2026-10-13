import sqlite3
import time

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.tracing import setup
from app.tracing.cleanup import purge_expired_spans
from app.tracing.exporter import SQLiteSpanExporter


def _insert_span(conn: sqlite3.Connection, index: int, start_time_ns: int) -> None:
    conn.execute(
        """
        INSERT INTO spans
        (trace_id, span_id, parent_span_id, name, kind,
         start_time_ns, end_time_ns, status, attributes, resource)
        VALUES (?, ?, NULL, 'test', 'INTERNAL', ?, ?, 'OK', '{}', '{}')
        """,
        (f"{index:032x}", f"{index:016x}", start_time_ns, start_time_ns + 1),
    )


def test_tracing_is_disabled_by_default():
    assert Settings.model_fields["tracing_enabled"].default is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tracing_sample_rate": -0.1},
        {"tracing_sample_rate": 1.1},
        {"tracing_retention_hours": 0},
        {"tracing_cleanup_interval_seconds": 0},
    ],
)
def test_tracing_settings_reject_unsafe_limits(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_exporter_uses_incremental_vacuum_and_no_redundant_trace_index(tmp_path):
    path = tmp_path / "tracing.db"
    exporter = SQLiteSpanExporter(str(path))
    exporter.shutdown()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(spans)")}
    assert "idx_spans_start" in indexes
    assert "idx_spans_trace" not in indexes


def test_exporter_does_not_drop_legacy_index_during_startup(tmp_path):
    path = tmp_path / "tracing.db"
    exporter = SQLiteSpanExporter(str(path))
    exporter.shutdown()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE INDEX idx_spans_trace ON spans (trace_id)")

    exporter = SQLiteSpanExporter(str(path))
    exporter.shutdown()
    with sqlite3.connect(path) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(spans)")}
    assert "idx_spans_trace" in indexes


def test_fastapi_health_endpoint_is_excluded(monkeypatch):
    calls = []

    def instrument(_cls, app, **kwargs):
        calls.append((app, kwargs))

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", classmethod(instrument))
    app = object()
    setup._instrument_fastapi(app, excluded_urls="/health,/ready")

    assert calls == [(app, {"excluded_urls": "/health,/ready"})]


def test_cleanup_is_batched_and_keeps_unexpired_spans(tmp_path):
    path = tmp_path / "tracing.db"
    exporter = SQLiteSpanExporter(str(path))
    exporter.shutdown()
    now_ns = time.time_ns()
    old_ns = now_ns - 48 * 3600 * 1_000_000_000
    with sqlite3.connect(path) as conn:
        for index in range(7):
            _insert_span(conn, index, old_ns)
        for index in range(7, 9):
            _insert_span(conn, index, now_ns)
        conn.commit()

    assert purge_expired_spans(str(path), 24, batch_size=3, max_batches=2) == 6
    assert purge_expired_spans(str(path), 24, batch_size=3, max_batches=2) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retention_hours": 0},
        {"batch_size": 0},
        {"max_batches": 0},
    ],
)
def test_cleanup_rejects_unbounded_or_disabled_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        purge_expired_spans(str(tmp_path / "tracing.db"), **kwargs)
