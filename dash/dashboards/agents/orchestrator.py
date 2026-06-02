"""Orchestrator: coordinates Scout + Designer for multi-agent dashboard build."""
import logging
import asyncio
import re
import time
from typing import AsyncGenerator
from .contracts import Finding, DesignDecision, AgentEvent

logger = logging.getLogger(__name__)


_TITLE_LEAK_RE = [
    re.compile(r"critical\s+style\s+rule.*", re.I),
    re.compile(r"fast\s+mode\b.*", re.I),
    re.compile(r"^build\s+dashboard\s+covering[:\s]*", re.I),
    re.compile(r"^\d+\)\s*"),
]


def _sanitize_title(s: str) -> str:
    s = (s or "").strip()
    for pat in _TITLE_LEAK_RE:
        s = pat.sub("", s).strip()
    s = re.sub(r"^[\s\-—:>•*#]+", "", s)
    s = s.split("\n")[0].strip()
    return s[:60] or "Dashboard"

class DashboardOrchestrator:
    def __init__(self, project_slug: str, prompt: str, chat_context: dict | None = None,
                 persona: str = "", budget: dict | None = None,
                 query_intent: str = "private"):
        self.project_slug = project_slug
        self.prompt = prompt
        self.chat_context = chat_context or {}
        self.persona = persona
        self.query_intent = query_intent or "private"
        self.budget = budget or {"max_findings": 12, "max_cells": 25, "rounds": 5, "tokens_max": 80000}
        self.findings: list[Finding] = []
        self.decisions: list[DesignDecision] = []
        self.spec: dict = self._empty_spec()
        self.tokens_used = 0
        self.start_time = time.time()

    def _empty_spec(self) -> dict:
        return {
            "id": f"dash_{int(time.time())}",
            "title": _sanitize_title(self.prompt) if self.prompt else "Dashboard",
            "project_slug": self.project_slug,
            "persona": self.persona,
            "filters": [],
            "cells": [],
            "theme": "light",
            "template": "executive",
            "insights": [],
        }

    def _should_stop(self) -> bool:
        return (
            len(self.spec["cells"]) >= self.budget["max_cells"]
            or self.tokens_used >= self.budget["tokens_max"]
            or (time.time() - self.start_time) > 120
        )

    async def stream(self) -> AsyncGenerator[dict, None]:
        """Yield AgentEvent dicts as agents work."""
        # Lazy import to avoid circular
        try:
            from . import scout, designer
        except ImportError as e:
            yield AgentEvent(type="error", msg=f"agents not ready: {e}").model_dump()
            return

        # WHY: bind ContextVar so all downstream tools (policy engine, scout, sanitizer) see intent
        prev_intent = "private"
        try:
            from dash.tools.skill_refinery import get_query_intent, set_request_context
            prev_intent = get_query_intent()
            set_request_context(query_intent=self.query_intent)
        except Exception:
            pass

        # ROUND 1 — Discovery
        yield AgentEvent(type="scout_thinking", agent="scout", msg="🔍 surveying tables...").model_dump()
        try:
            new_findings = await scout.discover(
                self.project_slug, self.prompt, self.chat_context, self.persona,
                max_findings=self.budget["max_findings"]
            )
            for f in new_findings:
                self.findings.append(f if isinstance(f, Finding) else Finding(**f))
                yield AgentEvent(type="scout_finding", agent="scout",
                                 msg=f"💡 {f.headline if isinstance(f, Finding) else f.get('headline','')}",
                                 data=(f.model_dump() if isinstance(f, Finding) else f)).model_dump()
        except Exception as e:
            logger.exception("scout discovery failed")
            yield AgentEvent(type="error", msg=f"scout error: {e}").model_dump()

        if not self.findings:
            yield AgentEvent(type="error", msg="no findings discovered").model_dump()
            return

        # ROUND 2 — Design
        yield AgentEvent(type="designer_thinking", agent="designer", msg="🎨 designing layout...").model_dump()
        try:
            decisions = await designer.design(self.findings, self.persona, self.prompt)
            for d in decisions:
                dd = d if isinstance(d, DesignDecision) else DesignDecision(**d)
                self.decisions.append(dd)
                cell = self._decision_to_cell(dd)
                self.spec["cells"].append(cell)
                yield AgentEvent(type="cell_added", agent="designer",
                                 msg=f"✓ {dd.title}", data={"cell": cell}).model_dump()
                if self._should_stop(): break
        except Exception as e:
            logger.exception("designer failed")
            yield AgentEvent(type="error", msg=f"designer error: {e}").model_dump()

        # ROUND 3 — Drill (top 3 high-severity findings)
        if not self._should_stop():
            high = [f for f in self.findings if f.severity == "high"][:3]
            for f in high:
                if self._should_stop(): break
                yield AgentEvent(type="scout_thinking", agent="scout",
                                 msg=f"🔍 drilling: {f.headline[:50]}...").model_dump()
                try:
                    drills = await scout.drill(self.project_slug, f, self.persona)
                    for df in drills:
                        dfo = df if isinstance(df, Finding) else Finding(**df)
                        self.findings.append(dfo)
                        yield AgentEvent(type="scout_finding", agent="scout",
                                         msg=f"  ↳ {dfo.headline}", data=dfo.model_dump()).model_dump()
                    if drills:
                        ddrill = await designer.design([Finding(**df) if isinstance(df, dict) else df for df in drills], self.persona, self.prompt)
                        for d in ddrill:
                            dd = d if isinstance(d, DesignDecision) else DesignDecision(**d)
                            cell = self._decision_to_cell(dd)
                            self.spec["cells"].append(cell)
                            yield AgentEvent(type="cell_added", agent="designer",
                                             msg=f"  ✓ {dd.title}", data={"cell": cell}).model_dump()
                            if self._should_stop(): break
                except Exception as e:
                    logger.warning(f"drill failed: {e}")

        # ROUND 4 — Critique
        if not self._should_stop():
            yield AgentEvent(type="scout_thinking", agent="scout",
                             msg="🔍 reviewing dashboard for gaps...").model_dump()
            try:
                gaps = await scout.critique(self.findings, self.persona, self.prompt)
                if gaps:
                    yield AgentEvent(type="scout_finding", agent="scout",
                                     msg=f"📋 found {len(gaps)} gaps").model_dump()
                    decs = await designer.design([Finding(**g) if isinstance(g, dict) else g for g in gaps[:5]], self.persona, self.prompt)
                    for d in decs:
                        dd = d if isinstance(d, DesignDecision) else DesignDecision(**d)
                        cell = self._decision_to_cell(dd)
                        self.spec["cells"].append(cell)
                        yield AgentEvent(type="cell_added", agent="designer",
                                         msg=f"  ✓ gap closed: {dd.title}", data={"cell": cell}).model_dump()
                        if self._should_stop(): break
            except Exception as e:
                logger.warning(f"critique failed: {e}")

        # ROUND 5 — Enrich (per cell finding+cause+action text)
        if not self._should_stop():
            yield AgentEvent(type="designer_thinking", agent="designer",
                             msg="🎨 enriching cells with insights...").model_dump()
            try:
                self.spec = await designer.enrich(self.spec, self.findings)
            except Exception as e:
                logger.warning(f"enrich failed: {e}")

        # Done
        elapsed = round(time.time() - self.start_time, 1)
        try:
            from dash.tools.skill_refinery import set_request_context
            set_request_context(query_intent=prev_intent)
        except Exception:
            pass
        yield AgentEvent(type="done", agent="orchestrator",
                         msg=f"✓ done. {len(self.spec['cells'])} cells in {elapsed}s",
                         data={"spec": self.spec, "query_intent": self.query_intent}).model_dump()

    def _decision_to_cell(self, d: DesignDecision) -> dict:
        # Phase G — propagate finding_hash so frontend can post keep/dismiss back.
        finding_hash = None
        try:
            for f in self.findings:
                if getattr(f, "id", None) == d.finding_id:
                    finding_hash = getattr(f, "finding_hash", None)
                    if not finding_hash:
                        from . import memory_loop
                        finding_hash = memory_loop.hash_finding(f)
                    break
        except Exception:
            finding_hash = None
        return {
            "id": f"cell_{d.finding_id}_{int(time.time()*1000)}" if d.finding_id else f"cell_{int(time.time()*1000)}",
            "type": d.cell_type,
            "grid": d.grid,
            "title": d.title,
            "config": {
                **d.config,
                "headline": d.headline_text,
                "palette_role": d.palette_role,
                "chart_type": d.chart_type,
                "drill_into": d.drill_into,
                "finding_hash": finding_hash,
            },
        }

    def run_sync(self) -> dict:
        """Sync run for non-streaming endpoint. Collects all events, returns final spec."""
        async def _collect():
            async for _ in self.stream():
                pass
        try:
            asyncio.run(_collect())
        except RuntimeError:
            # already in event loop
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_collect())
            loop.close()
        return self.spec
