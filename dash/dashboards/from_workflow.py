"""Build a dashboard spec from a workflow run's step results.

Each step result maps to a panel:
  - single numeric value → KPI panel
  - rows == 1, multi col → KPI cluster (one per numeric col)
  - has date/categorical + numeric col → chart panel (uses step.chart spec if set)
  - many rows, multi col → table panel
  - narrative only → insight panel

The spec is shaped to mirror the DeepDashSpec-ish JSONB stored in
`public.dash_dashboards_v2.spec`. Source flagged 'workflow' so the
frontend can render the workflow-build badge.

Incremental build flow (Task #7):
  - ensure_dashboard_skeleton(run_id, wf, eng) → creates empty dashboard
    immediately at run start (status='building', panels=[], panels_count=0)
  - upsert_panel(run_id, step_result, eng) → appends one panel per step
    completion; bumps panels_count + last_panel_at
  - build_dashboard_from_run(run_id, wf, results, eng) → finalises:
    sets status='done', picks layout, bumps spec_version
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


_NUMERIC_DTYPES = ("int", "float", "numeric", "decimal", "double", "real", "bigint", "smallint")


def _is_numeric(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        v = val.strip().replace(",", "")
        try:
            float(v)
            return True
        except Exception:
            return False
    return False


def _classify_columns(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Return (numeric_cols, date_cols, other_cols) from a sample row."""
    if not rows:
        return [], [], []
    sample = rows[0]
    numeric: list[str] = []
    date: list[str] = []
    other: list[str] = []
    for k, v in sample.items():
        kl = str(k).lower()
        if any(t in kl for t in ("date", "day", "month", "year", "week", "time", "_at", "ts")):
            date.append(k)
        elif _is_numeric(v):
            numeric.append(k)
        else:
            other.append(k)
    return numeric, date, other


