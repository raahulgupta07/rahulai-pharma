"""Tests for dash.skills.verifier (Phase 10B)."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from dash.skills import verifier as v


WELL_FORMED_MD = """---
name: pareto_revenue_breakdown
description: Compute Pareto 80/20 breakdown of revenue by SKU and flag the vital few.
allowed_tools:
  - run_sql_query
  - pareto_analysis
trigger_keywords:
  - pareto
  - 80/20
  - vital few
---

# Pareto Revenue Breakdown

When the user asks for an 80/20 / Pareto breakdown of revenue, call
`run_sql_query` to aggregate revenue per SKU and then `pareto_analysis`
to identify the vital few. Always report the share that drives 80% of
revenue and the long-tail count separately. Cite SKU IDs and never
fabricate numbers.

Output a [KPI:value|label|change] tag for total revenue and a
[CONFIDENCE:HIGH] tag when the dataset covers > 90 days.
"""

WELL_FORMED_FM = {
    "name": "pareto_revenue_breakdown",
    "description": "Compute Pareto 80/20 breakdown of revenue by SKU and flag the vital few.",
    "allowed_tools": ["run_sql_query", "pareto_analysis"],
    "trigger_keywords": ["pareto", "80/20", "vital few"],
}


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Default: EXPERIMENTAL_AGI=1, no DB, no LLM (stub)."""
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")
    monkeypatch.setattr(v, "_known_tool_names", lambda: set(v._KNOWN_TOOLS_FALLBACK))
    # Force LLM judge into the stub branch deterministically.
    monkeypatch.setattr(
        v, "_layer_llm_judge",
        lambda md, fm, ps: {"pass": True, "score": 0.8, "reason": "test stub",
                            "novelty": 0.8, "specificity": 0.8},
    )
    # No existing skills by default.
    import dash.skills.registry as reg
    monkeypatch.setattr(reg, "list_skills", lambda **kw: [])


def test_verifier_passes_on_well_formed_draft():
    r = v.verify_draft(WELL_FORMED_MD, WELL_FORMED_FM)
    assert r["ok"] is True
    assert r["passes"] is True
    assert r["recommendation"] == "promote"
    assert r["overall_score"] >= 0.7
    assert all(r["layers"][k]["pass"] for k in ("smoke", "reliability",
                                                 "llm_judge", "regression"))


def test_smoke_fails_on_empty_body():
    md = """---
name: x_skill
description: A description that is plenty long enough to clear the limit.
---

short
"""
    r = v.verify_draft(md, {"name": "x_skill",
                            "description": "A description that is plenty long enough to clear the limit."})
    assert r["layers"]["smoke"]["pass"] is False
    assert "body" in r["layers"]["smoke"]["reason"].lower()
    assert r["passes"] is False


def test_smoke_fails_on_missing_name():
    fm = {"description": "Long enough description to clear the floor."}
    r = v.verify_draft(WELL_FORMED_MD, fm)
    assert r["layers"]["smoke"]["pass"] is False
    assert "name" in r["layers"]["smoke"]["reason"].lower()
    assert r["passes"] is False


def test_reliability_flags_unknown_tool():
    fm = dict(WELL_FORMED_FM)
    fm["allowed_tools"] = ["run_sql_query", "totally_made_up_tool"]
    r = v.verify_draft(WELL_FORMED_MD, fm)
    rel = r["layers"]["reliability"]
    assert "totally_made_up_tool" in rel["reason"]
    assert rel["score"] == pytest.approx(0.5)


def test_llm_judge_stub_returns_half_when_unavailable(monkeypatch):
    # Undo the autouse stub: route through real _layer_llm_judge but kill imports.
    import importlib
    importlib.reload(v)
    monkeypatch.setenv("EXPERIMENTAL_AGI", "1")

    import sys
    sys.modules.pop("dash.settings", None)

    def _boom(*a, **k):  # pragma: no cover - defensive
        raise RuntimeError("no LLM in tests")

    # Patch the module reference verify_draft will use.
    fake = type(sys)("dash.settings")
    fake.training_llm_call = _boom  # type: ignore[attr-defined]
    sys.modules["dash.settings"] = fake

    result = v._layer_llm_judge(WELL_FORMED_MD, WELL_FORMED_FM, None)
    assert result["score"] == pytest.approx(0.5)
    assert result["novelty"] == pytest.approx(0.5)
    assert result["specificity"] == pytest.approx(0.5)
    assert "stub" in result["reason"]


def test_regression_detects_shadow(monkeypatch):
    import dash.skills.registry as reg
    monkeypatch.setattr(
        reg, "list_skills",
        lambda **kw: [{
            "id": "skl_existing", "name": "pareto_revenue_breakdown",
            "trigger_keywords": ["pareto", "80/20", "vital few"],
        }],
    )
    r = v.verify_draft(WELL_FORMED_MD, WELL_FORMED_FM)
    reg_layer = r["layers"]["regression"]
    assert reg_layer["pass"] is False
    assert "shadow" in reg_layer["reason"].lower()
    assert r["passes"] is False
    assert r["recommendation"] in ("reject", "review")


def test_overall_score_below_threshold_recommends_review(monkeypatch):
    # Force llm_judge to a low-pass score so weighted overall < 0.7.
    monkeypatch.setattr(
        v, "_layer_llm_judge",
        lambda md, fm, ps: {"pass": False, "score": 0.4, "reason": "weak",
                            "novelty": 0.4, "specificity": 0.4},
    )
    r = v.verify_draft(WELL_FORMED_MD, WELL_FORMED_FM)
    assert r["passes"] is False
    assert r["recommendation"] in ("review", "reject")
    assert any("llm_judge" in h or "novel" in h.lower() or "specific" in h.lower()
               for h in r["improvement_hints"])


def test_flag_off_returns_stub_pass(monkeypatch):
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    r = v.verify_draft("anything", {"name": "x"})
    assert r == {"ok": True, "passes": True, "overall_score": 1.0,
                 "layers": {}, "recommendation": "promote",
                 "improvement_hints": []}
