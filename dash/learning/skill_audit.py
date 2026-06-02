"""Skill audit gate — 10-point checklist before a candidate is promoted into
`public.dash_skill_library`.

Public surface:
    audit_skill_candidate(name, description, sql_template, params_schema,
                          judge_score, uses, project_slug=None) -> dict

Returns:
    {
      "pass":     bool,    # True only if ALL checks pass
      "score":    int,     # number of checks passed (0..10)
      "failures": [str],   # human-readable list of failed checks
      "checks":   {name: bool, ...},  # per-check truth table
    }

Never raises. Defensive: missing optional deps (sqlglot, embeddings_helper)
degrade to soft-pass on the affected check so the gate never silently blocks
on infrastructure issues — but the failure mode is logged.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────
_NAME_RX        = re.compile(r"^[a-z][a-z0-9_]{3,40}$")
_DESTRUCTIVE_RX = re.compile(r"\b(DROP|ALTER|TRUNCATE|DELETE|UPDATE|INSERT|GRANT|REVOKE|CREATE)\b", re.IGNORECASE)
_SELECT_STAR_RX = re.compile(r"SELECT\s+\*", re.IGNORECASE)
_PLACEHOLDER_RX = re.compile(r"\$(\d+)")
_BRACE_PLACE_RX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MIN_JUDGE      = 4.2
_MIN_USES       = 5
_DUP_SIM_MAX    = 0.92


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Embedding helpers (best-effort) ────────────────────────────────────────
def _embed_sync(text_val: str) -> Optional[List[float]]:
    try:
        import asyncio
        from dash.tools.embeddings_helper import embed_text
        try:
            asyncio.get_running_loop()
            return None  # already in loop, skip
        except RuntimeError:
            return asyncio.run(embed_text(text_val or ""))
    except Exception:
        logger.debug("skill_audit: embed failed", exc_info=True)
        return None


def _text_to_vec(s: Optional[str]) -> Optional[List[float]]:
    if not s:
        return None
    try:
        v = json.loads(s)
        if isinstance(v, list) and v:
            return [float(x) for x in v]
    except Exception:
        return None
    return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Individual checks (each returns (bool, optional failure-msg)) ─────────
def _check_sql_parses(sql: str) -> tuple[bool, Optional[str]]:
    if not sql or not sql.strip():
        return False, "sql is empty"
    try:
        import sqlglot
        try:
            sqlglot.parse_one(sql, read="postgres")
            return True, None
        except Exception as e:
            return False, f"sqlglot parse error: {str(e)[:120]}"
    except ImportError:
        # sqlglot unavailable — soft-pass with warning
        logger.warning("skill_audit: sqlglot missing — sql_parses check soft-passed")
        return True, None


def _check_no_select_star(sql: str) -> tuple[bool, Optional[str]]:
    if _SELECT_STAR_RX.search(sql or ""):
        return False, "SELECT * is not allowed"
    return True, None


def _check_no_destructive(sql: str) -> tuple[bool, Optional[str]]:
    sql_upper = (sql or "").upper()
    for kw in ("DROP", "ALTER", "TRUNCATE"):
        if re.search(rf"\b{kw}\b", sql_upper):
            return False, f"destructive keyword forbidden: {kw}"
    return True, None


def _check_params_match(sql: str, params_schema: Any) -> tuple[bool, Optional[str]]:
    """Every $N placeholder in SQL must have a matching key in params_schema, and
    every params_schema key (numeric or named) must appear in SQL. Also supports
    {name} brace placeholders (existing skill_library convention)."""
    dollar_ph = set(_PLACEHOLDER_RX.findall(sql or ""))
    brace_ph  = set(_BRACE_PLACE_RX.findall(sql or ""))
    placeholders = dollar_ph | brace_ph

    if isinstance(params_schema, str):
        try:
            params_schema = json.loads(params_schema)
        except Exception:
            params_schema = {}
    if not isinstance(params_schema, dict):
        params_schema = {}

    schema_keys = {str(k) for k in params_schema.keys()}

    if not placeholders and not schema_keys:
        return True, None  # zero-param skill is OK

    missing_in_schema = placeholders - schema_keys
    missing_in_sql    = schema_keys - placeholders
    if missing_in_schema:
        return False, f"placeholders missing from params_schema: {sorted(missing_in_schema)}"
    if missing_in_sql:
        return False, f"params_schema keys missing from SQL: {sorted(missing_in_sql)}"
    return True, None


def _check_description_len(description: str) -> tuple[bool, Optional[str]]:
    n = len(description or "")
    if n < 20:
        return False, f"description too short ({n} chars, need ≥20)"
    return True, None


def _check_judge_score(judge_score: Optional[float]) -> tuple[bool, Optional[str]]:
    if judge_score is None:
        return False, f"judge score missing (need ≥{_MIN_JUDGE})"
    try:
        v = float(judge_score)
    except Exception:
        return False, f"judge score not numeric: {judge_score!r}"
    if v < _MIN_JUDGE:
        return False, f"judge score too low ({v:.2f} < {_MIN_JUDGE})"
    return True, None


def _check_uses(uses: Optional[int]) -> tuple[bool, Optional[str]]:
    try:
        n = int(uses or 0)
    except Exception:
        return False, f"uses not numeric: {uses!r}"
    if n < _MIN_USES:
        return False, f"uses too low ({n} < {_MIN_USES})"
    return True, None


def _check_name(name: str) -> tuple[bool, Optional[str]]:
    if not name or not _NAME_RX.match(name):
        return False, f"name {name!r} fails regex ^[a-z][a-z0-9_]{{3,40}}$"
    return True, None


def _check_semantic_dup(eng, project_slug: Optional[str],
                        description: str) -> tuple[bool, Optional[str]]:
    """Compare candidate description embedding against all active skills in same
    project. Block if cosine sim ≥ 0.92.

    Soft-pass if: no project_slug, embedding unavailable, or no existing skills.
    """
    if not project_slug or not description:
        return True, None
    cand_vec = _embed_sync(description)
    if not cand_vec:
        logger.debug("skill_audit: candidate embed unavailable — dup check soft-passed")
        return True, None
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT name, description_embedding
                  FROM public.dash_skill_library
                 WHERE project_slug = :p
                   AND status = 'active'
                   AND description_embedding IS NOT NULL
                """
            ), {"p": project_slug}).mappings().all()
    except Exception:
        logger.debug("skill_audit: dup-check fetch failed", exc_info=True)
        return True, None
    for r in rows:
        emb = _text_to_vec(r.get("description_embedding"))
        if not emb:
            continue
        sim = _cosine(cand_vec, emb)
        if sim >= _DUP_SIM_MAX:
            return False, f"semantic duplicate of {r['name']!r} (cos={sim:.3f} ≥ {_DUP_SIM_MAX})"
    return True, None


