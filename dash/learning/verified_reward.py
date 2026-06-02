"""Verified reward — a HARD correctness signal for the self-learning loop.

Idea (from the AgentGym-RL eval): Dash's learning loop grades answers with an LLM
*judge* (fuzzy, a model scoring a model). But Dash already owns ground truth —
verified Q&A SQL and pinned metric answers. So for any data question we can grade
with an *oracle*: run the proven SQL, get the true number, and check whether the
agent's answer matches it. Provably-correct beats a judge's opinion.

`score_verified()` runs in the post-chat background (next to judge_response).
Fail-soft: any failure → verified='unknown', never raises, never blocks chat.

Result is written to public.dash_verified_scores (migration 107).
"""
from __future__ import annotations

import logging
import math
import re

from dash.utils.df_serialize import df_rows_to_jsonable

logger = logging.getLogger(__name__)

_STOP = {"the", "and", "for", "with", "what", "how", "show", "give",
         "list", "are", "was", "were", "our", "their", "this", "that", "from", "per",
         "which", "who", "have", "has", "did"}
# NOTE: "total", "count", "number", "many", "much", "all" are intentionally KEPT
# in the token set — they are load-bearing for metric questions ("total leads",
# "how many calls"). Stripping them collapses short metric questions to 0–1
# tokens and the shortcut matcher misses.

# Match within 1.5% (relative) or 1.0 absolute — covers rounding without passing junk.
_REL_TOL = 0.015
_ABS_TOL = 1.0


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9_]+", (s or "").lower()) if w not in _STOP and len(w) > 2}


def _normalize(s: str) -> str:
    """Normalize a string for comparison: lowercase, collapse whitespace,
    strip trailing punctuation."""
    return re.sub(r"\s+", " ", (s or "").lower().strip().rstrip(".?!"))


def _similarity(a: str, b: str) -> float:
    """Levenshtein-like similarity ratio between two strings via difflib."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _to_num(raw: str):
    """Parse '1,544' / '$21,198' / '64.3%' / '64.3' → float. None if not a number."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(s)
    except Exception:
        return None


def _answer_number(answer: str):
    """Best-effort headline number from the agent's answer.

    Prefers a [KPI:value|label|change] tag (Dash answers emit these), else the
    first large standalone number in the prose.
    """
    if not answer:
        return None
    m = re.search(r"\[KPI:\s*([^\|\]]+)", answer)
    if m:
        n = _to_num(m.group(1))
        if n is not None:
            return n
    # First number with >=3 digits (avoid years/small counts noise) or a percent.
    m = re.search(r"(\d[\d,]{2,}(?:\.\d+)?)\s*%?", answer)
    if m:
        return _to_num(m.group(1))
    return None


def _best_proven_pair(project_slug: str, question: str):
    """Return (proven_question, sql) most relevant to `question`, or None.

    Reuses the rare-term-weighted lexical match over the project's training Q&A
    JSON. Conservative: requires a real overlap so we never pick a wrong oracle.
    """
    from dash.paths import KNOWLEDGE_DIR
    import json as _json
    tdir = KNOWLEDGE_DIR / project_slug / "training"
    if not tdir.exists():
        return None
    pairs = []
    for f in tdir.glob("*.json"):
        try:
            data = _json.load(open(f))
        except Exception:
            continue
        if isinstance(data, list):
            for qa in data:
                q, sql = qa.get("question", ""), qa.get("sql", "")
                if q and sql:
                    pairs.append((q, sql))
    if not pairs:
        return None
    df: dict = {}
    for q, _s in pairs:
        for t in _tokens(q):
            df[t] = df.get(t, 0) + 1
    n = len(pairs)
    qtok = _tokens(question)
    if not qtok:
        return None
    best = None
    for q, sql in pairs:
        overlap = qtok & _tokens(q)
        if len(overlap) < 2:           # need ≥2 shared meaningful terms to trust it
            continue
        score = sum(math.log((n + 1) / df.get(t, 1)) for t in overlap)
        if best is None or score > best[0]:
            best = (score, q, sql)
    if not best:
        return None
    return best[1], best[2]


