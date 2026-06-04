from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models.models import PlatformSetting

router = APIRouter(prefix="/settings", tags=["Settings"])
logger = get_logger("api.settings")

_DEFAULTS: dict[str, Any] = {
    "build_engine": "pi_lite",
    "external_cli_command": "",
    "external_cli_pre_flags": "",
    "external_cli_post_flags": "",
}


def _get_all(db: Session) -> dict[str, Any]:
    rows = db.query(PlatformSetting).all()
    result = dict(_DEFAULTS)
    for row in rows:
        result[row.key] = row.value
    return result


def get_setting(db: Session, key: str) -> Any:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row is not None:
        return row.value
    return _DEFAULTS.get(key)


@router.get("")
def list_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    result = _get_all(db)
    logger.info("list_settings %s", fmt_kv(count=len(result)))
    return result


@router.patch("")
def patch_settings(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    for key, value in payload.items():
        row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
        if row is not None:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            db.add(PlatformSetting(key=key, value=value))
    db.commit()
    result = _get_all(db)
    logger.info("patch_settings %s", fmt_kv(keys=list(payload.keys())))
    return result


@router.post("/test-engine")
def test_engine(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Probe the configured external CLI command via EngineProbeAgent."""
    command = str(payload.get("command") or "").strip()
    if not command:
        stored = get_setting(db, "external_cli_command")
        command = str(stored or "").strip()
    if not command:
        return {"ok": False, "message": "No CLI command configured"}

    from app.services.engine_probe_agent import get_engine_probe_agent

    agent = get_engine_probe_agent()
    result = agent.probe(command)
    logger.info(
        "test_engine %s",
        fmt_kv(command=command, ok=result.ok, flags=result.flags_added),
    )

    return {
        "ok": result.ok,
        "message": result.message,
        "suggested_command": result.suggested_command,
        "flags_added": result.flags_added,
        "env_issues": result.env_issues,
    }
