from __future__ import annotations

import logging

from .schema import VisibilityPolicy, AudienceRules, FieldRule
from .transforms import band_expr, mask_expr

_log = logging.getLogger(__name__)


def _projection_key(proj) -> tuple[str | None, bool]:
    """Return (policy_lookup_key, is_aggregate) for a projection node.

    WHY alias-as-key: `SUM(qty) AS qty` should match a `qty` rule.
    Returns (None, _) if the projection is too complex to safely rewrite.
    """
    try:
        from sqlglot import expressions as exp
    except Exception:
        return None, False

    # Alias node: keep alias name as key.
    if isinstance(proj, exp.Alias):
        alias = proj.alias
        inner = proj.this
        is_agg = bool(inner.find(exp.AggFunc)) if inner is not None else False
        return alias or None, is_agg

    # Bare column ref: tbl.col or col
    if isinstance(proj, exp.Column):
        return proj.name, False

    # Star
    if isinstance(proj, exp.Star):
        return "*", False

    return None, False


def _build_band_case(col_sql: str, bands: list[dict]):
    """Build a sqlglot CASE expression by parsing band_expr SQL string.

    WHY: simpler than constructing exp.Case piece by piece; reuses
    transforms.py logic. We strip the trailing ` AS <name>` because
    callers wrap into Alias themselves.
    """
    import sqlglot
    raw = band_expr(col_sql, bands)
    # band_expr ends with " END AS col" — drop the alias for ast embedding
    idx = raw.rfind(" END AS ")
    case_sql = raw[: idx + 4] if idx != -1 else raw  # keep through " END"
    return sqlglot.parse_one(case_sql, dialect="postgres")


def _outer_select(tree):
    """Return the outermost Select node, or None.

    For `WITH ... SELECT ...` sqlglot returns the Select with a `with` arg;
    `tree` itself IS the Select. For UNION etc., bail (None).
    """
    try:
        from sqlglot import expressions as exp
    except Exception:
        return None
    if isinstance(tree, exp.Select):
        return tree
    return None


class PolicyEngine:
    def apply(
        self,
        sql: str,
        policy: VisibilityPolicy,
        intent: str,
    ) -> tuple[str, list[str]]:
        if intent == "private":
            return sql, []
        rules = getattr(policy, intent, None)
        if not isinstance(rules, AudienceRules) or not rules.fields:
            return sql, []

        try:
            import sqlglot
            from sqlglot import expressions as exp
        except Exception:
            _log.warning("sqlglot missing; policy engine passthrough")
            return sql, []

        try:
            tree = sqlglot.parse_one(sql.strip().rstrip(";"), dialect="postgres")
        except Exception as e:
            _log.warning(f"PolicyEngine parse failed: {e}; passthrough")
            return sql, []

        if tree is None:
            return sql, []

        try:
            select = _outer_select(tree)
            if select is None:
                return sql, []

            projs = list(select.expressions or [])
            if not projs:
                return sql, []

            # SELECT * → wrap original in subquery, project policy fields explicitly.
            if len(projs) == 1 and isinstance(projs[0], exp.Star):
                return self._handle_star(sql, rules)

            # Detect any aggregate-with-alias matching a policy field.
            # WHY: rewriting `CASE WHEN SUM(qty)<=10 ... END AS qty` is fine but
            # hide/mask of an aggregate alias is cleaner via subquery wrap.
            agg_alias_match = False
            for p in projs:
                key, is_agg = _projection_key(p)
                if key and is_agg and key in rules.fields:
                    agg_alias_match = True
                    break

            if agg_alias_match:
                return self._wrap_subquery(sql, rules, projs)

            return self._rewrite_inplace(tree, projs, rules)
        except Exception as e:
            _log.warning(f"PolicyEngine rewrite failed: {e}; passthrough")
            return sql, []

    # -- strategies ---------------------------------------------------------

    def _handle_star(self, sql: str, rules: AudienceRules) -> tuple[str, list[str]]:
        import sqlglot
        from sqlglot import expressions as exp

        downgraded: list[str] = []
        proj_parts: list[str] = []
        for col, rule in rules.fields.items():
            if rule.mode == "hide":
                downgraded.append(col)
                continue
            if rule.mode == "mask":
                proj_parts.append(mask_expr(col, rule.mask_with))
                downgraded.append(col)
                continue
            if rule.mode == "band":
                proj_parts.append(band_expr(col, rule.bands))
                downgraded.append(col)
                continue
            # full
            proj_parts.append(col)
        if not proj_parts:
            return sql, []
        wrapped = f"SELECT {', '.join(proj_parts)} FROM ({sql.rstrip(';').strip()}) _v"
        return wrapped, downgraded

    def _wrap_subquery(
        self,
        sql: str,
        rules: AudienceRules,
        projs,
    ) -> tuple[str, list[str]]:
        # Identify all alias names in inner query so we can project them
        from sqlglot import expressions as exp

        inner_names: list[str] = []
        for p in projs:
            key, _ = _projection_key(p)
            if key and key != "*":
                inner_names.append(key)

        downgraded: list[str] = []
        out_parts: list[str] = []
        for name in inner_names:
            rule = rules.fields.get(name)
            if rule is None or rule.mode == "full":
                out_parts.append(name)
                continue
            if rule.mode == "hide":
                downgraded.append(name)
                continue
            if rule.mode == "mask":
                out_parts.append(mask_expr(name, rule.mask_with))
                downgraded.append(name)
                continue
            if rule.mode == "band":
                out_parts.append(band_expr(name, rule.bands))
                downgraded.append(name)
                continue
            out_parts.append(name)

        if not out_parts:
            return sql, []
        wrapped = f"SELECT {', '.join(out_parts)} FROM ({sql.rstrip(';').strip()}) _v"
        return wrapped, downgraded

    def _rewrite_inplace(self, tree, projs, rules: AudienceRules) -> tuple[str, list[str]]:
        import sqlglot
        from sqlglot import expressions as exp

        downgraded: list[str] = []
        new_projs: list = []
        for p in projs:
            key, _ = _projection_key(p)
            if key is None or key == "*":
                new_projs.append(p)
                continue
            rule = rules.fields.get(key)
            if rule is None or rule.mode == "full":
                new_projs.append(p)
                continue

            if rule.mode == "hide":
                downgraded.append(key)
                continue  # drop projection

            if rule.mode == "mask":
                lit = exp.Literal.string(rule.mask_with)
                new_projs.append(exp.Alias(this=lit, alias=exp.to_identifier(key)))
                downgraded.append(key)
                continue

            if rule.mode == "band":
                # Build CASE over the underlying expression sql (so SUM(qty) etc work too).
                if isinstance(p, exp.Alias):
                    underlying_sql = p.this.sql(dialect="postgres")
                elif isinstance(p, exp.Column):
                    underlying_sql = p.sql(dialect="postgres")
                else:
                    underlying_sql = key
                case_node = _build_band_case(underlying_sql, rule.bands)
                new_projs.append(exp.Alias(this=case_node, alias=exp.to_identifier(key)))
                downgraded.append(key)
                continue

            new_projs.append(p)

        # If we'd drop ALL projections (e.g. all hides), leave SQL alone.
        if not new_projs:
            return tree.sql(dialect="postgres"), []

        tree.set("expressions", new_projs)
        return tree.sql(dialect="postgres"), downgraded
