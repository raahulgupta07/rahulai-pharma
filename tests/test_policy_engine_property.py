"""Property-based tests for PolicyEngine (sqlglot-backed)."""
from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

from dash.policy.engine import PolicyEngine
from dash.policy.schema import AudienceRules, FieldRule, VisibilityPolicy


COL_POOL = ["a", "b", "c", "qty", "revenue", "salary", "name"]
TBL_POOL = ["t", "u", "sales", "emp", "inventory"]
INTENTS = ["network", "public"]
MASK_VAL = "***MASK***"


# ---- strategies -----------------------------------------------------------

@st.composite
def col_subset(draw, min_size=1, max_size=4):
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    return draw(st.lists(st.sampled_from(COL_POOL), min_size=n, max_size=n, unique=True))


@st.composite
def field_rule_strategy(draw):
    mode = draw(st.sampled_from(["full", "band", "mask", "hide"]))
    if mode == "band":
        return FieldRule(mode="band", bands=[{"name": "low", "max": 100}, {"name": "high"}])
    if mode == "mask":
        return FieldRule(mode="mask", mask_with=MASK_VAL)
    return FieldRule(mode=mode)


@st.composite
def policy_strategy(draw):
    cols = draw(col_subset(1, 5))
    rules = {c: draw(field_rule_strategy()) for c in cols}
    intent = draw(st.sampled_from(INTENTS))
    pol = VisibilityPolicy()
    setattr(pol, intent, AudienceRules(fields=rules))
    return pol, intent, rules


@st.composite
def simple_select(draw):
    cols = draw(col_subset(1, 4))
    tbl = draw(st.sampled_from(TBL_POOL))
    return f"SELECT {', '.join(cols)} FROM {tbl}", cols


@st.composite
def join_select(draw):
    cols = draw(col_subset(1, 3))
    return f"SELECT {', '.join(cols)} FROM t JOIN u ON t.id = u.id", cols


@st.composite
def cte_select(draw):
    cols = draw(col_subset(1, 3))
    proj = ", ".join(cols)
    return f"WITH x AS (SELECT {proj} FROM t) SELECT {proj} FROM x", cols


@st.composite
def any_select(draw):
    return draw(st.one_of(simple_select(), join_select(), cte_select()))


# ---- properties -----------------------------------------------------------

@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(any_select(), policy_strategy())
def test_apply_never_raises(sql_pair, pol_tuple):
    sql, _ = sql_pair
    pol, intent, _ = pol_tuple
    eng = PolicyEngine()
    out, downgraded = eng.apply(sql, pol, intent)
    assert isinstance(out, str)
    assert isinstance(downgraded, list)


@settings(max_examples=30, deadline=None)
@given(any_select(), policy_strategy())
def test_private_intent_passthrough(sql_pair, pol_tuple):
    sql, _ = sql_pair
    pol, _, _ = pol_tuple
    eng = PolicyEngine()
    out, downgraded = eng.apply(sql, pol, "private")
    assert out == sql
    assert downgraded == []


def _outer_projection_segment(sql: str) -> str:
    """Return the substring between the LAST 'SELECT' and the next 'FROM' at top level.

    Coarse: good enough to assert about outermost projections.
    """
    # Use last-select heuristic to find outer SELECT in CTE-style queries.
    m = list(re.finditer(r"\bSELECT\b", sql, re.IGNORECASE))
    if not m:
        return sql
    start = m[-1].end()
    fm = re.search(r"\bFROM\b", sql[start:], re.IGNORECASE)
    if not fm:
        return sql[start:]
    return sql[start:start + fm.start()]


@settings(max_examples=40, deadline=None)
@given(any_select(), policy_strategy())
def test_hide_drops_col_from_outer_projection(sql_pair, pol_tuple):
    sql, cols = sql_pair
    pol, intent, rules = pol_tuple
    eng = PolicyEngine()
    out, _ = eng.apply(sql, pol, intent)
    proj = _outer_projection_segment(out)
    # If hiding would drop ALL projected cols, engine leaves SQL unchanged (by design).
    hidden_in_query = [c for c, r in rules.items() if r.mode == "hide" and c in cols]
    if hidden_in_query and set(hidden_in_query) >= set(cols):
        return
    for col, rule in rules.items():
        if rule.mode != "hide":
            continue
        if col not in cols:
            continue
        # Bare `col` token should not appear as a standalone projection ident
        assert not re.search(rf"(^|[\s,]){re.escape(col)}(\s*,|\s*$)", proj.strip()), (
            f"hidden col {col} still in outer projection: {proj!r} (out={out!r})"
        )


@settings(max_examples=40, deadline=None)
@given(any_select(), policy_strategy())
def test_mask_inserts_literal(sql_pair, pol_tuple):
    sql, cols = sql_pair
    pol, intent, rules = pol_tuple
    eng = PolicyEngine()
    out, downgraded = eng.apply(sql, pol, intent)
    for col, rule in rules.items():
        if rule.mode != "mask" or col not in cols:
            continue
        assert MASK_VAL in out, f"mask literal missing for {col}: {out!r}"
        assert col in downgraded


@settings(max_examples=30, deadline=None)
@given(any_select(), policy_strategy())
def test_band_inserts_case(sql_pair, pol_tuple):
    sql, cols = sql_pair
    pol, intent, rules = pol_tuple
    eng = PolicyEngine()
    out, downgraded = eng.apply(sql, pol, intent)
    for col, rule in rules.items():
        if rule.mode != "band" or col not in cols:
            continue
        assert "CASE" in out.upper(), f"band CASE missing for {col}: {out!r}"
        assert col in downgraded


def test_alias_lookup_aggregate():
    """Direct test: SUM(qty) AS qty + band rule on qty → CASE in output."""
    eng = PolicyEngine()
    pol = VisibilityPolicy(
        public=AudienceRules(fields={
            "qty": FieldRule(mode="band", bands=[{"name": "low", "max": 10}, {"name": "high"}])
        })
    )
    out, downgraded = eng.apply("SELECT SUM(qty) AS qty FROM inventory", pol, "public")
    assert "CASE" in out.upper()
    assert "qty" in downgraded


def test_garbage_sql_passthrough():
    eng = PolicyEngine()
    pol = VisibilityPolicy(
        public=AudienceRules(fields={"a": FieldRule(mode="hide")})
    )
    out, downgraded = eng.apply("not even sql !!!", pol, "public")
    assert isinstance(out, str)
    assert downgraded == []


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-v"])
