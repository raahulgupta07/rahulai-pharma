"""Ontology public-API key management.

Pattern mirrors ``dash/embed/__init__.py`` + ``dash/embed/manager.py``:
- Public key (``dop_pub_<32hex>``) is the lookup id, safe to log.
- Secret key (``dop_sec_<32hex>``) is shown ONCE on create/rotate; only
  its sha256 is persisted in ``dash_ontology_api_keys.secret_key_hash``.
- Bearer auth: clients send ``Authorization: Bearer dop_sec_<...>``.

All DB access uses parameterized SQL via SQLAlchemy ``text()``. The engine
is reused from ``dash.embed._get_engine`` to keep one NullPool per process.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Key generation ────────────────────────────────────────────────────────

def gen_public_key() -> str:
    """Browser-safe public identifier. 32 hex chars (~128 bits)."""
    return "dop_pub_" + secrets.token_hex(16)


def gen_secret_key() -> str:
    """Server-only bearer secret. 32 hex chars (~128 bits)."""
    return "dop_sec_" + secrets.token_hex(16)


def gen_keys() -> tuple[str, str]:
    """Convenience: ``(public, secret)`` pair."""
    return gen_public_key(), gen_secret_key()


def hash_secret(secret: str) -> str:
    """Constant-time-comparable sha256 hex digest."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(plain: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(plain), stored_hash)


# ── Engine ───────────────────────────────────────────────────────────────

def _engine():
    """Reuse the embed engine so we don't duplicate NullPool factories."""
    from dash.embed import _get_engine
    return _get_engine()


# ── Default scope ─────────────────────────────────────────────────────────

DEFAULT_SCOPE: dict[str, bool] = {
    "types": True,
    "glossary": True,
    "links": True,
    "lineage": False,
}


def _normalise_scope(scope: dict | None) -> dict:
    """Ensure scope is a flat dict of bool flags. Unknown keys preserved."""
    if not isinstance(scope, dict):
        return dict(DEFAULT_SCOPE)
    out = dict(DEFAULT_SCOPE)
    for k, v in scope.items():
        out[str(k)] = bool(v)
    return out


# ── CRUD ──────────────────────────────────────────────────────────────────

