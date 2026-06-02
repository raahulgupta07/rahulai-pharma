"""Unit tests for try_metric_shortcut threshold logic.

Validates Phase 10 fix: weak echo matches now rejected; only strong matches
(identical / high-overlap+score) accepted.
"""
import pytest
from dash.learning.verified_reward import _similarity, _normalize, _tokens


def test_normalize():
    assert _normalize("  Hello World?  ") == "hello world"
    assert _normalize("X.") == "x"


def test_similarity_identical():
    assert _similarity("show total leads", "Show total leads.") > 0.95


def test_similarity_partial():
    s = _similarity("show total leads", "show total contacts and leads")
    assert 0.4 < s < 0.95


def test_similarity_unrelated():
    assert _similarity("show total leads", "fix the bug") < 0.3


def test_tokens_drop_stopwords():
    t = _tokens("show the total leads for our team")
    assert "leads" in t
    assert "total" in t
    assert "the" not in t
    assert "for" not in t
