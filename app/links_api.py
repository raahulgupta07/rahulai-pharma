"""
Dash Links API — Obsidian-style bidirectional artifact links.

Endpoints (router prefix `/api/links`):
    POST   /api/links          — upsert link (ON CONFLICT DO NOTHING)
    GET    /api/links          — list links by src or dst filter
    GET    /api/links/summary  — aggregate counts grouped by other side's type+rel
    DELETE /api/links          — delete single row by composite key

Writes via get_write_engine(); reads via get_sql_engine().
PgBouncer rule: CAST(:x AS jsonb), never :x::jsonb (no JSONB here, noted).
Style mirrors app/golden_api.py.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/links", tags=["links"])


# ---------- helpers ----------

def _write_eng():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception as e:
        raise HTTPException(503, f"write engine unavailable: {e}")


def _read_eng():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception as e:
        raise HTTPException(503, f"read engine unavailable: {e}")


def _norm(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


# ---------- endpoints ----------

@router.post("")
def create_link(body: dict[str, Any]) -> dict[str, Any]:
    """Upsert a bidirectional link. Body:
        {src_type, src_id, dst_type, dst_id, rel, project_slug?}

    Idempotent via ON CONFLICT DO NOTHING on composite PK.
    """
    src_type = _norm(body.get("src_type"))
    src_id   = _norm(body.get("src_id"))
    dst_type = _norm(body.get("dst_type"))
    dst_id   = _norm(body.get("dst_id"))
    rel      = _norm(body.get("rel"))
    project_slug = _norm(body.get("project_slug")) or None

    if not (src_type and src_id and dst_type and dst_id and rel):
        raise HTTPException(400, "src_type, src_id, dst_type, dst_id, rel required")

    eng = _write_eng()
    try:
        with eng.begin() as c:
            c.execute(
                _t("""
                    INSERT INTO dash.dash_links
                        (src_type, src_id, dst_type, dst_id, rel, project_slug)
                    VALUES
                        (:st, :si, :dt, :di, :rel, :ps)
                    ON CONFLICT (src_type, src_id, dst_type, dst_id, rel)
                    DO NOTHING
                """),
                {"st": src_type, "si": src_id, "dt": dst_type, "di": dst_id,
                 "rel": rel, "ps": project_slug},
            )
    except Exception as e:
        logger.exception("create_link failed")
        raise HTTPException(500, f"create_link failed: {e}")

    return {"ok": True}


@router.get("")
def list_links(
    src_type: str | None = Query(None),
    src_id:   str | None = Query(None),
    dst_type: str | None = Query(None),
    dst_id:   str | None = Query(None),
    project_slug: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """List links. Provide any combination of filters."""
    where: list[str] = []
    params: dict[str, Any] = {"lim": limit}
    if src_type:
        where.append("src_type = :st"); params["st"] = src_type
    if src_id:
        where.append("src_id = :si"); params["si"] = src_id
    if dst_type:
        where.append("dst_type = :dt"); params["dt"] = dst_type
    if dst_id:
        where.append("dst_id = :di"); params["di"] = dst_id
    if project_slug:
        where.append("project_slug = :ps"); params["ps"] = project_slug
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    eng = _read_eng()
    try:
        with eng.connect() as c:
            rows = c.execute(
                _t(f"""
                    SELECT src_type, src_id, dst_type, dst_id, rel, project_slug, created_at
                      FROM dash.dash_links
                      {where_sql}
                     ORDER BY created_at DESC
                     LIMIT :lim
                """),
                params,
            ).mappings().all()
    except Exception as e:
        logger.exception("list_links failed")
        raise HTTPException(503, f"list_links failed: {e}")

    return {"count": len(rows), "links": [dict(r) for r in rows]}


@router.get("/summary")
def link_summary(
    type: str = Query(..., min_length=1, description="Artifact type to summarize"),
    id:   str = Query(..., min_length=1, description="Artifact id to summarize"),
) -> dict[str, Any]:
    """Aggregate counts of links touching (type, id), grouped by other side's type+rel.

    Counts incoming (dst=this) and outgoing (src=this) sides separately.
    UI uses this for sidebar chips like "3 charts · 12 chats".
    """
    eng = _read_eng()
    try:
        with eng.connect() as c:
            # incoming: this is dst, count by src_type+rel
            incoming = c.execute(
                _t("""
                    SELECT src_type AS other_type, rel, COUNT(*) AS n
                      FROM dash.dash_links
                     WHERE dst_type = :t AND dst_id = :i
                  GROUP BY src_type, rel
                  ORDER BY n DESC
                """),
                {"t": type, "i": id},
            ).mappings().all()
            # outgoing: this is src, count by dst_type+rel
            outgoing = c.execute(
                _t("""
                    SELECT dst_type AS other_type, rel, COUNT(*) AS n
                      FROM dash.dash_links
                     WHERE src_type = :t AND src_id = :i
                  GROUP BY dst_type, rel
                  ORDER BY n DESC
                """),
                {"t": type, "i": id},
            ).mappings().all()
    except Exception as e:
        logger.exception("link_summary failed")
        raise HTTPException(503, f"link_summary failed: {e}")

    # combined per-type totals for chip row
    totals: dict[str, int] = {}
    for r in incoming:
        totals[r["other_type"]] = totals.get(r["other_type"], 0) + int(r["n"])
    for r in outgoing:
        totals[r["other_type"]] = totals.get(r["other_type"], 0) + int(r["n"])

    return {
        "type": type,
        "id": id,
        "totals": [{"type": k, "count": v} for k, v in sorted(totals.items(), key=lambda x: -x[1])],
        "incoming": [dict(r) for r in incoming],
        "outgoing": [dict(r) for r in outgoing],
    }


@router.delete("")
def delete_link(body: dict[str, Any]) -> dict[str, Any]:
    """Delete a single row by composite key.
    Body: {src_type, src_id, dst_type, dst_id, rel}
    """
    src_type = _norm(body.get("src_type"))
    src_id   = _norm(body.get("src_id"))
    dst_type = _norm(body.get("dst_type"))
    dst_id   = _norm(body.get("dst_id"))
    rel      = _norm(body.get("rel"))
    if not (src_type and src_id and dst_type and dst_id and rel):
        raise HTTPException(400, "src_type, src_id, dst_type, dst_id, rel required")

    eng = _write_eng()
    try:
        with eng.begin() as c:
            res = c.execute(
                _t("""
                    DELETE FROM dash.dash_links
                     WHERE src_type = :st AND src_id = :si
                       AND dst_type = :dt AND dst_id = :di
                       AND rel = :rel
                """),
                {"st": src_type, "si": src_id, "dt": dst_type, "di": dst_id, "rel": rel},
            )
            deleted = res.rowcount or 0
    except Exception as e:
        logger.exception("delete_link failed")
        raise HTTPException(500, f"delete_link failed: {e}")

    return {"ok": True, "deleted": int(deleted)}
