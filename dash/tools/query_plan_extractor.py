"""
Query Plan Extractor
====================

After each chat, extracts JOIN strategies and filter patterns from SQL in the response.
Builds a memory of what query approaches work for which table combinations.
Runs as a background task — does not block the chat response.
"""

import re

from sqlalchemy import text

from db import get_sql_engine


def extract_query_plan(project_slug: str, question: str, answer: str, source_id: int | None = None):
    """Parse SQL from agent response and extract query plan details.

    ``source_id`` (Phase 3) tags the plan with the active provider source.
    NULL = legacy/project-wide.
    """

    # Extract SQL blocks from the response
    sql_blocks = re.findall(r'```sql\s*(.*?)```', answer, re.DOTALL | re.IGNORECASE)
    if not sql_blocks:
        # Try inline SQL patterns
        sql_blocks = re.findall(r'(?:SELECT|WITH)\s+.+?(?:;|$)', answer, re.DOTALL | re.IGNORECASE)

    if not sql_blocks:
        return

    for sql in sql_blocks[:2]:  # Process max 2 SQL blocks per response
        sql_clean = sql.strip()
        if len(sql_clean) < 20:
            continue

        sql_upper = sql_clean.upper()

        # Extract table names from FROM and JOIN clauses
        tables = set()
        # FROM table patterns
        from_matches = re.findall(r'FROM\s+(?:public\.|dash\.)?(\w+)', sql_clean, re.IGNORECASE)
        tables.update(from_matches)
        # JOIN table patterns
        join_matches = re.findall(r'JOIN\s+(?:public\.|dash\.)?(\w+)', sql_clean, re.IGNORECASE)
        tables.update(join_matches)

        if not tables:
            continue

        # Extract join strategy (ON clauses)
        join_conditions = re.findall(r'ON\s+(.+?)(?:WHERE|GROUP|ORDER|LIMIT|LEFT|RIGHT|INNER|OUTER|JOIN|$)', sql_clean, re.IGNORECASE | re.DOTALL)
        join_strategy = "; ".join(j.strip() for j in join_conditions)[:500] if join_conditions else None

        # Extract filter patterns (WHERE clause)
        where_match = re.search(r'WHERE\s+(.+?)(?:GROUP|ORDER|LIMIT|HAVING|$)', sql_clean, re.IGNORECASE | re.DOTALL)
        filters_used = where_match.group(1).strip()[:500] if where_match else None

        # Determine success: response likely contains data if there are tables or numbers
        has_data = bool(re.search(r'\|.*\|.*\|', answer)) or bool(re.search(r'\d+', answer))
        has_error = 'error' in answer.lower()[:200] or 'no data' in answer.lower()[:200]
        success = has_data and not has_error

        # Insert query plan
        try:
            engine = get_sql_engine()
            with engine.connect() as conn:
                conn.execute(text(
                    "INSERT INTO public.dash_query_plans "
                    "(project_slug, tables_involved, join_strategy, filters_used, success, question, sql_used, source_id) "
                    "VALUES (:slug, :tables, :join, :filters, :success, :question, :sql, :source_id)"
                ), {
                    "slug": project_slug,
                    "tables": list(tables),
                    "join": join_strategy,
                    "filters": filters_used,
                    "success": success,
                    "question": question[:500],
                    "sql": sql_clean[:2000],
                    "source_id": source_id,
                })
                conn.commit()
        except Exception:
            pass
