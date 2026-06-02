"""
Project knowledge graph — nodes (artifacts) + edges (dash.dash_links).

Nodes are unioned from optional source tables (tables_meta, metrics, charts,
chat_messages). Each source is wrapped in try/except so missing relations
return an empty list rather than failing the whole request.

Edges come from dash.dash_links filtered by project_slug.

Orphan nodes (zero incoming edges) are flagged `orphan: true`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception as e:
        raise HTTPException(503, f"sql engine unavailable: {e}")


def _safe_query(eng, sql: str, params: dict) -> list[dict]:
    """Run a query, returning [] on any error (missing relation, etc)."""
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"graph source skipped: {e}")
        return []


def _collect_nodes(slug: str) -> list[dict[str, Any]]:
    """Union nodes from optional source tables. Each wrapped in try/except."""
    eng = _engine()
    nodes: list[dict[str, Any]] = []

    # Tables
    for row in _safe_query(
        eng,
        "SELECT table_name AS id FROM dash.dash_tables_meta WHERE project_slug = :p",
        {"p": slug},
    ):
        nodes.append({"id": f"table:{row['id']}", "label": str(row["id"]), "type": "table"})

    # Metrics
    for row in _safe_query(
        eng,
        "SELECT name AS id FROM dash.dash_metrics WHERE project_slug = :p",
        {"p": slug},
    ):
        nodes.append({"id": f"metric:{row['id']}", "label": str(row["id"]), "type": "metric"})

    # Charts
    for row in _safe_query(
        eng,
        "SELECT id, COALESCE(title, id::text) AS label FROM dash.dash_charts WHERE project_slug = :p",
        {"p": slug},
    ):
        nodes.append({"id": f"chart:{row['id']}", "label": str(row["label"]), "type": "chart"})

    # Chat messages (last 30d)
    for row in _safe_query(
        eng,
        """
        SELECT id, COALESCE(LEFT(content, 60), id::text) AS label
        FROM dash.dash_chat_messages
        WHERE project_slug = :p AND created_at > now() - interval '30 days'
        """,
        {"p": slug},
    ):
        nodes.append({"id": f"chat:{row['id']}", "label": str(row["label"]), "type": "chat"})

    # Dedupe by id (last write wins)
    seen: dict[str, dict] = {}
    for n in nodes:
        seen[n["id"]] = n
    return list(seen.values())


def _collect_edges(slug: str) -> list[dict[str, Any]]:
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT src_type, src_id, dst_type, dst_id, rel
                    FROM dash.dash_links
                    WHERE project_slug = :p
                    """
                ),
                {"p": slug},
            ).mappings().all()
        return [
            {
                "source": f"{r['src_type']}:{r['src_id']}",
                "target": f"{r['dst_type']}:{r['dst_id']}",
                "rel": r["rel"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"dash.dash_links read failed for {slug}: {e}")
        return []


@router.get("/{slug}")
def get_graph(slug: str) -> dict[str, Any]:
    """Return {nodes, edges} for a project. Orphan nodes get `orphan: true`."""
    if not slug:
        raise HTTPException(400, "slug required")

    nodes = _collect_nodes(slug)
    edges = _collect_edges(slug)

    # Inject edge endpoints as nodes if missing (so edges from links aren't dangling)
    node_ids = {n["id"] for n in nodes}
    for e in edges:
        for endpoint, kind_key in ((e["source"], "src"), (e["target"], "dst")):
            if endpoint not in node_ids:
                # parse type from "type:id"
                parts = endpoint.split(":", 1)
                ntype = parts[0] if len(parts) == 2 else "unknown"
                nlabel = parts[1] if len(parts) == 2 else endpoint
                nodes.append({"id": endpoint, "label": nlabel, "type": ntype})
                node_ids.add(endpoint)

    # Orphan detection: zero incoming edges
    incoming: dict[str, int] = {}
    for e in edges:
        incoming[e["target"]] = incoming.get(e["target"], 0) + 1
    for n in nodes:
        if incoming.get(n["id"], 0) == 0:
            n["orphan"] = True

    return {"project_slug": slug, "nodes": nodes, "edges": edges}


@router.get("/{slug}/orphans")
def get_orphans(slug: str) -> dict[str, Any]:
    """Tables present in dash_tables_meta but never appearing in dash_links (any side)."""
    if not slug:
        raise HTTPException(400, "slug required")

    eng = _engine()

    tables = _safe_query(
        eng,
        "SELECT table_name AS name FROM dash.dash_tables_meta WHERE project_slug = :p",
        {"p": slug},
    )
    table_names = [t["name"] for t in tables]

    if not table_names:
        return {"project_slug": slug, "orphans": [], "count": 0}

    try:
        with eng.connect() as conn:
            linked = conn.execute(
                text(
                    """
                    SELECT DISTINCT src_id AS name FROM dash.dash_links
                    WHERE project_slug = :p AND src_type = 'table'
                    UNION
                    SELECT DISTINCT dst_id AS name FROM dash.dash_links
                    WHERE project_slug = :p AND dst_type = 'table'
                    """
                ),
                {"p": slug},
            ).scalars().all()
        linked_set = set(linked)
    except Exception as e:
        logger.warning(f"orphan lookup links read failed for {slug}: {e}")
        linked_set = set()

    orphans = [name for name in table_names if name not in linked_set]
    return {"project_slug": slug, "orphans": orphans, "count": len(orphans)}
