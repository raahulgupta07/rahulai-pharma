from dash.settings import LITE_MODEL
"""
Meta-Learning
=============

Tracks which self-correction strategies work best for different error types.
Analyzes agent responses for self-correction patterns and logs outcomes.
Runs as a background task — does not block chat.
"""

import json
from os import getenv

import httpx
from sqlalchemy import text

from db import get_sql_engine


def extract_meta_learnings(project_slug: str, question: str, answer: str, dialect: str | None = None):
    """Analyze agent response for self-correction patterns and log strategies.

    ``dialect`` (Phase 3): SQL dialect of the active provider (e.g. 'postgres',
    'mysql', 'sqlserver'). Plumbed through for callers; not persisted yet
    since ``dash_meta_learnings`` has no dialect column. The param is accepted
    so call sites can thread it once the schema lands.
    """
    _ = dialect  # reserved for future per-dialect strategy tracking
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    # Only analyze responses that mention corrections, retries, or errors
    lower = answer.lower()
    correction_signals = ['fixed', 'corrected', 'retry', 'retried', 'initially', 'instead',
                          'error', 'column does not exist', 'introspect', 'wrong column',
                          'updated the query', 'modified', 'zero rows', 'no data found']
    if not any(s in lower for s in correction_signals):
        return

    prompt = f"""Analyze this data agent response. Did it self-correct during query execution?

QUESTION: {question}

RESPONSE: {answer[:2000]}

If the agent encountered an error and fixed it, extract:
- error_type: one of [column_not_found, table_not_found, join_error, type_mismatch, zero_rows, syntax_error, permission_denied, other]
- fix_strategy: one of [introspect_schema, sample_distinct_values, different_join, remove_filter, add_cast, change_column, change_table, simplify_query, other]
- success: did the fix work? (true/false)
- error_message: brief description of the original error

If NO self-correction occurred, return empty array.

Respond with ONLY valid JSON (no markdown):
{{"corrections": [
  {{"error_type": "column_not_found", "fix_strategy": "introspect_schema", "success": true, "error_message": "Column 'rev' not found, used introspect to find 'revenue'"}}
]}}

If no corrections: {{"corrections": []}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": LITE_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "temperature": 0},
            timeout=15,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip().strip("`").strip())
        corrections = parsed.get("corrections", [])

        if not corrections:
            return

        engine = get_sql_engine()
        with engine.connect() as conn:
            for c in corrections[:3]:
                conn.execute(text(
                    "INSERT INTO public.dash_meta_learnings "
                    "(project_slug, error_type, fix_strategy, success, error_message) "
                    "VALUES (:slug, :et, :fs, :success, :em)"
                ), {
                    "slug": project_slug,
                    "et": c.get("error_type", "other"),
                    "fs": c.get("fix_strategy", "other"),
                    "success": c.get("success", False),
                    "em": c.get("error_message", "")[:500],
                })
            conn.commit()
    except Exception:
        pass
