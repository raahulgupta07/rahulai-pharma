"""SQL splitter — build per-source subqueries from federated AST.

Strategy:
  Input:  SELECT cols FROM source_a.t1 a JOIN source_b.t2 b ON a.x = b.y
          WHERE a.created > '2026-01-01' AND b.region = 'NA'

  Output: per-source subqueries with pushed-down WHERE clauses
          + list of join keys for in-memory merge.

  source_a:  SELECT a.x, a.created, a.<other_used_cols>
              FROM t1 a
              WHERE a.created > '2026-01-01'
  source_b:  SELECT b.y, b.region, b.<other_used_cols>
              FROM t2 b
              WHERE b.region = 'NA'

  In-memory: SELECT cols FROM source_a JOIN source_b ON a.x = b.y

Implementation: walk AST, group BY-clauses by source_a vs source_b.
Push down filters that reference only one source. Keep cross-source filters
for the final merge step.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class JoinKey:
    left_provider: str
    left_column: str          # qualified or alias.col
    right_provider: str
    right_column: str
    op: str = "="              # could be != / > / etc.


@dataclass
class SourceSubquery:
    provider_id: str
    sql: str                  # subquery in canonical dialect (postgres)
    columns_needed: list[str] = field(default_factory=list)
    pushed_filters: list[str] = field(default_factory=list)
    estimated_rows: int = 0    # set later by executor


@dataclass
class SplitPlan:
    subqueries: list[SourceSubquery] = field(default_factory=list)
    join_keys: list[JoinKey] = field(default_factory=list)
    final_select: str = ""    # SELECT clause for final merge
    final_where: str = ""      # cross-source WHERE filters
    final_order_by: str = ""
    final_limit: Optional[int] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def split(parsed) -> SplitPlan:
    """Given a ParsedFederatedSQL, return per-source subqueries + join plan.

    parsed: ParsedFederatedSQL from parser.parse()
    Returns SplitPlan with subqueries + join_keys + final_select + warnings.
    """
    plan = SplitPlan()

    if parsed.error:
        plan.error = parsed.error
        return plan

    if parsed.ast is None:
        # Fallback: simple per-table SELECT * (no AST means no real splitting)
        for ref in parsed.table_refs:
            plan.subqueries.append(SourceSubquery(
                provider_id=ref.provider_id,
                sql=f"SELECT * FROM {ref.table_name}",
                columns_needed=["*"],
            ))
        plan.warnings.append("no AST — fallback SELECT *")
        return plan

    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError:
        plan.error = "sqlglot required for splitter"
        return plan

    ast = parsed.ast

    # 1. Build alias → provider_id map
    alias_to_provider: dict[str, str] = {}
    alias_to_table: dict[str, str] = {}
    for ref in parsed.table_refs:
        if ref.alias:
            alias_to_provider[ref.alias] = ref.provider_id
            alias_to_table[ref.alias] = ref.table_name
        # Also map bare table name
        alias_to_provider[ref.table_name] = ref.provider_id
        alias_to_table[ref.table_name] = ref.table_name

    # 2. Per-provider column collection
    provider_cols: dict[str, set[str]] = {pid: set() for pid in parsed.provider_ids}

    # 2a. Walk SELECT projections
    for col in ast.find_all(exp.Column):
        table_alias = _column_table(col)
        if table_alias and table_alias in alias_to_provider:
            pid = alias_to_provider[table_alias]
            provider_cols[pid].add(col.name)

    # 3. Walk WHERE clause — separate single-source vs cross-source filters
    where_clause = ast.args.get("where")
    pushed_per_provider: dict[str, list[str]] = {pid: [] for pid in parsed.provider_ids}
    cross_source_filters: list[str] = []

    if where_clause:
        # Walk top-level conjunctions
        conditions = _split_and(where_clause.this)
        for cond in conditions:
            providers_in_cond = _providers_referenced(cond, alias_to_provider)
            cond_sql = cond.sql()
            if len(providers_in_cond) == 1:
                pid = list(providers_in_cond)[0]
                pushed_per_provider[pid].append(cond_sql)
                # Add referenced columns
                for c in cond.find_all(exp.Column):
                    talias = _column_table(c)
                    if talias and talias in alias_to_provider and alias_to_provider[talias] == pid:
                        provider_cols[pid].add(c.name)
            else:
                cross_source_filters.append(cond_sql)

    # 4. Walk JOIN ON clauses → JoinKeys
    join_clauses = list(ast.find_all(exp.Join))
    for j in join_clauses:
        on_clause = j.args.get("on")
        if on_clause is None:
            continue
        for eq in on_clause.find_all(exp.EQ):
            left = eq.args.get("this")
            right = eq.args.get("expression")
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            l_alias = _column_table(left)
            r_alias = _column_table(right)
            if not (l_alias and r_alias):
                continue
            l_pid = alias_to_provider.get(l_alias)
            r_pid = alias_to_provider.get(r_alias)
            if not (l_pid and r_pid):
                continue
            if l_pid == r_pid:
                continue   # same-source join, push down (handled in WHERE-style)
            plan.join_keys.append(JoinKey(
                left_provider=l_pid, left_column=left.name,
                right_provider=r_pid, right_column=right.name,
            ))
            provider_cols[l_pid].add(left.name)
            provider_cols[r_pid].add(right.name)

    # 5. Build per-source subqueries
    for ref in parsed.table_refs:
        pid = ref.provider_id
        cols = provider_cols.get(pid, set())
        if not cols:
            cols = {"*"}
        col_list = ", ".join(sorted(cols)) if "*" not in cols else "*"

        sql = f"SELECT {col_list} FROM {ref.table_name}"
        if ref.alias:
            sql += f" AS {ref.alias}"

        pushed = pushed_per_provider.get(pid, [])
        if pushed:
            sql += " WHERE " + " AND ".join(f"({p})" for p in pushed)

        plan.subqueries.append(SourceSubquery(
            provider_id=pid,
            sql=sql,
            columns_needed=sorted(cols),
            pushed_filters=pushed,
        ))

    # 6. Final merge SQL components
    select_clause = ast.args.get("expressions")
    if select_clause:
        plan.final_select = ", ".join(s.sql() for s in select_clause)

    if cross_source_filters:
        plan.final_where = " AND ".join(f"({c})" for c in cross_source_filters)

    order_by = ast.args.get("order")
    if order_by:
        plan.final_order_by = order_by.sql()

    limit = ast.args.get("limit")
    if limit:
        try:
            n = int(limit.expression.this)
            plan.final_limit = n
        except Exception:
            pass

    return plan


def _column_table(col) -> Optional[str]:
    """Extract the table/alias prefix of a sqlglot Column (or None)."""
    table = col.args.get("table")
    if table is None:
        return None
    if hasattr(table, "name"):
        return table.name
    return str(table)


def _split_and(node) -> list:
    """Recursively split AND-conjunctions into list of conditions."""
    try:
        from sqlglot import expressions as exp
    except ImportError:
        return [node] if node is not None else []

    out = []
    if isinstance(node, exp.And):
        out.extend(_split_and(node.this))
        out.extend(_split_and(node.expression))
    else:
        out.append(node)
    return out


def _providers_referenced(node, alias_to_provider: dict) -> set[str]:
    """Find all provider_ids referenced in an AST node via column.table aliases."""
    try:
        from sqlglot import expressions as exp
    except ImportError:
        return set()

    out = set()
    for col in node.find_all(exp.Column):
        table = _column_table(col)
        if table and table in alias_to_provider:
            out.add(alias_to_provider[table])
    return out
