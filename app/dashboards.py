"""
Dashboards API
==============

CRUD for project dashboards. Each dashboard has a list of widgets (charts, tables, text)
stored as JSONB in the dash_dashboards table.
"""

import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

router = APIRouter(prefix="/api/projects", tags=["Dashboards"])
_engine = _sa_create_engine(db_url, poolclass=NullPool)


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str):
    from app.auth import check_project_permission
    if not check_project_permission(user, slug):
        raise HTTPException(403, "Access denied")


@router.get("/{slug}/dashboards")
def list_dashboards(slug: str, request: Request):
    """List all dashboards for a project."""
    user = _get_user(request)
    with _engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, widgets, created_at, updated_at FROM public.dash_dashboards "
            "WHERE project_slug = :slug AND user_id = :uid ORDER BY updated_at DESC"
        ), {"slug": slug, "uid": user["user_id"]}).fetchall()

    dashboards = []
    for r in rows:
        widgets = r[2] if isinstance(r[2], list) else json.loads(r[2]) if r[2] else []
        dashboards.append({
            "id": r[0], "name": r[1], "widget_count": len(widgets),
            "created_at": str(r[3]) if r[3] else None,
            "updated_at": str(r[4]) if r[4] else None,
        })
    return {"dashboards": dashboards}


@router.post("/{slug}/dashboards")
def create_dashboard(slug: str, request: Request, name: str = "Dashboard"):
    """Create a new dashboard."""
    user = _get_user(request)
    with _engine.connect() as conn:
        result = conn.execute(text(
            "INSERT INTO public.dash_dashboards (project_slug, user_id, name) "
            "VALUES (:slug, :uid, :name) RETURNING id"
        ), {"slug": slug, "uid": user["user_id"], "name": name})
        dash_id = result.fetchone()[0]
        conn.commit()
    return {"status": "ok", "id": dash_id}


@router.get("/{slug}/dashboards/{dashboard_id}")
def get_dashboard(slug: str, dashboard_id: int, request: Request):
    """Get a dashboard with all widgets."""
    user = _get_user(request)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, name, widgets, created_at, updated_at FROM public.dash_dashboards "
            "WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": dashboard_id, "slug": slug, "uid": user["user_id"]}).fetchone()

    if not row:
        raise HTTPException(404, "Dashboard not found")

    widgets = row[2] if isinstance(row[2], list) else json.loads(row[2]) if row[2] else []
    return {
        "id": row[0], "name": row[1], "widgets": widgets,
        "created_at": str(row[3]) if row[3] else None,
        "updated_at": str(row[4]) if row[4] else None,
    }


@router.put("/{slug}/dashboards/{dashboard_id}")
def update_dashboard(slug: str, dashboard_id: int, request: Request, name: str = ""):
    """Update dashboard name."""
    user = _get_user(request)
    with _engine.connect() as conn:
        if name:
            conn.execute(text(
                "UPDATE public.dash_dashboards SET name = :name, updated_at = NOW() "
                "WHERE id = :id AND project_slug = :slug AND user_id = :uid"
            ), {"name": name, "id": dashboard_id, "slug": slug, "uid": user["user_id"]})
            conn.commit()
    return {"status": "ok"}


@router.delete("/{slug}/dashboards/{dashboard_id}")
def delete_dashboard(slug: str, dashboard_id: int, request: Request):
    """Delete a dashboard."""
    user = _get_user(request)
    with _engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM public.dash_dashboards WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": dashboard_id, "slug": slug, "uid": user["user_id"]})
        conn.commit()
    return {"status": "ok"}


