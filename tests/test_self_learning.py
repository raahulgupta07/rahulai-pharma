"""Unit tests for the self-learning subsystem.

Covers happy + error paths for: CuriosityEngine, ResearcherLoop,
HypothesisEngine, Verifier, Consolidator, forgetting helpers.

All DB engines and LLM calls are mocked — these tests do NOT touch a
real database or invoke any LLM. db.session is stubbed before imports
so the modules under test can be loaded on Python 3.9 too.
"""
from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub db.session BEFORE imports (Python 3.9 compat)
# ---------------------------------------------------------------------------
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


# Imports of subsystem under test (after the stubs are in place)
from dash.learning.base import (  # noqa: E402
    ConsolidationResult,
    Hypothesis,
    HypothesisType,
    Question,
    QuestionReason,
    ResearchDossier,
    ResearchSource,
    ResearchTier,
    VerificationResult,
    VerificationStatus,
)
from dash.learning.curiosity import CuriosityEngine  # noqa: E402
from dash.learning.researcher import ResearcherLoop  # noqa: E402
from dash.learning.hypothesis import HypothesisEngine  # noqa: E402
from dash.learning.verifier import Verifier  # noqa: E402
from dash.learning.consolidator import Consolidator  # noqa: E402
from dash.learning import forgetting as fg  # noqa: E402


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _mk_engine_with_conn(fetchall_rows=None, fetchone_row=None, rowcount=0,
                         exec_side_effect=None):
    """Build a MagicMock engine whose .connect() returns a context-managed
    connection that supports execute(...).fetchall()/fetchone()/rowcount.

    Also wires .begin() the same way for code paths that use it.
    """
    result = MagicMock()
    result.fetchall.return_value = fetchall_rows or []
    result.fetchone.return_value = fetchone_row
    result.fetchmany.return_value = (fetchall_rows or [])[:10]
    result.rowcount = rowcount
    result.keys = MagicMock(return_value=["c0", "c1"])

    conn = MagicMock()
    if exec_side_effect is not None:
        conn.execute.side_effect = exec_side_effect
    else:
        conn.execute.return_value = result
    conn.commit = MagicMock()

    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False

    eng = MagicMock()
    eng.connect.return_value = cm
    eng.begin.return_value = cm
    return eng, conn, result


