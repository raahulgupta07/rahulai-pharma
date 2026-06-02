from dash.settings import TRAINING_MODEL
"""
Proactive Insights
==================

After each chat, detects anomalies in the data and surfaces them as alerts.
Runs as a background task — does not block the chat response.
"""

import json
import re
from os import getenv

import httpx
from sqlalchemy import text

from db import get_sql_engine


def generate_proactive_insights(project_slug: str, question: str, answer: str, user_id: int | None = None):
    """Analyze chat response for anomalies and generate proactive insights."""
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    # Only analyze responses that contain numeric data
    numbers = re.findall(r'[\$]?\d[\d,]*\.?\d*[%]?', answer)
    if len(numbers) < 2:
        return  # Not enough numeric data to find anomalies

    # Extract table names mentioned in the response
    table_matches = re.findall(r'`?(\w+)`?\s+table|FROM\s+`?(\w+)`?|JOIN\s+`?(\w+)`?', answer, re.IGNORECASE)
    tables = list(set(t for match in table_matches for t in match if t))

    prompt = f"""You are a data anomaly detector. Analyze this Q&A exchange and identify any surprising or noteworthy patterns in the data.

QUESTION: {question}

RESPONSE: {answer[:3000]}

Look for:
1. Unusually high or low values compared to what's typical
2. Unexpected drops or spikes (e.g., "revenue dropped 34%")
3. Data quality concerns (many NULLs, zeros, suspicious patterns)
4. Counter-intuitive findings worth investigating

If you find 1-3 genuine anomalies or interesting patterns, return them. If the data looks normal, return an empty array.

Respond with ONLY valid JSON (no markdown):
{{"insights": [
  {{"insight": "Revenue dropped 34% compared to last month, driven by a 50% decline in Enterprise segment", "severity": "warning", "tables": ["revenue", "segments"]}},
  {{"insight": "Top 3 customers account for 78% of total revenue, creating concentration risk", "severity": "info", "tables": ["customers", "orders"]}}
]}}

If nothing noteworthy, respond: {{"insights": []}}"""

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
        parsed = json.loads(content.strip().strip("`").strip())
        insights = parsed.get("insights", [])

        if not insights:
            return

        engine = get_sql_engine()
        with engine.connect() as conn:
            for ins in insights[:3]:  # Cap at 3 per response
                conn.execute(text(
                    "INSERT INTO public.dash_proactive_insights "
                    "(project_slug, user_id, insight, severity, tables_involved) "
                    "VALUES (:slug, :uid, :insight, :severity, :tables)"
                ), {
                    "slug": project_slug,
                    "uid": user_id,
                    "insight": ins.get("insight", ""),
                    "severity": ins.get("severity", "info"),
                    "tables": ins.get("tables", []),
                })
            conn.commit()
    except Exception:
        pass
