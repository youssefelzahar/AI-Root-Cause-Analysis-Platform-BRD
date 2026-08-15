"""Secret encryption for stored SQL Server credentials (PRD section 8).

This module is *not* authentication - Phase 1 has none. It exists so saved
connection passwords are encrypted at rest rather than stored in plaintext.

Fernet is AES-128-CBC with an HMAC-SHA256 authentication tag and a URL-safe
token format, so there is no nonce management to get wrong. ``MultiFernet``
lets a retired key keep decrypting existing rows during a rotation.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import DEV_ENCRYPTION_KEY, settings
from app.core.exceptions import AppError

_PREFIX = "v1:"


class EncryptionUnavailableError(AppError):
    status_code = 500
    code = "ENCRYPTION_UNAVAILABLE"


class SecretDecryptionError(AppError):
    status_code = 500
    code = "SECRET_DECRYPTION_FAILED"


def _normalise(raw: str) -> bytes:
    """Accept either a real Fernet key or any passphrase.

    A passphrase is hashed to 32 bytes so local development works without
    generating a key, while production is still forced onto a real one by the
    settings validator.
    """
    candidate = raw.strip().encode()
    try:
        Fernet(candidate)
        return candidate
    except (ValueError, TypeError):
        import base64
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(candidate).digest())


@lru_cache
def _fernet() -> MultiFernet:
    primary = settings.encryption_key.get_secret_value() if settings.encryption_key else ""
    if not primary:
        if not settings.is_development:
            raise EncryptionUnavailableError("ENCRYPTION_KEY is not configured.")
        primary = DEV_ENCRYPTION_KEY
    keys = [Fernet(_normalise(primary))]
    keys.extend(Fernet(_normalise(legacy)) for legacy in settings.encryption_keys_legacy if legacy)
    return MultiFernet(keys)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Output is never the same twice."""
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty secret.")
    return _PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        raise SecretDecryptionError("Stored credential is empty.")
    token = ciphertext[len(_PREFIX):] if ciphertext.startswith(_PREFIX) else ciphertext
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Stored credential could not be decrypted. The encryption key may have changed."
        ) from exc


def rotate_secret(ciphertext: str) -> str:
    """Re-encrypt an existing token under the current primary key."""
    return encrypt_secret(decrypt_secret(ciphertext))


def reset_cache() -> None:
    """Test hook - drops the cached key set after settings are overridden."""
    _fernet.cache_clear()
