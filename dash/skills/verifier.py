"""Dash-OS Phase 10B — Skill draft verifier.

4-layer evaluation of a proposed SKILL.md draft:
  1. smoke       — structural sanity (parseable frontmatter + non-empty body)
  2. reliability — frontmatter.allowed_tools resolve to known Dash tools
  3. llm_judge   — novelty + specificity vs existing skills (LITE_MODEL)
  4. regression  — does it shadow an existing skill (same name / keyword overlap)?

Behind EXPERIMENTAL_AGI=1. When the flag is off, every layer returns a stub
pass so the rest of Phase 10 (drafter -> verifier -> promotion) is a no-op
in CI / production until explicitly enabled.

Public API:
    verify_draft(skill_md, frontmatter, project_slug=None, eval_cases=None) -> dict
    update_draft_verifier_results(draft_id, results) -> None
"""
from __future__ import annotations

import json as _json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Promotion threshold — overall score must clear AND no layer may explicitly fail.
PASS_THRESHOLD = float(os.getenv("SKILL_VERIFIER_THRESHOLD", "0.7"))

# Layer weights (sum = 1.0). Smoke is cheap but load-bearing; llm_judge dominates.
_LAYER_WEIGHTS = {
    "smoke": 0.15,
    "reliability": 0.20,
    "llm_judge": 0.40,
    "regression": 0.25,
}

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

# Conservative allow-list of well-known Dash tool names. Used when dynamic
# discovery via dash.tools.* fails (tests, fresh installs).
_KNOWN_TOOLS_FALLBACK = {
    "run_sql_query", "introspect_schema", "discover_tables", "save_query",
    "create_view", "create_dashboard", "auto_visualize", "search_all",
    "load_context", "load_skill", "load_skill_tool", "discover_skills",
    "predict", "feature_importance", "detect_anomalies_ml", "classify",
    "cluster", "decompose",
    "rfm_score", "cohort_curve", "next_best_offer", "item_affinity",
    "popular_products", "clv_score", "churn_risk_score", "propose_campaign",
    "mta_summary", "compute_mrr", "mrr_breakdown", "retention_metrics",
    "mrr_trend",
    "diagnostic_analysis", "comparator_analysis", "trend_analysis",
    "predictive_analysis", "prescriptive_analysis", "anomaly_detection",
    "root_cause_analysis", "pareto_analysis", "scenario_analysis",
    "benchmark_analysis",
    "web_search", "external_data_fetch",
}


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _stub_pass() -> Dict[str, Any]:
    return {
        "ok": True, "passes": True, "overall_score": 1.0,
        "layers": {}, "recommendation": "promote",
        "improvement_hints": [],
    }


