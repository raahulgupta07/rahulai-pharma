"""Ontology Workbench API.

Read-only super-admin endpoints aggregating Dash's ontology across all
templates + all projects/agents. Source data comes from existing tables.

Endpoints:
- GET  /api/ontology/summary
- GET  /api/ontology/types
- GET  /api/ontology/types/{name}
- GET  /api/ontology/links
- GET  /api/ontology/actions
- GET  /api/ontology/glossary
- GET  /api/ontology/growth
- GET  /api/ontology/lineage
- GET  /api/ontology/promotions/pending
- POST /api/ontology/promotions/{id}/approve
- POST /api/ontology/promotions/{id}/reject
- POST /api/ontology/actions/{name}/test-run
- GET  /api/ontology/healthcheck
- GET  /api/ontology/audit
- POST /api/ontology/cluster-suggest
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ontology", tags=["Ontology"])


# ── Auth ───────────────────────────────────────────────────────────────────

def _require_super_admin(request: Request) -> dict:
    """Resolve current user and ensure they are SUPER_ADMIN."""
    from app.auth import SUPER_ADMIN, get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    if u.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "super admin only")
    return u


# ── In-memory cache (60s) ──────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 60.0


def _cget(k: str):
    rec = _CACHE.get(k)
    if not rec:
        return None
    ts, v = rec
    if time.time() - ts > _TTL:
        _CACHE.pop(k, None)
        return None
    return v


def _cset(k: str, v):
    _CACHE[k] = (time.time(), v)


def _cclear():
    _CACHE.clear()


# ── Helpers ────────────────────────────────────────────────────────────────

def _engine():
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        raise HTTPException(503, "db engine unavailable")
    return eng


def _has_table(cn, name: str) -> bool:
    from sqlalchemy import text as _t
    try:
        r = cn.execute(_t("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).fetchone()
        return bool(r and r[0])
    except Exception:
        return False


def _registry() -> dict:
    # Industry preset template registry removed; ontology API now operates on
    # an empty template set. Callers iterating .values() handle this gracefully.
    return {}


def _entities_index() -> dict[str, dict]:
    """Build entity → metadata across all templates."""
    idx: dict[str, dict] = {}
    for tpl in _registry().values():
        for ent in (tpl.entities or []):
            rec = idx.setdefault(ent.name, {
                "aliases": set(), "templates": [], "columns": set(),
                "category": tpl.category, "first_seen_template": tpl.name,
            })
            for a in (ent.aliases or []):
                rec["aliases"].add(a)
            rec["templates"].append((tpl.name, tpl.category))
            for col in (ent.columns or []):
                rec["columns"].add(col.name)
    return idx


def _scalar(cn, sql: str, default=0, **params):
    from sqlalchemy import text as _t
    try:
        return cn.execute(_t(sql), params).scalar() or default
    except Exception:
        return default


# ── 1. Summary ─────────────────────────────────────────────────────────────

@router.get("/summary")
def ontology_summary(request: Request):
    """System-wide counters across templates + all projects."""
    _require_super_admin(request)
    cached = _cget("summary")
    # Defensive: only serve cached if it has expected shape with non-None counts
    if cached is not None and isinstance(cached, dict) and cached.get("templates") is not None:
        return cached
    from sqlalchemy import text as _t

    def _i(v) -> int:
        """Coerce any value (None, Decimal, str, int) to a safe int; 0 on failure."""
        try:
            if v is None:
                return 0
            return int(v)
        except Exception:
            return 0

    try:
        eng = _engine()
        idx = _entities_index()
        tpl_names = set(idx.keys())
        triples = rels = actions_total = formulas = glossary = aliases = 0
        promotions_pending = projects_total = active_agents = 0
        brain_names: set = set()
        with eng.begin() as cn:
            try:
                brain_names = {r[0] for r in cn.execute(_t(
                    "SELECT DISTINCT name FROM dash_company_brain "
                    "WHERE category IN ('entity','object_type')"
                )).fetchall()}
            except Exception:
                brain_names = set()

            triples = _i(_scalar(cn, "SELECT COUNT(*) FROM dash_knowledge_triples"))
            rels = _i(_scalar(cn, "SELECT COUNT(*) FROM dash_relationships"))
            actions_total = _i(_scalar(cn,
                "SELECT COUNT(DISTINCT name) FROM dash_autonomous_workflows"))
            formulas = _i(_scalar(cn,
                "SELECT COUNT(*) FROM dash_company_brain WHERE category='formula'"))
            glossary = _i(_scalar(cn,
                "SELECT COUNT(*) FROM dash_company_brain WHERE category='glossary'"))
            aliases = _i(_scalar(cn,
                "SELECT COUNT(*) FROM dash_company_brain WHERE category='alias'"))
            if _has_table(cn, "dash_promotion_log"):
                promotions_pending = _i(_scalar(cn,
                    "SELECT COUNT(*) FROM dash_promotion_log WHERE approver IS NULL"))
            # Narrow try/except — was silently failing through _scalar
            try:
                projects_total = _i(cn.execute(_t("SELECT COUNT(*) FROM dash_projects")).scalar())
            except Exception as e:
                logger.warning("projects_total count failed: %s", e)
            try:
                active_agents = _i(cn.execute(_t(
                    "SELECT COUNT(DISTINCT project_slug) FROM dash_autonomous_workflows "
                    "WHERE status='active'"
                )).scalar())
            except Exception as e:
                logger.warning("active_agents count failed: %s", e)

        types_total = len(tpl_names | brain_names)

        templates_by_category: dict[str, int] = {}
        try:
            for t in _registry().values():
                cat = getattr(t, "category", None) or "uncategorized"
                templates_by_category[cat] = templates_by_category.get(cat, 0) + 1
        except Exception:
            templates_by_category = {}

        out = {
            "templates": _i(len(_registry())),
            "types_total": _i(types_total),
            "links_total": _i(triples + rels),
            "actions_total": _i(actions_total),
            "formulas": _i(formulas), "glossary": _i(glossary), "aliases": _i(aliases),
            "promotions_pending": _i(promotions_pending),
            "active_agents": _i(active_agents),
            "projects_total": _i(projects_total),
            "templates_by_category": templates_by_category,
        }
        _cset("summary", out)
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ontology_summary failed")
        raise HTTPException(500, str(e))


# ── 2. Types list ──────────────────────────────────────────────────────────

@router.get("/types")
def list_types(request: Request, source: str = "all", q: str = "", limit: int = 200):
    """Aggregated entity catalog across templates + brain + bindings."""
    _require_super_admin(request)
    key = f"types:{source}:{q}:{limit}"
    if (c := _cget(key)) is not None:
        return c
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        idx = _entities_index()
        brain_aliases: dict[str, set[str]] = {}
        brain_entity_names: set[str] = set()
        brain_entity_defs: dict[str, str] = {}
        brain_entity_scope: dict[str, str] = {}
        promoted_names: set[str] = set()
        binding_counts: dict[str, int] = {}
        binding_conf: dict[str, list[float]] = {}

        with eng.begin() as cn:
            try:
                for name, definition, meta, _slug in cn.execute(_t(
                    "SELECT name, definition, metadata, project_slug "
                    "FROM dash_company_brain WHERE category='alias'"
                )).fetchall():
                    s = brain_aliases.setdefault(name, set())
                    if definition:
                        for tok in str(definition).split(","):
                            if (tok := tok.strip()):
                                s.add(tok)
                    if isinstance(meta, dict):
                        for a in meta.get("aliases", []) or []:
                            s.add(str(a))
            except Exception:
                pass
            try:
                # Brain-sourced entities: capture name, definition, metadata, scope
                for name, definition, meta, slug in cn.execute(_t(
                    "SELECT name, definition, metadata, project_slug "
                    "FROM dash_company_brain "
                    "WHERE category IN ('entity','object_type')"
                )).fetchall():
                    if not name:
                        continue
                    brain_entity_names.add(name)
                    if definition and name not in brain_entity_defs:
                        brain_entity_defs[name] = str(definition)[:500]
                    brain_entity_scope[name] = "global" if slug is None else "project"
                    if isinstance(meta, dict) and meta.get("promoted"):
                        promoted_names.add(name)
            except Exception:
                pass
            try:
                seen: dict[str, set[str]] = {}
                for tref, slug, conf in cn.execute(_t(
                    "SELECT template_ref, project_slug, confidence "
                    "FROM dash_template_bindings WHERE status='bound'"
                )).fetchall():
                    if not tref:
                        continue
                    ent = str(tref).split(".")[0]
                    seen.setdefault(ent, set()).add(slug)
                    if conf is not None:
                        try:
                            binding_conf.setdefault(ent, []).append(float(conf))
                        except Exception:
                            pass
                binding_counts = {k: len(v) for k, v in seen.items()}
            except Exception:
                pass

        all_names: set[str] = set()
        if source in ("all", "template"):
            all_names |= set(idx.keys())
        if source in ("all", "learned"):
            all_names |= brain_entity_names
        if source in ("all", "promoted"):
            all_names |= promoted_names
        if source not in ("all", "template", "learned", "promoted"):
            all_names = set(idx.keys()) | brain_entity_names

        ql = (q or "").strip().lower()
        results: list[dict] = []
        for name in all_names:
            tpl_rec = idx.get(name)
            aliases = (tpl_rec["aliases"] if tpl_rec else set()) | brain_aliases.get(name, set())
            tpls = [t[0] for t in (tpl_rec["templates"] if tpl_rec else [])]
            cat = tpl_rec["category"] if tpl_rec else None
            confs = binding_conf.get(name) or []
            confidence = round(sum(confs) / len(confs), 3) if confs else (0.95 if tpl_rec else 0.7)
            # Template wins on overlap, but boost confidence by +0.05 when brain also has it
            if tpl_rec and name in brain_entity_names:
                confidence = round(min(1.0, confidence + 0.05), 3)
            # Source: template wins. Otherwise promoted > learned
            if tpl_rec:
                src = "template"
            elif name in promoted_names:
                src = "promoted"
            else:
                src = "learned"
            row = {
                "name": name,
                "aliases": sorted(aliases)[:5],
                "used_in_templates": tpls[:25],
                "active_agents": int(binding_counts.get(name, 0)),
                "confidence": confidence,
                "source": src,
                "property_count": len(tpl_rec["columns"]) if tpl_rec else 0,
                "category": cat,
                "definition": brain_entity_defs.get(name) if not tpl_rec else None,
                "scope": brain_entity_scope.get(name) if not tpl_rec else None,
            }
            if ql and ql not in (name + " " + " ".join(row["aliases"])).lower():
                continue
            results.append(row)

        results.sort(key=lambda r: (-r["active_agents"], r["name"]))
        out = {"types": results[: max(1, min(limit, 1000))]}
        _cset(key, out)
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_types failed")
        raise HTTPException(500, str(e))


# ── 3. Type detail ─────────────────────────────────────────────────────────

@router.get("/types/{name}")
def get_type(name: str, request: Request):
    """Drill detail for a single entity."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
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
                    relationships.append({"from": rel.from_entity, "rel": rel.relation,
                                          "to": rel.to_entity, "count": 1})
            for wf in (tpl.autonomous_workflows or []):
                if wf.expected_entity == name:
                    actions_using.append({"name": wf.name, "schedule": wf.schedule,
                                          "action": wf.action})

        # Fallback: KG-learned entity (subject in dash_knowledge_triples)
        kg_summary: dict | None = None
        if not tpl_rec and not templates_using:
            with eng.begin() as cn:
                try:
                    r = cn.execute(_t(
                        "SELECT 1 FROM dash_company_brain WHERE name=:n "
                        "AND category IN ('entity','object_type') LIMIT 1"
                    ), {"n": name}).fetchone()
                    if r:
                        kg_summary = {"source": "brain"}
                except Exception:
                    pass
                if kg_summary is None:
                    try:
                        r = cn.execute(_t(
                            "SELECT COUNT(*), array_agg(DISTINCT predicate) "
                            "FROM dash_knowledge_triples "
                            "WHERE subject=:n OR object=:n"
                        ), {"n": name}).fetchone()
                        if r and r[0]:
                            kg_summary = {"source": "kg", "triple_count": int(r[0]),
                                           "predicates": list(r[1] or [])[:10]}
                    except Exception:
                        pass
            if kg_summary is None:
                raise HTTPException(404, f"entity '{name}' not found")

        aliases: set[str] = set(tpl_rec["aliases"]) if tpl_rec else set()
        active_agents: list[dict] = []
        promoted_at = None
        confidence = 0.95 if tpl_rec else 0.7

        with eng.begin() as cn:
            try:
                for definition, meta in cn.execute(_t(
                    "SELECT definition, metadata FROM dash_company_brain "
                    "WHERE category='alias' AND name=:n"
                ), {"n": name}).fetchall():
                    if definition:
                        for tok in str(definition).split(","):
                            if (tok := tok.strip()):
                                aliases.add(tok)
                    if isinstance(meta, dict):
                        for a in meta.get("aliases", []) or []:
                            aliases.add(str(a))
            except Exception:
                pass
            try:
                confs: list[float] = []
                for slug, pname, conf in cn.execute(_t(
                    "SELECT b.project_slug, COALESCE(p.name, b.project_slug), MAX(b.confidence) "
                    "FROM dash_template_bindings b "
                    "LEFT JOIN dash_projects p ON p.slug=b.project_slug "
                    "WHERE b.status='bound' AND (b.template_ref=:n OR b.template_ref LIKE :nl) "
                    "GROUP BY b.project_slug, p.name LIMIT 500"
                ), {"n": name, "nl": f"{name}.%"}).fetchall():
                    active_agents.append({"project_slug": slug, "project_name": pname,
                                          "applied_at": None})
                    if conf is not None:
                        try:
                            confs.append(float(conf))
                        except Exception:
                            pass
                if confs:
                    confidence = round(sum(confs) / len(confs), 3)
            except Exception:
                pass
            try:
                for pred, obj, c in cn.execute(_t(
                    "SELECT predicate, object, COUNT(*) FROM dash_knowledge_triples "
                    "WHERE subject=:n GROUP BY predicate, object ORDER BY COUNT(*) DESC LIMIT 50"
                ), {"n": name}).fetchall():
                    relationships.append({"from": name, "rel": pred, "to": obj, "count": int(c)})
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

        properties_common = sorted(
            ({"col": k, "appears_in": v} for k, v in col_counts.items()),
            key=lambda r: (-r["appears_in"], r["col"]),
        )
        return {
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
                "confidence": confidence,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_type failed")
        raise HTTPException(500, str(e))


# ── 4. Links ───────────────────────────────────────────────────────────────

@router.get("/links")
def list_links(request: Request, limit: int = 300):
    """Aggregate relationships across templates + KG + project FKs."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        edges: dict[tuple, dict] = {}
        for tpl in _registry().values():
            for rel in (tpl.relationships or []):
                edges.setdefault((rel.from_entity, rel.relation, rel.to_entity), {
                    "from_entity": rel.from_entity, "relation": rel.relation,
                    "to_entity": rel.to_entity, "agent_count": 0,
                    "source": "template", "confidence": 1.0,
                })

        # Dedupe by (from_entity, relation, to_entity): SUM agent_counts
        # across template/triples/relationships, prefer highest confidence.
        with eng.begin() as cn:
            try:
                for s, p, o, agents, conf in cn.execute(_t(
                    "SELECT subject, predicate, object, "
                    "COUNT(DISTINCT project_slug), AVG(COALESCE(confidence,0.7)) "
                    "FROM dash_knowledge_triples GROUP BY subject, predicate, object "
                    "ORDER BY COUNT(DISTINCT project_slug) DESC LIMIT 2000"
                )).fetchall():
                    k = (s, p, o)
                    a = int(agents or 0)
                    c = round(float(conf or 0.7), 3)
                    if k in edges:
                        edges[k]["agent_count"] = int(edges[k]["agent_count"]) + a
                        if c > float(edges[k].get("confidence") or 0):
                            edges[k]["confidence"] = c
                    else:
                        edges[k] = {"from_entity": s, "relation": p, "to_entity": o,
                                    "agent_count": a, "source": "learned",
                                    "confidence": c}
            except Exception:
                pass
            try:
                for ft, tt, agents, conf in cn.execute(_t(
                    "SELECT from_table, to_table, COUNT(DISTINCT project_slug), "
                    "AVG(COALESCE(confidence,0.8)) FROM dash_relationships "
                    "GROUP BY from_table, to_table "
                    "ORDER BY COUNT(DISTINCT project_slug) DESC LIMIT 2000"
                )).fetchall():
                    k = (ft, "references", tt)
                    a = int(agents or 0)
                    c = round(float(conf or 0.8), 3)
                    if k in edges:
                        edges[k]["agent_count"] = int(edges[k]["agent_count"]) + a
                        if c > float(edges[k].get("confidence") or 0):
                            edges[k]["confidence"] = c
                    else:
                        edges[k] = {"from_entity": ft, "relation": "references",
                                    "to_entity": tt, "agent_count": a,
                                    "source": "inferred", "confidence": c}
            except Exception:
                pass

        out = list(edges.values())
        out.sort(key=lambda r: (-r["agent_count"], r["from_entity"]))
        return {"links": out[: max(1, min(limit, 2000))]}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_links failed")
        raise HTTPException(500, str(e))


# ── 5. Actions ─────────────────────────────────────────────────────────────

@router.get("/actions")
def list_actions(request: Request, limit: int = 300):
    """Aggregate autonomous workflows across templates + projects."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        recs: dict[str, dict] = {}
        for tpl in _registry().values():
            for wf in (tpl.autonomous_workflows or []):
                rec = recs.setdefault(wf.name, {
                    "name": wf.name, "schedule": wf.schedule, "action": wf.action,
                    "templates_using": [], "active_count": 0, "paused_count": 0,
                    "failed_count": 0, "last_run_avg": None,
                })
                if tpl.name not in rec["templates_using"]:
                    rec["templates_using"].append(tpl.name)
        with eng.begin() as cn:
            try:
                for name, sched, act, ac, pc, fc, lr in cn.execute(_t(
                    "SELECT name, MAX(schedule), MAX(action), "
                    "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN last_error IS NOT NULL AND last_error<>'' THEN 1 ELSE 0 END), "
                    "MAX(last_run_at) FROM dash_autonomous_workflows GROUP BY name "
                    "ORDER BY 4 DESC LIMIT 1000"
                )).fetchall():
                    rec = recs.setdefault(name, {
                        "name": name, "schedule": sched or "daily", "action": act or "log",
                        "templates_using": [], "active_count": 0, "paused_count": 0,
                        "failed_count": 0, "last_run_avg": None,
                    })
                    rec["active_count"] = int(ac or 0)
                    rec["paused_count"] = int(pc or 0)
                    rec["failed_count"] = int(fc or 0)
                    if lr:
                        try:
                            rec["last_run_avg"] = lr.isoformat()
                        except Exception:
                            rec["last_run_avg"] = str(lr)
            except Exception:
                pass
        out = list(recs.values())
        out.sort(key=lambda r: (-r["active_count"], r["name"]))
        return {"actions": out[: max(1, min(limit, 1000))]}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_actions failed")
        raise HTTPException(500, str(e))


# ── 6. Glossary ────────────────────────────────────────────────────────────

@router.get("/glossary")
def list_glossary(request: Request, scope: str = "all", q: str = "", limit: int = 500):
    """Brain entries (glossary/formula/alias/pattern) with scope filter."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        ql = (q or "").strip().lower()
        out: list[dict] = []
        with eng.begin() as cn:
            try:
                rows = cn.execute(_t(
                    "SELECT name, definition, project_slug, user_id, category "
                    "FROM dash_company_brain "
                    "WHERE category IN ('glossary','formula','alias','pattern') "
                    "ORDER BY name LIMIT 5000"
                )).fetchall()
            except Exception:
                rows = []
            for name, definition, slug, uid, cat in rows:
                s = "global" if (slug is None and uid is None) else "project"
                if scope != "all" and s != scope:
                    continue
                if ql and ql not in ((name or "") + " " + (definition or "")).lower():
                    continue
                out.append({"name": name, "definition": definition, "scope": s,
                            "project_slug": slug, "category": cat, "count": 1})
                if len(out) >= max(1, min(limit, 5000)):
                    break
        return {"glossary": out}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_glossary failed")
        raise HTTPException(500, str(e))


# ── 7. Growth ──────────────────────────────────────────────────────────────

@router.get("/growth")
def growth_series(request: Request, days: int = 30):
    """Daily timeseries of new entities/triples/memories/promotions/workflows."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    days = max(1, min(int(days or 30), 365))
    try:
        eng = _engine()

        def _dense_series(cn, table: str, where_extra: str = "") -> list[dict]:
            """Generate full daily date range and LEFT JOIN aggregates so
            every day appears (count=0 if no rows that day)."""
            sql = (
                "WITH d AS ("
                "  SELECT generate_series("
                "    date_trunc('day', NOW() - (:d || ' days')::interval),"
                "    date_trunc('day', NOW()),"
                "    '1 day'"
                "  )::date AS day"
                ") "
                f"SELECT d.day, COALESCE(c.cnt, 0) AS cnt "
                f"FROM d LEFT JOIN ("
                f"  SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS cnt "
                f"  FROM {table} "
                f"  WHERE created_at > NOW() - (:d || ' days')::interval "
                f"  {('AND ' + where_extra) if where_extra else ''} "
                f"  GROUP BY 1"
                f") c ON c.day = d.day "
                f"ORDER BY d.day"
            )
            try:
                return [{"date": r[0].isoformat() if r[0] else None,
                         "count": int(r[1] or 0)}
                        for r in cn.execute(_t(sql), {"d": days}).fetchall()]
            except Exception:
                return []

        with eng.begin() as cn:
            new_entities = _dense_series(cn, "dash_company_brain",
                "category IN ('entity','object_type')")
            new_triples = _dense_series(cn, "dash_knowledge_triples")
            new_memories = _dense_series(cn, "dash_memories")
            if _has_table(cn, "dash_promotion_log"):
                new_promotions = _dense_series(cn, "dash_promotion_log")
            else:
                new_promotions = []
            new_workflows = _dense_series(cn, "dash_autonomous_workflows")

        _sum = lambda s: sum(p["count"] for p in s)
        return {
            "days": days,
            "series": {
                "new_entities": new_entities, "new_triples": new_triples,
                "new_memories": new_memories, "new_promotions": new_promotions,
                "new_workflows": new_workflows,
            },
            "totals": {
                "entities": _sum(new_entities), "triples": _sum(new_triples),
                "memories": _sum(new_memories), "promotions": _sum(new_promotions),
                "workflows": _sum(new_workflows),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("growth_series failed")
        raise HTTPException(500, str(e))


# ── 8. Lineage ─────────────────────────────────────────────────────────────

@router.get("/lineage")
def lineage_graph(request: Request, entity: str = "", max_nodes: int = 200):
    """ECharts-compatible lineage graph (2-hop neighbourhood or top-N)."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        max_nodes = max(10, min(int(max_nodes or 200), 1000))
        all_edges: list[tuple[str, str, str, float]] = []
        for tpl in _registry().values():
            for rel in (tpl.relationships or []):
                all_edges.append((rel.from_entity, rel.to_entity, rel.relation, 1.0))

        with eng.begin() as cn:
            try:
                for s, o, p, c in cn.execute(_t(
                    "SELECT subject, object, predicate, AVG(COALESCE(confidence,0.7)) "
                    "FROM dash_knowledge_triples GROUP BY subject, object, predicate "
                    "LIMIT 5000"
                )).fetchall():
                    all_edges.append((s, o, p, round(float(c or 0.7), 3)))
            except Exception:
                pass
            agent_counts: dict[str, int] = {}
            try:
                for ent, c in cn.execute(_t(
                    "SELECT split_part(template_ref,'.',1), COUNT(DISTINCT project_slug) "
                    "FROM dash_template_bindings WHERE status='bound' GROUP BY 1"
                )).fetchall():
                    if ent:
                        agent_counts[ent] = int(c or 0)
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
                key=lambda n: (-agent_counts.get(n, 0), n),
            )
            node_names = set(ranked[:max_nodes])

        node_names = set(list(node_names)[:max_nodes])
        edges = [e for e in all_edges if e[0] in node_names and e[1] in node_names]
        nodes_out = [{
            "id": n, "name": n, "category": "entity",
            "value": agent_counts.get(n, 0),
            "source": "template" if n in idx else "learned",
        } for n in node_names]
        edges_out = [{"source": f, "target": t, "relation": r, "value": c}
                     for (f, t, r, c) in edges]
        return {"nodes": nodes_out, "edges": edges_out}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("lineage_graph failed")
        raise HTTPException(500, str(e))


# ── 9 & 10. Promotions ─────────────────────────────────────────────────────

@router.get("/promotions/pending")
def promotions_pending(request: Request):
    """Up to 100 pending promotion log entries (graceful empty if missing)."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        with eng.begin() as cn:
            if not _has_table(cn, "dash_promotion_log"):
                return {"pending": []}
            try:
                rows = cn.execute(_t(
                    "SELECT id, fact_text, fact_type, source_project_slug, approved_at, "
                    "       triangulation_count, approval_method "
                    "FROM dash_promotion_log "
                    "WHERE approver IS NULL "
                    "ORDER BY id DESC LIMIT 100"
                )).fetchall()
            except Exception:
                return {"pending": []}
        return {"pending": [{
            "id": r[0],
            "name": (r[1] or "")[:80],
            "category": r[2] or "fact",
            "definition": r[1],
            "project_slug": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
            "tenants_count": r[5],
            "status": "pending",
        } for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("promotions_pending failed")
        raise HTTPException(500, str(e))


@router.post("/promotions/{pid}/approve")
def approve_promotion(pid: int, request: Request):
    """Approve a pending promotion + write to global brain."""
    user = _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        with eng.begin() as cn:
            if not _has_table(cn, "dash_promotion_log"):
                return {"ok": False, "error": "promotion log table not present"}
            try:
                row = cn.execute(_t(
                    "SELECT fact_text, fact_type FROM dash_promotion_log WHERE id=:id"
                ), {"id": pid}).fetchone()
                if not row:
                    return {"ok": False, "error": "not found"}
                cn.execute(_t(
                    "UPDATE dash_promotion_log SET approver=:u, approved_at=NOW(), "
                    "approval_method='admin_approved' WHERE id=:id"
                ), {"u": user.get("username"), "id": pid})
                try:
                    fact_name = (row[0] or "")[:80]
                    cat = row[1] or "fact"
                    exists = cn.execute(_t(
                        "SELECT 1 FROM dash_company_brain WHERE name=:n AND category=:c "
                        "AND project_slug IS NULL AND user_id IS NULL LIMIT 1"
                    ), {"n": fact_name, "c": cat}).fetchone()
                    if not exists:
                        cn.execute(_t(
                            "INSERT INTO dash_company_brain "
                            "(category, name, definition, project_slug, user_id, metadata) "
                            "VALUES (:c, :n, :d, NULL, NULL, CAST(:m AS jsonb))"
                        ), {"c": cat, "n": fact_name, "d": row[0] or "",
                            "m": '{"source":"promoted","approver":"' + (user.get("username") or "") + '"}'})
                except Exception as ie:
                    logger.warning("brain write skipped: %s", ie)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        _cclear()
        return {"ok": True, "id": pid}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("approve_promotion failed")
        raise HTTPException(500, str(e))


@router.post("/promotions/{pid}/reject")
def reject_promotion(pid: int, request: Request):
    """Reject a pending promotion."""
    user = _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        with eng.begin() as cn:
            if not _has_table(cn, "dash_promotion_log"):
                return {"ok": False, "error": "promotion log table not present"}
            try:
                cn.execute(_t(
                    "UPDATE dash_promotion_log SET approver=:u, approved_at=NOW(), "
                    "approval_method='admin_rejected', rejection_reason='admin rejected' "
                    "WHERE id=:id"
                ), {"u": user.get("username"), "id": pid})
            except Exception as e:
                return {"ok": False, "error": str(e)}
        _cclear()
        return {"ok": True, "id": pid}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("reject_promotion failed")
        raise HTTPException(500, str(e))


# ── 11. Action test-run ────────────────────────────────────────────────────

class _TestRunBody(BaseModel):
    project_slug: str | None = None
    dry_run: bool = True


@router.post("/actions/{name}/test-run")
def action_test_run(name: str, body: _TestRunBody, request: Request):
    """Trigger a single workflow by name (super admin only).

    If ``project_slug`` is given, runs only that workflow row; else
    iterates all active workflows with the given name. ``dry_run=true``
    returns the resolved SQL without executing.
    """
    _require_super_admin(request)
    from sqlalchemy import text as _t
    try:
        eng = _engine()
        results: list[dict] = []
        ran = 0
        with eng.begin() as cn:
            try:
                if body.project_slug:
                    rows = cn.execute(_t(
                        "SELECT id, project_slug, name, action, schedule, "
                        "resolved_query, query_template, template_name, status "
                        "FROM dash_autonomous_workflows "
                        "WHERE name=:n AND project_slug=:s LIMIT 50"
                    ), {"n": name, "s": body.project_slug}).fetchall()
                else:
                    rows = cn.execute(_t(
                        "SELECT id, project_slug, name, action, schedule, "
                        "resolved_query, query_template, template_name, status "
                        "FROM dash_autonomous_workflows "
                        "WHERE name=:n AND status='active' LIMIT 200"
                    ), {"n": name}).fetchall()
            except Exception as e:
                raise HTTPException(500, f"workflow lookup failed: {e}")

        for r in rows:
            wf = {"id": r[0], "project_slug": r[1], "name": r[2], "action": r[3],
                  "schedule": r[4], "resolved_query": r[5] or r[6] or "",
                  "query_template": r[6], "template_name": r[7], "status": r[8]}
            if body.dry_run:
                results.append({"slug": wf["project_slug"],
                                "sql": (wf["resolved_query"] or "").strip(),
                                "rows_returned": None, "dry_run": True})
                continue
            try:
                from dash.templates.runner import execute_workflow
                rows_out, err = execute_workflow(wf)
                if err:
                    results.append({"slug": wf["project_slug"], "rows_returned": 0, "error": err})
                else:
                    ran += 1
                    results.append({"slug": wf["project_slug"], "rows_returned": len(rows_out or [])})
            except Exception as e:
                results.append({"slug": wf["project_slug"], "rows_returned": 0, "error": str(e)[:300]})
        _cclear()
        return {"ok": True, "ran": ran, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("action_test_run failed")
        raise HTTPException(500, str(e))


# ── 12. Healthcheck (public) ───────────────────────────────────────────────

@router.get("/healthcheck")
def healthcheck():
    """Public, no-auth liveness probe for monitoring."""
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat(),
            "version": "1.0", "endpoints": 15}


# ── 13. Audit feed ─────────────────────────────────────────────────────────

@router.get("/audit")
def ontology_audit(request: Request, days: int = 14, limit: int = 200):
    """Recent ontology changes — entities, brain seeds, promotions."""
    _require_super_admin(request)
    from sqlalchemy import text as _t
    days = max(1, min(int(days or 14), 180))
    limit = max(1, min(int(limit or 200), 500))
    out: list[dict] = []
    try:
        eng = _engine()
        with eng.begin() as cn:
            # New entities via template_bindings.applied_at
            try:
                for ts, slug, tref, conf in cn.execute(_t(
                    "SELECT applied_at, project_slug, template_ref, confidence "
                    "FROM dash_template_bindings WHERE applied_at IS NOT NULL "
                    "AND applied_at > NOW() - (:d || ' days')::interval "
                    "ORDER BY applied_at DESC LIMIT :lim"
                ), {"d": days, "lim": limit}).fetchall():
                    out.append({"ts": ts.isoformat() if ts else None,
                                "kind": "entity_added", "actor": slug,
                                "target": str(tref or "").split(".")[0],
                                "details": {"template_ref": tref, "confidence": conf,
                                            "project_slug": slug}})
            except Exception:
                pass
            # Brain entries
            try:
                for ts, name, cat, slug, uid in cn.execute(_t(
                    "SELECT created_at, name, category, project_slug, user_id "
                    "FROM dash_company_brain "
                    "WHERE created_at > NOW() - (:d || ' days')::interval "
                    "ORDER BY created_at DESC LIMIT :lim"
                ), {"d": days, "lim": limit}).fetchall():
                    out.append({"ts": ts.isoformat() if ts else None,
                                "kind": "brain_seeded",
                                "actor": slug or ("user:" + str(uid) if uid else "system"),
                                "target": name,
                                "details": {"category": cat, "project_slug": slug}})
            except Exception:
                pass
            # Promotions
            if _has_table(cn, "dash_promotion_log"):
                try:
                    for ts, name, cat, status, by in cn.execute(_t(
                        "SELECT COALESCE(reviewed_at, created_at), name, category, "
                        "status, reviewed_by FROM dash_promotion_log "
                        "WHERE COALESCE(reviewed_at, created_at) > "
                        "NOW() - (:d || ' days')::interval "
                        "AND status IN ('approved','rejected') "
                        "ORDER BY 1 DESC LIMIT :lim"
                    ), {"d": days, "lim": limit}).fetchall():
                        kind = ("promotion_approved" if status == "approved"
                                else "promotion_rejected")
                        out.append({"ts": ts.isoformat() if ts else None,
                                    "kind": kind, "actor": by or "system",
                                    "target": name,
                                    "details": {"category": cat, "status": status}})
                except Exception:
                    pass

        out.sort(key=lambda r: r.get("ts") or "", reverse=True)
        return out[:limit]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ontology_audit failed")
        raise HTTPException(500, str(e))


# ── 14. Cluster suggest ────────────────────────────────────────────────────

def compute_cluster_suggestions(max_suggestions: int = 50) -> list[dict]:
    """Pure rule-based merge candidate detection over the template registry.

    Public helper, importable by cron daemons. Returns suggestions sorted
    descending by confidence. No DB writes, no auth.

    Args:
        max_suggestions: cap on returned candidates.

    Returns:
        List of dicts with keys: primary, merge_candidate, reason, confidence.
    """
    idx = _entities_index()
    normalized: dict[str, dict] = {}
    for name, rec in idx.items():
        aliases = {a.lower() for a in (rec.get("aliases") or set()) if a}
        normalized[name] = {
            "name_set": {name.lower()} | aliases,
            "aliases": aliases,
            "columns": {c.lower() for c in (rec.get("columns") or set())},
        }

    suggestions: list[dict] = []
    names = list(normalized.keys())
    seen: set[tuple[str, str]] = set()

    for i, a in enumerate(names):
        ra = normalized[a]
        if not ra["aliases"] and not ra["columns"]:
            continue
        for b in names[i + 1:]:
            rb = normalized[b]
            key = tuple(sorted((a, b)))
            if key in seen:
                continue

            # Rule 1: A's aliases ⊆ B's name+aliases → A is alias of B
            if ra["aliases"] and ra["aliases"].issubset(rb["name_set"]):
                seen.add(key)
                suggestions.append({"primary": b, "merge_candidate": a,
                    "reason": f"{a}'s aliases are subset of {b}'s names",
                    "confidence": 0.97})
                continue
            if rb["aliases"] and rb["aliases"].issubset(ra["name_set"]):
                seen.add(key)
                suggestions.append({"primary": a, "merge_candidate": b,
                    "reason": f"{b}'s aliases are subset of {a}'s names",
                    "confidence": 0.97})
                continue
            # Rule 2: ≥3 shared columns → merge candidate
            shared = ra["columns"] & rb["columns"]
            if len(shared) >= 3:
                union = ra["columns"] | rb["columns"]
                overlap = len(shared) / max(1, len(union))
                seen.add(key)
                primary = a if len(ra["columns"]) >= len(rb["columns"]) else b
                cand = b if primary == a else a
                suggestions.append({"primary": primary, "merge_candidate": cand,
                    "reason": f"share {len(shared)} columns ({overlap:.0%}): "
                              f"{', '.join(sorted(shared)[:5])}",
                    "confidence": round(0.6 + 0.3 * overlap, 3)})
            if len(suggestions) >= max_suggestions:
                break
        if len(suggestions) >= max_suggestions:
            break

    suggestions.sort(key=lambda r: -r["confidence"])
    return suggestions[:max_suggestions]


@router.post("/cluster-suggest")
def cluster_suggest(request: Request):
    """Aggressive alias / merge candidate detection.

    Pure Python rule-based heuristics over the template registry — no
    LLM call. Returns up to 50 suggestions.
    """
    _require_super_admin(request)
    try:
        suggestions = compute_cluster_suggestions(50)
        _cclear()
        return {"suggestions": suggestions}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("cluster_suggest failed")
        raise HTTPException(500, str(e))


# ── 15. Cluster daemon run-now ────────────────────────────────────────────

@router.post("/cluster/run-now")
def cluster_run_now(request: Request):
    """Trigger a single ontology auto-cluster cycle synchronously.

    Super-admin gated. Mirrors what the background daemon does on its
    interval. Used by the K8s CronJob and for local sanity testing.
    Returns counts (candidates_found, auto_merged, queued, errors).
    """
    _require_super_admin(request)
    try:
        from dash.cron.ontology_cluster_daemon import run_cycle
        out = run_cycle()
        _cclear()
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("cluster_run_now failed")
        raise HTTPException(500, str(e))


# ── 16. Benchmark sync (web → global Brain) ───────────────────────────────

@router.post("/benchmarks/sync-now")
async def benchmarks_sync_now(request: Request, industry: str = ""):
    """Trigger a single benchmark sync cycle synchronously.

    Super-admin gated. Used by the K8s CronJob (curl) and for manual
    admin runs. Returns counts:
    ``{fetched, parsed, promoted, rejected, errors}``.

    Optional ``?industry=retail`` narrows to a single industry key from
    ``BENCHMARK_SOURCES``.
    """
    _require_super_admin(request)
    try:
        from dash.learning.benchmark_sync import sync_benchmarks, BENCHMARK_SOURCES
        targets: list[str] | None = None
        if industry:
            if industry not in BENCHMARK_SOURCES:
                raise HTTPException(
                    400, f"unknown industry '{industry}'. "
                         f"Valid: {sorted(BENCHMARK_SOURCES.keys())}"
                )
            targets = [industry]
        out = await sync_benchmarks(industries=targets)
        _cclear()
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("benchmarks_sync_now failed")
        raise HTTPException(500, str(e))


@router.get("/benchmarks")
def benchmarks_list(
    request: Request,
    industry: str = "",
    kpi: str = "",
    limit: int = 200,
):
    """List the latest industry benchmarks held in the global Brain.

    Filters:
        - ``industry`` — exact match against ``metadata->>industry``.
        - ``kpi``      — case-insensitive substring against ``name``.

    No auth required for read (glossary/UI-facing); rows are global by
    construction (``project_slug IS NULL``, ``category='benchmark'``).
    """
    eng = _engine()
    from sqlalchemy import text as _t
    limit = max(1, min(int(limit or 200), 1000))

    sql = (
        "SELECT id, name, definition, metadata, updated_at "
        "FROM public.dash_company_brain "
        "WHERE category = 'benchmark' "
        "  AND project_slug IS NULL "
    )
    params: dict[str, Any] = {}
    if industry:
        sql += " AND metadata->>'industry' = :ind "
        params["ind"] = industry
    if kpi:
        sql += " AND LOWER(name) LIKE :kp "
        params["kp"] = f"%{kpi.lower()}%"
    sql += " ORDER BY updated_at DESC NULLS LAST, id DESC LIMIT :lim "
    params["lim"] = limit

    out: list[dict[str, Any]] = []
    try:
        with eng.connect() as cn:
            rows = cn.execute(_t(sql), params).fetchall()
        for r in rows:
            meta = r[3]
            if isinstance(meta, str):
                try:
                    import json as _j
                    meta = _j.loads(meta)
                except Exception:
                    meta = {}
            meta = meta or {}
            out.append({
                "id": int(r[0]),
                "kpi_name": r[1],
                "value": r[2],
                "industry": meta.get("industry") or "",
                "percentile": meta.get("percentile") or "",
                "unit": meta.get("unit") or "",
                "source": meta.get("source") or "",
                "source_url": meta.get("source_url") or "",
                "captured_at": meta.get("captured_at") or "",
                "updated_at": (
                    r[4].isoformat() if hasattr(r[4], "isoformat") else (
                        str(r[4]) if r[4] else ""
                    )
                ),
            })
    except Exception as e:
        logger.warning(f"benchmarks_list failed: {e}")
        return {"benchmarks": [], "error": str(e)[:200]}

    return {"benchmarks": out, "count": len(out)}


# ── 17. Public API key management (Phase E) ──────────────────────────────
# Issues bearer keys consumed by the public read API at /v1/ontology/*.
# Super-admin gated. Mirrors the embed key flow: secret returned ONCE.

class _ApiKeyCreateBody(BaseModel):
    name: str
    project_slug: str | None = None  # None == global (all projects readable)
    scope: dict | None = None        # {types,glossary,links,lineage,...}
    rate_limit_per_min: int | None = 60
    allowed_origins: list[str] | None = None


@router.post("/api-keys")
def create_ontology_api_key(body: _ApiKeyCreateBody, request: Request):
    """Issue a new ontology public-API bearer key.

    Returns the secret in plaintext ONCE — the caller must surface it
    immediately to the user; we only persist its sha256.
    """
    user = _require_super_admin(request)
    try:
        from dash.ontology_public.keys import create_key
        out = create_key(
            name=body.name,
            project_slug=body.project_slug,
            scope=body.scope,
            rate_limit_per_min=int(body.rate_limit_per_min or 60),
            allowed_origins=body.allowed_origins,
            user_id=int(user.get("id") or 0) or None,
        )
        return out
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_ontology_api_key failed")
        raise HTTPException(500, str(e))


@router.get("/api-keys")
def list_ontology_api_keys(request: Request, project_slug: str | None = None):
    """List all issued keys (no secrets)."""
    _require_super_admin(request)
    try:
        from dash.ontology_public.keys import list_keys
        return {"keys": list_keys(project_slug=project_slug)}
    except Exception as e:
        logger.exception("list_ontology_api_keys failed")
        raise HTTPException(500, str(e))


@router.post("/api-keys/{key_id}/rotate")
def rotate_ontology_api_key(key_id: int, request: Request):
    """Rotate the secret. Returns the new plaintext secret ONCE."""
    user = _require_super_admin(request)
    try:
        from dash.ontology_public.keys import rotate_secret, get_key
        new_secret = rotate_secret(int(key_id), user_id=int(user.get("id") or 0) or None)
        if not new_secret:
            raise HTTPException(404, "key not found or revoked")
        meta = get_key(int(key_id))
        return {"id": int(key_id), "secret": new_secret, "key": meta}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rotate_ontology_api_key failed")
        raise HTTPException(500, str(e))


@router.delete("/api-keys/{key_id}")
def revoke_ontology_api_key(key_id: int, request: Request):
    """Soft-delete: set status='revoked'. Audit rows preserved."""
    _require_super_admin(request)
    try:
        from dash.ontology_public.keys import revoke_key
        ok = revoke_key(int(key_id))
        if not ok:
            raise HTTPException(404, "key not found or already revoked")
        return {"ok": True, "id": int(key_id), "status": "revoked"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("revoke_ontology_api_key failed")
        raise HTTPException(500, str(e))


@router.get("/api-keys/{key_id}/usage")
def ontology_api_key_usage(key_id: int, request: Request, days: int = 14):
    """Per-key usage rollup: totals, p50/p95 latency, top endpoints, daily series."""
    _require_super_admin(request)
    try:
        from dash.ontology_public.keys import usage_stats, get_key
        meta = get_key(int(key_id))
        if not meta:
            raise HTTPException(404, "key not found")
        stats = usage_stats(int(key_id), days=int(days or 14))
        return {"key": meta, "usage": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ontology_api_key_usage failed")
        raise HTTPException(500, str(e))