def _panel_from_step(step_res: dict, idx: int) -> dict | None:
    """Map one step result → panel dict (returns None if nothing to render)."""
    rows = step_res.get("rows") or []
    row_count = step_res.get("row_count") or len(rows)
    title = str(step_res.get("title") or step_res.get("question") or f"Step {idx + 1}")
    narrative = str(step_res.get("narrative") or "")
    chart_spec = step_res.get("chart") or {}

    panel_id = f"p{idx}"

    # Narrative-only path
    if not rows and narrative:
        return {
            "panel_id": panel_id,
            "chart_type": "insight",
            "title": title,
            "narrative": narrative,
            "options": {},
            "grid": [(idx % 2) * 6, (idx // 2) * 3, 6, 3],
            "sources": [],
        }

    if not rows:
        return None

    numeric, date_cols, other = _classify_columns(rows)

    # KPI: 1 row × 1 numeric col
    if row_count == 1 and len(numeric) == 1:
        col = numeric[0]
        val = rows[0].get(col)
        return {
            "panel_id": panel_id,
            "chart_type": "kpi",
            "title": title,
            "narrative": narrative,
            "options": {"value": val, "label": col},
            "grid": [(idx % 4) * 3, (idx // 4) * 2, 3, 2],
            "sources": [],
        }

    # KPI cluster: 1 row × N numerics → first one used as primary KPI panel
    if row_count == 1 and len(numeric) >= 2:
        col = numeric[0]
        val = rows[0].get(col)
        return {
            "panel_id": panel_id,
            "chart_type": "kpi",
            "title": title or col,
            "narrative": narrative,
            "options": {"value": val, "label": col, "extra": {k: rows[0].get(k) for k in numeric[1:5]}},
            "grid": [(idx % 4) * 3, (idx // 4) * 2, 3, 2],
            "sources": [],
        }

    # Chart panel: has (date or other categorical) + numeric, multiple rows
    if rows and numeric and (date_cols or other) and row_count >= 2:
        # Prefer the visualizer-supplied chart spec when present.
        if chart_spec:
            return {
                "panel_id": panel_id,
                "chart_type": chart_spec.get("type") or chart_spec.get("chart_type") or "bar",
                "title": title,
                "narrative": narrative,
                "options": chart_spec.get("options") or chart_spec,
                "grid": [(idx % 2) * 6, (idx // 2) * 3, 6, 3],
                "sources": [],
                "rows": rows[:200],
                "columns": step_res.get("columns") or [],
            }
        # Fallback minimal bar
        xcol = date_cols[0] if date_cols else other[0]
        ycol = numeric[0]
        return {
            "panel_id": panel_id,
            "chart_type": "bar",
            "title": title,
            "narrative": narrative,
            "options": {
                "xAxis": {"type": "category", "data": [r.get(xcol) for r in rows[:50]]},
                "yAxis": {"type": "value"},
                "series": [{"type": "bar", "data": [r.get(ycol) for r in rows[:50]]}],
            },
            "grid": [(idx % 2) * 6, (idx // 2) * 3, 6, 3],
            "sources": [],
            "rows": rows[:200],
            "columns": step_res.get("columns") or [],
        }

    # Table panel fallback
    return {
        "panel_id": panel_id,
        "chart_type": "table",
        "title": title,
        "narrative": narrative,
        "options": {},
        "grid": [(idx % 2) * 6, (idx // 2) * 3, 6, 3],
        "sources": [],
        "rows": rows[:500],
        "columns": step_res.get("columns") or [],
    }


# ── Incremental write helpers (Task #7) ─────────────────────────────────


def _write_engine():
    """Return a write-capable engine for public schema. Caller does NOT dispose
    (cached shared engine — never call .dispose() on it per CLAUDE.md rule)."""
    from db.session import get_write_engine
    return get_write_engine()


def ensure_dashboard_skeleton(run_id: str, workflow: dict, engine) -> str:
    """Create an empty dashboard row immediately at run start.
    Idempotent via ON CONFLICT DO NOTHING. Returns dashboard_id.

    The skeleton spec carries status='building', panels=[], panels_count=0
    so the UI can subscribe and show 'building...' state right away.
    """
    dashboard_id = f"wfd_{run_id}"
    wf_id = workflow.get("id")
    wf_name = workflow.get("name") or "Workflow"
    project_slug = workflow.get("project_slug") or ""
    now = datetime.now(timezone.utc)
    label = f"WF: {wf_name} · {now.strftime('%Y-%m-%d %H:%M')}"

    spec = {
        "id": dashboard_id,
        "project_slug": project_slug,
        "title": wf_name,
        "panels": [],
        "panels_count": 0,
        "layout": "operational",
        "grid_cols": 12,
        "audience": "operator",
        "source": "workflow",
        "status": "building",
        "workflow_id": wf_id,
        "workflow_run_id": run_id,
        "created_at": now.isoformat(),
        "spec_version": 1,
    }

    try:
        eng = engine or _write_engine()
        from sqlalchemy import text as _t
        with eng.begin() as cn:
            cn.execute(_t(
                "INSERT INTO public.dash_dashboards_v2 "
                "(id, project_slug, spec, created_at, session_id, version, parent_id, label, signature_hash) "
                "VALUES (:id, :slug, CAST(:spec AS JSONB), NOW(), NULL, 1, NULL, :lbl, NULL) "
                "ON CONFLICT (id) DO NOTHING"
            ), {
                "id": dashboard_id,
                "slug": project_slug,
                "spec": json.dumps(spec, default=str),
                "lbl": label,
            })
    except Exception as e:  # noqa: BLE001
        logger.exception("from_workflow: skeleton failed for %s: %s", dashboard_id, e)
        raise

    return dashboard_id


def upsert_panel(run_id: str, step_result: dict, engine) -> int:
    """Append one panel to the dashboard skeleton's spec.panels array.
    Bumps panels_count + last_panel_at atomically. Returns new panel count.

    Panel mapping reuses _panel_from_step (same logic as full build).
    Idx for the panel is computed from current array length to keep ordering
    deterministic across concurrent step completions.
    """
    dashboard_id = f"wfd_{run_id}"

    try:
        eng = engine or _write_engine()
        from sqlalchemy import text as _t

        # Look up current panel count to assign idx for this panel
        with eng.connect() as cn:
            row = cn.execute(_t(
                "SELECT COALESCE(jsonb_array_length(spec->'panels'), 0) "
                "FROM public.dash_dashboards_v2 WHERE id=:did"
            ), {"did": dashboard_id}).first()
            current_count = int(row[0]) if row else 0

        panel = _panel_from_step(step_result, current_count)
        if not panel:
            return current_count

        # Atomic append + count bump + timestamp via jsonb_set
        with eng.begin() as cn:
            res = cn.execute(_t(
                "UPDATE public.dash_dashboards_v2 SET spec = "
                "jsonb_set("
                "  jsonb_set("
                "    jsonb_set("
                "      spec,"
                "      '{panels}',"
                "      COALESCE(spec->'panels', '[]'::jsonb) || CAST(:panel AS JSONB)"
                "    ),"
                "    '{panels_count}',"
                "    to_jsonb(jsonb_array_length(COALESCE(spec->'panels', '[]'::jsonb)) + 1)"
                "  ),"
                "  '{last_panel_at}',"
                "  to_jsonb(NOW()::text)"
                ") "
                "WHERE id=:did "
                "RETURNING jsonb_array_length(spec->'panels')"
            ), {
                "did": dashboard_id,
                "panel": json.dumps(panel, default=str),
            }).first()
            return int(res[0]) if res else current_count + 1
    except Exception as e:  # noqa: BLE001
        logger.exception("from_workflow: upsert_panel failed for %s: %s", dashboard_id, e)
        raise


def _pick_layout(panels: list[dict]) -> str:
    """Choose layout based on panel count + types."""
    if not panels:
        return "operational"
    types = [str(p.get("chart_type") or "").lower() for p in panels]
    kpi_count = sum(1 for t in types if t == "kpi")
    insight_count = sum(1 for t in types if t == "insight")
    n = len(panels)

    # Mostly narrative → narrative layout
    if insight_count >= n / 2:
        return "narrative"
    # Few panels, heavy KPI → executive
    if n <= 4 and kpi_count >= n / 2:
        return "executive"
    # Lots of similar panels → comparison
    if n >= 6 and len(set(types)) <= 2:
        return "comparison"
    return "operational"


def build_dashboard_from_run(run_id: str, workflow: dict, results: list[dict],
                             engine) -> str | None:
    """Build + persist a dashboard from a workflow run. Returns dashboard_id or None.

    Two paths:
      - Skeleton exists (incremental flow): FINALIZE — flip status='done',
        set layout, bump spec_version. Panels already appended by upsert_panel.
      - No skeleton (legacy/backward-compat): full INSERT path from results.
    """
    dashboard_id = f"wfd_{run_id}"

    # Detect skeleton via DB check
    skeleton_exists = False
    try:
        eng_check = engine or _write_engine()
        from sqlalchemy import text as _t
        with eng_check.connect() as cn:
            row = cn.execute(_t(
                "SELECT 1 FROM public.dash_dashboards_v2 WHERE id=:id"
            ), {"id": dashboard_id}).first()
            skeleton_exists = bool(row)
    except Exception:
        skeleton_exists = False

    if skeleton_exists:
        # FINALIZE path — panels already in spec via upsert_panel
        try:
            eng = engine or _write_engine()
            from sqlalchemy import text as _t
            with eng.begin() as cn:
                # Fetch current panels for layout decision
                row = cn.execute(_t(
                    "SELECT spec->'panels' FROM public.dash_dashboards_v2 WHERE id=:id"
                ), {"id": dashboard_id}).first()
                panels = row[0] if row and row[0] else []
                if isinstance(panels, str):
                    try:
                        panels = json.loads(panels)
                    except Exception:
                        panels = []
                layout = _pick_layout(panels or [])

                cn.execute(_t(
                    "UPDATE public.dash_dashboards_v2 SET spec = "
                    "jsonb_set("
                    "  jsonb_set("
                    "    jsonb_set(spec, '{status}', to_jsonb('done'::text)),"
                    "    '{layout}', to_jsonb(CAST(:layout AS text))"
                    "  ),"
                    "  '{spec_version}',"
                    "  to_jsonb(COALESCE((spec->>'spec_version')::int, 1) + 1)"
                    ") "
                    "WHERE id=:id"
                ), {"id": dashboard_id, "layout": layout})
            logger.info("from_workflow: finalised %s (panels=%d, layout=%s)",
                        dashboard_id, len(panels or []), layout)
            return dashboard_id
        except Exception as e:  # noqa: BLE001
            logger.exception("from_workflow: finalise failed for %s: %s", dashboard_id, e)
            return None

    # LEGACY path — no skeleton, build from results
    wf_id = workflow.get("id")
    wf_name = workflow.get("name") or "Workflow"
    project_slug = workflow.get("project_slug") or ""

    panels: list[dict] = []
    for i, res in enumerate(results or []):
        if not res:
            continue
        try:
            p = _panel_from_step(res, i)
            if p:
                panels.append(p)
        except Exception as e:  # noqa: BLE001
            logger.exception("from_workflow: panel build failed step=%d: %s", i, e)

    if not panels:
        logger.info("from_workflow: no panels built for run=%s", run_id)
        return None

    now = datetime.now(timezone.utc)
    label = f"WF: {wf_name} · {now.strftime('%Y-%m-%d %H:%M')}"
    layout = _pick_layout(panels)

    spec = {
        "id": dashboard_id,
        "project_slug": project_slug,
        "title": wf_name,
        "panels": panels,
        "panels_count": len(panels),
        "layout": layout,
        "grid_cols": 12,
        "audience": "operator",
        "source": "workflow",
        "status": "done",
        "workflow_id": wf_id,
        "workflow_run_id": run_id,
        "created_at": now.isoformat(),
        "spec_version": 1,
    }

    try:
        from db.session import get_write_engine
        from sqlalchemy import text as _t
        eng = get_write_engine()
        with eng.begin() as cn:
            cn.execute(_t(
                "INSERT INTO public.dash_dashboards_v2 "
                "(id, project_slug, spec, created_at, session_id, version, parent_id, label, signature_hash) "
                "VALUES (:id, :slug, CAST(:spec AS JSONB), NOW(), NULL, 1, NULL, :lbl, NULL) "
                "ON CONFLICT (id) DO UPDATE SET spec = EXCLUDED.spec, label = EXCLUDED.label"
            ), {
                "id": dashboard_id,
                "slug": project_slug,
                "spec": json.dumps(spec, default=str),
                "lbl": label,
            })
    except Exception as e:  # noqa: BLE001
        logger.exception("from_workflow: persist failed for %s: %s", dashboard_id, e)
        return None

    return dashboard_id


__all__ = ["build_dashboard_from_run", "ensure_dashboard_skeleton", "upsert_panel"]
