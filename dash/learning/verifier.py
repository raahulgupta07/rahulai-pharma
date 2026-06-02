"""Verifier — test a hypothesis against real data.

Methods:
1. SQL test: ask LLM to write a SQL query that would prove/disprove the
   hypothesis. Run against provider.engine_ro. Compare result to expectation.
2. Cross-source test: if multiple sources, run same query across both;
   if results agree, +confidence; if disagree, -confidence.
3. LLM review: send hypothesis + evidence to DEEP_MODEL with high-reasoning
   for sanity check. LLM returns 'agree'|'disagree'|'inconclusive'.
4. Eval-pipeline integration: add hypothesis as a Q&A pair → existing eval.

Updates dash_hypotheses.verification_status + confidence per outcome.
Returns VerificationResult dataclass.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional, Callable

from dash.learning.base import (
    Hypothesis, VerificationResult, VerificationStatus, HypothesisType,
)

logger = logging.getLogger(__name__)


class Verifier:
    def __init__(
        self,
        project_slug: Optional[str] = None,
        llm_call_fn: Optional[Callable] = None,
        dash_engine=None,
    ):
        self.project_slug = project_slug
        self.llm_call_fn = llm_call_fn
        self.dash_engine = dash_engine

    # -----------------------------------------------------------------
    # Public entry
    # -----------------------------------------------------------------

    def verify(self, hypothesis: Hypothesis) -> VerificationResult:
        """Run verification methods in priority order; return outcome."""
        # Skip definition/formula types — those don't have a falsification SQL
        if hypothesis.hypothesis_type in (
            HypothesisType.DEFINITION.value,
            HypothesisType.FORMULA.value,
        ):
            return self._verify_via_llm(hypothesis)

        # Try SQL verification first (if provider available)
        sql_result = self._verify_via_sql(hypothesis)
        if sql_result.status != VerificationStatus.PENDING.value:
            self._persist(hypothesis.id, sql_result)
            return sql_result

        # Fall back to LLM review
        return self._verify_via_llm(hypothesis)

    # -----------------------------------------------------------------
    # Method 1: SQL test
    # -----------------------------------------------------------------

    def _verify_via_sql(self, h: Hypothesis) -> VerificationResult:
        """Ask LLM to draft a test SQL, run it, interpret result."""
        if self.llm_call_fn is None or not self.project_slug:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="sql_skipped",
                evidence={"reason": "no llm or project_slug"},
            )

        # Get provider engine
        provider = self._get_provider()
        if provider is None or getattr(provider, "engine_ro", None) is None:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="sql_skipped",
                evidence={"reason": "no provider engine"},
            )

        dialect = getattr(provider, "dialect", "postgres")

        # Ask LLM for test SQL
        prompt = f"""Hypothesis: {h.statement}
Type: {h.hypothesis_type}

Write a SINGLE read-only SQL query for {dialect} that would prove or
disprove this hypothesis. Output ONLY the SQL (no markdown, no explanation).
Limit results to 100 rows. Use TOP for tsql, LIMIT for postgres/mysql.
"""
        try:
            sql = self.llm_call_fn(prompt, task='deep_analysis')
        except Exception as e:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="sql_skipped",
                evidence={"reason": f"llm error: {str(e)[:200]}"},
            )

        if not sql:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="sql_skipped",
                evidence={"reason": "llm returned no sql"},
            )

        sql = self._clean_sql(sql)
        if not self._is_safe_sql(sql):
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="sql_unsafe",
                evidence={"reason": "sql contains write keywords", "sql": sql[:500]},
            )

        # Execute
        try:
            from sqlalchemy import text
            with provider.engine_ro.connect() as conn:
                # Defensive per-execution timeout: best-effort. SQLAlchemy
                # does not accept a ``timeout`` kwarg in execution_options
                # for the sync Connection API, so this is a no-op there
                # and we rely on the engine-level ``begin`` listener
                # (Edits 1-3) to enforce statement_timeout server-side.
                try:
                    conn.execution_options(timeout=110)
                except Exception:  # noqa: BLE001
                    pass
                result = conn.execute(text(sql))
                rows = result.fetchmany(100)
                cols = list(result.keys()) if hasattr(result, 'keys') else []
        except Exception as e:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.FAILED.value,
                method="sql_exec_error",
                failed_reason=str(e)[:300],
                evidence={"sql": sql[:500]},
                confidence_delta=-0.10,
            )

        # Interpret with LLM
        sample = "\n".join(str(r)[:200] for r in rows[:10])
        interpret_prompt = f"""Hypothesis: {h.statement}
SQL: {sql}
Results ({len(rows)} rows): {sample}

Did the data SUPPORT, REFUTE, or INCONCLUSIVE this hypothesis?
Respond w/ JSON: {{"verdict": "support|refute|inconclusive", "reason": "..."}}
"""
        try:
            verdict = self.llm_call_fn(interpret_prompt, task='extraction')
        except Exception as e:
            verdict = None
            logger.warning(f"verifier interpret llm failed: {e}")

        verdict_data = self._parse_json(verdict)
        v = (verdict_data or {}).get("verdict", "inconclusive").lower()
        reason = (verdict_data or {}).get("reason", "")

        if v == "support":
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.VERIFIED.value,
                method="sql",
                evidence={"sql": sql[:500], "row_count": len(rows), "reason": reason},
                confidence_delta=+0.20,
            )
        elif v == "refute":
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.FAILED.value,
                method="sql",
                failed_reason=reason,
                evidence={"sql": sql[:500], "row_count": len(rows)},
                confidence_delta=-0.30,
            )
        else:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PARTIAL.value,
                method="sql",
                evidence={"sql": sql[:500], "row_count": len(rows), "reason": reason},
                confidence_delta=+0.05,
            )

    # -----------------------------------------------------------------
    # Method 3: LLM review
    # -----------------------------------------------------------------

    def _verify_via_llm(self, h: Hypothesis) -> VerificationResult:
        """LLM-only sanity check (used for definitions/formulas)."""
        if self.llm_call_fn is None:
            return VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PENDING.value,
                method="llm_skipped",
                evidence={"reason": "no llm"},
            )
        prompt = f"""Hypothesis: {h.statement}
