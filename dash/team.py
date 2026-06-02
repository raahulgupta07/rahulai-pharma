"""
Dash Team
=========

A self-learning data agent that provides insights, not just query results.
Factory function creates per-user team instances.
"""

import asyncio
import functools
import logging
import re
import threading
import time

log = logging.getLogger(__name__)

from agno.knowledge import Knowledge
from agno.learn import LearningMachine
from agno.team import Team, TeamMode

from dash.agents.analyst import create_analyst
from dash.agents.customer_strategist import build_customer_strategist
from dash.agents.deal_analyst import build_deal_analyst_agent
from dash.agents.engineer import create_engineer
from dash.agents.market_sentinel import build_market_sentinel_agent
from dash.agents.ops_optimizer import build_ops_optimizer_agent
from dash.agents.researcher import create_researcher
from dash.agents.supply_sentry import build_supply_sentry_agent
from dash.instructions import build_leader_instructions
from dash.settings import MODEL, SLACK_TOKEN, agent_db, dash_learning


# _has_financial_tables + investment vertical auto-load DELETED 2026-05-23
# (dash/verticals/investment removed — orphan, 0 frontend refs)


def create_team(
    user_id: str | None = None,
    knowledge: Knowledge | None = None,
    learning: LearningMachine | None = None,
) -> Team:
    """Create a Dash team, optionally scoped to a user."""
    l = learning or dash_learning

    analyst = create_analyst(user_id=user_id, knowledge=knowledge, learning=l)
    engineer = create_engineer(user_id=user_id, knowledge=knowledge, learning=l)

    leader_tools: list = []
    if SLACK_TOKEN:
        from agno.tools.slack import SlackTools
        leader_tools.append(
            SlackTools(
                enable_send_message=True,
                enable_list_channels=True,
                enable_send_message_thread=True,
                enable_get_channel_info=True,
                enable_get_thread=True,
                enable_get_user_info=True,
                enable_search_messages=True,
            )
        )

    return Team(
        id="dash",
        name="Dash",
        mode=TeamMode.coordinate,
        model=MODEL,
        members=[analyst, engineer],
        db=agent_db,
        instructions=build_leader_instructions(user_id=user_id),
        tools=leader_tools,
        learning=l,
        add_learnings_to_context=True,
        share_member_interactions=True,
        enable_agentic_memory=True,
        search_past_sessions=True,
        num_past_sessions_to_search=5,
        read_chat_history=True,
        add_history_to_context=True,
        num_history_runs=5,
        add_datetime_to_context=True,
        markdown=True,
    )


_team_cache: dict[str, tuple] = {}  # slug -> (team, created_at)
_cache_lock = threading.Lock()


def invalidate_team_cache(project_slug: str | None = None):
    """Drop cached team(s). Call after feature_config / patches change.
    project_slug=None evicts every cached team."""
    with _cache_lock:
        if project_slug is None:
            _team_cache.clear()
            return
        for k in list(_team_cache.keys()):
            if k.startswith(f"{project_slug}_"):
                _team_cache.pop(k, None)
_TEAM_CACHE_TTL = 60  # 1 minute — faster refresh for instruction changes


