"""Slide polish — 4 narrow specialist transforms applied AFTER stage_build.

Each transform:
- Returns a *mutated copy*, never mutates input.
- Try/except per slide — a single bad slide never crashes the pipeline.
- LLM tasks use task="extraction" → LITE_MODEL (cheap, fast).
- Visual picker is pure rules ($0, no LLM).

Functions:
    apply_action_titles(slides, narrative)      — label → action-sentence titles
    apply_evidence_citer(slides, executed)      — enforce (Source: [Qn]) on numeric claims
    apply_visual_picker(slide, data)            — set slide.chart_type by data shape (mutates one slide)
    apply_narrative_arc(slides, narrative)      — reorder situation → complication → resolution → recommendation
"""
from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_NUMERIC_CLAIM_RE = re.compile(
    r"(\$?\s?\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?\s?%?|"     # 1,234 / 12.5% / $1.2M
    r"\$\s?\d+(?:\.\d+)?\s?[KMB]?|"                       # $12K / $1.2M
    r"\b\d+(?:\.\d+)?\s?(?:x|×)\b)",                      # 2.5x / 3×
    re.IGNORECASE,
)
_HAS_SOURCE_RE = re.compile(r"\(\s*Source\s*:\s*\[?Q\d+\]?\s*\)", re.IGNORECASE)


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _has_numeric_claim(text: str) -> bool:
    if not text:
        return False
    return bool(_NUMERIC_CLAIM_RE.search(text))


def _has_source_tag(text: str) -> bool:
    if not text:
        return False
    return bool(_HAS_SOURCE_RE.search(text))


def _parse_json_loose(raw: Optional[str]) -> Optional[Any]:
    """Best-effort JSON parse: handles ```fenced``` and leading/trailing text."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        # strip ```json ... ```
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    # Try first {...} or [...] block
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


# ──────────────────────────────────────────────────────────────────────────────
# 1. Action Titles — label → full-sentence insight
# ──────────────────────────────────────────────────────────────────────────────

_ACTION_TITLE_PROMPT = """You rewrite slide titles using the McKinsey "ghost-deck" rule.

RULE: Every slide title must be a FULL SENTENCE that conveys the takeaway, NOT a topic label.

BAD  (label):  "Revenue by Channel"
GOOD (action): "Bakery drives 69% of revenue with 28% YoY growth"

BAD  (label):  "Customer Segmentation"
GOOD (action): "Top 20% of customers generate 73% of revenue"

