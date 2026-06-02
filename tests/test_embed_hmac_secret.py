"""Embed HMAC secret storage tests — Fernet encrypt-at-rest + legacy fallback.

Covers:
  1. create_embed stores ciphertext, not plaintext
  2. authenticate round-trip succeeds with encrypted secret
  3. legacy plaintext-only rows still authenticate
  4. rotate_secret replaces the encrypted secret (old sigs fail, new sigs pass)

Requires a reachable Postgres (via `db.db_url`). Auto-skips if the DB is not
reachable so the suite stays green in environments without the stack.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

# Ensure encryption key is available before any imports trigger Fernet().
os.environ.setdefault("JWT_SECRET", "test-embed-secret-storage-key")


def _can_connect() -> bool:
    try:
        from dash.embed import _get_engine
        eng = _get_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _can_connect(), reason="Postgres not reachable — skipping embed HMAC tests"
)


@pytest.fixture
def project_slug():
    """Use an existing project if available, else fall back to a fixed test slug.

    These tests INSERT/UPDATE only the dash_agent_embeds row and DELETE the row
    in teardown, so they don't require an actual `dash_projects` entry.
    """
    return "test_embed_hmac_proj"


@pytest.fixture
def cleanup_embeds(project_slug):
    """Remove any rows we create during the test."""
    created: list[str] = []
    yield created
    if created:
        from dash.embed import _get_engine
        eng = _get_engine()
        with eng.connect() as conn:
            conn.execute(
                text("DELETE FROM public.dash_agent_embeds WHERE embed_id = ANY(:ids)"),
                {"ids": created},
            )
            conn.commit()


def test_create_embed_stores_encrypted_not_plaintext(project_slug, cleanup_embeds):
    from dash.embed import _get_engine, manager as embed_mgr

    embed = embed_mgr.create_embed(
        project_slug=project_slug,
        name="hmac-encrypted-test",
        allowed_origins=["https://example.com"],
        auth_mode="hmac",
        user_id_required=False,
    )
    cleanup_embeds.append(embed["embed_id"])
    assert embed.get("secret_key", "").startswith("sk_"), "plaintext returned once on create"

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT secret_key, secret_key_encrypted, secret_key_hash "
                "FROM public.dash_agent_embeds WHERE embed_id = :e"
            ),
            {"e": embed["embed_id"]},
        ).first()

    assert row is not None
    assert row[0] is None, "secret_key plaintext column should be NULL on new rows"
    assert row[1] and len(row[1]) > 40, "secret_key_encrypted should be populated"
    assert row[2], "secret_key_hash should be populated"


def test_authenticate_succeeds_with_encrypted_secret(project_slug, cleanup_embeds):
    from dash.embed import hmac_user, manager as embed_mgr
    from dash.embed.auth import authenticate_session_request

    origin = "https://test-roundtrip.example"
    embed = embed_mgr.create_embed(
        project_slug=project_slug,
        name="hmac-roundtrip-test",
        allowed_origins=[origin],
        auth_mode="hmac",
        user_id_required=True,
    )
    cleanup_embeds.append(embed["embed_id"])

    user_payload = {"id": "u-123", "role": "viewer"}
    secret = embed["secret_key"]
    sig = hmac_user(secret, user_payload)

    ctx = authenticate_session_request(
        embed_id=embed["embed_id"],
        public_key=embed["public_key"],
        user_payload=user_payload,
        signature=sig,
        origin=origin,
        ip="127.0.0.1",
    )
    assert ctx["embed_id"] == embed["embed_id"]
    assert ctx["external_user"] == "u-123"


def test_legacy_plaintext_secret_still_works(project_slug, cleanup_embeds):
    """Insert a legacy-style row (plaintext secret_key, no encrypted col) and verify auth."""
    from dash.embed import (
        _get_engine,
        gen_embed_id,
        gen_public_key,
        gen_secret_key,
        hash_secret,
        hmac_user,
    )
    from dash.embed.auth import authenticate_session_request

    embed_id = gen_embed_id()
    public_key = gen_public_key()
    secret = gen_secret_key()
    secret_hash = hash_secret(secret)

    eng = _get_engine()
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.dash_agent_embeds
                  (embed_id, project_slug, public_key, secret_key, secret_key_hash,
                   secret_key_encrypted, name, allowed_origins, user_id_required,
                   user_id_signed, auth_mode, rate_limit_per_min, enabled)
                VALUES
                  (:e, :s, :p, :sk, :h, NULL, :n, :o, TRUE, TRUE, 'hmac', 30, TRUE)
                """
            ),
            {
                "e": embed_id,
                "s": project_slug,
                "p": public_key,
                "sk": secret,  # legacy plaintext
                "h": secret_hash,
                "n": "legacy-plaintext-test",
                "o": ["https://legacy.example"],
            },
        )
        conn.commit()
    cleanup_embeds.append(embed_id)

    user_payload = {"id": "u-legacy"}
    sig = hmac_user(secret, user_payload)
    ctx = authenticate_session_request(
        embed_id=embed_id,
        public_key=public_key,
        user_payload=user_payload,
        signature=sig,
        origin="https://legacy.example",
        ip="127.0.0.1",
    )
    assert ctx["external_user"] == "u-legacy"


def test_rotate_replaces_encrypted_secret(project_slug, cleanup_embeds):
    from dash.embed import _get_engine, hmac_user, manager as embed_mgr
    from dash.embed.auth import authenticate_session_request

    origin = "https://rotate.example"
    embed = embed_mgr.create_embed(
        project_slug=project_slug,
        name="hmac-rotate-test",
        allowed_origins=[origin],
        auth_mode="hmac",
        user_id_required=True,
    )
    cleanup_embeds.append(embed["embed_id"])
    old_secret = embed["secret_key"]

    new_secret = embed_mgr.rotate_secret(embed["embed_id"])
    assert new_secret != old_secret
    assert new_secret.startswith("sk_")

    # New sig works.
    payload = {"id": "u-new"}
    sig_new = hmac_user(new_secret, payload)
    ctx = authenticate_session_request(
        embed_id=embed["embed_id"],
        public_key=embed["public_key"],
        user_payload=payload,
        signature=sig_new,
        origin=origin,
        ip="127.0.0.1",
    )
    assert ctx["external_user"] == "u-new"

    # Old sig fails.
    sig_old = hmac_user(old_secret, payload)
    with pytest.raises(ValueError, match="invalid user signature"):
        authenticate_session_request(
            embed_id=embed["embed_id"],
            public_key=embed["public_key"],
            user_payload=payload,
            signature=sig_old,
            origin=origin,
            ip="127.0.0.1",
        )

    # Post-rotate plaintext column should be NULL.
    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT secret_key, secret_key_encrypted "
                "FROM public.dash_agent_embeds WHERE embed_id = :e"
            ),
            {"e": embed["embed_id"]},
        ).first()
    assert row[0] is None, "plaintext secret_key should be cleared on rotate"
    assert row[1], "secret_key_encrypted should be populated after rotate"
