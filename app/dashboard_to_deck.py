# REGISTER: app.main → include_router(dashboard_to_deck_router)
"""Phase 3 — Dashboard → Deck conversion.

Loads a dashboard spec from `dash_dashboards_v2`, maps each panel
(KPI strip / chart / insight / narrative) to a native pptx_renderer
slide-spec, renders to a `.pptx` via `dash.pptx_renderer.renderer.render_to_path`,
persists into `dash_presentations`, and returns the presentation_id +
slide_count + pptx_url.
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import traceback
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from dash.pptx_renderer.renderer import render_to_path
from dash.tools.skill_refinery import _get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboards", tags=["DashboardToDeck"])

DEFAULT_THEME = "coral_energy"


def _smart_truncate(text: str, max_len: int) -> str:
    """Truncate at word boundary, append ellipsis if cut."""
    if not text:
        return ""
    s = str(text).strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:  # only break at space if reasonably late
        cut = cut[:last_space]
    return cut.rstrip(" ,.;:") + "…"


def _short_label(text: str, max_len: int = 28) -> str:
    """Compress long titles into short KPI labels."""
    if not text:
        return ""
    s = str(text).strip()
    # Trim common verbose prefixes
    for pfx in ("Total ", "The ", "A "):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    s = s.split(".")[0].split(",")[0]
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s


# ---------------------------------------------------------------------------
# Panel → slide-spec mapping helpers
# ---------------------------------------------------------------------------

_KPI_CHART_TYPES = {"gauge", "kpi", "metric", "stat", "number", "value"}


def _panel_type(cell: dict) -> str:
    return (cell.get("type") or cell.get("panel_type") or "").lower()


def _panel_chart_type(cell: dict) -> str:
    cfg = cell.get("config") or {}
    return (cell.get("chart_type") or cfg.get("chart_type") or "").lower()


def _is_kpi_panel(cell: dict) -> bool:
    if _panel_type(cell) in {"kpi", "metric", "stat"}:
        return True
    # DeepDashAgent gauges = single-value displays → treat as KPI
    return _panel_chart_type(cell) in _KPI_CHART_TYPES


def _is_chart_panel(cell: dict) -> bool:
    if _is_kpi_panel(cell):
        return False  # KPIs get their own cover-stat slide
    if _panel_type(cell) == "chart":
        return True
    cfg = cell.get("config") or {}
    return bool(
        _panel_chart_type(cell)
        or cell.get("options") or cfg.get("options")
        or cfg.get("echarts_options") or cell.get("echarts_options")
        or cfg.get("chart_data") or cell.get("rows")
    )


def _is_insight_panel(cell: dict) -> bool:
    if _panel_type(cell) in {"insight", "narrative", "text", "markdown"}:
        return True
    # No data/chart, just narrative
    return bool(cell.get("narrative")) and not _panel_chart_type(cell)


def _extract_echarts_value(opts: dict) -> str:
    """Pull single number from gauge/kpi echarts options."""
    if not isinstance(opts, dict):
        return ""
    try:
        series = opts.get("series") or []
        if isinstance(series, list) and series:
            s0 = series[0] if isinstance(series[0], dict) else {}
            data = s0.get("data") or []
            if isinstance(data, list) and data:
                d0 = data[0]
                if isinstance(d0, dict):
                    v = d0.get("value")
                else:
                    v = d0
                if v is not None:
                    if isinstance(v, (int, float)):
                        return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"
                    return str(v)
    except Exception:
        pass
    return ""


def _kpi_value(cell: dict) -> str:
    cfg = cell.get("config") or {}
    for k in ("value", "kpi_value", "number", "metric_value"):
        v = cfg.get(k) if cfg.get(k) is not None else cell.get(k)
        if v is not None:
            return str(v)
    # DeepDash gauges: pull from options.series[0].data[0]
    opts = cell.get("options") or cfg.get("options") or cell.get("echarts_options") or {}
    v = _extract_echarts_value(opts)
    if v:
        return v
    return ""


def _kpi_label(cell: dict) -> str:
    raw = cell.get("title") or (cell.get("config") or {}).get("label") or ""
    return _short_label(raw, 32)


def _kpi_delta(cell: dict) -> str:
    cfg = cell.get("config") or {}
    for k in ("change", "delta", "trend"):
        v = cfg.get(k)
        if v is not None and v != "":
            return str(v)
    return ""


def _build_cover_stat_slide(kpi_cells: list[dict], dash_title: str) -> dict:
    """Map a KPI strip → 1 cover-stat slide with big-number cards."""
    stats = []
    for c in kpi_cells[:6]:  # cap at 6 KPIs on cover
        stats.append({
            "value": _kpi_value(c),
            "label": _kpi_label(c),
            "delta": _kpi_delta(c),
        })
    return {
        "layout": "cover",
        "eyebrow": "DASHBOARD SUMMARY",
        "title": dash_title or "Key Metrics",
        "subtitle": f"{len(stats)} KPI{'s' if len(stats) != 1 else ''} at a glance",
        "stats": stats,
    }


_CHART_TYPE_MAP = {
    "stacked_bar": "stacked_bar",
    "grouped_bar": "grouped_bar",
    "bar": "bar",
    "column": "bar",
    "line": "line",
    "area": "area",
    "pie": "pie",
    "donut": "pie",
    "scatter": "scatter",
    "histogram": "bar",
    "heatmap": "bar",
}


def _extract_chart_data_from_echarts(opts: dict, chart_type: str) -> dict:
    """Convert ECharts options → {labels: [...], series: [{name, data}]}.
    Returns empty {} if extraction fails so caller can decide fallback."""
    if not isinstance(opts, dict):
        return {}
    try:
        # Pie / donut — data lives as [{name, value}, ...] in series[0].data
        if chart_type in {"pie", "donut"}:
            series = opts.get("series") or []
            if isinstance(series, list) and series:
                data = series[0].get("data") if isinstance(series[0], dict) else None
                if isinstance(data, list) and data:
                    labels = []
                    values = []
                    for d in data:
                        if isinstance(d, dict):
                            labels.append(str(d.get("name") or ""))
                            values.append(d.get("value"))
                        else:
                            values.append(d)
                            labels.append("")
                    return {
                        "labels": labels,
                        "series": [{"name": series[0].get("name") or "Value", "data": values}],
                    }
            return {}

        # Cartesian charts — xAxis.data = labels, series[*].data = values
        x_axis = opts.get("xAxis") or {}
        if isinstance(x_axis, list):
            x_axis = x_axis[0] if x_axis else {}
        labels = list(x_axis.get("data") or []) if isinstance(x_axis, dict) else []
        labels = [str(l) for l in labels]

        series_raw = opts.get("series") or []
        if not isinstance(series_raw, list):
            return {}
        series_out = []
        for s in series_raw:
            if not isinstance(s, dict):
                continue
            data = s.get("data") or []
            if not isinstance(data, list):
                continue
            # data may be [num, ...] or [{value, name}, ...]
            nums = []
            inferred_labels = []
            for d in data:
                if isinstance(d, dict):
                    nums.append(d.get("value"))
                    inferred_labels.append(str(d.get("name") or ""))
                else:
                    nums.append(d)
            if not labels and inferred_labels:
                labels = inferred_labels
            series_out.append({"name": s.get("name") or "", "data": nums})

        if not series_out:
            return {}
        return {"labels": labels, "series": series_out}
    except Exception:
        return {}


def map_chart_to_slide(chart_cfg: dict, title: str = "", eyebrow: str = "CHART",
                       narrative: str = "", source: str = "") -> dict:
    """Build chart slide. Handles 3 data shapes:
      1. echarts options (DeepDashAgent panels — `options` field)
      2. flat rows array (legacy panels — `rows` field)
      3. chart_data {labels, series} (pre-built)
    """
    raw_type = (chart_cfg.get("chart_type") or "bar").lower()
    chart_type = _CHART_TYPE_MAP.get(raw_type, "bar")

    # 1. Try echarts options first (new DeepDash shape)
    opts = chart_cfg.get("options") or chart_cfg.get("echarts_options")
    chart_data = {}
    if isinstance(opts, dict):
        chart_data = _extract_chart_data_from_echarts(opts, raw_type)

    # 2. Fallback to rows via chart_mapper.build_chart_slide
    if not chart_data:
        rows = chart_cfg.get("rows") or chart_cfg.get("data") or []
        if rows and isinstance(rows, list):
            try:
                from dash.tools.chart_mapper import build_chart_slide
                built = build_chart_slide(
                    rows=rows,
                    query_meta=chart_cfg.get("query_meta") or {
                        "source_table": source or chart_cfg.get("source", "query"),
                        "rowcount": len(rows),
                    },
                    eyebrow=eyebrow,
                    title=title or chart_cfg.get("title", "Chart"),
                )
                if built:
                    if narrative and not built.get("action_line"):
                        built["action_line"] = narrative
                    return built
            except Exception as e:
                logger.debug("chart_mapper.build_chart_slide failed: %s", e)

    # 3. Last resort — existing chart_data dict
    if not chart_data:
        chart_data = chart_cfg.get("chart_data") or {}

    safe_title = _smart_truncate(title or "Chart", 90)
    n_series = len(chart_data.get("series", [])) if isinstance(chart_data, dict) else 0
    show_legend = n_series > 1
    # Detect empty extraction — caller can convert to narrative slide
    has_data = bool(chart_data and chart_data.get("series") and any(
        s.get("data") for s in (chart_data.get("series") or []) if isinstance(s, dict)
    ))

    return {
        "layout": "chart",
        "eyebrow": eyebrow,
        "title": safe_title,
        "chart_type": chart_type,
        "chart_data": chart_data,
        "show_legend": show_legend,
        "action_line": _smart_truncate(narrative or "", 140),
        "source": _smart_truncate(source or chart_cfg.get("source", ""), 100),
        "_has_data": has_data,
    }


def _build_chart_slide(cell: dict) -> dict:
    cfg = cell.get("config") or {}
    # Merge top-level + config keys; top-level wins (DeepDash panels)
    merged = {**cfg, **{k: v for k, v in cell.items() if k != "config"}}
    title = cell.get("title") or cfg.get("title") or "Chart"
    narrative = cell.get("narrative") or cfg.get("narrative") or ""
    sources = cell.get("sources") or cfg.get("sources") or []
    src = ", ".join(sources[:3]) if isinstance(sources, list) else str(sources)
    return map_chart_to_slide(merged, title=title, eyebrow="ANALYSIS",
                              narrative=narrative, source=src)


def _build_narrative_slide(cell: dict) -> dict:
    cfg = cell.get("config") or {}
    body = (
        cfg.get("body") or cfg.get("text") or cfg.get("markdown")
        or cell.get("content") or cell.get("narrative") or cfg.get("narrative") or ""
    )
    bullets = cfg.get("bullets") or cell.get("bullets") or []
    if not bullets and body:
        # split into sentences if no explicit list
        import re as _re
        parts = [p.strip() for p in _re.split(r"(?<=[.!?])\s+|\n+", str(body)) if p.strip()]
        bullets = parts[:6] if parts else [str(body)[:240]]
    return {
        "layout": "content_grid",
        "eyebrow": "INSIGHT",
        "title": cell.get("title") or "Key Insight",
        "bullets": bullets,
    }


def _pretty_slug(slug: str) -> str:
    """proj_demo_pg_crm → 'Pg Crm Demo'."""
    if not slug:
        return ""
    s = slug.replace("proj_", "").replace("demo_", "")
    parts = [p.capitalize() for p in s.replace("-", "_").split("_") if p]
    return " ".join(parts) or slug


def _build_closing_slide() -> dict:
    return {
        "layout": "closing",
        "eyebrow": "WHAT'S NEXT",
        "title": "Next Steps",
        "bullets": [
            "Review highlighted KPIs with stakeholders",
            "Validate top findings against ground truth",
            "Schedule a follow-up to track progress",
        ],
    }


# ---------------------------------------------------------------------------
# DB / spec loading
# ---------------------------------------------------------------------------

def _load_dashboard_spec(dashboard_id: str) -> dict:
    eng = _get_engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT spec, project_slug FROM public.dash_dashboards_v2 WHERE id = :id"
        ), {"id": dashboard_id}).fetchone()
    if not row:
        raise HTTPException(404, f"Dashboard not found: {dashboard_id}")
    raw_spec = row[0]
    spec = raw_spec if isinstance(raw_spec, dict) else json.loads(raw_spec)
    spec["_project_slug"] = spec.get("project_slug") or row[1] or ""
    return spec


def _extract_panels(spec: dict) -> list[dict]:
    """Spec uses 'cells' (DashboardSpec) or 'panels' (DeepDashSpec). Support both."""
    cells = spec.get("cells")
    if isinstance(cells, list) and cells:
        return cells
    panels = spec.get("panels")
    if isinstance(panels, list) and panels:
        return panels
    return []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/{dashboard_id}/to-deck")
def dashboard_to_deck(dashboard_id: str, request: Request):
    try:
        spec = _load_dashboard_spec(dashboard_id)
        panels = _extract_panels(spec)
        project_slug = spec.get("_project_slug", "")
        raw_title = spec.get("title") or "Dashboard"
        # Truncate over-long cover title — long titles cause text overflow off slide
        dash_title = raw_title if len(raw_title) <= 70 else raw_title[:70].rstrip() + "…"

        # ---- Assemble slide spec list -------------------------------------
        slides: list[dict] = []

        pretty_slug = _pretty_slug(project_slug)

        # 1) KPI strip → 1 cover-stat slide
        kpi_cells = [c for c in panels if _is_kpi_panel(c)]
        if kpi_cells:
            cover = _build_cover_stat_slide(kpi_cells, dash_title)
            cover["subtitle"] = pretty_slug or cover.get("subtitle", "")
            slides.append(cover)
        else:
            slides.append({
                "layout": "cover",
                "eyebrow": "DASHBOARD",
                "title": dash_title,
                "subtitle": pretty_slug or project_slug or "",
                "stats": [],
            })

        # 2) Each chart panel → 1 chart slide (fallback to narrative if extraction empty)
        for cell in panels:
            if _is_chart_panel(cell):
                slide = _build_chart_slide(cell)
                if not slide.get("_has_data") and (cell.get("narrative") or (cell.get("config") or {}).get("narrative")):
                    slide = _build_narrative_slide(cell)
                slide.pop("_has_data", None)
                slides.append(slide)

        # 3) Insight/narrative panels → narrative slides
        for cell in panels:
            if _is_insight_panel(cell):
                slides.append(_build_narrative_slide(cell))

        # 4) Closing "Next Steps"
        slides.append(_build_closing_slide())

        # ---- AI STYLIST — choose theme + accent based on dashboard content ---
        try:
            from dash.tools.deck_stylist import choose_style
            style = choose_style(spec, panels)
        except Exception as e:
            logger.warning("deck stylist failed, using default: %s", e)
            style = {
                "theme_name": DEFAULT_THEME,
                "accent_hex": "",
                "palette_hex": [],
                "narrative_tone": "executive",
                "reasoning": f"stylist crashed: {e}",
                "source": "default",
                "profile": {},
            }
        chosen_theme = style.get("theme_name") or DEFAULT_THEME

        # Inject stylist card colors into cover slide (if present)
        for sl in slides:
            if sl.get("layout") == "cover":
                if style.get("card_bg_hex"):
                    sl["_card_bg"] = style["card_bg_hex"]
                if style.get("card_value_hex"):
                    sl["_card_value_color"] = style["card_value_hex"]
                if style.get("card_label_hex"):
                    sl["_card_label_color"] = style["card_label_hex"]
                break

        deck_spec = {
            "title": dash_title,
            "theme": chosen_theme,
            "slides": slides,
        }

        # ---- Render via native pptx_renderer ------------------------------
        out_dir = os.path.join(tempfile.gettempdir(), "dash_decks")
        os.makedirs(out_dir, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in dash_title)[:60] or "dashboard"
        out_path = os.path.join(out_dir, f"{safe_name}_{dashboard_id}.pptx")

        try:
            from dash.pptx_renderer.themes import get_theme_with_overrides
            theme_obj = get_theme_with_overrides(
                chosen_theme,
                accent_hex=style.get("accent_hex") or None,
                palette=style.get("chart_palette") or style.get("palette_hex") or None,
            )
            render_to_path(deck_spec, out_path, theme_name=chosen_theme, theme_override=theme_obj)
        except Exception as e:
            logger.exception("pptx render failed for dashboard %s", dashboard_id)
            raise HTTPException(500, f"PPTX render failed: {e}")

        # ---- Persist into dash_presentations ------------------------------
        # Mirror shape from app/export.py:131-200 (project_slug, title, version,
        # thinking JSONB, slides JSONB, source_messages JSONB).
        try:
            with open(out_path, "rb") as fh:
                pptx_bytes = fh.read()
        except Exception:
            pptx_bytes = b""

        thinking = {
            "source": "dashboard_to_deck",
            "dashboard_id": dashboard_id,
            "theme": chosen_theme,
            "panel_counts": {
                "kpi": sum(1 for c in panels if _is_kpi_panel(c)),
                "chart": sum(1 for c in panels if _is_chart_panel(c)),
                "insight": sum(1 for c in panels if _is_insight_panel(c)),
            },
            "stylist": {
                "theme": chosen_theme,
                "accent_hex": style.get("accent_hex") or "",
                "card_bg_hex": style.get("card_bg_hex") or "",
                "card_value_hex": style.get("card_value_hex") or "",
                "card_label_hex": style.get("card_label_hex") or "",
                "chart_palette": style.get("chart_palette") or [],
                "narrative_tone": style.get("narrative_tone") or "",
                "reasoning": style.get("reasoning") or "",
                "source": style.get("source") or "default",
                "domain": (style.get("profile") or {}).get("domain_guess"),
                "mood": (style.get("profile") or {}).get("mood"),
                "audience": (style.get("profile") or {}).get("audience"),
            },
        }

        eng = _get_engine()
        with eng.begin() as conn:
            existing_max = conn.execute(text(
                "SELECT MAX(version) FROM public.dash_presentations "
                "WHERE project_slug = :s AND title = :t"
            ), {"s": project_slug, "t": dash_title}).scalar()
            version = (existing_max or 0) + 1

            row = conn.execute(text(
                "INSERT INTO public.dash_presentations "
                "(project_slug, title, version, thinking, slides, source_messages, "
                " pptxgenjs_spec, rendered_pptx_path, render_engine) "
                "VALUES (:s, :t, :v, CAST(:th AS jsonb), CAST(:sl AS jsonb), CAST(:msg AS jsonb), "
                " CAST(:spec AS jsonb), :path, :eng) "
                "RETURNING id"
            ), {
                "s": project_slug,
                "t": dash_title,
                "v": version,
                "th": json.dumps(thinking),
                "sl": json.dumps(slides),
                "msg": json.dumps([{"role": "system", "content": f"converted from dashboard {dashboard_id}"}]),
                "spec": json.dumps(deck_spec),
                "path": out_path if os.path.isfile(out_path) else None,
                "eng": "python-pptx",
            }).fetchone()
            presentation_id = row[0]

        pptx_url = f"/api/presentations/{presentation_id}/pptx"
        return {
            "ok": True,
            "presentation_id": presentation_id,
            "slide_count": len(slides),
            "pptx_url": pptx_url,
            "pptx_path": out_path,
            "size_bytes": len(pptx_bytes),
            "version": version,
            "stylist": thinking["stylist"],  # surface AI style decision to frontend
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dashboard_to_deck failed")
        raise HTTPException(500, f"dashboard_to_deck failed: {e}\n{traceback.format_exc()}")
