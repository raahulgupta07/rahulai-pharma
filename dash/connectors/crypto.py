"""Credentials encryption — frozen contract §3.

Fernet key sourced from env `CONNECTION_ENCRYPTION_KEY` (44-char urlsafe-b64).
Fallback: derive sha256 from `JWT_SECRET` and urlsafe-b64-encode the 32 bytes.

SECURITY: there is NO hardcoded fallback. If neither var is set, encrypt/decrypt
raises loudly rather than silently protecting stored connector credentials with a
publicly-known dev key. Set CONNECTION_ENCRYPTION_KEY (generate once, persist) in
prod; keep it stable forever — rotating it makes existing ciphertext undecryptable.
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


# Auto-generated key file (Open WebUI WEBUI_SECRET_KEY pattern). Lives on the
# PERSISTED knowledge volume (survives container recreate AND image rebuild), so
# a fresh install just works with NO manual env step. Override the dir/path with
# CONNECTION_ENCRYPTION_KEY_FILE. SECURITY: this file IS the master key for all
# stored connector/S3 credentials — back up the knowledge volume; if it's lost,
# existing ciphertext is undecryptable (re-enter creds). An explicit
# CONNECTION_ENCRYPTION_KEY env always wins and is preferred for multi-host.
_KEY_FILE = os.environ.get(
    "CONNECTION_ENCRYPTION_KEY_FILE",
    os.path.join(os.environ.get("KNOWLEDGE_DIR", "/app/knowledge"), ".connection_encryption_key"),
)


def _load_or_create_key_file() -> bytes | None:
    """Return a persisted Fernet key, generating+writing one on first call.
    Atomic O_EXCL create so concurrent gunicorn workers can't each generate a
    DIFFERENT key (that would orphan creds written by another worker) — the
    loser of the race reads the winner's file. Returns None if the dir isn't
    writable (caller then raises rather than using an ephemeral key)."""
    # Fast path: already exists.
    try:
        if os.path.exists(_KEY_FILE):
            data = open(_KEY_FILE, "rb").read().strip()
            if data:
                return data
    except OSError:
        pass
    # Create atomically — only one process wins; others fall through to re-read.
    try:
        os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
        new_key = Fernet.generate_key()
        fd = os.open(_KEY_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, new_key)
        finally:
            os.close(fd)
        return new_key
    except FileExistsError:
        # Another worker created it first — use theirs (read-after-write).
        try:
            data = open(_KEY_FILE, "rb").read().strip()
            return data or None
        except OSError:
            return None
    except OSError:
        return None  # dir not writable → caller raises


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.environ.get("CONNECTION_ENCRYPTION_KEY")
    if key:
        key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    else:
        jwt_secret = os.environ.get("JWT_SECRET")
        if jwt_secret:
            key_bytes = _derive_key_from_secret(jwt_secret)
        else:
            # Neither env set → auto-generate + persist (no manual config needed).
            key_bytes = _load_or_create_key_file()
            if not key_bytes:
                raise RuntimeError(
                    "Cannot encrypt connector credentials: no CONNECTION_ENCRYPTION_KEY / "
                    f"JWT_SECRET env and the key file {_KEY_FILE} is not writable. "
                    "Set CONNECTION_ENCRYPTION_KEY or make the knowledge volume writable."
                )
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
