"""
User Agent Engine
=================

Native, self-contained replacement for the former MiroFish HTTP client.
Uses Dash internals (LLM helpers, Agno Agent, pgvector memory) instead of
calling out to an external multi-agent simulation service.

Public API (preserved 1:1 from the old MirofishClient so agents_api.py works
unchanged):

    async build_persona(user_id, signals)                   -> dict
    async build_graph(user_id, seed_data)                   -> str (graph_id)
    async chat(agent_id, message, on_token)                 -> None  (streams)
    async run_simulation(graph_id, scenario, horizon,
                         seed_tables, actors)               -> dict
    async get_sim_status(sim_id)                            -> dict
    async recall_memory(agent_id, query, limit=5)           -> list[dict]

A module-level singleton is exposed as get_engine().
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


class EngineError(RuntimeError):
    """Raised for unrecoverable engine errors."""


# ---------------------------------------------------------------------------
# Helpers — lazy imports keep import-time cost low and avoid circulars.
# ---------------------------------------------------------------------------


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def _deep_llm(prompt: str, task: str = "deep_analysis") -> Optional[str]:
    """Synchronous LLM call via dash.settings.training_llm_call."""
    from dash.settings import training_llm_call
    return training_llm_call(prompt, task)


async def _deep_llm_async(prompt: str, task: str = "deep_analysis") -> Optional[str]:
    return await asyncio.to_thread(_deep_llm, prompt, task)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class UserAgentEngine:
    """Self-contained digital-twin engine. No external service dependency."""

    # 1. PERSONA --------------------------------------------------------

    async def build_persona(self, user_id: str, signals: dict) -> dict:
        """Synthesize a persona JSON from user signals via deep LLM."""
        prompt = (
            "You build a concise digital-twin persona for a business user. "
            "Given these signals (recent chats, project memberships, role, evals), "
            "return ONLY a single JSON object with keys: "
            "role (string), expertise (string[]), decision_style (string), "
            "risk_tolerance (one of 'low'|'medium'|'high'), "
            "vocab_style (string). No prose, no markdown.\n\n"
            f"USER_ID: {user_id}\n"
            f"SIGNALS:\n{json.dumps(signals, default=str)[:6000]}"
        )
        raw = await _deep_llm_async(prompt, "deep_analysis")
        if not raw:
            return {
                "role": signals.get("role") or "analyst",
                "expertise": [],
                "decision_style": "data-driven",
                "risk_tolerance": "medium",
                "vocab_style": "concise",
            }
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("persona JSON parse failed: %s", e)
        return {"role": "analyst", "expertise": [], "decision_style": "data-driven",
                "risk_tolerance": "medium", "vocab_style": "concise", "_raw": raw[:500]}

    # 2. GRAPH ----------------------------------------------------------

    async def build_graph(self, user_id: str, seed_data: dict) -> str:
        """Return a stable graph_id for this user's knowledge graph.

        Tries to invoke dash.tools.knowledge_graph.build_knowledge_graph when a
        project_slug is supplied; otherwise stubs with a uuid and logs.
        """
        graph_id = str(uuid.uuid4())
        slug = (seed_data or {}).get("project_slug")
        if slug:
            try:
                from dash.tools.knowledge_graph import build_knowledge_graph
                await asyncio.to_thread(build_knowledge_graph, slug)
                logger.info("knowledge graph built for project=%s graph_id=%s", slug, graph_id)
            except Exception as e:
                logger.warning("build_knowledge_graph failed (stubbed): %s", e)
        else:
            logger.info("build_graph stub graph_id=%s (no project_slug in seed_data)", graph_id)
        return graph_id

    # 3. CHAT -----------------------------------------------------------

    async def chat(
        self,
        agent_id: str,
        message: str,
        on_token: Callable[[str], Awaitable[None]],
    ) -> None:
        """Build an Agno digital-twin Agent on the fly and stream its reply."""
        persona = await asyncio.to_thread(self._load_persona, agent_id)
        instructions = (
            "You are this user's digital twin. Mirror their decision style, "
            "vocabulary, and risk tolerance. Be concise and decisive.\n"
            f"PERSONA: {json.dumps(persona, default=str)[:2000]}"
        )

        try:
            from agno.agent import Agent
            from dash.settings import MODEL
            twin = Agent(
                id=f"twin-{agent_id}",
                name="DigitalTwin",
                model=MODEL,
                instructions=instructions,
                markdown=True,
            )
        except Exception as e:
            logger.exception("twin agent build failed")
            await on_token(f"[engine error: {e}]")
            return

        try:
            # agno Agent supports async streaming via arun(stream=True), yielding
            # RunContentEvent objects whose `.content` carries the delta (or full
            # message for providers that don't token-stream, e.g. Gemini via
            # OpenRouter). Fall back to sync run() if arun isn't iterable.
            try:
                async for event in twin.arun(message, stream=True):
                    tok = getattr(event, "content", None) or getattr(event, "delta", None) or ""
                    if tok:
                        await on_token(str(tok))
            except TypeError:
                # arun returned a coroutine — execute sync iterator in a thread.
                run = await asyncio.to_thread(twin.run, message, stream=True)
                for event in run:
                    tok = getattr(event, "content", None) or getattr(event, "delta", None) or ""
                    if tok:
                        await on_token(str(tok))
        except Exception as e:
            logger.exception("twin chat stream failed")
            await on_token(f"[error: {e}]")

    def _load_persona(self, agent_id: str) -> dict:
        try:
            with _engine().connect() as conn:
                row = conn.execute(
                    text("SELECT persona_json FROM dash.user_agents WHERE id = :a"),
                    {"a": agent_id},
                ).fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception as e:
            logger.warning("load persona failed: %s", e)
        return {}

    # 4. SIMULATION -----------------------------------------------------

    async def run_simulation(
        self,
        graph_id: str,
        scenario: str,
        horizon: str,
        seed_tables: list,
        actors: int,
    ) -> dict:
        """Create a queued simulation row and fire-and-forget the runner."""
        sim_id = str(uuid.uuid4())
        # The dash.agent_simulations row is created by agents_api.start_sim — we
        # update it here in-place using remote_sim_id == this sim_id.
        asyncio.create_task(self._run_sim_task(
            sim_id=sim_id, graph_id=graph_id, scenario=scenario,
            horizon=horizon, seed_tables=seed_tables or [], actors=max(1, int(actors or 1)),
        ))
        return {"sim_id": sim_id, "status": "queued"}

    async def _run_sim_task(
        self, sim_id: str, graph_id: str, scenario: str,
        horizon: str, seed_tables: list, actors: int,
    ):
        """Background simulation: spawn actor personas, gather reactions, write report."""
        try:
            await self._update_sim(sim_id, status="running", progress=5)

            # Spawn actor reactions in parallel via LITE_MODEL.
            actor_tasks = [
                _deep_llm_async(
                    f"You are actor #{i+1} reacting to scenario over {horizon}.\n"
                    f"SCENARIO: {scenario}\nSEED_TABLES: {seed_tables}\n"
                    "Respond in 2 sentences with your action and rationale.",
                    "mining",
                )
                for i in range(min(actors, 10))
            ]
            reactions = await asyncio.gather(*actor_tasks, return_exceptions=True)
            reactions = [r for r in reactions if isinstance(r, str) and r]
            await self._update_sim(sim_id, status="running", progress=60)

            # Deep synthesis to a final report.
            joined = "\n".join(f"- {r}" for r in reactions) or "(no reactions)"
            report_prompt = (
                f"Write a concise simulation report (markdown). "
                f"Scenario: {scenario}. Horizon: {horizon}. Actors: {actors}.\n"
                f"Actor reactions:\n{joined}\n\n"
                "Sections: Summary, Key Dynamics, Risks, Recommendations."
            )
            report = await _deep_llm_async(report_prompt, "deep_analysis") or "(empty report)"

            result = {"graph_id": graph_id, "actor_reactions": reactions}
            await self._update_sim(
                sim_id, status="done", progress=100,
                report_md=report, result_json=result,
            )
        except Exception as e:
            logger.exception("simulation task failed: %s", sim_id)
            await self._update_sim(sim_id, status="failed", progress=100, error=str(e)[:500])

    async def _update_sim(self, sim_id: str, **fields):
        def _do():
            sets, params = [], {"sid": sim_id}
            for k, v in fields.items():
                if k == "result_json":
                    sets.append("result_json = result_json || CAST(:result_json AS jsonb)")
                    params["result_json"] = json.dumps(v)
                elif k in ("status", "progress", "report_md", "error"):
                    sets.append(f"{k} = :{k}")
                    params[k] = v
            if not sets:
                return
            sets.append("finished_at = CASE WHEN :status_check IN ('done','failed') "
                        "THEN now() ELSE finished_at END")
            params["status_check"] = fields.get("status", "")
            sql = (f"UPDATE dash.agent_simulations SET {', '.join(sets)} "
                   "WHERE result_json->>'remote_sim_id' = :sid")
            try:
                with _engine().begin() as conn:
                    conn.execute(text(sql), params)
            except Exception as e:
                logger.warning("update_sim failed (%s): %s", sim_id, e)
        await asyncio.to_thread(_do)

    async def get_sim_status(self, sim_id: str) -> dict:
        def _do():
            with _engine().connect() as conn:
                row = conn.execute(
                    text("SELECT status, progress, report_md, result_json, error "
                         "FROM dash.agent_simulations WHERE result_json->>'remote_sim_id' = :sid"),
                    {"sid": sim_id},
                ).fetchone()
            if not row:
                return {"status": "unknown", "progress": 0, "report_md": "",
                        "result_json": {}, "error": "sim not found"}
            return {
                "status": row[0], "progress": row[1] or 0,
                "report_md": row[2] or "",
                "result_json": row[3] if isinstance(row[3], dict) else {},
                "error": row[4],
            }
        return await asyncio.to_thread(_do)

    # 5. MEMORY RECALL --------------------------------------------------

    async def recall_memory(self, agent_id: str, query: str, limit: int = 5) -> list[dict]:
        """pgvector cosine search over dash.agent_memory_events.embedding."""
        try:
            from dash.tools.embeddings_helper import embed_text
            qvec = await embed_text(query)
        except Exception as e:
            logger.warning("recall_memory embed failed: %s", e)
            return []

        if not qvec:
            return []

        # Format vector for pgvector literal.
        vec_literal = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"

        def _do():
            try:
                with _engine().connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT id, event_type, payload, ts, "
                            "1 - (embedding <=> CAST(:v AS vector)) AS similarity "
                            "FROM dash.agent_memory_events "
                            "WHERE agent_id = :a AND embedding IS NOT NULL "
                            "ORDER BY embedding <=> CAST(:v AS vector) "
                            "LIMIT :lim"
                        ),
                        {"a": agent_id, "v": vec_literal, "lim": max(1, min(limit, 50))},
                    ).fetchall()
                return [
                    {
                        "id": str(r[0]),
                        "event_type": r[1],
                        "payload": r[2],
                        "ts": r[3].isoformat() if r[3] else None,
                        "similarity": float(r[4]) if r[4] is not None else None,
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.warning("recall_memory query failed: %s", e)
                return []

        return await asyncio.to_thread(_do)

    # Compatibility shims -------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Always enabled — no external dependency to gate on."""
        return True

    async def _request(self, method: str, path: str, **kwargs):
        """No-op stub for legacy callers (e.g. cleanup paths in agents_api)."""
        logger.debug("user_agent_engine._request noop %s %s", method, path)
        return {}


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_engine_singleton: Optional[UserAgentEngine] = None


def get_engine() -> UserAgentEngine:
    global _engine_singleton
    if _engine_singleton is None:
        _engine_singleton = UserAgentEngine()
    return _engine_singleton
