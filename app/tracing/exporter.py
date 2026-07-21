"""SQLite-backed span exporter for OpenTelemetry."""

import json
import sqlite3
import threading
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class SQLiteSpanExporter(SpanExporter):
    """Exports spans to a local SQLite database."""

    def __init__(self, db_path: str = "./tracing.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _ensure_table(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT,
                    start_time_ns INTEGER NOT NULL,
                    end_time_ns INTEGER NOT NULL,
                    status TEXT,
                    attributes TEXT,
                    resource TEXT,
                    PRIMARY KEY (trace_id, span_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans (trace_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spans_start ON spans (start_time_ns)"
            )
            conn.commit()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        rows = []
        for span in spans:
            ctx = span.get_span_context()
            parent = span.parent
            rows.append((
                format(ctx.trace_id, "032x"),
                format(ctx.span_id, "016x"),
                format(parent.span_id, "016x") if parent else None,
                span.name,
                span.kind.name if span.kind else None,
                span.start_time,
                span.end_time,
                span.status.status_code.name if span.status else None,
                json.dumps(dict(span.attributes) if span.attributes else {}),
                json.dumps(dict(span.resource.attributes) if span.resource else {}),
            ))
        try:
            with self._lock:
                conn = self._get_conn()
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO spans
                    (trace_id, span_id, parent_span_id, name, kind,
                     start_time_ns, end_time_ns, status, attributes, resource)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
