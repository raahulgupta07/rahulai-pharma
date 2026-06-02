"""Continuous training signal logging + nightly analysis.

Fire-and-forget signal capture from chat lifecycle events (question asked,
SQL executed, chart rendered, followup clicked). Nightly aggregator surfaces
patterns into project config: new lexicon terms, recurring failures, vertical
drift, workflow candidates.

No scheduler hookup yet. Module + migration only.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

_ENGINE = None


def _get_engine():
    """NullPool engine that can write to dash.training_signals.

    Mirrors pattern from dash/tools/skill_refinery.py._get_engine.
    """
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine as _sa_create_engine
        from sqlalchemy.pool import NullPool
        from db import db_url
        _ENGINE = _sa_create_engine(db_url, poolclass=NullPool)
    return _ENGINE


def _exec(sql: str, params: dict[str, Any] | None = None) -> Any:
    """Execute SQL with `SET LOCAL search_path TO dash, public`. Swallow errors."""
    from sqlalchemy import text
    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO dash, public"))
            return conn.execute(text(sql), params or {})
    except Exception as e:
        logger.warning("continuous: SQL failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Write path — fire-and-forget signal logging
# ---------------------------------------------------------------------------

_ALLOWED_FIELDS = {
    "chat_id", "message_id", "question", "tables_hit", "sql_text",
    "sql_success", "sql_error", "chart_action", "followup_clicked",
    "agent_used", "duration_ms",
}


def log_signal(project_slug: str, **kwargs) -> None:
    """Insert one row into training_signals. Fire-and-forget, swallow errors."""
    if not project_slug:
        return
    try:
        row: dict[str, Any] = {"project_slug": project_slug}
        for k in _ALLOWED_FIELDS:
            row[k] = kwargs.get(k)
        # JSONB-encode tables_hit if list/dict
        th = row.get("tables_hit")
        if th is not None and not isinstance(th, str):
            row["tables_hit"] = json.dumps(th)
        elif th is None:
            row["tables_hit"] = "[]"

        _exec(
            """
            INSERT INTO training_signals
              (project_slug, chat_id, message_id, question, tables_hit,
               sql_text, sql_success, sql_error, chart_action,
               followup_clicked, agent_used, duration_ms)
            VALUES
              (:project_slug, :chat_id, :message_id, :question, CAST(:tables_hit AS jsonb),
               :sql_text, :sql_success, :sql_error, :chart_action,
               COALESCE(:followup_clicked, FALSE), :agent_used, :duration_ms)
            """,
            row,
        )
    except Exception as e:
        logger.warning("log_signal failed (swallowed): %s", e)


def mark_followup_clicked(message_id: str) -> None:
    """Update followup_clicked=true for message_id."""
    if not message_id:
        return
    _exec(
        "UPDATE training_signals SET followup_clicked = TRUE WHERE message_id = :mid",
        {"mid": message_id},
    )


def mark_chart_action(message_id: str, action: str) -> None:
    """Update chart_action ('rendered', 'edited', 'rejected')."""
    if not message_id or not action:
        return
    if action not in ("rendered", "edited", "rejected"):
        logger.warning("mark_chart_action: invalid action %r", action)
        return
    _exec(
        "UPDATE training_signals SET chart_action = :a WHERE message_id = :mid",
        {"a": action, "mid": message_id},
    )


# ---------------------------------------------------------------------------
# Read path — summary for UI
# ---------------------------------------------------------------------------

def get_signal_summary(project_slug: str, days: int = 7) -> dict:
    """Aggregate counts for UI: total chats, success rate, top tables, top failures."""
    out: dict[str, Any] = {
        "project_slug": project_slug,
        "days": days,
        "total_signals": 0,
        "total_chats": 0,
        "sql_attempts": 0,
        "sql_successes": 0,
        "sql_failures": 0,
        "success_rate": None,
        "followup_click_rate": None,
        "top_tables": [],
        "top_failures": [],
        "top_agents": [],
    }
    try:
        from sqlalchemy import text
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO dash, public"))

            r = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS total,
                      COUNT(DISTINCT chat_id) AS chats,
                      COUNT(*) FILTER (WHERE sql_success IS NOT NULL) AS attempts,
                      COUNT(*) FILTER (WHERE sql_success = TRUE) AS ok,
                      COUNT(*) FILTER (WHERE sql_success = FALSE) AS fail,
                      COUNT(*) FILTER (WHERE followup_clicked = TRUE) AS fu_clicks
                    FROM training_signals
                    WHERE project_slug = :p
                      AND created_at > NOW() - make_interval(days => :d)
                    """
                ),
                {"p": project_slug, "d": days},
            ).mappings().first()
            if r:
                out["total_signals"] = int(r["total"] or 0)
                out["total_chats"] = int(r["chats"] or 0)
                out["sql_attempts"] = int(r["attempts"] or 0)
                out["sql_successes"] = int(r["ok"] or 0)
                out["sql_failures"] = int(r["fail"] or 0)
                if out["sql_attempts"] > 0:
                    out["success_rate"] = round(out["sql_successes"] / out["sql_attempts"], 3)
                if out["total_signals"] > 0:
                    out["followup_click_rate"] = round(
                        int(r["fu_clicks"] or 0) / out["total_signals"], 3
                    )

            # Top tables (unnest tables_hit JSONB array)
            rows = conn.execute(
                text(
                    """
                    SELECT t.tbl AS name, COUNT(*) AS n
                    FROM training_signals s,
                         LATERAL jsonb_array_elements_text(s.tables_hit) AS t(tbl)
                    WHERE s.project_slug = :p
                      AND s.created_at > NOW() - make_interval(days => :d)
                    GROUP BY t.tbl
                    ORDER BY n DESC
                    LIMIT 10
                    """
                ),
                {"p": project_slug, "d": days},
            ).mappings().all()
            out["top_tables"] = [{"name": r["name"], "count": int(r["n"])} for r in rows]

            # Top failure errors
            rows = conn.execute(
                text(
                    """
                    SELECT sql_error AS err, COUNT(*) AS n
                    FROM training_signals
                    WHERE project_slug = :p
                      AND created_at > NOW() - make_interval(days => :d)
                      AND sql_success = FALSE
                      AND sql_error IS NOT NULL
                    GROUP BY sql_error
                    ORDER BY n DESC
                    LIMIT 10
                    """
                ),
                {"p": project_slug, "d": days},
            ).mappings().all()
            out["top_failures"] = [{"error": r["err"], "count": int(r["n"])} for r in rows]

            # Top agents
            rows = conn.execute(
                text(
                    """
                    SELECT agent_used AS agent, COUNT(*) AS n
                    FROM training_signals
                    WHERE project_slug = :p
                      AND created_at > NOW() - make_interval(days => :d)
                      AND agent_used IS NOT NULL
                    GROUP BY agent_used
                    ORDER BY n DESC
                    LIMIT 10
                    """
                ),
                {"p": project_slug, "d": days},
            ).mappings().all()
            out["top_agents"] = [{"agent": r["agent"], "count": int(r["n"])} for r in rows]
    except Exception as e:
        logger.warning("get_signal_summary failed: %s", e)
        out["error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# Nightly analysis — cluster questions, detect drift, propose workflows
# ---------------------------------------------------------------------------

def _fetch_recent(project_slug: str, limit: int, success: bool | None) -> list[dict]:
    """Fetch most recent signals filtered by sql_success."""
    from sqlalchemy import text
    try:
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO dash, public"))
            if success is None:
                where = ""
            elif success:
                where = "AND sql_success = TRUE"
            else:
                where = "AND sql_success = FALSE"
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, question, tables_hit, sql_text, sql_error, agent_used
                    FROM training_signals
                    WHERE project_slug = :p
                      AND question IS NOT NULL
                      {where}
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                {"p": project_slug, "lim": limit},
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("_fetch_recent failed: %s", e)
        return []


def _cluster_questions_lite(questions: list[str]) -> dict:
    """Use LITE_MODEL to cluster questions into topics + extract new lexicon terms."""
    if not questions:
        return {"clusters": [], "new_terms": []}
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        logger.warning("LITE_MODEL unavailable: %s", e)
        return {"clusters": [], "new_terms": [], "error": str(e)}

    sample = questions[:80]  # cap prompt size
    prompt = (
        "Cluster these user questions by topic. Identify recurring terms/synonyms "
        "that may need to be added to the project's lexicon.\n\n"
        "Return JSON:\n"
        '{"clusters":[{"topic":"...","examples":["..."],"count":N}],'
        '"new_terms":[{"term":"...","synonyms":["..."],"context":"..."}]}\n\n'
        "Questions:\n" + "\n".join(f"- {q}" for q in sample if q)
    )
    try:
        resp = training_llm_call(prompt, task="extraction")
        if not resp:
            return {"clusters": [], "new_terms": []}
        # Strip markdown fences
        s = resp.strip()
        if s.startswith("```"):
            s = s.split("```", 2)[1]
            if s.startswith("json"):
                s = s[4:]
            s = s.strip()
        return json.loads(s)
    except Exception as e:
        logger.warning("cluster_questions LLM parse failed: %s", e)
        return {"clusters": [], "new_terms": []}


def nightly_analyze(project_slug: str) -> dict:
    """Returns suggestions to write back into project config.

    - new_lexicon_terms: questions clustered by topic, new synonyms
    - failed_query_patterns: tables/columns that failed often
    - vertical_drift: re-run vertical detect, compare to current
    - new_workflow_suggestions: queries asked >= 5 times -> workflow candidate
    """
    out: dict[str, Any] = {
        "project_slug": project_slug,
        "new_lexicon_terms": [],
        "failed_query_patterns": [],
        "vertical_drift": None,
        "new_workflow_suggestions": [],
    }

    failed = _fetch_recent(project_slug, limit=100, success=False)
    successful = _fetch_recent(project_slug, limit=100, success=True)

    # --- 1. Cluster questions + extract new lexicon (LITE_MODEL) ---
    all_qs = [r.get("question") or "" for r in (successful + failed)]
    cluster = _cluster_questions_lite([q for q in all_qs if q])
    out["question_clusters"] = cluster.get("clusters", [])
    out["new_lexicon_terms"] = cluster.get("new_terms", [])

    # --- 2. Failed query patterns: tables/errors that failed often ---
    fail_tables: Counter[str] = Counter()
    fail_errors: Counter[str] = Counter()
    for r in failed:
        th = r.get("tables_hit")
        if isinstance(th, str):
            try:
                th = json.loads(th)
            except Exception:
                th = []
        if isinstance(th, list):
            for t in th:
                if t:
                    fail_tables[str(t)] += 1
        err = r.get("sql_error")
        if err:
            # Truncate / normalize first line of error
            key = str(err).strip().split("\n", 1)[0][:160]
            fail_errors[key] += 1
    out["failed_query_patterns"] = {
        "top_tables": [{"name": n, "count": c} for n, c in fail_tables.most_common(10)],
        "top_errors": [{"error": e, "count": c} for e, c in fail_errors.most_common(10)],
    }

    # --- 3. Vertical drift: re-run vertical detect, compare ---
    try:
        from dash.learning.domain_detector import detect_vertical  # type: ignore
        detected = detect_vertical(project_slug)
    except Exception:
        detected = None
    current = None
    try:
        from sqlalchemy import text
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(text("SET LOCAL search_path TO dash, public"))
            r = conn.execute(
                text(
                    "SELECT feature_config->'vertical' AS v FROM dash_projects "
                    "WHERE slug = :s LIMIT 1"
                ),
                {"s": project_slug},
            ).first()
            if r:
                current = r[0]
    except Exception:
        pass
    out["vertical_drift"] = {
        "current": current,
        "detected": detected,
        "drift": bool(detected and current and detected != current),
    }

    # --- 4. Workflow candidates: questions asked >= 5x (loose normalization) ---
    norm_qs: Counter[str] = Counter()
    for q in all_qs:
        if not q:
            continue
        key = " ".join(q.lower().strip().split())[:200]
        norm_qs[key] += 1
    out["new_workflow_suggestions"] = [
        {"question": q, "count": c} for q, c in norm_qs.most_common(20) if c >= 5
    ]

    return out
