"""LLM codegen — convert insight pack + executed data into a pptxgenjs spec.

The spec is a JSON document with a small, strict layout vocabulary that the
python-pptx renderer knows how to draw. The LLM gets the
insight pack + truncated executed rows and returns ONLY the JSON spec.

Layouts allowed (8):
  cover · toc · section_divider · content_grid · table · chart · status_model · closing

Icons allowed (50): see ALLOWED_ICONS below.
Rail colors (5):    teal · amber · rose · emerald · sky (hex constants)

Output schema:
    {
      "title": str,
      "total": int,
      "brand": str,
      "slides": [ {"layout": "<one of 8>", ...layout-specific fields} ]
    }
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


ALLOWED_LAYOUTS = {
    "cover", "toc", "section_divider", "content_grid",
    "table", "chart", "status_model", "closing",
}

# 50 icon slot names — must match build.js icon dict keys.
ALLOWED_ICONS = [
    "check", "warning", "info", "alert", "x",
    "clock", "calendar",
    "chart-bar", "chart-line", "chart-pie",
    "database", "table", "server", "cloud",
    "dollar", "trend-up", "trend-down", "target",
    "briefcase", "building",
    "user", "users", "user-group", "customer",
    "settings", "refresh", "search", "filter", "layers",
    "arrow-right", "arrow-up", "arrow-down",
    "link", "sync", "cpu", "lightning", "sparkles",
    "code", "terminal", "robot",
    "star", "heart", "bookmark", "flag",
    "lock", "key", "shield", "eye",
    "file", "folder",
]

ALLOWED_RAIL_COLORS = {"#14B8A6", "#F59E0B", "#F43F5E", "#10B981", "#0EA5E9"}
ALLOWED_CHART_TYPES = {"bar", "line", "pie"}
ALLOWED_STATUS = {"on", "degraded", "off", "planned"}

MAX_SLIDES = 24


def _parse_json_lenient(text: str) -> Optional[Any]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _truncate_executed(executed: List[Dict[str, Any]], max_rows: int = 5) -> List[Dict[str, Any]]:
    out = []
    for i, e in enumerate(executed or [], 1):
        if not e.get("ok"):
            continue
        out.append({
            "qid": i,
            "question": e.get("question", "")[:200],
            "row_count": e.get("row_count", 0),
            "columns": (e.get("columns") or [])[:8],
            "rows_preview": (e.get("rows_preview") or [])[:max_rows],
        })
    return out


def _validate_slide(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return slide if it has minimum required fields for its layout, else None."""
    if not isinstance(s, dict):
        return None
    layout = s.get("layout")
    if layout not in ALLOWED_LAYOUTS:
        return None

    if layout == "cover":
        if not s.get("title"):
            return None
    elif layout == "toc":
        if not s.get("title") or not isinstance(s.get("parts"), list):
            return None
    elif layout == "section_divider":
        if not s.get("title"):
            return None
    elif layout == "content_grid":
        if not s.get("title") or not isinstance(s.get("cards"), list) or not s["cards"]:
            return None
    elif layout == "table":
        if not s.get("title") or not isinstance(s.get("columns"), list) or not isinstance(s.get("rows"), list):
            return None
    elif layout == "chart":
        if not s.get("title"):
            return None
        if s.get("chart_type") not in ALLOWED_CHART_TYPES:
            return None
        cd = s.get("chart_data")
        if not isinstance(cd, dict) or not isinstance(cd.get("labels"), list) or not isinstance(cd.get("series"), list):
            return None
    elif layout == "status_model":
        if not s.get("title") or not isinstance(s.get("items"), list) or not s["items"]:
            return None
    elif layout == "closing":
        if not s.get("title"):
            return None
    return s


def _validate_spec(spec: Any, brand: str, title: str) -> Dict[str, Any]:
    """Coerce + filter spec into the strict shape we ship to the Node renderer."""
    if not isinstance(spec, dict):
        return {"title": title, "total": 0, "brand": brand, "slides": []}
    raw_slides = spec.get("slides") or []
    if not isinstance(raw_slides, list):
        raw_slides = []
    clean: List[Dict[str, Any]] = []
    for s in raw_slides:
        v = _validate_slide(s)
        if v is None:
            logger.info("codegen: dropping invalid slide layout=%r", (s or {}).get("layout") if isinstance(s, dict) else None)
            continue
        clean.append(v)
        if len(clean) >= MAX_SLIDES:
            break
    return {
        "title": str(spec.get("title") or title),
        "total": len(clean),
        "brand": str(spec.get("brand") or brand),
        "slides": clean,
    }


