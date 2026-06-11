"""Repeated-question clustering — find the same questions asked over and over. [P2]

Pure read-only, deterministic analysis over the logged-question tables so a later
phase can decide which questions to pre-cache (see dash/learning/answer_cache.py).

NO LLM, NO embeddings — just SQL + python text normalization. Fast.

Sources (only the ones that genuinely hold a raw question + timestamp + slug):
- public.dash_feedback     — `question` text + `created_at` + `project_slug`.
- public.dash_embed_calls  — `message_text` (only populated when EMBED_LOG_BODIES=1)
                             + `ts`; project_slug comes via JOIN dash_agent_embeds
                             on embed_id (dash_embed_calls has no slug column).
- public.dash_traces is SKIPPED: the chat root meta (set_root_meta in
  app/projects.py) stamps only actor/channel/store_id — the raw user question is
  never written to meta, so there is nothing reliable to read.

Fail-soft everywhere: any error → [] / no-op, never raises.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Normalize a question for grouping. Mirrors dash/learning/answer_cache._norm."""
    return re.sub(r"\s+", " ", (s or "").lower().strip().rstrip(".?!"))


def _iso_utc(v) -> str | None:
    """Serialize a DB timestamp as ISO-8601 UTC ending in 'Z'. None-safe, never throws."""
    if v is None:
        return None
    try:
        iso = getattr(v, "isoformat", None)
        s = (v.isoformat() if callable(iso) else str(v)).strip()
        if not s:
            return None
        s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            return s
        t_idx = s.find("T")
        time_part = s[t_idx + 1:] if t_idx >= 0 else s
        if "+" in time_part or "-" in time_part:
            return s  # already tz-aware
        return s + "Z"
    except Exception:
        return None


def _max_ts(a, b):
    """Return the larger of two raw ts values (any None loses)."""
    if a is None:
        return b
    if b is None:
        return a
    try:
        return a if a >= b else b
    except Exception:
        return a


def recent_questions(project_slug: str, *, days: int = 30, limit: int = 500) -> list[dict]:
    """Raw logged questions used for clustering — debugging helper. Fail-soft → []."""
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        out: list[dict] = []
        eng = get_sql_engine()
        with eng.connect() as conn:
            try:
                fb = conn.execute(_text(
                    "SELECT question, created_at FROM public.dash_feedback "
                    "WHERE project_slug = :s AND question IS NOT NULL "
                    "  AND created_at >= now() - make_interval(days => :d) "
                    "ORDER BY created_at DESC LIMIT :lim"
                ), {"s": project_slug, "d": int(days), "lim": int(limit)}).fetchall()
                out += [{"question": r[0], "ts": _iso_utc(r[1]), "source": "feedback"} for r in fb]
            except Exception as exc:
                logger.debug("recent_questions feedback read failed: %s", exc)
            try:
                em = conn.execute(_text(
                    "SELECT c.message_text, c.ts FROM public.dash_embed_calls c "
                    "JOIN public.dash_agent_embeds e ON e.embed_id = c.embed_id "
                    "WHERE e.project_slug = :s AND c.message_text IS NOT NULL "
                    "  AND c.ts >= now() - make_interval(days => :d) "
                    "ORDER BY c.ts DESC LIMIT :lim"
                ), {"s": project_slug, "d": int(days), "lim": int(limit)}).fetchall()
                out += [{"question": r[0], "ts": _iso_utc(r[1]), "source": "embed"} for r in em]
            except Exception as exc:
                logger.debug("recent_questions embed read failed: %s", exc)
        return out[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.debug("recent_questions failed for %s: %s", project_slug, exc)
        return []


def cluster_questions(project_slug: str, *, days: int = 30, min_count: int = 2,
                      limit: int = 50) -> list[dict]:
    """Return frequent question clusters for a project, newest-window first.

    Reads logged questions (last `days` days) from the available log tables,
    normalizes each (lowercase, collapse whitespace, strip trailing punctuation),
    groups by normalized form, keeps clusters with COUNT >= min_count, returns
    up to `limit` clusters sorted by count desc then recency.

    Each cluster dict:
      {
        "representative": str,   # most common RAW form in the cluster
        "norm": str,             # the normalized grouping key
        "count": int,            # total occurrences across sources
        "variants": list[str],   # up to 5 distinct raw forms seen
        "last_seen": str | None, # ISO-8601 UTC of newest occurrence
        "sources": dict,         # {"feedback": n, "embed": n, ...} counts per source
      }

    Pure read-only, deterministic, no LLM, no embeddings. Fail-soft: any error -> [].
    """
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine

        rows: list[tuple[str, object, str]] = []  # (raw_question, raw_ts, source)
        eng = get_sql_engine()
        with eng.connect() as conn:
            try:
                for r in conn.execute(_text(
                    "SELECT question, created_at FROM public.dash_feedback "
                    "WHERE project_slug = :s AND question IS NOT NULL "
                    "  AND created_at >= now() - make_interval(days => :d)"
                ), {"s": project_slug, "d": int(days)}).fetchall():
                    rows.append((r[0], r[1], "feedback"))
            except Exception as exc:
                logger.debug("cluster_questions feedback read failed: %s", exc)
            try:
                for r in conn.execute(_text(
                    "SELECT c.message_text, c.ts FROM public.dash_embed_calls c "
                    "JOIN public.dash_agent_embeds e ON e.embed_id = c.embed_id "
                    "WHERE e.project_slug = :s AND c.message_text IS NOT NULL "
                    "  AND c.ts >= now() - make_interval(days => :d)"
                ), {"s": project_slug, "d": int(days)}).fetchall():
                    rows.append((r[0], r[1], "embed"))
            except Exception as exc:
                logger.debug("cluster_questions embed read failed: %s", exc)

        # Group by normalized form.
        groups: dict[str, dict] = {}
        for raw, ts, source in rows:
            raw = (raw or "").strip()
            norm = _norm(raw)
            if len(norm) < 3:  # drop empty / whitespace / very-short / nonsense
                continue
            g = groups.get(norm)
            if g is None:
                g = groups[norm] = {
                    "norm": norm,
                    "count": 0,
                    "raw_counts": Counter(),   # raw form -> sub-count
                    "sources": Counter(),
                    "last_ts": None,
                }
            g["count"] += 1
            g["raw_counts"][raw] += 1
            g["sources"][source] += 1
            g["last_ts"] = _max_ts(g["last_ts"], ts)

        clusters = []
        for g in groups.values():
            if g["count"] < min_count:
                continue
            # representative = most common raw form (tie → lexical for determinism)
            rep = max(g["raw_counts"].items(), key=lambda kv: (kv[1], kv[0]))[0]
            variants = [raw for raw, _ in g["raw_counts"].most_common(5)]
            clusters.append({
                "representative": rep,
                "norm": g["norm"],
                "count": g["count"],
                "variants": variants,
                "last_seen": _iso_utc(g["last_ts"]),
                "sources": dict(g["sources"]),
            })

        # Sort by count desc, then recency desc (None last), deterministic tiebreak.
        clusters.sort(key=lambda c: (c["count"], c["last_seen"] or "", c["norm"]),
                      reverse=True)
        return clusters[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.debug("cluster_questions failed for %s: %s", project_slug, exc)
        return []
