"""CuriosityEngine — generates questions for the self-learning loop.

The engine pulls signals from many places (knowledge-graph holes, drift
alerts, failed evals, thumbs-down feedback, anomalies, underused tables,
LLM-driven topic gaps, cross-source entity overlaps, user-suggested
questions, and previous-cycle follow-ups), scores them, deduplicates
against `dash_curiosity_questions`, and persists the survivors.

All sources are wrapped in defensive try/except — a single bad query
never aborts the whole batch. The LLM-backed source is optional: if no
`llm_call_fn` is wired, that source is silently skipped.

Targets the schema authored by the parallel migration
`db/migrations/002_self_learning.sql`::

    dash_curiosity_questions(
        id, project_slug, source_id, question, topic, reason,
        priority, status, cycle_num, domain, created_at,
        answered_at, metadata
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Iterable

from sqlalchemy import NullPool, create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


# ── Engine acquisition ──────────────────────────────────────────────────────

def _default_engine() -> Engine | None:
    """Return a NullPool engine pointing at the dash DB, or None on failure."""
    try:
        from db import db_url  # type: ignore
        return create_engine(db_url, poolclass=NullPool)
    except Exception as exc:  # pragma: no cover - import-time env issues
        log.warning("CuriosityEngine: could not build default engine: %s", exc)
        return None


# ── Default LLM bridge ──────────────────────────────────────────────────────

def _default_llm_call(prompt: str, task: str = "deep_analysis") -> str | None:
    try:
        from dash.settings import training_llm_call  # type: ignore
        return training_llm_call(prompt, task=task)
    except Exception as exc:
        log.warning("CuriosityEngine: training_llm_call unavailable: %s", exc)
        return None


# ── Tuple typing helper ─────────────────────────────────────────────────────
# Each source method returns list[tuple[question, topic, reason, priority, domain]]
from typing import Optional, Tuple
QTuple = Tuple[str, str, str, int, Optional[str]]  # 3.9 compat


# ═══════════════════════════════════════════════════════════════════════════
# CuriosityEngine
# ═══════════════════════════════════════════════════════════════════════════

class CuriosityEngine:
    """Generate, score, dedupe and persist curiosity questions.

    Parameters
    ----------
    project_slug:
        The owning project. Pass ``None`` for cross-project / central runs.
    source_id:
        Optional dash_data_sources id to scope KG/drift/anomaly queries.
    llm_call_fn:
        Callable ``(prompt, task=...) -> str | None``. Defaults to
        ``dash.settings.training_llm_call`` if available.
    dash_engine:
        SQLAlchemy engine. Defaults to one built from ``db.db_url``.
    """

    TABLE = "public.dash_curiosity_questions"

    def __init__(
        self,
        project_slug: str | None = None,
        source_id: int | None = None,
        llm_call_fn: Callable[..., str | None] | None = None,
        dash_engine: Engine | None = None,
    ) -> None:
        self.project_slug = project_slug
        self.source_id = source_id
        self.llm_call_fn = llm_call_fn  # may be None — _topic_gaps will skip
        self.engine: Engine | None = dash_engine if dash_engine is not None else _default_engine()

    # ── Public API ──────────────────────────────────────────────────────────

    def generate(self, max_questions: int = 20, cycle_num: int = 0) -> list[dict]:
        """Run all sources, score, dedupe, persist top ``max_questions``.

        Returns the list of question dicts that were *successfully*
        persisted. Each dict carries: question, topic, reason, priority,
        domain, cycle_num, status, project_slug, source_id, metadata.
        """
        sources: list[tuple[str, Callable[[], list[QTuple]]]] = [
            ("kg_holes",         self._from_kg_holes),
            ("drift",            self._from_drift),
            ("failed_qa",        self._from_failed_qa),
            ("thumbs_down",      self._from_thumbs_down),
            ("anomalies",        self._from_anomalies),
            ("underused_tables", self._from_underused_tables),
            ("topic_gaps",       self._from_topic_gaps),
            ("cross_source",     self._from_cross_source),
            ("user_request",     self._from_user_request),
            ("cycle_followup",   self._from_cycle_followup),
        ]

        bucket: list[dict] = []
        for name, fn in sources:
            try:
                rows = fn() or []
            except Exception as exc:
                log.warning("CuriosityEngine[%s]: source '%s' failed: %s",
                            self.project_slug, name, exc)
                rows = []
            for row in rows:
                try:
                    q, topic, reason, base_priority, domain = self._unpack(row)
                except Exception as exc:
                    log.debug("CuriosityEngine: malformed tuple from %s: %s", name, exc)
                    continue
                if not q:
                    continue
                bucket.append({
                    "project_slug": self.project_slug,
                    "source_id": self.source_id,
                    "question": q.strip(),
                    "topic": (topic or name).strip()[:200],
                    "reason": (reason or name).strip()[:200],
                    "priority": int(base_priority) if base_priority is not None else 50,
                    "status": "pending",
                    "cycle_num": int(cycle_num),
                    "domain": (domain or None),
                    "metadata": {"source_method": name},
                })

        # Re-score with global rules + drop dupes
        seen_in_batch: set[str] = set()
        scored: list[dict] = []
        for q in bucket:
            q["priority"] = self._score_priority(q)
            h = self._hash(q["question"])
            if h in seen_in_batch:
                continue
            seen_in_batch.add(h)
            try:
                if self._is_duplicate(q["question"], q["project_slug"]):
                    continue
            except Exception as exc:
                log.debug("CuriosityEngine: dedup check failed (continuing): %s", exc)
            scored.append(q)

        scored.sort(key=lambda r: r["priority"], reverse=True)
        top = scored[: max(0, int(max_questions))]
        try:
            self._persist(top)
        except Exception as exc:
            log.error("CuriosityEngine: persistence layer crashed: %s", exc)
        return top

    # ── Question sources ────────────────────────────────────────────────────

    def _from_kg_holes(self) -> list[QTuple]:
        """Entities in dash_knowledge_triples appearing < 2 times."""
        if self.engine is None or not self.project_slug:
            return []
        out: list[QTuple] = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT entity, cnt FROM (
                    SELECT subject AS entity, COUNT(*) AS cnt
                    FROM public.dash_knowledge_triples
                    WHERE project_slug = :s
                    GROUP BY subject
                    UNION ALL
                    SELECT object AS entity, COUNT(*) AS cnt
                    FROM public.dash_knowledge_triples
                    WHERE project_slug = :s
                    GROUP BY object
                ) e
                GROUP BY entity, cnt
                HAVING SUM(cnt) < 2
                LIMIT 30
                """
            ), {"s": self.project_slug}).fetchall()
        for r in rows:
            ent = r[0]
            if not ent:
                continue
            q = f"How does {ent} relate to other entities in this dataset?"
            out.append((q, "kg_holes", "kg_hole", 70, None))
        return out

    def _from_drift(self) -> list[QTuple]:
        """Recent drift alerts → why did distribution change?"""
        if self.engine is None or not self.project_slug:
            return []
        sql = """
            SELECT table_name, column_name, alert_type, details
            FROM public.dash_drift_alerts
            WHERE project_slug = :s
              AND created_at > NOW() - INTERVAL '14 days'
            ORDER BY created_at DESC
            LIMIT 25
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
        out: list[QTuple] = []
        for r in rows:
            tbl, col, atype = r[0], r[1], r[2]
            if not tbl:
                continue
            target = f"{tbl}.{col}" if col else tbl
            q = f"Why did {target} change distribution this week ({atype or 'drift'})?"
            out.append((q, "drift", "drift", 80, None))
        return out

    def _from_failed_qa(self) -> list[QTuple]:
        """dash_evals with status FAIL."""
        if self.engine is None or not self.project_slug:
            return []
        sql = """
            SELECT question, COALESCE(reasoning, '') AS rsn
            FROM public.dash_evals
            WHERE project_slug = :s AND UPPER(COALESCE(status, '')) = 'FAIL'
            ORDER BY created_at DESC NULLS LAST
            LIMIT 20
        """
        out: list[QTuple] = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
        for r in rows:
            qtext = (r[0] or "").strip()
            if not qtext:
                continue
            q = f"Why did the eval question '{qtext[:160]}' fail? What knowledge is missing?"
            out.append((q, "failed_qa", "failed_qa", 85, None))
        return out

    def _from_thumbs_down(self) -> list[QTuple]:
        """dash_feedback rating='down'."""
        if self.engine is None or not self.project_slug:
            return []
        sql = """
            SELECT question, answer
            FROM public.dash_feedback
            WHERE project_slug = :s AND rating = 'down'
            ORDER BY created_at DESC
            LIMIT 15
        """
        out: list[QTuple] = []
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
        for r in rows:
            qtext = (r[0] or "").strip()
            if not qtext:
                continue
            q = (
                f"What is the correct answer for '{qtext[:160]}'? "
                "We previously got it wrong — find the right tables, joins, or rule."
            )
            out.append((q, "thumbs_down", "thumbs_down", 90, None))
        return out

    def _from_anomalies(self) -> list[QTuple]:
        """dash_proactive_insights from last 7 days."""
        if self.engine is None or not self.project_slug:
            return []
        sql = """
            SELECT description, severity
            FROM public.dash_proactive_insights
            WHERE project_slug = :s
              AND created_at > NOW() - INTERVAL '7 days'
            ORDER BY created_at DESC
            LIMIT 20
        """
        out: list[QTuple] = []
        with self.engine.connect() as conn:
            try:
                rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
            except Exception:
                # column name fallback
                rows = conn.execute(text(
                    "SELECT title AS description, NULL AS severity "
                    "FROM public.dash_proactive_insights "
                    "WHERE project_slug = :s "
                    "AND created_at > NOW() - INTERVAL '7 days' LIMIT 20"
                ), {"s": self.project_slug}).fetchall()
        for r in rows:
            desc = (r[0] or "").strip()
            if not desc:
                continue
            q = f"What caused the anomaly: {desc[:200]}?"
            out.append((q, "anomaly", "anomaly", 75, None))
        return out

    def _from_underused_tables(self) -> list[QTuple]:
        """Tables in semantic model with 0 query patterns."""
        if self.engine is None or not self.project_slug:
            return []
        out: list[QTuple] = []
        try:
            from dash.context.semantic_model import load_semantic_model  # type: ignore
            sm = load_semantic_model(self.project_slug) or {}
            tables = list((sm.get("tables") or {}).keys()) if isinstance(sm, dict) else []
        except Exception:
            tables = []
        if not tables:
            return out
        with self.engine.connect() as conn:
            used = conn.execute(text(
                "SELECT DISTINCT lower(unnest(tables)) FROM public.dash_query_patterns "
                "WHERE project_slug = :s"
            ), {"s": self.project_slug}).fetchall() if False else []
            # Fallback simpler query — many query-patterns tables don't have
            # a `tables` array column, so do a pattern-text search instead.
            patt = conn.execute(text(
                "SELECT COALESCE(sql, '') FROM public.dash_query_patterns "
                "WHERE project_slug = :s LIMIT 500"
            ), {"s": self.project_slug}).fetchall()
        used_set = {row[0].lower() for row in used} if used else set()
        sql_blob = " ".join((p[0] or "").lower() for p in patt)
        for tbl in tables:
            tname = str(tbl).lower()
            if tname in used_set or tname in sql_blob:
                continue
            q = f"What is the table {tbl} for, and when should I query it?"
            out.append((q, "underused_table", "underused_table", 50, None))
        return out

    def _from_topic_gaps(self) -> list[QTuple]:
        """LLM-generated questions from current persona + brain glossary gaps.

        Now uses branch+prune: 1 LLM call generates N variants per gap,
        scored on specificity/testability/novelty, top-1 (or 2 if tied) kept.
        """
        if not self.llm_call_fn:
            return []

        # First, identify gap topics (cheap heuristic — top 5 underused dim values)
        gaps = self._identify_gaps()
        if not gaps:
            # Fallback: ask LLM "what should we learn next?" with 1 broad topic
            gaps = [f"general analysis for {self.project_slug or 'this project'}"]

        return self._branch_and_prune(gaps, variants_per_gap=3)

    def _identify_gaps(self) -> list[str]:
        """Cheap (no-LLM) gap detection — multi-signal.

        Returns up to 12 gap topic strings, prioritized by signal type.
        """
        gaps: list[str] = []
        if self.engine is None:
            return gaps

        sources = [
            self._gap_unexplored_tables,
            self._gap_unexplained_anomalies,
            self._gap_kg_isolated_entities,
            self._gap_brain_uncovered_terms,
            self._gap_dim_value_coverage,
            self._gap_irregular_time_patterns,
            self._gap_persona_ambiguities,
            self._gap_unverified_cross_source,
            self._gap_low_confidence_memories,
            self._gap_failed_eval_themes,
        ]

        for fn in sources:
            try:
                new_gaps = fn() or []
                for g in new_gaps:
                    if g and g not in gaps:
                        gaps.append(g)
                        if len(gaps) >= 12:
                            return gaps
            except Exception as exc:
                log.debug(f"_identify_gaps source {fn.__name__}: {exc}")
        return gaps

    def _gap_unexplored_tables(self) -> list[str]:
        """Tables in semantic model with 0 queries in dash_query_patterns."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT t.table_name FROM public.dash_table_metadata t "
                    "WHERE t.project_slug = :s "
                    "  AND NOT EXISTS ("
                    "    SELECT 1 FROM public.dash_query_patterns q "
                    "    WHERE q.project_slug = :s "
                    "      AND q.sql ILIKE '%' || t.table_name || '%' "
                    "  ) "
                    "LIMIT 3"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                out.append(f"unexplored table: {r[0]}")
        except Exception:
            pass
        return out

    def _gap_unexplained_anomalies(self) -> list[str]:
        """Recent anomalies w/o resolution."""
        out: list[str] = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT description FROM public.dash_proactive_insights "
                    "WHERE project_slug = :s "
                    "  AND created_at > NOW() - INTERVAL '7 days' "
                    "ORDER BY created_at DESC LIMIT 3"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                if r[0]:
                    out.append(f"unexplained anomaly: {r[0][:200]}")
        except Exception:
            pass
        return out

    def _gap_kg_isolated_entities(self) -> list[str]:
        """KG entities with only 1 relationship — likely under-modeled."""
        out: list[str] = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "WITH ent AS ("
                    "  SELECT subject AS e FROM public.dash_knowledge_triples "
                    "  WHERE project_slug = :s "
                    "  UNION ALL "
                    "  SELECT object AS e FROM public.dash_knowledge_triples "
                    "  WHERE project_slug = :s "
                    ") SELECT e, COUNT(*) AS c FROM ent "
                    "GROUP BY e HAVING COUNT(*) = 1 "
                    "ORDER BY e LIMIT 3"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                out.append(f"isolated KG entity: {r[0]}")
        except Exception:
            pass
        return out

    def _gap_brain_uncovered_terms(self) -> list[str]:
        """Common business terms in chat history not in Brain glossary."""
        out: list[str] = []
        try:
            with self.engine.connect() as conn:
                # heuristic: capitalized words in recent feedback questions
                rows = conn.execute(text(
                    "SELECT DISTINCT regexp_matches(question, '\\b[A-Z][a-zA-Z]{4,}\\b', 'g') AS m "
                    "FROM public.dash_feedback "
                    "WHERE project_slug = :s "
                    "  AND created_at > NOW() - INTERVAL '14 days' "
                    "LIMIT 5"
                ), {"s": self.project_slug}).fetchall()
            terms = set()
            for r in rows:
                try:
                    terms.add(r[0][0] if isinstance(r[0], (list, tuple)) else str(r[0]))
                except Exception:
                    pass
            for t in list(terms)[:3]:
                out.append(f"uncovered term: {t}")
        except Exception:
            pass
        return out

    def _gap_dim_value_coverage(self) -> list[str]:
        """Dim columns with very high cardinality but rarely filtered in past queries."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, column_name "
                    "FROM public.dash_annotations "
                    "WHERE project_slug = :s "
                    "  AND annotation ILIKE '%dimension%' "
                    "ORDER BY column_name LIMIT 3"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                out.append(f"dimension coverage: {r[0]}.{r[1]}")
        except Exception:
            pass
        return out

    def _gap_irregular_time_patterns(self) -> list[str]:
        """Time-series tables with gaps or seasonality not explained."""
        out: list[str] = []
        try:
            # heuristic — table names with date columns
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT DISTINCT table_name "
                    "FROM public.dash_annotations "
                    "WHERE project_slug = :s "
                    "  AND (column_name ILIKE '%date%' OR column_name ILIKE '%_at%') "
                    "LIMIT 2"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                out.append(f"time-series patterns in: {r[0]}")
        except Exception:
            pass
        return out

    def _gap_persona_ambiguities(self) -> list[str]:
        """Persona text contains 'TBD' or '?' markers."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                r = conn.execute(text(
                    "SELECT persona FROM public.dash_personas "
                    "WHERE project_slug = :s "
                    "ORDER BY created_at DESC LIMIT 1"
                ), {"s": self.project_slug}).fetchone()
            if r and r[0]:
                text_v = r[0]
                for marker in ("TBD", "TODO", "unclear", "ambiguous", "?"):
                    if marker in text_v:
                        out.append(f"persona ambiguity: clarify {marker} markers")
                        break
        except Exception:
            pass
        return out

    def _gap_unverified_cross_source(self) -> list[str]:
        """Cross-source candidate joins not yet verified."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT DISTINCT from_table, to_table "
                    "FROM public.dash_relationships "
                    "WHERE project_slug = :s "
                    "  AND COALESCE(confidence, 0) < 0.7 "
                    "LIMIT 2"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                out.append(f"unverified join: {r[0]} ↔ {r[1]}")
        except Exception:
            pass
        return out

    def _gap_low_confidence_memories(self) -> list[str]:
        """Memories below confidence threshold — candidates for re-verification."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT fact FROM public.dash_memories "
                    "WHERE project_slug = :s "
                    "  AND COALESCE(confidence_score, 0.5) < 0.4 "
                    "  AND (archived IS NULL OR archived = FALSE) "
                    "ORDER BY confidence_score ASC LIMIT 2"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                if r[0]:
                    out.append(f"low-confidence fact: {r[0][:150]}")
        except Exception:
            pass
        return out

    def _gap_failed_eval_themes(self) -> list[str]:
        """Recurring failure themes from dash_evals."""
        out: list[str] = []
        if not self.project_slug:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT question FROM public.dash_evals "
                    "WHERE project_slug = :s "
                    "  AND UPPER(status) = 'FAIL' "
                    "ORDER BY id DESC LIMIT 2"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                if r[0]:
                    out.append(f"failed eval theme: {r[0][:200]}")
        except Exception:
            pass
        return out

    def _branch_and_prune(self, gaps: list[str], variants_per_gap: int = 3) -> list[QTuple]:
        """For each gap topic, ask LLM to generate N candidate questions.
        Score each by clarity + specificity + testability. Keep best 1-2.

        Single batched LLM call (not 3 separate calls) → cost-efficient.
        """
        if not self.llm_call_fn or not gaps:
            return []

        prompt = f"""You are a question generator. For each gap topic listed below,
write {variants_per_gap} candidate questions. Score each on:
- specificity (0-3): targets a concrete, measurable thing
- testability (0-3): answer can be verified with data
- novelty (0-3): not already obvious from existing knowledge

Return JSON array, one entry per gap, each with {variants_per_gap} variants.

Gaps:
{chr(10).join(f"- {g}" for g in gaps[:6])}

Output format:
[
  {{
    "gap": "<original gap>",
    "variants": [
      {{"question": "...", "specificity": 2, "testability": 3, "novelty": 1, "topic": "...", "domain": "..."}},
      {{"question": "...", "specificity": 3, "testability": 2, "novelty": 3, "topic": "...", "domain": "..."}},
      ...
    ]
  }}
]
"""
        try:
            ans = self.llm_call_fn(prompt, task='deep_analysis')
            if not ans:
                return []
            ans_clean = ans.strip()
            if ans_clean.startswith("```"):
                ans_clean = ans_clean.split("```", 2)[1]
                if ans_clean.startswith("json"):
                    ans_clean = ans_clean[4:].strip()
                ans_clean = ans_clean.rsplit("```", 1)[0].strip()
            data = json.loads(ans_clean)
            if not isinstance(data, list):
                return []
        except Exception as exc:
            log.warning(f"_branch_and_prune parse failed: {exc}")
            return []

        out: list[QTuple] = []
        for gap_obj in data:
            if not isinstance(gap_obj, dict):
                continue
            variants = gap_obj.get("variants") or []
            if not variants:
                continue
            # Score each variant
            scored = []
            for v in variants:
                if not isinstance(v, dict):
                    continue
                spec = int(v.get("specificity", 0) or 0)
                test_ = int(v.get("testability", 0) or 0)
                nov = int(v.get("novelty", 0) or 0)
                total = spec + test_ + nov  # 0-9
                scored.append((total, v))
            scored.sort(key=lambda x: x[0], reverse=True)
            if not scored:
                continue
            top_score, top = scored[0]
            # Keep top-1, plus runner-up if within 1 point
            keep = [top]
            if len(scored) > 1 and abs(scored[1][0] - top_score) <= 1:
                keep.append(scored[1][1])
            for v in keep:
                q = (v.get("question") or "").strip()
                if not q:
                    continue
                topic = (v.get("topic") or gap_obj.get("gap") or "topic_gap").strip()[:200]
                domain = (v.get("domain") or None)
                # Map LLM score (0-9) to priority (40-90)
                priority = 40 + (top_score * 5)
                out.append((q, topic, "topic_gap", priority, domain))
        return out

    def _from_cross_source(self) -> list[QTuple]:
        """If project has 2+ sources, ask about cross-source overlap."""
        if self.engine is None or not self.project_slug:
            return []
        sql = """
            SELECT id, COALESCE(name, source_type), source_type
            FROM public.dash_data_sources
            WHERE project_slug = :s
            ORDER BY id
            LIMIT 10
        """
        with self.engine.connect() as conn:
            try:
                rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
            except Exception:
                return []
        if len(rows) < 2:
            return []
        out: list[QTuple] = []
        # Pairwise — keep small.
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                a_label = a[1] or f"source_{a[0]}"
                b_label = b[1] or f"source_{b[0]}"
                q = (
                    f"How do entities in {a_label} relate to entities in "
                    f"{b_label}? Are there shared customer/account/product IDs?"
                )
                out.append((q, "cross_source", "cross_source", 60, None))
                if len(out) >= 6:
                    return out
        return out

    def _from_user_request(self) -> list[QTuple]:
        """Pass through user-suggested questions (already in the table)."""
        if self.engine is None:
            return []
        sql = (
            "SELECT question, topic, COALESCE(domain, '') "
            "FROM " + self.TABLE + " "
            "WHERE reason = 'user_request' AND status = 'pending' "
            "AND (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
            "LIMIT 25"
        )
        out: list[QTuple] = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
        except Exception:
            return []
        for r in rows:
            q = (r[0] or "").strip()
            if not q:
                continue
            out.append((q, (r[1] or "user_request"), "user_request", 95, (r[2] or None)))
        return out

    def _from_cycle_followup(self) -> list[QTuple]:
        """Drill deeper on the most-recent prior cycle's answered questions."""
        if self.engine is None:
            return []
        sql = (
            "SELECT question, topic, COALESCE(domain, '') "
            "FROM " + self.TABLE + " "
            "WHERE status = 'answered' "
            "AND (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
            "ORDER BY answered_at DESC NULLS LAST LIMIT 10"
        )
        out: list[QTuple] = []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(sql), {"s": self.project_slug}).fetchall()
        except Exception:
            return []
        for r in rows:
            base = (r[0] or "").strip()
            if not base:
                continue
            q = f"Going deeper on '{base[:160]}': what causal factors or edge cases were missed?"
            out.append((q, (r[1] or "cycle_followup"), "cycle_followup", 30, (r[2] or None)))
        return out

    # ── Scoring & dedup ─────────────────────────────────────────────────────

    def _score_priority(self, q: dict) -> int:
        """Compute final 0-100 priority based on combined signals."""
        reason = (q.get("reason") or "").lower()
        meta = q.get("metadata") or {}
        src = (meta.get("source_method") or "").lower()
        question = (q.get("question") or "").lower()

        has_drift = "drift" in reason or "drift" in question
        has_anomaly = "anomaly" in reason or "anomaly" in question

        if reason == "user_request":
            return 95
        if reason == "thumbs_down" and has_drift:
            return 95
        if has_anomaly and has_drift:
            return 90
        if reason == "thumbs_down":
            return 90
        if reason == "failed_qa":
            return 85
        if reason == "drift":
            return 80
        if reason == "kg_hole":
            # Boost if question mentions a heavily-cited entity (proxy: long name)
            return 80 if len(question) > 80 else 70
        if reason == "anomaly":
            return 75
        if reason == "cross_source":
            return 60
        if reason == "underused_table" or src == "underused_tables":
            return 50
        if "topic_gap" in reason or src == "topic_gaps":
            return 40
        if reason == "cycle_followup":
            return 30
        return int(q.get("priority") or 50)

    def _is_duplicate(self, question: str, project_slug: str | None) -> bool:
        """True iff a non-archived row with the same question text already exists."""
        if self.engine is None or not question:
            return False
        sql = (
            "SELECT 1 FROM " + self.TABLE + " "
            "WHERE question = :q "
            "AND (status IS NULL OR status != 'archived') "
            "AND ((:s IS NULL AND project_slug IS NULL) OR project_slug = :s) "
            "LIMIT 1"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"q": question, "s": project_slug}).fetchone()
        return row is not None

    @staticmethod
    def _hash(text_in: str) -> str:
        return hashlib.sha256((text_in or "").strip().lower().encode("utf-8")).hexdigest()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _persist(self, questions: list[dict]) -> int:
        """Bulk insert (per-row try/except). Returns number actually inserted."""
        if self.engine is None or not questions:
            return 0
        sql = text(
            "INSERT INTO " + self.TABLE + " "
            "(project_slug, source_id, question, topic, reason, priority, "
            " status, cycle_num, domain, metadata) "
            "VALUES (:project_slug, :source_id, :question, :topic, :reason, "
            " :priority, :status, :cycle_num, :domain, CAST(:metadata AS JSONB))"
        )
        inserted = 0
        with self.engine.begin() as conn:
            for q in questions:
                try:
                    conn.execute(sql, {
                        "project_slug": q.get("project_slug"),
                        "source_id": q.get("source_id"),
                        "question": q["question"],
                        "topic": q.get("topic"),
                        "reason": q.get("reason"),
                        "priority": int(q.get("priority") or 50),
                        "status": q.get("status") or "pending",
                        "cycle_num": int(q.get("cycle_num") or 0),
                        "domain": q.get("domain"),
                        "metadata": json.dumps(q.get("metadata") or {}),
                    })
                    inserted += 1
                except Exception as exc:
                    log.warning("CuriosityEngine: insert failed for one question: %s", exc)
        return inserted

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _unpack(row: Iterable[Any]) -> tuple[str, str, str, int, str | None]:
        """Normalize a 4- or 5-tuple from a source method."""
        items = tuple(row)
        if len(items) == 5:
            q, topic, reason, prio, domain = items
        elif len(items) == 4:
            q, topic, reason, prio = items
            domain = None
        else:
            raise ValueError(f"expected 4 or 5 fields, got {len(items)}")
        return (str(q), str(topic), str(reason), int(prio or 50), domain)

    def _fetch_persona(self) -> str | None:
        if self.engine is None or not self.project_slug:
            return None
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT persona FROM public.dash_personas "
                    "WHERE project_slug = :s ORDER BY created_at DESC NULLS LAST LIMIT 1"
                ), {"s": self.project_slug}).fetchone()
            return (row[0] if row else None)
        except Exception:
            return None

    def _detect_domain(self) -> str:
        if self.engine is None or not self.project_slug:
            return "general"
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT domain FROM public.dash_projects WHERE slug = :s LIMIT 1"
                ), {"s": self.project_slug}).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
        return "general"

    def _fetch_top_kpis(self, limit: int = 5) -> list[str]:
        if self.engine is None or not self.project_slug:
            return []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT fact FROM public.dash_memories "
                    "WHERE project_slug = :s AND fact ILIKE '%kpi%' "
                    "AND (archived IS NULL OR archived = FALSE) "
                    "ORDER BY created_at DESC LIMIT :n"
                ), {"s": self.project_slug, "n": int(limit)}).fetchall()
            return [r[0] for r in rows if r and r[0]]
        except Exception:
            return []

    def _fetch_recent_questions(self, limit: int = 3) -> list[str]:
        if self.engine is None or not self.project_slug:
            return []
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT question FROM public.dash_feedback "
                    "WHERE project_slug = :s "
                    "ORDER BY created_at DESC LIMIT :n"
                ), {"s": self.project_slug, "n": int(limit)}).fetchall()
            return [r[0] for r in rows if r and r[0]]
        except Exception:
            return []

    @staticmethod
    def _safe_json_array(raw: str) -> list[Any]:
        s = (raw or "").strip()
        if not s:
            return []
        # Strip code fences
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        # Find first '[' and last ']'
        i, j = s.find("["), s.rfind("]")
        if i != -1 and j != -1 and j > i:
            s = s[i: j + 1]
        try:
            data = json.loads(s)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "questions" in data:
                qs = data.get("questions") or []
                return qs if isinstance(qs, list) else []
        except Exception:
            return []
        return []


__all__ = ["CuriosityEngine"]