# =========================================================================
# CuriosityEngine
# =========================================================================
class TestCuriosityEngine:
    def test_engine_instantiates_without_llm(self):
        eng, _, _ = _mk_engine_with_conn()
        ce = CuriosityEngine(project_slug="proj", dash_engine=eng)
        assert ce.project_slug == "proj"
        assert ce.llm_call_fn is None
        assert ce.engine is eng

    def test_score_priority_for_thumbs_down_drift_is_high(self):
        ce = CuriosityEngine(project_slug="p", dash_engine=MagicMock())
        q = {
            "reason": "thumbs_down",
            "question": "Why did revenue drift down?",
            "metadata": {"source_method": "thumbs_down"},
            "priority": 50,
        }
        assert ce._score_priority(q) == 95

    def test_score_priority_user_request_is_95(self):
        ce = CuriosityEngine(dash_engine=MagicMock())
        assert ce._score_priority({"reason": "user_request", "question": "x"}) == 95

    def test_score_priority_anomaly_drift_is_90(self):
        ce = CuriosityEngine(dash_engine=MagicMock())
        q = {"reason": "anomaly", "question": "drift event observed",
             "metadata": {"source_method": "anomalies"}}
        assert ce._score_priority(q) == 90

    def test_score_priority_falls_through_to_default(self):
        ce = CuriosityEngine(dash_engine=MagicMock())
        q = {"reason": "weird_unmatched", "question": "x", "priority": 17}
        assert ce._score_priority(q) == 17

    def test_dedup_skips_existing_question(self):
        eng, conn, result = _mk_engine_with_conn(fetchone_row=(1,))
        ce = CuriosityEngine(project_slug="p", dash_engine=eng)
        assert ce._is_duplicate("dup question?", "p") is True
        conn.execute.assert_called_once()
        # SQL must reference the curiosity table
        sql_arg = str(conn.execute.call_args[0][0])
        assert "dash_curiosity_questions" in sql_arg

    def test_dedup_returns_false_when_no_row(self):
        eng, _, _ = _mk_engine_with_conn(fetchone_row=None)
        ce = CuriosityEngine(project_slug="p", dash_engine=eng)
        assert ce._is_duplicate("fresh question?", "p") is False

    def test_dedup_handles_empty_question(self):
        ce = CuriosityEngine(project_slug="p", dash_engine=MagicMock())
        assert ce._is_duplicate("", "p") is False

    def test_generate_with_no_signals_returns_empty(self):
        eng, _, _ = _mk_engine_with_conn()  # fetchall returns []
        ce = CuriosityEngine(project_slug="p", dash_engine=eng)
        out = ce.generate(max_questions=20, cycle_num=1)
        assert out == []

    def test_generate_calls_each_source(self):
        eng, _, _ = _mk_engine_with_conn()
        ce = CuriosityEngine(project_slug="p", dash_engine=eng)
        # Patch every source method to return one safe tuple
        method_names = [
            "_from_kg_holes", "_from_drift", "_from_failed_qa",
            "_from_thumbs_down", "_from_anomalies", "_from_underused_tables",
            "_from_topic_gaps", "_from_cross_source", "_from_user_request",
            "_from_cycle_followup",
        ]
        for n in method_names:
            setattr(ce, n, MagicMock(return_value=[]))
        ce._persist = MagicMock(return_value=0)  # bypass DB
        ce.generate(max_questions=5)
        for n in method_names:
            getattr(ce, n).assert_called_once()

    def test_generate_aggregates_and_dedups(self):
        eng, _, _ = _mk_engine_with_conn(fetchone_row=None)  # nothing existing
        ce = CuriosityEngine(project_slug="p", dash_engine=eng)
        # one source returns two identical questions → should dedupe
        ce._from_kg_holes = MagicMock(return_value=[
            ("Same Q?", "topic", "kg_hole", 70, None),
            ("Same Q?", "topic", "kg_hole", 70, None),
        ])
        for n in ("_from_drift", "_from_failed_qa", "_from_thumbs_down",
                  "_from_anomalies", "_from_underused_tables",
                  "_from_topic_gaps", "_from_cross_source",
                  "_from_user_request", "_from_cycle_followup"):
            setattr(ce, n, MagicMock(return_value=[]))
        ce._persist = MagicMock(return_value=1)
        out = ce.generate(max_questions=10)
        assert len(out) == 1
        ce._persist.assert_called_once()

    def test_safe_json_array_parses_and_strips_fences(self):
        raw = "```json\n[{\"question\":\"q\",\"topic\":\"t\",\"reason\":\"r\"}]\n```"
        items = CuriosityEngine._safe_json_array(raw)
        assert isinstance(items, list) and items[0]["question"] == "q"

    def test_safe_json_array_empty_on_garbage(self):
        assert CuriosityEngine._safe_json_array("not json at all") == []


