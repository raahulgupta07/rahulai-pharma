"""Forgetting curve + reinforcement — keep memory healthy.

Daily decay job:
- confidence_score -= 0.02 per day if not cited
- archive at 30 days uncited (still searchable, lower priority)
- delete from active queries at 90 days uncited (cold storage)
- decay_resistant memories bypass decay (>=5 citations OR manual flag)

Reinforcement (called from chat hooks):
- citation_count += 1
- last_cited_at = NOW()
- confidence_score += 0.05 (clamped to 1.0)
- if citation_count >= 5: set decay_resistant = TRUE

Promotion-back:
- archived memory cited again -> unarchive + bump confidence
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DecayResult:
    decayed_count: int = 0
    archived_count: int = 0
    unarchived_count: int = 0
    promoted_resistant_count: int = 0
    deleted_count: int = 0


def daily_decay_job(dash_engine=None, *,
                    decay_rate: float = 0.02,
                    archive_threshold_days: int = 30,
                    cold_storage_days: int = 90,
                    citation_threshold: int = 5) -> DecayResult:
    """Run forgetting curve sweep across all dash_memories.

    Should be called once per day via cron.
    """
    result = DecayResult()
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            # 1. Decay confidence on uncited memories (not decay_resistant)
            r1 = conn.execute(text(
                "UPDATE public.dash_memories SET "
                " confidence_score = GREATEST(0.0, confidence_score - :rate) "
                "WHERE (decay_resistant IS NULL OR decay_resistant = FALSE) "
                "  AND (archived IS NULL OR archived = FALSE) "
                "  AND (last_cited_at IS NULL OR last_cited_at < NOW() - INTERVAL '1 day') "
                "RETURNING 1"
            ), {"rate": decay_rate})
            result.decayed_count = r1.rowcount or 0

            # 2. Archive memories uncited for >30 days
            r2 = conn.execute(text(
                "UPDATE public.dash_memories SET archived = TRUE "
                "WHERE (decay_resistant IS NULL OR decay_resistant = FALSE) "
                "  AND (archived IS NULL OR archived = FALSE) "
                "  AND created_at < NOW() - (:d * INTERVAL '1 day') "
                "  AND (last_cited_at IS NULL "
                "       OR last_cited_at < NOW() - (:d * INTERVAL '1 day')) "
                "RETURNING 1"
            ), {"d": archive_threshold_days})
            result.archived_count = r2.rowcount or 0

            # 3. Promote frequent citations to decay_resistant
            r3 = conn.execute(text(
                "UPDATE public.dash_memories SET decay_resistant = TRUE "
                "WHERE (decay_resistant IS NULL OR decay_resistant = FALSE) "
                "  AND citation_count >= :n "
                "RETURNING 1"
            ), {"n": citation_threshold})
            result.promoted_resistant_count = r3.rowcount or 0

            # 4. Cold-storage flag for >90 days uncited (kept in DB, lower
            #    priority — Analyst skips them unless explicit request)
            #    Implemented as confidence floor of 0.05 + archived
            r4 = conn.execute(text(
                "UPDATE public.dash_memories SET "
                " confidence_score = LEAST(0.05, confidence_score) "
                "WHERE archived = TRUE "
                "  AND (decay_resistant IS NULL OR decay_resistant = FALSE) "
                "  AND created_at < NOW() - (:d * INTERVAL '1 day') "
                "  AND (last_cited_at IS NULL "
                "       OR last_cited_at < NOW() - (:d * INTERVAL '1 day')) "
                "RETURNING 1"
            ), {"d": cold_storage_days})
            result.deleted_count = r4.rowcount or 0

            conn.commit()
    except Exception as e:
        logger.exception(f"daily_decay_job failed: {e}")

    return result


def reinforce_memory(memory_id: int, dash_engine=None, *,
                     citation_bump: float = 0.05,
                     auto_unarchive: bool = True) -> bool:
    """Hook called from chat citation. Bump counters + unarchive if needed.

    Returns True on success.
    """
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            update_clauses = (
                "citation_count = COALESCE(citation_count, 0) + 1, "
                "last_cited_at = NOW(), "
                "confidence_score = LEAST(1.0, COALESCE(confidence_score, 0.5) + :bump)"
            )
            if auto_unarchive:
                update_clauses += ", archived = FALSE"

            conn.execute(text(
                f"UPDATE public.dash_memories SET {update_clauses} "
                f"WHERE id = :id"
            ), {"bump": citation_bump, "id": memory_id})
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"reinforce_memory({memory_id}) failed: {e}")
        return False


def reinforce_by_text(fact_substring: str, dash_engine=None,
                      project_slug: Optional[str] = None,
                      max_matches: int = 10) -> int:
    """Bulk reinforce memories whose fact contains the given substring.

    Used as fallback when chat doesn't have explicit memory_id citations
    but mentions a learned fact.

    Returns count of memories reinforced.
    """
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id FROM public.dash_memories "
                "WHERE LOWER(fact) LIKE :p "
                "  AND (project_slug = :s OR project_slug IS NULL) "
                "  AND (archived IS NULL OR archived = FALSE) "
                "ORDER BY confidence_score DESC NULLS LAST "
                "LIMIT :n"
            ), {"p": f"%{fact_substring.lower()}%",
                "s": project_slug, "n": max_matches}).fetchall()
            ids = [r[0] for r in rows]
        for mid in ids:
            reinforce_memory(mid, dash_engine=eng)
        return len(ids)
    except Exception as e:
        logger.warning(f"reinforce_by_text failed: {e}")
        return 0


def stats(dash_engine=None) -> dict:
    """Quick stats for monitoring memory health."""
    out = {
        "total": 0, "active": 0, "archived": 0, "decay_resistant": 0,
        "high_confidence": 0, "low_confidence": 0,
        "avg_citation_count": 0.0,
    }
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT "
                " COUNT(*) AS total, "
                " COUNT(*) FILTER (WHERE archived IS NULL OR archived = FALSE) AS active, "
                " COUNT(*) FILTER (WHERE archived = TRUE) AS archived, "
                " COUNT(*) FILTER (WHERE decay_resistant = TRUE) AS resistant, "
                " COUNT(*) FILTER (WHERE confidence_score >= 0.8) AS high_conf, "
                " COUNT(*) FILTER (WHERE confidence_score < 0.3) AS low_conf, "
                " COALESCE(AVG(citation_count), 0) AS avg_cites "
                "FROM public.dash_memories"
            )).fetchone()
            if r:
                out = {
                    "total": int(r[0] or 0),
                    "active": int(r[1] or 0),
                    "archived": int(r[2] or 0),
                    "decay_resistant": int(r[3] or 0),
                    "high_confidence": int(r[4] or 0),
                    "low_confidence": int(r[5] or 0),
                    "avg_citation_count": float(r[6] or 0),
                }
    except Exception as e:
        logger.warning(f"stats failed: {e}")
    return out
