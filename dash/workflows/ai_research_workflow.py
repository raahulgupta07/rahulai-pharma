"""AI Research workflow.

Pattern: N parallel researchers (``asyncio.gather``) on a user-provided
topic → DEEP synthesis.

Schedule: daily 07:00 UTC (cron ``0 7 * * *``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

WORKFLOW_META = {
    "name": "ai_research",
    "schedule": "0 7 * * *",
    "description": "Daily multi-researcher synthesis on a topic via parallel LLM calls + DEEP synth.",
    "tags": ["research", "parallel", "deep"],
}


# ── Engine + run state helpers ─────────────────────────────────────────
def _engine():
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _record_run(run_id: str, workflow_name: str, args: Dict[str, Any],
                status: str, result: Dict[str, Any] | None = None,
                error: str | None = None) -> None:
    """Best-effort store of run state. Tries dash_workflow_runs_v2,
    falls back to dash_audit_log."""
    from sqlalchemy import text
    eng = _engine()
    if eng is None:
        return
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.dash_workflow_runs_v2
                      (run_id, workflow_name, project_slug, status,
                       input_args, result, error, finished_at)
                    VALUES
                      (:rid, :wf, :ps, :st,
                       CAST(:ia AS jsonb),
                       CAST(:rs AS jsonb),
                       :err, CASE WHEN :st IN ('done','failed','cancelled')
                                  THEN now() ELSE NULL END)
                    ON CONFLICT (run_id) DO UPDATE
                      SET status = EXCLUDED.status,
                          result = COALESCE(EXCLUDED.result, public.dash_workflow_runs_v2.result),
                          error  = COALESCE(EXCLUDED.error,  public.dash_workflow_runs_v2.error),
                          finished_at = EXCLUDED.finished_at
                    """
                ),
                {
                    "rid": run_id,
                    "wf": workflow_name,
                    "ps": args.get("project_slug"),
                    "st": status,
                    "ia": json.dumps(args, default=str),
                    "rs": json.dumps(result, default=str) if result is not None else None,
                    "err": error,
                },
            )
            return
    except Exception:
        pass
    # Fallback: audit log
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.dash_audit_log
                      (user_id, action, target_type, target_id, metadata)
                    VALUES
                      (NULL, :act, 'workflow_run', :rid, CAST(:md AS jsonb))
                    """
                ),
                {
                    "act": f"workflow.{workflow_name}.{status}",
                    "rid": run_id,
                    "md": json.dumps({"args": args, "result": result, "error": error}, default=str),
                },
            )
    except Exception:
        logger.debug("workflow run state persist skipped", exc_info=True)


# ── LLM helpers ────────────────────────────────────────────────────────
async def _llm(prompt: str, task: str) -> str:
    try:
        from dash.settings import training_llm_call  # type: ignore
        out = await asyncio.to_thread(training_llm_call, prompt, task)
        return out or ""
    except Exception as e:
        logger.exception("ai_research: LLM call failed")
        return f"[llm_error: {e}]"


async def _researcher(topic: str, angle: str, idx: int) -> Dict[str, Any]:
    """One parallel researcher. Reuses dash.agents.researcher if present
    (best-effort), else uses training_llm_call directly."""
    prompt = (
        f"You are Researcher #{idx} investigating the topic: '{topic}'.\n"
        f"Focus angle: {angle}\n\n"
        "Produce 5-8 bullet findings with concrete facts, sources where possible, "
        "and a 1-line key insight. Be terse and specific."
    )
    # Try to leverage the existing researcher agent if importable.
    try:
        from dash.agents import researcher as _r  # type: ignore  # noqa: F401
        # We don't have a project context here; fall through to LLM call.
    except Exception:
        pass
    content = await _llm(prompt, task="analysis")
    return {"angle": angle, "idx": idx, "content": content}


_ANGLES = [
    "current state and definitions",
    "leading practitioners and tools",
    "risks, controversies, failure modes",
    "future outlook and emerging directions",
    "quantitative metrics and benchmarks",
    "case studies and real-world deployments",
]


# ── Public entry ───────────────────────────────────────────────────────
async def run_ai_research(topic: str, num_researchers: int = 4) -> Dict[str, Any]:
    """Run N parallel researchers on `topic`, then DEEP-synthesize."""
    n = max(1, min(8, int(num_researchers)))
    run_id = f"wfr2_{secrets.token_hex(4)}"
    args = {"topic": topic, "num_researchers": n}
    _record_run(run_id, WORKFLOW_META["name"], args, status="running")
    t0 = time.time()

    angles = _ANGLES[:n] if n <= len(_ANGLES) else _ANGLES + [
        f"additional angle {i}" for i in range(n - len(_ANGLES))
    ]
    try:
        tasks = [_researcher(topic, angles[i], i + 1) for i in range(n)]
        findings: List[Dict[str, Any]] = await asyncio.gather(*tasks)

        synth_prompt = (
            f"You are the lead synthesizer. Topic: '{topic}'.\n"
            f"Below are {n} parallel researcher findings. Produce:\n"
            "1) TL;DR (3 lines)\n"
            "2) Top 5 cross-cutting insights\n"
            "3) Disagreements / open questions\n"
            "4) Recommended next actions\n\n"
            "---\n"
            + "\n\n---\n\n".join(
                f"## Researcher {f['idx']} — {f['angle']}\n{f['content']}"
                for f in findings
            )
        )
        synthesis = await _llm(synth_prompt, task="deep_analysis")

        result = {
            "topic": topic,
            "num_researchers": n,
            "findings": findings,
            "synthesis": synthesis,
            "elapsed_s": round(time.time() - t0, 2),
            "run_id": run_id,
        }
        _record_run(run_id, WORKFLOW_META["name"], args, status="done", result=result)
        return {"ok": True, "run_id": run_id, **result}
    except Exception as e:
        logger.exception("ai_research failed")
        _record_run(run_id, WORKFLOW_META["name"], args, status="failed", error=str(e))
        return {"ok": False, "run_id": run_id, "error": str(e)}
