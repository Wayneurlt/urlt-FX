from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_ph = PasswordHasher()  # Argon2id with library defaults

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Session tokens are stored hashed so a DB leak does not leak live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class SecretsUnavailable(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().secret_encryption_key
    if key is None:
        raise SecretsUnavailable("FX_SECRET_ENCRYPTION_KEY is not set; credentials cannot be stored")
    try:
        return Fernet(key.get_secret_value().encode())
    except (ValueError, TypeError) as exc:
        raise SecretsUnavailable("FX_SECRET_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretsUnavailable("stored secret cannot be decrypted with the current key") from exc


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "•" * 8 + (value[-2:] if len(value) > 8 else "")
