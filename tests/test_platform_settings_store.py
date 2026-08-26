from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import is_encrypted
from app.db.database import Base
from app.models.models import PlatformSetting
from app.services.platform.settings_store import (
    get_setting,
    migrate_sensitive_settings,
    upsert_setting,
)


@pytest.fixture(autouse=True)
def reset_derived_key(monkeypatch):
    import app.core.security as security

    monkeypatch.setattr(security, "_DERIVED_KEY", None)
    yield
    monkeypatch.setattr(security, "_DERIVED_KEY", None)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/settings.db")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        yield session
    engine.dispose()


def _raw_value(db_session, key: str):
    return db_session.execute(
        select(PlatformSetting.value).where(PlatformSetting.key == key)
    ).scalar_one()


def test_sensitive_setting_is_encrypted_at_rest(db_session):
    upsert_setting(db_session, "ai_api_key", "secret-value")
    db_session.commit()

    stored_value = _raw_value(db_session, "ai_api_key")
    assert isinstance(stored_value, str)
    assert stored_value != "secret-value"
    assert is_encrypted(stored_value)
    assert get_setting(db_session, "ai_api_key") == "secret-value"


def test_normal_setting_remains_plaintext(db_session):
    upsert_setting(db_session, "ai_model", "candidate-model")
    db_session.commit()

    assert _raw_value(db_session, "ai_model") == "candidate-model"
    assert get_setting(db_session, "ai_model") == "candidate-model"


def test_migrate_sensitive_settings_encrypts_legacy_plaintext(db_session):
    db_session.add(PlatformSetting(key="ai_api_key", value="legacy-plaintext"))
    db_session.commit()

    assert migrate_sensitive_settings(db_session) == 1
    db_session.commit()

    stored_value = _raw_value(db_session, "ai_api_key")
    assert isinstance(stored_value, str)
    assert is_encrypted(stored_value)
    assert get_setting(db_session, "ai_api_key") == "legacy-plaintext"
    assert migrate_sensitive_settings(db_session) == 0


def test_llm_config_reads_decrypted_platform_setting(db_session, monkeypatch):
    from app.db import database as database_module
    from app.services.llm import _resolve_llm_config

    upsert_setting(db_session, "ai_api_key", "secret-value")
    upsert_setting(db_session, "ai_model", "candidate-model")
    db_session.commit()
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_session.bind))

    config = _resolve_llm_config()

    assert config["api_key"] == "secret-value"
    assert config["model"] == "candidate-model"


def test_llm_config_does_not_hide_decryption_failure(db_session, monkeypatch):
    from app.db import database as database_module
    from app.services.llm import _resolve_llm_config

    db_session.add(PlatformSetting(key="ai_api_key", value="gAAAAA-invalid-ciphertext"))
    db_session.commit()
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_session.bind))

    with pytest.raises(ValueError, match="Failed to decrypt protected value"):
        _resolve_llm_config()
