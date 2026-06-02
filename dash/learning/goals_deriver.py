"""Auto-generate learning_goals.md per project from training signals.

Mirrors scope_deriver pattern. Called from TRAIN ALL pipeline + manual
re-derive endpoint. LLM picks priorities/deprioritized topics/success
criteria/constraints based on persona + tables + docs + KG + bad feedback.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from dash.learning.goals import goals_path, read_goals, write_goals

logger = logging.getLogger(__name__)

_MAX_SIGNAL_CHARS = 3000


def _collect(project_slug: str) -> dict[str, Any]:
    from dash.tools.skill_refinery import _get_engine

    sig: dict[str, Any] = {
        "persona": {},
        "tables": [],
        "docs": [],
        "kg_entities": [],
        "bad_feedback": [],
        "low_evals": [],
    }
    eng = _get_engine()

    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT agent_name, agent_role, agent_personality FROM public.dash_projects WHERE slug=:s"
            ), {"s": project_slug}).fetchone()
            if row:
                sig["persona"] = {"name": row[0] or "", "role": row[1] or "", "style": row[2] or ""}
    except Exception as e:
        logger.debug(f"persona fetch failed: {e}")

    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT table_name, purpose FROM dash_table_metadata
                WHERE project_slug=:s LIMIT 30
            """), {"s": project_slug}).fetchall()
            sig["tables"] = [{"t": r[0], "p": (r[1] or "")[:120]} for r in rows]
    except Exception as e:
        logger.debug(f"tables fetch failed: {e}")

    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT filename FROM dash_documents WHERE project_slug=:s LIMIT 20
            """), {"s": project_slug}).fetchall()
            sig["docs"] = [r[0] for r in rows]
    except Exception as e:
        logger.debug(f"docs fetch failed: {e}")

    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT subject, COUNT(*) c FROM dash_knowledge_triples
                WHERE project_slug=:s GROUP BY subject ORDER BY c DESC LIMIT 20
            """), {"s": project_slug}).fetchall()
            sig["kg_entities"] = [r[0] for r in rows]
    except Exception as e:
        logger.debug(f"kg fetch failed: {e}")

    try:
        with eng.connect() as conn:
            rows = conn.execute(text("""
                SELECT question FROM dash_feedback
                WHERE project_slug=:s AND rating='bad'
                ORDER BY created_at DESC LIMIT 10
            """), {"s": project_slug}).fetchall()
            sig["bad_feedback"] = [r[0][:200] for r in rows if r[0]]
    except Exception as e:
        logger.debug(f"feedback fetch failed: {e}")

    return sig


def _build_prompt(slug: str, sig: dict) -> str:
    persona = sig["persona"]
    tables_str = ", ".join([f"{t['t']} ({t['p']})" for t in sig["tables"][:15]])
    docs_str = ", ".join(sig["docs"][:15])
    kg_str = ", ".join(sig["kg_entities"][:15])
    bad_str = "\n".join([f"- {q}" for q in sig["bad_feedback"][:8]]) or "(none)"

    body = f"""PROJECT: {slug}
PERSONA: name={persona.get('name')} role={persona.get('role')} style={persona.get('style')}
TABLES: {tables_str}
DOCS: {docs_str}
TOP KG ENTITIES: {kg_str}
KNOWN GAPS (bad feedback questions agent failed):
{bad_str}
"""
    if len(body) > _MAX_SIGNAL_CHARS:
        body = body[:_MAX_SIGNAL_CHARS]

    return f"""You are configuring an autonomous learning loop for a data agent.
Output ONLY valid markdown for `learning_goals.md`. No preamble, no fences.

Required sections:
## What I want this agent to learn
(2-3 sentence summary of mission given the persona/tables/docs)

## Topics to prioritize
(5-8 specific bullets — base on tables, KG entities, persona, gaps. Concrete, not generic.)

## Topics to deprioritize / skip
(3-5 bullets — outside the agent's domain or already covered)

## Success criteria
(3 measurable bullets — e.g. "answer 90% of inventory questions correctly")

## Constraints
(3-5 bullets — PII, domain bounds, refuse-list. Always include "Avoid generating questions about PII".)

SIGNALS:
{body}
"""


def derive_goals(project_slug: str, force: bool = False) -> dict:
    """LLM-derive learning_goals.md. Skip if user already customized (unless force).

    Returns {ok, path, content, derived: bool, reason}.
    """
    p = goals_path(project_slug)
    current = read_goals(project_slug)
    is_template = "Replace this with bullet points" in current and "(e.g." in current

    if not force and not is_template:
        return {
            "ok": True, "derived": False, "path": str(p),
            "content": current,
            "reason": "user-edited file present; use force=true to overwrite",
        }

    sig = _collect(project_slug)
    prompt = _build_prompt(project_slug, sig)

    try:
        from dash.settings import training_llm_call
        raw = training_llm_call(prompt, task="deep_analysis")
    except Exception as e:
        logger.warning(f"goals LLM call failed: {e}")
        return {"ok": False, "derived": False, "reason": f"llm error: {e}", "content": current}

    md = (raw or "").strip()
    if md.startswith("```"):
        md = md.strip("`").lstrip("markdown").strip()
    if not md or "## " not in md:
        return {"ok": False, "derived": False, "reason": "empty/invalid LLM output", "content": current}

    md = f"<!-- auto-derived: true -->\n{md}\n"
    write_goals(project_slug, md)
    return {"ok": True, "derived": True, "path": str(p), "content": md, "signals_used": list(sig.keys())}
