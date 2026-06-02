"""
Entity Linker — KG Extraction Stats API
=======================================

Surfaces per-project counts of regex vs llm-extracted triples and the
estimated cost differential ($0.0001/LLM call assumed, $0 for regex).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import NullPool, create_engine, text

from app.auth import get_current_user
from db import db_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["entity-linker"])

_engine = create_engine(db_url, poolclass=NullPool)

_LLM_COST_PER_CALL = 0.0001  # rough avg per chat triple-extraction call


def _detect_schema() -> str:
    """Return the schema where ``dash_knowledge_triples`` lives."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = 'dash_knowledge_triples' "
                "  AND table_schema IN ('dash','public','ai') "
                "ORDER BY CASE table_schema "
                "  WHEN 'dash' THEN 1 WHEN 'public' THEN 2 ELSE 3 END "
                "LIMIT 1"
            )).fetchone()
        return row[0] if row else "public"
    except Exception:
        return "public"


@router.get("/{slug}/kg/extraction-stats")
def extraction_stats(
    slug: str,
    days: int = Query(14, ge=1, le=365),
    user=Depends(get_current_user),
) -> dict:
    """Per-project breakdown of regex vs llm-extracted triples + cost saved."""
    schema = _detect_schema()
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT COALESCE(extractor, 'llm') AS extractor, COUNT(*) AS cnt, "
                f"       COALESCE(SUM(extraction_cost_usd), 0) AS cost "
                f"  FROM {schema}.dash_knowledge_triples "
                f" WHERE project_slug = :slug "
                f"   AND created_at > now() - (:days || ' days')::interval "
                f" GROUP BY 1"
            ), {"slug": slug, "days": str(days)}).fetchall()
    except Exception as exc:
        log.warning("extraction_stats query failed: %s", exc)
        raise HTTPException(500, f"query failed: {exc}")

    counts: dict[str, int] = {"regex": 0, "llm": 0, "llm_fallback": 0}
    actual_cost = 0.0
    for r in rows:
        ext = (r[0] or "llm").lower()
        cnt = int(r[1] or 0)
        cost = float(r[2] or 0)
        if ext not in counts:
            counts[ext] = 0
        counts[ext] += cnt
        actual_cost += cost

    total = sum(counts.values())
    llm_total = counts.get("llm", 0) + counts.get("llm_fallback", 0)
    regex_total = counts.get("regex", 0)

    # "If we had used LLM for everything" baseline.
    baseline_cost = total * _LLM_COST_PER_CALL
    cost_saved = max(baseline_cost - actual_cost, 0.0)
    saved_pct = (cost_saved / baseline_cost * 100) if baseline_cost > 0 else 0.0

    return {
        "project_slug": slug,
        "days": days,
        "counts": counts,
        "total": total,
        "regex_total": regex_total,
        "llm_total": llm_total,
        "actual_cost_usd": round(actual_cost, 6),
        "baseline_cost_usd": round(baseline_cost, 6),
        "cost_saved_usd": round(cost_saved, 6),
        "cost_saved_pct": round(saved_pct, 2),
        "llm_cost_per_call_assumed": _LLM_COST_PER_CALL,
    }
