"""Phase G — finding retention memory loop.

Tracks which findings users keep vs dismiss per project. Scout uses this
to bias future surfacing toward "stuff users like." Cross-tenant promotion
job (`promotion.py`) reads aggregates here.

Fail-safe by design: every public function swallows exceptions and returns
a safe default so Scout/Designer never break if the DB is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# WHY: keep in module so tests can monkeypatch
_SQL_KEYWORDS_RE = re.compile(r"\b(GROUP\s+BY|ORDER\s+BY|JOIN|WHERE|HAVING|UNION|DISTINCT)\b", re.I)
_TABLE_COL_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", re.I)


def _engine():
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _ensure_tables() -> None:
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_finding_retention ("
            "id BIGSERIAL PRIMARY KEY,"
            "project_slug TEXT NOT NULL,"
            "finding_hash TEXT NOT NULL,"
            "finding_signature JSONB,"
            "headline TEXT,"
            "surface_count INT DEFAULT 1,"
            "keep_count INT DEFAULT 0,"
            "dismiss_count INT DEFAULT 0,"
            "last_seen TIMESTAMPTZ DEFAULT now(),"
            "UNIQUE(project_slug, finding_hash))"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_retention_signature "
            "ON public.dash_finding_retention((finding_signature->>'severity'), project_slug)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_finding_promotions ("
            "id BIGSERIAL PRIMARY KEY,"
            "finding_hash TEXT NOT NULL,"
            "headline TEXT,"
            "pattern JSONB,"
            "contributing_projects TEXT[],"
            "promotion_score FLOAT DEFAULT 0,"
            "promoted_at TIMESTAMPTZ DEFAULT now(),"
            "UNIQUE(finding_hash))"
        ))


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _finding_attr(f: Any, name: str, default: Any = "") -> Any:
    if isinstance(f, dict):
        return f.get(name, default)
    return getattr(f, name, default)


def hash_finding(f: Any) -> str:
    """sha256 of normalized headline + first 200 chars of cause hypothesis."""
    headline = _normalize(_finding_attr(f, "headline", ""))
    cause = _normalize(_finding_attr(f, "cause_hypothesis", ""))[:200]
    payload = f"{headline}|{cause}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def signature_finding(f: Any) -> dict:
    """Extract SQL keywords + table.cols + domain_tags + severity."""
    sql = _finding_attr(f, "sql", "") or ""
    keywords = sorted({k.upper().replace("  ", " ") for k in _SQL_KEYWORDS_RE.findall(sql)})
    columns = sorted({f"{a}.{b}".lower() for a, b in _TABLE_COL_RE.findall(sql)})
    return {
        "sql_keywords": keywords,
        "columns": columns[:12],
        "domain_tags": list(_finding_attr(f, "domain_tags", []) or []),
        "severity": _finding_attr(f, "severity", "medium"),
    }


def record_surface(project_slug: str, f: Any) -> str | None:
    """Upsert a row, increment surface_count. Returns finding_hash, or None on error."""
    if not project_slug:
        return None
    try:
        _ensure_tables()
        h = hash_finding(f)
        sig = signature_finding(f)
        headline = (_finding_attr(f, "headline", "") or "")[:500]
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_finding_retention "
                "(project_slug, finding_hash, finding_signature, headline, surface_count) "
                "VALUES (:p, :h, CAST(:sig AS JSONB), :hl, 1) "
                "ON CONFLICT (project_slug, finding_hash) DO UPDATE "
                "SET surface_count = public.dash_finding_retention.surface_count + 1, "
                "    last_seen = now(), "
                "    finding_signature = EXCLUDED.finding_signature, "
                "    headline = EXCLUDED.headline"
            ), {"p": project_slug, "h": h, "sig": json.dumps(sig), "hl": headline})
        return h
    except Exception as e:
        logger.debug(f"record_surface failed: {e}")
        return None


def _bump(project_slug: str, finding_hash: str, column: str) -> bool:
    if not project_slug or not finding_hash or column not in ("keep_count", "dismiss_count"):
        return False
    try:
        _ensure_tables()
        eng = _engine()
        with eng.begin() as conn:
            res = conn.execute(text(
                f"UPDATE public.dash_finding_retention "
                f"SET {column} = {column} + 1, last_seen = now() "
                f"WHERE project_slug = :p AND finding_hash = :h"
            ), {"p": project_slug, "h": finding_hash})
            if res.rowcount == 0:
                # WHY: insert a stub so the counter is preserved even if surface wasn't logged
                conn.execute(text(
                    "INSERT INTO public.dash_finding_retention "
                    "(project_slug, finding_hash, surface_count, keep_count, dismiss_count) "
                    "VALUES (:p, :h, 0, :k, :d) "
                    "ON CONFLICT (project_slug, finding_hash) DO NOTHING"
                ), {"p": project_slug, "h": finding_hash,
                    "k": 1 if column == "keep_count" else 0,
                    "d": 1 if column == "dismiss_count" else 0})
        return True
    except Exception as e:
        logger.debug(f"_bump {column} failed: {e}")
        return False


def record_keep(project_slug: str, f_or_hash: Any) -> bool:
    h = f_or_hash if isinstance(f_or_hash, str) else hash_finding(f_or_hash)
    return _bump(project_slug, h, "keep_count")


def record_dismiss(project_slug: str, f_or_hash: Any) -> bool:
    h = f_or_hash if isinstance(f_or_hash, str) else hash_finding(f_or_hash)
    return _bump(project_slug, h, "dismiss_count")


def get_retained_findings(project_slug: str, top_n: int = 10) -> list[dict]:
    """Findings the user has kept more than dismissed, ordered by keep_count DESC."""
    if not project_slug:
        return []
    try:
        _ensure_tables()
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT finding_hash, headline, finding_signature, "
                "       keep_count, dismiss_count, surface_count "
                "FROM public.dash_finding_retention "
                "WHERE project_slug = :p AND keep_count > dismiss_count "
                "ORDER BY keep_count DESC, last_seen DESC LIMIT :n"
            ), {"p": project_slug, "n": top_n}).fetchall()
        out = []
        for r in rows:
            sig = r[2]
            if isinstance(sig, str):
                try: sig = json.loads(sig)
                except Exception: sig = {}
            out.append({
                "finding_hash": r[0], "headline": r[1] or "",
                "signature": sig or {},
                "keep_count": r[3], "dismiss_count": r[4], "surface_count": r[5],
            })
        return out
    except Exception as e:
        logger.debug(f"get_retained_findings failed: {e}")
        return []


def should_promote(finding_hash: str, min_projects: int = 3, min_avg_keep: float = 2.0) -> bool:
    """True if finding present in >= min_projects with avg keep_count >= min_avg_keep."""
    if not finding_hash:
        return False
    try:
        _ensure_tables()
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(DISTINCT project_slug), COALESCE(AVG(keep_count), 0) "
                "FROM public.dash_finding_retention "
                "WHERE finding_hash = :h AND keep_count > 0"
            ), {"h": finding_hash}).fetchone()
        if not row:
            return False
        n_proj, avg_keep = int(row[0] or 0), float(row[1] or 0)
        return n_proj >= min_projects and avg_keep >= min_avg_keep
    except Exception as e:
        logger.debug(f"should_promote failed: {e}")
        return False
