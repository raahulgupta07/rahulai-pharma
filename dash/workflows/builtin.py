"""5 reference workflows ported from demo-os patterns.

Registered via register_builtins() — called from app/workflows_api.py on import.
Idempotent: upsert by builtin slug id.
"""
from __future__ import annotations

import json as _json
import logging

logger = logging.getLogger(__name__)


BUILTINS = [
    {
        "id": "wf_morning_brief",
        "name": "Morning Brief",
        "description": "Parallel gather (KPI changes + new docs + drift alerts) → Synthesizer → email/Slack.",
        "category": "ops",
        "trigger_kind": "cron",
        "cron_expr": "0 8 * * 1-5",
        "spec": {
            "steps": [
                {"id": "kpi_delta", "kind": "agent", "agent": "Analyst",
                 "prompt": "Summarize top 5 KPI deltas since yesterday for project {project_slug}.",
                 "parallel_group": "gather"},
                {"id": "new_docs", "kind": "agent", "agent": "Researcher",
                 "prompt": "List documents uploaded since yesterday w/ 1-line summary each.",
                 "parallel_group": "gather"},
                {"id": "drift", "kind": "agent", "agent": "Analyst",
                 "prompt": "Any drift alerts open since last brief? Summarize severity + table.",
                 "parallel_group": "gather"},
                {"id": "synthesize", "kind": "agent", "agent": "Leader",
                 "depends_on": ["kpi_delta", "new_docs", "drift"],
                 "prompt": "Write 5-bullet morning brief. KPIs: {kpi_delta}\nDocs: {new_docs}\nDrift: {drift}"},
            ],
            "outputs": ["synthesize"],
        },
    },
    {
        "id": "wf_daily_research",
        "name": "Daily Research",
        "description": "4 parallel Researcher forks on curiosity questions → Hypothesis → Verifier → Brain promotion.",
        "category": "research",
        "trigger_kind": "cron",
        "cron_expr": "0 7 * * *",
        "spec": {
            "steps": [
                {"id": "q1", "kind": "agent", "agent": "Researcher",
                 "prompt": "Research question 1 from curiosity engine. Compress findings.",
                 "parallel_group": "research"},
                {"id": "q2", "kind": "agent", "agent": "Researcher",
                 "prompt": "Research question 2.", "parallel_group": "research"},
                {"id": "q3", "kind": "agent", "agent": "Researcher",
                 "prompt": "Research question 3.", "parallel_group": "research"},
                {"id": "q4", "kind": "agent", "agent": "Researcher",
                 "prompt": "Research question 4.", "parallel_group": "research"},
                {"id": "hypothesize", "kind": "agent", "agent": "Leader",
                 "depends_on": ["q1", "q2", "q3", "q4"],
                 "prompt": "Form hypotheses from: {q1} {q2} {q3} {q4}"},
                {"id": "verify", "kind": "agent", "agent": "Analyst",
                 "depends_on": ["hypothesize"],
                 "prompt": "Verify hypotheses against live data: {hypothesize}"},
                {"id": "promote_gate", "kind": "hitl", "hitl_action": "confirmation",
                 "depends_on": ["verify"]},
                {"id": "promote", "kind": "agent", "agent": "Leader",
                 "depends_on": ["promote_gate"],
                 "prompt": "Promote verified facts to Company Brain. Facts: {verify}"},
            ],
            "outputs": ["promote"],
        },
    },
    {
        "id": "wf_content_pipeline",
        "name": "Content Pipeline",
        "description": "Research + outline → Draft/Review loop (max 3 iter) → publish dashboard.",
        "category": "content",
        "trigger_kind": "manual",
        "spec": {
            "steps": [
                {"id": "research", "kind": "agent", "agent": "Researcher",
                 "prompt": "Research topic: {topic}"},
                {"id": "outline", "kind": "agent", "agent": "Leader",
                 "depends_on": ["research"],
                 "prompt": "Outline article from research: {research}"},
                {"id": "draft", "kind": "agent", "agent": "Leader",
                 "depends_on": ["outline"],
                 "prompt": "Draft article from outline: {outline}"},
                {"id": "review", "kind": "agent", "agent": "Leader",
                 "depends_on": ["draft"],
                 "prompt": "Review draft. Return JSON {approved: bool, feedback: str}. Draft: {draft}",
                 "on_error": "continue"},
                {"id": "publish", "kind": "tool", "tool": "make_pdf",
                 "depends_on": ["review"],
                 "args": {"title": "{topic}", "sections": [{"heading": "Article", "body": "{draft}"}]}},
            ],
            "outputs": ["publish"],
        },
    },
    {
        "id": "wf_doc_walkthrough",
        "name": "Document Walkthrough",
        "description": "Parse uploaded doc → script → assemble written walkthrough.",
        "category": "content",
        "trigger_kind": "manual",
        "spec": {
            "steps": [
                {"id": "parse", "kind": "agent", "agent": "Researcher",
                 "prompt": "Extract section structure of document: {doc_id}"},
                {"id": "script", "kind": "agent", "agent": "Leader",
                 "depends_on": ["parse"],
                 "prompt": "Write narration script per section. Sections: {parse}"},
                {"id": "assemble", "kind": "tool", "tool": "make_md",
                 "depends_on": ["script"],
                 "args": {"title": "Walkthrough", "body": "{script}"}},
            ],
            "outputs": ["assemble"],
        },
    },
    {
        "id": "wf_support_triage",
        "name": "Support Triage",
        "description": "Classify ticket → route by severity → escalate (HITL) if critical.",
        "category": "support",
        "trigger_kind": "event",
        "spec": {
            "steps": [
                {"id": "classify", "kind": "agent", "agent": "Leader",
                 "prompt": "Classify ticket. Return JSON {severity: critical|high|low, category: str}. Ticket: {ticket_text}"},
                {"id": "router", "kind": "router", "depends_on": ["classify"],
                 "route_by": "classify['severity'] if isinstance(classify, dict) else 'low'",
                 "branches": {
                     "critical": ["escalate_gate", "escalate"],
                     "high": ["assign_specialist"],
                     "low": ["auto_reply"],
                 }},
                {"id": "escalate_gate", "kind": "hitl", "hitl_action": "confirmation",
                 "depends_on": ["router"]},
                {"id": "escalate", "kind": "agent", "agent": "Leader",
                 "depends_on": ["escalate_gate"],
                 "prompt": "Page on-call. Ticket: {ticket_text}"},
                {"id": "assign_specialist", "kind": "agent", "agent": "Leader",
                 "depends_on": ["router"],
                 "prompt": "Route to specialist. Category: {classify}"},
                {"id": "auto_reply", "kind": "agent", "agent": "Leader",
                 "depends_on": ["router"],
                 "prompt": "Draft auto-reply for low-severity ticket: {ticket_text}"},
            ],
            "outputs": ["escalate", "assign_specialist", "auto_reply"],
        },
    },
]


