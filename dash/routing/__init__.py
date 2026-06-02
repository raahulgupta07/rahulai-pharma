"""Complexity routing — Feature A.

Tier-1 deterministic + optional LITE-LLM tiebreak classifier that decides how
"hard" a chat message is (LOOKUP / ANALYSIS / AGENTIC) and which model tier it
would map to. Informational for now — does NOT change which team runs.
"""
from __future__ import annotations

from dash.routing.complexity_router import classify_complexity

__all__ = ["classify_complexity"]
