"""Credentials encryption — frozen contract §3.

Fernet key sourced from env `CONNECTION_ENCRYPTION_KEY` (44-char urlsafe-b64).
Fallback: derive sha256 from `JWT_SECRET` and urlsafe-b64-encode the 32 bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet


def _derive_key_from_secret(secret: str) -> bytes:
    """sha256(secret) -> 32 bytes -> urlsafe_b64 = 44-char Fernet key."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.environ.get("CONNECTION_ENCRYPTION_KEY")
    if key:
        key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    else:
        jwt_secret = os.environ.get("JWT_SECRET") or "dev-insecure-jwt-secret"
        key_bytes = _derive_key_from_secret(jwt_secret)
    return Fernet(key_bytes)


def encrypt_credentials(creds: dict) -> str:
    """JSON-encode then Fernet-encrypt a credentials dict. Returns urlsafe ascii token."""
    plaintext = json.dumps(creds, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = get_fernet().encrypt(plaintext)
    return token.decode("ascii")


def decrypt_credentials(token: str) -> dict:
    """Reverse of encrypt_credentials. Raises cryptography.fernet.InvalidToken on tamper."""
    if isinstance(token, str):
        token = token.encode("ascii")
    plaintext = get_fernet().decrypt(token)
    return json.loads(plaintext.decode("utf-8"))
