from dash.settings import TRAINING_MODEL
"""
Suggest Rules
=============

After a conversation, extract potential business rules for human review.
Runs as a background task — does not block the chat response.
"""

import json
from os import getenv

import httpx
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url


def suggest_rules_from_conversation(project_slug: str, session_id: str, question: str, answer: str):
    """Call LLM to extract business rule candidates from a conversation."""
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    prompt = f"""You are analyzing a data conversation to extract reusable business rules.

QUESTION: {question}

ANSWER: {answer[:2000]}

Extract 0-3 business rules that could help the agent answer similar questions better in the future.
Rules should be specific, reusable definitions like:
- Metric definitions ("MRR = sum of active subscription amounts")
- Business logic ("fiscal year starts in April")
- Data quality notes ("revenue column includes refunds, filter status='completed' for net revenue")
- Calculation rules ("churn rate = lost customers / total customers at period start")

If no useful rules can be extracted, return an empty array.

Respond with ONLY valid JSON (no markdown):
[{{"name": "short rule name", "type": "business_rule|metric|gotcha|calculation", "definition": "full definition"}}]"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": TRAINING_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.1,
            },
            timeout=15,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        rules = json.loads(content.strip().strip("`").strip())

        if not isinstance(rules, list) or len(rules) == 0:
            return

        # Insert into suggested_rules table
        engine = _sa_create_engine(db_url, poolclass=NullPool)
        with engine.connect() as conn:
            for rule in rules[:3]:
                name = rule.get("name", "")
                rtype = rule.get("type", "business_rule")
                definition = rule.get("definition", "")
                if not name or not definition:
                    continue

                # Check for duplicates
                existing = conn.execute(text(
                    "SELECT 1 FROM public.dash_suggested_rules "
                    "WHERE project_slug = :slug AND definition = :def AND status = 'pending'"
                ), {"slug": project_slug, "def": definition}).fetchone()
                if existing:
                    continue

                conn.execute(text(
                    "INSERT INTO public.dash_suggested_rules (project_slug, name, type, definition, source_session_id) "
                    "VALUES (:slug, :name, :type, :def, :sid)"
                ), {"slug": project_slug, "name": name, "type": rtype, "def": definition, "sid": session_id})
            conn.commit()
    except Exception:
        pass
