"""HypothesisEngine — convert ResearchDossier into testable Hypothesis objects.

Pattern: 1 dossier → 1-3 hypotheses. Type detection by LLM. Confidence
seeded by triangulation count (0 tiers = 0.30, 1 tier = 0.40, 2 = 0.55,
3+ = 0.70). LLM extracts SPO/rule/formula form.

Persists to dash_hypotheses table. Returns list of saved Hypothesis objects
(with id populated).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Optional, Callable

from dash.learning.base import (
    Hypothesis, HypothesisType, ResearchDossier, Question,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


class HypothesisEngine:
    def __init__(
        self,
        project_slug: Optional[str] = None,
        source_id: Optional[int] = None,
        llm_call_fn: Optional[Callable] = None,
        dash_engine = None,
    ):
        self.project_slug = project_slug
        self.source_id = source_id
        self.llm_call_fn = llm_call_fn
        self.dash_engine = dash_engine

    def form_from_dossier(
        self,
        question: Question,
        dossier: ResearchDossier,
        *,
        max_hypotheses: int = 3,
        parent_hypothesis_id: Optional[int] = None,
    ) -> list[Hypothesis]:
        """Generate 1..N hypotheses from research evidence + persist them.

        If ``parent_hypothesis_id`` is provided, the new hypotheses become
        children in the experiment tree; their ``lineage_depth`` is set to
        the parent's depth + 1.
        """
        if not dossier.sources:
            logger.debug("no evidence in dossier; no hypotheses formed")
            return []

        confidence_seed = self._seed_confidence(dossier)
        lineage_depth = 0
        if parent_hypothesis_id:
            lineage_depth = self._fetch_parent_depth(parent_hypothesis_id) + 1

        hypotheses_data = self._llm_extract(question, dossier, max_hypotheses)
        if not hypotheses_data:
            # fallback: single hypothesis from dossier summary
            if dossier.summary:
                hypotheses_data = [{
                    "statement": dossier.summary[:500],
                    "type": HypothesisType.PATTERN.value,
                    "rationale": "fallback from dossier summary",
                }]

        out: list[Hypothesis] = []
        for h_data in hypotheses_data[:max_hypotheses]:
            try:
                h = Hypothesis(
                    project_slug=self.project_slug,
                    source_id=self.source_id,
                    question_id=question.id,
                    statement=h_data.get("statement", ""),
                    hypothesis_type=h_data.get("type", HypothesisType.PATTERN.value),
                    sources_consulted=[
                        {
                            "tier": s.tier, "source": s.source,
                            "url": s.url, "confidence": s.confidence,
                        }
                        for s in dossier.best_evidence(6)
                    ],
                    triangulation_count=dossier.triangulation_count,
                    confidence=confidence_seed,
                    verification_status=VerificationStatus.PENDING.value,
                    citations=[s.url for s in dossier.sources if s.url][:6],
                    metadata={
                        "rationale": h_data.get("rationale", ""),
                        "test_idea": h_data.get("test_idea", ""),
                    },
                )
                hid = self._persist(
                    h,
                    parent_hypothesis_id=parent_hypothesis_id,
                    lineage_depth=lineage_depth,
                )
                if hid:
                    h.id = hid
                    out.append(h)
            except Exception as e:
                logger.warning(f"failed to persist hypothesis: {e}")

        return out

    def _seed_confidence(self, dossier: ResearchDossier) -> float:
        """Initial confidence based on triangulation."""
        n = dossier.triangulation_count
        if n >= 3:
            return 0.70
        if n == 2:
            return 0.55
        if n == 1:
            return 0.40
        return 0.30

    def _llm_extract(
        self,
        question: Question,
        dossier: ResearchDossier,
        max_n: int,
    ) -> list[dict]:
        """Ask LLM to propose hypotheses from evidence. Returns JSON list."""
        if self.llm_call_fn is None:
            return []

        evidence = "\n\n".join(
            f"[{s.tier}/{s.source} conf={s.confidence:.2f}] {s.snippet}"
            for s in dossier.best_evidence(8)
        )

        prompt = f"""You are a hypothesis-forming analyst. From the evidence below,