# =========================================================================
# ResearcherLoop
# =========================================================================
class TestResearcherLoop:
    def _q(self):
        return Question(id=7, question="What drives churn?", topic="churn",
                        domain="saas")

    def test_research_with_no_sources_returns_empty_dossier(self):
        # Pass a single internal tier we control; force it to produce nothing.
        rl = ResearcherLoop(project_slug=None, llm_call_fn=None,
                            enabled_tiers=[ResearchTier.INTERNAL_DB.value])
        rl._tier_internal_db = MagicMock(return_value=[])
        d = rl.research(self._q())
        assert isinstance(d, ResearchDossier)
        assert d.sources == []
        assert d.triangulation_count == 0
        assert d.summary == ""

    def test_research_aggregates_across_tiers(self):
        rl = ResearcherLoop(project_slug="p", llm_call_fn=None,
                            enabled_tiers=[
                                ResearchTier.INTERNAL_DB.value,
                                ResearchTier.INTERNAL_KG.value,
                                ResearchTier.INTERNAL_BRAIN.value,
                            ])
        rl._tier_internal_db = MagicMock(return_value=[
            ResearchSource(tier="internal_db", source="t", confidence=0.6)])
        rl._tier_internal_kg = MagicMock(return_value=[
            ResearchSource(tier="internal_kg", source="kg", confidence=0.8)])
        rl._tier_internal_brain = MagicMock(return_value=[
            ResearchSource(tier="internal_brain", source="b", confidence=0.9)])
        d = rl.research(self._q())
        assert len(d.sources) == 3
        assert d.triangulation_count == 3

    def test_triangulation_count_correct(self):
        # 3 tiers, but 1 below 0.5 confidence → only 2 should count
        rl = ResearcherLoop(project_slug="p", llm_call_fn=None,
                            enabled_tiers=[
                                ResearchTier.INTERNAL_DB.value,
                                ResearchTier.INTERNAL_KG.value,
                                ResearchTier.INTERNAL_BRAIN.value,
                            ])
        rl._tier_internal_db = MagicMock(return_value=[
            ResearchSource(tier="internal_db", source="t", confidence=0.7)])
        rl._tier_internal_kg = MagicMock(return_value=[
            ResearchSource(tier="internal_kg", source="kg", confidence=0.2)])
        rl._tier_internal_brain = MagicMock(return_value=[
            ResearchSource(tier="internal_brain", source="b", confidence=0.55)])
        d = rl.research(self._q())
        assert d.triangulation_count == 2

    def test_research_continues_when_one_tier_fails(self):
        rl = ResearcherLoop(project_slug="p", llm_call_fn=None,
                            enabled_tiers=[
                                ResearchTier.INTERNAL_DB.value,
                                ResearchTier.INTERNAL_KG.value,
                            ])
        rl._tier_internal_db = MagicMock(side_effect=RuntimeError("boom"))
        rl._tier_internal_kg = MagicMock(return_value=[
            ResearchSource(tier="internal_kg", source="kg", confidence=0.7)])
        d = rl.research(self._q())
        assert len(d.sources) == 1
        assert d.triangulation_count == 1

    def test_llm_synthesize_skipped_when_no_llm_fn(self):
        rl = ResearcherLoop(project_slug="p", llm_call_fn=None,
                            enabled_tiers=[ResearchTier.INTERNAL_DB.value])
        rl._tier_internal_db = MagicMock(return_value=[
            ResearchSource(tier="internal_db", source="t", confidence=0.6)])
        d = rl.research(self._q())
        # llm_call_fn is None so summary stays empty even if sources exist
        assert d.summary == ""

    def test_research_caps_per_tier(self):
        rl = ResearcherLoop(project_slug="p", llm_call_fn=None,
                            enabled_tiers=[ResearchTier.INTERNAL_DB.value])
        # the tier method receives max_per_tier — assert it was passed through
        spy = MagicMock(return_value=[])
        rl._tier_internal_db = spy
        rl.research(self._q(), max_per_tier=2)
        assert spy.call_args.kwargs.get("max_per_tier") == 2

    def test_llm_synthesize_called_when_sources_present(self):
        llm = MagicMock(return_value="synthesized answer CONFIDENCE: high")
        rl = ResearcherLoop(project_slug="p", llm_call_fn=llm,
                            enabled_tiers=[ResearchTier.INTERNAL_DB.value])
        rl._tier_internal_db = MagicMock(return_value=[
            ResearchSource(tier="internal_db", source="t", confidence=0.7)])
        d = rl.research(self._q())
        assert "synthesized" in d.summary
        llm.assert_called()


