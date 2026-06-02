"""SkillRefiner — LLM that proposes tool description/arg patches.

Phase 4: takes a tool's current state + failure samples and returns a JSON
patch (new_description, default_args, reason). Caller stores draft in
dash_tool_patches with applied=false until human review.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

REFINER_PROMPT = """You are SkillRefiner — an agent that improves agent tools by rewriting their descriptions and tuning default arguments.

A tool in the Dash data agent platform is failing. Analyze the failure pattern and propose a patch.

## Tool
Name: {tool_name}
Current description: {old_description}
Score: {score}/100  (success_rate={success_rate}%, calls={calls}, fails={fails}, p50={p50}ms)

## Recent failures
{failures}

## Project context
{project_context}

## Your job
Output ONE JSON object with these exact keys:
- new_description: (string) — sharpened tool description. Keep it concise. State when to use, when NOT to use, and an example. If old description is fine, repeat it unchanged.
- default_args: (object) — JSON object of default argument values to merge into every call. Use {{}} if no defaults needed.
- reason: (string, max 200 chars) — explain WHY this patch will fix the failures.

Output ONLY the JSON. No markdown fences, no commentary.
"""


def propose_patch(tool_name: str,
                  old_description: str,
                  score: float,
                  success_rate: float,
                  calls: int,
                  fails: int,
                  p50_ms: int,
                  failures: list[dict],
                  project_context: str = "") -> dict[str, Any]:
    """Call DEEP_MODEL to draft a patch. Returns dict with new_description/default_args/reason.

    Raises ValueError if LLM output is unparseable.
    """
    from dash.settings import training_llm_call

    fails_text = "\n".join(
        f"- [{f.get('error_class','Error')}] {f.get('error_message','')[:200]}"
        for f in (failures or [])[:10]
    ) or "(no failure samples available)"

    prompt = REFINER_PROMPT.format(
        tool_name=tool_name,
        old_description=(old_description or "(empty)")[:1500],
        score=round(float(score), 1),
        success_rate=round(float(success_rate), 1),
        calls=calls,
        fails=fails,
        p50=p50_ms,
        failures=fails_text,
        project_context=project_context[:500] or "(none)",
    )

    raw = training_llm_call(prompt, task="deep_analysis")
    if not raw:
        raise ValueError("LLM returned empty response")

    text = raw.strip()
    # Strip code fences if present.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"could not extract JSON from: {raw[:200]}")
        data = json.loads(m.group(0))

    new_desc = (data.get("new_description") or "").strip()
    default_args = data.get("default_args") or {}
    reason = (data.get("reason") or "").strip()[:500]

    if not new_desc:
        raise ValueError("LLM omitted new_description")
    if not isinstance(default_args, dict):
        default_args = {}

    return {
        "new_description": new_desc,
        "default_args": default_args,
        "reason": reason,
    }


SHADOW_PROMPT = """You are SkillJudge — predict whether a proposed tool patch will fix recent failures.

## Tool: {tool_name}

## OLD description
{old_description}

## NEW description (proposed)
{new_description}

## Default args (proposed)
{default_args}

## Recent failures (the patch should fix these)
{failures}

## Recent successes (the patch should NOT break these)
{successes}

## Job
For each failure, predict: would NEW description+args have avoided it? (pass=true/false)
For each success, predict: would NEW still succeed? (pass=true/false)

Output ONLY this JSON (no fences):
{{"verdicts": [{{"sample": "fail-1", "pass": true|false, "why": "short"}}, ...],
  "pass_rate": <0..100 integer>,
  "summary": "1-line"}}
"""


def shadow_validate(tool_name: str,
                    old_description: str,
                    new_description: str,
                    default_args: dict,
                    failures: list[dict],
                    successes: list[dict]) -> dict:
    """LLM-as-judge prediction of patch effectiveness on past samples."""
    from dash.settings import training_llm_call

    fails_text = "\n".join(
        f"- fail-{i+1}: [{f.get('error_class','Error')}] {(f.get('error_message') or '')[:200]} (args={f.get('args_hash','?')})"
        for i, f in enumerate((failures or [])[:5])
    ) or "(none)"

    succ_text = "\n".join(
        f"- ok-{i+1}: latency={s.get('latency_ms',0)}ms (args={s.get('args_hash','?')})"
        for i, s in enumerate((successes or [])[:3])
    ) or "(none)"

    prompt = SHADOW_PROMPT.format(
        tool_name=tool_name,
        old_description=(old_description or "(empty)")[:1000],
        new_description=new_description[:1000],
        default_args=json.dumps(default_args or {}),
        failures=fails_text,
        successes=succ_text,
    )

    raw = training_llm_call(prompt, task="deep_analysis")
    if not raw:
        raise ValueError("shadow LLM returned empty")

    text_ = raw.strip()
    text_ = re.sub(r"^```(?:json)?\s*", "", text_)
    text_ = re.sub(r"\s*```$", "", text_)
    try:
        data = json.loads(text_)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text_, re.DOTALL)
        if not m:
            raise ValueError(f"shadow: unparsable: {raw[:200]}")
        data = json.loads(m.group(0))

    pr = data.get("pass_rate")
    try:
        pr = max(0, min(100, int(pr)))
    except Exception:
        pr = 0

    return {
        "pass_rate": pr,
        "verdicts": data.get("verdicts") or [],
        "summary": (data.get("summary") or "")[:300],
    }