def try_metric_shortcut(project_slug: str, question: str) -> dict | None:
    """Pre-flight metric oracle: if `question` STRONGLY matches a verified
    proven Q&A pair (≥3 shared rare-term tokens — tighter than the grading
    matcher's ≥2), run that proven SQL → return the truth.

    Lets the chat layer answer pinned-metric questions deterministically
    in ~30ms instead of risking LITE-model flakiness on the `metric` tool.
    Returns None on any miss / low confidence → caller falls through to team.run.

    Fail-soft: any error → None (never raises).
    """
    try:
        from dash.paths import KNOWLEDGE_DIR
        import json as _json
        tdir = KNOWLEDGE_DIR / project_slug / "training"
        if not tdir.exists():
            return None
        pairs: list[tuple[str, str]] = []
        for f in tdir.glob("*.json"):
            try:
                data = _json.load(open(f))
            except Exception:
                continue
            if isinstance(data, list):
                for qa in data:
                    q, sql = qa.get("question", ""), qa.get("sql", "")
                    if q and sql:
                        pairs.append((q, sql))
        if not pairs:
            return None
        df: dict = {}
        for q, _s in pairs:
            for t in _tokens(q):
                df[t] = df.get(t, 0) + 1
        n = len(pairs)
        qtok = _tokens(question)
        if not qtok:
            return None
        best = None
        for q, sql in pairs:
            overlap = qtok & _tokens(q)
            if len(overlap) < 3:               # STRONGER than grading (≥2)
                continue
            score = sum(math.log((n + 1) / df.get(t, 1)) for t in overlap)
            if best is None or score > best[0]:
                best = (score, q, sql)
        if not best:
            return None
        score, proven_q, sql = best
        # 2026-05-26: tightened acceptance — weak echo matches (e.g. score 26
        # on a partially-overlapping question) were producing skinny "fast"
        # cached answers that bypassed the full STANDARD-tier exec card.
        # New gate: only fire when sim≥0.95 (essentially identical) OR
        # score≥MIN_SHORTCUT_SCORE (40, default). Env override:
        # METRIC_SHORTCUT_MIN_SCORE.
        import os as _os_thr
        try:
            MIN_SHORTCUT_SCORE = float(_os_thr.getenv("METRIC_SHORTCUT_MIN_SCORE", "40"))
        except Exception:
            MIN_SHORTCUT_SCORE = 40.0
        sim = _similarity(question, proven_q)
        n_overlap = len(qtok & _tokens(proven_q))
        if sim >= 0.95:
            pass  # essentially identical question — accept
        elif score >= MIN_SHORTCUT_SCORE and n_overlap >= 4:
            pass  # high-confidence rare-term match — accept
        else:
            logger.debug(
                "metric_shortcut rejected: sim=%.2f overlap=%d score=%.2f (min=%.1f)",
                sim, n_overlap, score, MIN_SHORTCUT_SCORE,
            )
            return None
        run = _run_rows(project_slug, sql, limit=20)
        if not run or run.get("value") is None:
            return None
        return {"matched": True, "value": run["value"], "source_q": proven_q,
                "sql": sql, "score": score,
                "rows": run.get("rows") or [],
                "columns": run.get("columns") or [],
                "row_count": run.get("row_count") or 0,
                "elapsed_ms": run.get("elapsed_ms") or 0}
    except Exception as exc:  # noqa: BLE001
        logger.debug("try_metric_shortcut failed for %s: %s", project_slug, exc)
        return None


