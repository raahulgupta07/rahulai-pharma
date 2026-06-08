"""Brain Wiki — auto-generated, backlinked concept pages.

A human-readable wiki built live from what the agent already knows:
  - public.dash_company_brain  (glossary / formula / alias / kpi / pattern / org)
  - public.dash_knowledge_triples  (subject -predicate-> object) → backlinks

  GET /api/projects/{slug}/wiki              -> concept index (grouped, searchable)
  GET /api/projects/{slug}/wiki/page?name=   -> one page + links-out + backlinks

No LLM, no new tables — pure projection of the agent's knowledge. The graph is
the map; this is the readable wiki. Fail-soft throughout.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Wiki"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)
logger = logging.getLogger(__name__)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug):
        raise HTTPException(403, "Access denied")


# junk values that leak into the KG from cell-value extraction (Yes/No flags,
# null markers, single chars). These pollute the wiki as fake "entity" concepts.
_JUNK_ENTITIES = {
    "yes", "no", "n0", "n/a", "na", "none", "null", "nil", "true", "false",
    "nan", "-", "'-", "\\0", "\x00", "0", "1", "unknown", "other", "n.a.",
}


def _is_junk_entity(name: str) -> bool:
    n = (name or "").strip().lower()
    if len(n) <= 1:
        return True
    if n in _JUNK_ENTITIES:
        return True
    if not any(c.isalnum() for c in n):  # pure punctuation/control
        return True
    return False


def _build_index(slug: str) -> dict:
    """concept name(lower) -> {name, category, body, aliases, out:[], back:[]}"""
    idx: dict = {}

    def node(name: str, category: str = "entity", body: str = ""):
        if not name:
            return None
        k = name.strip().lower()
        if not k:
            return None
        # curated brain entries (real category) always pass; bare KG entities
        # get the junk filter so Yes/No/\0/N0 never become wiki pages.
        if category == "entity" and _is_junk_entity(name):
            return None
        e = idx.get(k)
        if not e:
            e = {"name": name.strip(), "category": category, "body": body,
                 "aliases": [], "out": [], "back": []}
            idx[k] = e
        else:
            # prefer a real brain category/body over a bare entity
            if e["category"] == "entity" and category != "entity":
                e["category"] = category
            if not e["body"] and body:
                e["body"] = body
        return e

    with _engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # brain entries (project + global)
        try:
            rows = conn.execute(text(
                "SELECT name, category, definition, metadata FROM public.dash_company_brain "
                "WHERE project_slug=:s OR project_slug IS NULL "
                "ORDER BY name"
            ), {"s": slug}).fetchall()
            for nm, cat, defn, meta in rows:
                e = node(nm, cat or "glossary", defn or "")
                if e and isinstance(meta, dict):
                    al = meta.get("aliases")
                    if isinstance(al, list):
                        e["aliases"] = [str(a) for a in al][:8]
        except Exception:
            logger.exception("wiki brain %s", slug)

        # KG triples → links + entity nodes
        try:
            rows = conn.execute(text(
                "SELECT subject, predicate, object FROM public.dash_knowledge_triples "
                "WHERE project_slug=:s"
            ), {"s": slug}).fetchall()
            for subj, pred, obj in rows:
                if not subj or not obj:
                    continue
                sn = node(subj)
                on = node(obj)
                if not sn or not on:  # one side filtered as junk → drop the edge
                    continue
                sn["out"].append({"predicate": pred or "related", "target": obj.strip()})
                on["back"].append({"predicate": pred or "related", "source": subj.strip()})
        except Exception:
            logger.exception("wiki triples %s", slug)

    return idx


@router.get("/{slug}/wiki")
def wiki_index(slug: str, request: Request, q: str = ""):
    user = _get_user(request)
    _check_access(user, slug)
    idx = _build_index(slug)
    ql = q.strip().lower()
    pages = []
    for e in idx.values():
        if ql and ql not in e["name"].lower() and ql not in (e["body"] or "").lower():
            continue
        pages.append({
            "name": e["name"],
            "category": e["category"],
            "links": len(e["out"]) + len(e["back"]),
            "snippet": (e["body"] or "")[:120],
            "has_body": bool(e["body"]),
        })
    pages.sort(key=lambda p: (-p["links"], p["name"].lower()))
    by_cat: dict = {}
    for p in pages:
        by_cat.setdefault(p["category"], 0)
        by_cat[p["category"]] += 1
    return {"slug": slug, "total": len(pages), "by_category": by_cat, "pages": pages}


@router.get("/{slug}/wiki/page")
def wiki_page(slug: str, request: Request, name: str):
    user = _get_user(request)
    _check_access(user, slug)
    idx = _build_index(slug)
    e = idx.get(name.strip().lower())
    if not e:
        raise HTTPException(404, "concept not found")
    # siblings = same category (excluding self), top by link count
    sib = sorted(
        [o for o in idx.values()
         if o["category"] == e["category"] and o["name"].lower() != e["name"].lower()],
        key=lambda o: -(len(o["out"]) + len(o["back"])))[:8]
    # mark which link targets/sources exist as their own pages
    def exists(n):
        return n.strip().lower() in idx
    out = [{"predicate": l["predicate"], "target": l["target"], "page": exists(l["target"])}
           for l in e["out"]]
    back = [{"predicate": l["predicate"], "source": l["source"], "page": exists(l["source"])}
            for l in e["back"]]
    return {
        "name": e["name"],
        "category": e["category"],
        "body": e["body"],
        "aliases": e["aliases"],
        "links_out": out,
        "backlinks": back,
        "siblings": [s["name"] for s in sib],
    }