def create_project_team(
    project_slug: str,
    agent_name: str = "Agent",
    agent_role: str = "",
    agent_personality: str = "friendly",
    user_id: int | None = None,
) -> Team:
    """Create a team scoped to a specific project."""
    cache_key = f"{project_slug}_{user_id}"
    now = time.time()
    with _cache_lock:
        # Evict expired entries to prevent memory leak
        expired = [k for k, (_, ts) in _team_cache.items() if now - ts > _TEAM_CACHE_TTL * 5]
        for k in expired:
            del _team_cache[k]
        if cache_key in _team_cache:
            cached_team, cached_at = _team_cache[cache_key]
            if now - cached_at < _TEAM_CACHE_TTL:
                return cached_team

    from db.session import create_project_knowledge, create_project_learnings
    from agno.learn import LearnedKnowledgeConfig, LearningMachine, LearningMode

    knowledge = create_project_knowledge(project_slug)
    learnings = create_project_learnings(project_slug)
    learning = LearningMachine(
        knowledge=learnings,
        learned_knowledge=LearnedKnowledgeConfig(mode=LearningMode.AGENTIC),
    )

    analyst = create_analyst(project_slug=project_slug, knowledge=knowledge, learning=learning, actual_user_id=user_id)
    engineer = create_engineer(project_slug=project_slug, knowledge=knowledge, learning=learning, dashboard_user_id=user_id)

    # Provider abstraction wiring (additive — legacy single-schema path
    # keeps working when no providers are registered).
    provider_tools: list = []
    try:
        from dash.providers import get_registry
        registry = get_registry()
        # Lazy load: if nothing registered for this slug yet, try to load.
        # create_project_team is sync, so run the async loader via asyncio.run
        # only when we're not already inside a running loop.
        if not registry.list_for_project(project_slug):
            try:
                asyncio.get_running_loop()
                # Already inside a loop — schedule load but don't block.
                asyncio.create_task(registry.load_for_project(project_slug))
            except RuntimeError:
                # No running loop — safe to run synchronously.
                try:
                    asyncio.run(registry.load_for_project(project_slug))
                except Exception as _e:
                    log.warning(f"Provider load_for_project failed for {project_slug}: {_e}")
        for p in (
            registry.list_for_project(project_slug, agent_scope="analyst_only")
            + registry.list_for_project(project_slug, agent_scope="shared")
        ):
            try:
                # Skip file-dialect providers — those go to Researcher
                if getattr(p, "dialect", "") == "files":
                    continue
                provider_tools.extend(p.emit_tools())
            except Exception as _e:
                log.warning(f"emit_tools failed for provider {getattr(p, 'id', '?')}: {_e}")
        if provider_tools:
            log.info(f"Loaded {len(provider_tools)} provider tools for project {project_slug}")
    except Exception as e:
        log.warning(f"Provider wiring failed for {project_slug}: {e}")

    # Federated query tool — JOIN across multiple sources within this project
    try:
        from dash.tools.federated_query import make_federated_tool
        fed_tool = make_federated_tool(
            project_slug=project_slug,
            agent_role="analyst",
            user_id=user_id,
        )
        if fed_tool is not None:
            provider_tools.append(fed_tool)
            log.info(f"federated_query tool attached for {project_slug}")
    except Exception as e:
        log.debug(f"federated_query not attached: {e}")

    if provider_tools:
        if hasattr(analyst, "tools") and isinstance(analyst.tools, list):
            analyst.tools.extend(provider_tools)
        else:
            log.warning(
                f"Cannot attach {len(provider_tools)} provider tools — analyst.tools missing or not a list"
            )

    # Build doc context for Researcher
    doc_instructions = ""
    from dash.paths import KNOWLEDGE_DIR
    docs_dir = KNOWLEDGE_DIR / project_slug / "docs"
    if docs_dir.exists():
        doc_texts = []
        doc_names = []
        for f in sorted(docs_dir.iterdir()):
            if f.is_file():
                doc_names.append(f.name)
                try:
                    content = f.read_text(errors='ignore')[:3000]
                    if content.strip():
                        doc_texts.append(f"### Document: {f.name}\n{content}")
                except Exception:
                    pass
        if doc_texts:
            doc_list = ", ".join(doc_names)
            doc_instructions = (
                f"\n\n## UPLOADED DOCUMENTS ({len(doc_names)} files: {doc_list})\n\n"
                f"If asked 'which documents do we have' — list these file names.\n\n"
                + "\n\n---\n\n".join(doc_texts[:5])
            )

    # Load grounded facts from LangExtract for Researcher
    try:
        import json as _json
        facts_file = KNOWLEDGE_DIR / project_slug / "training" / "grounded_facts.json"
        if facts_file.exists():
            with open(facts_file) as _ff:
                grounded_facts = _json.load(_ff)
            if grounded_facts:
                fact_lines = []
                for gf in grounded_facts[:20]:
                    tag = "✅" if gf.get("grounded", True) else "⚠️"
                    attrs = ""
                    if gf.get("attributes"):
                        attr_parts = [f"{k}: {v}" for k, v in gf["attributes"].items() if isinstance(v, str)]
                        attrs = f" ({', '.join(attr_parts[:2])})" if attr_parts else ""
                    fact_lines.append(f"- {tag} [{gf.get('type', 'fact').upper()}] {gf.get('text', '')}{attrs}")
                if fact_lines:
                    doc_instructions += (
                        f"\n\n## GROUNDED FACTS ({len(fact_lines)} source-verified extractions)\n"
                        f"These facts are verified against source documents. Prefer these over raw text.\n\n"
                        + "\n".join(fact_lines)
                    )
    except Exception:
        pass

    # Inject knowledge graph context for Researcher
    try:
        from dash.tools.knowledge_graph import get_knowledge_graph_context
        kg_context = get_knowledge_graph_context(project_slug, for_agent="researcher")
        if kg_context:
            doc_instructions += "\n\n" + kg_context
    except Exception:
        pass

    # Company Brain context for Researcher
    try:
        from app.brain import get_brain_context
        brain_ctx = get_brain_context(for_agent="researcher", project_slug=project_slug)
        if brain_ctx:
            doc_instructions += "\n\n" + brain_ctx
    except Exception:
        pass

    researcher = create_researcher(knowledge=knowledge, instructions=doc_instructions, project_slug=project_slug)

    # Researcher provider tools — file/document sources only
    researcher_provider_tools: list = []
    try:
        from dash.providers import get_registry
        reg = get_registry()
        file_providers = reg.list_for_project(project_slug, agent_scope="researcher_only")
        # Also pick up "shared" providers explicitly (filter currently includes them via "researcher_only" call)
        seen_ids = {p.id for p in file_providers}
        for p in reg.list_for_project(project_slug, agent_scope="shared"):
            if p.id not in seen_ids:
                file_providers.append(p)
        for p in file_providers:
            # Only file-dialect providers (skip SQL providers explicitly)
            if getattr(p, "dialect", "") == "files":
                try:
                    researcher_provider_tools.extend(p.emit_tools())
                except Exception as _e:
                    log.warning(f"emit_tools failed for researcher provider {getattr(p, 'id', '?')}: {_e}")
        log.info(f"Loaded {len(researcher_provider_tools)} researcher provider tools for {project_slug}")
    except Exception as e:
        log.warning(f"Researcher provider load failed for {project_slug}: {e}")

    if researcher_provider_tools and hasattr(researcher, "tools") and isinstance(researcher.tools, list):
        researcher.tools.extend(researcher_provider_tools)
    elif researcher_provider_tools:
        log.warning(f"Cannot attach {len(researcher_provider_tools)} researcher tools — no .tools list")

    # Web fetch tool — let Researcher fetch live URLs referenced in chat
    try:
        from dash.tools.web_fetch import make_tool as _make_web_fetch
        _wf = _make_web_fetch()
        if _wf is not None:
            if hasattr(researcher, "tools") and isinstance(researcher.tools, list):
                researcher.tools.append(_wf)
            else:
                researcher.tools = [_wf]
    except Exception as e:
        log.warning(f"web_fetch tool not loaded: {e}")

    # Customer Strategist — owns RFM/CLV/churn/recommendations + auto campaigns
    try:
        customer_strategist = build_customer_strategist(
            knowledge=knowledge, learning=learning, project_slug=project_slug,
            user_id=None, actual_user_id=user_id,
        )
    except Exception as _cs_err:
        log.warning(f"Customer Strategist build failed for {project_slug}: {_cs_err}")
        customer_strategist = None

    # Deal Analyst — VentureDesk pillar 2 (DCF / IRR / MOIC / sensitivity / partner fit)
    try:
        deal_analyst = build_deal_analyst_agent(
            project_slug=project_slug, user_id=user_id,
        )
    except Exception as _da_err:
        log.warning(f"Deal Analyst build failed for {project_slug}: {_da_err}")
        deal_analyst = None

    # Market Sentinel — external intel (competitor news, sector trends, sentiment)
    try:
        market_sentinel = build_market_sentinel_agent(
            project_slug=project_slug, user_id=user_id,
        )
    except Exception as _ms_err:
        log.warning(f"Market Sentinel build failed for {project_slug}: {_ms_err}")
        market_sentinel = None

    # Ops Optimizer — post-investment value-creation (KPI tracking, board reports)
    try:
        ops_optimizer = build_ops_optimizer_agent(
            project_slug=project_slug, user_id=user_id,
        )
    except Exception as _oo_err:
        log.warning(f"Ops Optimizer build failed for {project_slug}: {_oo_err}")
        ops_optimizer = None

    # Supply Sentry — supply-chain risk (single-source, lead time, geopolitical)
    try:
        supply_sentry = build_supply_sentry_agent(
            project_slug=project_slug, user_id=user_id,
        )
    except Exception as _ss_err:
        log.warning(f"Supply Sentry build failed for {project_slug}: {_ss_err}")
        supply_sentry = None

    # Per-project agent gating (feature_config.agents.*)
    from dash.feature_config import get_feature_config as _fc
    _agents_cfg = _fc(project_slug).get("agents", {})
    members = []
    if _agents_cfg.get("analyst", True):        members.append(analyst)
    if _agents_cfg.get("engineer", True):       members.append(engineer)
    if _agents_cfg.get("researcher", True):     members.append(researcher)
    if customer_strategist is not None and _agents_cfg.get("customer_strategist", True):
        members.append(customer_strategist)
    if deal_analyst is not None and _agents_cfg.get("deal_analyst", True):
        members.append(deal_analyst)
    if market_sentinel is not None and _agents_cfg.get("market_sentinel", True):
        members.append(market_sentinel)
    if ops_optimizer is not None and _agents_cfg.get("ops_optimizer", True):
        members.append(ops_optimizer)
    if supply_sentry is not None and _agents_cfg.get("supply_sentry", True):
        members.append(supply_sentry)

    # Hired sub-agents (Digital Workforce) — load enabled custom agents from
    # dash.dash_custom_agents into the live team so the Leader can delegate to
    # them. This is the wire that makes the Workforce console functional:
    # auto-spawned drafts → user clicks HIRE (enabled=true) → loaded here →
    # actually answers queries → usage_count (RUNS) increments via build_or_reuse
    # → probation progresses. Fail-soft + capped so a bad row never breaks chat.
    try:
        from dash.agents.factory import AgentFactory
        from sqlalchemy import text as _text
        from db.session import get_sql_engine as _gse
        _ceng = _gse()
        with _ceng.connect() as _cc:
            _crows = _cc.execute(_text(
                "SELECT name FROM dash.dash_custom_agents "
                "WHERE project_slug = :s AND enabled = true "
                "ORDER BY COALESCE(usage_count,0) DESC LIMIT 10"
            ), {"s": project_slug}).fetchall()
        _loaded = 0
        for (_cnm,) in _crows:
            try:
                _res = AgentFactory.build_or_reuse(_cnm, project_slug)  # bumps usage_count
                if _res.get("ok") and _res.get("spec"):
                    _cag = AgentFactory.instantiate(_res["spec"])
                    if _cag is not None:
                        members.append(_cag)
                        _loaded += 1
            except Exception as _ce:
                log.debug("custom agent load skipped %s: %s", _cnm, _ce)
        if _loaded:
            log.info("loaded %d hired sub-agent(s) into team for %s", _loaded, project_slug)
    except Exception as _cae:
        log.debug("custom-agents load skipped for %s: %s", project_slug, _cae)

    if not members:
        # Don't ship a team with zero members — fall back to researcher.
        members = [researcher]

    # Hard cap per-agent tool calls to prevent runaway loops on specialist
    # analysis tools (pareto_analysis, trend_analysis, etc). Agno's Agent
    # class exposes `tool_call_limit` — set it on every member.
    for _m in members:
        try:
            if hasattr(_m, "tool_call_limit"):
                _m.tool_call_limit = 15
        except Exception:
            pass

    # Sanitize tool schemas across all members BEFORE wiring into Team so the
    # first LLM request (often the tool-listing one) doesn't 400 on Gemini's
    # strict required[] validator. Pure pruning; safe on already-clean lists.
    try:
        from dash.tools.build import sanitize_tool_schemas
        for _m in members:
            _tools = getattr(_m, "tools", None)
            if isinstance(_tools, list):
                sanitize_tool_schemas(_tools)
    except Exception as _san_err:
        log.warning(f"team sanitize_tool_schemas failed: {_san_err}")

    team = Team(
        id="dash",
        name=agent_name,
        mode=TeamMode.coordinate,
        model=MODEL,
        members=members,
        db=agent_db,
        instructions=build_leader_instructions(user_id=project_slug, project_slug=project_slug),
        tools=[],
        learning=learning,
        add_learnings_to_context=True,
        share_member_interactions=True,
        enable_agentic_memory=True,
        add_datetime_to_context=True,
        markdown=True,
    )
    with _cache_lock:
        _team_cache[cache_key] = (team, time.time())
    return team


