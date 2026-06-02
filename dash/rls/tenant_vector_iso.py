"""Per-tenant vector isolation on dash.dash_vectors.

Migration 079 adds `tenant_namespace TEXT NOT NULL DEFAULT 'default'` +
partial unique index for dedup within tenant. This module:

  • derive_tenant_ns(project_slug, user_attrs) → canonical ns string
  • vector_search_isolated(...) → cosine search WHERE tenant_namespace=:ns
  • Audits cross-tenant attempts to dash_rls_audit

Usage:
    from dash.rls.tenant_vector_iso import vector_search_isolated, derive_tenant_ns

    ns = derive_tenant_ns("acme", {"tenant_id": "store_42"})
    rows = vector_search_isolated(emb, tenant_ns=ns, top_k=10)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)
_eng = create_engine(db_url, poolclass=NullPool)


def derive_tenant_ns(project_slug: str, user_attrs: dict | None = None) -> str:
    """Derive canonical tenant namespace.

    - No user_attrs → return project_slug (project-wide tenant).
    - user_attrs.tenant_id present → `{slug}::{tenant_id}` for multi-tenant-
      within-project isolation.
    """
    if not project_slug:
        raise ValueError("project_slug required")
    if user_attrs:
        tid = user_attrs.get("tenant_id") or user_attrs.get("tenant_namespace")
        if tid:
            return f"{project_slug}::{tid}"
    return project_slug


def _fmt_vec(emb: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in emb) + "]"


def vector_search_isolated(
    query_emb: list[float],
    tenant_ns: str,
    top_k: int = 10,
    project_slug: str | None = None,
    namespace: str | None = None,
) -> list[dict]:
    """Cosine search restricted to a single tenant_namespace.

    Returns list of {id, source_id, text, metadata, similarity, tenant_namespace}.
    """
    if not tenant_ns:
        raise ValueError("tenant_ns required")
    if not query_emb:
        return []
    top_k = max(1, min(int(top_k), 200))
    vec_lit = _fmt_vec(query_emb)
    params: dict[str, Any] = {"ns": tenant_ns, "k": top_k}
    where = ["tenant_namespace = :ns"]
    if project_slug:
        where.append("project_slug = :slug")
        params["slug"] = project_slug
    if namespace:
        where.append("namespace = :nm")
        params["nm"] = namespace
    sql = f"""
        SELECT id, source_id, text, metadata, tenant_namespace,
               1 - (embedding <=> '{vec_lit}'::vector) AS similarity
        FROM dash.dash_vectors
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> '{vec_lit}'::vector
        LIMIT :k
    """
    out: list[dict] = []
    try:
        with _eng.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            for r in rows:
                d = dict(r)
                md = d.get("metadata")
                if isinstance(md, str):
                    try:
                        d["metadata"] = json.loads(md)
                    except Exception:
                        pass
                out.append(d)
    except Exception as e:
        logger.exception(f"vector_search_isolated: query failed: {e}")
        raise
    return out


def audit_cross_tenant_attempt(
    project_slug: str,
    requested_ns: str,
    actual_ns: str,
    user_attrs: dict | None = None,
    reason: str = "cross_tenant_access_blocked",
) -> None:
    """Log cross-tenant access attempt to dash_rls_audit (best-effort)."""
    try:
        from dash.rls.audit import log_rls_event
        log_rls_event(
            project_slug=project_slug,
            original_sql=f"vector_search_isolated requested_ns={requested_ns}",
            rewritten_sql=f"actual_ns={actual_ns}",
            mode="tenant_vector_iso",
            blocked=True,
            block_reason=reason,
            user_attrs=user_attrs or {},
            external_user=None,
            embed_id=None,
        )
    except Exception as e:
        logger.warning(f"tenant_vector_iso: audit log failed: {e}")


def enforce_tenant_boundary(
    project_slug: str,
    user_attrs: dict | None,
    requested_ns: str | None = None,
) -> str:
    """Resolve effective tenant_namespace and audit any mismatch.

    If `requested_ns` is supplied and differs from derived, log a blocked
    attempt and return the derived (safe) ns.
    """
    derived = derive_tenant_ns(project_slug, user_attrs)
    if requested_ns and requested_ns != derived:
        audit_cross_tenant_attempt(
            project_slug, requested_ns, derived, user_attrs,
            reason="requested_ns != derived_ns; using derived",
        )
    return derived


__all__ = [
    "derive_tenant_ns",
    "vector_search_isolated",
    "audit_cross_tenant_attempt",
    "enforce_tenant_boundary",
]
