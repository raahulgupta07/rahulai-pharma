"""Correction-learning loop.

Capture user edits to agent output, extract durable rules via LLM, save to
project rules store, inject into next agent runs.

Public API:
    record_correction(project, run_id, agent_name, original, edited, user)
    extract_rule_from_diff(correction_id) -> rule_text | None
    save_rule(project, scope, scope_target, rule_text, source_correction_id)
    get_active_rules(project, agent_name=None, skill_name=None) -> list[str]
    build_rules_prompt_block(project, agent_name) -> str
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _engine():
    """Lazy engine accessor matching app/hitl_api.py pattern."""
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            from db import db_url  # type: ignore
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            return create_engine(db_url, poolclass=NullPool)


def _diff_summary(original: str, edited: str, max_lines: int = 60) -> str:
    """Generate a compact unified diff summary."""
    o = (original or "").splitlines()
    e = (edited or "").splitlines()
    diff = difflib.unified_diff(o, e, fromfile="original", tofile="edited", lineterm="", n=2)
    lines = list(diff)
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines truncated)"]
    return "\n".join(lines)


def record_correction(
    project: Optional[str],
    run_id: Optional[str],
    agent_name: Optional[str],
    original: str,
    edited: str,
    user: Optional[str],
) -> Optional[int]:
    """Persist a correction event. Returns correction_id or None on failure."""
    if (original or "") == (edited or ""):
        return None
    diff = _diff_summary(original or "", edited or "")
    try:
        eng = _engine()
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_corrections
                        (project_slug, run_id, agent_name, original_output,
                         edited_output, diff_summary, created_by)
                    VALUES (:p, :r, :a, :o, :e, :d, :u)
                    RETURNING id
                    """
                ),
                {
                    "p": project, "r": run_id, "a": agent_name,
                    "o": original, "e": edited, "d": diff, "u": user,
                },
            ).first()
        return int(row[0]) if row else None
    except Exception:
        logger.exception("corrections.record_correction failed")
        return None


_RULE_PROMPT = """You are analyzing a user's edit to an AI agent's output. Your job is to
extract a SINGLE DURABLE RULE that, if the agent had followed it, would have
produced the edited version directly.

ORIGINAL AGENT OUTPUT:
---
{original}
---

USER'S EDITED VERSION:
---
{edited}
---

DIFF (for reference):
---
{diff}
---

Return ONE short imperative sentence (under 25 words) capturing the rule the
agent should follow next time. No preamble, no quotes, no explanation.
Examples of good rules:
- Always round revenue figures to the nearest thousand.
- Cite the source table for every metric.
- Use Myanmar Kyat (MMK), not USD, for fuel prices.

If no clear rule can be extracted, return exactly: NO_RULE
"""


def extract_rule_from_diff(correction_id: int) -> Optional[str]:
    """Call LLM to extract a durable rule from a stored correction."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT project_slug, agent_name, original_output,
                           edited_output, diff_summary
                      FROM dash.dash_corrections WHERE id = :id
                    """
                ),
                {"id": correction_id},
            ).mappings().first()
        if not row:
            return None

        prompt = _RULE_PROMPT.format(
            original=(row["original_output"] or "")[:4000],
            edited=(row["edited_output"] or "")[:4000],
            diff=(row["diff_summary"] or "")[:2000],
        )

        try:
            from dash.settings import training_llm_call
            raw = training_llm_call(prompt, task="extraction")
        except Exception:
            logger.exception("corrections: LLM call failed")
            return None

        if not raw:
            return None
        rule = raw.strip().strip('"').strip("'")
        # Strip common prefixes the model may add
        for prefix in ("Rule:", "RULE:", "- "):
            if rule.startswith(prefix):
                rule = rule[len(prefix):].strip()
        if not rule or rule.upper().startswith("NO_RULE"):
            return None
        # Cap length
        if len(rule) > 400:
            rule = rule[:400].rstrip() + "..."

        # Auto-save as agent-scoped rule
        try:
            save_rule(
                project=row["project_slug"],
                scope="agent" if row["agent_name"] else "project",
                scope_target=row["agent_name"],
                rule_text=rule,
                source_correction_id=correction_id,
            )
        except Exception:
            logger.exception("corrections: auto-save rule failed")

        return rule
    except Exception:
        logger.exception("corrections.extract_rule_from_diff failed")
        return None