def create_key(
    *,
    name: str,
    project_slug: str | None,
    scope: dict | None,
    rate_limit_per_min: int = 60,
    allowed_origins: list[str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Create a new key. Returns ``{public, secret, ...}`` — secret is
    plaintext, returned ONCE. Caller must surface to UI immediately."""
    if not name or not name.strip():
        raise ValueError("name required")
    public, secret = gen_keys()
    scope_n = _normalise_scope(scope)
    eng = _engine()
    with eng.begin() as cn:
        row = cn.execute(text(
            "INSERT INTO public.dash_ontology_api_keys "
            "(name, public_key, secret_key_hash, project_slug, scope, "
            " rate_limit_per_min, status, allowed_origins, created_by) "
            "VALUES (:name, :pub, :hsh, :slug, CAST(:scope AS jsonb), "
            "        :rl, 'active', :origins, :uid) "
            "RETURNING id, name, public_key, project_slug, scope, "
            "          rate_limit_per_min, status, allowed_origins, "
            "          created_by, created_at"
        ), {
            "name": name.strip(),
            "pub": public,
            "hsh": hash_secret(secret),
            "slug": project_slug,
            "scope": json.dumps(scope_n),
            "rl": int(rate_limit_per_min or 60),
            "origins": list(allowed_origins or []) or None,
            "uid": user_id,
        }).first()
    out = _row_to_dict(row)
    out["secret"] = secret  # plaintext, ONCE
    return out


def list_keys(project_slug: str | None = None) -> list[dict]:
    """List all keys (no secrets, no hashes). Optionally filter by project."""
    eng = _engine()
    sql = (
        "SELECT id, name, public_key, project_slug, scope, "
        "       rate_limit_per_min, status, allowed_origins, "
        "       created_by, created_at, last_used_at "
        "FROM public.dash_ontology_api_keys "
    )
    params: dict[str, Any] = {}
    if project_slug:
        sql += "WHERE project_slug = :slug "
        params["slug"] = project_slug
    sql += "ORDER BY id DESC LIMIT 500"
    with eng.connect() as cn:
        rows = cn.execute(text(sql), params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_key(key_id: int) -> dict | None:
    eng = _engine()
    with eng.connect() as cn:
        row = cn.execute(text(
            "SELECT id, name, public_key, project_slug, scope, "
            "       rate_limit_per_min, status, allowed_origins, "
            "       created_by, created_at, last_used_at "
            "FROM public.dash_ontology_api_keys WHERE id = :id"
        ), {"id": int(key_id)}).first()
    return _row_to_dict(row) if row else None


def revoke_key(key_id: int) -> bool:
    """Soft-delete: set status='revoked'. Audit trail preserved."""
    eng = _engine()
    with eng.begin() as cn:
        r = cn.execute(text(
            "UPDATE public.dash_ontology_api_keys "
            "SET status = 'revoked' "
            "WHERE id = :id AND status <> 'revoked'"
        ), {"id": int(key_id)})
    return bool(getattr(r, "rowcount", 0))


def rotate_secret(key_id: int, user_id: int | None = None) -> str | None:
    """Generate new secret, swap hash, return plaintext ONCE.

    Returns None if the key does not exist or is revoked.
    """
    new_secret = gen_secret_key()
    eng = _engine()
    with eng.begin() as cn:
        r = cn.execute(text(
            "UPDATE public.dash_ontology_api_keys "
            "SET secret_key_hash = :hsh "
            "WHERE id = :id AND status = 'active' "
            "RETURNING id"
        ), {"id": int(key_id), "hsh": hash_secret(new_secret)}).first()
    if not r:
        return None
    return new_secret


# ── Auth helper used by the public router ────────────────────────────────

def verify_bearer(authorization_header: str | None) -> dict | None:
    """Resolve ``Authorization: Bearer dop_sec_<...>`` to an active key row.

    Returns the key dict (no secret_key_hash) or None on any failure. Bumps
    ``last_used_at`` best-effort. Revoked keys return None.
    """
    if not authorization_header:
        return None
    h = authorization_header.strip()
    if not h.lower().startswith("bearer "):
        return None
    secret = h[7:].strip()
    if not secret or not secret.startswith("dop_sec_"):
        return None

    h_hash = hash_secret(secret)
    eng = _engine()
    try:
        with eng.begin() as cn:
            row = cn.execute(text(
                "SELECT id, name, public_key, project_slug, scope, "
                "       rate_limit_per_min, status, allowed_origins, "
                "       created_by, created_at, last_used_at "
                "FROM public.dash_ontology_api_keys "
                "WHERE secret_key_hash = :h"
            ), {"h": h_hash}).first()
            if not row:
                return None
            d = _row_to_dict(row)
            if d.get("status") != "active":
                return None
            # Best-effort last_used_at bump.
            try:
                cn.execute(text(
                    "UPDATE public.dash_ontology_api_keys "
                    "SET last_used_at = now() WHERE id = :id"
                ), {"id": d["id"]})
            except Exception:
                pass
            return d
    except Exception:
        logger.exception("verify_bearer failed")
        return None


# ── Audit ─────────────────────────────────────────────────────────────────

def log_call(
    *,
    key_id: int,
    endpoint: str,
    status_code: int,
    latency_ms: int,
    ip: str | None,
) -> None:
    """Insert one audit row. Best-effort; never raises."""
    try:
        eng = _engine()
        with eng.begin() as cn:
            cn.execute(text(
                "INSERT INTO public.dash_ontology_api_calls "
                "(key_id, endpoint, status_code, latency_ms, ip) "
                "VALUES (:k, :e, :s, :l, :ip)"
            ), {
                "k": int(key_id),
                "e": endpoint[:200],
                "s": int(status_code) if status_code is not None else None,
                "l": int(latency_ms) if latency_ms is not None else None,
                "ip": ip,
            })
    except Exception:
        logger.debug("ontology_public audit log skipped", exc_info=True)


def usage_stats(key_id: int, days: int = 14) -> dict:
    """Aggregated usage for one key: totals, p50/p95 latency, top
    endpoints, daily series."""
    eng = _engine()
    days = max(1, min(int(days or 14), 90))
    with eng.connect() as cn:
        totals = cn.execute(text(
            "SELECT COUNT(*) AS calls, "
            "       SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS errors, "
            "       AVG(latency_ms)::int AS avg_ms "
            "FROM public.dash_ontology_api_calls "
            "WHERE key_id = :k "
            "  AND created_at > now() - (:d || ' days')::interval"
        ), {"k": int(key_id), "d": days}).first()

        # PERCENTILE_CONT for p50 / p95.
        try:
            pct = cn.execute(text(
                "SELECT "
                "  PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50, "
                "  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95 "
                "FROM public.dash_ontology_api_calls "
                "WHERE key_id = :k "
                "  AND created_at > now() - (:d || ' days')::interval"
            ), {"k": int(key_id), "d": days}).first()
            p50 = int(pct[0]) if pct and pct[0] is not None else None
            p95 = int(pct[1]) if pct and pct[1] is not None else None
        except Exception:
            p50 = p95 = None

        top = cn.execute(text(
            "SELECT endpoint, COUNT(*) AS c "
            "FROM public.dash_ontology_api_calls "
            "WHERE key_id = :k "
            "  AND created_at > now() - (:d || ' days')::interval "
            "GROUP BY endpoint ORDER BY c DESC LIMIT 10"
        ), {"k": int(key_id), "d": days}).fetchall()

        daily = cn.execute(text(
            "WITH d AS ("
            "  SELECT generate_series("
            "    date_trunc('day', now() - (:d || ' days')::interval),"
            "    date_trunc('day', now()),"
            "    '1 day'"
            "  )::date AS day"
            ") "
            "SELECT d.day, COALESCE(c.cnt, 0) "
            "FROM d LEFT JOIN ("
            "  SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS cnt "
            "  FROM public.dash_ontology_api_calls "
            "  WHERE key_id = :k "
            "    AND created_at > now() - (:d || ' days')::interval "
            "  GROUP BY 1"
            ") c ON c.day = d.day "
            "ORDER BY d.day"
        ), {"k": int(key_id), "d": days}).fetchall()

    return {
        "calls": int(totals[0] or 0) if totals else 0,
        "errors": int(totals[1] or 0) if totals else 0,
        "avg_latency_ms": int(totals[2] or 0) if totals and totals[2] is not None else 0,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "top_endpoints": [{"endpoint": r[0], "count": int(r[1])} for r in top],
        "daily": [{"date": r[0].isoformat() if r[0] else None,
                   "count": int(r[1] or 0)} for r in daily],
        "days": days,
    }


# ── Internal: row → dict ──────────────────────────────────────────────────

def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    sc = d.get("scope")
    if isinstance(sc, str):
        try:
            d["scope"] = json.loads(sc)
        except Exception:
            d["scope"] = {}
    if d.get("scope") is None:
        d["scope"] = dict(DEFAULT_SCOPE)
    for k in ("created_at", "last_used_at"):
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat() if v.tzinfo else v.isoformat()
        elif v is not None:
            d[k] = str(v)
    # Always strip the hash, even when caller asked for the full row.
    d.pop("secret_key_hash", None)
    return d
