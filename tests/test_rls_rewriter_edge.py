"""Edge-case tests for RLS sqlglot rewriter.

Run: docker exec dash-api python -m pytest tests/test_rls_rewriter_edge.py -v
"""
import json, pytest
from unittest.mock import patch
from dash.rls import rewriter


def _cfg(filters: dict, mode: str = "rewrite", deny: bool = True, keys: list = None):
    return {
        "enabled": True, "mode": mode,
        "user_attr_keys": keys or list(set(
            k for f in filters.values() for k in _extract_binds(f)
        )),
        "table_filters": filters,
        "default_deny": deny,
    }

def _extract_binds(expr: str):
    import re
    return re.findall(r":([a-z_]+)", expr)


def _rewrite(filters, sql, attrs, mode="rewrite", deny=True, keys=None):
    cfg = _cfg(filters, mode=mode, deny=deny, keys=keys)
    with patch.object(rewriter, "_load_config", return_value=cfg):
        return rewriter.rewrite(sql, "test", attrs)


def test_cte_inner_table_filtered():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "WITH x AS (SELECT * FROM sales) SELECT * FROM x",
                   {"store_id": 1})
    assert "store_id = 1" in out
    assert out.lower().count("store_id = 1") == 1


def test_union_both_branches_filtered():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT id FROM sales UNION SELECT id FROM sales",
                   {"store_id": 1})
    assert out.lower().count("store_id = 1") == 2


def test_subquery_in_where():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM orders WHERE id IN (SELECT id FROM sales)",
                   {"store_id": 1})
    assert "store_id = 1" in out


def test_join_with_alias():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT s.amount FROM sales s JOIN inventory i ON s.id=i.sid",
                   {"store_id": 1})
    assert "store_id = 1" in out


def test_schema_qualified_table():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM public.sales",
                   {"store_id": 1})
    assert "store_id = 1" in out


def test_multiple_filters():
    out = _rewrite(
        {"sales": "store_id = :store_id", "inventory": "loc = :store_id"},
        "SELECT * FROM sales JOIN inventory USING (id)",
        {"store_id": 1},
    )
    assert "store_id = 1" in out
    assert "loc = 1" in out


def test_existing_where_preserved():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM sales WHERE region='west'",
                   {"store_id": 1})
    out_low = out.lower().replace(" ", "")
    assert "region='west'" in out_low
    assert "store_id=1" in out_low


def test_unfiltered_table_passthrough():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM other_table",
                   {"store_id": 1})
    assert "store_id" not in out.lower()


def test_quote_escape_prevents_injection():
    out = _rewrite({"sales": "name = :name"},
                   "SELECT * FROM sales",
                   {"name": "O'Brien"}, keys=["name"])
    assert "'O''Brien'" in out
    assert " OR 1=1" not in out.upper()


def test_boolean_attr():
    out = _rewrite({"sales": "active = :flag"},
                   "SELECT * FROM sales",
                   {"flag": True}, keys=["flag"])
    assert "active = true" in out.lower()


def test_none_attr():
    out = _rewrite({"sales": "deleted_at IS :del"},
                   "SELECT * FROM sales",
                   {"del": None}, keys=["del"])
    assert "NULL" in out


def test_numeric_attr_unquoted():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM sales",
                   {"store_id": 42})
    assert "store_id = 42" in out
    assert "'42'" not in out


def test_nested_or_preserved():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM sales WHERE (region='west' OR region='east')",
                   {"store_id": 1})
    out_n = out.lower()
    assert " or " in out_n
    assert "store_id = 1" in out_n


def test_parse_failure_default_deny_raises():
    with pytest.raises(Exception):
        _rewrite({"sales": "store_id = :store_id"},
                 "NOT A VALID SQL @#$",
                 {"store_id": 1}, deny=True)


def test_parse_failure_no_deny_passthrough():
    bad = "NOT A VALID SQL @#$"
    out = _rewrite({"sales": "store_id = :store_id"}, bad,
                   {"store_id": 1}, deny=False)
    assert out == bad


def test_advisory_mode_no_op():
    out = _rewrite({"sales": "store_id = :store_id"},
                   "SELECT * FROM sales",
                   {"store_id": 1}, mode="advisory")
    assert "store_id" not in out.lower()
