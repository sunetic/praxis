from __future__ import annotations

import pathlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.database import get_db
from app.models.models import PlatformSetting

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])
logger = get_logger("api.onboarding")

_INIT_SQL_PATH = pathlib.Path(__file__).parent.parent.parent / "deploy" / "mariadb" / "init.sql"

_ONBOARDING_KEY = "onboarding_completed"


def is_onboarding_completed(db: Session) -> bool:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == _ONBOARDING_KEY).first()
    return bool(row and row.value)


def _upsert(db: Session, key: str, value: Any) -> None:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row is not None:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        db.add(PlatformSetting(key=key, value=value))


@router.get("/status")
def get_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    completed = is_onboarding_completed(db)
    return {"completed": completed}


@router.post("/complete")
async def complete_onboarding(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Save LLM config from onboarding and mark onboarding as completed.
    Also triggers scheduler startup if not already running.
    """
    llm_config = payload.get("llm_config", {})

    # Save using the keys that LLMClient reads: ai_base_url, ai_api_key, ai_model
    if "llm_api_key" in llm_config:
        _upsert(db, "ai_api_key", llm_config["llm_api_key"])
    if "llm_model" in llm_config:
        _upsert(db, "ai_model", llm_config["llm_model"])
    if "llm_base_url" in llm_config:
        _upsert(db, "ai_base_url", llm_config["llm_base_url"])
    # Also store provider for display purposes
    if "llm_provider" in llm_config:
        _upsert(db, "llm_provider", llm_config["llm_provider"])

    _upsert(db, _ONBOARDING_KEY, True)
    db.commit()

    # Initialize monitordb schema (EE-only — skipped if collector module absent)
    try:
        await _init_monitordb()
    except ImportError:
        pass

    # Reset LLM singleton so next request picks up the new config
    try:
        import app.services.llm as _llm_mod

        _llm_mod._llm_client = None
    except Exception:
        pass

    # Trigger scheduler startup if not already running
    try:
        from app.core.config import get_settings
        from app.services.scheduler.runtime_state import get_scheduler_worker, set_scheduler_worker
        from app.services.scheduler.worker import SchedulerWorker

        if get_scheduler_worker() is None:
            s = get_settings()
            worker = SchedulerWorker(
                refresh_interval_seconds=s.scheduler_refresh_interval_seconds,
                job_coalesce=s.scheduler_job_coalesce,
                job_misfire_grace_seconds=s.scheduler_job_misfire_grace_seconds,
                job_max_instances=s.scheduler_job_max_instances,
            )
            await worker.start()
            set_scheduler_worker(worker)
            logger.info("scheduler_started_after_onboarding")
    except Exception as exc:
        logger.warning("scheduler_start_failed_after_onboarding error=%s", exc)

    logger.info("onboarding_completed")
    return {"completed": True}


async def _init_monitordb() -> None:
    """Run init.sql against the configured monitordb. Idempotent (CREATE IF NOT EXISTS)."""
    from app.core.config import get_settings
    from app.db.connection import DBConnectionPool
    from app.db.database import SessionLocal
    from app.services.datasource.router import resolve_collector_datasource

    s = get_settings()
    if not s.monitor_db_host:
        logger.info("monitordb_init_skipped no MONITOR_DB_HOST configured")
        return

    if not _INIT_SQL_PATH.exists():
        logger.warning("monitordb_init_skipped init.sql not found at %s", _INIT_SQL_PATH)
        return

    sql_text = _INIT_SQL_PATH.read_text(encoding="utf-8")
    # Split on semicolons, skip empty/comment-only lines
    statements = [
        stmt.strip()
        for stmt in sql_text.split(";")
        if stmt.strip() and not stmt.strip().startswith("--")
    ]

    try:
        with SessionLocal() as db:
            target_ds = resolve_collector_datasource(db, cluster_key="__config__")
        pool = DBConnectionPool()
        for stmt in statements:
            try:
                await pool.execute_query(target_ds, stmt, params=[])
            except Exception as exc:
                logger.warning("monitordb_init_stmt_failed stmt_prefix=%s error=%s", stmt[:60], exc)
        logger.info("monitordb_init_done statements=%s", len(statements))
    except Exception as exc:
        logger.warning("monitordb_init_failed error=%s", exc)
