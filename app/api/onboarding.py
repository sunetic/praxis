from __future__ import annotations

import pathlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.database import get_db
from app.services.platform.settings_store import get_setting, upsert_setting

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])
logger = get_logger("api.onboarding")

_INIT_SQL_PATH = pathlib.Path(__file__).parent.parent.parent / "deploy" / "mariadb" / "init.sql"

_ONBOARDING_KEY = "onboarding_completed"


def is_onboarding_completed(db: Session) -> bool:
    if not get_setting(db, _ONBOARDING_KEY):
        return False
    return all(
        isinstance(value, str) and bool(value.strip())
        for value in (get_setting(db, "ai_api_key"), get_setting(db, "ai_model"))
    )


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

    if not isinstance(llm_config, dict):
        raise HTTPException(status_code=422, detail="llm_config must be an object")

    required = ("llm_api_key", "llm_model")
    missing = [
        key
        for key in required
        if not isinstance(llm_config.get(key), str) or not llm_config[key].strip()
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required LLM configuration: {', '.join(missing)}",
        )

    llm_config = {
        key: value.strip() if isinstance(value, str) else value for key, value in llm_config.items()
    }

    # New logical runs snapshot these settings; existing runs keep their config.
    if "llm_api_key" in llm_config:
        upsert_setting(db, "ai_api_key", llm_config["llm_api_key"])
    if "llm_model" in llm_config:
        upsert_setting(db, "ai_model", llm_config["llm_model"])
    if "llm_base_url" in llm_config:
        upsert_setting(db, "ai_base_url", llm_config["llm_base_url"])
    # Also store provider for display purposes
    if "llm_provider" in llm_config:
        upsert_setting(db, "llm_provider", llm_config["llm_provider"])

    upsert_setting(db, _ONBOARDING_KEY, True)
    db.commit()

    # Initialize monitordb schema (EE-only — skipped if collector module absent)
    try:
        await _init_monitordb()
    except ImportError:
        pass

    # Trigger scheduler startup if not already running
    try:
        from app.core.config import get_settings
        from app.services.scheduler.runtime_state import get_scheduler_worker

        worker = get_scheduler_worker()
        if (
            get_settings().scheduler_autostart
            and worker is not None
            and not worker.health()["running"]
        ):
            await worker.start()
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