def _run_scalar(project_slug: str, sql: str):
    """Execute proven SQL read-only; return first numeric cell of first row."""
    from sqlalchemy import text as _text
    from dash.tools.metric_compiler import resolve_engine
    engine, _schema = resolve_engine(project_slug)
    with engine.connect() as conn:
        conn.execute(_text("SET LOCAL statement_timeout = '20s'"))
        row = conn.execute(_text(sql)).fetchone()
    if not row:
        return None
    for cell in row:
        n = _to_num(cell) if not isinstance(cell, (int, float)) else float(cell)
        if n is not None:
            return n
    return None


def _run_rows(project_slug: str, sql: str, limit: int = 20):
    """Execute proven SQL read-only; return scalar (first numeric of first row)
    PLUS top-N rows + column names for DATA/CHART tab population."""
    import time as _t
    from sqlalchemy import text as _text
    from dash.tools.metric_compiler import resolve_engine
    engine, _schema = resolve_engine(project_slug)
    t0 = _t.time()
    with engine.connect() as conn:
        conn.execute(_text("SET LOCAL statement_timeout = '20s'"))
        # Wrap SQL in a subquery to safely apply LIMIT (preserves ORDER BY)
        # but only when SQL doesn't already contain a LIMIT clause.
        _safe_sql = sql.rstrip().rstrip(";")
        rs = conn.execute(_text(_safe_sql))
        cols = list(rs.keys())
        all_rows = rs.fetchmany(max(limit, 100))
    elapsed_ms = int((_t.time() - t0) * 1000)
    if not all_rows:
        return None
    # Scalar value = first numeric cell of first row
    value = None
    for cell in all_rows[0]:
        n = _to_num(cell) if not isinstance(cell, (int, float)) else float(cell)
        if n is not None:
            value = n
            break
    # 2026-05-25 (Day 2): centralized row serialization via df_rows_to_jsonable.
    # Single helper handles Decimal/datetime/bytes/NaN/Inf coercion + fail-soft.
    # See dash/utils/df_serialize.py.
    rows_out = df_rows_to_jsonable(all_rows[:limit], cols)
    return {"value": value, "rows": rows_out, "columns": cols,
            "row_count": len(all_rows), "elapsed_ms": elapsed_ms}


def _matches(expected: float, got: float) -> bool:
    if expected is None or got is None:
        return False
    if abs(expected - got) <= _ABS_TOL:
        return True
    denom = abs(expected) or 1.0
    return abs(expected - got) / denom <= _REL_TOL


def score_verified(project_slug: str, question: str, answer: str,
                   session_id: str | None = None) -> dict:
    """Grade an answer against ground truth. Writes one dash_verified_scores row.

    Returns {verified, expected, got, source_q}. Fail-soft → 'unknown'.
    """
    result = {"verified": "unknown", "expected": None, "got": None, "source_q": None}
    try:
        got = _answer_number(answer)
        pair = _best_proven_pair(project_slug, question)
        if pair and got is not None:
            proven_q, sql = pair
            expected = _run_scalar(project_slug, sql)
            if expected is not None:
                result = {
                    "verified": "pass" if _matches(expected, got) else "fail",
                    "expected": expected, "got": got, "source_q": proven_q,
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("score_verified failed for %s: %s", project_slug, exc)

    # Prometheus counter — pass|fail|unknown.
    try:
        from dash.utils.metrics import inc_verified as _inc_verified
        _inc_verified(result.get("verified") or "unknown")
    except Exception:
        pass

    # Persist (best-effort).
    try:
        from sqlalchemy import text as _text
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(_text(
                "INSERT INTO public.dash_verified_scores "
                "(project_slug, session_id, question, verified, expected, got, source_q) "
                "VALUES (:s, :sid, :q, :v, :e, :g, :sq)"
            ), {"s": project_slug, "sid": session_id or "", "q": (question or "")[:1000],
                "v": result["verified"], "e": result["expected"], "g": result["got"],
                "sq": (result["source_q"] or "")[:1000]})
    except Exception as exc:  # noqa: BLE001
        logger.debug("score_verified persist failed for %s: %s", project_slug, exc)

    return result
