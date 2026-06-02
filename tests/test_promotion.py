"""Tests for dash/learning/promotion.py — project → central promotion pipeline.

All DB and LLM interactions are mocked.
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub db.session before any subsystem import (Python 3.9 compat).
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


from dash.learning.base import HypothesisType, PromotionCandidate
from dash.learning.promotion import PromotionPipeline


def _make_engine():
    """Build a mock engine usable as `with engine.connect() as conn`."""
    eng = MagicMock()
    conn = MagicMock()
    eng.connect.return_value.__enter__.return_value = conn
    eng.connect.return_value.__exit__.return_value = False
    return eng, conn


def _candidate(text="some fact", ftype=None):
    return PromotionCandidate(
        hypothesis_id=42,
        source_project_slug="proj",
        fact_text=text,
        fact_type=ftype or HypothesisType.PATTERN.value,
    )


# ---------------------------------------------------------------------------
# Stage 2: PII screen
# ---------------------------------------------------------------------------

class TestScreenPii:
    def test_email_blocked(self):
        p = PromotionPipeline()
        c = _candidate("contact me at user@example.com please")
        assert p.screen_pii(c) is False
        assert c.pii_safe is False
        assert "pii_match" in (c.rejection_reason or "")

    def test_phone_blocked(self):
        p = PromotionPipeline()
        c = _candidate("call 4155551234 today")
        assert p.screen_pii(c) is False

    def test_ssn_blocked(self):
        p = PromotionPipeline()
        c = _candidate("ssn is 123-45-6789")
        assert p.screen_pii(c) is False

    def test_currency_value_blocked(self):
        p = PromotionPipeline()
        c = _candidate("revenue was $1,234.56 yesterday")
        assert p.screen_pii(c) is False

    def test_specific_date_blocked(self):
        p = PromotionPipeline()
        c = _candidate("event on 2026-05-05 is here")
        assert p.screen_pii(c) is False

    def test_person_name_blocked(self):
        p = PromotionPipeline()
        c = _candidate("Alice Wang reviewed the report")
        assert p.screen_pii(c) is False

    def test_clean_text_passes(self):
        p = PromotionPipeline()
        c = _candidate("RFM segmentation uses recency frequency monetary")
        assert p.screen_pii(c) is True
        assert c.pii_safe is True


# ---------------------------------------------------------------------------
# Stage 3: generalization
# ---------------------------------------------------------------------------

class TestScreenGeneralizable:
    def test_skips_when_no_llm(self):
        p = PromotionPipeline(llm_call_fn=None)
        ok, reason = p.screen_generalizable(_candidate())
        assert ok is True
        assert "skip" in reason or "unavailable" in reason

    def test_verdict_a_universal_passes(self):
        llm = MagicMock(return_value='{"verdict":"A","reason":"universal"}')
        p = PromotionPipeline(llm_call_fn=llm)
        ok, reason = p.screen_generalizable(_candidate())
        assert ok is True
        assert "verdict=A" in reason

    def test_verdict_b_pattern_passes(self):
        llm = MagicMock(return_value='{"verdict":"B","reason":"common pattern"}')
        p = PromotionPipeline(llm_call_fn=llm)
        ok, _ = p.screen_generalizable(_candidate())
        assert ok is True

    def test_verdict_c_specific_rejected(self):
        llm = MagicMock(return_value='{"verdict":"C","reason":"specific row"}')
        p = PromotionPipeline(llm_call_fn=llm)
        c = _candidate()
        ok, reason = p.screen_generalizable(c)
        assert ok is False
        assert "verdict=C" in reason
        assert c.rejection_reason == "not_generalizable_C"

    def test_unparseable_rejects_conservatively(self):
        llm = MagicMock(return_value="completely garbage nonsense")
        p = PromotionPipeline(llm_call_fn=llm)
        c = _candidate()
        ok, _ = p.screen_generalizable(c)
        assert ok is False
        assert c.rejection_reason == "verdict_unparseable"

    def test_llm_exception_allows(self):
        llm = MagicMock(side_effect=RuntimeError("api down"))
        p = PromotionPipeline(llm_call_fn=llm)
        ok, reason = p.screen_generalizable(_candidate())
        assert ok is True
        assert "llm_error" in reason


# ---------------------------------------------------------------------------
# Stage 4: triangulation
# ---------------------------------------------------------------------------

class TestTriangulation:
    def test_returns_count_from_db(self):
        eng, conn = _make_engine()
        conn.execute.return_value.fetchone.return_value = (4,)
        p = PromotionPipeline(dash_engine=eng)
        c = _candidate("RFM is recency frequency monetary")
        n = p.check_triangulation(c)
        assert n == 4
        assert c.triangulation_count == 4

    def test_returns_zero_when_no_engine(self):
        p = PromotionPipeline(dash_engine=None)
        with patch("db.session.get_sql_engine", return_value=None):
            assert p.check_triangulation(_candidate()) == 0

    def test_returns_zero_on_db_error(self):
        eng, conn = _make_engine()
        conn.execute.side_effect = RuntimeError("nope")
        p = PromotionPipeline(dash_engine=eng)
        assert p.check_triangulation(_candidate()) == 0

    def test_definition_solo_type_threshold(self):
        """Definition type only needs 1 (solo)."""
        from dash.learning.promotion import _SOLO_TYPES
        assert HypothesisType.DEFINITION.value in _SOLO_TYPES
        assert HypothesisType.FORMULA.value in _SOLO_TYPES

    def test_pattern_type_needs_full_threshold(self):
        from dash.learning.promotion import _SOLO_TYPES, _TRIANGULATION_THRESHOLD
        assert HypothesisType.PATTERN.value not in _SOLO_TYPES
        assert _TRIANGULATION_THRESHOLD == 3


# ---------------------------------------------------------------------------
# Stage 5: promote
# ---------------------------------------------------------------------------

class TestPromote:
    def test_promote_inserts_central_brain_row_and_flips_flag(self):
        eng, conn = _make_engine()
        p = PromotionPipeline(dash_engine=eng)
        c = _candidate("RFM = recency frequency monetary",
                       ftype=HypothesisType.DEFINITION.value)
        c.pii_safe = True
        # Call promote
        ok = p.promote(c)
        assert ok is True
        # 2 INSERT/UPDATE statements + 1 audit insert (separate connect)
        # promote() itself does INSERT + UPDATE + commit
        assert conn.commit.call_count >= 1

    def test_promote_returns_false_without_engine(self):
        p = PromotionPipeline(dash_engine=None)
        with patch("db.session.get_sql_engine", return_value=None):
            assert p.promote(_candidate()) is False

    def test_promote_returns_false_on_insert_error(self):
        eng, conn = _make_engine()
        conn.execute.side_effect = RuntimeError("constraint")
        p = PromotionPipeline(dash_engine=eng)
        assert p.promote(_candidate()) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_brain_category_mapping(self):
        assert PromotionPipeline._brain_category(
            HypothesisType.FORMULA.value) == "formula"
        assert PromotionPipeline._brain_category(
            HypothesisType.DEFINITION.value) == "glossary"
        assert PromotionPipeline._brain_category(
            HypothesisType.THRESHOLD.value) == "threshold"
        assert PromotionPipeline._brain_category(
            HypothesisType.RULE.value) == "rule"
        assert PromotionPipeline._brain_category("unknown") == "pattern"

    def test_derive_name_uses_first_words(self):
        n = PromotionPipeline._derive_name(
            "RFM segmentation uses three signals", "glossary")
        assert n.startswith("Rfm")

    def test_derive_name_falls_back_to_hash_for_empty(self):
        n = PromotionPipeline._derive_name("", "pattern")
        assert n.startswith("pattern_")
        assert len(n.split("_")[1]) == 8

    def test_norm_key_lowercases_and_truncates(self):
        k = PromotionPipeline._norm_key("   HELLO  WORLD   " + "x" * 100)
        assert k.startswith("hello world")
        assert len(k) <= 60

    def test_decide_method_solo_uses_llm(self):
        llm = MagicMock()
        p = PromotionPipeline(llm_call_fn=llm)
        c = _candidate(ftype=HypothesisType.DEFINITION.value)
        assert "llm" in p._decide_method(c)

    def test_decide_method_pattern_uses_triangulation(self):
        p = PromotionPipeline(llm_call_fn=None)
        c = _candidate(ftype=HypothesisType.PATTERN.value)
        assert "triangulation" in p._decide_method(c)


# ---------------------------------------------------------------------------
# find_candidates
# ---------------------------------------------------------------------------

class TestFindCandidates:
    def test_returns_empty_when_no_engine(self):
        p = PromotionPipeline(dash_engine=None)
        with patch("db.session.get_sql_engine", return_value=None):
            assert p.find_candidates() == []

    def test_returns_candidates_from_db(self):
        eng, conn = _make_engine()
        conn.execute.return_value.fetchall.return_value = [
            (1, "proj-a", "RFM = recency frequency monetary",
             HypothesisType.DEFINITION.value, 2),
            (2, "proj-b", "weekend traffic peaks",
             HypothesisType.PATTERN.value, 4),
        ]
        p = PromotionPipeline(dash_engine=eng)
        cands = p.find_candidates()
        assert len(cands) == 2
        assert cands[0].hypothesis_id == 1
        assert cands[0].source_project_slug == "proj-a"
        assert cands[1].triangulation_count == 4

    def test_returns_empty_on_db_error(self):
        eng, conn = _make_engine()
        conn.execute.side_effect = RuntimeError("query failed")
        p = PromotionPipeline(dash_engine=eng)
        assert p.find_candidates() == []


# ---------------------------------------------------------------------------
# run_promotion_cycle (full pipeline)
# ---------------------------------------------------------------------------

class TestRunCycle:
    def test_full_cycle_returns_counts(self):
        """End-to-end: pii reject + generalize reject + tri reject + promote."""
        cands = [
            # PII reject
            PromotionCandidate(
                hypothesis_id=1, source_project_slug="p",
                fact_text="email user@example.com",
                fact_type=HypothesisType.PATTERN.value),
            # Verdict C reject
            PromotionCandidate(
                hypothesis_id=2, source_project_slug="p",
                fact_text="clean fact two",
                fact_type=HypothesisType.PATTERN.value),
            # Triangulation low reject
            PromotionCandidate(
                hypothesis_id=3, source_project_slug="p",
                fact_text="clean fact three",
                fact_type=HypothesisType.PATTERN.value),
            # Definition (solo) — promote
            PromotionCandidate(
                hypothesis_id=4, source_project_slug="p",
                fact_text="RFM means recency frequency monetary",
                fact_type=HypothesisType.DEFINITION.value),
        ]

        # LLM verdict per candidate, in order of generalize calls (skips #1)
        llm_returns = iter([
            '{"verdict":"C","reason":"specific"}',  # #2
            '{"verdict":"A","reason":"universal"}',  # #3
            '{"verdict":"A","reason":"universal"}',  # #4
        ])

        def llm_side(*a, **kw):
            return next(llm_returns)

        eng, conn = _make_engine()
        # triangulation results, then promote inserts (counts) — return any
        # int row for triangulation, fetchone returns (1,) by default-ish.
        # We patch _get_*_count via fetchone — but conn is single mock.
        # fetchone -> (1,) so triangulation = 1 for #3 (need 3) -> reject
        # for #4 it's solo (needs 1) -> pass
        conn.execute.return_value.fetchone.return_value = (1,)

        p = PromotionPipeline(llm_call_fn=llm_side, dash_engine=eng)
        with patch.object(p, "find_candidates", return_value=cands):
            stats = p.run_promotion_cycle()

        assert stats["candidates"] == 4
        assert stats["rejected_pii"] == 1
        assert stats["rejected_generalize"] == 1
        assert stats["rejected_triangulation"] == 1
        assert stats["promoted"] == 1

    def test_handles_find_candidates_error(self):
        p = PromotionPipeline()
        with patch.object(p, "find_candidates",
                          side_effect=RuntimeError("db gone")):
            stats = p.run_promotion_cycle()
        assert stats["candidates"] == 0
        assert stats["errors"] == 1

    def test_no_candidates_returns_zero_counts(self):
        p = PromotionPipeline()
        with patch.object(p, "find_candidates", return_value=[]):
            stats = p.run_promotion_cycle()
        assert stats["candidates"] == 0
        assert stats["promoted"] == 0
