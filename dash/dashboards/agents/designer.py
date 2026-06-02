"""Designer — visual + layout specialist. Maps Findings → DesignDecisions.

Knows: chart-type-by-data-shape rules, layout templates, color semantics,
       hierarchy (KPIs top, drills nested), Tufte/Munzner principles.
"""
from __future__ import annotations
import logging, json, time, re
from typing import Any
from .contracts import Finding, DesignDecision

logger = logging.getLogger(__name__)


# ───── chart picker rules ─────

def pick_chart_type(finding: Finding) -> str:
    """Heuristic: data shape → chart type."""
    data = finding.data or []
    if not data: return "bar"

    n_rows = len(data)
    cols = list(data[0].keys()) if data else []
    n_cols = len(cols)

    # Detect time column
    time_keys = ("date","time","day","week","month","year","period","ts","at","created")
    has_time = any(any(t in c.lower() for t in time_keys) for c in cols)

    # Detect numeric ratio
    sample = data[0] if data else {}
    n_numeric = sum(1 for v in sample.values() if isinstance(v, (int, float)))

    if n_rows == 1 and n_numeric == 1:
        return "kpi_only"  # not chart
    if has_time and n_cols >= 2:
        return "line"
    if n_rows <= 5 and n_numeric == 1:
        return "donut"
    if n_rows > 5 and n_rows <= 20 and n_cols == 2:
        return "bar"
    if n_rows > 20 and n_cols == 2:
        return "bar"  # sorted bar chart
    if n_cols >= 3 and n_numeric >= 2:
        return "scatter"
    return "bar"


_STORE_KEYWORDS = ("store", "scope", "branch", "site", "location", "outlet")
_SKU_KEYWORDS = ("sku", "product", "item", "name", "material", "article")


def _looks_like(col: str, kws: tuple) -> bool:
    c = (col or "").lower()
    return any(k in c for k in kws)


def _active_network_intent() -> bool:
    try:
        from dash.tools.skill_refinery import get_query_intent
        return get_query_intent() in ("network", "public")
    except Exception:
        return False


def pick_cell_type(finding: Finding) -> str:
    """KPI vs chart vs table vs insight vs network_grid."""
    data = finding.data or []
    if not data: return "insight"
    cols = list(data[0].keys())
    n_numeric = sum(1 for v in data[0].values() if isinstance(v, (int, float)))
    # WHY: banded values come back as strings ("low"/"ok") — also count cols that look like value/qty as numeric
    if n_numeric == 0:
        for c in cols:
            cl = c.lower()
            if any(k in cl for k in ("qty","quantity","count","value","amount","total","stock","sum")):
                n_numeric = 1; break
    n_cat = len(cols) - n_numeric
    has_store = any(_looks_like(c, _STORE_KEYWORDS) for c in cols)
    has_sku = any(_looks_like(c, _SKU_KEYWORDS) for c in cols)
    # WHY: trigger network grid on tag OR active network query intent — both signal cross-scope view
    network_signal = "network" in (finding.domain_tags or []) or _active_network_intent()
    if network_signal and n_cat >= 2 and n_numeric >= 1 and has_store and has_sku:
        return "network_grid"
    if len(data) == 1 and len(list(data[0].values())) == 1: return "kpi"
    if n_numeric == 1 and len(cols) == 1 and len(data) == 1: return "kpi"
    if "drill" in (finding.domain_tags or []) or "gap" in (finding.domain_tags or []):
        if len(data) > 8: return "table"
    if len(data) > 30: return "table"
    return "chart"


def pick_palette(finding: Finding) -> str:
    """Map severity + tags to palette role."""
    sev = finding.severity
    tags = " ".join(finding.domain_tags or []).lower()
    if sev == "high" or "alert" in tags or "stockout" in tags or "expir" in tags or "risk" in tags:
        return "danger"
    if sev == "medium" or "warning" in tags or "slow" in tags:
        return "warning"
    if "good" in tags or "growth" in tags or "improve" in tags:
        return "good"
    if sev == "low":
        return "neutral"
    return "info"


# ───── layout planner ─────

