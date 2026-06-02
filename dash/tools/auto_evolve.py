from dash.settings import DEEP_MODEL
"""
Auto-Evolve Instructions
========================

Automatically generates evolved instructions when enough chats have accumulated.
Called from background tasks — does not block chat.
"""

import json
from os import getenv

import httpx
from sqlalchemy import text

from db import get_sql_engine


def auto_evolve_instructions(project_slug: str):
    """Generate evolved instructions from accumulated learnings."""
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    engine = get_sql_engine()
    with engine.connect() as conn:
        memories = conn.execute(text(
            "SELECT fact FROM public.dash_memories WHERE project_slug = :s AND (archived IS NULL OR archived = FALSE) ORDER BY created_at DESC LIMIT 30"
        ), {"s": project_slug}).fetchall()

        feedback_good = conn.execute(text(
            "SELECT question, answer FROM public.dash_feedback WHERE project_slug = :s AND rating = 'up' ORDER BY created_at DESC LIMIT 10"
        ), {"s": project_slug}).fetchall()

        feedback_bad = conn.execute(text(
            "SELECT question, answer FROM public.dash_feedback WHERE project_slug = :s AND rating = 'down' ORDER BY created_at DESC LIMIT 5"
        ), {"s": project_slug}).fetchall()

        patterns = conn.execute(text(
            "SELECT question, sql FROM public.dash_query_patterns WHERE project_slug = :s ORDER BY uses DESC LIMIT 10"
        ), {"s": project_slug}).fetchall()

        latest = conn.execute(text(
            "SELECT version FROM public.dash_evolved_instructions WHERE project_slug = :s ORDER BY version DESC LIMIT 1"
        ), {"s": project_slug}).fetchone()

        chat_count = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_quality_scores WHERE project_slug = :s"
        ), {"s": project_slug}).scalar() or 0

    mem_text = "\n".join(f"- {r[0]}" for r in memories) if memories else "None"
    good_text = "\n".join(f"- Q: {r[0]}\n  A: {(r[1] or '')[:150]}" for r in feedback_good) if feedback_good else "None"
    bad_text = "\n".join(f"- Q: {r[0]}" for r in feedback_bad) if feedback_bad else "None"
    pattern_text = "\n".join(f"- {r[0]} → {r[1][:80]}" for r in patterns) if patterns else "None"

    prompt = f"""Generate concise supplementary instructions (max 400 words) for a data analyst agent based on its learnings.

MEMORIES: {mem_text}
GOOD RESPONSES: {good_text}
BAD RESPONSES: {bad_text}
PATTERNS: {pattern_text}

Focus on: data-specific patterns, user preferences, common mistakes to avoid, proven approaches.

Respond with ONLY valid JSON: {{"instructions": "...", "reasoning": "..."}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": DEEP_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 800, "temperature": 0.1},
            timeout=30,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        instructions = parsed.get("instructions", "")
        reasoning = parsed.get("reasoning", "")

        if not instructions:
            return

        new_version = (latest[0] + 1) if latest else 1
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_evolved_instructions (project_slug, instructions, version, reasoning, chat_count_at_generation) "
                "VALUES (:s, :inst, :v, :r, :cc)"
            ), {"s": project_slug, "inst": instructions, "v": new_version, "r": reasoning, "cc": chat_count})
            conn.commit()
    except Exception:
        pass
