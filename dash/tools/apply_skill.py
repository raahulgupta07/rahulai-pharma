"""Apply Skill tool — execute proven SQL template from skill library with usage tracking.

Voyager-style. When agent picks a skill from Layer 15 inject, it calls
apply_skill(skill_id, params) instead of writing fresh SQL. Increments
success_count / failure_count + last_used_at for A/B revert daemon.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from agno.tools import tool
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"\$(\d+)")


def _fill_template(sql_template: str, params: dict) -> tuple[str, dict]:
    """Convert $1, $2 placeholders to :p1, :p2 SQLAlchemy binds.

    params dict can be keyed by position ("1","2") OR by name (matches params_schema).
    Returns (sql, bind_dict).
    """
    binds: dict[str, Any] = {}
    # Position-keyed params: {"1": "APAC", "2": 5}
    pos_keys = sorted(params.keys()) if all(k.isdigit() for k in params.keys()) else None

    def _sub(m):
        pos = m.group(1)
        key = f"p{pos}"
        if pos_keys and pos in params:
            binds[key] = params[pos]
        else:
            # Fall back to ordered values from dict
            vals = list(params.values())
            idx = int(pos) - 1
            if idx < len(vals):
                binds[key] = vals[idx]
            else:
                binds[key] = None
        return f":{key}"

    sql = _PARAM_RE.sub(_sub, sql_template)
    return sql, binds


def _track_usage(engine, skill_id: int, success: bool, error: str | None = None) -> None:
    """Increment success/failure counter + last_used_at.

    Uses get_write_engine() (NOT the passed-in engine) — public.dash_skill_library
    writes are blocked by _guard_public_schema on the read-only get_sql_engine.
    """
    col = "success_count" if success else "failure_count"
    try:
        from db.session import get_write_engine
        write_eng = get_write_engine()
        with write_eng.begin() as c:
            c.execute(
                _t(f"""
                    UPDATE public.dash_skill_library
                       SET {col} = {col} + 1,
                           last_used_at = now()
                     WHERE id = :sid
                """),
                {"sid": int(skill_id)},
            )
    except Exception:
        logger.exception("apply_skill: usage tracking failed for skill_id=%s", skill_id)


@tool(
    name="apply_skill",
    description=(
        "Execute proven SQL skill from PROVEN SKILLS library (Layer 15 inject). "
        "Use when user question matches a listed skill — faster + validated vs writing fresh SQL. "
        "Args: skill_id (int, from PROVEN SKILLS list), params (dict, fill template placeholders e.g. {\"1\":\"APAC\",\"2\":5}). "
        "Returns: rows as JSON. Increments skill usage counter for self-improvement loop."
    ),
)
def apply_skill(skill_id: int, params: dict | str = None) -> str:
    """Execute proven SQL template with usage tracking."""
    from db.session import get_sql_engine

    # params can arrive as JSON string from LLM
    if isinstance(params, str):
        try:
            params = json.loads(params) if params.strip() else {}
        except Exception:
            params = {}
    params = params or {}

    eng = get_sql_engine()
    try:
        with eng.connect() as c:
            row = c.execute(
                _t("""
                    SELECT id, project_slug, name, sql_template, params_schema, status
                      FROM public.dash_skill_library
                     WHERE id = :sid
                """),
                {"sid": int(skill_id)},
            ).mappings().first()
    except Exception as e:
        return json.dumps({"ok": False, "error": f"skill lookup failed: {e}"})

    if not row:
        return json.dumps({"ok": False, "error": f"skill_id {skill_id} not found"})
    if row["status"] != "active":
        return json.dumps({"ok": False, "error": f"skill {row['name']} status={row['status']} (deprecated/reverted)"})

    sql, binds = _fill_template(row["sql_template"], params)

    # Execute against project read-only engine (RLS preserved)
    from db.session import get_project_readonly_engine
    try:
        proj_eng = get_project_readonly_engine(row["project_slug"])
        with proj_eng.connect() as c:
            result = c.execute(_t(sql), binds)
            rows = [dict(r._mapping) for r in result.fetchall()[:1000]]
    except Exception as e:
        _track_usage(eng, skill_id, success=False, error=str(e))
        return json.dumps({
            "ok": False,
            "skill_id": skill_id,
            "skill_name": row["name"],
            "error": str(e),
            "sql_executed": sql,
        })

    _track_usage(eng, skill_id, success=True)
    # Sanitize skill_name for tag (pipes would break pipe-separated tag format)
    _safe_skill_name = str(row["name"]).replace("|", "/")
    _skill_tag = f"[SKILL_USED:{_safe_skill_name}|{skill_id}]"
    return json.dumps({
        "ok": True,
        "skill_id": skill_id,
        "skill_name": row["name"],
        "row_count": len(rows),
        "rows": rows,
        "sql_executed": sql,
    }, default=str) + f"\n{_skill_tag}"
