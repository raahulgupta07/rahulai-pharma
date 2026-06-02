"""Phase 7 — dashboard memory feedback loop.

Logs user actions on dashboard cells and aggregates them into preferences
that bias the planner's next LLM call.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ACTIONS = ("kept", "deleted", "saved", "undone", "drilled")


def _engine():
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _ensure_table():
    eng = _engine()
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_dashboard_memory ("
            "id BIGSERIAL PRIMARY KEY,"
            "user_id TEXT,"
            "project_slug TEXT,"
            "action TEXT,"
            "cell_type TEXT,"
            "chart_type TEXT,"
            "insight_type TEXT,"
            "spec_id TEXT,"
            "created_at TIMESTAMPTZ DEFAULT NOW())"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_dash_dashboard_memory_lookup "
            "ON public.dash_dashboard_memory (user_id, project_slug, action)"
        ))


def _extract(cell: dict | None) -> tuple[str | None, str | None, str | None]:
    if not cell or not isinstance(cell, dict):
        return None, None, None
    cfg = cell.get("config") or {}
    cell_type = cell.get("type")
    chart_type = cfg.get("chart_type") or cfg.get("chartType") or cfg.get("type")
    insight_type = cfg.get("insight_type") or cfg.get("insightType") or cfg.get("kind")
    return cell_type, chart_type, insight_type


def log_action(user_id: str, project_slug: str, action: str,
               cell: dict | None = None, **kw: Any) -> bool:
    if action not in _ACTIONS:
        return False
    if not user_id or not project_slug:
        return False
    try:
        _ensure_table()
        cell_type, chart_type, insight_type = _extract(cell)
        spec_id = kw.get("spec_id")
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_dashboard_memory "
                "(user_id, project_slug, action, cell_type, chart_type, insight_type, spec_id) "
                "VALUES (:u, :s, :a, :ct, :ch, :it, :sp)"
            ), {"u": str(user_id), "s": project_slug, "a": action,
                "ct": cell_type, "ch": chart_type, "it": insight_type,
                "sp": str(spec_id) if spec_id else None})
        return True
    except Exception as e:
        logger.debug(f"log_action failed: {e}")
        return False


def _aggregate(rows: list, key: str) -> tuple[list[str], list[str]]:
    """Return (preferred, avoid) by tallying kept vs deleted per key."""
    tally: dict[str, dict[str, int]] = {}
    for action, k in rows:
        if not k:
            continue
        d = tally.setdefault(k, {"kept": 0, "deleted": 0})
        if action in d:
            d[action] += 1
    preferred, avoid = [], []
    for k, d in tally.items():
        total = d["kept"] + d["deleted"]
        if total == 0:
            continue
        if d["deleted"] >= 3 and d["deleted"] / total > 0.6:
            avoid.append(k)
        elif d["kept"] >= 3 and d["kept"] / total > 0.6:
            preferred.append(k)
    return preferred, avoid


def get_preferences(user_id: str, project_slug: str) -> dict:
    empty = {"preferred_chart_types": [], "avoid_chart_types": [],
             "preferred_insight_types": [], "avoid_insight_types": [],
             "kept_count": 0, "deleted_count": 0}
    if not user_id or not project_slug:
        return empty
    try:
        _ensure_table()
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT action, chart_type, insight_type FROM public.dash_dashboard_memory "
                "WHERE user_id=:u AND project_slug=:s "
                "AND action IN ('kept','deleted','saved') "
                "ORDER BY created_at DESC LIMIT 500"
            ), {"u": str(user_id), "s": project_slug}).fetchall()
    except Exception as e:
        logger.debug(f"get_preferences failed: {e}")
        return empty

    # 'saved' counts as 'kept' for preference inference
    norm = [("kept" if a == "saved" else a, ch, it) for (a, ch, it) in rows]
    chart_rows = [(a, ch) for (a, ch, _) in norm]
    insight_rows = [(a, it) for (a, _, it) in norm]
    pref_ch, avoid_ch = _aggregate(chart_rows, "chart")
    pref_it, avoid_it = _aggregate(insight_rows, "insight")
    kept = sum(1 for (a, _, _) in norm if a == "kept")
    deleted = sum(1 for (a, _, _) in norm if a == "deleted")
    return {"preferred_chart_types": pref_ch, "avoid_chart_types": avoid_ch,
            "preferred_insight_types": pref_it, "avoid_insight_types": avoid_it,
            "kept_count": kept, "deleted_count": deleted}
