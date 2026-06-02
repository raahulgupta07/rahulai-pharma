"""Embed row-level security runtime engine.

Migration 064 adds rls_enabled / rls_claims / rls_policies / rls_claim_source
columns to dash_agent_embeds. This module provides the runtime enforcement:

  - ContextVars carrying per-request claims + active policy list
  - load_rls_for_embed(embed_id)  - read config from DB
  - extract_claims(...)           - pull declared claims from token/hmac/url/header
  - rewrite_sql(sql, policies, claims)  - SQL rewriter (sqlglot AST or regex fallback)
  - scrub_result(rows, policies, claims)- defense-in-depth row scrubber
  - audit_denial(...)             - INSERT into dash_embed_rls_audit

Policy modes (per column):
  hidden     -> remove column from SELECT list / replace with NULL AS col
  redacted   -> replace value with NULL AS col (mask configurable later)
  private    -> append WHERE table.filter_col = '<claim>' (filters whole rows)
  shared     -> no-op
  own_value  -> per-ROW mask: CASE WHEN filter_col = '<claim>' THEN col ELSE NULL END.
                Row stays in result set; value is NULL for non-matching rows.
                Aggregates (SUM/AVG/COUNT/MIN/MAX) get the CASE injected inside.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

VALID_MODES = {"hidden", "redacted", "private", "shared", "own_value"}

# ── ContextVars (request-scoped) ────────────────────────────────────────
EMBED_CLAIMS: ContextVar[dict | None] = ContextVar("embed_claims", default=None)
EMBED_RLS_POLICIES: ContextVar[list | None] = ContextVar("embed_rls_policies", default=None)
EMBED_RLS_AUDIT_CTX: ContextVar[dict | None] = ContextVar("embed_rls_audit_ctx", default=None)


class RLSDenied(Exception):
    """Raised when SQL cannot be safely rewritten or a required claim is missing."""


# ── sqlglot probe ───────────────────────────────────────────────────────
try:
    import sqlglot
    from sqlglot import exp as _sg_exp
    _HAS_SQLGLOT = True
except Exception:  # pragma: no cover
    sqlglot = None  # type: ignore
    _sg_exp = None  # type: ignore
    _HAS_SQLGLOT = False


# ── DB helper ───────────────────────────────────────────────────────────
def _engine():
    from dash.embed import _get_engine
    return _get_engine()


# ── Load config ─────────────────────────────────────────────────────────
def load_rls_for_embed(embed_id: str) -> tuple[bool, list, list, str]:
    """Return (enabled, claims_def, policies, claim_source).

    Returns (False, [], [], 'token') if RLS disabled, embed not found, or
    DB read fails.
    """
    if not embed_id:
        return False, [], [], "token"
    try:
        from sqlalchemy import text as _t
        eng = _engine()
        with eng.begin() as conn:
            row = conn.execute(_t(
                "SELECT COALESCE(rls_enabled, FALSE)        AS rls_enabled, "
                "       COALESCE(CAST(rls_claims   AS TEXT), '[]') AS rls_claims, "
                "       COALESCE(CAST(rls_policies AS TEXT), '[]') AS rls_policies, "
                "       COALESCE(rls_claim_source, 'token') AS rls_claim_source "
                "  FROM public.dash_agent_embeds WHERE embed_id = :eid"
            ), {"eid": embed_id}).fetchone()
        if not row or not row[0]:
            return False, [], [], "token"
        import json as _j
        claims_def = _j.loads(row[1]) if isinstance(row[1], str) else (row[1] or [])
        policies = _j.loads(row[2]) if isinstance(row[2], str) else (row[2] or [])
        return True, claims_def or [], policies or [], row[3] or "token"
    except Exception as e:
        logger.warning("load_rls_for_embed(%s) failed: %s", embed_id, e)
        return False, [], [], "token"


# ── Claim extraction ────────────────────────────────────────────────────
def extract_claims(
    claims_def: list,
    source: str,
    *,
    token_payload: dict | None = None,
    hmac_payload: dict | None = None,
    url_params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Pull declared claim keys from the configured source.

    Raises RLSDenied if a required claim is missing.
    """
    src_map = {
        "token": token_payload or {},
        "hmac": hmac_payload or {},
        "url": url_params or {},
        "header": headers or {},
    }
    bag = src_map.get(source or "token", {}) or {}
    # Header keys are case-insensitive — also expose lowercased view.
    if source == "header":
        bag = {**bag, **{(k or "").lower(): v for k, v in bag.items()}}
    out: dict = {}
    for cdef in (claims_def or []):
        key = (cdef or {}).get("key")
        if not key:
            continue
        val = bag.get(key)
        if val is None and source == "header":
            val = bag.get(key.lower())
        if val is None or val == "":
            if (cdef or {}).get("required"):
                raise RLSDenied(f"Missing required claim: {key}")
            continue
        out[key] = val
    return out