def _load_user_projects(user_id: int | None) -> list[dict]:
    """Load all projects for a user with their table names."""
    if not user_id:
        return []
    try:
        from sqlalchemy import text as sa_text
        from db import get_sql_engine
        engine = get_sql_engine()
        with engine.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT slug, name, agent_name, agent_role, agent_personality, schema_name "
                "FROM public.dash_projects WHERE user_id = :uid ORDER BY updated_at DESC"
            ), {"uid": user_id}).fetchall()

        projects = []
        for r in rows:
            table_names = []
            column_names = []
            try:
                from sqlalchemy import inspect as sa_inspect
                insp = sa_inspect(engine)
                schema = r[5] if r[5] else None
                table_names = insp.get_table_names(schema=schema) if schema else []
                # Load column names for better routing (first 5 tables, skip system cols)
                _skip = {"id", "created_at", "updated_at", "source_table", "source_file"}
                for tn in table_names[:5]:
                    try:
                        cols = insp.get_columns(tn, schema=schema)
                        column_names.extend(
                            c["name"] for c in cols
                            if c["name"].lower() not in _skip
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            # Load persona keywords for domain matching
            persona_keywords = []
            try:
                pr = conn.execute(sa_text(
                    "SELECT persona FROM public.dash_personas WHERE project_slug = :s ORDER BY created_at DESC LIMIT 1"
                ), {"s": r[0]}).fetchone()
                if pr and pr[0]:
                    # Extract meaningful words from persona (>4 chars, not common)
                    _common = {"this", "that", "with", "from", "have", "will", "your", "about", "their", "which",
                               "would", "there", "been", "some", "other", "than", "them", "each", "make", "like",
                               "into", "over", "such", "after", "also", "most", "should", "could", "these", "agent",
                               "data", "table", "query", "project", "based", "using", "provide", "analysis"}
                    words = set(w.lower().strip(".,;:!?()[]") for w in pr[0].split() if len(w) > 4)
                    persona_keywords = [w for w in words if w not in _common][:20]
            except Exception:
                pass
            projects.append({
                "slug": r[0], "name": r[1], "agent_name": r[2] or "Agent",
                "agent_role": r[3] or "", "agent_personality": r[4] or "friendly",
                "tables": table_names, "columns": column_names,
                "persona_keywords": persona_keywords,
            })
        return projects
    except Exception:
        return []


def create_dash_route_team(
    user_id: int | None = None,
    user_name: str = "",
) -> Team:
    """Create a route team that auto-dispatches to the right project agent.

    Uses TeamMode.route — Agno automatically picks the best member based on
    the user's question and each member's role description.
    """
    from agno.agent import Agent

    # Strip any prompt injection characters
    safe_name = re.sub(r'[^a-zA-Z0-9\s._-]', '', user_name or 'User')[:50]

    projects = _load_user_projects(user_id)

    members: list = []
    for p in projects:
        proj_team = create_project_team(
            project_slug=p["slug"],
            agent_name=p["agent_name"],
            agent_role=p["agent_role"],
            agent_personality=p["agent_personality"],
            user_id=user_id,
        )
        # Set clear role for routing — Agno uses this to pick the right member
        proj_team.role = (
            f"Data agent for project '{p['name']}'. "
            f"Specializes in: {p['agent_role'] or 'data analysis'}. "
            f"Tables: {', '.join(p['tables'][:10]) if p['tables'] else 'no data yet'}"
        )
        members.append(proj_team)

    # General agent handles greetings, help, and no-project scenarios
    project_list = ", ".join(p["agent_name"] for p in projects) if projects else "none yet"
    general = Agent(
        id="general",
        name="General Assistant",
        role="Handle greetings, introductions, help requests, general questions, and guide users to create projects",
        model=MODEL,
        instructions=(
            f"You are Dash, a self-learning data agent. You're warm, helpful, and sharp about data. "
            f"The user '{safe_name}' has these agents: {project_list}. "
            f"{'Guide them to ask data questions about their projects.' if projects else 'They have no projects yet. Guide them to create one at /ui/projects.'} "
            f"For greetings, be friendly. For 'what can you do', explain your capabilities: "
            f"data analysis, SQL queries, dashboards, self-learning, workflow automation."
        ),
        markdown=True,
    )
    members.append(general)

    return Team(
        id="dash-router",
        name="Dash",
        mode=TeamMode.route,
        model=MODEL,
        members=members,
        db=agent_db,
        add_datetime_to_context=True,
        markdown=True,
    )


# Default singleton for backward compatibility (AgentOS registration)
dash = create_team()


if __name__ == "__main__":
    test_cases = [
        "Hey, what can you do?",
        "What's our current MRR?",
    ]
    for idx, prompt in enumerate(test_cases, start=1):
        print(f"\n--- Dash test case {idx}/{len(test_cases)} ---")
        print(f"Prompt: {prompt}")
        dash.print_response(prompt, stream=True)
