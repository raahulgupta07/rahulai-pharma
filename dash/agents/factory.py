"""Dash-OS Phase 11A — Sub-agent factory.

Persists every spawned sub-agent definition on first call, then reuses the
saved row on subsequent spawns by (project_slug, name). Other agents in the
same project can list + reuse via name.

Behind EXPERIMENTAL_AGI=1. All operations schema-qualify ``dash.<table>``.

Public surface:
    SubAgentSpec               dataclass
    AgentFactory.upsert_definition(spec, project_slug, created_by_agent, created_by_user)
    AgentFactory.build_or_reuse(name, project_slug=None)
    AgentFactory.instantiate(spec)
    AgentFactory._generate_agent_md(spec)        # exposed for tests
    AgentFactory._resolve_scoped_tools(names)    # exposed for tests
"""
from __future__ import annotations

import json as _json
import logging
import os
import secrets
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Flag ────────────────────────────────────────────────────────────────────
def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


# ── DB helper ───────────────────────────────────────────────────────────────
def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


# ── Spec ────────────────────────────────────────────────────────────────────
@dataclass
class SubAgentSpec:
    name: str
    purpose: str
    base_agent: str = "Analyst"
    scoped_skills: List[str] = field(default_factory=list)   # skill ids
    scoped_tools: List[str] = field(default_factory=list)    # tool names
    persona: Optional[str] = None
    extra_instructions: str = ""


# ── Known safe tool registry (Dash core) ────────────────────────────────────
# These are tool names a spawned sub-agent may use. Filtered by spawner's
# allowed-tool list (ContextVar) when set; else default safe set is allowed.
_KNOWN_TOOL_LOADERS: Dict[str, str] = {
    # name -> "module:attr" lazy loader spec
    "run_sql_query": "dash.tools.live_query:run_sql_query",
    "search_all": "dash.tools.semantic_search:search_all",
    "discover_skills": "dash.tools.skill_refinery:discover_skills",
    "load_skill_tool": "dash.tools.skill_refinery:load_skill_tool",
    "discover_tables": "dash.tools.introspect:discover_tables",
    # ── VentureDesk (Deal Analyst template) ──
    "dcf":                     "dash.tools.venture_tools:dcf",
    "irr_moic":                "dash.tools.venture_tools:irr_moic",
    "sensitivity_grid":        "dash.tools.venture_tools:sensitivity_grid",
    "unit_economics":          "dash.tools.venture_tools:unit_economics",
    "partner_fit_score":       "dash.tools.venture_tools:partner_fit_score",
    "save_deal":               "dash.tools.venture_tools:save_deal",
    "save_scenario":           "dash.tools.venture_tools:save_scenario",
    "list_deals":              "dash.tools.venture_tools:list_deals",
    "seed_capability_weights": "dash.tools.venture_tools:seed_capability_weights",
    # ── Ops Optimizer (post-investment portfolio operations) ──
    "register_portco":     "dash.tools.ops_tools:register_portco",
    "ingest_kpis":         "dash.tools.ops_tools:ingest_kpis",
    "kpi_dashboard":       "dash.tools.ops_tools:kpi_dashboard",
    "detect_anomalies":    "dash.tools.ops_tools:detect_anomalies",
    "propose_value_play":  "dash.tools.ops_tools:propose_value_play",
    "update_initiative":   "dash.tools.ops_tools:update_initiative",
    "portfolio_health":    "dash.tools.ops_tools:portfolio_health",
    "generate_board_pack": "dash.tools.ops_tools:generate_board_pack",
    "benchmark_portco":    "dash.tools.ops_tools:benchmark_portco",
    "watchlist_add":       "dash.tools.ops_tools:watchlist_add",
    # ── Market Sentinel (external intelligence) ──
    "ingest_market_signal":      "dash.tools.market_tools:ingest_market_signal",
    "search_signals":            "dash.tools.market_tools:search_signals",
    "estimate_tam_sam":          "dash.tools.market_tools:estimate_tam_sam",
    "competitor_map":            "dash.tools.market_tools:competitor_map",
    "trend_detect":              "dash.tools.market_tools:trend_detect",
    "link_signals_to_deal":      "dash.tools.market_tools:link_signals_to_deal",
    "summarize_market_for_memo": "dash.tools.market_tools:summarize_market_for_memo",
    "refresh_competitor_shares": "dash.tools.market_tools:refresh_competitor_shares",
    # ── Supply Sentry (Sprint 3 — supply chain risk) ──
    "register_supplier":             "dash.tools.supply_tools:register_supplier",
    "link_sku":                      "dash.tools.supply_tools:link_sku",
    "ingest_supplier_event":         "dash.tools.supply_tools:ingest_supplier_event",
    "score_supplier":                "dash.tools.supply_tools:score_supplier",
    "detect_supply_anomaly":         "dash.tools.supply_tools:detect_supply_anomaly",
    "cross_tenant_exposure":         "dash.tools.supply_tools:cross_tenant_exposure",
    "propose_alt_supplier":          "dash.tools.supply_tools:propose_alt_supplier",
    "resilience_scorecard":          "dash.tools.supply_tools:resilience_scorecard",
    "news_scan_suppliers":           "dash.tools.supply_tools:news_scan_suppliers",
    "generate_supply_risk_report":   "dash.tools.supply_tools:generate_supply_risk_report",
}
_DEFAULT_SAFE_TOOLS = (
    "run_sql_query", "search_all", "discover_skills", "load_skill_tool",
    # VentureDesk tools — safe (pure-math + project-scoped writes)
    "dcf", "irr_moic", "sensitivity_grid", "unit_economics",
    "partner_fit_score", "save_deal", "save_scenario", "list_deals",
    "seed_capability_weights",
    # Ops Optimizer — project-scoped portfolio operations writes
    "register_portco", "ingest_kpis", "kpi_dashboard", "detect_anomalies",
    "propose_value_play", "update_initiative", "portfolio_health",
    "generate_board_pack", "benchmark_portco", "watchlist_add",
    # Market Sentinel — external intelligence
    "ingest_market_signal", "search_signals", "estimate_tam_sam",
    "competitor_map", "trend_detect", "link_signals_to_deal",
    "summarize_market_for_memo", "refresh_competitor_shares",
    # Supply Sentry — supply chain risk
    "register_supplier", "link_sku", "ingest_supplier_event",
    "score_supplier", "detect_supply_anomaly", "cross_tenant_exposure",
    "propose_alt_supplier", "resilience_scorecard", "news_scan_suppliers",
    "generate_supply_risk_report",
)


