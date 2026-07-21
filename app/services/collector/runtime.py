from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import models
from app.services.collector import cleanup, sql_audit
from app.services.datasource.router import DataSourceRoutingError, resolve_collector_datasource
from app.services.scheduler.result import ScheduleRuntimeResult

logger = get_logger("collector.runtime")

_VALID_MODES = {"all", "threshold", "watchlist", "sample", "cleanup"}
_SQL_AUDIT_MODES = {"all", "threshold", "watchlist", "sample"}


class CollectorRuntimeService:
    def __init__(self, *, session_factory: sessionmaker[Session] | Any) -> None:
        self._session_factory = session_factory

    def _list_collectable_datasources(self, db: Session) -> list[models.DataSource]:
        rows = (
            db.query(models.DataSource)
            .filter(
                models.DataSource.status == "active",
                models.DataSource.tenant_role != "api",
            )
            .order_by(models.DataSource.id.asc())
            .all()
        )
        return [item for item in rows if (item.tenant_role or "user").strip().lower() != "sys"]

    async def _collect_for_datasource(
        self,
        source_ds: models.DataSource,
        target_ds: models.DataSource,
        *,
        mode: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if mode in _SQL_AUDIT_MODES:
            result["sql_audit"] = await sql_audit.run_sql_audit_collection(source_ds, target_ds)
        return result

    async def invoke(
        self,
        schedule: models.Schedule,
        *,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ScheduleRuntimeResult:
        started = time.monotonic()
        run_id = trace_id or str(uuid.uuid4())
        effective_payload = payload if isinstance(payload, dict) else {}

        mode = str(effective_payload.get("mode") or "all").strip().lower()
        if mode not in _VALID_MODES:
            return ScheduleRuntimeResult(
                run_id=run_id,
                status="failed",
                output=None,
                output_summary=None,
                error_class="validation",
                error_message=f"invalid collector mode: {mode}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        db = self._session_factory()
        try:
            target_ds = resolve_collector_datasource(db, "")
            source_datasources = [] if mode == "cleanup" else self._list_collectable_datasources(db)
        except DataSourceRoutingError as err:
            return ScheduleRuntimeResult(
                run_id=run_id,
                status="failed",
                output=None,
                output_summary=None,
                error_class="validation",
                error_message=str(err),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            db.close()

        try:
            if mode == "cleanup":
                result = {"cleanup": await cleanup.run_cleanup(target_ds)}
                summary = json.dumps(result, ensure_ascii=False, default=str)[:1000]
                return ScheduleRuntimeResult(
                    run_id=run_id,
                    status="success",
                    output=result,
                    output_summary=summary,
                    error_class=None,
                    error_message=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            items: list[dict[str, Any]] = []
            failures = 0
            for source_ds in source_datasources:
                try:
                    logger.info("collector using datasource id=%s role=%s", source_ds.id, source_ds.tenant_role or "user")
                    item_result = await self._collect_for_datasource(source_ds, target_ds, mode=mode)
                    items.append(
                        {
                            "datasource_id": source_ds.id,
                            "status": "success",
                            **item_result,
                        }
                    )
                except Exception as err:
                    failures += 1
                    items.append(
                        {
                            "datasource_id": source_ds.id,
                            "status": "failed",
                            "error_summary": str(err),
                        }
                    )

            output = {
                "mode": "batch",
                "collector_mode": mode,
                "datasource_count": len(source_datasources),
                "succeeded": len(source_datasources) - failures,
                "failed": failures,
                "items": items,
            }
            status = "success" if failures < len(source_datasources) else ("failed" if source_datasources else "success")
            error_message = None if status == "success" else f"collector {mode} batch failed for all datasources"
            summary = json.dumps(output, ensure_ascii=False, default=str)[:1000]
            return ScheduleRuntimeResult(
                run_id=run_id,
                status=status,
                output=output,
                output_summary=summary,
                error_class=None if status == "success" else "runtime",
                error_message=error_message,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as err:
            return ScheduleRuntimeResult(
                run_id=run_id,
                status="failed",
                output=None,
                output_summary=None,
                error_class="runtime",
                error_message=str(err),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
