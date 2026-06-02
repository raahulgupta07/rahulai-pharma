"""Phase 9 — Layer 14 skill auto-inject.

Called by instruction builders. Finds top-1 matching skill via keyword
score (cheap, $0). Prepends skill body to system prompt for that turn only.

Behind EXPERIMENTAL_AGI=1: returns full skill body.
Off: returns empty string (no-op).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def auto_inject_block(
    user_message: str,
    project_slug: Optional[str] = None,
    agent_name: Optional[str] = None,
    top_k: int = 1,
    max_chars: int = 4000,
) -> str:
    """Return formatted skill block to prepend, or empty string."""
    if not _enabled() or not user_message:
        return ""
    try:
        from dash.skills.registry import find_skills_for, load_skill
        matches = find_skills_for(
            user_message, project_slug=project_slug, agent_name=agent_name, top_k=top_k,
        )
        if not matches:
            return ""
        blocks = []
        for m in matches:
            body = load_skill(m["id"], agent_name=agent_name)
            if body.get("ok") and body.get("instructions"):
                inst = body["instructions"][:max_chars]
                blocks.append(
                    f"## LAYER 14 — AUTO-LOADED SKILL: {m['name']}\n"
                    f"_Category: {m.get('category','—')} · Triggered by your message._\n\n"
                    f"{inst}\n"
                )
        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n\n---\n"
    except Exception as e:
        logger.warning("skill auto-inject failed: %s", e)
        return ""