LAYOUT_RULES = {
    "executive": {
        "kpi_row_h": 2,
        "kpi_w": 3,
        "chart_w": 6, "chart_h": 4,
        "table_w": 12, "table_h": 4,
    },
    "operational": {
        "kpi_row_h": 2,
        "kpi_w": 2,  # smaller, more tiles
        "chart_w": 4, "chart_h": 3,
        "table_w": 8, "table_h": 3,
    },
    "analytical": {
        "kpi_row_h": 2,
        "kpi_w": 3,
        "chart_w": 8, "chart_h": 4,
        "table_w": 12, "table_h": 5,
    },
}


def pick_template(findings: list[Finding], persona: str, prompt: str) -> str:
    p = (persona or "").lower()
    pr = (prompt or "").lower()
    if any(k in p for k in ["cfo","ceo","exec","leader","vp"]):
        return "executive"
    if any(k in pr for k in ["live","now","today","real-time","ops","operational"]):
        return "operational"
    if any(k in pr for k in ["why","drive","cause","explain","analyze"]):
        return "analytical"
    return "executive"


def assign_grid(decisions: list[DesignDecision], template: str) -> list[DesignDecision]:
    """Pack cells into 12-col grid. Every cell gets non-overlapping coords."""
    rules = LAYOUT_RULES.get(template, LAYOUT_RULES["executive"])
    row = 0

    kpis = [d for d in decisions if d.cell_type == "kpi"]
    insights = [d for d in decisions if d.cell_type == "insight"]
    charts = [d for d in decisions if d.cell_type == "chart"]
    tables = [d for d in decisions if d.cell_type == "table"]
    grids = [d for d in decisions if d.cell_type == "network_grid"]

    # Top banner: only first danger insight, full-width
    banner = None
    for d in insights:
        if d.palette_role == "danger":
            banner = d
            d.grid = [0, row, 12, 2]
            row += 2
            break

    # KPI row(s)
    col = 0
    for d in kpis[:6]:
        if col + rules["kpi_w"] > 12:
            col = 0
            row += rules["kpi_row_h"]
        d.grid = [col, row, rules["kpi_w"], rules["kpi_row_h"]]
        col += rules["kpi_w"]
    if kpis:
        row += rules["kpi_row_h"]
        col = 0

    # Charts
    for d in charts:
        if col + rules["chart_w"] > 12:
            col = 0
            row += rules["chart_h"]
        d.grid = [col, row, rules["chart_w"], rules["chart_h"]]
        col += rules["chart_w"]
    if charts:
        row += rules["chart_h"]
        col = 0

    # Tables full-width
    for d in tables:
        d.grid = [0, row, rules["table_w"], rules["table_h"]]
        row += rules["table_h"]

    # Network grids — full 12-col, 4-row block
    for d in grids:
        d.grid = [0, row, 12, 4]
        row += 4

    # ALL remaining insights (skip banner, place 2-up)
    col = 0
    for d in insights:
        if d is banner:
            continue
        if col + 6 > 12:
            col = 0
            row += 2
        d.grid = [col, row, 6, 2]
        col += 6
    if any(d is not banner for d in insights):
        row += 2

    return decisions


# ───── public API ─────

async def design(findings: list[Finding], persona: str = "", prompt: str = "") -> list[DesignDecision]:
    """Map Findings → DesignDecisions. Pick chart, layout, color, title."""
    if not findings: return []

    template = pick_template(findings, persona, prompt)
    decisions: list[DesignDecision] = []

    for f in findings:
        if not isinstance(f, Finding):
            try: f = Finding(**f) if isinstance(f, dict) else Finding()
            except Exception: continue

        cell_type = pick_cell_type(f)
        chart_type = pick_chart_type(f) if cell_type == "chart" else None
        if chart_type == "kpi_only":
            cell_type = "kpi"
            chart_type = None

        palette = pick_palette(f)
        title = _make_title(f)

        # Determine x/y cols from data
        x_col, y_col = "", ""
        if f.data:
            cols = list(f.data[0].keys())
            x_col = cols[0] if cols else ""
            for c in cols[1:]:
                v = f.data[0].get(c)
                if isinstance(v, (int, float)):
                    y_col = c; break
            if not y_col and len(cols) >= 2:
                y_col = cols[1]

        cfg: dict = {
            "sql": f.sql,
            "x_col": x_col,
            "y_col": y_col,
            "chart_type": chart_type,
            "severity": f.severity,
            "domain_tags": f.domain_tags,
            "drill_into": [f.id],
        }
        grid = [0, 0, 3, 2]

        if cell_type == "network_grid" and f.data:
            cols = list(f.data[0].keys())
            x_axis = next((c for c in cols if _looks_like(c, _STORE_KEYWORDS)), cols[0])
            y_axis = next((c for c in cols if _looks_like(c, _SKU_KEYWORDS) and c != x_axis),
                          cols[1] if len(cols) > 1 else "")
            value_col = next((c for c in cols
                              if isinstance(f.data[0].get(c), (int, float))), "")
            chart_type = None
            cfg.update({
                "x_axis": x_axis,
                "y_axis": y_axis,
                "value_col": value_col,
                "band_legend": [
                    {"name": "out", "color": "#c62828"},
                    {"name": "low", "color": "#e65100"},
                    {"name": "ok",  "color": "#2e7d32"},
                ],
                "chart_type": None,
            })
            grid = [0, 0, 12, 4]

        d = DesignDecision(
            finding_id=f.id,
            cell_type=cell_type,
            chart_type=chart_type,
            grid=grid,
            palette_role=palette,
            title=title,
            headline_text=f.headline[:200],
            drill_into=[f.id],
            config=cfg,
        )
        decisions.append(d)

    decisions = assign_grid(decisions, template)
    return decisions


