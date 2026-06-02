"""Promotion pipeline — move verified, PII-safe, generalizable facts from a
project pool into the central Company Brain pool.

Stages:
    1. find_candidates    — VERIFIED + NOT promoted_to_central yet, opt-in
    2. screen_pii         — regex strip emails/phones/SSN/currency/names
    3. screen_generalize  — LLM verdict A/B/C (universal/pattern/specific)
    4. check_triangulation — same fact verified across N distinct projects
    5. promote            — INSERT central row + audit + flip flag

Project opt-out: dash_projects.contribute_to_central = FALSE → skip.

Audit trail: every attempt (success or rejection) writes a
dash_promotion_log row with the rejection_reason.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

from dash.learning.base import (
    HypothesisType,
    PromotionCandidate,
    PromotionMethod,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PII regex blockers — any match → reject candidate
# ---------------------------------------------------------------------------
_PII_BLOCKERS: list[re.Pattern] = [
    re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+'),     # email
    re.compile(r'\+?\d{10,15}'),                    # phone
    re.compile(r'\d{3}-?\d{2}-?\d{4}'),             # SSN
    re.compile(r'\$[\d,]+\.\d{2}'),                 # currency w/ specific value
    re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),           # specific date
    re.compile(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b'),     # likely person name (FirstLast)
]

# Hypothesis types that need only 1 verified instance + LLM gate
_SOLO_TYPES = {HypothesisType.DEFINITION.value, HypothesisType.FORMULA.value}

# Hypothesis types that need triangulation across ≥3 distinct projects
_TRIANGULATION_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Generalization prompt
# ---------------------------------------------------------------------------
_GENERALIZE_PROMPT = """Statement: "{fact_text}"
Type: {fact_type}

Does this statement describe:
A) A universal industry-wide fact (e.g. "RFM = recency + frequency + monetary")?
B) A pattern that holds for many businesses (e.g. "weekend traffic peaks for retail")?
C) A specific finding for one dataset (e.g. "Customer 12345 churned in March")?