INPUTS:
- Current title (may be a label or already a sentence)
- Slide bullets (the supporting facts)
- Overall narrative (the deck's story)

OUTPUT: Return ONLY the rewritten title as a single sentence. No quotes. No prefix. No explanation.
If the title is already a strong action sentence (has a verb + a number/comparison), return it unchanged.

---
NARRATIVE: {narrative}

CURRENT TITLE: {title}

BULLETS:
{bullets}

REWRITTEN TITLE:"""


def apply_action_titles(slides: List[Dict[str, Any]], narrative: str) -> List[Dict[str, Any]]:
    """Rewrite each slide title from label-style to action-sentence style via LITE_MODEL."""
    from dash.settings import training_llm_call

    out = copy.deepcopy(slides) if slides else []
    narrative = narrative or ""

    for idx, slide in enumerate(out):
        try:
            title = (_safe_get(slide, "title", "") or "").strip()
            bullets = _safe_get(slide, "bullets", []) or []
            if not title:
                continue
            # Skip if already strong action-sentence (verb + number)
            if len(title.split()) >= 6 and _has_numeric_claim(title):
                continue
            prompt = _ACTION_TITLE_PROMPT.format(
                narrative=narrative[:1200],
                title=title,
                bullets="\n".join(f"- {b}" for b in bullets[:8]) or "(no bullets)",
            )
            raw = training_llm_call(prompt, task="extraction")
            if raw:
                new_title = raw.strip().strip('"').strip("'").splitlines()[0].strip()
                # sanity: keep <= 140 chars, non-empty, different from "REWRITTEN TITLE:"
                if new_title and 4 <= len(new_title) <= 140 and "REWRITTEN" not in new_title.upper():
                    slide["title"] = new_title
        except Exception as e:
            logger.warning("apply_action_titles slide %d failed: %s", idx, e)
            continue
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 2. Evidence Citer — enforce (Source: [Qn]) on numeric claims
# ──────────────────────────────────────────────────────────────────────────────

def _build_executed_index(executed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a slim index of executed queries for LLM citation matching."""
    idx = []
    for i, e in enumerate(executed or []):
        if not isinstance(e, dict):
            continue
        if not e.get("ok", True):
            continue
        idx.append({
            "qn": f"Q{i+1}",
            "question": str(e.get("question") or e.get("intent") or "")[:160],
            "sql": str(e.get("sql") or "")[:300],
            "summary": str(
                e.get("summary") or e.get("result_summary") or
                e.get("answer") or ""
            )[:200],
        })
    return idx


_CITER_PROMPT = """You enforce citation discipline on slide content.

RULES:
1. Every numeric claim (dollar amount, percentage, count, multiplier) MUST end with (Source: [Qn]) where Qn refers to an executed query below.
2. If a numeric claim does NOT match ANY executed query, DROP that bullet entirely.
3. Titles: append the citation tag if a numeric claim is present and matches a query. If no match, strip the number from the title (keep the qualitative point) rather than drop the title.
4. Non-numeric bullets pass through unchanged.
5. Already-cited bullets pass through unchanged.

EXECUTED QUERIES (you may cite ONLY these):
{queries}

SLIDE TITLE: {title}

SLIDE BULLETS:
{bullets}

OUTPUT: Return ONLY valid JSON, no prose:
{{"title": "<new title>", "bullets": ["<bullet 1>", "<bullet 2>", ...]}}"""


def apply_evidence_citer(
    slides: List[Dict[str, Any]],
    executed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """For each slide, enforce (Source: [Qn]) on numeric claims; drop unsupported claims."""
    from dash.settings import training_llm_call

    out = copy.deepcopy(slides) if slides else []
    q_index = _build_executed_index(executed or [])
    if not q_index:
        return out  # no queries to cite against — pass through
    queries_block = "\n".join(
        f"- {q['qn']}: {q['question']} → {q['summary'] or q['sql']}" for q in q_index
    )

    for idx, slide in enumerate(out):
        try:
            title = _safe_get(slide, "title", "") or ""
            bullets = _safe_get(slide, "bullets", []) or []
            # Fast path: nothing numeric, skip LLM
            joined = title + " | " + " | ".join(bullets)
            if not _has_numeric_claim(joined):
                continue
            # Fast path: every bullet+title is already sourced
            if (_has_source_tag(title) or not _has_numeric_claim(title)) and all(
                (_has_source_tag(b) or not _has_numeric_claim(b)) for b in bullets
            ):
                continue
            prompt = _CITER_PROMPT.format(
                queries=queries_block,
                title=title,
                bullets="\n".join(f"- {b}" for b in bullets[:12]) or "(no bullets)",
            )
            raw = training_llm_call(prompt, task="extraction")
            parsed = _parse_json_loose(raw)
            if isinstance(parsed, dict):
                new_title = parsed.get("title")
                new_bullets = parsed.get("bullets")
                if isinstance(new_title, str) and new_title.strip():
                    slide["title"] = new_title.strip()
                if isinstance(new_bullets, list):
                    slide["bullets"] = [
                        str(b).strip() for b in new_bullets if str(b).strip()
                    ]
        except Exception as e:
            logger.warning("apply_evidence_citer slide %d failed: %s", idx, e)
            continue
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 3. Visual Picker — pure rules, $0, no LLM
# ──────────────────────────────────────────────────────────────────────────────

def _coerce_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _infer_data_shape(data: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect a slide/data dict and return shape hints used by the rules."""
    shape: Dict[str, Any] = {
        "n_numeric": 0,
        "n_categories": 0,
        "time_ordered": False,
        "composition": False,
        "distribution": False,
        "single_value": False,
    }
    if not isinstance(data, dict):
        return shape

    # Explicit hints win
    for k in ("n_numeric", "n_categories"):
        if k in data:
            shape[k] = _coerce_int(data[k], 0)
    for k in ("time_ordered", "composition", "distribution", "single_value"):
        if k in data:
            shape[k] = bool(data[k])

    # Try to infer from rows/columns if present
    rows = data.get("rows") or data.get("data") or []
    cols = data.get("columns") or data.get("cols") or []
    if isinstance(cols, list) and cols and shape["n_numeric"] == 0:
        numeric_cols = [
            c for c in cols
            if isinstance(c, dict) and str(c.get("type", "")).lower() in
            ("number", "numeric", "int", "float", "integer", "double")
        ]
        if numeric_cols:
            shape["n_numeric"] = len(numeric_cols)
        # time-ordered detection
        if any(
            "date" in str(c.get("name", "")).lower() or
            "time" in str(c.get("name", "")).lower() or
            "month" in str(c.get("name", "")).lower() or
            "year" in str(c.get("name", "")).lower()
            for c in cols if isinstance(c, dict)
        ):
            shape["time_ordered"] = True
    if isinstance(rows, list) and shape["n_categories"] == 0:
        shape["n_categories"] = len(rows)

    # KPI hint: a single big number explicitly flagged
    if data.get("kpi") or data.get("big_number"):
        shape["single_value"] = True

    # Chart type hint passthrough (caller may have pre-set it)
    if data.get("chart_type") and not data.get("force_pick"):
        shape["existing_chart_type"] = data["chart_type"]

    return shape


def _pick_chart_type(shape: Dict[str, Any]) -> str:
    """Pure rules from the spec. NO LLM."""
    n_num = _coerce_int(shape.get("n_numeric"), 0)
    n_cat = _coerce_int(shape.get("n_categories"), 0)
    time_ordered = bool(shape.get("time_ordered"))
    composition = bool(shape.get("composition"))
    distribution = bool(shape.get("distribution"))
    single_value = bool(shape.get("single_value"))

    if single_value or (n_num == 1 and n_cat == 0):
        return "kpi"
    if distribution:
        return "histogram"
    if composition and time_ordered:
        return "stacked_area"
    if composition and 0 < n_cat <= 6:
        return "donut"
    if n_num >= 2:
        return "scatter"
    if n_num == 1 and time_ordered:
        return "line"
    if n_num == 1 and 0 < n_cat <= 8:
        return "bar"
    if n_num == 1 and n_cat > 8:
        return "horizontal_bar"
    # default fallback: prefer existing or bar
    return shape.get("existing_chart_type") or "bar"


def apply_visual_picker(slide: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Mutates one slide: sets `chart_type` based on data shape. Pure rules, $0."""
    if not isinstance(slide, dict):
        return slide
    try:
        shape = _infer_data_shape(data if isinstance(data, dict) else {})
        # Skip if slide has no chart intent at all
        wants_chart = (
            shape["n_numeric"] > 0 or shape["single_value"] or
            shape["composition"] or shape["distribution"] or
            slide.get("chart_type") is not None or slide.get("visual") is not None
        )
        if not wants_chart:
            return slide
        slide["chart_type"] = _pick_chart_type(shape)
    except Exception as e:
        logger.warning("apply_visual_picker failed: %s", e)
    return slide


# ──────────────────────────────────────────────────────────────────────────────
# 4. Narrative Arc — reorder into situation → complication → resolution → recommendation
# ──────────────────────────────────────────────────────────────────────────────

_ARC_TAGS = ("situation", "complication", "resolution", "recommendation")
_ARC_TARGET_COUNTS = {
    "situation": 1,
    "complication": 3,      # 2-3
    "resolution": 3,        # 2-3
    "recommendation": 1,
}
_ARC_ORDER = {tag: i for i, tag in enumerate(_ARC_TAGS)}

_ARC_PROMPT = """Tag each slide with ONE of: situation, complication, resolution, recommendation.

Narrative arc rules:
- situation     = sets context, current state, baseline ("here is what we see")
- complication  = the problem, gap, risk, surprising fact ("here is what's wrong")
- resolution    = the analysis, drivers, root cause, opportunity ("here is why / what to do")
- recommendation = concrete action, decision, ask ("here is the ask")

DECK NARRATIVE: {narrative}

SLIDES:
{slides}

OUTPUT: Return ONLY valid JSON, no prose. Array, one tag per slide, in original order:
{{"tags": ["situation", "complication", ...]}}"""


def _build_slide_summary(slide: Dict[str, Any], idx: int) -> str:
    title = _safe_get(slide, "title", "") or ""
    bullets = _safe_get(slide, "bullets", []) or []
    bul_text = " · ".join(str(b)[:80] for b in bullets[:3])
    return f"[{idx}] {title} :: {bul_text}"


def apply_narrative_arc(
    slides: List[Dict[str, Any]],
    narrative: str,
) -> List[Dict[str, Any]]:
    """Tag each slide with arc role via LITE_MODEL, then sort situation→...→recommendation."""
    from dash.settings import training_llm_call

    if not slides:
        return []
    out = copy.deepcopy(slides)
    narrative = narrative or ""

    # Build tag list (fail-soft per-slide)
    tags: List[str] = []
    try:
        summary = "\n".join(_build_slide_summary(s, i) for i, s in enumerate(out))
        prompt = _ARC_PROMPT.format(narrative=narrative[:1200], slides=summary[:6000])
        raw = training_llm_call(prompt, task="extraction")
        parsed = _parse_json_loose(raw)
        if isinstance(parsed, dict):
            t = parsed.get("tags")
            if isinstance(t, list):
                tags = [str(x).strip().lower() for x in t]
    except Exception as e:
        logger.warning("apply_narrative_arc tagging failed: %s", e)

    # Normalize / fill tags. Default tag heuristic = position-based fallback.
    n = len(out)
    if len(tags) != n:
        # heuristic fallback: first=situation, last=recommendation, middle split
        tags = []
        for i in range(n):
            if i == 0:
                tags.append("situation")
            elif i == n - 1:
                tags.append("recommendation")
            elif i <= n // 2:
                tags.append("complication")
            else:
                tags.append("resolution")

    # Clamp unknown tags to closest valid
    fixed_tags: List[str] = []
    for t in tags:
        if t not in _ARC_ORDER:
            # crude alias rules
            if "problem" in t or "risk" in t or "issue" in t:
                t = "complication"
            elif "action" in t or "ask" in t or "next" in t:
                t = "recommendation"
            elif "analysis" in t or "driver" in t or "why" in t:
                t = "resolution"
            else:
                t = "situation"
        fixed_tags.append(t)

    # Sort: arc order, then original index (stable within bucket)
    indexed = list(enumerate(out))
    indexed.sort(key=lambda p: (_ARC_ORDER.get(fixed_tags[p[0]], 99), p[0]))

    # Optionally tag back onto slide so downstream consumers can see it
    reordered: List[Dict[str, Any]] = []
    for orig_idx, slide in indexed:
        try:
            slide["_arc_role"] = fixed_tags[orig_idx]
        except Exception:
            pass
        reordered.append(slide)
    return reordered
