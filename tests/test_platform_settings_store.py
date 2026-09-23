from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import is_encrypted
from app.db.database import Base
from app.models.models import PlatformSetting
from app.services.platform.settings_store import (
    get_setting,
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


def test_llm_config_reads_decrypted_platform_setting(db_session, monkeypatch):
    from app.db import database as database_module
    from app.services.agent.models import resolve_model_config

    upsert_setting(db_session, "ai_api_key", "secret-value")
    upsert_setting(db_session, "ai_model", "candidate-model")
    db_session.commit()
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_session.bind))

    config = resolve_model_config()

    assert config.api_key.get_secret_value() == "secret-value"
    assert config.model_name == "candidate-model"


def test_llm_config_does_not_hide_decryption_failure(db_session, monkeypatch):
    from app.db import database as database_module
    from app.services.agent.models import resolve_model_config

    db_session.add(PlatformSetting(key="ai_api_key", value="gAAAAA-invalid-ciphertext"))
    db_session.commit()
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=db_session.bind))

    with pytest.raises(ValueError, match="Failed to decrypt protected value"):
        resolve_model_config()
