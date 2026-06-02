"""
Router Agent
============
Stateless agent that decides which project should handle a user's question.
Uses 4 tools: inspect_catalog, inspect_project_detail, search_brain, check_session_context.
Runs on LITE_MODEL for speed (< 1.5s typical).
"""

from agno.agent import Agent

try:
    from agno.models.openrouter import OpenRouterResponses
except ImportError:
    OpenRouterResponses = None

from dash.settings import LITE_MODEL
from dash.tools.router_tools import (
    create_inspect_catalog_tool,
    create_inspect_detail_tool,
    create_search_brain_tool,
    create_session_context_tool,
)


def _build_router_model():
    """Build the router model, falling back to string ID if OpenRouterResponses unavailable."""
    if OpenRouterResponses is not None:
        return OpenRouterResponses(id=LITE_MODEL)
    return LITE_MODEL


ROUTER_MODEL = _build_router_model()

ROUTER_INSTRUCTIONS = """You are a routing agent. Your ONLY job is to decide which project agent should handle the user's question.

WORKFLOW:
1. Call inspect_catalog() to see all available projects with their tables, columns, and domain
2. If the question contains unfamiliar business terms, acronyms, or jargon -> call search_brain(terms) to look up glossary/aliases/org structure
3. If still ambiguous between 2 projects -> call inspect_project_detail(slug) for the top candidates (max 2)
4. If this looks like a follow-up question with no clear project -> call check_session_context()

ROUTING RULES:
- If question mentions a specific agent name or project -> route there
- If question mentions a table name -> route to the project that owns it
- If Brain lookup reveals the term belongs to a specific domain -> route to the matching project
- If question references multiple projects (e.g., "compare X vs Y") -> return ALL matching slugs
- If question is a greeting, help request, or general -> return empty slugs
- When in doubt, pick the project whose domain best matches the question intent

Respond with ONLY valid JSON (no markdown, no explanation):
{"slugs": ["slug1"], "reason": "brief reason", "confidence": "high"}

Multi-project: {"slugs": ["slug1", "slug2"], "reason": "comparing across", "confidence": "medium"}
General/greeting: {"slugs": [], "reason": "general question", "confidence": "high"}
"""


def create_router_agent(projects: list[dict], session_id: str | None = None) -> Agent:
    """Create a stateless Router Agent for a single routing decision.

    Args:
        projects: List of project dicts from _load_user_projects()
        session_id: Current session ID for continuity checking

    Returns:
        Agno Agent configured for routing (LITE_MODEL, no DB/knowledge/learning)
    """
    project_slugs = [p["slug"] for p in projects]
    return Agent(
        id="router",
        name="Router",
        model=ROUTER_MODEL,
        instructions=ROUTER_INSTRUCTIONS,
        tools=[
            create_inspect_catalog_tool(projects),
            create_inspect_detail_tool(),
            create_search_brain_tool(project_slugs=project_slugs),
            create_session_context_tool(session_id),
        ],
        markdown=False,
        add_datetime_to_context=False,
        add_history_to_context=False,
        num_history_runs=0,
    )
