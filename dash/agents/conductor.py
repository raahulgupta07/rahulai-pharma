"""
Conductor Agent (Upload Orchestrator)
=======================================

The Upload Leader. Sees all files, creates the plan,
assigns Parser/Scanner/Vision agents, validates via Inspector,
calls Engineer for post-upload optimization. Handles retries.
"""

from agno.agent import Agent
from agno.team import Team, TeamMode
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db


def create_upload_team(project_slug: str, user_id: int = 1) -> Team:
    """Create the Upload Agent Team for a project.

    Team structure:
        Conductor (leader) → Parser + Scanner + Vision + Inspector
        + Engineer (existing, for post-upload optimization)
    """
    import re
    schema = re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]

    # Get project engine for Parser + Inspector
    try:
        from db import get_project_engine
        engine = get_project_engine(project_slug)
    except Exception:
        engine = None

    # Create sub-agents
    from dash.agents.parser import create_parser
    from dash.agents.scanner import create_scanner
    from dash.agents.vision_agent import create_vision_agent
    from dash.agents.inspector import create_inspector
    from dash.agents.engineer import create_engineer

    try:
        from db.session import create_project_knowledge, create_project_learnings
        from agno.learn import LearnedKnowledgeConfig, LearningMachine, LearningMode
        knowledge = create_project_knowledge(project_slug)
        learnings = create_project_learnings(project_slug)
        learning = LearningMachine(knowledge=learnings, learned_knowledge=LearnedKnowledgeConfig(mode=LearningMode.AGENTIC))
    except Exception:
        knowledge = None
        learning = None

    parser = create_parser(project_slug, knowledge=knowledge, engine=engine, schema=schema)
    scanner = create_scanner(project_slug, knowledge=knowledge)
    vision = create_vision_agent()
    inspector = create_inspector(project_slug, engine=engine, schema=schema)
    engineer = create_engineer(project_slug=project_slug, knowledge=knowledge, learning=learning, dashboard_user_id=user_id)

    # Create the Conductor team
    team = Team(
        id="upload_team",
        name="Conductor",
        mode=TeamMode.coordinate,
        model=MODEL,
        members=[parser, scanner, vision, inspector, engineer],
        db=agent_db,
        instructions="""You are the Conductor — the Upload Orchestrator.

YOUR JOB: Process uploaded files by assigning the right agent to each file.

TEAM:
- **Parser**: handles Excel (.xlsx/.xls), CSV (.csv), JSON (.json) — data files with tables
- **Scanner**: handles PDF (.pdf), PPTX (.pptx), DOCX (.docx), TXT (.txt), MD (.md), SQL (.sql) — documents
- **Vision**: handles JPG (.jpg/.jpeg), PNG (.png) — images
- **Inspector**: validates EVERY table after creation — checks quality, health score
- **Engineer**: creates views, discovers relationships between tables — runs LAST

WORKFLOW:
1. LOOK at the file(s) and their type(s)
2. ASSIGN the right agent:
   - Data files → Parser
   - Documents → Scanner
   - Images → Vision
3. AFTER tables are created → Inspector validates each table
4. IF Inspector says FAIL → ask Parser/Scanner to retry with different strategy
5. AFTER all validated → Engineer creates views + discovers relationships
6. REPORT summary: tables created, health scores, views, relationships

MERGE DECISION (for Excel with multiple sheets/tables):
After Parser creates tables, check if any should be MERGED:
- Same columns across sheets (different FY/year) → tell Parser to merge
- Same columns across files (different location) → tell Parser to merge
- Different columns → keep separate

SELF-CORRECTION:
If Inspector reports a table with health < 40%:
- Ask Parser to retry with: different header row, different split, read raw + AI
- Max 2 retries per table
- If still fails after retries → mark as "needs manual review"

ALWAYS report what happened clearly.""",
        tools=[ReasoningTools()],
        share_member_interactions=True,
        add_datetime_to_context=True,
        markdown=True,
    )

    return team