Type: {h.hypothesis_type}

Is this a well-known, generally accepted statement in its domain?
Respond JSON: {{"verdict": "agree|disagree|inconclusive", "reason": "..."}}
"""
        try:
            ans = self.llm_call_fn(prompt, task='deep_analysis')
        except Exception as e:
            logger.warning(f"verifier llm review failed: {e}")
            ans = None

        data = self._parse_json(ans)
        v = (data or {}).get("verdict", "inconclusive").lower()
        reason = (data or {}).get("reason", "")

        if v == "agree":
            res = VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.VERIFIED.value,
                method="llm_review",
                evidence={"reason": reason},
                confidence_delta=+0.15,
            )
        elif v == "disagree":
            res = VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.FAILED.value,
                method="llm_review",
                failed_reason=reason,
                confidence_delta=-0.25,
            )
        else:
            res = VerificationResult(
                hypothesis_id=h.id or 0,
                status=VerificationStatus.PARTIAL.value,
                method="llm_review",
                evidence={"reason": reason},
                confidence_delta=0.0,
            )
        self._persist(h.id, res)
        return res

    # -----------------------------------------------------------------
    # Method 2: Cross-source check (stub for Phase 2)
    # -----------------------------------------------------------------

    def cross_source_check(
        self, hypothesis: Hypothesis, sources: list,
    ) -> VerificationResult:
        """If 2+ sources, run same SQL on each; agreement = bonus confidence."""
        if len(sources) < 2:
            return VerificationResult(
                hypothesis_id=hypothesis.id or 0,
                status=VerificationStatus.PENDING.value,
                method="cross_source_skipped",
                evidence={"reason": "<2 sources"},
            )
        return VerificationResult(
            hypothesis_id=hypothesis.id or 0,
            status=VerificationStatus.PARTIAL.value,
            method="cross_source",
            evidence={"sources_checked": len(sources)},
            confidence_delta=+0.05,
        )

    # -----------------------------------------------------------------
    # Method 4: Eval-pipeline integration (stub for Phase 2)
    # -----------------------------------------------------------------

    def eval_pipeline_check(self, hypothesis: Hypothesis) -> VerificationResult:
        """Add hypothesis as a Q&A eval pair → existing eval pipeline.

        Phase 2: integrates with dash_evals. For now, returns PENDING.
        """
        return VerificationResult(
            hypothesis_id=hypothesis.id or 0,
            status=VerificationStatus.PENDING.value,
            method="eval_skipped",
            evidence={"reason": "eval-pipeline integration deferred"},
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _get_provider(self):
        try:
            from dash.providers import get_registry
            providers = get_registry().list_for_project(self.project_slug or "")
            for p in providers:
                if getattr(p, "engine_ro", None) is not None and not getattr(p, "degraded", False):
                    return p
        except Exception as e:
            logger.debug(f"verifier _get_provider failed: {e}")
        return None

    def _is_safe_sql(self, sql: str) -> bool:
        unsafe = re.compile(
            r'\b(insert|update|delete|drop|alter|truncate|grant|revoke|merge|create|replace)\b',
            re.IGNORECASE,
        )
        if unsafe.search(sql):
            return False
        # must start with SELECT or WITH
        head = sql.lstrip().lower()
        return head.startswith("select") or head.startswith("with")

    def _clean_sql(self, sql: str) -> str:
        # strip markdown fences
        s = sql.strip()
        if s.startswith("```"):
            parts = s.split("```", 2)
            if len(parts) >= 2:
                s = parts[1]
                if s.startswith("sql"):
                    s = s[3:].strip()
                s = s.rsplit("```", 1)[0].strip()
        # remove trailing semicolons (multi-statement protection)
        s = s.rstrip(";").strip()
        return s

    def _parse_json(self, text_in: Optional[str]) -> Optional[dict]:
        if not text_in:
            return None
        s = text_in.strip()
        if s.startswith("```"):
            parts = s.split("```", 2)
            if len(parts) >= 2:
                s = parts[1]
                if s.startswith("json"):
                    s = s[4:].strip()
                s = s.rsplit("```", 1)[0].strip()
        try:
            return json.loads(s)
        except Exception:
            # try to find first {...} object
            try:
                m = re.search(r'\{.*\}', s, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception:
                pass
            return None

    def _persist(self, hypothesis_id: Optional[int], result: VerificationResult) -> None:
        if hypothesis_id is None:
            return
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            with eng.connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_hypotheses SET "
                    " verification_status = :vs, "
                    " verified_by = :by, "
                    " verified_at = NOW(), "
                    " failed_reason = :fr, "
                    " confidence = LEAST(1.0, GREATEST(0.0, confidence + :delta)), "
                    " metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:ev AS jsonb) "
                    "WHERE id = :id"
                ), {
                    "vs": result.status,
                    "by": result.method,
                    "fr": result.failed_reason,
                    "delta": result.confidence_delta,
                    "ev": json.dumps(result.evidence),
                    "id": hypothesis_id,
                })
                conn.commit()
        except Exception as e:
            logger.warning(f"verifier persist failed: {e}")
