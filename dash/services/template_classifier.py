"""Template fit-scoring + suggestion service.

Reads dash.dash_custom_agents WHERE source='builtin' AND is_promoted_global=true.
For each template, scores fit against the project's:
  - user_* table columns (overlap w/ schema_keywords)
  - brain entities (overlap w/ entity_types)
  - upload modality mix (pdf/xlsx/csv counts)

Returns ranked list[{template_id, name, score, signals}].

All DB reads via db.session.get_sql_engine (read-only path).
Fail-soft everywhere — returns [] on any error.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _gather_project_context(slug: str) -> dict[str, Any]:
    """Collect signals for fit scoring: column names, entity types, modality mix."""
    ctx: dict[str, Any] = {
        "columns": set(),       # lowercased column names across user_* tables
        "entities": set(),      # lowercased brain entity names
        "modality": {"pdf": 0, "xlsx": 0, "csv": 0, "other": 0},
        "total_uploads": 0,
    }
    try:
        eng = _engine()
        with eng.connect() as cx:
            # 1) Columns from project's user_<slug> schema (or fallback patterns).
            try:
                rows = cx.execute(text("""
                    SELECT lower(column_name) AS col
                    FROM information_schema.columns
                    WHERE table_schema = :sch
                """), {"sch": f"user_{slug}"}).fetchall()
                for r in rows:
                    if r[0]:
                        ctx["columns"].add(r[0])
            except Exception:
                logger.debug("template_classifier: column scan failed for %s", slug)

            # 2) Brain entities (project-scoped) — name lookups.
            try:
                rows = cx.execute(text("""
                    SELECT lower(name)
                    FROM public.dash_company_brain
                    WHERE (project_slug = :p OR project_slug IS NULL)
                      AND name IS NOT NULL
                    LIMIT 500
                """), {"p": slug}).fetchall()
                for r in rows:
                    if r[0]:
                        ctx["entities"].add(r[0])
            except Exception:
                logger.debug("template_classifier: brain scan failed for %s", slug)

            # 3) Modality mix — uploaded file extensions for this project.
            try:
                rows = cx.execute(text("""
                    SELECT lower(filename) AS fn
                    FROM public.dash_documents
                    WHERE project_slug = :p
                    LIMIT 2000
                """), {"p": slug}).fetchall()
                for r in rows:
                    fn = r[0] or ""
                    ctx["total_uploads"] += 1
                    if fn.endswith(".pdf"):
                        ctx["modality"]["pdf"] += 1
                    elif fn.endswith(".xlsx") or fn.endswith(".xls"):
                        ctx["modality"]["xlsx"] += 1
                    elif fn.endswith(".csv"):
                        ctx["modality"]["csv"] += 1
                    else:
                        ctx["modality"]["other"] += 1
            except Exception:
                logger.debug("template_classifier: docs scan failed for %s", slug)
    except Exception:
        logger.exception("template_classifier._gather_project_context failed")
    return ctx


def _score_template(template: dict, ctx: dict) -> tuple[float, dict]:
    """Score a single template against project ctx.

    Returns (score 0-1, matched signals dict).
    Weighted: 0.45 schema_keywords + 0.30 entity_types + 0.10 domain_phrases
              + 0.15 modality_alignment.
    """
    sig = template.get("fit_signals") or {}
    if not isinstance(sig, dict):
        sig = {}

    kws = [str(x).lower() for x in (sig.get("schema_keywords") or [])]
    ents = [str(x).lower() for x in (sig.get("entity_types") or [])]
    phrases = [str(x).lower() for x in (sig.get("domain_phrases") or [])]
    modality = sig.get("modality") or {}

    matched: dict[str, Any] = {
        "schema_keywords": [], "entity_types": [],
        "domain_phrases": [], "modality_score": 0.0,
    }

    # --- schema keyword overlap (any substring match across columns) ---
    col_blob = " ".join(ctx["columns"]) if ctx["columns"] else ""
    kw_hits = 0
    for kw in kws:
        if kw and kw in col_blob:
            kw_hits += 1
            matched["schema_keywords"].append(kw)
    kw_score = (kw_hits / len(kws)) if kws else 0.0

    # --- entity overlap ---
    ent_hits = 0
    for e in ents:
        if e and any(e in name for name in ctx["entities"]):
            ent_hits += 1
            matched["entity_types"].append(e)
    ent_score = (ent_hits / len(ents)) if ents else 0.0

    # --- domain phrase hit (any in brain blob) ---
    brain_blob = " ".join(ctx["entities"]) if ctx["entities"] else ""
    ph_hits = 0
    for p in phrases:
        if p and p in brain_blob:
            ph_hits += 1
            matched["domain_phrases"].append(p)
    ph_score = (ph_hits / len(phrases)) if phrases else 0.0

    # --- modality alignment (cosine-ish over share vectors) ---
    mod_score = 0.0
    total = ctx["total_uploads"] or 0
    if total > 0 and modality:
        # project share
        ps = {
            "pdf": ctx["modality"]["pdf"] / total,
            "xlsx": ctx["modality"]["xlsx"] / total,
            "csv": ctx["modality"]["csv"] / total,
        }
        # template ideal share
        ts = {
            "pdf": float(modality.get("pdf", 0) or 0),
            "xlsx": float(modality.get("xlsx", 0) or 0),
            "csv": float(modality.get("csv", 0) or 0),
        }
        # 1 - L1 distance / 2  (rough alignment, 0-1)
        dist = sum(abs(ps[k] - ts[k]) for k in ps) / 2.0
        mod_score = max(0.0, 1.0 - dist)
    matched["modality_score"] = round(mod_score, 3)

    score = (
        0.45 * kw_score
        + 0.30 * ent_score
        + 0.10 * ph_score
        + 0.15 * mod_score
    )
    return float(min(1.0, max(0.0, score))), matched


def classify_for_project(slug: str, top_n: int = 3) -> list[dict]:
    """Return top-N templates ranked by data-fit score."""
    if not slug:
        return []
    try:
        eng = _engine()
        with eng.connect() as cx:
            rows = cx.execute(text("""
                SELECT id, name, description, base_agent, scoped_tools, fit_signals
                FROM dash.dash_custom_agents
                WHERE source = 'builtin'
                  AND is_promoted_global = TRUE
                  AND enabled = TRUE
            """)).fetchall()
        if not rows:
            return []

        ctx = _gather_project_context(slug)
        scored: list[dict] = []
        for r in rows:
            tpl = {
                "id": r[0], "name": r[1], "description": r[2],
                "base_agent": r[3], "scoped_tools": r[4] or [],
                "fit_signals": r[5] or {},
            }
            score, signals = _score_template(tpl, ctx)
            scored.append({
                "template_id": tpl["id"],
                "name": tpl["name"],
                "description": tpl["description"],
                "base_agent": tpl["base_agent"],
                "score": round(score, 4),
                "signals": signals,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[: max(1, int(top_n))]
    except Exception:
        logger.exception("classify_for_project failed for slug=%s", slug)
        return []


__all__ = ["classify_for_project"]
