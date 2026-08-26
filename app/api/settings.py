from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.schemas.schemas import (
    PlatformSettingsResponse,
    PlatformSettingsUpdateRequest,
    SettingsEngineTestRequest,
    SettingsEngineTestResponse,
)
from app.services.platform.settings_store import (
    DEFAULT_PLATFORM_SETTINGS,
    get_setting,
    load_settings,
    upsert_setting,
)

router = APIRouter(prefix="/settings", tags=["Settings"])
logger = get_logger("api.settings")


def _get_response(db: Session) -> PlatformSettingsResponse:
    result = dict(DEFAULT_PLATFORM_SETTINGS)
    result.update(load_settings(db))
    api_key = result.pop("ai_api_key", None)
    result["ai_api_key_configured"] = bool(str(api_key).strip()) if api_key is not None else False
    result["praxis_edition"] = get_settings().praxis_edition
    return PlatformSettingsResponse.model_validate(result)


@router.get("", response_model=PlatformSettingsResponse)
def list_settings(db: Session = Depends(get_db)) -> PlatformSettingsResponse:
    response = _get_response(db)
    logger.info("list_settings")
    return response


@router.patch("", response_model=PlatformSettingsResponse)
def patch_settings(
    payload: PlatformSettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> PlatformSettingsResponse:
    update_data = payload.model_dump(exclude_none=True)
    for key, value in update_data.items():
        upsert_setting(db, key, value)
    db.commit()
    logger.info("patch_settings %s", fmt_kv(keys=list(update_data.keys())))
    return _get_response(db)


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
