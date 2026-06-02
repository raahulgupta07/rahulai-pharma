"""Embed HMAC secret storage — Fernet-encrypted at rest.

Plaintext secrets are needed for HMAC verification of incoming embed session
requests. We never want to store them in plaintext on disk, so we
encrypt-at-rest using Fernet (cryptography.fernet) and decrypt on read.

Key sourcing (mirrors dash/connectors/crypto.py):
  1. CONNECTION_ENCRYPTION_KEY env var — 44-char urlsafe-b64 Fernet key OR raw 32 bytes
  2. fallback: sha256(JWT_SECRET) → urlsafe_b64 → 44-char Fernet key
  3. neither set → clear ImproperlyConfigured error at first call

Failure-soft on decrypt: returns None on InvalidToken so legacy plaintext rows
can still authenticate via the fallback path in dash/embed/auth.py.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EmbedSecretConfigError(RuntimeError):
    """Raised when neither CONNECTION_ENCRYPTION_KEY nor JWT_SECRET is configured."""


def _derive_key_from_secret(secret: str) -> bytes:
    """sha256(secret) → 32 bytes → urlsafe_b64 = 44-char Fernet key."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _normalize_env_key(raw: str) -> bytes:
    """Accept either a 44-char urlsafe-b64 Fernet key or a raw 32-byte string."""
    raw_b = raw.encode("utf-8") if isinstance(raw, str) else raw
    # If exactly 44 chars and ends with '=', assume already base64-encoded Fernet key.
    if len(raw_b) == 44:
        return raw_b
    # If 32 raw bytes, base64-encode to produce a 44-char Fernet key.
    if len(raw_b) == 32:
        return base64.urlsafe_b64encode(raw_b)
    # Otherwise try as-is; Fernet() will raise a clear error if malformed.
    return raw_b


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Lazy singleton. Derives key from env at first call.

    Mirrors dash/connectors/crypto.py: prefer CONNECTION_ENCRYPTION_KEY, fall
    back to sha256(JWT_SECRET). If JWT_SECRET is also unset, uses the same
    "dev-insecure-jwt-secret" default the connectors layer uses so dev stacks
    boot without extra config. Production deployments MUST set at least one
    of these env vars (see docs/SECRETS_AUDIT.md).
    """
    env_key = os.environ.get("CONNECTION_ENCRYPTION_KEY")
    if env_key:
        return Fernet(_normalize_env_key(env_key))
    jwt_secret = os.environ.get("JWT_SECRET") or "dev-insecure-jwt-secret"
    return Fernet(_derive_key_from_secret(jwt_secret))


def encrypt_secret(plaintext: str) -> str:
    """Fernet-encrypt a plaintext secret. Returns urlsafe-b64 ascii token."""
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("plaintext must be a non-empty string")
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    """Fernet-decrypt. Returns None on bad cipher (fail-soft for legacy rows).

    None is returned for:
      - empty/None ciphertext
      - InvalidToken (tampered, wrong key, or not actually ciphertext)
      - any unexpected error (logged once)
    """
    if not ciphertext:
        return None
    try:
        token = ciphertext.encode("ascii") if isinstance(ciphertext, str) else ciphertext
        plaintext_b = _fernet().decrypt(token)
        return plaintext_b.decode("utf-8")
    except InvalidToken:
        return None
    except EmbedSecretConfigError:
        # Misconfiguration — let it propagate so caller can surface.
        raise
    except Exception as e:  # pragma: no cover - unexpected path
        logger.warning("embed decrypt_secret failed: %s", e)
        return None
