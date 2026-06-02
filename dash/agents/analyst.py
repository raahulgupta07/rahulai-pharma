"""
Analyst Agent
=============

SQL generation, execution, schema introspection, and data quality handling.
Factory function creates per-user instances.
"""

from agno.agent import Agent
from agno.knowledge import Knowledge
from agno.learn import LearningMachine

from dash.instructions import build_analyst_instructions
from dash.settings import MODEL, agent_db, dash_knowledge, dash_learning
from dash.tools.build import build_analyst_tools


def create_analyst(
    user_id: str | None = None,
    knowledge: Knowledge | None = None,
    learning: LearningMachine | None = None,
    project_slug: str | None = None,
    actual_user_id: int | None = None,
) -> Agent:
    """Create an Analyst agent, optionally scoped to a user or project."""
    k = knowledge or dash_knowledge
    l = learning or dash_learning

    return Agent(
        id="analyst",
        name="Analyst",
        role=(
            "SQL data analyst with 11 specialist analysis tools: comparator (period comparison), "
            "diagnostic (root cause), narrator (executive summary), validator (data quality), "
            "planner (what-if scenarios), trend, pareto, benchmark, root_cause, prescriptive. "
            "No ML or prediction tools. Delegate ML tasks to Data Scientist."
        ),
        model=MODEL,
        db=agent_db,
        instructions=build_analyst_instructions(user_id=user_id or project_slug, project_slug=project_slug, actual_user_id=actual_user_id),
        knowledge=k,
        search_knowledge=True,
        learning=l,
        add_learnings_to_context=True,
        tools=build_analyst_tools(k, user_id=user_id, project_slug=project_slug),
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
    )


# Default singleton for backward compatibility (used by AgentOS registration)
analyst = create_analyst()
