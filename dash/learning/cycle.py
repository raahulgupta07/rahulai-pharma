"""Cycle orchestrator — chains all self-learning components into one async run.

Flow per cycle:
  1. CuriosityEngine.generate(N=20)
  2. For each top-N question:
       a. ResearcherLoop.research(q) -> dossier
       b. HypothesisEngine.form_from_dossier(q, dossier) -> hypotheses
       c. For each hypothesis: Verifier.verify(h) -> result
       d. If verified: Consolidator.consolidate(h)
  3. forgetting.daily_decay_job()
  4. Promotion (only on central cycle, or every Nth project cycle)
  5. Persist run_row to dash_self_learning_runs

Streams TrainEvent-shaped dicts via async generator for SSE.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncIterator, Callable, Optional

from dash.learning.base import CycleResult

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:  # pragma: no cover
    from dash.obs.trace import start_trace, trace_span, end_trace
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    def start_trace(*_a, **_k):  # type: ignore
        return ""

    def end_trace(*_a, **_k):  # type: ignore
        return None

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


PER_QUESTION_TIMEOUT_S = 120.0   # kpt budget per experiment


class LearningCycle:
    """Single self-learning cycle.

    Set ``project_slug=None`` to run the central learning cycle.
    """

    def __init__(
        self,
        project_slug: Optional[str] = None,
        source_id: Optional[int] = None,
        llm_call_fn: Optional[Callable] = None,
        max_questions: Optional[int] = None,
        run_decay: bool = True,
        run_promotion: bool = False,
        per_question_timeout_s: Optional[float] = None,
        dry_run: bool = False,
    ):
        self.project_slug = project_slug
        self.source_id = source_id

        # Resolve max_questions from admin settings if not explicitly provided
        if max_questions is None:
            try:
                from dash.admin.settings import get_setting
                max_questions = int(
                    get_setting("max_questions_per_cycle", project_slug=project_slug) or 20
                )
            except Exception:
                max_questions = 20

        # Resolve per_question_timeout_s from admin settings if not explicitly provided
        if per_question_timeout_s is None:
            try:
                from dash.admin.settings import get_setting
                per_question_timeout_s = float(
                    get_setting("per_question_timeout_s") or PER_QUESTION_TIMEOUT_S
                )
            except Exception:
                per_question_timeout_s = PER_QUESTION_TIMEOUT_S

        # Allow admin setting to force dry-run mode
        if not dry_run:
            try:
                from dash.admin.settings import get_setting
                dry_run = bool(get_setting("enable_dry_run", project_slug=project_slug))
            except Exception:
                pass

        self.dry_run = dry_run
        if dry_run:
            # Force-disable LLM regardless of caller — kpt "deterministic baseline"
            self.llm_call_fn = None
            # Smaller cap in dry mode for quick canary signal
            self.max_questions = min(int(max_questions or 5), 5)
        else:
            self.llm_call_fn = llm_call_fn
            self.max_questions = max_questions
        self.run_decay = run_decay
        # Central cycle (project_slug=None) always promotes
        self.run_promotion = run_promotion or (project_slug is None)
        self.per_question_timeout_s = per_question_timeout_s

    async def _process_question(self, qobj, researcher, heng, ver, cons, result):
        """Full per-question sub-pipeline. Returns (events, counters)."""
        from dash.learning.base import VerificationStatus

        events = []
        counters = {
            "answered": 0,
            "cost": 0.0,
            "formed": 0,
            "verified": 0,
            "failed": 0,
            "consolidated": 0,
        }

        # Research
        events.append({
            "step": "research",
            "status": "start",
            "message": qobj.question[:100],
        })
        dossier = await researcher.research_async(qobj)
        counters["answered"] = 1
        counters["cost"] += float(getattr(dossier, "total_cost_usd", 0.0) or 0.0)
        events.append({
            "step": "research",
            "status": "done",
            "message": (
                f"{len(dossier.sources)} sources, "
                f"{dossier.triangulation_count} tiers agree"
            ),
        })

        # Hypothesize (with lineage parent if this is a cycle_followup)
        parent_hid = self._lineage_parent_for(qobj)
        hyps = await asyncio.to_thread(
            lambda: heng.form_from_dossier(
                qobj, dossier, parent_hypothesis_id=parent_hid,
            )
        )
        counters["formed"] = len(hyps)
        events.append({
            "step": "hypothesize",
            "status": "done",
            "message": f"{len(hyps)} hypotheses",
        })

        # kpt pattern #14 — Counter-hypothesis (null-hypothesis arm).
        # When enabled, generate a negated twin for every primary hypothesis,
        # verify both, and only keep the primary if it out-scores its counter.
        try:
            from dash.learning import counter_hypothesis as _ch
            if _ch.is_enabled() and hyps:
                counters = _ch.build_counters(hyps)
                if counters:
                    events.append({
                        "step": "counter_hypothesis",
                        "status": "done",
                        "message": f"built {len(counters)} counter-hypotheses",
                    })
                    # Verify counters first; stash scores by counter_of id
                    counter_scores = {}
                    for ch in counters:
                        try:
                            cv = await asyncio.to_thread(ver.verify, ch)
                            counter_scores[id(ch)] = float(getattr(cv, "confidence_delta", 0.0) or 0.0)
                        except Exception:
                            pass
                    # Attach max-counter-score to each primary's metadata for the
                    # downstream verifier loop to consult (advisory only).
                    if counter_scores:
                        worst = max(counter_scores.values())
                        for h in hyps:
                            try:
                                m = dict(getattr(h, "metadata", None) or {})
                                m["counter_score"] = worst
                                h.metadata = m
                            except Exception:
                                pass
        except Exception as e:
            logger.warning(f"counter_hypothesis hook failed: {e}")

        # Verify + consolidate
        for h in hyps:
            vres = await asyncio.to_thread(ver.verify, h)
            if vres.status == VerificationStatus.VERIFIED.value:
                counters["verified"] += 1
                h.verification_status = vres.status
                h.confidence = max(
                    0.0,
                    min(1.0, h.confidence + (vres.confidence_delta or 0.0)),
                )
                cres = await asyncio.to_thread(cons.consolidate, h)
                if cres.targets:
                    counters["consolidated"] += 1
            elif vres.status == VerificationStatus.FAILED.value:
                counters["failed"] += 1
        events.append({
            "step": "verify",
            "status": "done",
            "message": (
                f"verified={counters['verified']} "
                f"failed={counters['failed']}"
            ),
        })

        return events, counters

    async def run(self) -> AsyncIterator[dict]:
        """Yield event dicts: {step, status, message?, count?, cost?}."""
        result = CycleResult(
            project_slug=self.project_slug,
            cycle_num=self._next_cycle_num(),
            started_at=datetime.utcnow(),
        )
        run_id = self._insert_run_row(result)

        # Begin root trace for this learning cycle.
        start_trace("learning", project_slug=self.project_slug, name="learning.cycle")

        # Cost ceiling pre-flight
        try:
            from dash.learning.cost_guard import get_status, pause_until_tomorrow
            cs = await asyncio.to_thread(get_status, self.project_slug)
            if cs.over_budget:
                if self.project_slug:
                    await asyncio.to_thread(pause_until_tomorrow, self.project_slug)
                result.error = f"daily cost cap reached (${cs.today_spend_usd:.2f}/${cs.daily_cap_usd:.2f})"
                self._update_run_row(run_id, result, status="failed")
                yield {"step": "cycle", "status": "error", "message": result.error}
                try:
                    end_trace("error", result.error)
                except Exception:  # noqa: BLE001
                    pass
                return
        except Exception as e:
            logger.warning(f"cost guard check failed: {e}")

        try:
            yield {"step": "cycle", "status": "start",
                   "message": f"dry_run={self.dry_run}"}
            # ----------------------------------------------------------------
            # Phase 1 — Curiosity
            # ----------------------------------------------------------------
            yield {"step": "curiosity", "status": "start"}
            from dash.learning.curiosity import CuriosityEngine

            with trace_span("learning.curiosity", kind="learning",
                            project_slug=self.project_slug):
                ce = CuriosityEngine(
                    project_slug=self.project_slug,
                    source_id=self.source_id,
                    llm_call_fn=self.llm_call_fn,
                )
                questions = await asyncio.to_thread(
                    ce.generate,
                    max_questions=self.max_questions,
                    cycle_num=result.cycle_num,
                )
                result.questions_generated = len(questions)
            yield {
                "step": "curiosity",
                "status": "done",
                "message": f"{len(questions)} questions",
            }

            # ----------------------------------------------------------------
            # Phase 2 — research + hypothesize + verify + consolidate per Q
            # ----------------------------------------------------------------
            from dash.learning.researcher import ResearcherLoop
            from dash.learning.hypothesis import HypothesisEngine
            from dash.learning.verifier import Verifier
            from dash.learning.consolidator import Consolidator
            from dash.learning.base import Question, VerificationStatus

            researcher = ResearcherLoop(
                project_slug=self.project_slug,
                source_id=self.source_id,
                llm_call_fn=self.llm_call_fn,
            )
            heng = HypothesisEngine(
                project_slug=self.project_slug,
                source_id=self.source_id,
                llm_call_fn=self.llm_call_fn,
            )
            ver = Verifier(
                project_slug=self.project_slug,
                llm_call_fn=self.llm_call_fn,
            )
            cons = Consolidator(llm_call_fn=self.llm_call_fn)

            for q in questions[: self.max_questions]:
                qobj = Question(
                    id=q.get("id"),
                    project_slug=self.project_slug,
                    source_id=self.source_id,
                    question=q.get("question", ""),
                    topic=q.get("topic"),
                    domain=q.get("domain"),
                    reason=q.get("reason") or "gap",
                    metadata=q.get("metadata") or {},
                )

                # kpt pattern #15 — Cost-ROI gate. Skip questions whose
                # expected cost exceeds expected information gain.
                try:
                    from dash.learning import roi_gate as _rg
                    if _rg.is_enabled():
                        decision = _rg.evaluate(qobj)
                        if decision.skip:
                            yield {
                                "step": "roi_gate",
                                "status": "skip",
                                "message": (
                                    f"skip Q (cost=${decision.expected_cost:.3f} "
                                    f"gain={decision.expected_gain:.2f}): {decision.reason}"
                                ),
                            }
                            self._mark_answered(qobj.id)
                            continue
                except Exception as e:
                    logger.warning(f"roi_gate hook failed: {e}")

                try:
                    with trace_span("learning.research_pipeline", kind="learning",
                                    project_slug=self.project_slug):
                        events, counters = await asyncio.wait_for(
                            self._process_question(
                                qobj, researcher, heng, ver, cons, result
                            ),
                            timeout=self.per_question_timeout_s,
                        )
                    for ev in events:
                        yield ev
                    result.questions_answered += counters["answered"]
                    result.cost_usd += counters["cost"]
                    result.hypotheses_formed += counters["formed"]
                    result.hypotheses_verified += counters["verified"]
                    result.hypotheses_failed += counters["failed"]
                    result.facts_consolidated += counters["consolidated"]
                except asyncio.TimeoutError:
                    logger.warning(
                        f"question timeout after {self.per_question_timeout_s}s: "
                        f"{qobj.question[:100]}"
                    )
                    yield {
                        "step": "timeout",
                        "status": "error",
                        "message": (
                            f"skipped Q after {self.per_question_timeout_s}s: "
                            f"{qobj.question[:80]}"
                        ),
                    }
                except Exception as e:
                    logger.warning(f"question failed: {e}")
                    yield {
                        "step": "question",
                        "status": "error",
                        "message": str(e)[:200],
                    }

                # mark question answered in DB
                self._mark_answered(qobj.id)

                # Soft cost check between phases — short-circuit if approaching cap
                try:
                    from dash.learning.cost_guard import get_status as _cg_status
                    _cs = await asyncio.to_thread(_cg_status, self.project_slug)
                    if _cs.daily_cap_usd > 0 and (_cs.today_spend_usd + result.cost_usd) >= 0.9 * _cs.daily_cap_usd:
                        yield {
                            "step": "cycle",
                            "status": "warn",
                            "message": (
                                f"approaching cost cap "
                                f"(${_cs.today_spend_usd + result.cost_usd:.2f}/"
                                f"${_cs.daily_cap_usd:.2f}); short-circuiting"
                            ),
                        }
                        break
                except Exception:
                    pass

            # ----------------------------------------------------------------
            # Phase 3 — Forgetting curve
            # ----------------------------------------------------------------
            if self.run_decay:
                yield {"step": "forgetting", "status": "start"}
                try:
                    from dash.learning.forgetting import daily_decay_job

                    with trace_span("learning.forgetting", kind="learning",
                                    project_slug=self.project_slug):
                        dr = await asyncio.to_thread(daily_decay_job)
                    yield {
                        "step": "forgetting",
                        "status": "done",
                        "message": (
                            f"decayed={dr.decayed_count} "
                            f"archived={dr.archived_count} "
                            f"resistant={dr.promoted_resistant_count}"
                        ),
                    }
                except Exception as e:
                    logger.warning(f"decay failed: {e}")
                    yield {
                        "step": "forgetting",
                        "status": "error",
                        "message": str(e)[:200],
                    }

            # ----------------------------------------------------------------
            # Phase 4 — Promotion (central or periodic project)
            # ----------------------------------------------------------------
            if self.run_promotion:
                # Admin gate: enable_promotion_to_central
                try:
                    from dash.admin.settings import get_setting
                    if not get_setting("enable_promotion_to_central", project_slug=self.project_slug):
                        yield {
                            "step": "promotion",
                            "status": "done",
                            "message": "disabled by admin setting",
                        }
                        self.run_promotion = False
                except Exception:
                    pass
            if self.run_promotion:
                try:
                    from dash.learning.promotion import PromotionPipeline  # type: ignore

                    pp = PromotionPipeline(llm_call_fn=self.llm_call_fn)
                    with trace_span("learning.promotion", kind="learning",
                                    project_slug=self.project_slug):
                        pres = await asyncio.to_thread(pp.run_promotion_cycle)
                    result.facts_promoted = int(pres.get("promoted", 0) or 0)
                    yield {
                        "step": "promotion",
                        "status": "done",
                        "message": (
                            f"promoted={result.facts_promoted} "
                            f"rejected={pres.get('rejected', 0)}"
                        ),
                    }
                except ImportError:
                    yield {
                        "step": "promotion",
                        "status": "done",
                        "message": "PromotionPipeline not yet available",
                    }
                except Exception as e:
                    logger.warning(f"promotion failed: {e}")
                    yield {
                        "step": "promotion",
                        "status": "error",
                        "message": str(e)[:200],
                    }

            # ----------------------------------------------------------------
            # Done
            # ----------------------------------------------------------------
            result.completed_at = datetime.utcnow()
            self._update_run_row(run_id, result, status="completed")

            # ----------------------------------------------------------------
            # Phase 5 — Agent IQ snapshot
            # ----------------------------------------------------------------
            try:
                from dash.learning.agent_iq import compute as _iq_compute, persist_to_run as _iq_persist
                snap = await asyncio.to_thread(_iq_compute, self.project_slug)
                if run_id is not None:
                    await asyncio.to_thread(_iq_persist, run_id, snap)
                yield {
                    "step": "agent_iq",
                    "status": "done",
                    "message": f"agent_iq={snap.agent_iq:.1f}",
                    "components": snap.components,
                }
            except Exception as e:
                logger.warning(f"agent_iq snapshot failed: {e}")

            # ----------------------------------------------------------------
            # kpt pattern #13 — Eval pinning + regression detection.
            # Compares this cycle's verified/formed ratio against trailing
            # window. Emits a 'warn' event on regression — never blocks.
            # ----------------------------------------------------------------
            try:
                from dash.learning import eval_pinning as _ep
                if _ep.is_enabled():
                    pin = await asyncio.to_thread(
                        _ep.check, self.project_slug,
                        result.hypotheses_verified,
                        result.hypotheses_formed,
                        result.hypotheses_failed,
                    )
                    yield {
                        "step": "eval_pinning",
                        "status": "warn" if pin.regression else "done",
                        "message": (
                            f"score={pin.score:.2f} "
                            f"trailing_avg={pin.trailing_avg:.2f} "
                            f"delta={pin.delta:+.2f}"
                            f"{' REGRESSION' if pin.regression else ''}"
                        ),
                    }
            except Exception as e:
                logger.warning(f"eval_pinning hook failed: {e}")

            # ----------------------------------------------------------------
            # Phase 6 — Today's discoveries digest
            # ----------------------------------------------------------------
            try:
                from dash.learning.digest import generate as gen_digest
                with trace_span("learning.digest", kind="learning",
                                project_slug=self.project_slug):
                    digest = await asyncio.to_thread(
                        gen_digest, self.project_slug, result.cycle_num, run_id,
                        llm_call_fn=self.llm_call_fn,
                    )
                yield {
                    "step": "digest",
                    "status": "done",
                    "message": digest.summary[:200] if digest.summary else "no summary",
                }
            except Exception as e:
                logger.warning(f"digest failed: {e}")

            # ----------------------------------------------------------------
            # Phase 7 — SkillRefinery: rolling tool utility scores
            # ----------------------------------------------------------------
            try:
                from dash.tools.skill_refinery import compute_utility_scores
                scored = await asyncio.to_thread(
                    compute_utility_scores, self.project_slug
                )
                yield {
                    "step": "skill_refinery_score",
                    "status": "done",
                    "message": f"scored {len(scored)} tools",
                }
            except Exception as e:
                logger.warning(f"skill_refinery scoring failed: {e}")

            yield {
                "step": "cycle",
                "status": "done",
                "message": json.dumps({
                    "questions": result.questions_generated,
                    "answered": result.questions_answered,
                    "verified": result.hypotheses_verified,
                    "consolidated": result.facts_consolidated,
                    "promoted": result.facts_promoted,
                    "cost_usd": round(result.cost_usd, 4),
                }),
            }

        except Exception as e:
            logger.exception(f"cycle failed: {e}")
            result.error = str(e)[:500]
            self._update_run_row(run_id, result, status="failed")
            yield {"step": "cycle", "status": "error", "message": result.error}
        finally:
            # Close the root learning trace (fires on success, error, or
            # generator close). Fail-soft.
            try:
                end_trace("error" if result.error else "done", result.error)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _next_cycle_num(self) -> int:
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine

            with get_sql_engine().connect() as conn:
                row = conn.execute(text(
                    "SELECT COALESCE(MAX(cycle_num), 0) + 1 "
                    "FROM public.dash_self_learning_runs "
                    "WHERE project_slug IS NOT DISTINCT FROM :s"
                ), {"s": self.project_slug}).fetchone()
                return int(row[0]) if row else 1
        except Exception:
            return 1

    def _insert_run_row(self, result: CycleResult) -> Optional[int]:
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine

            with get_sql_engine().connect() as conn:
                row = conn.execute(text(
                    "INSERT INTO public.dash_self_learning_runs "
                    "(project_slug, cycle_num, status, started_at, metadata) "
                    "VALUES (:slug, :n, 'running', NOW(), "
                    " CAST(:meta AS JSONB)) RETURNING id"
                ), {
                    "slug": self.project_slug,
                    "n": result.cycle_num,
                    "meta": json.dumps({"dry_run": bool(self.dry_run)}),
                }).fetchone()
                conn.commit()
                return int(row[0]) if row else None
        except Exception as e:
            logger.warning(f"run row insert failed: {e}")
            return None

    def _update_run_row(
        self, run_id: Optional[int], result: CycleResult, *, status: str
    ):
        if run_id is None:
            return
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine

            with get_sql_engine().connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_self_learning_runs SET "
                    " status = :st, "
                    " questions_generated = :qg, "
                    " questions_answered = :qa, "
                    " hypotheses_formed = :hf, "
                    " hypotheses_verified = :hv, "
                    " hypotheses_failed = :hx, "
                    " facts_consolidated = :fc, "
                    " facts_promoted = :fp, "
                    " cost_usd = :cost, "
                    " completed_at = NOW(), "
                    " duration_seconds = EXTRACT(EPOCH FROM NOW() - started_at)::int, "
                    " error = :err "
                    "WHERE id = :id"
                ), {
                    "st": status,
                    "qg": result.questions_generated,
                    "qa": result.questions_answered,
                    "hf": result.hypotheses_formed,
                    "hv": result.hypotheses_verified,
                    "hx": result.hypotheses_failed,
                    "fc": result.facts_consolidated,
                    "fp": result.facts_promoted,
                    "cost": result.cost_usd,
                    "err": result.error,
                    "id": run_id,
                })
                conn.commit()
        except Exception as e:
            logger.warning(f"run row update failed: {e}")

    def _lineage_parent_for(self, qobj) -> Optional[int]:
        """Return parent hypothesis id for a cycle_followup question.

        Order:
        1. Explicit ``parent_hypothesis_id`` in question.metadata.
        2. Fallback: most recent verified hypothesis in same project.
        Returns None for non-followup questions.
        """
        try:
            if (getattr(qobj, "reason", "") or "").lower() != "cycle_followup":
                return None
            meta = getattr(qobj, "metadata", None) or {}
            pid = meta.get("parent_hypothesis_id")
            if isinstance(pid, int) and pid > 0:
                return pid
            from sqlalchemy import text
            from db.session import get_sql_engine
            with get_sql_engine().connect() as conn:
                row = conn.execute(text(
                    "SELECT id FROM public.dash_hypotheses "
                    "WHERE (project_slug = :s OR (:s IS NULL AND project_slug IS NULL)) "
                    "  AND verification_status = 'verified' "
                    "ORDER BY COALESCE(verified_at, created_at) DESC NULLS LAST "
                    "LIMIT 1"
                ), {"s": self.project_slug}).fetchone()
                return int(row[0]) if row else None
        except Exception:
            return None

    def _mark_answered(self, question_id: Optional[int]):
        if question_id is None:
            return
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine

            with get_sql_engine().connect() as conn:
                conn.execute(text(
                    "UPDATE public.dash_curiosity_questions "
                    "SET status = 'answered', answered_at = NOW() "
                    "WHERE id = :id"
                ), {"id": question_id})
                conn.commit()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Convenience helpers used by the HTTP layer
# ----------------------------------------------------------------------


async def stream_cycle_sse(cycle: LearningCycle) -> AsyncIterator[str]:
    """Wrap LearningCycle.run() events into ``data: {json}\\n\\n`` SSE frames."""
    async for evt in cycle.run():
        try:
            yield f"data: {json.dumps(evt)}\n\n"
        except Exception:
            yield f"data: {json.dumps({'step': 'cycle', 'status': 'error', 'message': 'serialize_error'})}\n\n"
