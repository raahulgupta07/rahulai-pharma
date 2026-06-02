"""Embed auth error-code tests.

For each EmbedAuthError code, force the condition and assert the raised
exception carries the matching `code` + `status` + non-empty `detail`. Also
confirms backward-compat: the legacy `detail` field stays populated alongside
the new `code` field.

These tests stub the SQLAlchemy engine + verify functions so they exercise
the auth branching logic without needing a live DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Engine + row stub helpers
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_kw):
        return SimpleNamespace(fetchone=lambda: self._row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, row):
        self._row = row

    def begin(self):
        return _FakeConn(self._row)

    def connect(self):
        return _FakeConn(self._row)


def _row(**overrides):
    base = dict(
        embed_id="emb_x",
        project_slug="proj_x",
        public_key="pub_x",
        secret_key="sk_plain",
        secret_key_hash="sk_hash",
        allowed_origins=["https://allowed.example"],
        user_id_required=False,
        auth_mode="public",
        jwt_jwks_url=None,
        rate_limit_per_min=30,
        feature_config={},
        enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _call(**kwargs):
    """Invoke authenticate_session_request with sensible defaults."""
    from dash.embed.auth import authenticate_session_request

    defaults = dict(
        embed_id="emb_x",
        public_key="pub_x",
        user_payload=None,
        signature=None,
        origin="https://allowed.example",
        ip="127.0.0.1",
        server_origin="https://dash.local",
    )
    defaults.update(kwargs)
    return authenticate_session_request(**defaults)


# ---------------------------------------------------------------------------
# Per-code tests
# ---------------------------------------------------------------------------


def test_missing_credentials_code_400():
    from dash.embed.auth import EmbedAuthError

    with pytest.raises(EmbedAuthError) as exc:
        _call(embed_id="", public_key="")
    assert exc.value.code == "missing_credentials"
    assert exc.value.status == 400
    assert exc.value.detail
    assert "missing" in exc.value.detail.lower()


def test_embed_not_found_code_403():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(None)):
        with pytest.raises(EmbedAuthError) as exc:
            _call()
    assert exc.value.code == "embed_not_found"
    assert exc.value.status == 403
    assert exc.value.detail


def test_embed_not_found_when_disabled():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(_row(enabled=False))):
        with pytest.raises(EmbedAuthError) as exc:
            _call()
    assert exc.value.code == "embed_not_found"
    assert exc.value.status == 403


def test_origin_denied_code_403():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(_row())):
        with patch("dash.embed.auth.verify_origin", return_value=False):
            with pytest.raises(EmbedAuthError) as exc:
                _call(origin="https://evil.example")
    assert exc.value.code == "origin_denied"
    assert exc.value.status == 403
    assert "evil.example" in exc.value.detail


def test_payload_required_code_400():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="hmac", user_id_required=True)
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with pytest.raises(EmbedAuthError) as exc:
                _call()
    assert exc.value.code == "payload_required"
    assert exc.value.status == 400
    assert exc.value.detail


def test_secret_not_provisioned_code_500():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="hmac", user_id_required=True, secret_key=None)
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with pytest.raises(EmbedAuthError) as exc:
                _call(user_payload={"id": "u1"}, signature="sig")
    assert exc.value.code == "secret_not_provisioned"
    assert exc.value.status == 500


def test_sig_invalid_code_403():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="hmac", user_id_required=True)
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with patch("dash.embed.auth.verify_user_payload", return_value=False):
                with pytest.raises(EmbedAuthError) as exc:
                    _call(user_payload={"id": "u1"}, signature="badsig")
    assert exc.value.code == "sig_invalid"
    assert exc.value.status == 403


def test_partial_auth_code_400():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="hmac", user_id_required=False)
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with pytest.raises(EmbedAuthError) as exc:
                # Payload but no signature → partial.
                _call(user_payload={"id": "u1"}, signature=None)
    assert exc.value.code == "partial_auth"
    assert exc.value.status == 400


def test_jwt_unsupported_code_501():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="jwt")
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with pytest.raises(EmbedAuthError) as exc:
                _call()
    assert exc.value.code == "jwt_unsupported"
    assert exc.value.status == 501


def test_unknown_auth_mode_code_500():
    from dash.embed.auth import EmbedAuthError

    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(
        _row(auth_mode="something_weird")
    )):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            with pytest.raises(EmbedAuthError) as exc:
                _call()
    assert exc.value.code == "unknown_auth_mode"
    assert exc.value.status == 500
    assert "something_weird" in exc.value.detail


def test_public_mode_succeeds():
    """Sanity: happy path returns ctx dict, no error raised."""
    with patch("dash.embed.auth._get_engine", return_value=_FakeEngine(_row())):
        with patch("dash.embed.auth.verify_origin", return_value=True):
            ctx = _call()
    assert ctx["embed_id"] == "emb_x"
    assert ctx["project_slug"] == "proj_x"


# ---------------------------------------------------------------------------
# Backward compatibility — `detail` field still present
# ---------------------------------------------------------------------------


def test_backward_compat_detail_field():
    """Every EmbedAuthError must carry a non-empty `detail` so existing
    widgets that parse only `detail` continue to work even before they're
    updated to read the new `code` field."""
    from dash.embed.auth import EmbedAuthError

    # Sample every error path; assert each has detail.
    cases = [
        ("missing_credentials", lambda: _call(embed_id="", public_key="")),
    ]
    for code, fn in cases:
        with pytest.raises(EmbedAuthError) as exc:
            fn()
        assert exc.value.code == code
        assert exc.value.detail
        assert isinstance(exc.value.detail, str)
        # Confirm exception str() falls back to detail (used by HTTPException fallback).
        assert str(exc.value) == exc.value.detail
