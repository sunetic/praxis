"""Periodic cleanup of expired spans from the tracing SQLite database."""

import sqlite3
import time

from app.core.logging import get_logger

logger = get_logger("app.tracing.cleanup")


def purge_expired_spans(db_path: str, retention_hours: int = 24) -> int:
    """Delete spans older than retention_hours. Returns number of rows deleted."""
    cutoff_ns = int((time.time() - retention_hours * 3600) * 1e9)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("DELETE FROM spans WHERE end_time_ns < ?", (cutoff_ns,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info(
                "tracing_cleanup_ok deleted=%d retention_hours=%d", deleted, retention_hours
            )
        return deleted
    except Exception as e:
        logger.warning("tracing_cleanup_fail error=%s", e)
        return 0
