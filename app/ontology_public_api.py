"""Ontology public read API — Phase E.

Bearer-key gated read-only endpoints mounted at ``/v1/ontology``. Mirrors
``app/embed_public.py`` (rate-limited + audit-logged + per-key CORS).

Auth: ``Authorization: Bearer dop_sec_<32hex>`` issued by super-admin via
``POST /api/ontology/api-keys`` (see ``app/ontology_api.py``).

Scoping rules:
- If the key's ``project_slug`` is set, results are filtered to that project.
- If NULL (super-admin issued global key), all projects are visible.
- Per-route scope flags from ``key.scope`` (e.g. ``lineage: false``) gate
  access; off-flag routes return 403.

This file deliberately reuses query helpers from ``app/ontology_api.py``
(e.g. ``_entities_index``, ``_engine``, ``_has_table``) to avoid duplicated
SQL. None of the admin-only routes (``/promotions/*``, ``/test-run``,
``/audit``, ``/cluster-suggest``) are exposed here.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time as _time
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ontology", tags=["ontology-public"])


# ── Rate limit (per-key sliding 60s window) ──────────────────────────────

_RATE_BUCKETS: dict[int, deque] = {}
_RATE_LOCK = threading.Lock()


def _rate_limit(key_id: int, limit_per_min: int) -> tuple[bool, int, int]:
    """Sliding-window rate limit.

    Returns ``(allowed, remaining, reset_seconds)``. ``reset_seconds`` is
    seconds until the oldest in-window timestamp expires (i.e. when the
    next slot frees up). For empty buckets it's 60.
    """
    now = _time.monotonic()
    cutoff = now - 60.0
    cap = max(1, int(limit_per_min or 60))
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS.setdefault(key_id, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= cap:
            reset = max(1, int(60 - (now - bucket[0])))
            return False, 0, reset
        bucket.append(now)
        remaining = max(0, cap - len(bucket))
        reset = max(1, int(60 - (now - bucket[0])))
        return True, remaining, reset


# ── Auth + scope gate ────────────────────────────────────────────────────

def _client_ip(req: Request) -> str | None:
    if req.client and req.client.host:
        return req.client.host
    fwd = req.headers.get("X-Forwarded-For") or req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return None


def _audit_async(key_id: int, endpoint: str, status_code: int,
                 latency_ms: int, ip: str | None) -> None:
    """Fire-and-forget audit insert. Never blocks request."""
    try:
        from dash.ontology_public.keys import log_call
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(asyncio.to_thread(
                log_call, key_id=key_id, endpoint=endpoint,
                status_code=status_code, latency_ms=latency_ms, ip=ip,
            ))
        else:
            log_call(key_id=key_id, endpoint=endpoint,
                     status_code=status_code, latency_ms=latency_ms, ip=ip)
    except Exception:
        logger.debug("audit_async failed", exc_info=True)


def _cors_headers(allowed_origins: list[str] | None,
                  request_origin: str | None) -> dict[str, str]:
    """Per-key CORS: echo Origin only if allowlisted, OR echo if list empty."""
    out: dict[str, str] = {}
    if not request_origin:
        return out
    if not allowed_origins or request_origin in allowed_origins:
        out["Access-Control-Allow-Origin"] = request_origin
        out["Vary"] = "Origin"
        out["Access-Control-Allow-Credentials"] = "false"
    return out


def _authenticate(req: Request, required_scope: str | None = None) -> dict:
    """Return the validated key row, or raise HTTPException.

    Logs a 401 audit row when the bearer token resolves to an existing-but-
    revoked key (so admins can spot abuse of leaked keys).
    """
    auth = req.headers.get("Authorization") or req.headers.get("authorization")
    from dash.ontology_public.keys import verify_bearer, hash_secret
    key = verify_bearer(auth)
    if not key:
        # Best-effort revoked-key audit hit so admins notice.
        if auth and auth.lower().startswith("bearer dop_sec_"):
            try:
                from dash.embed import _get_engine
                from sqlalchemy import text as _t
                eng = _get_engine()
                with eng.connect() as cn:
                    row = cn.execute(_t(
                        "SELECT id FROM public.dash_ontology_api_keys "
                        "WHERE secret_key_hash = :h"
                    ), {"h": hash_secret(auth.strip()[7:].strip())}).first()
                if row:
                    _audit_async(int(row[0]), req.url.path, 401, 0, _client_ip(req))
            except Exception:
                pass
        raise HTTPException(status_code=401, detail="invalid or revoked bearer key")
    if required_scope:
        scope = key.get("scope") or {}
        if not scope.get(required_scope, False):
            _audit_async(int(key["id"]), req.url.path, 403, 0, _client_ip(req))
            raise HTTPException(status_code=403, detail=f"scope '{required_scope}' not granted")
    return key


def _project_filter_clause(key: dict, alias: str = "") -> tuple[str, dict]:
    """Build a WHERE fragment to constrain to the key's project_slug.

    Returns ``("", {})`` for global keys. Caller appends with ``AND``.
    Use ``alias`` if the column is qualified (e.g. ``b.project_slug``).
    """
    slug = key.get("project_slug")
    if not slug:
        return "", {}
    col = f"{alias}.project_slug" if alias else "project_slug"
    return f"{col} = :_pk_slug", {"_pk_slug": slug}


# ── Healthcheck (no auth) ─────────────────────────────────────────────────

@router.get("/healthcheck")
def healthcheck():
    """Liveness ping. Public, no auth, no rate limit."""
    return {"ok": True, "service": "ontology-public", "version": "v1"}


# ── /types ─────────────────────────────────────────────────────────────────

@router.get("/types")
def public_list_types(request: Request, source: str = "all", q: str = "",
                      limit: int = 200):
    """List ontology entity types.

    Filters by the key's project (if scoped) so partner integrators only
    see the entities reflected in their own deployment. Global keys see
    the full catalog.
    """
    t0 = _time.monotonic()
    key = _authenticate(request, required_scope="types")
    allowed, remaining, reset = _rate_limit(int(key["id"]), int(key.get("rate_limit_per_min") or 60))
    if not allowed:
        _audit_async(int(key["id"]), "/v1/ontology/types", 429,
                     int((_time.monotonic() - t0) * 1000), _client_ip(request))
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset)})

    try:
        from app.ontology_api import _engine, _entities_index
        from sqlalchemy import text as _t

        eng = _engine()
        idx = _entities_index()
        brain_aliases: dict[str, set[str]] = {}
        brain_entity_names: set[str] = set()
        brain_entity_defs: dict[str, str] = {}
        binding_counts: dict[str, int] = {}
        slug_filter, slug_params = _project_filter_clause(key)
        slug_where = f" AND {slug_filter}" if slug_filter else ""

        with eng.begin() as cn:
            try:
                for name, definition, meta, _slug in cn.execute(_t(
                    "SELECT name, definition, metadata, project_slug "
                    "FROM dash_company_brain WHERE category='alias'"
                    + (f" AND ({slug_filter} OR project_slug IS NULL)" if slug_filter else "")
                ), slug_params).fetchall():
                    s = brain_aliases.setdefault(name, set())
                    if definition:
                        for tok in str(definition).split(","):
                            tok = tok.strip()
                            if tok:
                                s.add(tok)
                    if isinstance(meta, dict):
                        for a in meta.get("aliases", []) or []:
                            s.add(str(a))
            except Exception:
                pass
            try:
                for name, definition, _meta, _slug in cn.execute(_t(
                    "SELECT name, definition, metadata, project_slug "
                    "FROM dash_company_brain "
                    "WHERE category IN ('entity','object_type')"
                    + (f" AND ({slug_filter} OR project_slug IS NULL)" if slug_filter else "")
                ), slug_params).fetchall():
                    if not name:
                        continue
                    brain_entity_names.add(name)
                    if definition and name not in brain_entity_defs:
                        brain_entity_defs[name] = str(definition)[:500]
            except Exception:
                pass
            try:
                seen: dict[str, set[str]] = {}
                bind_sql = ("SELECT template_ref, project_slug "
                            "FROM dash_template_bindings WHERE status='bound'")
                if slug_filter:
                    bind_sql += f" AND {slug_filter}"
                for tref, slug in cn.execute(_t(bind_sql), slug_params).fetchall():
                    if not tref:
                        continue
                    ent = str(tref).split(".")[0]
                    seen.setdefault(ent, set()).add(slug)
                binding_counts = {k: len(v) for k, v in seen.items()}
            except Exception:
                pass

        all_names: set[str] = set()
        if source in ("all", "template"):
            all_names |= set(idx.keys())
        if source in ("all", "learned"):
            all_names |= brain_entity_names

        ql = (q or "").strip().lower()
        results: list[dict[str, Any]] = []
        for name in all_names:
            tpl_rec = idx.get(name)
            aliases = (tpl_rec["aliases"] if tpl_rec else set()) | brain_aliases.get(name, set())
            tpls = [t[0] for t in (tpl_rec["templates"] if tpl_rec else [])]
            row = {
                "name": name,
                "aliases": sorted(aliases)[:10],
                "used_in_templates": tpls[:25],
                "active_agents": int(binding_counts.get(name, 0)),
                "source": "template" if tpl_rec else "learned",
                "property_count": len(tpl_rec["columns"]) if tpl_rec else 0,
                "category": tpl_rec["category"] if tpl_rec else None,
                "definition": brain_entity_defs.get(name) if not tpl_rec else None,
            }
            if ql and ql not in (name + " " + " ".join(row["aliases"])).lower():
                continue
            results.append(row)
        results.sort(key=lambda r: (-r["active_agents"], r["name"]))
        out = {"types": results[: max(1, min(int(limit or 200), 1000))]}
    except HTTPException:
        raise
    except Exception:
        logger.exception("public_list_types failed")
        _audit_async(int(key["id"]), "/v1/ontology/types", 500,
                     int((_time.monotonic() - t0) * 1000), _client_ip(request))
        raise HTTPException(500, "internal error")

    latency = int((_time.monotonic() - t0) * 1000)
    _audit_async(int(key["id"]), "/v1/ontology/types", 200, latency, _client_ip(request))
    headers = {
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        **_cors_headers(key.get("allowed_origins"), request.headers.get("Origin")),
    }
    return JSONResponse(content=out, headers=headers)


# ── /types/{name} ──────────────────────────────────────────────────────────

@router.get("/types/{name}")
def public_get_type(name: str, request: Request):
    """Drill detail for a single entity (templates_using, links_out,
    actions_using, provenance)."""
    t0 = _time.monotonic()
    key = _authenticate(request, required_scope="types")
    allowed, remaining, reset = _rate_limit(int(key["id"]), int(key.get("rate_limit_per_min") or 60))
    if not allowed:
        _audit_async(int(key["id"]), f"/v1/ontology/types/{name}", 429, 0, _client_ip(request))
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset)})

    try:
        from app.ontology_api import _engine, _entities_index, _registry, _has_table
        from sqlalchemy import text as _t

        eng = _engine()
        idx = _entities_index()
        tpl_rec = idx.get(name)
        col_counts: dict[str, int] = {}
        templates_using: list[dict] = []
        relationships: list[dict] = []
        actions_using: list[dict] = []
        for tpl in _registry().values():
            if name not in {e.name for e in (tpl.entities or [])}:
                continue
            templates_using.append({"template": tpl.name, "category": tpl.category})
            for ent in tpl.entities:
                if ent.name == name:
                    for col in (ent.columns or []):
                        col_counts[col.name] = col_counts.get(col.name, 0) + 1
            for rel in (tpl.relationships or []):
                if rel.from_entity == name:
                    relationships.append({"from": rel.from_entity,
                                          "rel": rel.relation,
                                          "to": rel.to_entity})
            for wf in (tpl.autonomous_workflows or []):
                if wf.expected_entity == name:
                    actions_using.append({"name": wf.name, "schedule": wf.schedule,
                                          "action": wf.action})

        slug_filter, slug_params = _project_filter_clause(key)
        aliases: set[str] = set(tpl_rec["aliases"]) if tpl_rec else set()
        active_agents: list[dict] = []
        promoted_at = None

        with eng.begin() as cn:
            try:
                params = {"n": name, **slug_params}
                sql = ("SELECT definition, metadata FROM dash_company_brain "
                       "WHERE category='alias' AND name=:n")
                if slug_filter:
                    sql += f" AND ({slug_filter} OR project_slug IS NULL)"
                for definition, meta in cn.execute(_t(sql), params).fetchall():
                    if definition:
                        for tok in str(definition).split(","):
                            tok = tok.strip()
                            if tok:
                                aliases.add(tok)
                    if isinstance(meta, dict):
                        for a in meta.get("aliases", []) or []:
                            aliases.add(str(a))
            except Exception:
                pass
            try:
                bind_sql = ("SELECT b.project_slug, COALESCE(p.name, b.project_slug) "
                            "FROM dash_template_bindings b "
                            "LEFT JOIN dash_projects p ON p.slug=b.project_slug "
                            "WHERE b.status='bound' AND (b.template_ref=:n OR b.template_ref LIKE :nl)")
                p2: dict[str, Any] = {"n": name, "nl": f"{name}.%"}
                if slug_filter:
                    bind_sql += " AND b.project_slug = :_pk_slug"
                    p2["_pk_slug"] = key["project_slug"]
                bind_sql += " GROUP BY b.project_slug, p.name LIMIT 500"
                for slug, pname in cn.execute(_t(bind_sql), p2).fetchall():
                    active_agents.append({"project_slug": slug, "project_name": pname})
            except Exception:
                pass
            try:
                kg_sql = ("SELECT predicate, object, COUNT(*) FROM dash_knowledge_triples "
                          "WHERE subject=:n")
                pkg: dict[str, Any] = {"n": name}
                if slug_filter:
                    kg_sql += " AND project_slug = :_pk_slug"
                    pkg["_pk_slug"] = key["project_slug"]
                kg_sql += " GROUP BY predicate, object ORDER BY COUNT(*) DESC LIMIT 50"
                for pred, obj, c in cn.execute(_t(kg_sql), pkg).fetchall():
                    relationships.append({"from": name, "rel": pred, "to": obj,
                                          "count": int(c)})
            except Exception:
                pass
            if _has_table(cn, "dash_promotion_log"):
                try:
                    r = cn.execute(_t(
                        "SELECT created_at FROM dash_promotion_log "
                        "WHERE name=:n AND status='approved' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ), {"n": name}).fetchone()
                    if r and r[0]:
                        promoted_at = r[0].isoformat()
                except Exception:
                    pass

        if not tpl_rec and not templates_using and not relationships and not active_agents:
            _audit_async(int(key["id"]), f"/v1/ontology/types/{name}", 404, 0, _client_ip(request))
            raise HTTPException(status_code=404, detail=f"entity '{name}' not found")

        properties_common = sorted(
            ({"col": k, "appears_in": v} for k, v in col_counts.items()),
            key=lambda r: (-r["appears_in"], r["col"]),
        )
        out = {
            "name": name,
            "aliases": sorted(aliases),
            "templates_using": templates_using,
            "active_agents": active_agents,
            "properties_common": properties_common,
            "links_out": relationships,
            "actions_using": actions_using,
            "provenance": {
                "first_seen_template": tpl_rec["first_seen_template"] if tpl_rec else None,
                "promoted_at": promoted_at,
            },
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("public_get_type failed")
        _audit_async(int(key["id"]), f"/v1/ontology/types/{name}", 500, 0, _client_ip(request))
        raise HTTPException(500, "internal error")

    latency = int((_time.monotonic() - t0) * 1000)
    _audit_async(int(key["id"]), f"/v1/ontology/types/{name}", 200, latency, _client_ip(request))
    return JSONResponse(content=out, headers={
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        **_cors_headers(key.get("allowed_origins"), request.headers.get("Origin")),
    })


# ── /links ─────────────────────────────────────────────────────────────────

@router.get("/links")
def public_list_links(request: Request, limit: int = 300):
    """Aggregate relationships across templates + KG + project FKs."""
    t0 = _time.monotonic()
    key = _authenticate(request, required_scope="links")
    allowed, remaining, reset = _rate_limit(int(key["id"]), int(key.get("rate_limit_per_min") or 60))
    if not allowed:
        _audit_async(int(key["id"]), "/v1/ontology/links", 429, 0, _client_ip(request))
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset)})
    try:
        from app.ontology_api import _engine, _registry
        from sqlalchemy import text as _t

        eng = _engine()
        edges: dict[tuple, dict] = {}
        for tpl in _registry().values():
            for rel in (tpl.relationships or []):
                edges[(rel.from_entity, rel.relation, rel.to_entity)] = {
                    "from_entity": rel.from_entity, "relation": rel.relation,
                    "to_entity": rel.to_entity, "agent_count": 0,
                    "source": "template", "confidence": 1.0,
                }
        slug_filter, slug_params = _project_filter_clause(key)
        with eng.begin() as cn:
            try:
                kg_sql = ("SELECT subject, predicate, object, "
                          "COUNT(DISTINCT project_slug), AVG(COALESCE(confidence,0.7)) "
                          "FROM dash_knowledge_triples ")
                if slug_filter:
                    kg_sql += f"WHERE {slug_filter} "
                kg_sql += ("GROUP BY subject, predicate, object "
                           "ORDER BY COUNT(DISTINCT project_slug) DESC LIMIT 2000")
                for s, p, o, agents, conf in cn.execute(_t(kg_sql), slug_params).fetchall():
                    k = (s, p, o)
                    a = int(agents or 0)
                    c = round(float(conf or 0.7), 3)
                    if k in edges:
                        edges[k]["agent_count"] = int(edges[k]["agent_count"]) + a
                        if c > float(edges[k].get("confidence") or 0):
                            edges[k]["confidence"] = c
                    else:
                        edges[k] = {"from_entity": s, "relation": p, "to_entity": o,
                                    "agent_count": a, "source": "learned", "confidence": c}
            except Exception:
                pass
        out_list = list(edges.values())
        out_list.sort(key=lambda r: (-r["agent_count"], r["from_entity"]))
        out = {"links": out_list[: max(1, min(int(limit or 300), 2000))]}
    except HTTPException:
        raise
    except Exception:
        logger.exception("public_list_links failed")
        _audit_async(int(key["id"]), "/v1/ontology/links", 500, 0, _client_ip(request))
        raise HTTPException(500, "internal error")

    latency = int((_time.monotonic() - t0) * 1000)
    _audit_async(int(key["id"]), "/v1/ontology/links", 200, latency, _client_ip(request))
    return JSONResponse(content=out, headers={
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        **_cors_headers(key.get("allowed_origins"), request.headers.get("Origin")),
    })


# ── /glossary ──────────────────────────────────────────────────────────────

@router.get("/glossary")
def public_list_glossary(request: Request, scope: str = "all",
                         q: str = "", category: str = "all", limit: int = 500):
    """Brain entries: glossary / formula / alias / pattern."""
    t0 = _time.monotonic()
    key = _authenticate(request, required_scope="glossary")
    allowed, remaining, reset = _rate_limit(int(key["id"]), int(key.get("rate_limit_per_min") or 60))
    if not allowed:
        _audit_async(int(key["id"]), "/v1/ontology/glossary", 429, 0, _client_ip(request))
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset)})
    try:
        from app.ontology_api import _engine
        from sqlalchemy import text as _t

        eng = _engine()
        slug_filter, slug_params = _project_filter_clause(key)
        ql = (q or "").strip().lower()
        valid_cats = {"all", "glossary", "formula", "alias", "pattern"}
        if category not in valid_cats:
            category = "all"

        out: list[dict] = []
        with eng.begin() as cn:
            sql = ("SELECT name, definition, project_slug, user_id, category "
                   "FROM dash_company_brain "
                   "WHERE category IN ('glossary','formula','alias','pattern')")
            params: dict[str, Any] = {}
            if category != "all":
                sql += " AND category = :cat"
                params["cat"] = category
            if slug_filter:
                # Project-scoped key sees its project's rows + global rows.
                sql += f" AND ({slug_filter} OR project_slug IS NULL)"
                params.update(slug_params)
            sql += " ORDER BY name LIMIT 5000"
            try:
                rows = cn.execute(_t(sql), params).fetchall()
            except Exception:
                rows = []
            for name, definition, slug, uid, cat in rows:
                s = "global" if (slug is None and uid is None) else "project"
                if scope != "all" and s != scope:
                    continue
                if ql and ql not in ((name or "") + " " + (definition or "")).lower():
                    continue
                out.append({"name": name, "definition": definition, "scope": s,
                            "project_slug": slug, "category": cat})
                if len(out) >= max(1, min(int(limit or 500), 5000)):
                    break
        result = {"glossary": out}
    except HTTPException:
        raise
    except Exception:
        logger.exception("public_list_glossary failed")
        _audit_async(int(key["id"]), "/v1/ontology/glossary", 500, 0, _client_ip(request))
        raise HTTPException(500, "internal error")

    latency = int((_time.monotonic() - t0) * 1000)
    _audit_async(int(key["id"]), "/v1/ontology/glossary", 200, latency, _client_ip(request))
    return JSONResponse(content=result, headers={
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        **_cors_headers(key.get("allowed_origins"), request.headers.get("Origin")),
    })


# ── /lineage ───────────────────────────────────────────────────────────────

@router.get("/lineage")
def public_lineage(request: Request, entity: str = "", max_nodes: int = 200):
    """Force-graph nodes/edges. Requires ``scope.lineage = true``."""
    t0 = _time.monotonic()
    key = _authenticate(request, required_scope="lineage")
    allowed, remaining, reset = _rate_limit(int(key["id"]), int(key.get("rate_limit_per_min") or 60))
    if not allowed:
        _audit_async(int(key["id"]), "/v1/ontology/lineage", 429, 0, _client_ip(request))
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(reset)})
    try:
        from app.ontology_api import _engine, _entities_index, _registry
        from sqlalchemy import text as _t

        eng = _engine()
        max_nodes = max(10, min(int(max_nodes or 200), 1000))
        all_edges: list[tuple[str, str, str, float]] = []
        for tpl in _registry().values():
            for rel in (tpl.relationships or []):
                all_edges.append((rel.from_entity, rel.to_entity, rel.relation, 1.0))
        slug_filter, slug_params = _project_filter_clause(key)
        with eng.begin() as cn:
            try:
                kg_sql = ("SELECT subject, object, predicate, AVG(COALESCE(confidence,0.7)) "
                          "FROM dash_knowledge_triples ")
                if slug_filter:
                    kg_sql += f"WHERE {slug_filter} "
                kg_sql += "GROUP BY subject, object, predicate LIMIT 5000"
                for s, o, p, c in cn.execute(_t(kg_sql), slug_params).fetchall():
                    all_edges.append((s, o, p, round(float(c or 0.7), 3)))
            except Exception:
                pass

        idx = _entities_index()
        if entity:
            keep: set[str] = {entity}
            for _ in range(2):
                grow: set[str] = set()
                for f, t, _r, _c in all_edges:
                    if f in keep:
                        grow.add(t)
                    if t in keep:
                        grow.add(f)
                keep |= grow
            node_names = keep
        else:
            ranked = sorted(
                set(idx.keys()) | {f for f, _, _, _ in all_edges} | {t for _, t, _, _ in all_edges},
                key=lambda n: n,
            )
            node_names = set(ranked[:max_nodes])

        node_names = set(list(node_names)[:max_nodes])
        edges = [e for e in all_edges if e[0] in node_names and e[1] in node_names]
        nodes_out = [{"id": n, "name": n,
                      "source": "template" if n in idx else "learned"}
                     for n in node_names]
        edges_out = [{"source": f, "target": t, "relation": r, "value": c}
                     for (f, t, r, c) in edges]
        out = {"nodes": nodes_out, "edges": edges_out}
    except HTTPException:
        raise
    except Exception:
        logger.exception("public_lineage failed")
        _audit_async(int(key["id"]), "/v1/ontology/lineage", 500, 0, _client_ip(request))
        raise HTTPException(500, "internal error")

    latency = int((_time.monotonic() - t0) * 1000)
    _audit_async(int(key["id"]), "/v1/ontology/lineage", 200, latency, _client_ip(request))
    return JSONResponse(content=out, headers={
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
        **_cors_headers(key.get("allowed_origins"), request.headers.get("Origin")),
    })