def register_builtins() -> int:
    """Idempotent upsert of all builtin workflow defs. Returns count."""
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            eng = get_sql_engine()
        except Exception:
            return 0
    if eng is None:
        return 0
    try:
        from sqlalchemy import text
        count = 0
        with eng.begin() as conn:
            for b in BUILTINS:
                conn.execute(
                    text(
                        """
                        INSERT INTO dash.dash_workflow_defs
                          (id, name, description, category, spec, is_builtin,
                           trigger_kind, cron_expr)
                        VALUES (:id, :nm, :ds, :cat, CAST(:sp AS jsonb), true,
                                :tk, :ce)
                        ON CONFLICT (id) DO UPDATE
                          SET spec = EXCLUDED.spec,
                              name = EXCLUDED.name,
                              description = EXCLUDED.description,
                              trigger_kind = EXCLUDED.trigger_kind,
                              cron_expr = EXCLUDED.cron_expr,
                              updated_at = now()
                        """
                    ),
                    {
                        "id": b["id"], "nm": b["name"], "ds": b.get("description"),
                        "cat": b.get("category"), "sp": _json.dumps(b["spec"]),
                        "tk": b.get("trigger_kind", "manual"), "ce": b.get("cron_expr"),
                    },
                )
                count += 1
        return count
    except Exception as e:
        logger.warning("register_builtins failed: %s", e)
        return 0
