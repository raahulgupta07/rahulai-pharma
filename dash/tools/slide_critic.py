"""Slide Critic — adversarial McKinsey-partner review per slide.

After Stage 6 generates slides, Critic runs per-slide:
    1. Rate 1-5 (action-title strength, evidence cited, layout, narrative fit)
    2. Identify weaknesses
    3. Suggest rewrite

If score < 4.0: regenerate that slide with critique as feedback context.
Max 2 critique passes (hard cap to prevent loop / cost runaway).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _llm_call(prompt: str, task: str = "deep_analysis") -> Optional[str]:
    try:
        from dash.settings import training_llm_call
        return training_llm_call(prompt, task=task)
    except Exception as e:
        logger.warning("critic llm call failed: %s", e)
        return None


def _parse_json(s: str) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    s = s.strip()
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


def critique_slide(slide_spec: Dict[str, Any], narrative: str,
                   audience: Optional[str] = None) -> Dict[str, Any]:
    """Run adversarial review on one slide.

    Returns:
        {
            "score": 1.0-5.0,
            "weaknesses": ["vague title", "no source cited", ...],
            "suggested_rewrite": {title, bullets, action_line, layout} or None,
            "verdict": "accept" | "revise"
        }
    """
    title = slide_spec.get("title", "")
    bullets = slide_spec.get("bullets") or []
    layout = slide_spec.get("layout", "")
    action_line = slide_spec.get("action_line", "")

    aud_clause = f" for a {audience}" if audience else ""

    prompt = (
        "You are a McKinsey partner reviewing a junior's slide. Be harsh but fair.\n\n"
        f"NARRATIVE ARC: {narrative}\n\n"
        f"SLIDE TO REVIEW{aud_clause}:\n"
        f"  Title: {title}\n"
        f"  Layout: {layout}\n"
        f"  Bullets:\n" + "\n".join(f"    - {b}" for b in bullets) + "\n"
        f"  Action line: {action_line}\n\n"
        "RUBRIC (1-5 each):\n"
        "1. Action title is a FULL SENTENCE stating insight (not a topic label)\n"
        "2. Bullets cite specific numbers WITH [Q1]..[QN] tag pointing to an executed query\n"
        "3. Action line tells audience what to DO\n"
        "4. Slide fits narrative arc (situation → complication → resolution)\n"
        "5. No fabricated data, no vague filler\n\n"
        "HARD RULES — auto-fail if violated:\n"
        "- NO placeholder tokens: [X], [Y], [ERP System Name], [X]%, [X]M, $X, $XM, $[Y]M\n"
        "- NO fake citations: never write 'McKinsey', 'Gartner', 'Forrester', 'BCG', 'industry study',\n"
        "  'benchmark report' etc unless the original chat data contained that exact source.\n"
        "  Real citations look like (Source: [Q3]) referring to executed query #3, not paper titles.\n"
        "- NO contradicting numbers: do not change a number that appeared elsewhere in the deck.\n"
        "- Correlation r in [-0.1, 0.1] means NO relationship — never call this a 'driver' or 'cause'.\n"
        "- If a number is unknown, DROP THE CLAIM entirely. Don't write [X] expecting a human fill.\n\n"
        "When rewriting:\n"
        "- Keep claims that have a concrete number from the original data.\n"
        "- Strip any claim with a placeholder or invented source.\n"
        "- Prefer fewer strong bullets (2-3) over many weak ones.\n\n"
        "Output:\n"
        '{\n'
        '  "score": 3.5,  // average of 5 dimensions\n'
        '  "weaknesses": ["placeholder $X bleed", "fake McKinsey citation", ...],\n'
        '  "suggested_rewrite": {\n'
        '    "title": "<full-sentence insight, no [brackets]>",\n'
        '    "bullets": ["...with real number + [Qn] citation", ...],\n'
        '    "action_line": "→ specific action"\n'
        '  },\n'
        '  "verdict": "revise"  // "accept" if score >= 4.0\n'
        "}\n\n"
        "Return ONLY JSON."
    )

    raw = _llm_call(prompt, task="deep_analysis")
    parsed = _parse_json(raw or "")
    if not parsed:
        return {"score": 4.0, "weaknesses": [], "suggested_rewrite": None, "verdict": "accept"}

    score = parsed.get("score") or 4.0
    try:
        score = float(score)
    except Exception:
        score = 4.0

    weaknesses = parsed.get("weaknesses") or []
    if isinstance(weaknesses, str):
        weaknesses = [weaknesses]

    return {
        "score": score,
        "weaknesses": weaknesses[:5],
        "suggested_rewrite": parsed.get("suggested_rewrite"),
        "verdict": "revise" if score < 4.0 else "accept",
    }


def apply_critique(slide_spec: Dict[str, Any], critique: Dict[str, Any]) -> Dict[str, Any]:
    """Merge critique's suggested rewrite into slide spec. Preserves chart/table/kpis."""
    rewrite = critique.get("suggested_rewrite") or {}
    if not rewrite:
        return slide_spec
    out = dict(slide_spec)
    for key in ("title", "bullets", "action_line", "layout"):
        v = rewrite.get(key)
        if v:
            out[key] = v
    return out


