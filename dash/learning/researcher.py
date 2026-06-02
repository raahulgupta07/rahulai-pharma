"""ResearcherLoop — fan out a question across all evidence tiers.

Tiers (configurable per cycle):
  1. INTERNAL_DB        — query own SQL data
  2. INTERNAL_KG        — search knowledge graph
  3. INTERNAL_BRAIN     — search company brain
  4. INTERNAL_MEMORY    — search dash_memories
  5. LLM_DEEP_THINK     — DEEP_MODEL high-reasoning
  6. WEB_SEARCH         — Tavily/Brave/Perplexity
  7. EXTERNAL_API       — FRED/Wiki/WorldBank/etc.

Each tier returns ResearchSource[]. Aggregate into ResearchDossier.
LLM synthesizes a final summary. Triangulation count = # of tiers
that produced >=1 source w/ confidence >= 0.5.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime
from typing import Optional, Callable

from dash.learning.base import (
    Question, ResearchSource, ResearchDossier, ResearchTier,
)

logger = logging.getLogger(__name__)


def _with_timeout(conn, seconds: int = 30):
    """Apply a defensive per-statement timeout via execution_options.

    Falls back silently if the SQLAlchemy version doesn't accept the
    ``timeout`` kwarg — engine-level ``begin`` listeners on the providers
    (and ``get_sql_engine`` for the dash main engine) handle the actual
    ``statement_timeout`` enforcement at the DB layer.
    """
    try:
        return conn.execution_options(timeout=seconds)
    except TypeError:
        return conn


class ResearcherLoop:
    def __init__(
        self,
        project_slug: Optional[str] = None,
        source_id: Optional[int] = None,
        llm_call_fn: Optional[Callable] = None,
        dash_engine=None,
        enabled_tiers: Optional[list[str]] = None,
    ):
        self.project_slug = project_slug
        self.source_id = source_id
        self.llm_call_fn = llm_call_fn
        self.dash_engine = dash_engine

        # Resolve enabled tiers from admin settings if not explicitly provided
        if enabled_tiers is None:
            try:
                from dash.admin.settings import get_setting
                tiers: list[str] = []
                if get_setting("enable_self_learning", project_slug=project_slug):
                    tiers.append(ResearchTier.INTERNAL_DB.value)
                    tiers.append(ResearchTier.INTERNAL_KG.value)
                    tiers.append(ResearchTier.INTERNAL_BRAIN.value)
                    tiers.append(ResearchTier.INTERNAL_MEMORY.value)
                    tiers.append(ResearchTier.LLM_DEEP_THINK.value)
                if (get_setting("enable_tavily")
                        or get_setting("enable_brave_search")
                        or get_setting("enable_perplexity")):
                    tiers.append(ResearchTier.WEB_SEARCH.value)
                if (get_setting("enable_fred")
                        or get_setting("enable_wikipedia")
                        or get_setting("enable_world_bank")
                        or get_setting("enable_alpha_vantage")
                        or get_setting("enable_wikidata")):
                    tiers.append(ResearchTier.EXTERNAL_API.value)
                enabled_tiers = tiers if tiers else None
            except Exception:
                pass

        self.enabled_tiers = enabled_tiers or [
            ResearchTier.INTERNAL_DB.value,
            ResearchTier.INTERNAL_KG.value,
            ResearchTier.INTERNAL_BRAIN.value,
            ResearchTier.INTERNAL_MEMORY.value,
            ResearchTier.LLM_DEEP_THINK.value,
            ResearchTier.WEB_SEARCH.value,
            ResearchTier.EXTERNAL_API.value,
        ]
        self._domain_cache: Optional[dict] = None

    def _get_domain(self) -> dict:
        """Lazy-load detected domain from disk. Cached per-instance."""
        if self._domain_cache is not None:
            return self._domain_cache
        try:
            from dash.learning.domain_detector import load
            d = load(self.project_slug or "", self.source_id or 0)
            self._domain_cache = d or {"primary": "generic", "secondaries": []}
        except Exception:
            self._domain_cache = {"primary": "generic", "secondaries": []}
        return self._domain_cache

    def _domain_prompt_block(self) -> str:
        """Domain-aware preamble for LLM prompts. Empty if no domain detected."""
        d = self._get_domain()
        primary = d.get("primary") or "generic"
        secondaries = d.get("secondaries", []) or []
        if primary == "generic" and not secondaries:
            return ""
        domains_str = primary
        if secondaries:
            domains_str += f" + {', '.join(secondaries)}"
        return (
            f"You are a domain expert in: {domains_str}. "
            f"Use industry-standard terminology and benchmarks for these domains. "
            f"Reference common metrics, formulas, and patterns specific to "
            f"{domains_str}.\n\n"
        )

    def research(self, question: Question, *, max_per_tier: int = 5) -> ResearchDossier:
        """Sync wrapper. Uses asyncio.run if no loop, else falls back to sync impl
        to avoid nested-loop crash. New callers should prefer ``research_async``.
        """
        try:
            asyncio.get_running_loop()
            # Already inside an event loop — can't asyncio.run() here.
            # Fall back to original sequential implementation.
            return self.research_sync(question, max_per_tier=max_per_tier)
        except RuntimeError:
            return asyncio.run(
                self.research_async(question, max_per_tier=max_per_tier)
            )

    async def research_async(
        self,
        question: Question,
        *,
        max_per_tier: int = 5,
        per_tier_timeout: float = 30.0,
    ) -> ResearchDossier:
        """Run all enabled tiers in parallel via asyncio.gather.

        Each tier runs in a worker thread with a per-tier timeout. Per-tier
        failures (timeout, exception) don't kill the whole research run.
        """
        dossier = ResearchDossier(
            question_id=question.id,
            question_text=question.question,
        )

        tier_methods = {
            ResearchTier.INTERNAL_DB.value:        self._tier_internal_db,
            ResearchTier.INTERNAL_KG.value:        self._tier_internal_kg,
            ResearchTier.INTERNAL_BRAIN.value:     self._tier_internal_brain,
            ResearchTier.INTERNAL_MEMORY.value:    self._tier_internal_memory,
            ResearchTier.LLM_DEEP_THINK.value:     self._tier_llm,
            ResearchTier.WEB_SEARCH.value:         self._tier_web,
            ResearchTier.EXTERNAL_API.value:       self._tier_external_api,
        }

        async def _run_tier(name: str):
            method = tier_methods.get(name)
            if method is None:
                return name, []
            try:
                t0 = time.time()
                sources = await asyncio.wait_for(
                    asyncio.to_thread(method, question, max_per_tier=max_per_tier),
                    timeout=per_tier_timeout,
                )
                dt = time.time() - t0
                logger.debug(
                    f"tier {name}: {len(sources or [])} sources in {dt:.2f}s"
                )
                return name, sources or []
            except asyncio.TimeoutError:
                logger.warning(f"tier {name} timeout after {per_tier_timeout}s")
                return name, []
            except Exception as e:
                logger.warning(f"tier {name} failed: {e}")
                return name, []

        tasks = [_run_tier(t) for t in self.enabled_tiers]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        agreement_tiers = set()
        for tier_name, sources in results:
            for s in sources:
                s.fetched_at = datetime.utcnow()
                dossier.sources.append(s)
                dossier.total_cost_usd += s.cost_usd
            if any(s.confidence >= 0.5 for s in sources):
                agreement_tiers.add(tier_name)

        dossier.triangulation_count = len(agreement_tiers)

        # Final LLM summary if we have any data
        if dossier.sources and self.llm_call_fn:
            try:
                dossier.summary = await asyncio.to_thread(
                    self._llm_synthesize, question, dossier
                )
                dossier.total_cost_usd += 0.02
            except Exception as e:
                logger.warning(f"summary failed: {e}")
                dossier.summary = "[summary unavailable]"

        return dossier

    def research_sync(self, question: Question, *, max_per_tier: int = 5) -> ResearchDossier:
        """Original sequential implementation. Kept as fallback for callers
        already running inside an event loop."""
        dossier = ResearchDossier(
            question_id=question.id,
            question_text=question.question,
        )

        tier_methods = {
            ResearchTier.INTERNAL_DB.value:        self._tier_internal_db,
            ResearchTier.INTERNAL_KG.value:        self._tier_internal_kg,
            ResearchTier.INTERNAL_BRAIN.value:     self._tier_internal_brain,
            ResearchTier.INTERNAL_MEMORY.value:    self._tier_internal_memory,
            ResearchTier.LLM_DEEP_THINK.value:     self._tier_llm,
            ResearchTier.WEB_SEARCH.value:         self._tier_web,
            ResearchTier.EXTERNAL_API.value:       self._tier_external_api,
        }

        agreement_tiers = set()

        for tier_name in self.enabled_tiers:
            method = tier_methods.get(tier_name)
            if method is None:
                continue
            try:
                t0 = time.time()
                sources = method(question, max_per_tier=max_per_tier)
                dt = time.time() - t0
                logger.debug(f"tier {tier_name}: {len(sources)} sources in {dt:.2f}s")
                for s in sources:
                    s.fetched_at = datetime.utcnow()
                    dossier.sources.append(s)
                    dossier.total_cost_usd += s.cost_usd
                if any(s.confidence >= 0.5 for s in sources):
                    agreement_tiers.add(tier_name)
            except Exception as e:
                logger.warning(f"tier {tier_name} failed: {e}")

        dossier.triangulation_count = len(agreement_tiers)

        # Final LLM summary if we have any data
        if dossier.sources and self.llm_call_fn:
            try:
                dossier.summary = self._llm_synthesize(question, dossier)
                dossier.total_cost_usd += 0.02
            except Exception as e:
                logger.warning(f"summary failed: {e}")
                dossier.summary = "[summary unavailable]"

        return dossier

    # ----- Internal tiers -----

    def _tier_internal_db(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Search own SQL data — find related table/column references in
        question, return sample stats. Cheap heuristic."""
        out = []
        if not self.project_slug:
            return out
        try:
            from sqlalchemy import text
            eng = self.dash_engine or self._get_engine()
            if eng is None:
                return out
            qlow = q.question.lower()
            with eng.connect() as _raw_conn:
                conn = _with_timeout(_raw_conn, 30)
                rows = conn.execute(text(
                    "SELECT table_name FROM public.dash_table_metadata "
                    "WHERE project_slug = :s LIMIT 50"
                ), {"s": self.project_slug}).fetchall()
            for r in rows:
                tbl = r[0]
                if tbl.lower() in qlow or any(p in qlow for p in tbl.lower().split("_")):
                    out.append(ResearchSource(
                        tier=ResearchTier.INTERNAL_DB.value,
                        source=f"table:{tbl}",
                        snippet=f"Project table '{tbl}' may be relevant",
                        confidence=0.6,
                    ))
                    if len(out) >= max_per_tier:
                        break
        except Exception as e:
            logger.debug(f"internal_db tier: {e}")
        return out

    def _tier_internal_kg(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Search dash_knowledge_triples for entities mentioned in question."""
        out = []
        try:
            from sqlalchemy import text
            eng = self.dash_engine or self._get_engine()
            if eng is None:
                return out
            words = [w for w in q.question.lower().split() if len(w) > 4]
            for w in words[:8]:
                with eng.connect() as _raw_conn:
                    conn = _with_timeout(_raw_conn, 30)
                    rows = conn.execute(text(
                        "SELECT subject, predicate, object FROM public.dash_knowledge_triples "
                        "WHERE (project_slug = :s OR project_slug IS NULL) "
                        "AND (LOWER(subject) LIKE :p OR LOWER(object) LIKE :p) "
                        "LIMIT :n"
                    ), {"s": self.project_slug, "p": f"%{w}%", "n": max_per_tier}).fetchall()
                for r in rows:
                    out.append(ResearchSource(
                        tier=ResearchTier.INTERNAL_KG.value,
                        source="knowledge_graph",
                        snippet=f"{r[0]} -[{r[1]}]-> {r[2]}",
                        confidence=0.75,
                    ))
                    if len(out) >= max_per_tier:
                        return out
        except Exception as e:
            logger.debug(f"internal_kg tier: {e}")
        return out

    def _tier_internal_brain(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Search dash_company_brain for matching definitions/formulas."""
        out = []
        try:
            from sqlalchemy import text
            eng = self.dash_engine or self._get_engine()
            if eng is None:
                return out
            words = [w for w in q.question.lower().split() if len(w) > 4]
            for w in words[:6]:
                with eng.connect() as _raw_conn:
                    conn = _with_timeout(_raw_conn, 30)
                    rows = conn.execute(text(
                        "SELECT name, definition, category FROM public.dash_company_brain "
                        "WHERE (project_slug = :s OR project_slug IS NULL) "
                        "AND (LOWER(name) LIKE :p OR LOWER(definition) LIKE :p) "
                        "LIMIT :n"
                    ), {"s": self.project_slug, "p": f"%{w}%", "n": max_per_tier}).fetchall()
                for r in rows:
                    defn = (r[1] or "")[:400]
                    out.append(ResearchSource(
                        tier=ResearchTier.INTERNAL_BRAIN.value,
                        source=f"brain:{r[0]}",
                        snippet=f"{r[0]} ({r[2]}): {defn}",
                        confidence=0.85,
                    ))
                    if len(out) >= max_per_tier:
                        return out
        except Exception as e:
            logger.debug(f"internal_brain tier: {e}")
        return out

    def _tier_internal_memory(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Search dash_memories for relevant past learnings."""
        out = []
        try:
            from sqlalchemy import text
            eng = self.dash_engine or self._get_engine()
            if eng is None:
                return out
            words = [w for w in q.question.lower().split() if len(w) > 4]
            seen = set()
            for w in words[:6]:
                with eng.connect() as _raw_conn:
                    conn = _with_timeout(_raw_conn, 30)
                    rows = conn.execute(text(
                        "SELECT id, fact, source, confidence_score FROM public.dash_memories "
                        "WHERE (project_slug = :s OR scope = 'global') "
                        "AND (archived IS NULL OR archived = FALSE) "
                        "AND LOWER(fact) LIKE :p "
                        "ORDER BY confidence_score DESC NULLS LAST LIMIT :n"
                    ), {"s": self.project_slug, "p": f"%{w}%", "n": max_per_tier}).fetchall()
                for r in rows:
                    if r[0] in seen:
                        continue
                    seen.add(r[0])
                    out.append(ResearchSource(
                        tier=ResearchTier.INTERNAL_MEMORY.value,
                        source=f"memory:{r[2] or 'unknown'}:{r[0]}",
                        snippet=(r[1] or "")[:400],
                        confidence=float(r[3] or 0.5),
                    ))
                    if len(out) >= max_per_tier:
                        return out
        except Exception as e:
            logger.debug(f"internal_memory tier: {e}")
        return out

    def _tier_llm(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Ask LLM (DEEP_MODEL) for analytical answer."""
        if self.llm_call_fn is None:
            return []
        domain_block = self._domain_prompt_block()
        prompt = (
            f"{domain_block}"
            f"Q: {q.question}\n\n"
            f"Topic: {q.topic or 'general'}\n"
            f"Domain: {q.domain or 'general'}\n\n"
            f"Provide a 200-word analysis. List 3 key points with reasoning. "
            f"If you don't know, say so. Do NOT speculate without basis."
        )
        try:
            ans = self.llm_call_fn(prompt, task='deep_analysis')
            if not ans:
                return []
            return [ResearchSource(
                tier=ResearchTier.LLM_DEEP_THINK.value,
                source="llm:deep_model",
                snippet=ans[:1500],
                confidence=0.75,
                cost_usd=0.05,
            )]
        except Exception as e:
            logger.warning(f"llm tier failed: {e}")
            return []

    def _tier_web(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """Web search via Tavily/Brave/Perplexity."""
        try:
            from dash.learning.web_search import search
        except Exception:
            return []
        try:
            d = self._get_domain()
            primary = d.get("primary") or ""
            if primary and primary != "generic":
                web_query = f"{q.question} {primary}"
            else:
                web_query = q.question
            resp = search(web_query, max_results=max_per_tier)
            if resp.error:
                return []
            out = []
            for r in resp.results[:max_per_tier]:
                out.append(ResearchSource(
                    tier=ResearchTier.WEB_SEARCH.value,
                    source=f"web:{r.source}",
                    url=r.url,
                    snippet=f"{r.title}: {r.snippet}"[:600],
                    confidence=float(r.score or 0.6),
                    cost_usd=resp.cost_usd / max(1, len(resp.results)),
                ))
            if resp.summary:
                out.append(ResearchSource(
                    tier=ResearchTier.WEB_SEARCH.value,
                    source=f"web:{resp.source_type}:summary",
                    snippet=resp.summary[:1500],
                    confidence=0.7,
                ))
            return out
        except Exception as e:
            logger.warning(f"web tier: {e}")
            return []

    def _tier_external_api(self, q: Question, max_per_tier: int) -> list[ResearchSource]:
        """FRED, Wikipedia, WorldBank, Wikidata, AlphaVantage."""
        try:
            from dash.learning.external_data import fetch_facts
        except Exception:
            return []
        try:
            d = self._get_domain()
            domain = q.domain or d.get("primary") or "all"
            facts = fetch_facts(q.question, domain=domain,
                                limit_per_source=max_per_tier)
            return [
                ResearchSource(
                    tier=ResearchTier.EXTERNAL_API.value,
                    source=f"api:{f.source}",
                    url=f.url,
                    snippet=f"{f.title}: {f.value}"[:600],
                    confidence=float(f.confidence),
                )
                for f in facts[:max_per_tier * 2]
            ]
        except Exception as e:
            logger.warning(f"external_api tier: {e}")
            return []

    # ----- Synthesis -----

    def _llm_synthesize(self, q: Question, dossier: ResearchDossier) -> str:
        """Ask LLM to summarize the dossier into 2-3 paragraph answer."""
        domain_block = self._domain_prompt_block()
        evidence = "\n\n".join(
            f"[{s.tier}/{s.source}] {s.snippet}" for s in dossier.best_evidence(8)
        )
        prompt = (
            f"{domain_block}"
            f"Question: {q.question}\n\n"
            f"Evidence collected ({len(dossier.sources)} sources, "
            f"{dossier.triangulation_count} tiers in agreement):\n\n"
            f"{evidence}\n\n"
            f"Synthesize a 200-word answer. Note where sources agree (high confidence) "
            f"and disagree (flag uncertainty). End with: CONFIDENCE: high|medium|low."
        )
        return self.llm_call_fn(prompt, task='deep_analysis') or ""

    # ----- Helpers -----

    def _get_engine(self):
        try:
            from db.session import get_sql_engine
            return get_sql_engine()
        except Exception:
            return None