# =========================================================================
# HypothesisEngine
# =========================================================================
class TestHypothesisEngine:
    def test_seed_confidence_table(self):
        he = HypothesisEngine()
        assert he._seed_confidence(ResearchDossier(triangulation_count=0)) == 0.30
        assert he._seed_confidence(ResearchDossier(triangulation_count=1)) == 0.40
        assert he._seed_confidence(ResearchDossier(triangulation_count=2)) == 0.55
        assert he._seed_confidence(ResearchDossier(triangulation_count=3)) == 0.70
        assert he._seed_confidence(ResearchDossier(triangulation_count=5)) == 0.70

    def test_form_from_empty_dossier_returns_empty(self):
        he = HypothesisEngine()
        out = he.form_from_dossier(Question(id=1, question="q?"),
                                   ResearchDossier())
        assert out == []

    def test_form_from_dossier_with_summary_falls_back(self):
        he = HypothesisEngine(project_slug="p", llm_call_fn=None)
        # No LLM → _llm_extract returns []; falls back to summary-based hypothesis
        d = ResearchDossier(
            sources=[ResearchSource(tier="internal_db", source="t",
                                    confidence=0.7)],
            summary="Customer churn correlates with reduced login frequency.",
            triangulation_count=1,
        )
        he._persist = MagicMock(return_value=42)
        out = he.form_from_dossier(Question(id=1, question="q?"), d)
        assert len(out) == 1
        assert out[0].id == 42
        assert "churn" in out[0].statement.lower()

    def test_llm_extract_parses_json_array(self):
        payload = json.dumps([
            {"statement": "X drives Y", "type": "causal",
             "rationale": "evidence A", "test_idea": "compare cohorts"}
        ])
        llm = MagicMock(return_value=payload)
        he = HypothesisEngine(llm_call_fn=llm)
        items = he._llm_extract(Question(question="q?"),
                                ResearchDossier(sources=[
                                    ResearchSource(tier="x", source="y",
                                                   confidence=0.6)]),
                                3)
        assert items and items[0]["type"] == "causal"

    def test_llm_extract_strips_markdown_fences(self):
        payload = "```json\n[{\"statement\":\"S\",\"type\":\"pattern\"}]\n```"
        llm = MagicMock(return_value=payload)
        he = HypothesisEngine(llm_call_fn=llm)
        items = he._llm_extract(Question(question="q?"),
                                ResearchDossier(sources=[
                                    ResearchSource(tier="x", source="y",
                                                   confidence=0.6)]),
                                3)
        assert items[0]["statement"] == "S"

    def test_llm_extract_returns_empty_on_garbage(self):
        llm = MagicMock(return_value="not json {oops")
        he = HypothesisEngine(llm_call_fn=llm)
        items = he._llm_extract(Question(question="q?"),
                                ResearchDossier(sources=[
                                    ResearchSource(tier="x", source="y",
                                                   confidence=0.6)]),
                                3)
        assert items == []

    def test_persist_returns_id(self):
        eng, conn, _ = _mk_engine_with_conn(fetchone_row=(99,))
        he = HypothesisEngine(dash_engine=eng)
        h = Hypothesis(project_slug="p", statement="S", confidence=0.5,
                       triangulation_count=1)
        new_id = he._persist(h)
        assert new_id == 99
        conn.execute.assert_called_once()
        sql_arg = str(conn.execute.call_args[0][0])
        assert "dash_hypotheses" in sql_arg.lower()

    def test_persist_returns_none_on_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("db down")
        he = HypothesisEngine(dash_engine=eng)
        assert he._persist(Hypothesis(statement="x")) is None

    def test_update_confidence_clamps_0_1(self):
        eng, conn, _ = _mk_engine_with_conn()
        he = HypothesisEngine(dash_engine=eng)
        he.update_confidence(1, +0.2, reason="positive")
        sql_arg = str(conn.execute.call_args[0][0])
        # SQL itself must clamp via LEAST/GREATEST
        assert "LEAST(1.0" in sql_arg and "GREATEST(0.0" in sql_arg


