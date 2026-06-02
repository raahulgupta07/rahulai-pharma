"""
Create Dashboard Tool
=====================

Agent tool that creates dashboards with multiple widgets programmatically.
Returns [DASHBOARD:id] tag for frontend panel activation.
"""

import json
import uuid

from agno.tools import tool
from agno.utils.log import logger
from sqlalchemy import text

from db import get_sql_engine


def create_dashboard_tool(project_slug: str, user_id: int = 1):
    """Create a dashboard tool scoped to a project.

    Args:
        project_slug: Project identifier
        user_id: Owner user ID for dashboard visibility
    """

    @tool
    def create_dashboard(
        name: str,
        widgets: str,
        description: str = "",
    ) -> str:
        """Create a dashboard with multiple widgets for the user.

        Use this when the user asks you to build a dashboard, report, or visual summary.
        First query the data you need using SQL, then call this tool with the results.

        Args:
            name: Dashboard title (e.g. "Revenue Dashboard", "Customer Overview")
            widgets: JSON array of widgets. Each widget object has:
                - type: "metric" | "chart" | "text" | "table"
                - title: Widget title (e.g. "Total Revenue", "Top 10 Customers")
                - content: For metric = the number as string (e.g. "599"), for text = markdown content
                - chartType: For chart only: "bar" | "line" | "pie" | "scatter" | "area"
                - headers: For chart/table: list of column names (e.g. ["Film", "Revenue"])
                - rows: For chart/table: list of data rows (e.g. [["Film A", "250"], ["Film B", "180"]])
                - full: Set true for full-width widgets (optional, default false)
            description: Optional description of the dashboard
        """
        if not name or not name.strip():
            return "Error: Dashboard name is required."

        # Type check before parsing
        if not isinstance(widgets, str):
            return "Error: widgets must be a JSON string, not a Python object. Wrap your widget array in json.dumps() or pass it as a string."

        # Parse widgets JSON
        try:
            widget_list = json.loads(widgets)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON syntax in widgets. Check for missing brackets or quotes. Details: {e}"

        if not isinstance(widget_list, list):
            return "Error: widgets must be a JSON array of widget objects."

        if len(widget_list) == 0:
            return "Error: At least one widget is required."

        # Build widget objects with IDs
        built_widgets = []
        skipped = 0
        for w in widget_list:
            if not isinstance(w, dict):
                skipped += 1
                continue
            widget = {
                "id": f"w_{uuid.uuid4().hex[:8]}",
                "type": w.get("type", "text"),
                "title": w.get("title", "Widget"),
                "content": w.get("content", ""),
                "chartType": w.get("chartType", "bar"),
                "headers": w.get("headers", []),
                "rows": w.get("rows", []),
                "full": w.get("full", False),
            }
            built_widgets.append(widget)

        if len(built_widgets) == 0:
            return "Error: No valid widgets found in the array. Each widget must be a JSON object with at least a 'type' and 'title'."

        # Insert dashboard into DB using shared engine
        try:
            engine = get_sql_engine()
            with engine.connect() as conn:
                result = conn.execute(text(
                    "INSERT INTO public.dash_dashboards (project_slug, user_id, name, widgets) "
                    "VALUES (:slug, :uid, :name, CAST(:widgets AS jsonb)) RETURNING id"
                ), {
                    "slug": project_slug,
                    "uid": user_id,
                    "name": name.strip(),
                    "widgets": json.dumps(built_widgets),
                })
                dash_id = result.fetchone()[0]
                conn.commit()

            msg = f"[DASHBOARD:{dash_id}] Created dashboard '{name}' with {len(built_widgets)} widgets."
            if skipped > 0:
                msg += f" ({skipped} invalid items were skipped.)"
            logger.info(f"Created dashboard '{name}' (id={dash_id}) with {len(built_widgets)} widgets for project {project_slug}")
            return msg

        except Exception as e:
            logger.error(f"Failed to create dashboard: {e}")
            return f"Error creating dashboard: {e}"

    return create_dashboard