def save_rule(
    project: Optional[str],
    scope: str,
    scope_target: Optional[str],
    rule_text: str,
    source_correction_id: Optional[int] = None,
) -> Optional[int]:
    """Persist a durable rule. Returns rule_id."""
    if not rule_text or not rule_text.strip():
        return None
    scope = scope if scope in ("project", "agent", "skill") else "project"
    try:
        eng = _engine()
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_correction_rules
                        (project_slug, scope, scope_target, rule_text,
                         source_correction_id, active)
                    VALUES (:p, :s, :t, :r, :c, true)
                    RETURNING id
                    """
                ),
                {
                    "p": project, "s": scope, "t": scope_target,
                    "r": rule_text.strip(), "c": source_correction_id,
                },
            ).first()
        return int(row[0]) if row else None
    except Exception:
        logger.exception("corrections.save_rule failed")
        return None


def get_active_rules(
    project: Optional[str],
    agent_name: Optional[str] = None,
    skill_name: Optional[str] = None,
) -> List[str]:
    """Return active rule texts for project, optionally filtered by agent/skill.

    Always includes project-wide rules (scope='project'). Increments hit_count.
    Uses CAST(... AS TEXT) IS NULL guard for PgBouncer.
    """
    try:
        eng = _engine()
        with eng.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, rule_text
                      FROM dash.dash_correction_rules
                     WHERE active = true
                       AND (CAST(:p AS TEXT) IS NULL OR project_slug = :p OR project_slug IS NULL)
                       AND (
                            scope = 'project'
                         OR (scope = 'agent' AND CAST(:a AS TEXT) IS NOT NULL AND scope_target = :a)
                         OR (scope = 'skill' AND CAST(:k AS TEXT) IS NOT NULL AND scope_target = :k)
                       )
                     ORDER BY created_at DESC
                     LIMIT 40
                    """
                ),
                {"p": project, "a": agent_name, "k": skill_name},
            ).mappings().all()
            ids = [r["id"] for r in rows]
            if ids:
                conn.execute(
                    text(
                        "UPDATE dash.dash_correction_rules "
                        "SET hit_count = hit_count + 1 "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
        return [r["rule_text"] for r in rows]
    except Exception:
        logger.exception("corrections.get_active_rules failed")
        return []


def build_rules_prompt_block(
    project: Optional[str],
    agent_name: Optional[str] = None,
) -> str:
    """Return a markdown block for system prompt injection, or '' if no rules."""
    if not project:
        return ""
    rules = get_active_rules(project, agent_name=agent_name)
    if not rules:
        return ""
    lines = ["## Learned rules (apply unless contradicted):"]
    for r in rules:
        lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def list_recent_corrections(
    project: Optional[str],
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return recent correction events for UI."""
    limit = max(1, min(200, int(limit)))
    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, run_id, agent_name, diff_summary,
                           created_at, created_by
                      FROM dash.dash_corrections
                     WHERE (CAST(:p AS TEXT) IS NULL OR project_slug = :p)
                     ORDER BY created_at DESC
                     LIMIT :lim
                    """
                ),
                {"p": project, "lim": limit},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("corrections.list_recent_corrections failed")
        return []


def list_rules(
    project: Optional[str],
    scope: Optional[str] = None,
    scope_target: Optional[str] = None,
    include_inactive: bool = True,
) -> List[Dict[str, Any]]:
    """Return all rules matching filters for admin UI."""
    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_slug, scope, scope_target, rule_text,
                           source_correction_id, active, hit_count, created_at
                      FROM dash.dash_correction_rules
                     WHERE (CAST(:p AS TEXT) IS NULL OR project_slug = :p)
                       AND (CAST(:s AS TEXT) IS NULL OR scope = :s)
                       AND (CAST(:t AS TEXT) IS NULL OR scope_target = :t)
                       AND (:inc = true OR active = true)
                     ORDER BY active DESC, created_at DESC
                     LIMIT 500
                    """
                ),
                {"p": project, "s": scope, "t": scope_target, "inc": include_inactive},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("corrections.list_rules failed")
        return []


def toggle_rule(rule_id: int) -> Optional[bool]:
    """Flip active flag. Returns new active state."""
    try:
        eng = _engine()
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE dash.dash_correction_rules "
                    "SET active = NOT active WHERE id = :id "
                    "RETURNING active"
                ),
                {"id": rule_id},
            ).first()
        return bool(row[0]) if row else None
    except Exception:
        logger.exception("corrections.toggle_rule failed")
        return None


def delete_rule(rule_id: int) -> bool:
    try:
        eng = _engine()
        with eng.begin() as conn:
            res = conn.execute(
                text("DELETE FROM dash.dash_correction_rules WHERE id = :id"),
                {"id": rule_id},
            )
        return res.rowcount > 0
    except Exception:
        logger.exception("corrections.delete_rule failed")
        return False
