"""Content pipeline workflow.

Pattern: research → outline → draft → review → revise (loop max 3
iterations with end condition).

End condition: critic returns ``quality_score >= 0.85`` OR
``iteration >= max_iterations``.

Schedule: on-demand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Dict

from .ai_research_workflow import _llm, _record_run

logger = logging.getLogger(__name__)

WORKFLOW_META = {
    "name": "content_pipeline",
    "schedule": "manual",
    "description": "research→outline→draft→review→revise loop with quality-gated end condition.",
    "tags": ["content", "loop", "critic", "deep"],
}


_SCORE_RE = re.compile(r"quality_score\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def _parse_score(text: str) -> float:
    """Extract quality_score from critic output. Returns 0.0 on miss."""
    if not text:
        return 0.0
    # Try JSON first
    try:
        # find a JSON object substring
        m = re.search(r"\{[^{}]*\"quality_score\"[^{}]*\}", text)
        if m:
            obj = json.loads(m.group(0))
            return float(obj.get("quality_score", 0.0))
    except Exception:
        pass
    m = _SCORE_RE.search(text)
    if m:
        try:
            v = float(m.group(1))
            # Accept 0-1 or 0-100 scales
            return v / 100.0 if v > 1.0 else v
        except Exception:
            return 0.0
    return 0.0


async def run_content_pipeline(topic: str, max_iterations: int = 3) -> Dict[str, Any]:
    max_iter = max(1, min(5, int(max_iterations)))
    run_id = f"wfr2_{secrets.token_hex(4)}"
    args = {"topic": topic, "max_iterations": max_iter}
    _record_run(run_id, WORKFLOW_META["name"], args, status="running")
    t0 = time.time()

    history = []
    try:
        # 1. RESEARCH
        research = await _llm(
            f"Research topic '{topic}'. Produce 10 verified factual bullets with rough sources.",
            task="analysis",
        )
        history.append({"step": "research", "output": research[:4000]})

        # 2. OUTLINE
        outline = await _llm(
            f"Topic: {topic}\nResearch:\n{research}\n\n"
            "Produce a hierarchical outline (H2/H3) for a 1200-word article.",
            task="analysis",
        )
        history.append({"step": "outline", "output": outline[:4000]})

        # 3. DRAFT
        draft = await _llm(
            f"Topic: {topic}\nOutline:\n{outline}\n\n"
            "Write the full ~1200-word draft in clean Markdown. No filler.",
            task="deep_analysis",
        )
        history.append({"step": "draft_v1", "output": draft[:6000]})

        last_score = 0.0
        last_review = ""
        # 4. REVIEW + 5. REVISE loop
        for it in range(1, max_iter + 1):
            review = await _llm(
                f"You are a strict editor. Topic: {topic}\nDraft:\n{draft}\n\n"
                "Critique the draft. End your response with a line:\n"
                'quality_score: <0.0-1.0>\n'
                "where 1.0 is publication-ready.",
                task="analysis",
            )
            score = _parse_score(review)
            history.append({"step": f"review_{it}", "output": review[:3000], "score": score})
            last_review = review
            last_score = score

            if score >= 0.85:
                break
            if it >= max_iter:
                break

            draft = await _llm(
                f"Topic: {topic}\nPrevious draft:\n{draft}\n\n"
                f"Editor critique:\n{review}\n\n"
                "Produce a revised full draft addressing every critique point. Markdown only.",
                task="deep_analysis",
            )
            history.append({"step": f"draft_v{it + 1}", "output": draft[:6000]})

        result = {
            "topic": topic,
            "final_draft": draft,
            "final_score": last_score,
            "iterations": sum(1 for h in history if h["step"].startswith("review_")),
            "history": history,
            "last_review": last_review,
            "elapsed_s": round(time.time() - t0, 2),
            "run_id": run_id,
        }
        _record_run(run_id, WORKFLOW_META["name"], args, status="done", result=result)
        return {"ok": True, "run_id": run_id, **result}
    except Exception as e:
        logger.exception("content_pipeline failed")
        _record_run(run_id, WORKFLOW_META["name"], args, status="failed", error=str(e))
        return {"ok": False, "run_id": run_id, "error": str(e)}