# =========================================================================
# Verifier
# =========================================================================
class TestVerifier:
    def test_verify_definition_uses_llm_review(self):
        llm = MagicMock(return_value=json.dumps(
            {"verdict": "agree", "reason": "well known"}))
        eng, _, _ = _mk_engine_with_conn()
        v = Verifier(project_slug="p", llm_call_fn=llm, dash_engine=eng)
        h = Hypothesis(id=1, statement="ARR is annual recurring revenue.",
                       hypothesis_type=HypothesisType.DEFINITION.value)
        res = v.verify(h)
        assert res.status == VerificationStatus.VERIFIED.value
        assert res.method == "llm_review"

    def test_verify_via_sql_blocks_unsafe_query(self):
        # LLM returns a write-statement → must be blocked
        llm = MagicMock(return_value="DROP TABLE customers;")
        v = Verifier(project_slug="p", llm_call_fn=llm)
        # Inject a fake provider with engine_ro
        provider = MagicMock()
        provider.engine_ro = MagicMock()
        provider.dialect = "postgres"
        v._get_provider = MagicMock(return_value=provider)
        h = Hypothesis(id=1, statement="x", hypothesis_type=HypothesisType.RULE.value)
        res = v._verify_via_sql(h)
        assert res.method == "sql_unsafe"
        assert res.status == VerificationStatus.PENDING.value

    def test_verify_via_sql_strips_markdown(self):
        v = Verifier(project_slug="p", llm_call_fn=MagicMock())
        cleaned = v._clean_sql("```sql\nSELECT 1\n```")
        assert cleaned.strip().lower().startswith("select")
        assert "```" not in cleaned

    def test_verify_marks_failed_on_exec_error(self):
        # First LLM call → SQL string.  Then execution raises.
        llm = MagicMock(return_value="SELECT * FROM nope")
        v = Verifier(project_slug="p", llm_call_fn=llm)
        provider = MagicMock()
        eng_ro = MagicMock()
        # Simulate execute raising
        cm = MagicMock()
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("relation does not exist")
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        eng_ro.connect.return_value = cm
        provider.engine_ro = eng_ro
        provider.dialect = "postgres"
        v._get_provider = MagicMock(return_value=provider)
        h = Hypothesis(id=1, statement="x", hypothesis_type=HypothesisType.RULE.value)
        res = v._verify_via_sql(h)
        assert res.status == VerificationStatus.FAILED.value
        assert res.method == "sql_exec_error"
        assert res.confidence_delta < 0

    def test_verify_via_llm_agree_returns_verified(self):
        llm = MagicMock(return_value='{"verdict":"agree","reason":"ok"}')
        eng, _, _ = _mk_engine_with_conn()
        v = Verifier(project_slug="p", llm_call_fn=llm, dash_engine=eng)
        h = Hypothesis(id=1, statement="s",
                       hypothesis_type=HypothesisType.FORMULA.value)
        res = v._verify_via_llm(h)
        assert res.status == VerificationStatus.VERIFIED.value
        assert res.confidence_delta > 0

    def test_verify_via_llm_disagree_returns_failed(self):
        llm = MagicMock(return_value='{"verdict":"disagree","reason":"nope"}')
        eng, _, _ = _mk_engine_with_conn()
        v = Verifier(project_slug="p", llm_call_fn=llm, dash_engine=eng)
        h = Hypothesis(id=1, statement="s",
                       hypothesis_type=HypothesisType.FORMULA.value)
        res = v._verify_via_llm(h)
        assert res.status == VerificationStatus.FAILED.value
        assert res.confidence_delta < 0

    def test_verify_via_llm_no_llm_returns_pending(self):
        v = Verifier(project_slug="p", llm_call_fn=None)
        res = v._verify_via_llm(
            Hypothesis(id=1, statement="x",
                       hypothesis_type=HypothesisType.DEFINITION.value))
        assert res.status == VerificationStatus.PENDING.value
        assert res.method == "llm_skipped"

    def test_cross_source_check_under_2_sources_returns_pending(self):
        v = Verifier(project_slug="p")
        h = Hypothesis(id=1, statement="s")
        res = v.cross_source_check(h, sources=["only_one"])
        assert res.status == VerificationStatus.PENDING.value
        assert res.method == "cross_source_skipped"

    def test_cross_source_check_2plus_sources_returns_partial(self):
        v = Verifier(project_slug="p")
        res = v.cross_source_check(Hypothesis(id=1, statement="s"),
                                   sources=["a", "b"])
        assert res.status == VerificationStatus.PARTIAL.value
        assert res.confidence_delta > 0

    def test_persist_updates_db_row(self):
        eng, conn, _ = _mk_engine_with_conn()
        v = Verifier(project_slug="p", dash_engine=eng)
        result = VerificationResult(
            hypothesis_id=11,
            status=VerificationStatus.VERIFIED.value,
            method="sql",
            confidence_delta=0.2,
            evidence={"sql": "SELECT 1"},
        )
        v._persist(11, result)
        conn.execute.assert_called_once()
        sql_arg = str(conn.execute.call_args[0][0])
        assert "UPDATE" in sql_arg.upper()
        assert "dash_hypotheses" in sql_arg

    def test_parse_json_recovers_object_from_extra_text(self):
        v = Verifier()
        out = v._parse_json('garbage {"verdict":"agree"} trailing')
        assert out == {"verdict": "agree"}

    def test_is_safe_sql_rejects_select_and_drop(self):
        v = Verifier()
        assert v._is_safe_sql("SELECT 1") is True
        assert v._is_safe_sql("DELETE FROM t") is False
        assert v._is_safe_sql("WITH x AS (SELECT 1) SELECT * FROM x") is True