@router.post("/{slug}/dashboards/{dashboard_id}/widgets")
async def add_widget(slug: str, dashboard_id: int, request: Request):
    """Add a widget to a dashboard. Body: {type, title, content, chartType, headers, rows}"""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()

    widget = {
        "id": f"w_{uuid.uuid4().hex[:8]}",
        "type": body.get("type", "text"),
        "title": body.get("title", "Widget"),
        "content": body.get("content", ""),
        "chartType": body.get("chartType", "bar"),
        "headers": body.get("headers", []),
        "rows": body.get("rows", []),
        "full": body.get("full", False),
    }

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT widgets FROM public.dash_dashboards "
            "WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": dashboard_id, "slug": slug, "uid": user["user_id"]}).fetchone()

        if not row:
            raise HTTPException(404, "Dashboard not found")

        widgets = row[0] if isinstance(row[0], list) else json.loads(row[0]) if row[0] else []
        widgets.append(widget)

        conn.execute(text(
            "UPDATE public.dash_dashboards SET widgets = CAST(:w AS jsonb), updated_at = NOW() "
            "WHERE id = :id"
        ), {"w": json.dumps(widgets), "id": dashboard_id})
        conn.commit()

    return {"status": "ok", "widget": widget}


@router.delete("/{slug}/dashboards/{dashboard_id}/widgets/{widget_id}")
def remove_widget(slug: str, dashboard_id: int, widget_id: str, request: Request):
    """Remove a widget from a dashboard."""
    user = _get_user(request)
    _check_access(user, slug)
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT widgets FROM public.dash_dashboards "
            "WHERE id = :id AND project_slug = :slug AND user_id = :uid"
        ), {"id": dashboard_id, "slug": slug, "uid": user["user_id"]}).fetchone()

        if not row:
            raise HTTPException(404, "Dashboard not found")

        widgets = row[0] if isinstance(row[0], list) else json.loads(row[0]) if row[0] else []
        widgets = [w for w in widgets if w.get("id") != widget_id]

        conn.execute(text(
            "UPDATE public.dash_dashboards SET widgets = CAST(:w AS jsonb), updated_at = NOW() "
            "WHERE id = :id"
        ), {"w": json.dumps(widgets), "id": dashboard_id})
        conn.commit()

    return {"status": "ok"}


