"""Quick smoke test for the Layer 2 RLS SQL rewriter.

We monkey-patch `_load_config` to avoid hitting the database. The test only
runs if `sqlglot` is available; otherwise it skips.
"""
import pytest

sqlglot = pytest.importorskip("sqlglot")


def _stub_cfg(monkeypatch, **overrides):
    cfg = {
        "enabled": True,
        "mode": "rewrite",
        "user_attr_keys": ["store_id"],
        "table_filters": {"sales": "store_id = :store_id"},
        "default_deny": False,
    }
    cfg.update(overrides)
    from dash.rls import rewriter
    monkeypatch.setattr(rewriter, "_load_config", lambda slug: cfg)
    return rewriter


def test_rewrite_simple(monkeypatch):
    rewriter = _stub_cfg(monkeypatch)
    out = rewriter.rewrite(
        "SELECT * FROM sales WHERE date > '2024-01-01'",
        "demo",
        {"store_id": 42},
    )
    assert "store_id = 42" in out
    assert "date" in out  # original predicate preserved


def test_rewrite_no_filter_no_op(monkeypatch):
    rewriter = _stub_cfg(monkeypatch)
    sql = "SELECT 1 FROM other_table"
    out = rewriter.rewrite(sql, "demo", {"store_id": 42})
    # No filter for `other_table` → unchanged semantics (sqlglot may reformat,
    # but no store_id should leak in).
    assert "store_id" not in out


def test_advisory_mode_no_op(monkeypatch):
    rewriter = _stub_cfg(monkeypatch, mode="advisory")
    sql = "SELECT * FROM sales"
    assert rewriter.rewrite(sql, "demo", {"store_id": 42}) == sql


def test_disabled_no_op(monkeypatch):
    rewriter = _stub_cfg(monkeypatch, enabled=False)
    sql = "SELECT * FROM sales"
    assert rewriter.rewrite(sql, "demo", {"store_id": 42}) == sql


def test_default_deny_missing_attr(monkeypatch):
    rewriter = _stub_cfg(monkeypatch, default_deny=True)
    with pytest.raises(PermissionError):
        rewriter.rewrite("SELECT * FROM sales", "demo", {})  # no store_id
