from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret, is_encrypted
from app.models.models import PlatformSetting

DEFAULT_PLATFORM_SETTINGS: dict[str, Any] = {
    "build_engine": "pi_lite",
    "external_cli_command": "",
    "external_cli_pre_flags": "",
    "external_cli_post_flags": "",
    "sql_allow_mutating": False,
    "context_window_tokens": 128_000,
    "context_compression_threshold_percent": 75,
}

SENSITIVE_PLATFORM_SETTING_KEYS = frozenset({"ai_api_key"})


def _encode_value(key: str, value: Any) -> Any:
    if key not in SENSITIVE_PLATFORM_SETTING_KEYS or value is None or value == "":
        return value
    text = str(value)
    return text if is_encrypted(text) else encrypt_secret(text)


def _decode_value(key: str, value: Any) -> Any:
    if key not in SENSITIVE_PLATFORM_SETTING_KEYS or not isinstance(value, str):
        return value
    return decrypt_secret(value) if is_encrypted(value) else value


def load_settings(db: Session, keys: Iterable[str] | None = None) -> dict[str, Any]:
    query = db.query(PlatformSetting)
    if keys is not None:
        selected_keys = tuple(keys)
        if not selected_keys:
            return {}
        query = query.filter(PlatformSetting.key.in_(selected_keys))
    return {row.key: _decode_value(row.key, row.value) for row in query.all()}


def get_setting(db: Session, key: str) -> Any:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row is None:
        return DEFAULT_PLATFORM_SETTINGS.get(key)
    return _decode_value(row.key, row.value)


def upsert_setting(db: Session, key: str, value: Any) -> None:
    encoded_value = _encode_value(key, value)
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row is None:
        db.add(PlatformSetting(key=key, value=encoded_value))
    else:
        row.value = encoded_value


def migrate_sensitive_settings(db: Session) -> int:
    rows = (
        db.query(PlatformSetting)
        .filter(PlatformSetting.key.in_(SENSITIVE_PLATFORM_SETTING_KEYS))
        .all()
    )
    migrated = 0
    for row in rows:
        encoded_value = _encode_value(row.key, row.value)
        if encoded_value == row.value:
            continue
        row.value = encoded_value
        migrated += 1
    return migrated
