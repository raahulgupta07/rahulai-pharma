"""MDL compiler — semantic SQL → raw SQL via sqlglot AST rewrite.

Reads `dash_metric_definitions` rows where model_name IS NOT NULL,
builds in-memory model registry, rewrites incoming SQL:

  - FROM <model_name>             → FROM <raw_table_ref>
  - SELECT <virtual_col>          → SELECT <expression> AS <virtual_col>
  - JOIN <model_name>             → JOIN <raw_table_ref>

Falls through unchanged for tables not in registry (back-compat with
non-MDL projects).

Reuses sqlglot pattern from `dash/rls/rewriter.py:95`. Same `sqlglot.exp`
AST walk, same fail-soft behavior (parse error → return original SQL).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 5-min in-memory cache keyed by project_slug
_MODEL_CACHE: dict[str, tuple[float, dict[str, dict]]] = {}
_CACHE_TTL_S = 300.0


def invalidate(slug: str) -> None:
    """Drop cached models for slug. Call after install_mdl / MDL row edit /
    golden promote so next compile sees fresh registry without 5-min TTL wait.
    Fail-soft: never raises.
    """
    try:
        _MODEL_CACHE.pop(slug, None)
    except Exception:
        pass


def detect_cycles(models: dict[str, dict]) -> list[list[str]]:
    """Find vcol→vcol cycles per model. Returns list of cycle paths.

    Each model's vcols form a DAG: vcol A's expression may reference vcol B by
    logical name. A cycle = A → B → A. Compiler iterative cap MAX_ITERS=4 will
    silently return non-fixed-point SQL on cycle — install-time detection
    prevents that.

    Pure string scan (no sqlglot needed). Token-match vcol names inside
    expressions. False-positive ok (over-flag) → reject cycle, force user to
    rename. False-negative bad → silent compile cap hit.
    """
    import re as _re
    cycles: list[list[str]] = []
    for mname, m in (models or {}).items():
        vcs = m.get("virtual_columns") or []
        if not vcs:
            continue
        # Build adjacency: vcol → set of vcols it references
        names = {vc["name"] for vc in vcs if vc.get("name")}
        adj: dict[str, set[str]] = {n: set() for n in names}
        for vc in vcs:
            n = vc.get("name")
            expr = vc.get("expression") or ""
            if not n or not expr:
                continue
            for tok in _re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", expr):
                if tok in names and tok != n:
                    adj[n].add(tok)
        # DFS cycle detect
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in names}
        stack: list[str] = []
        def dfs(u: str) -> None:
            color[u] = GRAY
            stack.append(u)
            for v in adj.get(u, ()):
                if color.get(v) == GRAY:
                    # back-edge → cycle from v..u..v
                    idx = stack.index(v)
                    cycles.append([f"{mname}.{x}" for x in stack[idx:]] + [f"{mname}.{v}"])
                elif color.get(v) == WHITE:
                    dfs(v)
            color[u] = BLACK
            stack.pop()
        for n in names:
            if color[n] == WHITE:
                dfs(n)
    return cycles


def load_models(slug: str, force: bool = False) -> dict[str, dict]:
    """Load MDL models for project. Returns {model_name: {raw_table, virtual_columns, relationships}}.

    Cached 5 min. Pass force=True to invalidate.
    """
    import time
    now = time.time()
    if not force:
        cached = _MODEL_CACHE.get(slug)
        if cached and cached[0] > now:
            return cached[1]

    models: dict[str, dict] = {}
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as cn:
            rows = cn.execute(text(
                "SELECT DISTINCT ON (model_name) model_name, raw_table_ref, "
                "       virtual_columns, relationships "
                "  FROM public.dash_metric_definitions "
                " WHERE project_slug = :s AND model_name IS NOT NULL "
                "   AND raw_table_ref IS NOT NULL "
                " ORDER BY model_name, updated_at DESC"
            ), {"s": slug}).mappings().fetchall()

        for r in rows:
            models[r["model_name"]] = {
                "raw_table": r["raw_table_ref"],
                "virtual_columns": r["virtual_columns"] or [],
                "relationships": r["relationships"] or [],
            }
    except Exception as e:
        logger.warning(f"load_models failed for {slug}: {e}")
        return {}

    _MODEL_CACHE[slug] = (now + _CACHE_TTL_S, models)
    return models


def compile_query(slug: str, semantic_sql: str, dialect: str = "postgres") -> str:
    """Rewrite semantic SQL → raw SQL using MDL registry.

    Spec rules:
      1. Replace table refs: model_name → raw_table_ref
      2. Replace virtual column refs in SELECT/WHERE with their expressions
      3. Virtual cols may reference OTHER virtual cols by their logical name
         (e.g., extended_value = qty * unit_cost). Iterative compile pass
         resolves chains. Hard cap MAX_ITERS to prevent infinite loops.
      4. Leave non-MDL tables untouched (back-compat)

    Returns original SQL on parse error or empty registry (fail-soft).
    """
    if not semantic_sql or not semantic_sql.strip():
        return semantic_sql

    models = load_models(slug)
    if not models:
        return semantic_sql  # no MDL for this project → pass through

    # Iterative fixed-point compile: handles vcol → vcol → raw chains.
    # 3 iterations enough for any realistic depth; cycle guard via no-change exit.
    MAX_ITERS = 4
    current = semantic_sql
    for _ in range(MAX_ITERS):
        next_sql = _compile_once(current, models, dialect=dialect)
        if next_sql == current:
            return next_sql  # fixed point reached
        current = next_sql
    # cap hit — likely vcol cycle or pathological chain. Log so ops can audit.
    logger.warning(
        f"MDL compile MAX_ITERS={MAX_ITERS} reached for slug={slug}; "
        f"possible vcol cycle. SQL head: {(semantic_sql or '')[:120]!r}"
    )
    return current


def _compile_once(sql: str, models: dict[str, dict], dialect: str = "postgres") -> str:
    """Single AST-rewrite pass. Returns possibly-changed SQL.

    `dialect` flows through sqlglot.parse_one + tree.sql; defaults postgres.
    Other accepted values: 'mysql', 'snowflake', 'bigquery', 'duckdb',
    'spark', 'trino'. Any sqlglot-supported dialect works; unknown values
    fall back to postgres via sqlglot's internal default.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        logger.warning("sqlglot missing; pass-through")
        return sql

    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        logger.debug(f"sqlglot parse failed ({e}); pass-through")
        return sql

    # Build vcol lookup: (model_name, vcol_name) → expression
    vcol_map: dict[tuple[str, str], str] = {}
    # Track raw_table → model_name so unqualified cols on raw tables (post first
    # iteration when FROM is already rewritten) still resolve.
    table_to_model: dict[str, str] = {}
    raw_table_to_model: dict[str, str] = {}

    for mname, m in models.items():
        raw_table_to_model[m["raw_table"]] = mname
        for vc in m.get("virtual_columns") or []:
            vcol_map[(mname, vc["name"])] = vc["expression"]

    # 1. Rewrite Table refs (only on first iter when name still matches model)
    for t in tree.find_all(exp.Table):
        name = t.name
        alias = t.alias_or_name
        if name in models:
            t.set("this", exp.to_identifier(models[name]["raw_table"]))
            table_to_model[alias] = name
        elif name in raw_table_to_model:
            # Already raw (later iter) — still need to track for col resolution
            table_to_model[alias] = raw_table_to_model[name]

    # 2. Rewrite vcol refs (Column nodes whose table maps to a model + vcol exists)
    for col in tree.find_all(exp.Column):
        col_name = col.name
        col_table = col.table

        model_name = None
        if col_table and col_table in table_to_model:
            model_name = table_to_model[col_table]
        elif not col_table and len(table_to_model) == 1:
            model_name = next(iter(table_to_model.values()))

        if model_name and (model_name, col_name) in vcol_map:
            expr_sql = vcol_map[(model_name, col_name)]
            try:
                expr_ast = sqlglot.parse_one(f"SELECT {expr_sql}", dialect="postgres")
                expr_node = expr_ast.expressions[0]
                col.replace(expr_node)
            except Exception as e:
                logger.debug(f"vcol expansion failed for {col_name}: {e}")

    return tree.sql(dialect=dialect)


def models_for_prompt(slug: str, max_chars: int = 2000) -> str:
    """Render MDL models as compact text for LLM context injection.

    Format (compact, ≤2000 chars):

        ## SEMANTIC MODELS (use these names, NOT raw columns)
        TABLE customer_calls (raw: crm_jun_2025):
          cols: customer_id, call_date, outcome, brand
          virtual: was_successful = (ot_cd = 'successful')
          joins: brands (many_to_one on brand = brands.code)
    """
    models = load_models(slug)
    if not models:
        return ""

    lines = ["## SEMANTIC MODELS (use these names, NOT raw columns)"]
    for mname, m in sorted(models.items()):
        lines.append(f"TABLE {mname} (raw: {m['raw_table']}):")
        vcs = m.get("virtual_columns") or []
        if vcs:
            for vc in vcs[:6]:
                lines.append(f"  virtual: {vc['name']} = {vc['expression']}")
        rels = m.get("relationships") or []
        for rel in rels[:4]:
            lines.append(f"  joins: {rel.get('model')} ({rel.get('type','?')} on {rel.get('on','?')})")
        if sum(len(l) for l in lines) > max_chars:
            lines.append(f"  ...(truncated)")
            break

    out = "\n".join(lines)
    return out[:max_chars]
