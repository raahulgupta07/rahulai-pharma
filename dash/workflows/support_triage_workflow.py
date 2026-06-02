"""Support triage workflow.

Pattern: classify ticket priority/category → route to specialist agent →
escalate to Helpdesk if severity == 'critical'.

Schedule: on-demand.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any, Dict

from .ai_research_workflow import _llm, _record_run

logger = logging.getLogger(__name__)

WORKFLOW_META = {
    "name": "support_triage",
    "schedule": "manual",
    "description": "Classify ticket → route to specialist → escalate if critical.",
    "tags": ["support", "routing", "classify"],
}


_VALID_SEVERITY = {"critical", "high", "medium", "low"}
_VALID_CATEGORY = {
    "billing", "auth", "data", "performance", "outage",
    "feature_request", "bug", "ui", "integration", "other",
}


def _parse_classification(text: str) -> Dict[str, str]:
    """Robustly extract category, severity, summary from LLM output."""
    out = {"category": "other", "severity": "medium", "summary": ""}
    if not text:
        return out
    # Try JSON object substring
    try:
        m = re.search(r"\{[\s\S]*?\}", text)
        if m:
            obj = json.loads(m.group(0))
            cat = str(obj.get("category", "")).strip().lower()
            sev = str(obj.get("severity", "")).strip().lower()
            if cat in _VALID_CATEGORY:
                out["category"] = cat
            if sev in _VALID_SEVERITY:
                out["severity"] = sev
            if obj.get("summary"):
                out["summary"] = str(obj["summary"])[:500]
            return out
    except Exception:
        pass
    # Regex fallback
    msev = re.search(r"severity\s*[:=]\s*([a-z]+)", text, re.IGNORECASE)
    if msev and msev.group(1).lower() in _VALID_SEVERITY:
        out["severity"] = msev.group(1).lower()
    mcat = re.search(r"category\s*[:=]\s*([a-z_]+)", text, re.IGNORECASE)
    if mcat and mcat.group(1).lower() in _VALID_CATEGORY:
        out["category"] = mcat.group(1).lower()
    return out


_SPECIALIST_MAP = {
    "billing": "BillingSpecialist",
    "auth": "SecuritySpecialist",
    "data": "DataSpecialist",
    "performance": "PerformanceSpecialist",
    "outage": "OnCallSpecialist",
    "feature_request": "ProductSpecialist",
    "bug": "EngineeringSpecialist",
    "ui": "FrontendSpecialist",
    "integration": "IntegrationsSpecialist",
    "other": "GeneralistSpecialist",
}


async def _classify(ticket: Dict[str, Any]) -> Dict[str, str]:
    prompt = (
        "Classify the following support ticket. Respond as STRICT JSON with keys:\n"
        '{"category": <one of: ' + ",".join(sorted(_VALID_CATEGORY)) + '>, '
        '"severity": <one of: critical, high, medium, low>, '
        '"summary": <one-line summary>}\n\n'
        f"Ticket subject: {ticket.get('subject', '')}\n"
        f"Ticket body:\n{ticket.get('body', '')}\n"
        f"User tier: {ticket.get('user_tier', 'standard')}\n"
        f"Reported impact: {ticket.get('impact', 'unspecified')}\n"
    )
    text = await _llm(prompt, task="extraction")
    return _parse_classification(text)


async def _route_to_specialist(specialist: str, ticket: Dict[str, Any],
                               cls: Dict[str, str]) -> str:
    prompt = (
        f"You are {specialist}. A ticket has been routed to you.\n"
        f"Category: {cls['category']}   Severity: {cls['severity']}\n"
        f"Summary: {cls.get('summary', '')}\n\n"
        f"Subject: {ticket.get('subject', '')}\n"
        f"Body:\n{ticket.get('body', '')}\n\n"
        "Produce a triage response: (1) initial diagnosis, (2) immediate "
        "actions for the user, (3) next investigation steps, (4) ETA."
    )
    return await _llm(prompt, task="analysis")


async def _escalate_helpdesk(ticket: Dict[str, Any], cls: Dict[str, str],
                             specialist_response: str) -> str:
    prompt = (
        "ESCALATION to Helpdesk Lead. Severity is CRITICAL.\n"
        f"Ticket: {json.dumps(ticket, default=str)[:2000]}\n"
        f"Classification: {cls}\n"
        f"Specialist initial response:\n{specialist_response}\n\n"
        "Produce an escalation summary: incident-commander assignment, war-room "
        "agenda, customer-facing comms draft, and rollback options."
    )
    return await _llm(prompt, task="deep_analysis")


async def run_support_triage(ticket: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(ticket, dict):
        return {"ok": False, "error": "ticket must be a dict"}
    run_id = f"wfr2_{secrets.token_hex(4)}"
    args = {"ticket": ticket}
    _record_run(run_id, WORKFLOW_META["name"], args, status="running")
    t0 = time.time()

    try:
        cls = await _classify(ticket)
        specialist = _SPECIALIST_MAP.get(cls["category"], "GeneralistSpecialist")
        specialist_response = await _route_to_specialist(specialist, ticket, cls)

        escalation = None
        if cls["severity"] == "critical":
            escalation = await _escalate_helpdesk(ticket, cls, specialist_response)

        result = {
            "classification": cls,
            "routed_to": specialist,
            "specialist_response": specialist_response,
            "escalated": cls["severity"] == "critical",
            "escalation_summary": escalation,
            "elapsed_s": round(time.time() - t0, 2),
            "run_id": run_id,
        }
        _record_run(run_id, WORKFLOW_META["name"], args, status="done", result=result)
        return {"ok": True, "run_id": run_id, **result}
    except Exception as e:
        logger.exception("support_triage failed")
        _record_run(run_id, WORKFLOW_META["name"], args, status="failed", error=str(e))
        return {"ok": False, "run_id": run_id, "error": str(e)}