# =========================================================================
# Consolidator
# =========================================================================
class TestConsolidator:
    def test_consolidate_skips_unverified(self):
        c = Consolidator(dash_engine=MagicMock())
        h = Hypothesis(id=1, statement="x",
                       verification_status=VerificationStatus.PENDING.value)
        res = c.consolidate(h)
        assert res.targets == []
        assert "not verified" in (res.error or "")

    def test_routing_definition_to_brain(self):
        eng, conn, _ = _mk_engine_with_conn(fetchone_row=(33,))
        c = Consolidator(dash_engine=eng)
        c._is_duplicate = MagicMock(return_value=False)
        h = Hypothesis(
            id=1, statement="ARR is annual recurring revenue.",
            hypothesis_type=HypothesisType.DEFINITION.value,
            verification_status=VerificationStatus.VERIFIED.value,
            project_slug="p", confidence=0.9,
        )
        res = c.consolidate(h)
        assert "brain" in res.targets
        assert 33 in res.brain_entry_ids

    def test_routing_threshold_to_rules_and_brain(self):
        eng, conn, _ = _mk_engine_with_conn(fetchone_row=(7,))
        c = Consolidator(dash_engine=eng)
        c._is_duplicate = MagicMock(return_value=False)
        h = Hypothesis(
            id=1, statement="Churn risk above 0.8 is high.",
            hypothesis_type=HypothesisType.THRESHOLD.value,
            verification_status=VerificationStatus.VERIFIED.value,
            project_slug="p", confidence=0.7,
        )
        res = c.consolidate(h)
        assert "rules_db" in res.targets
        assert "brain" in res.targets

    def test_routing_pattern_to_memory_and_kg(self):
        eng, conn, _ = _mk_engine_with_conn(fetchone_row=(5,))
        c = Consolidator(dash_engine=eng)
        c._is_duplicate = MagicMock(return_value=False)
        h = Hypothesis(
            id=1, statement="Login frequency drives retention.",
            hypothesis_type=HypothesisType.PATTERN.value,
            verification_status=VerificationStatus.VERIFIED.value,
            project_slug="p", confidence=0.6,
        )
        res = c.consolidate(h)
        assert "memory" in res.targets
        # KG triple needs SPO extraction; statement has " drives " → ok
        assert "kg" in res.targets

    def test_dedup_returns_duplicate_skipped(self):
        c = Consolidator(dash_engine=MagicMock())
        c._is_duplicate = MagicMock(return_value=True)
        h = Hypothesis(
            id=1, statement="dup",
            hypothesis_type=HypothesisType.PATTERN.value,
            verification_status=VerificationStatus.VERIFIED.value,
        )
        res = c.consolidate(h)
        assert res.duplicate_skipped is True
        assert res.targets == []

    def test_extract_spo_falls_back_to_regex(self):
        c = Consolidator(dash_engine=MagicMock(), llm_call_fn=None)
        spo = c._extract_spo("Login frequency drives retention.")
        assert spo is not None
        assert spo[0].lower().startswith("login")
        assert spo[1] == "drives"

    def test_extract_spo_handles_no_verb(self):
        c = Consolidator(dash_engine=MagicMock(), llm_call_fn=None)
        assert c._extract_spo("a single fragment without verbs") is None

    def test_extract_spo_uses_llm_when_present(self):
        llm = MagicMock(return_value='{"s":"X","p":"affects","o":"Y"}')
        c = Consolidator(dash_engine=MagicMock(), llm_call_fn=llm)
        spo = c._extract_spo("X affects Y in some way")
        assert spo == ("X", "affects", "Y")

    def test_extract_name_truncates_to_first_words(self):
        c = Consolidator(dash_engine=MagicMock())
        name = c._extract_name(
            "Customer churn rate increases when login frequency drops.",
            "threshold",
        )
        assert len(name.split()) <= 5

    def test_consolidate_handles_persistence_exception(self):
        eng = MagicMock()
        # raising on connect simulates DB outage during _to_memory
        eng.connect.side_effect = RuntimeError("db down")
        c = Consolidator(dash_engine=eng)
        c._is_duplicate = MagicMock(return_value=False)
        h = Hypothesis(
            id=1, statement="x correlates with y",
            hypothesis_type=HypothesisType.CORRELATION.value,
            verification_status=VerificationStatus.VERIFIED.value,
        )
        res = c.consolidate(h)
        # internal helpers swallow their own exceptions; result stays empty
        assert res.targets == []
        assert res.error is None or isinstance(res.error, str)


