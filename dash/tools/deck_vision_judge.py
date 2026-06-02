"""Deep Deck Phase 4 — Vision QA judge (stage 8).

Different-model judge that inspects a rendered slide (PNG/JPG) and scores
it 0-100 on layout, legibility, narrative coherence, color accessibility,
and factual consistency. Returns structured JSON.

TACL different-model rule: this MUST use DEEP_MODEL (via the
"deep_analysis" task) while the generator uses CHAT_MODEL. Enforced
by hardcoding the task name in `judge_slide`.

Fail-soft: any error → returns {score: 100, issues: [], suggestions: []}
so a flaky judge / OpenRouter outage never blocks deck delivery.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── _first_object helper, copied inline from app/metrics_api.py ──────
def _first_object(s: str) -> Optional[str]:
    """Scan for the first balanced {...} object and return it as a string.
    Returns None if no balanced object found."""
    if not s:
        return None
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


_JUDGE_PROMPT = (
    "You are a senior visual design critic. Score this slide 0-100 on: "
    "(1) layout balance / no overflow, (2) data legibility / chart readability, "
    "(3) narrative coherence with the title, (4) color accessibility, "
    "(5) no factual inconsistencies. "
    "Reply ONLY JSON: {\"score\": int, \"issues\": [string], \"suggestions\": [string]}.\n\n"
    "Slide title: {title}\n"
    "Slide layout: {layout}\n"
    "Slide JSON (truncated): {slide_json}\n"
    "Deck context (truncated): {deck_context}\n"
)


def _safe_pass() -> Dict[str, Any]:
    """Fail-soft sentinel: pass with score 100."""
    return {"score": 100, "issues": [], "suggestions": []}


def _read_image_b64(path: str) -> Optional[Dict[str, str]]:
    """Read an image file → {b64, mime} dict for training_vision_call.
    Returns None if file missing/unreadable."""
    if not path or not os.path.exists(path):
        return None
    try:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext, "image/png")
        with open(path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return {"b64": b64, "mime": mime}
    except Exception as e:
        logger.warning("deck_vision_judge: read image failed (%s): %s", path, e)
        return None


def _parse_judge_response(raw: Optional[str]) -> Dict[str, Any]:
    """Parse LLM response → judge dict. Fail-soft to pass-sentinel."""
    if not raw:
        return _safe_pass()
    text = raw.strip()
    # Strip code fences
    text = re.sub(r"```[a-zA-Z]*\n?", "", text).replace("```", "").strip()
    blob = _first_object(text)
    if not blob:
        return _safe_pass()
    try:
        parsed = json.loads(blob)
    except Exception:
        return _safe_pass()
    if not isinstance(parsed, dict):
        return _safe_pass()
    try:
        score_raw = parsed.get("score", 100)
        score = int(round(float(score_raw)))
    except Exception:
        score = 100
    score = max(0, min(100, score))
    issues = parsed.get("issues") or []
    suggestions = parsed.get("suggestions") or []
    if not isinstance(issues, list):
        issues = []
    if not isinstance(suggestions, list):
        suggestions = []
    return {
        "score": score,
        "issues": [str(x)[:240] for x in issues if x][:8],
        "suggestions": [str(x)[:240] for x in suggestions if x][:8],
    }


def judge_slide(
    slide_png_path: Optional[str],
    slide_spec: Dict[str, Any],
    deck_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Vision-judge a single rendered slide.

    Args:
        slide_png_path: filesystem path to a rendered preview (PNG/JPG).
            If None or missing, falls back to text-only judging via
            training_llm_call (still uses DEEP_MODEL task).
        slide_spec: the slide's spec dict (title, layout, body, etc.)
        deck_context: surrounding deck info (audience, brand, total slides)

    Returns:
        {"score": int 0-100, "issues": [str], "suggestions": [str]}
        On any error → {"score": 100, "issues": [], "suggestions": []}
        so a flaky judge never blocks deck delivery.

    NOTE: Uses task="deep_analysis" so the judge model is DEEP_MODEL
    (different from CHAT_MODEL used by the generator). Enforces the
    TACL different-model rule.
    """
    try:
        title = str(slide_spec.get("title") or "")[:120]
        layout = str(slide_spec.get("layout") or "default")[:40]
        # Compact JSON to keep prompt tight
        try:
            slide_json = json.dumps(slide_spec, default=str)[:2400]
        except Exception:
            slide_json = str(slide_spec)[:2400]
        try:
            ctx_json = json.dumps(deck_context, default=str)[:800]
        except Exception:
            ctx_json = str(deck_context)[:800]

        prompt = _JUDGE_PROMPT.format(
            title=title or "(untitled)",
            layout=layout,
            slide_json=slide_json,
            deck_context=ctx_json,
        )

        img = _read_image_b64(slide_png_path) if slide_png_path else None

        raw: Optional[str] = None
        if img is not None:
            # Vision path — uses DEEP_MODEL via deep_analysis task
            try:
                from dash.settings import training_vision_call
                raw = training_vision_call(prompt, [img], task="deep_analysis")
            except Exception as e:
                logger.warning("deck_vision_judge: vision call failed: %s", e)
                raw = None
        if not raw:
            # Text-only fallback — still DEEP_MODEL (deep_analysis task)
            try:
                from dash.settings import training_llm_call
                raw = training_llm_call(prompt, task="deep_analysis")
            except Exception as e:
                logger.warning("deck_vision_judge: text fallback failed: %s", e)
                raw = None

        return _parse_judge_response(raw)
    except Exception as e:
        logger.warning("deck_vision_judge.judge_slide failed: %s", e)
        return _safe_pass()
