"""End-of-cycle digest — 1-paragraph "today we learned" summary.

kpt's "run-then-review log" pattern. Synthesizes verified hypotheses
+ key consolidations + agent_iq delta into human-readable insight.

Optional Slack/email webhook integration via SLACK_LEARNING_WEBHOOK env.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Digest:
    project_slug: Optional[str]
    cycle_num: int
    run_id: Optional[int]
    summary: str = ""
    highlights: list[dict] = field(default_factory=list)
    hypotheses_count: int = 0
    verified_count: int = 0
    cost_usd: float = 0.0
    agent_iq: Optional[float] = None
    notified_via: list[str] = field(default_factory=list)


def generate(
    project_slug: Optional[str],
    cycle_num: int,
    run_id: Optional[int],
    *,
    llm_call_fn: Optional[Callable] = None,
    dash_engine=None,
) -> Digest:
    """Build digest at end of cycle."""
    digest = Digest(project_slug=project_slug, cycle_num=cycle_num, run_id=run_id)

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()

        with eng.connect() as conn:
            # Pull verified hypotheses from this cycle
            rows = conn.execute(text(
                "SELECT id, statement, hypothesis_type, confidence "
                "FROM public.dash_hypotheses "
                "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                "  AND verification_status = 'verified' "
                "  AND created_at > NOW() - INTERVAL '1 day' "
                "ORDER BY confidence DESC LIMIT 10"
            ), {"s": project_slug}).fetchall()

            digest.highlights = [{
                "id": r[0],
                "statement": (r[1] or "")[:300],
                "type": r[2],
                "confidence": float(r[3] or 0),
            } for r in rows]
            digest.verified_count = len(rows)

            # Get cycle stats
            if run_id:
                r2 = conn.execute(text(
                    "SELECT hypotheses_formed, hypotheses_verified, "
                    " cost_usd, metadata "
                    "FROM public.dash_self_learning_runs WHERE id = :id"
                ), {"id": run_id}).fetchone()
                if r2:
                    digest.hypotheses_count = int(r2[0] or 0)
                    digest.verified_count = int(r2[1] or 0)
                    digest.cost_usd = float(r2[2] or 0)
                    md = r2[3] or {}
                    iq_data = (md or {}).get("agent_iq", {}).get("components", {})
                    iq_val = iq_data.get("agent_iq")
                    if iq_val is not None:
                        try:
                            digest.agent_iq = float(iq_val)
                        except Exception:
                            digest.agent_iq = None

        # LLM synthesis
        if digest.highlights and llm_call_fn:
            digest.summary = _llm_summary(digest, llm_call_fn)
        else:
            digest.summary = _fallback_summary(digest)

        # Persist
        _persist(digest, eng)

        # Notify
        _notify_slack(digest)

    except Exception as e:
        logger.warning(f"digest generate failed: {e}")

    return digest


def _llm_summary(d: Digest, llm_call_fn) -> str:
    findings = "\n".join(
        f"- {h['type']} (conf {h['confidence']:.2f}): {h['statement']}"
        for h in d.highlights[:8]
    )
    iq_str = f"{d.agent_iq:.0f}" if d.agent_iq is not None else "n/a"
    prompt = f"""Today's self-learning cycle for project '{d.project_slug or 'central'}'
generated {d.hypotheses_count} hypotheses, {d.verified_count} verified.

Top verified findings:
{findings}

Cost: ${d.cost_usd:.3f}
Agent IQ: {iq_str}

Write a 100-word summary in first-person plural ("Today we learned...").
Group findings by theme. End with one sentence on what we'll explore next.
Be specific, no fluff.
"""
    try:
        ans = llm_call_fn(prompt, task='deep_analysis')
        return (ans or "").strip()[:1500] or _fallback_summary(d)
    except Exception:
        return _fallback_summary(d)


def _fallback_summary(d: Digest) -> str:
    if d.verified_count == 0:
        return f"No new findings this cycle. Cost: ${d.cost_usd:.3f}."
    return (
        f"Today we verified {d.verified_count} hypotheses out of "
        f"{d.hypotheses_count}. Top finding: "
        f"\"{d.highlights[0]['statement']}\" "
        f"(confidence {d.highlights[0]['confidence']:.2f}). "
        f"Cost: ${d.cost_usd:.3f}."
    )


def _persist(d: Digest, engine) -> None:
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_digests "
                "(project_slug, cycle_num, run_id, summary, highlights, "
                " hypotheses_count, verified_count, cost_usd, agent_iq, "
                " notified_via) "
                "VALUES (:s, :n, :r, :sum, :h, :hc, :vc, :c, :iq, :nv)"
            ), {
                "s": d.project_slug,
                "n": d.cycle_num,
                "r": d.run_id,
                "sum": d.summary,
                "h": json.dumps(d.highlights),
                "hc": d.hypotheses_count,
                "vc": d.verified_count,
                "c": d.cost_usd,
                "iq": d.agent_iq,
                "nv": json.dumps(d.notified_via),
            })
            conn.commit()
    except Exception as e:
        logger.warning(f"digest persist failed: {e}")


def _notify_slack(d: Digest) -> None:
    """Optional Slack webhook ping."""
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_digest_slack",
                            project_slug=getattr(d, "project_slug", None)):
            return
    except Exception:
        pass
    webhook = os.environ.get("SLACK_LEARNING_WEBHOOK")
    if not webhook or not d.summary:
        return
    try:
        import requests
        text = f":bulb: *Self-learning digest — {d.project_slug or 'central'}*\n{d.summary}"
        r = requests.post(webhook, json={"text": text}, timeout=10)
        if r.status_code in (200, 204):
            d.notified_via.append("slack")
    except Exception as e:
        logger.debug(f"slack notify failed: {e}")


def list_recent(
    project_slug: Optional[str],
    limit: int = 20,
    dash_engine=None,
) -> list[dict]:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, cycle_num, summary, hypotheses_count, "
                " verified_count, cost_usd, agent_iq, created_at "
                "FROM public.dash_digests "
                "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                "ORDER BY created_at DESC LIMIT :n"
            ), {"s": project_slug, "n": limit}).fetchall()
        return [{
            "id": r[0],
            "cycle_num": r[1],
            "summary": r[2],
            "hypotheses_count": r[3],
            "verified_count": r[4],
            "cost_usd": float(r[5] or 0),
            "agent_iq": float(r[6]) if r[6] is not None else None,
            "ts": r[7].isoformat() if r[7] else None,
        } for r in rows]
    except Exception as e:
        logger.warning(f"list_recent failed: {e}")
        return []
