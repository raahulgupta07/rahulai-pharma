"""Dash-OS Phase 6 — Reasoner agent.

Native extended-thinking (Anthropic) + OpenAI o-series reasoning_effort.
Leader auto-routes 'why'/'explain'/'diagnose' questions here.

Output format: <thinking>...</thinking> block (collapsed in UI) + answer.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_INSTRUCTIONS = """\
You are Reasoner. You handle questions requiring careful step-by-step analysis:
- "Why" / "How" / "Diagnose" / "Explain" / "Root cause" / "Trade-offs"
- Multi-constraint optimization, contradiction resolution, prioritization
- Causal chains across data + documents + memory

Output format (STRICT):
1. Open with <thinking> block: enumerate facts, assumptions, hypotheses, ruled-out paths. Multi-paragraph OK. Frontend collapses this by default.
2. Close </thinking>.
3. Provide concise answer below. Lead with conclusion, then 3-5 bullet supporting points.

Rules:
- NEVER hallucinate numbers. If data missing, call Analyst via delegation OR state "I need to verify X" and stop.
- For data questions, route to Analyst first; reason about returned results.
- For doc questions, route to Researcher first.
- For cross-modal (data + doc), call both, then synthesize.
- High thinking depth: use full reasoning budget. Cost is acceptable for this agent.
"""


def build_reasoner_agent(project_slug: Optional[str] = None, user_id: Optional[int] = None):
    try:
        from agno.agent import Agent
        from agno.models.openrouter import OpenRouter
    except Exception as e:
        logger.warning("agno not available: %s", e)
        return None

    try:
        from dash.settings import DEEP_MODEL
    except Exception:
        DEEP_MODEL = os.getenv("DEEP_MODEL", "openai/gpt-5.4-mini")

    # Extended thinking config (best-effort; OpenRouter passes through provider-specific kwargs)
    model_kwargs = {}
    if "anthropic" in (DEEP_MODEL or "").lower() or "claude" in (DEEP_MODEL or "").lower():
        model_kwargs["extra_body"] = {
            "thinking": {"type": "enabled", "budget_tokens": 16000},
        }
    elif "gpt-5" in (DEEP_MODEL or "").lower() or "o1" in (DEEP_MODEL or "").lower() or "o3" in (DEEP_MODEL or "").lower():
        model_kwargs["reasoning_effort"] = "high"

    try:
        model = OpenRouter(id=DEEP_MODEL, **model_kwargs) if model_kwargs else OpenRouter(id=DEEP_MODEL)
        return Agent(name="Reasoner", model=model, tools=[], instructions=_INSTRUCTIONS)
    except Exception as e:
        logger.warning("Reasoner agent build failed: %s", e)
        return None