def critique_pass(slides: List[Dict[str, Any]], narrative: str,
                  audience: Optional[str] = None,
                  threshold: float = 4.0,
                  pass_num: int = 1) -> List[Dict[str, Any]]:
    """Run one critique pass over all slides. Returns list of critique results."""
    results = []
    for i, s in enumerate(slides):
        crit = critique_slide(s, narrative, audience)
        crit["slide_idx"] = i
        crit["pass_num"] = pass_num
        results.append(crit)
    return results


def critique_and_patch(slides: List[Dict[str, Any]], narrative: str,
                       audience: Optional[str] = None,
                       max_passes: int = 2,
                       threshold: float = 4.0) -> Dict[str, Any]:
    """Full critique loop. Returns {slides, critique_log, passes_used}.

    Cost guard: max_passes hard-capped at 2 (configurable, never higher).
    """
    max_passes = min(max_passes, 2)
    critique_log: List[Dict[str, Any]] = []
    patched = list(slides)
    passes_used = 0

    for p in range(1, max_passes + 1):
        passes = critique_pass(patched, narrative, audience, threshold, pass_num=p)
        critique_log.extend(passes)
        passes_used = p

        # Apply rewrites for any slide below threshold
        any_patched = False
        for crit in passes:
            if crit["verdict"] == "revise" and crit.get("suggested_rewrite"):
                idx = crit["slide_idx"]
                if 0 <= idx < len(patched):
                    patched[idx] = apply_critique(patched[idx], crit)
                    any_patched = True

        if not any_patched:
            break  # all accepted, stop early

    return {
        "slides": patched,
        "critique_log": critique_log,
        "passes_used": passes_used,
    }


def save_critique_log(pres_id: int, critique_log: List[Dict[str, Any]]) -> None:
    """Persist critique results for audit."""
    try:
        from sqlalchemy import create_engine as _ce, text
        from sqlalchemy.pool import NullPool
        from db import db_url
        eng = _ce(db_url, poolclass=NullPool)
        with eng.connect() as conn:
            for crit in critique_log:
                try:
                    conn.execute(
                        text(
                            """
                            INSERT INTO dash.dash_slide_critique
                                (pres_id, slide_idx, pass_num, score,
                                 weaknesses, suggested_fix, accepted)
                            VALUES (:p, :si, :pn, :sc,
                                    CAST(:w AS jsonb), :sf, :acc)
                            """
                        ),
                        {
                            "p": pres_id,
                            "si": crit.get("slide_idx", 0),
                            "pn": crit.get("pass_num", 1),
                            "sc": float(crit.get("score", 0)),
                            "w": json.dumps(crit.get("weaknesses") or []),
                            "sf": (crit.get("suggested_rewrite") or {}).get("title", ""),
                            "acc": crit.get("verdict") == "revise",
                        },
                    )
                except Exception:
                    continue
            conn.commit()
    except Exception as e:
        logger.warning("save_critique_log failed: %s", e)
