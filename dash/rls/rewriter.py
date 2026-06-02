"""Layer 2: SQL rewriter — injects per-table WHERE filters from RLS config.

Mode 'advisory' = no rewrite (LLM-only).
Mode 'rewrite'  = inject filters via sqlglot.
Mode 'pg_rls'   = no rewrite here (Postgres policies enforce); rewriter still runs as defense-in-depth.
"""
import json
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

_eng = create_engine(db_url, poolclass=NullPool)
_log = logging.getLogger(__name__)


def _load_config(project_slug: str) -> dict:
    # Try to select bypass_roles; fall back if column missing (pre-migration 098).
    with _eng.connect() as conn:
        try:
            row = conn.execute(text("""
                SELECT enabled, mode, user_attr_keys, table_filters, default_deny, bypass_roles
                FROM dash_project_rls_config WHERE project_slug=:s
            """), {"s": project_slug}).mappings().first()
        except Exception:
            row = conn.execute(text("""
                SELECT enabled, mode, user_attr_keys, table_filters, default_deny
                FROM dash_project_rls_config WHERE project_slug=:s
            """), {"s": project_slug}).mappings().first()
    if not row:
        return {"enabled": False}
    d = dict(row)
    tf = d.get("table_filters")
    if isinstance(tf, str):
        try:
            d["table_filters"] = json.loads(tf)
        except Exception:
            d["table_filters"] = {}
    uak = d.get("user_attr_keys")
    if isinstance(uak, str):
        try:
            d["user_attr_keys"] = json.loads(uak)
        except Exception:
            d["user_attr_keys"] = []
    br = d.get("bypass_roles")
    if isinstance(br, str):
        try:
            d["bypass_roles"] = json.loads(br)
        except Exception:
            d["bypass_roles"] = ["admin", "super_admin"]
    elif br is None:
        d["bypass_roles"] = ["admin", "super_admin"]
    return d


def _quote_lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def rewrite(sql: str, project_slug: str, user_attrs: dict | None) -> str:
    """Inject per-table WHERE filters. Returns modified SQL string.

    No-op if RLS disabled, mode=advisory, no filters, or no user_attrs.
    """
    cfg = _load_config(project_slug)
    if not cfg.get("enabled"):
        return sql
    if cfg.get("mode") == "advisory":
        return sql
    user_attrs = user_attrs or {}

    # Issue #2 — admin/bypass short-circuit before any rewrite logic.
    # Skip rewrite entirely if user role is in bypass_roles OR bypass_rls flag set.
    if user_attrs.get("bypass_rls") is True:
        return sql
    bypass_roles = cfg.get("bypass_roles") or ["admin", "super_admin"]
    _user_role = user_attrs.get("_user_role") or user_attrs.get("role")
    if _user_role and bypass_roles and _user_role in bypass_roles:
        return sql

    filters = cfg.get("table_filters") or {}
    if not filters:
        return sql

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        _log.warning("sqlglot missing; RLS rewrite skipped")
        return sql

    def _audit(blocked=False, block_reason=None, rewritten_sql=None):
        """Best-effort audit log; never raise."""
        try:
            from dash.rls.audit import log_rls_event
            try:
                from dash.tools.skill_refinery import get_external_user
                ext_user = get_external_user()
            except Exception:
                ext_user = None
            log_rls_event(
                project_slug=project_slug,
                original_sql=sql,
                rewritten_sql=rewritten_sql,
                mode=cfg.get("mode"),
                blocked=blocked,
                block_reason=block_reason,
                user_attrs=user_attrs,
                external_user=ext_user,
                embed_id=None,
            )
        except Exception:
            pass

    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
    except Exception as e:
        _log.warning(f"RLS rewrite parse failed: {e}; passthrough")
        if cfg.get("default_deny"):
            _audit(blocked=True, block_reason=f"parse failed: {e}")
            raise PermissionError(f"RLS: cannot parse SQL: {e}")
        return sql

    if parsed is None:
        return sql

    # Collect all CTE aliases up front. A reference to one of these in an
    # outer SELECT's FROM/JOIN must NOT be filtered — the CTE's inner SELECT
    # already had its base table filtered when it was visited.
    cte_aliases: set[str] = set()
    for cte in parsed.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            cte_aliases.add(alias)

    def _direct_tables(select_node):
        """Yield only Table nodes that are FROM/JOIN sources of THIS select.

        Avoids descending into subqueries, CTE definitions, or scalar-subqueries
        in the SELECT list — those have their own Select nodes that are visited
        separately by parsed.find_all(exp.Select). Also skips refs whose name
        matches a CTE alias (those are not base tables).
        """
        def _scan(root):
            for t in root.find_all(exp.Table):
                parent = t.parent
                inside_subselect = False
                while parent is not None and parent is not root:
                    if isinstance(parent, exp.Select) and parent is not select_node:
                        inside_subselect = True
                        break
                    parent = parent.parent
                if inside_subselect:
                    continue
                if t.name in cte_aliases:
                    continue
                yield t

        from_clause = select_node.args.get("from") or select_node.args.get("from_")
        if from_clause is not None:
            yield from _scan(from_clause)
        for jn in (select_node.args.get("joins") or []):
            yield from _scan(jn)

    any_added = False
    # For each Select node, find tables in its FROM/JOIN and inject combined WHERE.
    for select in parsed.find_all(exp.Select):
        added = []
        for src in _direct_tables(select):
            tname = src.name
            if not tname:
                continue
            flt = filters.get(tname) or filters.get(tname.lower())
            if not flt:
                continue
            # Bind user_attrs
            bound = str(flt)
            for k, v in user_attrs.items():
                bound = bound.replace(f":{k}", _quote_lit(v))
            # Check unbound :foo remained
            if ":" in bound and any(f":{k}" in bound for k in cfg.get("user_attr_keys", []) or []):
                if cfg.get("default_deny"):
                    _audit(blocked=True, block_reason=f"missing user attr for filter on {tname}")
                    raise PermissionError(f"RLS: missing user attr for filter on {tname}")
                continue
            added.append(f"({bound})")
        if added:
            any_added = True
            existing_where = select.args.get("where")
            combined = " AND ".join(added)
            if existing_where:
                new_cond = sqlglot.parse_one(
                    f"({existing_where.this.sql(dialect='postgres')}) AND ({combined})",
                    dialect="postgres",
                )
                select.set("where", exp.Where(this=new_cond))
            else:
                select.set(
                    "where",
                    exp.Where(this=sqlglot.parse_one(combined, dialect="postgres")),
                )
    result_sql = parsed.sql(dialect="postgres")
    if any_added:
        _audit(blocked=False, rewritten_sql=result_sql)
    return result_sql
