"""Extract chat-thread context for dashboard seeding.

Pulls a chat session's questions, SQLs run, and filter hints from agno's
`ai.agno_sessions.runs` JSONB column. Returned dict matches the shape that
`dash.dashboards.planner.generate_spec(chat_context=...)` consumes.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Filter phrases pulled out of user questions for the planner to reuse as defaults.
_FILTER_PATTERNS = [
    r"\blast\s+\d+\s+(?:day|week|month|quarter|year)s?\b",
    r"\bin\s+(?:19|20)\d{2}\b",
    r"\b(?:19|20)\d{2}\s*(?:Q[1-4]|H[12])?\b",
    r"\b(?:Q[1-4]|H[12])\s*(?:19|20)\d{2}\b",
    r"\b(?:north|south|east|west|northeast|northwest|southeast|southwest)\s+\w+\b",
    r"\b(?:region|country|state|city|store|branch|department|category|segment|brand)\s*[:=]?\s*[A-Z][\w\-]+\b",
    r"\bytd\b|\bmtd\b|\bqtd\b|\byoy\b|\bmom\b",
    r"\btop\s+\d+\b",
]


def _extract_filters(text_blob: str) -> list[str]:
    found: list[str] = []
    for pat in _FILTER_PATTERNS:
        for m in re.finditer(pat, text_blob, flags=re.IGNORECASE):
            s = m.group(0).strip()
            if s and s.lower() not in (f.lower() for f in found):
                found.append(s)
    return found[:20]


def _walk_for_sqls(obj: Any, out: list[str]) -> None:
    """Recursively scan tool_calls / tool_results for SQL strings."""
    if not obj or len(out) >= 60:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in ("query", "sql", "sql_query") and isinstance(v, str) and len(v) > 8:
                if "select" in v.lower() or "with " in v.lower():
                    out.append(v.strip()[:1000])
            else:
                _walk_for_sqls(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _walk_for_sqls(it, out)


def _walk_for_results(obj: Any, out: list[dict], current_sql: str | None = None) -> None:
    """Walk tool_results capturing (sql, sample_rows) pairs. Cap each result 3 rows x 5 cols."""
    if not obj or len(out) >= 30:
        return
    if isinstance(obj, dict):
        sql_here = current_sql
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in ("query", "sql", "sql_query") and isinstance(v, str) and len(v) > 8 and "select" in v.lower():
                sql_here = v.strip()
            if kl in ("result", "results", "data", "rows", "tool_result", "output") and isinstance(v, (list, dict)):
                rows = v if isinstance(v, list) else (v.get("rows") if isinstance(v, dict) else None)
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    sample = []
                    for r in rows[:3]:
                        keys = list(r.keys())[:5]
                        sample.append({k2: r.get(k2) for k2 in keys})
                    out.append({"sql": (sql_here or "")[:300], "sample": sample})
                    if len(out) >= 30:
                        return
        for v in obj.values():
            _walk_for_results(v, out, sql_here)
    elif isinstance(obj, list):
        for it in obj:
            _walk_for_results(it, out, current_sql)


def extract_context(thread_id: str, up_to_msg_id: str | None = None) -> dict:
    """Pull chat thread → return dict for planner.generate_spec(chat_context=...).

    Returns: {questions, sqls, results, insights, filters_mentioned, persona}.
    `up_to_msg_id` truncates at that run id (best-effort, matches run.run_id).
    """
    from dash.tools.skill_refinery import _get_engine

    out: dict[str, Any] = {
        "questions": [],
        "sqls": [],
        "results": [],
        "prior_results": [],
        "insights": [],
        "filters_mentioned": [],
        "persona": "",
    }

    eng = _get_engine()
    project_slug: str | None = None
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT project_slug FROM public.dash_chat_sessions WHERE session_id=:s"
            ), {"s": thread_id}).fetchone()
            if row:
                project_slug = row[0]
            if project_slug:
                prow = conn.execute(text(
                    "SELECT agent_role, agent_personality FROM public.dash_projects WHERE slug=:s"
                ), {"s": project_slug}).fetchone()
                if prow:
                    out["persona"] = " · ".join([x for x in (prow[0], prow[1]) if x])

            srow = conn.execute(text(
                "SELECT runs FROM ai.agno_sessions WHERE session_id=:s"
            ), {"s": thread_id}).fetchone()
            runs = (srow[0] if srow and isinstance(srow[0], list) else []) or []
    except Exception as e:
        logger.debug(f"extract_context db error: {e}")
        return out

    filter_blob: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if up_to_msg_id and str(run.get("run_id") or "") == str(up_to_msg_id):
            break
        if run.get("parent_run_id"):
            continue

        inp = run.get("input") or {}
        umsg = ""
        if isinstance(inp, dict):
            umsg = inp.get("input_content") or inp.get("content") or ""
        elif isinstance(inp, str):
            umsg = inp
        if umsg:
            out["questions"].append(umsg.strip()[:500])
            filter_blob.append(umsg)

        _walk_for_sqls(run.get("messages"), out["sqls"])
        _walk_for_sqls(run.get("tools"), out["sqls"])
        _walk_for_results(run.get("messages"), out["prior_results"])
        _walk_for_results(run.get("tools"), out["prior_results"])

        content = run.get("content") or ""
        if isinstance(content, str) and content:
            for line in content.splitlines():
                ll = line.lower()
                if any(k in ll for k in ("anomaly", "trend", "correlation", "insight:", "key finding")):
                    out["insights"].append(line.strip()[:300])

    out["filters_mentioned"] = _extract_filters(" \n ".join(filter_blob))
    # de-dup sqls
    seen: set[str] = set()
    out["sqls"] = [s for s in out["sqls"] if not (s in seen or seen.add(s))][:30]
    out["questions"] = out["questions"][:30]
    out["insights"] = out["insights"][:10]
    out["prior_results"] = out["prior_results"][:15]
    return out
