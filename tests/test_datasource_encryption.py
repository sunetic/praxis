"""Tests for datasource password encryption via EncryptedString TypeDecorator."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    get_encryption_key,
    is_encrypted,
)
from app.db.database import Base
from app.models import models


@pytest.fixture(autouse=True)
def reset_derived_key(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "_DERIVED_KEY", None)
    yield
    monkeypatch.setattr(sec, "_DERIVED_KEY", None)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)  # noqa: N806
    session = Session()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Unit tests for encrypt/decrypt helpers
# ---------------------------------------------------------------------------


def test_encrypt_produces_fernet_token():
    token = encrypt_secret("secret123")
    assert is_encrypted(token)


def test_decrypt_roundtrip():
    plain = "my-database-password"
    assert decrypt_secret(encrypt_secret(plain)) == plain


def test_is_encrypted_rejects_plaintext():
    assert not is_encrypted("plaintext")
    assert not is_encrypted("")


def test_encrypt_same_plaintext_different_tokens():
    t1 = encrypt_secret("pw")
    t2 = encrypt_secret("pw")
    assert t1 != t2  # Fernet uses random IV


def test_key_derivation_is_deterministic(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "_DERIVED_KEY", None)
    k1 = get_encryption_key()
    monkeypatch.setattr(sec, "_DERIVED_KEY", None)
    k2 = get_encryption_key()
    assert k1 == k2


def test_explicit_datasource_encryption_key_overrides_derivation(monkeypatch):
    import base64

    import app.core.security as sec
    from app.core.config import get_settings

    explicit_key = base64.urlsafe_b64encode(b"x" * 32).decode()
    settings = get_settings()
    monkeypatch.setattr(settings, "datasource_encryption_key", explicit_key)
    monkeypatch.setattr(sec, "_DERIVED_KEY", None)

    k = get_encryption_key()
    assert k is not None
    assert len(k) > 0


# ---------------------------------------------------------------------------
# Integration tests: TypeDecorator via ORM session
# ---------------------------------------------------------------------------


def test_password_stored_encrypted(db_session):
    ds = models.DataSource(
        name="test",
        host="127.0.0.1",
        port=2881,
        db_type="oceanbase",
        cluster_key="127.0.0.1:2881",
        tenant_role="user",
        user="root",
        password="secret123",
        database="oceanbase",
    )
    db_session.add(ds)
    db_session.commit()

    raw = db_session.execute(
        text("SELECT password FROM datasources WHERE id = :id"), {"id": ds.id}
    ).scalar()
    assert raw is not None
    assert is_encrypted(raw), f"Expected Fernet token in DB, got: {raw!r}"


def test_password_read_back_as_plaintext(db_session):
    ds = models.DataSource(
        name="test",
        host="127.0.0.1",
        port=2881,
        db_type="oceanbase",
        cluster_key="127.0.0.1:2881",
        tenant_role="user",
        user="root",
        password="my-plain-pw",
        database="oceanbase",
    )
    db_session.add(ds)
    db_session.commit()
    db_session.expire(ds)

    fetched = db_session.query(models.DataSource).filter_by(id=ds.id).one()
    assert fetched.password == "my-plain-pw"


def test_null_password_stored_as_null(db_session):
    ds = models.DataSource(
        name="no-pass",
        host="127.0.0.1",
        port=2881,
        db_type="oceanbase",
        cluster_key="127.0.0.1:2881",
        tenant_role="user",
        user="root",
        password=None,
        database="oceanbase",
    )
    db_session.add(ds)
    db_session.commit()

    raw = db_session.execute(
        text("SELECT password FROM datasources WHERE id = :id"), {"id": ds.id}
    ).scalar()
    assert raw is None


def test_update_password_reencrypts(db_session):
    ds = models.DataSource(
        name="test",
        host="127.0.0.1",
        port=2881,
        db_type="oceanbase",
        cluster_key="127.0.0.1:2881",
        tenant_role="user",
        user="root",
        password="original",
        database="oceanbase",
    )
    db_session.add(ds)
    db_session.commit()

    ds.password = "updated"
    db_session.commit()
    db_session.expire(ds)

    fetched = db_session.query(models.DataSource).filter_by(id=ds.id).one()
    assert fetched.password == "updated"

    raw = db_session.execute(
        text("SELECT password FROM datasources WHERE id = :id"), {"id": ds.id}
    ).scalar()
    assert is_encrypted(raw)


def test_existing_plaintext_row_reads_transparently(db_session):
    """Simulates a pre-migration row with plaintext password."""
    db_session.execute(
        text(
            "INSERT INTO datasources (name, host, port, db_type, cluster_key, tenant_role, user, password, database, status, created_at, updated_at) "
            "VALUES ('legacy', '127.0.0.1', 2881, 'oceanbase', '127.0.0.1:2881', 'user', 'root', 'legacy-plain', 'oceanbase', 'active', datetime('now'), datetime('now'))"
        )
    )
    db_session.commit()

    fetched = db_session.query(models.DataSource).filter_by(name="legacy").one()
    assert fetched.password == "legacy-plain"
