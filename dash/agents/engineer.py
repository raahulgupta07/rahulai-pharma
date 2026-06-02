"""
Engineer Agent
==============

Owns the data model: schema migrations, views, pipelines.
Factory function creates per-user instances.
"""

from agno.agent import Agent
from agno.knowledge import Knowledge
from agno.learn import LearningMachine

from dash.instructions import build_engineer_instructions
from dash.settings import MODEL, agent_db, dash_knowledge, dash_learning
from dash.tools.build import build_engineer_tools


def create_engineer(
    user_id: str | None = None,
    knowledge: Knowledge | None = None,
    learning: LearningMachine | None = None,
    project_slug: str | None = None,
    dashboard_user_id: int | None = None,
) -> Agent:
    """Create an Engineer agent, optionally scoped to a user or project."""
    k = knowledge or dash_knowledge
    l = learning or dash_learning

    return Agent(
        id="engineer",
        name="Engineer",
        role="Schema migrations, views, pipelines, data loading, knowledge management",
        model=MODEL,
        db=agent_db,
        instructions=build_engineer_instructions(user_id=user_id or project_slug, project_slug=project_slug),
        knowledge=k,
        search_knowledge=True,
        learning=l,
        add_learnings_to_context=True,
        tools=build_engineer_tools(k, user_id=user_id, project_slug=project_slug, dashboard_user_id=dashboard_user_id),
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
    )


# Default singleton for backward compatibility
engineer = create_engineer()