# ── SQL escaping ────────────────────────────────────────────────────────
def _sqlescape(val: Any) -> str:
    """Escape a scalar for safe inline literal use. Single-quote escape only —
    callers must NOT trust raw claim values."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


def _norm_ident(name: str) -> str:
    return (name or "").strip().strip('"').strip("`").lower()


# ── SQL rewrite ─────────────────────────────────────────────────────────
def _rewrite_with_sqlglot(sql: str, policies: list, claims: dict) -> str:
    """sqlglot AST-based rewriter. Raises RLSDenied if structure is too
    complex to safely handle (CTEs, set ops, etc.)."""
    assert _HAS_SQLGLOT and sqlglot is not None and _sg_exp is not None

    role_claim = (claims or {}).get("role")
    roles = {role_claim} if isinstance(role_claim, str) else set(role_claim or [])

    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as e:
        raise RLSDenied(f"SQL parse failed: {e}")

    # Refuse compound structures we can't analyze safely.
    if list(tree.find_all(_sg_exp.CTE)):
        raise RLSDenied("RLS rewriter does not support CTEs")
    if list(tree.find_all(_sg_exp.Union, _sg_exp.Intersect, _sg_exp.Except)):
        raise RLSDenied("RLS rewriter does not support set operations")
    if list(tree.find_all(_sg_exp.Subquery)):
        # A SELECT in WHERE clause is fine if it's a scalar — but we play safe.
        raise RLSDenied("RLS rewriter does not support subqueries")

    # Find the SELECT
    select = tree if isinstance(tree, _sg_exp.Select) else tree.find(_sg_exp.Select)
    if select is None:
        raise RLSDenied("No SELECT found")

    # Collect referenced tables (name -> alias-or-name)
    table_refs: dict[str, str] = {}
    for t in select.find_all(_sg_exp.Table):
        tname = _norm_ident(t.name)
        ident = _norm_ident(t.alias_or_name) or tname
        if tname:
            table_refs[tname] = ident

    # Index policies by table
    by_table: dict[str, list] = {}
    for p in (policies or []):
        tn = _norm_ident(p.get("table") or "")
        if tn:
            by_table.setdefault(tn, []).append(p)

    # ── Helper: build CASE WHEN <alias>.<filter_col> = <claim> THEN <inner> ELSE NULL END
    def _build_case(inner_expr, filter_col: str, claim_val, table_alias: str):
        """Construct sqlglot CASE expression for own_value masking."""
        cond_sql = f'"{table_alias}"."{filter_col}" = {_sqlescape(claim_val)}'
        try:
            cond = sqlglot.parse_one(cond_sql, read="postgres", into=_sg_exp.EQ)
        except Exception:
            cond = sqlglot.parse_one(f"({cond_sql})", read="postgres")
        case_expr = _sg_exp.Case(ifs=[_sg_exp.If(this=cond, true=inner_expr)], default=_sg_exp.Null())
        return case_expr

    _AGG_TYPES = (_sg_exp.Sum, _sg_exp.Avg, _sg_exp.Min, _sg_exp.Max, _sg_exp.Count)

    def _resolve_own_value_for_col(col_name: str, tbl_hint: str | None):
        """Return (policy, table_name, table_alias) if this col matches an own_value policy
        AND caller is not bypassed, else None."""
        for tname, plist in by_table.items():
            if tname not in table_refs:
                continue
            if tbl_hint and tbl_hint not in (tname, table_refs[tname]):
                continue
            for p in plist:
                if (p.get("mode") or "").lower() != "own_value":
                    continue
                if _norm_ident(p.get("column") or "") != col_name:
                    continue
                bypass = set(p.get("bypass_roles") or [])
                if bypass & roles:
                    continue
                filter_col = p.get("filter") or p.get("column")
                if not filter_col:
                    continue
                claim_key = p.get("claim") or filter_col
                cval = (claims or {}).get(claim_key)
                if cval is None:
                    continue
                return p, tname, table_refs[tname], filter_col, cval
        return None

    # ── Projection rewrite (hidden/redacted/own_value) ───────────────
    # Only touch SELECT lists when not SELECT * (we leave SELECT * alone —
    # scrub_result handles those at the row level).
    projections = list(select.expressions or [])
    is_star = any(isinstance(e, _sg_exp.Star) for e in projections)

    own_value_rewrites: list[tuple[str, str, str]] = []  # (table, col, sql_snippet) for audit

    if not is_star and projections:
        new_proj: list = []
        for exp_node in projections:
            # ── Aggregate handling for own_value: wrap inner column with CASE
            agg_inner_col = None
            agg_alias_name = None
            agg_node_for_rewrite = None
            outer_alias_node = None

            candidate = exp_node
            if isinstance(candidate, _sg_exp.Alias):
                outer_alias_node = candidate
                agg_alias_name = _norm_ident(candidate.alias)
                candidate = candidate.this
            if isinstance(candidate, _AGG_TYPES):
                inner = candidate.this
                if isinstance(inner, _sg_exp.Column):
                    agg_inner_col = (_norm_ident(inner.name), _norm_ident(inner.table) if inner.table else None)
                    agg_node_for_rewrite = candidate

            if agg_inner_col is not None and agg_node_for_rewrite is not None:
                cname, thint = agg_inner_col
                resolved = _resolve_own_value_for_col(cname, thint)
                if resolved is not None:
                    _p, tname, talias, fcol, cval = resolved
                    inner_col = agg_node_for_rewrite.this
                    case_expr = _build_case(inner_col, fcol, cval, talias)
                    agg_node_for_rewrite.set("this", case_expr)
                    own_value_rewrites.append((tname, cname, f"agg({cname})→CASE"))
                    # preserve outer alias if any
                    if outer_alias_node is not None:
                        new_proj.append(outer_alias_node)
                    else:
                        new_proj.append(agg_node_for_rewrite)
                    continue

            # Discover target column alias / name (non-aggregate path)
            col_name = None
            tbl_hint = None
            if isinstance(exp_node, _sg_exp.Alias):
                alias_name = _norm_ident(exp_node.alias)
                inner = exp_node.this
                if isinstance(inner, _sg_exp.Column):
                    col_name = _norm_ident(inner.name)
                    tbl_hint = _norm_ident(inner.table) if inner.table else None
                else:
                    col_name = alias_name
            elif isinstance(exp_node, _sg_exp.Column):
                col_name = _norm_ident(exp_node.name)
                tbl_hint = _norm_ident(exp_node.table) if exp_node.table else None
            else:
                new_proj.append(exp_node)
                continue

            # Match against any policy targeting this column on a referenced table.
            matched = None
            matched_table = None
            matched_alias = None
            for tname, plist in by_table.items():
                if tname not in table_refs:
                    continue
                if tbl_hint and tbl_hint not in (tname, table_refs[tname]):
                    continue
                for p in plist:
                    if _norm_ident(p.get("column") or "") == col_name:
                        matched = p
                        matched_table = tname
                        matched_alias = table_refs[tname]
                        break
                if matched:
                    break

            if not matched:
                new_proj.append(exp_node)
                continue

            mode = (matched.get("mode") or "shared").lower()
            bypass = set(matched.get("bypass_roles") or [])
            if bypass & roles:
                new_proj.append(exp_node)
                continue

            if mode in ("hidden", "redacted"):
                # Replace with NULL AS <col_name>
                null_alias = _sg_exp.Alias(
                    this=_sg_exp.Null(),
                    alias=_sg_exp.to_identifier(col_name),
                )
                new_proj.append(null_alias)
            elif mode == "own_value":
                filter_col = matched.get("filter") or matched.get("column")
                claim_key = matched.get("claim") or filter_col
                cval = (claims or {}).get(claim_key)
                if not filter_col or cval is None:
                    # Defensive: missing claim → mask to NULL (don't leak)
                    null_alias = _sg_exp.Alias(
                        this=_sg_exp.Null(),
                        alias=_sg_exp.to_identifier(col_name),
                    )
                    new_proj.append(null_alias)
                else:
                    inner_col = _sg_exp.column(col_name, table=matched_alias)
                    case_expr = _build_case(inner_col, filter_col, cval, matched_alias)
                    # Preserve alias if present, else use col_name as alias
                    alias_name_use = (
                        _norm_ident(exp_node.alias)
                        if isinstance(exp_node, _sg_exp.Alias) else col_name
                    )
                    new_proj.append(_sg_exp.Alias(
                        this=case_expr,
                        alias=_sg_exp.to_identifier(alias_name_use),
                    ))
                    own_value_rewrites.append((matched_table or "", col_name, f"{col_name}→CASE"))
            else:
                new_proj.append(exp_node)

        select.set("expressions", new_proj)

    # ── WHERE injection (private) ────────────────────────────────────
    extra_wheres: list[str] = []
    for tname, plist in by_table.items():
        if tname not in table_refs:
            continue
        alias = table_refs[tname]
        for p in plist:
            if (p.get("mode") or "").lower() != "private":
                continue
            bypass = set(p.get("bypass_roles") or [])
            if bypass & roles:
                continue
            filter_col = p.get("filter") or p.get("column")
            if not filter_col:
                continue
            claim_key = p.get("claim") or filter_col
            cval = (claims or {}).get(claim_key)
            if cval is None:
                # Required filter but no claim → deny.
                raise RLSDenied(f"Missing claim '{claim_key}' for private policy on {tname}.{filter_col}")
            extra_wheres.append(f'"{alias}"."{filter_col}" = {_sqlescape(cval)}')

    # Emit audit log for own_value rewrites (best-effort; never blocks)
    if own_value_rewrites:
        try:
            ctx = EMBED_RLS_AUDIT_CTX.get() or {}
            for (tname, cname, snippet) in own_value_rewrites:
                audit_denial(
                    ctx.get("embed_id"),
                    ctx.get("session_token"),
                    claims,
                    tname,
                    cname,
                    "rewrite_own_value",
                    sql,
                )
        except Exception:
            pass

    out_sql = tree.sql(dialect="postgres")
    if extra_wheres:
        joined = " AND ".join(extra_wheres)
        if re.search(r"\bWHERE\b", out_sql, re.IGNORECASE):
            out_sql = re.sub(r"\bWHERE\b", f"WHERE ({joined}) AND ", out_sql, count=1, flags=re.IGNORECASE)
        else:
            # Insert before GROUP/ORDER/LIMIT/HAVING if present, else append.
            m = re.search(r"\b(GROUP BY|ORDER BY|LIMIT|HAVING)\b", out_sql, re.IGNORECASE)
            if m:
                out_sql = out_sql[: m.start()] + f"WHERE {joined} " + out_sql[m.start():]
            else:
                out_sql = out_sql.rstrip().rstrip(";") + f" WHERE {joined}"
    return out_sql


def _rewrite_with_regex(sql: str, policies: list, claims: dict) -> str:
    """Minimal regex fallback when sqlglot unavailable. Only supports
    appending WHERE for private mode + naive column strip for hidden mode
    on simple SELECT col1, col2 FROM tbl statements."""
    role_claim = (claims or {}).get("role")
    roles = {role_claim} if isinstance(role_claim, str) else set(role_claim or [])

    new_sql = sql

    # hidden / redacted: replace col with NULL AS col in SELECT list
    m = re.match(r"\s*SELECT\s+(.+?)\s+FROM\s+", new_sql, re.IGNORECASE | re.DOTALL)
    if m and "*" not in m.group(1):
        cols = [c.strip() for c in m.group(1).split(",")]
        out_cols = []
        for c in cols:
            cn = _norm_ident(re.split(r"\s+AS\s+|\s+", c, maxsplit=1)[0])
            replaced = False
            for p in (policies or []):
                if (p.get("mode") or "").lower() not in ("hidden", "redacted"):
                    continue
                if set(p.get("bypass_roles") or []) & roles:
                    continue
                if _norm_ident(p.get("column") or "") == cn:
                    out_cols.append(f"NULL AS {cn}")
                    replaced = True
                    break
            if not replaced:
                out_cols.append(c)
        new_sql = new_sql.replace(m.group(1), ", ".join(out_cols), 1)

    # private: append WHERE col = '<claim>'
    extra = []
    for p in (policies or []):
        if (p.get("mode") or "").lower() != "private":
            continue
        if set(p.get("bypass_roles") or []) & roles:
            continue
        tbl = p.get("table")
        fcol = p.get("filter") or p.get("column")
        ckey = p.get("claim") or fcol
        cval = (claims or {}).get(ckey)
        if not (tbl and fcol):
            continue
        if cval is None:
            raise RLSDenied(f"Missing claim '{ckey}' for private policy on {tbl}.{fcol}")
        # Only inject if table appears in query.
        if not re.search(rf"\b{re.escape(tbl)}\b", new_sql, re.IGNORECASE):
            continue
        extra.append(f'{tbl}."{fcol}" = {_sqlescape(cval)}')

    if extra:
        joined = " AND ".join(extra)
        if re.search(r"\bWHERE\b", new_sql, re.IGNORECASE):
            new_sql = re.sub(r"\bWHERE\b", f"WHERE ({joined}) AND ", new_sql, count=1, flags=re.IGNORECASE)
        else:
            new_sql = new_sql.rstrip().rstrip(";") + f" WHERE {joined}"
    return new_sql


def rewrite_sql(sql: str, policies: list, claims: dict, *, audit_cb=None) -> str:
    """Best-effort SQL rewriter. Raises RLSDenied for unsafe structures or
    missing required claims; caller decides what to do (typically: log audit,
    return empty result with a "policy restricted" note)."""
    if not sql or not (policies or []):
        return sql
    try:
        if _HAS_SQLGLOT:
            return _rewrite_with_sqlglot(sql, policies, claims or {})
        return _rewrite_with_regex(sql, policies, claims or {})
    except RLSDenied:
        if audit_cb:
            try:
                audit_cb()
            except Exception:
                pass
        raise


# ── Result scrubber (defense-in-depth) ─────────────────────────────────
def scrub_result(rows: list[dict], policies: list, claims: dict) -> list[dict]:
    """Post-execution scrub: drop hidden columns, NULL out redacted ones."""
    if not rows or not policies:
        return rows
    role_claim = (claims or {}).get("role")
    roles = {role_claim} if isinstance(role_claim, str) else set(role_claim or [])

    hidden: set[str] = set()
    redacted: set[str] = set()
    # own_value: {col_lower: (filter_col_lower, claim_value)} — mask col when row's filter_col != claim
    own_value: dict[str, tuple[str, Any]] = {}
    for p in policies:
        mode = (p.get("mode") or "").lower()
        if set(p.get("bypass_roles") or []) & roles:
            continue
        col = _norm_ident(p.get("column") or "")
        if not col:
            continue
        if mode == "hidden":
            hidden.add(col)
        elif mode == "redacted":
            redacted.add(col)
        elif mode == "own_value":
            fcol = _norm_ident(p.get("filter") or p.get("column") or "")
            ckey = p.get("claim") or (p.get("filter") or p.get("column"))
            cval = (claims or {}).get(ckey) if ckey else None
            if fcol and cval is not None:
                own_value[col] = (fcol, cval)

    if not (hidden or redacted or own_value):
        return rows

    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        # Build lookup by lowercased key for own_value filter checks
        row_lower = {_norm_ident(k): v for k, v in r.items()}
        nr = {}
        for k, v in r.items():
            kn = _norm_ident(k)
            if kn in hidden:
                continue
            if kn in redacted:
                nr[k] = None
                continue
            if kn in own_value:
                fcol, cval = own_value[kn]
                row_val = row_lower.get(fcol)
                if row_val is None or str(row_val) != str(cval):
                    nr[k] = None
                else:
                    nr[k] = v
                continue
            nr[k] = v
        out.append(nr)
    return out


# ── Audit ───────────────────────────────────────────────────────────────
def audit_denial(
    embed_id: str | None,
    session_token: str | None,
    claims: dict | None,
    table: str | None,
    column: str | None,
    action: str,
    sql_snippet: str | None,
) -> None:
    """INSERT into public.dash_embed_rls_audit. Fail-soft."""
    try:
        from sqlalchemy import text as _t
        import json as _j
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(_t(
                "INSERT INTO public.dash_embed_rls_audit "
                "  (embed_id, session_token, claims, denied_table, denied_column, action, sql_snippet) "
                "VALUES (:e, :s, CAST(:c AS jsonb), :t, :col, :a, :sql)"
            ), {
                "e": embed_id,
                "s": session_token,
                "c": _j.dumps(claims or {}),
                "t": table,
                "col": column,
                "a": action,
                "sql": (sql_snippet or "")[:4000],
            })
    except Exception as e:
        logger.warning("audit_denial failed: %s", e)