async def enrich(spec: dict, findings: list[Finding]) -> dict:
    """Per-cell enrichment: cause hypothesis + suggested action.

    Cheap LLM call per cell with data.
    """
    findings_by_id = {f.id: f for f in findings if hasattr(f, 'id')}

    for cell in spec.get("cells", []):
        # Try to match cell to finding via title match
        cfg = cell.get("config", {})
        finding_id = None
        for fid, f in findings_by_id.items():
            if f.headline and f.headline[:50] in cell.get("title","")[:50]:
                finding_id = fid; break

        f = findings_by_id.get(finding_id) if finding_id else None
        if not f or not f.data:
            continue

        # Cheap enrich via LLM
        try:
            prompt = f"""Given this data finding, output JSON: {{"cause":"...", "action":"..."}}.

HEADLINE: {f.headline[:200]}
DATA SAMPLE: {json.dumps(f.data[:3], default=str)[:300]}

cause: short hypothesis (1 sentence) for WHY this is happening.
action: short suggested next step (1 sentence).
JSON only, no fences.
"""
            from dash.settings import training_llm_call
            raw = training_llm_call(prompt, task="extraction") or ""
            raw = raw.strip()
            if raw.startswith("```"): raw = re.sub(r"^```\w*\n?","",raw).rstrip("`").strip()
            m = re.search(r'\{[\s\S]*\}', raw)
            if m: raw = m.group(0)
            try:
                parsed = json.loads(raw)
                cause = parsed.get("cause","")[:300]
                action = parsed.get("action","")[:300]
                # WHY: scrub raw numbers from LLM-written narrative when intent restricts
                try:
                    from dash.tools.skill_refinery import get_query_intent
                    from .text_guard import sanitize_narrative
                    intent = get_query_intent()
                    if intent != "private":
                        cause = sanitize_narrative(cause, "", intent)
                        action = sanitize_narrative(action, "", intent)
                except Exception: pass
                cfg["cause"] = cause
                cfg["action"] = action
            except Exception: pass
        except Exception as e:
            logger.debug(f"enrich failed: {e}")

        cell["config"] = cfg

    return spec


# ───── helpers ─────

_PROMPT_LEAK_PATTERNS = [
    r"critical\s+style\s+rule.*",
    r"fast\s+mode\b.*",
    r"build\s+dashboard\s+covering.*",
    r"^output\s+only.*",
    r"^you\s+are\s+.*",
    r"^json\s+only.*",
    r"^step\s+\d+[:.].*",
    r"^\d+\)\s*",
]


def _make_title(f: Finding) -> str:
    """Short human title from headline. Strip prompt leaks + Q1: prefixes."""
    h = (f.headline or "").strip()
    h = re.sub(r"^Q\d+[:.]?\s*", "", h, flags=re.I)
    for pat in _PROMPT_LEAK_PATTERNS:
        h = re.sub(pat, "", h, flags=re.I).strip()
    h = re.sub(r"^[\s\-—:>•*#]+", "", h)
    for sep in [".", ":", " — ", " - ", "\n"]:
        if sep in h:
            h = h.split(sep)[0]
            break
    h = h.strip()
    if not h or len(h) < 4:
        h = (f.id or "Insight").replace("_", " ").title()
    return h[:60].strip().title()
