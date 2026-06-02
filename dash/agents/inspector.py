"""
Inspector Agent (Data Quality Inspector)
==========================================

Validates every table after creation. Profiles columns,
checks duplicates, scores health, flags issues, triggers retry.
"""

from agno.agent import Agent
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db


def create_inspector(project_slug: str, engine=None, schema: str = "") -> Agent:
    """Create an Inspector agent for data quality validation."""
    import re
    schema = schema or re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]

    from dash.tools.upload_tools import build_inspector_tools
    tools = build_inspector_tools(project_slug, engine=engine, schema=schema)
    tools.append(ReasoningTools())

    return Agent(
        id="inspector",
        name="Inspector",
        role="Data Quality Inspector — profiles every table, checks duplicates, scores health, generates alerts, flags bad data for retry.",
        model=MODEL,
        db=agent_db,
        instructions="""You are the Inspector — a data quality expert.

YOUR JOB: Validate EVERY table created during upload.

WORKFLOW:
1. For each table, run validate_table to get full quality report
2. CHECK the result:
   - PASS (health >= 70%): table is good, report stats
   - NEEDS_REVIEW (40-69%): flag issues, suggest fixes
   - FAIL (< 40%): data is bad — recommend retry with different parsing

3. REPORT for each table:
   - Health score (0-100%)
   - Key alerts (high nulls, duplicates, type issues)
   - Whether it PASSES or needs retry

QUALITY CHECKS:
- Columns named "unnamed_0", "unnamed_1" → FAIL (wrong header row)
- >80% null values in most columns → FAIL (wrong data range)
- Only 1-2 rows when sheet had 17 → FAIL (parsing missed data)
- All columns are text when numbers expected → FAIL (type mismatch)
- Duplicate rows → WARN (not failure, but note it)

IF A TABLE FAILS:
- Explain WHAT's wrong (unnamed columns? too many nulls? too few rows?)
- Suggest WHAT to fix (different header row? different skip rows? different split?)
- The Conductor will ask Parser to retry with your suggestions""",
        tools=tools,
        add_datetime_to_context=True,
        markdown=True,
    )
