"""Schema-aware skill params inference.

Public surface:
    infer_params_schema(sql_template: str, project_slug: str) -> dict

Parses `$1, $2, ...` (and `{name}`) placeholders out of a parameterized SQL
template. For each placeholder, walks the SQL with a tiny regex to find the
adjacent column reference (e.g. `WHERE region = $1`), looks up the column's
data type via the project's read-only engine `information_schema.columns`,
and maps to a normalised JSON-schema-ish type ("text" / "int" / "numeric" /
"date" / "timestamp" / "bool").

Fallback: when no column can be inferred, calls LITE_MODEL once with the SQL
+ outstanding placeholders for a JSON guess. Bounded LLM cost: at most 1
call per skill (the single fallback batch covers all unresolved params).

Output shape (compatible with existing skill_library params_schema):
    {"1": {"type": "text", "required": True},
     "2": {"type": "int",  "required": True}}

Never raises. Returns `{}` on total failure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from dash.settings import training_llm_call

logger = logging.getLogger(__name__)


_PLACEHOLDER_RX = re.compile(r"\$(\d+)")
_BRACE_PLACE_RX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Look for `<col> <op> <placeholder>` or `<col> <op> ( $N )` or `<col> IN (...$N...)`
# Conservative: allow optional schema/table qualifier, optional double-quotes.
_COL_BEFORE_PH = re.compile(
    r"""([a-zA-Z_][a-zA-Z0-9_\.\"]*)\s*               # column ref
        (?:=|<=|>=|<|>|!=|<>|\s+IN\s*\(|\s+LIKE\s+|\s+ILIKE\s+|\s+BETWEEN\s+)
        [^,;()]{0,40}?                                  # tolerate cast/parens
        \{KEY\}                                         # placeholder marker
    """,
    re.IGNORECASE | re.VERBOSE,
)


_PG_TYPE_MAP = {
    "integer":            "int",
    "bigint":             "int",
    "smallint":           "int",
    "serial":             "int",
    "bigserial":          "int",
    "numeric":            "numeric",
    "decimal":            "numeric",
    "real":               "numeric",
    "double precision":   "numeric",
    "money":              "numeric",
    "text":               "text",
    "character varying":  "text",
    "varchar":            "text",
    "character":          "text",
    "char":               "text",
    "uuid":               "text",
    "date":               "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone":    "timestamp",
    "timestamp":          "timestamp",
    "boolean":            "bool",
    "bool":               "bool",
    "json":               "text",
    "jsonb":              "text",
}


_LLM_FALLBACK_PROMPT = """Given this parameterized SQL template and the list of
placeholders that could not be inferred from the schema, guess the most likely
data type for each placeholder. Pick from: text, int, numeric, date, timestamp, bool.

SQL:
{sql}

Unresolved placeholders: {keys}

Output ONLY a JSON object like {{"1": "text", "2": "int"}} — no preamble, no
markdown fences.
"""


def _placeholders(sql: str) -> List[str]:
    return sorted(set(_PLACEHOLDER_RX.findall(sql or "")) | set(_BRACE_PLACE_RX.findall(sql or "")))


def _find_column_for_placeholder(sql: str, key: str) -> Optional[str]:
    """Return the column name (possibly schema/table-qualified) referenced
    immediately before placeholder `key` in `sql`, or None."""
    if not sql:
        return None
    # Build a marker-safe variant of the SQL so we can substitute the chosen
    # placeholder in our regex.
    if key.isdigit():
        marker_pat = rf"\${key}\b"
    else:
        marker_pat = rf"\{{{key}\}}"
    # Use the COL_BEFORE_PH pattern by injecting the marker.
    rx = re.compile(_COL_BEFORE_PH.pattern.replace("\\{KEY\\}", marker_pat),
                    re.IGNORECASE | re.VERBOSE)
    m = rx.search(sql)
    if not m:
        return None
    col = m.group(1).strip().strip('"')
    # Strip optional schema/table prefix (a.b.c -> c)
    parts = col.split(".")
    return parts[-1].strip().strip('"') or None


def _resolve_col_type(eng, col: str, project_slug: str) -> Optional[str]:
    """Look up the column's data type via information_schema. Searches all
    schemas owned by the project's read-only engine (commonly: project's own
    schema + 'public' + 'dash').
    """
    if not col:
        return None
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT data_type
                  FROM information_schema.columns
                 WHERE LOWER(column_name) = LOWER(:c)
                 LIMIT 5
                """
            ), {"c": col}).fetchall()
    except Exception:
        logger.debug("skill_schema_infer: information_schema lookup failed for %s", col,
                     exc_info=True)
        return None
    if not rows:
        return None
    raw = (rows[0][0] or "").strip().lower()
    return _PG_TYPE_MAP.get(raw, "text")


def _llm_fallback(sql: str, unresolved: List[str]) -> Dict[str, str]:
    if not unresolved:
        return {}
    try:
        prompt = _LLM_FALLBACK_PROMPT.format(
            sql=(sql or "")[:1200],
            keys=", ".join(unresolved),
        )
        raw = training_llm_call(prompt, "extraction")
        if not raw:
            return {}
        # 4-tier cleanup
        cleaned = raw.strip().strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            parsed = json.loads(cleaned)
        except Exception:
            i, j = cleaned.find("{"), cleaned.rfind("}")
            if i >= 0 and j > i:
                try:
                    parsed = json.loads(cleaned[i:j+1])
                except Exception:
                    return {}
            else:
                return {}
        if not isinstance(parsed, dict):
            return {}
        # Normalise to known types
        out: Dict[str, str] = {}
        valid = {"text", "int", "numeric", "date", "timestamp", "bool"}
        for k, v in parsed.items():
            v_norm = str(v).strip().lower()
            out[str(k)] = v_norm if v_norm in valid else "text"
        return out
    except Exception:
        logger.debug("skill_schema_infer: LLM fallback failed", exc_info=True)
        return {}


def infer_params_schema(sql_template: str, project_slug: str) -> Dict[str, Any]:
    """Infer params_schema for a parameterized SQL template using the project's
    information_schema. Returns dict shaped as
    {placeholder: {"type": str, "required": True}}.
    """
    if not sql_template:
        return {}
    keys = _placeholders(sql_template)
    if not keys:
        return {}

    eng = None
    try:
        from db.session import get_project_readonly_engine
        eng = get_project_readonly_engine(project_slug)
    except Exception:
        logger.debug("skill_schema_infer: project ro engine bootstrap failed for %s",
                     project_slug, exc_info=True)

    resolved: Dict[str, str] = {}
    unresolved: List[str] = []

    for k in keys:
        t: Optional[str] = None
        if eng is not None:
            col = _find_column_for_placeholder(sql_template, k)
            if col:
                t = _resolve_col_type(eng, col, project_slug)
        if t:
            resolved[k] = t
        else:
            unresolved.append(k)

    # LLM fallback for unresolved placeholders (single batched call)
    if unresolved:
        guessed = _llm_fallback(sql_template, unresolved)
        for k in unresolved:
            resolved.setdefault(k, guessed.get(k, "text"))

    # Build final schema in skill_library's existing shape
    out: Dict[str, Any] = {}
    for k, t in resolved.items():
        out[k] = {"type": t, "required": True}
    return out


__all__ = ["infer_params_schema"]
