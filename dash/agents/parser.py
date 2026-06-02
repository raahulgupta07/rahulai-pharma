"""
Parser Agent (Data Extraction Specialist)
==========================================

Handles Excel, CSV, JSON files. Detects clean vs messy data,
finds headers, unpivots months, merges related sheets, self-corrects.
"""

from agno.agent import Agent
from agno.knowledge import Knowledge
from agno.tools.reasoning import ReasoningTools

from dash.settings import MODEL, agent_db


def create_parser(project_slug: str, knowledge: Knowledge | None = None, engine=None, schema: str = "") -> Agent:
    """Create a Parser agent for data file processing."""
    import re
    schema = schema or re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:63]

    from dash.tools.upload_tools import build_parser_tools
    tools = build_parser_tools(project_slug, engine=engine, schema=schema)
    tools.append(ReasoningTools())

    return Agent(
        id="parser",
        name="Parser",
        role="Data Extraction Specialist — reads Excel, CSV, JSON. Detects structure, unpivots time-series, merges related sheets, handles merged cells.",
        model=MODEL,
        db=agent_db,
        instructions="""You are the Parser — a data extraction specialist.

YOUR JOB: Take any data file and extract clean, queryable tables.

WORKFLOW:
1. SCAN the file first (read_excel_sheets for Excel, etc.)
2. ANALYZE the structure:
   - Clean data (proper headers, no merges)? → parse directly
   - Messy data (merged cells, metadata rows, months as columns)? → use AI parsing
3. EXTRACT tables following the structure
4. CHECK if multiple tables share the same columns → MERGE them with a distinguishing column
5. REPORT what you created

SELF-CORRECTION:
- If columns are "unnamed_0, unnamed_1" → wrong header row, retry
- If table has 0 rows after parsing → try different header/skip rows
- If merge fails → keep tables separate

MERGE RULES:
- Multiple sheets with SAME columns + different FY/year → merge + add fiscal_year column
- Multiple blocks in one sheet with SAME columns + different category → merge + add category column
- Summary sheet + detail sheets → keep BOTH (don't merge summary into detail)
- DIFFERENT column structures → keep separate

ALWAYS use real column names from the Excel header row. Never invent names.""",
        tools=tools,
        add_datetime_to_context=True,
        markdown=True,
    )
