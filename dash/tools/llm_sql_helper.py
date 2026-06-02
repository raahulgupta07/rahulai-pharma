"""
Self-correcting LLM SQL generator. Wraps `training_llm_call` with
validate-and-retry loop so callers can't accidentally persist garbage SQL.

Re-exports `get_schema_hint` from sql_validator for convenience so callers can
import both rules + schema hint from a single module.

Public API:
    generate_sql_safe(prompt, project_slug, *, task='extraction', schema=None,
                      max_retries=1, parse_sql_fn=None) -> dict

Pattern (callers MUST use this for any LLM-generated SQL):
    r = generate_sql_safe(prompt, slug, parse_sql_fn=my_parser)
    if not r['validated']:
        # skip persist — never write unvalidated SQL
        log.info(f'skipped: {r["skip_reason"]}')
        continue
    sql = r['sql']  # validated + auto-fixed
"""
from __future__ import annotations
import logging
from typing import Callable, Optional

logger = logging.getLogger("llm_sql_helper")

# Re-export so callers can import both from one module
try:
    from dash.tools.sql_validator import get_schema_hint  # noqa: F401
except Exception:  # pragma: no cover
    def get_schema_hint(project_slug: str, schema: Optional[str] = None, **kwargs) -> str:
        return ""


def _default_parse_sql(text: str) -> Optional[str]:
    """Default SQL extractor. Strips ```sql fences, returns first SELECT/WITH stmt."""
    if not text:
        return None
    import re
    t = text.strip()
    # Strip markdown fences
    fence_match = re.search(r"```(?:sql)?\s*\n?(.+?)\n?```", t, re.IGNORECASE | re.DOTALL)
    if fence_match:
        t = fence_match.group(1).strip()
    # Strip leading SQL comment lines
    lines = [ln for ln in t.split("\n") if not ln.strip().startswith("--")]
    t = "\n".join(lines).strip()
    # Find first SELECT/WITH/EXPLAIN/SHOW
    m = re.search(r"\b(SELECT|WITH|EXPLAIN|SHOW)\b.*", t, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    sql = m.group(0).strip().rstrip(";").strip()
    return sql or None


def generate_sql_safe(
    prompt: str,
    project_slug: str,
    *,
    task: str = "extraction",
    schema: Optional[str] = None,
    max_retries: int = 1,
    parse_sql_fn: Optional[Callable[[str], Optional[str]]] = None,
    inject_schema_hint: bool = True,
) -> dict:
    """Generate SQL via LLM with validate + auto-fix + retry.

    Returns:
        {
            "sql": str | None,
            "validated": bool,
            "attempts": int,
            "fixes_applied": [str],
            "skip_reason": str | None,
            "raw_responses": [str],   # for debug
        }
    """
    from dash.settings import training_llm_call
    from dash.tools.sql_validator import validate_and_fix, get_schema_hint

    parse = parse_sql_fn or _default_parse_sql
    out = {
        "sql": None, "validated": False, "attempts": 0,
        "fixes_applied": [], "skip_reason": None, "raw_responses": [],
    }

    # Optional: inject schema hint + Postgres rules into prompt on first try
    full_prompt = prompt
    if inject_schema_hint:
        hint = get_schema_hint(project_slug, schema)
        rules = _postgres_sql_rules()
        full_prompt = f"{rules}\n\n{hint}\n\n{prompt}"

    last_errors: list[str] = []
    for attempt in range(max_retries + 1):
        out["attempts"] = attempt + 1
        try:
            raw = training_llm_call(full_prompt, task) or ""
        except Exception as e:
            out["skip_reason"] = f"LLM call failed: {str(e)[:200]}"
            return out
        out["raw_responses"].append(raw[:500])

        sql = parse(raw)
        if not sql:
            last_errors = ["could not parse SQL from LLM response"]
            full_prompt = _retry_prompt(prompt, project_slug, schema, "", last_errors)
            continue

        v = validate_and_fix(sql, project_slug, schema=schema, strict=True)
        if v["ok"]:
            out["sql"] = v["sql"]
            out["validated"] = True
            out["fixes_applied"] = v["fixes_applied"]
            return out

        last_errors = v["errors"]
        # Build retry prompt with error + schema hint
        full_prompt = _retry_prompt(prompt, project_slug, schema, sql, last_errors)

    # Exhausted retries
    out["skip_reason"] = f"validation failed after {out['attempts']} attempts: {'; '.join(last_errors)[:300]}"
    return out


def _postgres_sql_rules() -> str:
    """Dialect rules block — injected into every LLM-SQL prompt."""
    return (
        "## POSTGRES DIALECT — ENFORCED:\n"
        "- TEXT columns storing dates: cast with `col::date` BEFORE date_trunc / EXTRACT / >= comparisons.\n"
        "- Numeric columns stored as TEXT: cast with `col::numeric` before SUM/AVG/MIN/MAX.\n"
        "- Quote identifiers with spaces or reserved words: \"Column Name\".\n"
        "- Use COALESCE(col, 0) for NULL-safe numeric ops, not col=NULL.\n"
        "- Always include LIMIT 5000 for exploration queries.\n"
        "- Use ONLY tables + columns listed in the SCHEMA section below. Do NOT invent columns.\n"
    )


def _retry_prompt(orig_prompt: str, slug: str, schema: Optional[str], failed_sql: str, errors: list[str]) -> str:
    """Build retry prompt with error feedback."""
    from dash.tools.sql_validator import get_schema_hint
    hint = get_schema_hint(slug, schema)
    rules = _postgres_sql_rules()
    err_str = "\n".join(f"  - {e}" for e in errors)
    failed_block = f"\n\nPREVIOUS ATTEMPT (FAILED VALIDATION):\n```sql\n{failed_sql}\n```\n" if failed_sql else ""
    return (
        f"{rules}\n\n{hint}\n\n"
        f"{orig_prompt}"
        f"{failed_block}"
        f"\nERRORS:\n{err_str}\n\n"
        f"Rewrite the SQL fixing ALL errors above. Use ONLY the columns + tables from SCHEMA. Output ONLY the corrected SQL inside ```sql ... ``` fences."
    )
