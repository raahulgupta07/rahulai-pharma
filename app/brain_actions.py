"""
Brain sharing actions
=====================

Promote / Pull / Resolve between the AGENT side and the COMPANY side of the
single Brain.

POST /api/brain/promote   {category, name, agent_id}     agent value → company
POST /api/brain/pull      {category, name, company_id}   company value → agent
POST /api/brain/resolve   {category, name, agent_id, company_id, winner}
                                                         winner copies to loser

Company-side writes go through dash_company_brain and are version-audited via
brain_versions.snapshot_version (called inside the same open transaction).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brain", tags=["BrainActions"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)

LOCKED_SLUG = "citypharma"

# category (unified) -> dash_company_brain.category for the company side
_COMPANY_CAT = {
    "definitions": "formula",
    "glossary": "glossary",
    "patterns": "pattern",
    "rules": "threshold",
}

# category -> (agent table, value column, id column) for the agent side
_AGENT_TABLE = {
    "definitions": ("dash_metric_definitions", "description", "id"),
    "patterns": ("dash_query_patterns", "sql", "id"),
    "rules": ("dash_rules_db", "definition", "id"),
}


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class PromoteBody(BaseModel):
    category: str
    name: str
    agent_id: int
    project_slug: str = LOCKED_SLUG


class PullBody(BaseModel):
    category: str
    name: str
    company_id: int
    project_slug: str = LOCKED_SLUG


class ResolveBody(BaseModel):
    category: str
    name: str
    agent_id: int | None = None
    company_id: int | None = None
    winner: str  # 'agent' | 'company'
    project_slug: str = LOCKED_SLUG


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _read_agent_value(conn, category: str, agent_id: int) -> str | None:
    spec = _AGENT_TABLE.get(category)
    if not spec:
        raise HTTPException(400, f"category '{category}' has no writable agent side")
    table, valcol, idcol = spec
    row = conn.execute(
        text(f"SELECT {valcol} AS v FROM {table} WHERE {idcol} = :id"),
        {"id": agent_id},
    ).mappings().first()
    return row["v"] if row else None


def _read_company_value(conn, company_id: int) -> str | None:
    row = conn.execute(
        text("SELECT definition FROM dash_company_brain WHERE id = :id"),
        {"id": company_id},
    ).mappings().first()
    return row["definition"] if row else None


def _write_agent_value(conn, category: str, agent_id: int, value: str) -> None:
    table, valcol, idcol = _AGENT_TABLE[category]
    conn.execute(
        text(f"UPDATE {table} SET {valcol} = :v WHERE {idcol} = :id"),
        {"v": value, "id": agent_id},
    )


def _snapshot(conn, brain_id: int, change_type: str, user_id, reason: str) -> None:
    try:
        from app.brain_versions import snapshot_version
        snapshot_version(conn, brain_id, change_type, user_id, reason)
    except Exception as e:  # noqa: BLE001 — audit must never block the action
        logger.warning("brain_actions snapshot failed (%s): %s", change_type, e)


def _upsert_company(conn, category: str, name: str, value: str, slug: str, user_id) -> int:
    company_cat = _COMPANY_CAT.get(category)
    if not company_cat:
        raise HTTPException(400, f"category '{category}' cannot map to a company entry")
    existing = conn.execute(
        text(
            """
            SELECT id FROM dash_company_brain
            WHERE category = :c AND lower(trim(name)) = lower(trim(:n))
              AND (project_slug = :slug OR project_slug IS NULL)
            LIMIT 1
            """
        ),
        {"c": company_cat, "n": name, "slug": slug},
    ).mappings().first()

    if existing:
        bid = existing["id"]
        conn.execute(
            text(
                "UPDATE dash_company_brain SET definition = :v, updated_at = now() "
                "WHERE id = :id"
            ),
            {"v": value, "id": bid},
        )
        _snapshot(conn, bid, "update", user_id, f"promote agent→company ({category})")
        return bid

    bid = conn.execute(
        text(
            """
            INSERT INTO dash_company_brain
              (category, name, definition, metadata, project_slug, created_by,
               created_at, updated_at)
            VALUES (:c, :n, :v, '{}'::jsonb, :slug, :uid, now(), now())
            RETURNING id
            """
        ),
        {"c": company_cat, "n": name, "v": value, "slug": slug, "uid": user_id},
    ).scalar_one()
    _snapshot(conn, bid, "create", user_id, f"promote agent→company ({category})")
    return bid


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/promote")
def promote(body: PromoteBody, request: Request):
    user = _get_user(request)
    uid = user.get("user_id") or user.get("id")
    with _engine.begin() as conn:
        value = _read_agent_value(conn, body.category, body.agent_id)
        if value is None:
            raise HTTPException(404, "agent entry not found / empty")
        bid = _upsert_company(conn, body.category, body.name, value, body.project_slug, uid)
    return {"ok": True, "action": "promote", "company_id": bid, "status": "synced"}


@router.post("/pull")
def pull(body: PullBody, request: Request):
    _get_user(request)
    if body.category not in _AGENT_TABLE:
        raise HTTPException(400, f"category '{body.category}' has no writable agent side")
    with _engine.begin() as conn:
        value = _read_company_value(conn, body.company_id)
        if value is None:
            raise HTTPException(404, "company entry not found / empty")
        # find the matching agent row by name (single-agent locked project)
        table, valcol, idcol = _AGENT_TABLE[body.category]
        namecol = "question" if body.category == "patterns" else "name"
        row = conn.execute(
            text(
                f"SELECT {idcol} AS id FROM {table} "
                f"WHERE project_slug = :slug AND lower(trim({namecol})) = lower(trim(:n)) "
                f"LIMIT 1"
            ),
            {"slug": body.project_slug, "n": body.name},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "no matching agent entry to pull into")
        _write_agent_value(conn, body.category, row["id"], value)
    return {"ok": True, "action": "pull", "agent_id": row["id"], "status": "synced"}


@router.post("/resolve")
def resolve(body: ResolveBody, request: Request):
    user = _get_user(request)
    uid = user.get("user_id") or user.get("id")
    if body.winner not in ("agent", "company"):
        raise HTTPException(400, "winner must be 'agent' or 'company'")
    with _engine.begin() as conn:
        if body.winner == "agent":
            if body.agent_id is None:
                raise HTTPException(400, "agent_id required when winner=agent")
            value = _read_agent_value(conn, body.category, body.agent_id)
            if value is None:
                raise HTTPException(404, "agent entry not found / empty")
            bid = _upsert_company(conn, body.category, body.name, value, body.project_slug, uid)
            return {"ok": True, "action": "resolve", "winner": "agent",
                    "company_id": bid, "status": "synced"}
        # winner == company
        if body.company_id is None:
            raise HTTPException(400, "company_id required when winner=company")
        value = _read_company_value(conn, body.company_id)
        if value is None:
            raise HTTPException(404, "company entry not found / empty")
        if body.category not in _AGENT_TABLE:
            raise HTTPException(400, f"category '{body.category}' has no writable agent side")
        table, valcol, idcol = _AGENT_TABLE[body.category]
        namecol = "question" if body.category == "patterns" else "name"
        row = conn.execute(
            text(
                f"SELECT {idcol} AS id FROM {table} "
                f"WHERE project_slug = :slug AND lower(trim({namecol})) = lower(trim(:n)) "
                f"LIMIT 1"
            ),
            {"slug": body.project_slug, "n": body.name},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "no matching agent entry to resolve into")
        _write_agent_value(conn, body.category, row["id"], value)
        return {"ok": True, "action": "resolve", "winner": "company",
                "agent_id": row["id"], "status": "synced"}
