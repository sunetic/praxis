from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.schemas.schemas import (
    PlatformSettingsResponse,
    PlatformSettingsUpdateRequest,
)
from app.services.platform.settings_store import (
    DEFAULT_PLATFORM_SETTINGS,
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
