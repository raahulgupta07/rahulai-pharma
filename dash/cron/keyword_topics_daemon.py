"""Keyword topic-cluster daemon — privacy-safe deep analytics (hybrid layer).

DEFAULT OFF — opt in with KEYWORD_TOPICS_ENABLED=1. Hard off: KEYWORD_TOPICS_DISABLED=1.
Leader-gated (spawned by the single daemon leader in app/main.py lifespan).

The /keywords endpoint already gives LIVE SQL term-frequency. This daemon adds the
optional LLM layer: periodically it samples recent question text IN MEMORY, asks a
small model to cluster it into a handful of human topics, and writes ONLY the
aggregate result (topic label + count + a few representative keywords) into
public.dash_keyword_topics. Raw question/answer text is NEVER stored or returned —
it is consumed during clustering and discarded. This keeps the manager policy intact
(dashboards show keyword/topic analysis only) while giving nicer named clusters than
the regex intent buckets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# How often the daemon wakes. The rollup itself covers a longer trailing window.
_TICK_SECONDS = int(os.getenv("KEYWORD_TOPICS_TICK_SECONDS", "3600"))  # hourly
_WINDOW_HOURS = int(os.getenv("KEYWORD_TOPICS_WINDOW_HOURS", "168"))   # trailing 7d
_SAMPLE_MAX = int(os.getenv("KEYWORD_TOPICS_SAMPLE_MAX", "400"))        # cap LLM input


def _enabled() -> bool:
    if os.getenv("KEYWORD_TOPICS_DISABLED") in ("1", "true", "TRUE", "yes"):
        return False
    return os.getenv("KEYWORD_TOPICS_ENABLED") in ("1", "true", "TRUE", "yes")


def _run_once() -> dict:
    """Sample → cluster via small LLM → store aggregates. Returns a small summary.
    Blocking; the loop runs it off the event loop via asyncio.to_thread."""
    from app.usage_api import _collect_questions  # reuse the multi-source collector
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=_WINDOW_HOURS)
    texts = _collect_questions(start, end)
    if not texts:
        return {"clustered": 0, "topics": 0, "reason": "no questions"}

    # cap the sample we hand the model (cost + context); keep newest-ish by slicing tail
    sample = texts[-_SAMPLE_MAX:] if len(texts) > _SAMPLE_MAX else texts
    numbered = "\n".join(f"{i+1}. {q[:160]}" for i, q in enumerate(sample))
    prompt = (
        "You are analysing customer questions to a pharmacy assistant. Group the "
        "questions below into 4-8 clear TOPIC clusters (e.g. 'stock availability', "
        "'drug substitutes', 'pricing', 'dosage & usage', 'sales analytics'). "
        "Return STRICT JSON only: a list of objects with keys "
        '"topic" (short label), "count" (how many questions fit), and "keywords" '
        "(3-6 representative words, no full sentences). Do NOT echo any question "
        "text verbatim.\n\nQUESTIONS:\n" + numbered
    )

    try:
        from app.catalog_enrich import _call_openrouter
        from dash.settings import TRAINING_MODEL
        model = TRAINING_MODEL or os.getenv("TRAINING_MODEL", "gemini-3-flash-preview")
    except Exception:
        from app.catalog_enrich import _call_openrouter
        model = os.getenv("TRAINING_MODEL", "gemini-3-flash-preview")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"clustered": 0, "topics": 0, "reason": "no api key"}

    raw = _call_openrouter(prompt, model, api_key, timeout=90.0)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[txt.find("["):] if "[" in txt else txt
    try:
        clusters = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
    except Exception as e:
        logger.warning("keyword_topics: bad LLM JSON: %s", e)
        return {"clustered": 0, "topics": 0, "reason": "parse error"}

    total = sum(int(c.get("count") or 0) for c in clusters) or len(sample)
    from db.session import get_write_engine
    from sqlalchemy import text as _sql
    eng = get_write_engine()
    stored = 0
    with eng.begin() as conn:
        # one snapshot per window — clear any prior rows for this exact window
        conn.execute(_sql("DELETE FROM public.dash_keyword_topics WHERE window_end = :e"),
                     {"e": end})
        for c in clusters:
            label = str(c.get("topic") or "").strip()[:120]
            if not label:
                continue
            cnt = int(c.get("count") or 0)
            kws = c.get("keywords") or []
            if isinstance(kws, str):
                kws = [kws]
            conn.execute(_sql(
                "INSERT INTO public.dash_keyword_topics "
                "(window_start, window_end, topic, count, pct, keywords) "
                "VALUES (:ws, :we, :t, :c, :p, :k)"),
                {"ws": start, "we": end, "t": label, "c": cnt,
                 "p": round(100.0 * cnt / total, 1) if total else 0,
                 "k": json.dumps([str(x)[:40] for x in kws][:8])})
            stored += 1
    return {"clustered": len(sample), "topics": stored}


async def keyword_topics_loop(tick_seconds: int | None = None) -> None:
    tick = tick_seconds or _TICK_SECONDS
    logger.info("keyword_topics daemon started (tick=%ss)", tick)
    while True:
        try:
            if _enabled():
                res = await asyncio.to_thread(_run_once)
                if res.get("topics"):
                    logger.info("keyword_topics: %s topics from %s questions",
                                res.get("topics"), res.get("clustered"))
        except Exception:
            logger.exception("keyword_topics loop tick failed")
        await asyncio.sleep(tick)