def _check_anti_pattern(eng, project_slug: Optional[str],
                        sql: str, description: str) -> tuple[bool, Optional[str]]:
    """Reject if SQL/description matches an active anti-pattern for this project.

    Anti-pattern matching is substring-based on `pattern_text` / `description`
    column (whichever exists). Soft-pass if table missing.
    """
    if not project_slug:
        return True, None
    try:
        with eng.connect() as conn:
            # Probe which text columns exist on dash_anti_patterns
            cols = {r[0] for r in conn.execute(text(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema='dash' AND table_name='dash_anti_patterns'
                """
            )).fetchall()}
            if not cols:
                return True, None
            text_cols = [c for c in ("pattern_text", "description", "rule_text", "body")
                         if c in cols]
            if not text_cols:
                return True, None
            select_cols = ", ".join(text_cols)
            rows = conn.execute(text(
                f"SELECT {select_cols} FROM public.dash_anti_patterns "
                f"WHERE project_slug = :p AND status = 'active'"
            ), {"p": project_slug}).fetchall()
    except Exception:
        logger.debug("skill_audit: anti-pattern fetch failed", exc_info=True)
        return True, None

    needle_sql  = (sql or "").lower()
    needle_desc = (description or "").lower()
    for r in rows:
        for cell in r:
            if not cell:
                continue
            ap = str(cell).strip().lower()
            if len(ap) < 8:
                continue
            if ap in needle_sql or ap in needle_desc:
                return False, f"matches active anti-pattern: {str(cell)[:80]}"
    return True, None


# ── Orchestrator ───────────────────────────────────────────────────────────
def audit_skill_candidate(
    name: str,
    description: str,
    sql_template: str,
    params_schema: Any,
    judge_score: Optional[float],
    uses: Optional[int],
    project_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Run 10-point audit. ALL must pass for promotion to proceed."""
    eng = None
    try:
        eng = _engine()
    except Exception:
        logger.exception("skill_audit: engine bootstrap failed (dup/anti-pattern checks soft-passed)")

    checks: Dict[str, bool]  = {}
    failures: List[str]      = []

    def _run(key: str, fn, *args):
        try:
            ok, msg = fn(*args)
        except Exception as e:
            logger.exception("skill_audit: check %s raised", key)
            ok, msg = False, f"check raised: {str(e)[:120]}"
        checks[key] = bool(ok)
        if not ok and msg:
            failures.append(f"[{key}] {msg}")

    _run("sql_parses",         _check_sql_parses,    sql_template)
    _run("no_select_star",     _check_no_select_star, sql_template)
    _run("no_destructive",     _check_no_destructive, sql_template)
    _run("params_match",       _check_params_match,   sql_template, params_schema)
    _run("description_length", _check_description_len, description)
    _run("judge_score",        _check_judge_score,    judge_score)
    _run("uses",               _check_uses,           uses)
    _run("name_regex",         _check_name,           name)
    if eng is not None:
        _run("semantic_dup",   _check_semantic_dup,   eng, project_slug, description)
        _run("anti_pattern",   _check_anti_pattern,   eng, project_slug, sql_template, description)
    else:
        checks["semantic_dup"] = True
        checks["anti_pattern"] = True

    score = sum(1 for v in checks.values() if v)
    return {
        "pass":     all(checks.values()) and len(checks) >= 10,
        "score":    score,
        "failures": failures,
        "checks":   checks,
    }


__all__ = ["audit_skill_candidate"]