propose UP TO {max_n} testable hypotheses about the question.

QUESTION: {question.question}
TOPIC: {question.topic or 'general'}
DOMAIN: {question.domain or 'general'}

EVIDENCE:
{evidence}

DOSSIER SUMMARY: {dossier.summary or '(none)'}

Each hypothesis must:
- Be a single declarative statement
- Be falsifiable (can be tested with data)
- Have a clear type: causal, correlation, rule, formula, threshold, definition, or pattern

Output JSON array (no markdown):
[
  {{
    "statement": "Customer churn rate increases when monthly login frequency drops below 3",
    "type": "threshold",
    "rationale": "Evidence X+Y suggests engagement drops precede churn",
    "test_idea": "SQL: compare churn rate for users with login_count < 3 vs >= 3"
  }}
]

If no hypothesis is well-supported, return empty array [].
"""
        ans = None
        try:
            ans = self.llm_call_fn(prompt, task='deep_analysis')
            if not ans:
                return []
            # Strip markdown code fences if present
            ans_clean = ans.strip()
            if ans_clean.startswith("```"):
                ans_clean = ans_clean.split("```", 2)[1]
                if ans_clean.startswith("json"):
                    ans_clean = ans_clean[4:].strip()
                ans_clean = ans_clean.rsplit("```", 1)[0].strip()
            data = json.loads(ans_clean)
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            raw = ans[:200] if ans else '?'
            logger.warning(f"_llm_extract parse failed: {e}; raw: {raw}")
            return []

    def _persist(
        self,
        h: Hypothesis,
        *,
        parent_hypothesis_id: Optional[int] = None,
        lineage_depth: int = 0,
    ) -> Optional[int]:
        """Insert into dash_hypotheses; return new id."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            with eng.connect() as conn:
                row = conn.execute(text(
                    "INSERT INTO public.dash_hypotheses "
                    "(project_slug, source_id, question_id, statement, hypothesis_type, "
                    " sources_consulted, triangulation_count, confidence, "
                    " verification_status, citations, metadata, "
                    " parent_hypothesis_id, lineage_depth) "
                    "VALUES (:slug, :sid, :qid, :stmt, :htype, :src, :tri, :conf, "
                    " :vs, :cite, :meta, :pid, :depth) RETURNING id"
                ), {
                    "slug": h.project_slug, "sid": h.source_id, "qid": h.question_id,
                    "stmt": h.statement, "htype": h.hypothesis_type,
                    "src": json.dumps(h.sources_consulted),
                    "tri": h.triangulation_count, "conf": h.confidence,
                    "vs": h.verification_status,
                    "cite": json.dumps(h.citations),
                    "meta": json.dumps(h.metadata),
                    "pid": parent_hypothesis_id,
                    "depth": int(lineage_depth or 0),
                }).fetchone()
                conn.commit()
                return int(row[0]) if row else None
        except Exception as e:
            logger.warning(f"hypothesis persist failed: {e}")
            return None

    def _fetch_parent_depth(self, parent_id: int) -> int:
        """Look up lineage_depth of parent hypothesis (0 if missing)."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            with eng.connect() as conn:
                row = conn.execute(text(
                    "SELECT COALESCE(lineage_depth, 0) "
                    "FROM public.dash_hypotheses WHERE id = :id"
                ), {"id": parent_id}).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def update_confidence(self, hypothesis_id: int, delta: float, reason: str = "") -> None:
        """Bump or decay hypothesis confidence (clamped 0..1)."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            with eng.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_hypotheses SET "
                    " confidence = LEAST(1.0, GREATEST(0.0, confidence + :d)), "
                    " metadata = COALESCE(metadata, '{}'::jsonb) || "
                    "            jsonb_build_object('last_delta_reason', :r) "
                    "WHERE id = :id"
                ), {"d": delta, "r": reason, "id": hypothesis_id})
                conn.commit()
        except Exception as e:
            logger.warning(f"update_confidence failed: {e}")
