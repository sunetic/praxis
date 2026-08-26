from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

_FERNET_TOKEN_PREFIX = b"gAAAAA"
_DERIVED_KEY: bytes | None = None


def get_encryption_key() -> bytes:
    global _DERIVED_KEY
    if _DERIVED_KEY is not None:
        return _DERIVED_KEY

    from app.core.config import get_settings

    settings = get_settings()

    explicit = settings.datasource_encryption_key.strip()
    if explicit:
        raw = base64.urlsafe_b64decode(explicit + "==")
        _DERIVED_KEY = base64.urlsafe_b64encode(raw[:32])
        return _DERIVED_KEY

    if settings.secret_key == "dev-secret-key":
        import logging

        logging.getLogger("app.core.security").warning(
            "secret_key is the default dev value; protected values are encrypted with a weak key. "
            "Set SECRET_KEY or DATASOURCE_ENCRYPTION_KEY in .env for production."
        )
    material = settings.secret_key.encode()
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"praxis-datasource-password-v1",
        info=b"datasource-encryption",
    ).derive(material)
    _DERIVED_KEY = base64.urlsafe_b64encode(derived)
    return _DERIVED_KEY


def _fernet() -> Fernet:
    return Fernet(get_encryption_key())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt protected value — the encryption key may have changed. "
            "Set DATASOURCE_ENCRYPTION_KEY to the original key to recover access."
        ) from exc


def is_encrypted(value: str) -> bool:
    return value.encode().startswith(_FERNET_TOKEN_PREFIX)


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts string values at the ORM boundary."""

    impl = String(512)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        if is_encrypted(value):
            return value
        return encrypt_secret(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        if not is_encrypted(value):
            return value
        return decrypt_secret(value)
