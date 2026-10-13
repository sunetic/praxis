"""Periodic cleanup of expired spans from the tracing SQLite database."""

import sqlite3
import time

from app.core.logging import get_logger

logger = get_logger("app.tracing.cleanup")


def purge_expired_spans(
    db_path: str,
    retention_hours: int = 24,
    *,
    batch_size: int = 5_000,
    max_batches: int = 20,
) -> int:
    """Delete expired spans in bounded transactions and reclaim reusable pages."""
    if retention_hours <= 0 or batch_size <= 0 or max_batches <= 0:
        raise ValueError("Trace retention and cleanup batch limits must be positive")
    cutoff_ns = int((time.time() - retention_hours * 3600) * 1e9)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        deleted = 0
        for _ in range(max_batches):
            cursor = conn.execute(
                """
                DELETE FROM spans
                WHERE rowid IN (
                    SELECT rowid FROM spans
                    WHERE start_time_ns < ?
                    ORDER BY start_time_ns
                    LIMIT ?
                )
                """,
                (cutoff_ns, batch_size),
            )
            batch_deleted = max(cursor.rowcount, 0)
            conn.commit()
            deleted += batch_deleted
            if batch_deleted < batch_size:
                break
        if deleted > 0:
            logger.info(
                "tracing_cleanup_ok deleted=%d retention_hours=%d", deleted, retention_hours
            )
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                conn.execute("PRAGMA incremental_vacuum(1000)")
            except Exception as maintenance_error:
                logger.warning("tracing_space_reclaim_deferred error=%s", maintenance_error)
        return deleted
    except Exception as e:
        logger.warning("tracing_cleanup_fail error=%s", e)
        return 0
    finally:
        if conn is not None:
            conn.close()