Answer JSON: {{"verdict": "A|B|C", "reason": "..."}}
A or B → safe to promote to central pool.
C → keep project-only.
"""


class PromotionPipeline:
    """Top-level orchestration for project → central knowledge promotion."""

    def __init__(self, llm_call_fn=None, dash_engine=None):
        self.llm_call_fn = llm_call_fn
        self.dash_engine = dash_engine

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _engine(self):
        if self.dash_engine is not None:
            return self.dash_engine
        try:
            from db.session import get_sql_engine
            return get_sql_engine()
        except Exception as e:
            logger.warning(f"promotion: cannot acquire dash engine: {e}")
            return None

    @staticmethod
    def _norm_key(text: str) -> str:
        """Normalize statement for fuzzy triangulation match."""
        t = (text or "").lower().strip()
        t = re.sub(r"\s+", " ", t)
        return t[:60]

    # ------------------------------------------------------------------ #
    # Stage 1: discover candidates                                       #
    # ------------------------------------------------------------------ #
    def find_candidates(
        self, *, min_confidence: float = 0.85, limit: int = 50
    ) -> list[PromotionCandidate]:
        """Query verified+unpromoted hypotheses across all opt-in projects."""
        eng = self._engine()
        if eng is None:
            return []
        try:
            from sqlalchemy import text
            sql = text(
                "SELECT h.id, h.project_slug, h.statement, h.hypothesis_type, "
                "       h.triangulation_count "
                "FROM public.dash_hypotheses h "
                "LEFT JOIN public.dash_projects p "
                "       ON p.slug = h.project_slug "
                "WHERE h.verification_status = :verified "
                "  AND (h.promoted_to_central IS NULL OR h.promoted_to_central = FALSE) "
                "  AND h.confidence >= :conf "
                "  AND h.project_slug IS NOT NULL "
                "  AND (p.contribute_to_central IS NULL "
                "       OR p.contribute_to_central = TRUE) "
                "ORDER BY h.confidence DESC "
                "LIMIT :lim"
            )
            with eng.connect() as conn:
                rows = conn.execute(sql, {
                    "verified": VerificationStatus.VERIFIED.value,
                    "conf": float(min_confidence),
                    "lim": int(limit),
                }).fetchall()
        except Exception as e:
            logger.warning(f"find_candidates: {e}")
            return []

        out: list[PromotionCandidate] = []
        for r in rows:
            try:
                out.append(PromotionCandidate(
                    hypothesis_id=int(r[0]),
                    source_project_slug=str(r[1] or ""),
                    fact_text=str(r[2] or ""),
                    fact_type=str(r[3] or HypothesisType.PATTERN.value),
                    triangulation_count=int(r[4] or 0),
                ))
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------ #
    # Stage 2: PII screen                                                #
    # ------------------------------------------------------------------ #
    def screen_pii(self, candidate: PromotionCandidate) -> bool:
        """True if PII-safe."""
        text = candidate.fact_text or ""
        for pat in _PII_BLOCKERS:
            try:
                if pat.search(text):
                    candidate.pii_safe = False
                    candidate.contains_data_values = True
                    candidate.rejection_reason = f"pii_match:{pat.pattern[:30]}"
                    return False
            except Exception:
                continue
        candidate.pii_safe = True
        return True

    # ------------------------------------------------------------------ #
    # Stage 3: generalization gate                                       #
    # ------------------------------------------------------------------ #
    def screen_generalizable(
        self, candidate: PromotionCandidate
    ) -> tuple[bool, str]:
        """LLM-asked: does this generalize? Returns (yes/no, reason)."""
        if self.llm_call_fn is None:
            # No LLM available — abstain (allow), rely on triangulation.
            return True, "llm_unavailable_skip"

        prompt = _GENERALIZE_PROMPT.format(
            fact_text=candidate.fact_text[:500],
            fact_type=candidate.fact_type,
        )
        try:
            raw = self.llm_call_fn(prompt, task="extraction")
        except Exception as e:
            logger.warning(f"screen_generalizable LLM err: {e}")
            return True, f"llm_error:{str(e)[:80]}"

        verdict = ""
        reason = ""
        try:
            cleaned = (raw or "").strip().strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            data = json.loads(cleaned)
            verdict = str(data.get("verdict", "")).strip().upper()[:1]
            reason = str(data.get("reason", ""))[:240]
        except Exception:
            # Fallback: scan raw text for A/B/C
            up = (raw or "").upper()
            for v in ("A", "B", "C"):
                if f'"{v}"' in up or f"VERDICT: {v}" in up:
                    verdict = v
                    break
            reason = (raw or "")[:240]

        if verdict in ("A", "B"):
            return True, f"verdict={verdict} {reason}"
        if verdict == "C":
            candidate.rejection_reason = "not_generalizable_C"
            return False, f"verdict=C {reason}"
        # Ambiguous → conservative: reject
        candidate.rejection_reason = "verdict_unparseable"
        return False, f"verdict_unknown raw={(raw or '')[:80]}"

    # ------------------------------------------------------------------ #
    # Stage 4: triangulation                                             #
    # ------------------------------------------------------------------ #
    def check_triangulation(self, candidate: PromotionCandidate) -> int:
        """Count distinct projects with same/similar verified hypothesis."""
        eng = self._engine()
        if eng is None:
            return 0
        try:
            from sqlalchemy import text
            key = self._norm_key(candidate.fact_text)
            if not key:
                return 0
            sql = text(
                "SELECT COUNT(DISTINCT project_slug) "
                "FROM public.dash_hypotheses "
                "WHERE verification_status = :verified "
                "  AND project_slug IS NOT NULL "
                "  AND LOWER(SUBSTRING(TRIM(statement) FROM 1 FOR 60)) = :key"
            )
            with eng.connect() as conn:
                row = conn.execute(sql, {
                    "verified": VerificationStatus.VERIFIED.value,
                    "key": key,
                }).fetchone()
            count = int(row[0] or 0) if row else 0
            candidate.triangulation_count = max(
                candidate.triangulation_count, count
            )
            return count
        except Exception as e:
            logger.warning(f"check_triangulation: {e}")
            return 0

    # ------------------------------------------------------------------ #
    # Stage 5: promote                                                   #
    # ------------------------------------------------------------------ #
    def promote(self, candidate: PromotionCandidate) -> bool:
        """Insert into central Brain + audit log + flip promoted flag."""
        eng = self._engine()
        if eng is None:
            self._audit(candidate, approved=False,
                        method=PromotionMethod.AUTO_LLM.value,
                        reason="no_db_engine")
            return False
        try:
            from sqlalchemy import text
            category = self._brain_category(candidate.fact_type)
            name = self._derive_name(candidate.fact_text, category)
            method = self._decide_method(candidate)

            with eng.connect() as conn:
                # 1) Central brain row (project_slug=NULL → central pool)
                conn.execute(text(
                    "INSERT INTO public.dash_company_brain "
                    "(project_slug, source_id, name, definition, category, metadata) "
                    "VALUES (NULL, NULL, :name, :defn, :cat, :meta)"
                ), {
                    "name": name[:200],
                    "defn": candidate.fact_text,
                    "cat": category,
                    "meta": json.dumps({
                        "promoted_from": candidate.source_project_slug,
                        "hypothesis_id": candidate.hypothesis_id,
                        "fact_type": candidate.fact_type,
                        "triangulation_count": candidate.triangulation_count,
                        "method": method,
                    }),
                })

                # 2) Flip promoted_to_central on the source hypothesis
                conn.execute(text(
                    "UPDATE public.dash_hypotheses "
                    "SET promoted_to_central = TRUE "
                    "WHERE id = :hid"
                ), {"hid": candidate.hypothesis_id})

                conn.commit()
        except Exception as e:
            logger.warning(f"promote insert failed: {e}")
            self._audit(candidate, approved=False,
                        method=PromotionMethod.AUTO_LLM.value,
                        reason=f"insert_error:{str(e)[:120]}")
            return False

        self._audit(candidate, approved=True,
                    method=self._decide_method(candidate),
                    reason=None)
        return True

    # ------------------------------------------------------------------ #
    # Audit log                                                          #
    # ------------------------------------------------------------------ #
    def _audit(
        self,
        candidate: PromotionCandidate,
        *,
        approved: bool,
        method: str,
        reason: Optional[str],
    ) -> None:
        eng = self._engine()
        if eng is None:
            return
        try:
            from sqlalchemy import text
            with eng.connect() as conn:
                conn.execute(text(
                    "INSERT INTO public.dash_promotion_log "
                    "(source_project_slug, hypothesis_id, fact_text, fact_type, "
                    " approval_method, pii_scrubbed, triangulation_count, "
                    " approver, rejection_reason) "
                    "VALUES (:slug, :hid, :ft, :ftype, :method, :pii, :tri, "
                    "        :approver, :rej)"
                ), {
                    "slug": candidate.source_project_slug,
                    "hid": candidate.hypothesis_id,
                    "ft": (candidate.fact_text or "")[:2000],
                    "ftype": candidate.fact_type,
                    "method": method,
                    "pii": bool(candidate.pii_safe),
                    "tri": int(candidate.triangulation_count or 0),
                    "approver": "system" if approved else "system_reject",
                    "rej": None if approved else (reason or candidate.rejection_reason),
                })
                conn.commit()
        except Exception as e:
            logger.warning(f"_audit: {e}")

    # ------------------------------------------------------------------ #
    # Run cycle                                                          #
    # ------------------------------------------------------------------ #
    def run_promotion_cycle(self) -> dict:
        """Top-level: scan, screen, promote. Returns counts."""
        stats = {
            "candidates": 0,
            "rejected_pii": 0,
            "rejected_generalize": 0,
            "rejected_triangulation": 0,
            "promoted": 0,
            "errors": 0,
        }
        try:
            candidates = self.find_candidates()
        except Exception as e:
            logger.warning(f"run_promotion_cycle find err: {e}")
            stats["errors"] += 1
            return stats

        stats["candidates"] = len(candidates)

        for cand in candidates:
            try:
                # PII gate
                if not self.screen_pii(cand):
                    stats["rejected_pii"] += 1
                    self._audit(cand, approved=False,
                                method=PromotionMethod.AUTO_LLM.value,
                                reason=cand.rejection_reason or "pii")
                    continue

                # Generalization gate
                ok, gen_reason = self.screen_generalizable(cand)
                if not ok:
                    stats["rejected_generalize"] += 1
                    self._audit(cand, approved=False,
                                method=PromotionMethod.AUTO_LLM.value,
                                reason=gen_reason)
                    continue

                # Triangulation: definitions/formulas need only 1
                tri = self.check_triangulation(cand)
                if cand.fact_type in _SOLO_TYPES:
                    needed = 1
                else:
                    needed = _TRIANGULATION_THRESHOLD

                if tri < needed:
                    cand.rejection_reason = (
                        f"triangulation:{tri}<{needed}"
                    )
                    stats["rejected_triangulation"] += 1
                    self._audit(cand, approved=False,
                                method=PromotionMethod.AUTO_TRIANGULATION.value,
                                reason=cand.rejection_reason)
                    continue

                if self.promote(cand):
                    stats["promoted"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                logger.warning(f"promotion loop err on hyp {cand.hypothesis_id}: {e}")
                stats["errors"] += 1
                continue

        return stats

    # ------------------------------------------------------------------ #
    # Misc utilities                                                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _brain_category(fact_type: str) -> str:
        if fact_type == HypothesisType.FORMULA.value:
            return "formula"
        if fact_type == HypothesisType.DEFINITION.value:
            return "glossary"
        if fact_type == HypothesisType.THRESHOLD.value:
            return "threshold"
        if fact_type == HypothesisType.RULE.value:
            return "rule"
        return "pattern"

    @staticmethod
    def _derive_name(statement: str, category: str) -> str:
        words = re.findall(r"\w+", statement or "")[:5]
        if not words:
            h = hashlib.sha256((statement or "").encode()).hexdigest()[:8]
            return f"{category}_{h}"
        return " ".join(words).title()

    def _decide_method(self, cand: PromotionCandidate) -> str:
        if cand.fact_type in _SOLO_TYPES and self.llm_call_fn is not None:
            return PromotionMethod.AUTO_LLM.value
        return PromotionMethod.AUTO_TRIANGULATION.value


__all__ = ["PromotionPipeline"]