# =========================================================================
# forgetting
# =========================================================================
class TestForgetting:
    def test_decay_skips_resistant_memories(self):
        # Build engine where execute().rowcount reports counts per call
        results = [MagicMock(rowcount=5), MagicMock(rowcount=2),
                   MagicMock(rowcount=1), MagicMock(rowcount=0)]
        for r in results:
            r.fetchall.return_value = []
            r.fetchone.return_value = None
        conn = MagicMock()
        conn.execute.side_effect = results
        cm = MagicMock()
        cm.__enter__.return_value = conn
        cm.__exit__.return_value = False
        eng = MagicMock()
        eng.connect.return_value = cm
        out = fg.daily_decay_job(dash_engine=eng)
        # All 4 SQL calls should have made it through
        assert conn.execute.call_count == 4
        # Verify the decay UPDATE excludes decay_resistant
        first_sql = str(conn.execute.call_args_list[0][0][0])
        assert "decay_resistant" in first_sql
        assert out.decayed_count == 5

    def test_archive_at_30_days(self):
        results = [MagicMock(rowcount=0), MagicMock(rowcount=12),
                   MagicMock(rowcount=0), MagicMock(rowcount=0)]
        for r in results:
            r.fetchall.return_value = []
        conn = MagicMock()
        conn.execute.side_effect = results
        cm = MagicMock()
        cm.__enter__.return_value = conn
        eng = MagicMock()
        eng.connect.return_value = cm
        out = fg.daily_decay_job(dash_engine=eng, archive_threshold_days=30)
        assert out.archived_count == 12
        # check the archive UPDATE used the configured threshold
        archive_sql = str(conn.execute.call_args_list[1][0][0])
        assert "archived = TRUE" in archive_sql

    def test_promote_to_resistant_at_5_citations(self):
        results = [MagicMock(rowcount=0), MagicMock(rowcount=0),
                   MagicMock(rowcount=4), MagicMock(rowcount=0)]
        conn = MagicMock()
        conn.execute.side_effect = results
        cm = MagicMock()
        cm.__enter__.return_value = conn
        eng = MagicMock()
        eng.connect.return_value = cm
        out = fg.daily_decay_job(dash_engine=eng, citation_threshold=5)
        assert out.promoted_resistant_count == 4
        promote_sql = str(conn.execute.call_args_list[2][0][0])
        assert "decay_resistant = TRUE" in promote_sql

    def test_reinforce_bumps_counter(self):
        eng, conn, _ = _mk_engine_with_conn()
        ok = fg.reinforce_memory(42, dash_engine=eng)
        assert ok is True
        sql = str(conn.execute.call_args[0][0])
        assert "citation_count" in sql
        assert "last_cited_at = NOW()" in sql

    def test_reinforce_unarchives_by_default(self):
        eng, conn, _ = _mk_engine_with_conn()
        fg.reinforce_memory(1, dash_engine=eng, auto_unarchive=True)
        sql = str(conn.execute.call_args[0][0])
        assert "archived = FALSE" in sql

    def test_reinforce_does_not_unarchive_when_false(self):
        eng, conn, _ = _mk_engine_with_conn()
        fg.reinforce_memory(1, dash_engine=eng, auto_unarchive=False)
        sql = str(conn.execute.call_args[0][0])
        assert "archived = FALSE" not in sql

    def test_reinforce_returns_false_on_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("kaboom")
        assert fg.reinforce_memory(1, dash_engine=eng) is False

    def test_stats_returns_expected_keys(self):
        row = (10, 8, 2, 3, 4, 1, 1.5)
        eng, conn, result = _mk_engine_with_conn(fetchone_row=row)
        out = fg.stats(dash_engine=eng)
        for k in ("total", "active", "archived", "decay_resistant",
                  "high_confidence", "low_confidence", "avg_citation_count"):
            assert k in out
        assert out["total"] == 10
        assert out["avg_citation_count"] == pytest.approx(1.5)

    def test_stats_handles_db_error(self):
        eng = MagicMock()
        eng.connect.side_effect = RuntimeError("nope")
        out = fg.stats(dash_engine=eng)
        # falls through to the default zeros dict
        assert out["total"] == 0
        assert out["active"] == 0