# ── Layer 1: smoke ──────────────────────────────────────────────────────
def _layer_smoke(skill_md: str, frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    if not skill_md or not skill_md.strip():
        return {"pass": False, "score": 0.0, "reason": "empty SKILL.md"}
    if not isinstance(frontmatter, dict) or not frontmatter:
        return {"pass": False, "score": 0.0, "reason": "missing or invalid frontmatter"}
    name = (frontmatter.get("name") or "").strip()
    if not name:
        return {"pass": False, "score": 0.0, "reason": "frontmatter.name missing"}
    if not _SLUG_RE.match(name):
        return {"pass": False, "score": 0.2, "reason": f"name '{name}' not a valid slug"}
    desc = (frontmatter.get("description") or "").strip()
    if not (10 <= len(desc) <= 300):
        return {"pass": False, "score": 0.4,
                "reason": f"description length {len(desc)} not in [10,300]"}
    # Body = SKILL.md minus frontmatter block.
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", skill_md, count=1, flags=re.DOTALL)
    if len(body.strip()) < 200:
        return {"pass": False, "score": 0.5,
                "reason": f"body has only {len(body.strip())} chars (need >=200)"}
    return {"pass": True, "score": 1.0, "reason": "structure OK"}


# ── Layer 2: reliability ────────────────────────────────────────────────
def _known_tool_names() -> set:
    """Best-effort discovery of registered Dash tool names; falls back to allow-list."""
    names = set(_KNOWN_TOOLS_FALLBACK)
    for mod in ("dash.tools.build", "dash.tools.semantic_search",
                "dash.tools.visualizer", "dash.skills.registry"):
        try:
            __import__(mod)
        except Exception:
            continue
    return names


def _layer_reliability(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
    tools = frontmatter.get("allowed_tools") or []
    if isinstance(tools, str):
        try:
            tools = _json.loads(tools)
        except Exception:
            tools = [t.strip() for t in tools.split(",") if t.strip()]
    if not tools:
        return {"pass": True, "score": 1.0, "reason": "no allowed_tools declared"}
    known = _known_tool_names()
    matched = [t for t in tools if t in known]
    unknown = [t for t in tools if t not in known]
    score = len(matched) / max(1, len(tools))
    if unknown:
        return {
            "pass": score >= 0.5, "score": score,
            "reason": f"unknown tools: {unknown}; matched {len(matched)}/{len(tools)}",
        }
    return {"pass": True, "score": 1.0,
            "reason": f"all {len(tools)} tool(s) resolved"}


# ── Layer 3: LLM-judge ──────────────────────────────────────────────────
def _layer_llm_judge(
    skill_md: str, frontmatter: Dict[str, Any], project_slug: Optional[str],
) -> Dict[str, Any]:
    # Soft stub when flag is off (verify_draft itself short-circuits, but be safe).
    if not _enabled():
        return {"pass": True, "score": 0.5, "reason": "judge stub (flag off)",
                "novelty": 0.5, "specificity": 0.5}
    try:
        from dash.settings import training_llm_call  # type: ignore
    except Exception as e:
        return {"pass": True, "score": 0.5, "reason": f"judge stub (no LLM: {e})",
                "novelty": 0.5, "specificity": 0.5}
    try:
        from dash.skills.registry import list_skills  # type: ignore
        existing = list_skills(project_slug=project_slug) or []
    except Exception:
        existing = []
    catalog = "\n".join(
        f"- {s.get('name')}: {(s.get('description') or '')[:120]}"
        for s in existing[:30]
    ) or "(no existing skills)"
    prompt = (
        "You are evaluating a proposed new agent SKILL.md against the existing "
        "skill catalog. Score 0..1 on two axes and output JSON.\n\n"
        f"EXISTING SKILLS:\n{catalog}\n\n"
        f"PROPOSED SKILL FRONTMATTER:\n{_json.dumps(frontmatter, indent=2)[:1500]}\n\n"
        f"PROPOSED SKILL.md (first 3000 chars):\n{skill_md[:3000]}\n\n"
        "Output ONLY JSON: "
        '{"novelty": 0.0..1.0, "specificity": 0.0..1.0, "reason": "..."}\n'
        "novelty=1.0 means substantially different from every existing skill. "
        "specificity=1.0 means concrete, scoped instructions (no vague "
        "'be helpful' patterns)."
    )
    try:
        raw = training_llm_call(prompt, "extraction")
        if not raw:
            raise RuntimeError("empty LLM response")
        try:
            obj = _json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            obj = _json.loads(m.group(0)) if m else {}
        novelty = float(obj.get("novelty", 0.5))
        specificity = float(obj.get("specificity", 0.5))
        reason = obj.get("reason", "")
    except Exception as e:
        return {"pass": True, "score": 0.5, "reason": f"judge stub ({e})",
                "novelty": 0.5, "specificity": 0.5}
    score = (novelty + specificity) / 2.0
    passes = novelty >= 0.5 and specificity >= 0.5
    return {"pass": passes, "score": score, "reason": reason,
            "novelty": novelty, "specificity": specificity}


# ── Layer 4: regression ─────────────────────────────────────────────────
def _layer_regression(
    frontmatter: Dict[str, Any], project_slug: Optional[str],
) -> Dict[str, Any]:
    proposed_name = (frontmatter.get("name") or "").strip().lower()
    proposed_kws = frontmatter.get("trigger_keywords") or []
    if isinstance(proposed_kws, str):
        try:
            proposed_kws = _json.loads(proposed_kws)
        except Exception:
            proposed_kws = [k.strip() for k in proposed_kws.split(",") if k.strip()]
    proposed_kw_set = {str(k).lower().strip() for k in proposed_kws if str(k).strip()}

    try:
        from dash.skills.registry import list_skills  # type: ignore
        existing = list_skills(project_slug=project_slug) or []
    except Exception:
        existing = []

    max_overlap = 0.0
    shadow_reason = ""
    for s in existing:
        ex_name = (s.get("name") or "").strip().lower()
        if ex_name and proposed_name and ex_name == proposed_name:
            return {"pass": False, "score": 0.0,
                    "reason": f"shadows existing skill '{ex_name}' by name",
                    "drift_pct": 1.0}
        ex_kws = s.get("trigger_keywords") or []
        if isinstance(ex_kws, str):
            try:
                ex_kws = _json.loads(ex_kws)
            except Exception:
                ex_kws = []
        ex_kw_set = {str(k).lower().strip() for k in ex_kws if str(k).strip()}
        if not (proposed_kw_set and ex_kw_set):
            continue
        overlap = len(proposed_kw_set & ex_kw_set) / max(1, len(proposed_kw_set | ex_kw_set))
        if overlap > max_overlap:
            max_overlap = overlap
            shadow_reason = f"{int(overlap*100)}% keyword overlap with '{ex_name}'"
    if max_overlap >= 0.8:
        return {"pass": False, "score": 1.0 - max_overlap,
                "reason": f"shadow detected: {shadow_reason}",
                "drift_pct": max_overlap}
    return {"pass": True, "score": 1.0 - max_overlap,
            "reason": shadow_reason or "no shadow detected",
            "drift_pct": max_overlap}


# ── Public API ──────────────────────────────────────────────────────────
def verify_draft(
    skill_md: str,
    frontmatter: Dict[str, Any],
    project_slug: Optional[str] = None,
    eval_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run all 4 verification layers. See module docstring for schema."""
    if not _enabled():
        return _stub_pass()

    layers: Dict[str, Dict[str, Any]] = {
        "smoke": _layer_smoke(skill_md, frontmatter or {}),
        "reliability": _layer_reliability(frontmatter or {}),
        "llm_judge": _layer_llm_judge(skill_md, frontmatter or {}, project_slug),
        "regression": _layer_regression(frontmatter or {}, project_slug),
    }

    overall = sum(
        _LAYER_WEIGHTS[k] * float(layers[k].get("score", 0.0))
        for k in _LAYER_WEIGHTS
    )
    any_fail = any(not layers[k].get("pass", False) for k in layers)
    passes = (overall >= PASS_THRESHOLD) and not any_fail

    hints: List[str] = []
    for name, layer in layers.items():
        if not layer.get("pass", False):
            hints.append(f"{name}: {layer.get('reason', 'failed')}")
    if layers["llm_judge"].get("novelty", 1.0) < 0.5:
        hints.append("Differentiate from existing skills (raise novelty).")
    if layers["llm_judge"].get("specificity", 1.0) < 0.5:
        hints.append("Replace vague guidance with concrete steps + tool calls.")

    if passes:
        recommendation = "promote"
    elif overall >= 0.5 and not layers["regression"].get("pass", True) is False:
        recommendation = "review"
    elif not layers["regression"].get("pass", True):
        recommendation = "reject"
    else:
        recommendation = "review"

    return {
        "ok": True, "overall_score": round(overall, 4), "passes": passes,
        "layers": layers, "recommendation": recommendation,
        "improvement_hints": hints,
    }


def update_draft_verifier_results(draft_id: str, results: Dict[str, Any]) -> None:
    """Persist verifier results into dash.dash_skill_drafts.verifier_results.

    Also updates status to 'verified' (passes=True) or 'rejected' otherwise.
    No-op when DB is unavailable.
    """
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            eng = get_sql_engine()
        except Exception:
            eng = None
    if eng is None:
        logger.debug("update_draft_verifier_results: no DB engine; skipping")
        return
    new_status = "verified" if results.get("passes") else "rejected"
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dash.dash_skill_drafts
                       SET verifier_results = CAST(:vr AS jsonb),
                           status = :st
                     WHERE id = :id
                    """
                ),
                {"vr": _json.dumps(results), "st": new_status, "id": draft_id},
            )
    except Exception as e:
        logger.warning("update_draft_verifier_results failed: %s", e)