# ── AgentFactory ────────────────────────────────────────────────────────────
class AgentFactory:
    """Factory that always persists definitions on first spawn."""

    # ------------------------------------------------------------------
    @staticmethod
    def _generate_agent_md(spec: SubAgentSpec) -> str:
        """Compose SKILL.md-style markdown spec with YAML frontmatter."""
        skills_yaml = "[" + ", ".join(repr(s) for s in (spec.scoped_skills or [])) + "]"
        tools_yaml = "[" + ", ".join(repr(t) for t in (spec.scoped_tools or [])) + "]"
        body = (spec.extra_instructions or "").strip()
        if not body:
            body = (
                f"You are **{spec.name}**, a focused sub-agent.\n\n"
                f"Purpose: {spec.purpose}\n\n"
                "Stay strictly within the scope of your purpose. Use only the tools and "
                "skills listed in your frontmatter. Return concise, factual results."
            )
        persona_line = f"persona: {spec.persona}\n" if spec.persona else ""
        return (
            "---\n"
            f"name: {spec.name}\n"
            f"purpose: {spec.purpose}\n"
            f"base_agent: {spec.base_agent}\n"
            f"scoped_skills: {skills_yaml}\n"
            f"scoped_tools: {tools_yaml}\n"
            f"{persona_line}"
            "---\n\n"
            f"# {spec.name}\n\n"
            f"{body}\n"
        )

    # ------------------------------------------------------------------
    @staticmethod
    def upsert_definition(
        spec: SubAgentSpec,
        project_slug: Optional[str],
        created_by_agent: Optional[str],
        created_by_user: Optional[int],
    ) -> Dict[str, Any]:
        """INSERT new row OR UPDATE on (project_slug, name) conflict.

        Returns ``{ok, id, was_new, usage_count}``. ``agent_md`` is regenerated
        from the spec every call so edits are picked up.
        """
        eng = _get_engine()
        agent_md = AgentFactory._generate_agent_md(spec)
        if eng is None:
            # No DB — synthesize an in-proc id so callers can still proceed in tests.
            return {
                "ok": True,
                "id": "cag_" + secrets.token_hex(4),
                "was_new": True,
                "usage_count": 0,
                "agent_md": agent_md,
                "in_proc": True,
            }
        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, usage_count FROM dash.dash_custom_agents "
                        "WHERE project_slug IS NOT DISTINCT FROM :ps AND name = :nm"
                    ),
                    {"ps": project_slug, "nm": spec.name},
                ).first()
                if row:
                    conn.execute(
                        text(
                            "UPDATE dash.dash_custom_agents SET "
                            "  purpose=:pu, base_agent=:ba, agent_md=:md, "
                            "  scoped_skills=CAST(:ss AS jsonb), scoped_tools=CAST(:st AS jsonb), "
                            "  persona=:per, extra_instructions=:ei, updated_at=now() "
                            "WHERE id=:id"
                        ),
                        {
                            "id": row[0], "pu": spec.purpose, "ba": spec.base_agent,
                            "md": agent_md,
                            "ss": _json.dumps(spec.scoped_skills or []),
                            "st": _json.dumps(spec.scoped_tools or []),
                            "per": spec.persona, "ei": spec.extra_instructions,
                        },
                    )
                    return {
                        "ok": True, "id": row[0], "was_new": False,
                        "usage_count": int(row[1] or 0), "agent_md": agent_md,
                    }
                new_id = "cag_" + secrets.token_hex(4)
                conn.execute(
                    text(
                        "INSERT INTO dash.dash_custom_agents "
                        "(id, project_slug, name, purpose, base_agent, agent_md, "
                        " scoped_skills, scoped_tools, persona, extra_instructions, "
                        " created_by_agent, created_by_user, source) "
                        "VALUES (:id,:ps,:nm,:pu,:ba,:md,"
                        " CAST(:ss AS jsonb), CAST(:st AS jsonb),:per,:ei,"
                        " :cba,:cbu,'spawned')"
                    ),
                    {
                        "id": new_id, "ps": project_slug, "nm": spec.name,
                        "pu": spec.purpose, "ba": spec.base_agent, "md": agent_md,
                        "ss": _json.dumps(spec.scoped_skills or []),
                        "st": _json.dumps(spec.scoped_tools or []),
                        "per": spec.persona, "ei": spec.extra_instructions,
                        "cba": created_by_agent, "cbu": created_by_user,
                    },
                )
                return {
                    "ok": True, "id": new_id, "was_new": True,
                    "usage_count": 0, "agent_md": agent_md,
                }
        except Exception as e:  # pragma: no cover
            logger.warning("AgentFactory.upsert_definition failed: %s", e)
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    @staticmethod
    def build_or_reuse(
        name: str, project_slug: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return spec for instantiation. Reuses saved definition if exists.

        Bumps ``usage_count`` and ``last_used_at``. Returns
        ``{ok, agent_id, spec, was_new}``.
        """
        eng = _get_engine()
        if eng is None:
            return {"ok": False, "reason": "db_unavailable"}
        try:
            from sqlalchemy import text
            with eng.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, name, purpose, base_agent, scoped_skills, "
                        "       scoped_tools, persona, extra_instructions, "
                        "       usage_count, enabled "
                        "FROM dash.dash_custom_agents "
                        "WHERE project_slug IS NOT DISTINCT FROM :ps AND name = :nm"
                    ),
                    {"ps": project_slug, "nm": name},
                ).first()
                if not row:
                    return {"ok": False, "reason": "not_found"}
                if not row[9]:
                    return {"ok": False, "reason": "disabled", "agent_id": row[0]}
                conn.execute(
                    text(
                        "UPDATE dash.dash_custom_agents "
                        "SET usage_count = COALESCE(usage_count,0) + 1, "
                        "    last_used_at = now() WHERE id = :id"
                    ),
                    {"id": row[0]},
                )
            spec = SubAgentSpec(
                name=row[1], purpose=row[2] or "", base_agent=row[3] or "Analyst",
                scoped_skills=list(row[4] or []), scoped_tools=list(row[5] or []),
                persona=row[6], extra_instructions=row[7] or "",
            )
            return {"ok": True, "agent_id": row[0], "spec": spec, "was_new": False}
        except Exception as e:
            return {"ok": False, "reason": "lookup_failed", "error": str(e)}

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_scoped_tools(names: List[str]) -> List[Any]:
        """Resolve a list of tool names to importable callables.

        Honors the spawner's allowed-tool list when set via ContextVar
        ``current_allowed_tools`` (best-effort import). Unknown / disallowed
        tool names are silently dropped.
        """
        # Pull spawner-allowed list if any
        allowed: Optional[set] = None
        try:
            from dash.agentic.hooks import current_allowed_tools  # type: ignore
            val = current_allowed_tools.get()
            if val:
                allowed = set(val)
        except Exception:
            allowed = None
        if allowed is None:
            allowed = set(_DEFAULT_SAFE_TOOLS)

        resolved: List[Any] = []
        for nm in (names or []):
            if nm not in _KNOWN_TOOL_LOADERS or nm not in allowed:
                continue
            mod_path, attr = _KNOWN_TOOL_LOADERS[nm].split(":")
            try:
                mod = __import__(mod_path, fromlist=[attr])
                fn = getattr(mod, attr, None)
                if fn is not None:
                    resolved.append(fn)
            except Exception as e:  # pragma: no cover
                logger.debug("scoped tool load failed %s: %s", nm, e)
        return resolved

    # ------------------------------------------------------------------
    @staticmethod
    def instantiate(spec: SubAgentSpec) -> Optional[Any]:
        """Build a live Agno Agent from a spec. Returns None on failure."""
        try:
            from agno.agent import Agent  # type: ignore
        except Exception as e:
            logger.warning("Agno Agent import failed: %s", e)
            return None
        try:
            from dash.settings import MODEL  # type: ignore
        except Exception:
            MODEL = None  # type: ignore

        tools = AgentFactory._resolve_scoped_tools(spec.scoped_tools)

        # Build instructions = base body + skills injection block
        skills_block = ""
        if spec.scoped_skills:
            skills_block = (
                "\n\n## ATTACHED SKILLS\n"
                "You may load and apply these skills as needed:\n"
                + "\n".join(f"- {s}" for s in spec.scoped_skills)
            )
        instructions = AgentFactory._generate_agent_md(spec) + skills_block

        try:
            return Agent(
                name=spec.name,
                role=spec.purpose or f"Sub-agent: {spec.name}",
                model=MODEL,
                instructions=instructions,
                tools=tools or None,
                markdown=True,
            )
        except Exception as e:
            logger.warning("AgentFactory.instantiate(%s) failed: %s", spec.name, e)
            return None


__all__ = ["SubAgentSpec", "AgentFactory"]
