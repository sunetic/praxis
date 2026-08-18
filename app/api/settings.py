from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models.models import PlatformSetting
from app.schemas.schemas import (
    PlatformSettingsResponse,
    PlatformSettingsUpdateRequest,
    SettingsEngineTestRequest,
    SettingsEngineTestResponse,
)

router = APIRouter(prefix="/settings", tags=["Settings"])
logger = get_logger("api.settings")

_DEFAULTS: dict[str, Any] = {
    "build_engine": "pi_lite",
    "external_cli_command": "",
    "external_cli_pre_flags": "",
    "external_cli_post_flags": "",
    "sql_allow_mutating": False,
    "context_window_tokens": 128_000,
    "context_compression_threshold_percent": 75,
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


@router.get("", response_model=PlatformSettingsResponse)
def list_settings(db: Session = Depends(get_db)) -> PlatformSettingsResponse:
    result = _get_all(db)
    result["praxis_edition"] = get_settings().praxis_edition
    logger.info("list_settings %s", fmt_kv(count=len(result)))
    return PlatformSettingsResponse.model_validate(result)


@router.patch("", response_model=PlatformSettingsResponse)
def patch_settings(
    payload: PlatformSettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> PlatformSettingsResponse:
    update_data = payload.model_dump(exclude_none=True)
    for key, value in update_data.items():
        row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
        if row is not None:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            db.add(PlatformSetting(key=key, value=value))
    db.commit()
    result = _get_all(db)
    result["praxis_edition"] = get_settings().praxis_edition
    logger.info("patch_settings %s", fmt_kv(keys=list(update_data.keys())))
    return PlatformSettingsResponse.model_validate(result)


@router.post("/test-engine", response_model=SettingsEngineTestResponse)
def test_engine(
    payload: SettingsEngineTestRequest,
    db: Session = Depends(get_db),
) -> SettingsEngineTestResponse:
    """Probe the configured external CLI command via EngineProbeAgent."""
    command = payload.command.strip()
    if not command:
        stored = get_setting(db, "external_cli_command")
        command = str(stored or "").strip()
    if not command:
        return SettingsEngineTestResponse(ok=False, message="No CLI command configured")

    from app.services.engine_probe_agent import get_engine_probe_agent

    agent = get_engine_probe_agent()
    result = agent.probe(command)
    logger.info(
        "test_engine %s",
        fmt_kv(command=command, ok=result.ok, flags=result.flags_added),
    )

    return SettingsEngineTestResponse(
        ok=result.ok,
        message=result.message,
        suggested_command=result.suggested_command,
        flags_added=result.flags_added,
        env_issues=result.env_issues,
    )