@router.post("/{slug}/generate-dashboard-from-chat")
async def generate_dashboard_from_chat(slug: str, request: Request):
    """Generate a dashboard from chat conversation content."""
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()

    chat_content = body.get("chat_content", "")
    sql_queries = body.get("sql_queries", [])
    session_id = body.get("session_id", "")
    message_count = body.get("message_count", 0)

    if not chat_content:
        raise HTTPException(400, "No chat content provided")

    # Step 1: LLM extracts ALL data from chat (like slides-agent think step)
    from dash.settings import training_llm_call

    think_prompt = f"""You are a McKinsey data analyst. Read this entire conversation carefully.
Extract EVERY piece of data mentioned:

1. ALL NUMBERS: revenue, counts, percentages, growth rates, totals
2. ALL COMPARISONS: X vs Y, before/after, this period vs last
3. ALL TABLES: any tabular data with rows and columns
4. ALL TRENDS: increasing, decreasing, stable patterns
5. ALL BREAKDOWNS: by category, region, time period, product
6. ALL INSIGHTS: anomalies, risks, opportunities, recommendations

FULL CONVERSATION:
{chat_content[:10000]}

Extract every single data point. Format as structured lists. Miss nothing."""

    thinking = training_llm_call(think_prompt, "deep_analysis")

    # Step 2: Generate rich dashboard from extracted data
    prompt = f"""You are building an EXECUTIVE DASHBOARD. Use the extracted data below.

EXTRACTED DATA:
{(thinking or '')[:4000]}

Create exactly 6-8 widgets. You MUST include:
1. THREE metric cards — the 3 most important numbers (with % change if available)
2. ONE bar chart — comparing categories (use real numbers from the data)
3. ONE line/trend chart OR pie chart — showing distribution or trends
4. ONE data table — the most important comparison table (min 3 rows)
5. ONE insights widget — 3-4 bullet point key findings
6. ONE action items widget — what should be done next

CRITICAL RULES:
- Use ONLY real numbers from the conversation. Never invent data.
- Every chart MUST have numeric data in rows (not text)
- Metric content should be just the number (e.g. "629" not "629 successful calls")
- Chart rows format: [["Label", number], ["Label", number]]

Return ONLY valid JSON:
{{
  "name": "dashboard title (max 5 words)",
  "widgets": [
    {{"type": "metric", "title": "Total Calls", "content": "824"}},
    {{"type": "metric", "title": "Success Rate", "content": "37.6%"}},
    {{"type": "metric", "title": "Growth", "content": "+12%"}},
    {{"type": "chart", "title": "Calls by Channel", "chartType": "bar", "headers": ["Channel", "Count"], "rows": [["Digital", 629], ["Trade", 195]]}},
    {{"type": "chart", "title": "Trend", "chartType": "line", "headers": ["Month", "Value"], "rows": [["Jan", 100], ["Feb", 120], ["Mar", 150]]}},
    {{"type": "table", "title": "Comparison", "headers": ["Metric", "Digital", "Trade"], "rows": [["Calls", "1900", "520"], ["Success", "629", "195"]]}},
    {{"type": "text", "title": "Key Insights", "content": "• Finding 1\\n• Finding 2\\n• Finding 3"}},
    {{"type": "text", "title": "Actions", "content": "• Action 1\\n• Action 2\\n• Action 3"}}
  ]
}}"""

    result = training_llm_call(prompt, "dashboard")
    if not result:
        raise HTTPException(500, "LLM call failed — check OpenRouter API key and credits")

    # Robust JSON parsing
    import re
    cleaned = result.strip()
    # Strip markdown code fences
    cleaned = re.sub(r'^```\w*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```$', '', cleaned)
    cleaned = cleaned.strip()
    # Try to find JSON object
    json_match = re.search(r'\{[\s\S]*\}', cleaned)
    if json_match:
        cleaned = json_match.group(0)
    try:
        dashboard_spec = json.loads(cleaned)
    except Exception:
        # Fallback: create a simple dashboard manually
        dashboard_spec = {
            "name": f"Chat Dashboard",
            "widgets": [
                {"type": "text", "title": "Chat Summary", "content": f"Dashboard generated from {message_count} chat messages with {len(sql_queries)} SQL queries."},
                {"type": "text", "title": "Key Content", "content": chat_content[:500]},
            ]
        }

    # Step 2: Create dashboard in DB (widgets stored as JSONB array in dash_dashboards)
    dashboard_name = dashboard_spec.get("name", f"Chat Dashboard - {session_id[:8]}")
    raw_widgets = dashboard_spec.get("widgets", [])

    # Normalize widgets to match the existing widget format
    widgets = []
    for i, w in enumerate(raw_widgets):
        widget = {
            "id": f"w_{uuid.uuid4().hex[:8]}",
            "type": w.get("type", "text"),
            "title": w.get("title", f"Widget {i+1}"),
            "content": w.get("content", ""),
            "chartType": w.get("chartType", "bar"),
            "headers": w.get("headers", []),
            "rows": w.get("rows", []),
            "full": w.get("full", False),
        }
        widgets.append(widget)

    with _engine.connect() as conn:
        row = conn.execute(text(
            "INSERT INTO public.dash_dashboards (project_slug, user_id, name, widgets) "
            "VALUES (:slug, :uid, :name, CAST(:w AS jsonb)) RETURNING id"
        ), {
            "slug": slug,
            "uid": user["user_id"],
            "name": dashboard_name,
            "w": json.dumps(widgets),
        })
        dashboard_id = row.fetchone()[0]
        conn.commit()

    # Continuous training signal — chart action 'rendered'. message_id is
    # passed by frontend when available; fire-and-forget, never breaks response.
    # NOTE: no edit/reject endpoints exist yet, only 'rendered' is wired here.
    try:
        _msg_id = body.get("message_id") if isinstance(body, dict) else None
        if _msg_id:
            from dash.learning.continuous import mark_chart_action as _mark_chart
            _mark_chart(_msg_id, "rendered")
    except Exception:
        pass

    return {"status": "ok", "dashboard_id": dashboard_id, "name": dashboard_name, "widgets": len(widgets)}