_PROMPT_TEMPLATE = """You are a senior deck designer that emits a strict pptxgenjs JSON spec.

You will be given:
  - INSIGHT PACK: narrative, key_insight, supporting_evidence, recommendations, audience_action
  - EXECUTED QUERIES: list of {{qid, question, row_count, columns, rows_preview}} (first 5 rows each)

Output ONLY a single JSON object — NO markdown fences, NO prose:
{{
  "title": "<deck title>",
  "total": <slide count>,
  "brand": "{brand}",
  "slides": [ ... ]
}}

═══════════════════════════════════════════════════════════════════════════════
ALLOWED LAYOUTS (ONLY these 8 — anything else is dropped)
═══════════════════════════════════════════════════════════════════════════════

1. cover
   {{"layout":"cover","title":str,"subtitle":str?,"tagline":str?,"author":str?,
     "stats":[{{"value":str,"label":str}}, ...up to 3]}}

2. toc
   {{"layout":"toc","eyebrow":"TABLE OF CONTENTS","title":str,
     "parts":[{{"label":"PART I","title":str,"pages":"3-6"}}, ...]}}

3. section_divider
   {{"layout":"section_divider","part":"PART I","title":str,"subtitle":str,"dark":true}}

4. content_grid
   {{"layout":"content_grid","eyebrow":str,"title":str,"action_line":str?,
     "cards":[{{"title":str,"body":str,"icon":str?,"rail_color":str?}}, ...]}}

5. table
   {{"layout":"table","eyebrow":str,"title":str,
     "columns":[str,...],"rows":[[...],[...]],"note":str?}}

6. chart
   {{"layout":"chart","eyebrow":str,"title":str,
     "chart_type":"bar"|"line"|"pie",
     "chart_data":{{"labels":[str,...],"series":[{{"name":str,"values":[num,...]}}]}},
     "source":str?}}

7. status_model
   {{"layout":"status_model","eyebrow":str,"title":str,
     "items":[{{"label":str,"status":"on"|"degraded"|"off"|"planned","description":str}}, ...]}}

8. closing
   {{"layout":"closing","dark":true,"title":str,"subtitle":str?,
     "next_steps":[{{"title":str,"body":str}}, ...up to 3]}}

═══════════════════════════════════════════════════════════════════════════════
DECK STRUCTURE (18-24 slides total)
═══════════════════════════════════════════════════════════════════════════════
  - 1 cover (first)
  - 1 toc
  - 7 section_dividers (one per part)
  - 14-16 content slides (content_grid, table, chart, status_model)
  - 1 closing (last)

EVERY content slide MUST have a visualization. NO text-only "title + bullets" slides.
Every content slide MUST be one of: content_grid (with icons), table, chart, status_model.

═══════════════════════════════════════════════════════════════════════════════
ICON LIBRARY — pick `icon` ONLY from these 50 slot names
═══════════════════════════════════════════════════════════════════════════════
{icon_list}

═══════════════════════════════════════════════════════════════════════════════
RAIL COLORS — `rail_color` MUST be one of these 5 hex values
═══════════════════════════════════════════════════════════════════════════════
  "#14B8A6" (teal)
  "#F59E0B" (amber)
  "#F43F5E" (rose)
  "#10B981" (emerald)
  "#0EA5E9" (sky)

═══════════════════════════════════════════════════════════════════════════════
COPY RULES
═══════════════════════════════════════════════════════════════════════════════
- Eyebrow format: "KIND · NAME · POSITION"
    examples: "AGENT · DATA SCIENTIST", "USE CASE · 3 OF 4", "FINDING · REVENUE MIX"
- Title format: declarative sentence, ≤ 12 words.
    examples: "80% revenue comes from 292 SKUs", "Churn doubled in tier-3 cities"
- Source line on data slides: "Source: <table>.<col> · n=<rowcount>"
- All numeric claims MUST come from executed.rows_preview. Never invent.
- NEVER use placeholders like [X], [TBD], $XM, "lorem ipsum".
- NEVER cite McKinsey, Gartner, BCG, Bain, Forrester, or any third-party consultancy.

═══════════════════════════════════════════════════════════════════════════════
INPUTS
═══════════════════════════════════════════════════════════════════════════════
AUDIENCE: {audience}

INSIGHT PACK:
{insight_pack_json}

EXECUTED QUERIES (truncated to 5 rows each):
{executed_json}

Return ONLY the JSON object. No markdown fences. No prose.
"""


def generate_pptxgenjs_spec(
    insight_pack: Dict[str, Any],
    executed: List[Dict[str, Any]],
    audience: Optional[str],
    brand: str,
    author: str,
    theme: str = "city_executive",
) -> Dict[str, Any]:
    """Generate the pptxgenjs spec via the deep-analysis LLM.

    Returns a dict always shaped {title, total, brand, slides[]} — slides[]
    may be empty on LLM failure or aggressive schema rejection.

    Note: `author` and `theme` are kept in the signature for backward compat
    but are not part of the new strict spec — author can appear inside the
    cover slide; theme is passed separately to render_pptx_via_js().
    """
    title_guess = str(insight_pack.get("title") or insight_pack.get("key_insight") or "Analysis Deck")[:120]

    prompt = _PROMPT_TEMPLATE.format(
        brand=brand,
        icon_list=", ".join(ALLOWED_ICONS),
        audience=audience or "standard",
        insight_pack_json=json.dumps({
            "narrative": insight_pack.get("narrative", ""),
            "key_insight": insight_pack.get("key_insight", ""),
            "supporting_evidence": (insight_pack.get("supporting_evidence") or [])[:12],
            "recommendations": (insight_pack.get("recommendations") or [])[:8],
            "audience_action": insight_pack.get("audience_action", ""),
            "author": author,
        }, default=str, ensure_ascii=False)[:8000],
        executed_json=json.dumps(_truncate_executed(executed), default=str, ensure_ascii=False)[:12000],
    )

    raw: Optional[str] = None
    try:
        from dash.settings import training_llm_call
        raw = training_llm_call(prompt, task="deep_analysis")
    except Exception as e:
        logger.warning("codegen_pptxgenjs: training_llm_call failed: %s", e)

    spec = _parse_json_lenient(raw or "") or {}
    if isinstance(spec, dict):
        spec.setdefault("brand", brand)
        spec.setdefault("title", title_guess)
    return _validate_spec(spec, brand=brand, title=title_guess)
