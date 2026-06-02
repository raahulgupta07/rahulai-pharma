"""Governance backend — super-admin CRUD over policies, approvals, data zones,
PII rules, retention, audit hooks, compliance map.

Mirrors app/admin_connectors.py pattern: APIRouter w/ in-module super-admin gate,
SQLAlchemy via db.session.get_sql_engine, fail-soft dict responses.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/governance", tags=["governance"])


# ---------------------------------------------------------------------------
# Auth (mirror admin_connectors.py)
# ---------------------------------------------------------------------------
def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super_admin") and not user.get("is_super"):
        raise HTTPException(403, "super-admin only")


def _gate(request: Request) -> dict:
    u = _get_user(request)
    _require_super(u)
    return u


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _eng():
    from db.session import get_sql_engine
    return get_sql_engine()


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    eng = _eng()
    with eng.connect() as c:
        result = c.execute(text(sql), params or {})
        return [_row_to_dict(r) for r in result.fetchall()]


def _one(sql: str, params: dict | None = None) -> dict | None:
    rs = _rows(sql, params)
    return rs[0] if rs else None


def _exec(sql: str, params: dict | None = None) -> Any:
    eng = _eng()
    with eng.begin() as c:
        return c.execute(text(sql), params or {})


# ===========================================================================
# POLICIES
# ===========================================================================
@router.get("/policies")
def list_policies(request: Request):
    _gate(request)
    return {"policies": _rows(
        "SELECT id, name, type, scope, status, yaml_body, hits_24h, "
        "created_at, updated_at FROM dash.gov_policies ORDER BY id DESC"
    )}


@router.post("/policies")
async def create_policy(request: Request):
    _gate(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    ptype = body.get("type") or "guardrail"
    if not name:
        raise HTTPException(400, "name required")
    if ptype not in ("redact", "guardrail", "gate", "block"):
        raise HTTPException(400, "invalid type")
    status = body.get("status") or "DRAFT"
    if status not in ("ACTIVE", "DRAFT", "DISABLED"):
        raise HTTPException(400, "invalid status")
    try:
        _exec(
            "INSERT INTO dash.gov_policies (name, type, scope, status, yaml_body) "
            "VALUES (:n, :t, :sc, :st, :y)",
            {"n": name, "t": ptype, "sc": body.get("scope") or "global",
             "st": status, "y": body.get("yaml_body") or ""},
        )
    except Exception as e:
        raise HTTPException(400, f"insert failed: {e}")
    return {"ok": True, "policy": _one(
        "SELECT * FROM dash.gov_policies WHERE name=:n", {"n": name})}


@router.patch("/policies/{pid}")
async def update_policy(pid: int, request: Request):
    _gate(request)
    body = await request.json()
    fields = []
    params: dict = {"id": pid}
    for k in ("name", "type", "scope", "status", "yaml_body"):
        if k in body:
            fields.append(f"{k} = :{k}")
            params[k] = body[k]
    if "hits_24h" in body:
        fields.append("hits_24h = :hits_24h")
        params["hits_24h"] = int(body["hits_24h"])
    if not fields:
        raise HTTPException(400, "no fields to update")
    fields.append("updated_at = now()")
    _exec(f"UPDATE dash.gov_policies SET {', '.join(fields)} WHERE id = :id", params)
    return {"ok": True, "policy": _one(
        "SELECT * FROM dash.gov_policies WHERE id=:id", {"id": pid})}


@router.delete("/policies/{pid}")
def delete_policy(pid: int, request: Request):
    _gate(request)
    _exec("DELETE FROM dash.gov_policies WHERE id = :id", {"id": pid})
    return {"ok": True}


# ===========================================================================
# APPROVALS
# ===========================================================================
@router.get("/approvals")
def list_approvals(request: Request, status: str | None = None):
    _gate(request)
    sql = ("SELECT id, req_id, user_id, resource, cost_estimate, requested_at, "
           "status, decided_at, decided_by FROM dash.gov_approvals")
    params: dict = {}
    if status:
        sql += " WHERE status = :s"
        params["s"] = status.upper()
    sql += " ORDER BY requested_at DESC LIMIT 500"
    return {"approvals": _rows(sql, params)}


@router.post("/approvals/{req_id}/decide")
async def decide_approval(req_id: str, request: Request):
    user = _gate(request)
    body = await request.json()
    decision = (body.get("decision") or "").lower()
    if decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be 'approve' or 'deny'")
    new_status = "APPROVED" if decision == "approve" else "DENIED"
    res = _exec(
        "UPDATE dash.gov_approvals SET status=:st, decided_at=now(), decided_by=:by "
        "WHERE req_id=:r AND status='PENDING'",
        {"st": new_status, "by": user.get("username") or user.get("email") or "admin",
         "r": req_id},
    )
    if getattr(res, "rowcount", 0) == 0:
        raise HTTPException(404, "approval not found or already decided")
    return {"ok": True, "approval": _one(
        "SELECT * FROM dash.gov_approvals WHERE req_id=:r", {"r": req_id})}


# ===========================================================================
# DATA ZONES
# ===========================================================================
@router.get("/data-zones")
def list_data_zones(request: Request):
    _gate(request)
    return {"zones": _rows(
        "SELECT id, zone_name, region, datasets_count, classification, egress "
        "FROM dash.gov_data_zones ORDER BY zone_name"
    )}


@router.post("/data-zones")
async def create_data_zone(request: Request):
    _gate(request)
    body = await request.json()
    name = (body.get("zone_name") or "").strip()
    if not name:
        raise HTTPException(400, "zone_name required")
    egress = body.get("egress") or "blocked"
    if egress not in ("blocked", "vpn-only", "open"):
        raise HTTPException(400, "invalid egress")
    try:
        _exec(
            "INSERT INTO dash.gov_data_zones (zone_name, region, datasets_count, "
            "classification, egress) VALUES (:n, :r, :d, :c, :e)",
            {"n": name, "r": body.get("region"), "d": int(body.get("datasets_count", 0)),
             "c": body.get("classification"), "e": egress},
        )
    except Exception as e:
        raise HTTPException(400, f"insert failed: {e}")
    return {"ok": True, "zone": _one(
        "SELECT * FROM dash.gov_data_zones WHERE zone_name=:n", {"n": name})}


@router.delete("/data-zones/{zid}")
def delete_data_zone(zid: int, request: Request):
    _gate(request)
    _exec("DELETE FROM dash.gov_data_zones WHERE id = :id", {"id": zid})
    return {"ok": True}


# ===========================================================================
# PII RULES
# ===========================================================================
@router.get("/pii-rules")
def list_pii_rules(request: Request):
    _gate(request)
    return {"rules": _rows(
        "SELECT id, pattern_name, regex, action, matches_24h, owner "
        "FROM dash.gov_pii_rules ORDER BY pattern_name"
    )}


@router.post("/pii-rules")
async def create_pii_rule(request: Request):
    _gate(request)
    body = await request.json()
    pn = (body.get("pattern_name") or "").strip()
    rx = body.get("regex") or ""
    if not pn or not rx:
        raise HTTPException(400, "pattern_name and regex required")
    action = body.get("action") or "mask"
    if action not in ("mask", "tokenize", "allow-log", "block"):
        raise HTTPException(400, "invalid action")
    try:
        _exec(
            "INSERT INTO dash.gov_pii_rules (pattern_name, regex, action, owner) "
            "VALUES (:n, :r, :a, :o)",
            {"n": pn, "r": rx, "a": action, "o": body.get("owner")},
        )
    except Exception as e:
        raise HTTPException(400, f"insert failed: {e}")
    return {"ok": True, "rule": _one(
        "SELECT * FROM dash.gov_pii_rules WHERE pattern_name=:n", {"n": pn})}


@router.delete("/pii-rules/{rid}")
def delete_pii_rule(rid: int, request: Request):
    _gate(request)
    _exec("DELETE FROM dash.gov_pii_rules WHERE id = :id", {"id": rid})
    return {"ok": True}


# ===========================================================================
# RETENTION
# ===========================================================================
@router.get("/retention")
def list_retention(request: Request):
    _gate(request)
    return {"retention": _rows(
        "SELECT id, object_name, ttl_days, soft_delete_days, hard_delete_days, "
        "next_purge_at, est_rows FROM dash.gov_retention ORDER BY object_name"
    )}


@router.patch("/retention/{rid}")
async def update_retention(rid: int, request: Request):
    _gate(request)
    body = await request.json()
    fields = []
    params: dict = {"id": rid}
    for k in ("ttl_days", "soft_delete_days", "hard_delete_days", "est_rows"):
        if k in body:
            fields.append(f"{k} = :{k}")
            params[k] = int(body[k])
    if "object_name" in body:
        fields.append("object_name = :object_name")
        params["object_name"] = body["object_name"]
    if "next_purge_at" in body and body["next_purge_at"]:
        fields.append("next_purge_at = :next_purge_at")
        params["next_purge_at"] = body["next_purge_at"]
    if not fields:
        raise HTTPException(400, "no fields to update")
    _exec(f"UPDATE dash.gov_retention SET {', '.join(fields)} WHERE id = :id", params)
    return {"ok": True, "retention": _one(
        "SELECT * FROM dash.gov_retention WHERE id=:id", {"id": rid})}


# ===========================================================================
# AUDIT HOOKS
# ===========================================================================
@router.get("/audit-hooks")
def list_audit_hooks(request: Request):
    _gate(request)
    return {"hooks": _rows(
        "SELECT id, hook_name, sink_url, events_per_min, status "
        "FROM dash.gov_audit_hooks ORDER BY hook_name"
    )}


@router.post("/audit-hooks")
async def create_audit_hook(request: Request):
    _gate(request)
    body = await request.json()
    hn = (body.get("hook_name") or "").strip()
    url = (body.get("sink_url") or "").strip()
    if not hn or not url:
        raise HTTPException(400, "hook_name and sink_url required")
    status = body.get("status") or "active"
    if status not in ("active", "failing", "disabled"):
        raise HTTPException(400, "invalid status")
    try:
        _exec(
            "INSERT INTO dash.gov_audit_hooks (hook_name, sink_url, events_per_min, status) "
            "VALUES (:n, :u, :e, :s)",
            {"n": hn, "u": url, "e": int(body.get("events_per_min", 0)), "s": status},
        )
    except Exception as e:
        raise HTTPException(400, f"insert failed: {e}")
    return {"ok": True, "hook": _one(
        "SELECT * FROM dash.gov_audit_hooks WHERE hook_name=:n", {"n": hn})}


@router.delete("/audit-hooks/{hid}")
def delete_audit_hook(hid: int, request: Request):
    _gate(request)
    _exec("DELETE FROM dash.gov_audit_hooks WHERE id = :id", {"id": hid})
    return {"ok": True}


# ===========================================================================
# COMPLIANCE MAP
# ===========================================================================
@router.get("/compliance-map")
def list_compliance(request: Request):
    _gate(request)
    return {"controls": _rows(
        "SELECT id, control_id, framework, mapped_to, coverage_pct, last_review_at "
        "FROM dash.gov_compliance_map ORDER BY framework, control_id"
    )}


@router.patch("/compliance-map/{cid}")
async def update_compliance(cid: int, request: Request):
    _gate(request)
    body = await request.json()
    fields = []
    params: dict = {"id": cid}
    for k in ("control_id", "framework", "mapped_to"):
        if k in body:
            fields.append(f"{k} = :{k}")
            params[k] = body[k]
    if "coverage_pct" in body:
        pct = int(body["coverage_pct"])
        if pct < 0 or pct > 100:
            raise HTTPException(400, "coverage_pct must be 0..100")
        fields.append("coverage_pct = :coverage_pct")
        params["coverage_pct"] = pct
    if "last_review_at" in body and body["last_review_at"]:
        fields.append("last_review_at = :last_review_at")
        params["last_review_at"] = body["last_review_at"]
    if not fields:
        raise HTTPException(400, "no fields to update")
    _exec(f"UPDATE dash.gov_compliance_map SET {', '.join(fields)} WHERE id = :id", params)
    return {"ok": True, "control": _one(
        "SELECT * FROM dash.gov_compliance_map WHERE id=:id", {"id": cid})}


# ===========================================================================
# SUMMARY
# ===========================================================================
@router.get("/summary")
def summary(request: Request):
    _gate(request)
    eng = _eng()
    out: dict = {}
    try:
        with eng.connect() as c:
            out["policies_active"] = c.execute(text(
                "SELECT COUNT(*) FROM dash.gov_policies WHERE status='ACTIVE'"
            )).scalar() or 0
            out["approvals_pending"] = c.execute(text(
                "SELECT COUNT(*) FROM dash.gov_approvals WHERE status='PENDING'"
            )).scalar() or 0
            out["zones"] = c.execute(text(
                "SELECT COUNT(*) FROM dash.gov_data_zones"
            )).scalar() or 0
            out["pii_rules"] = c.execute(text(
                "SELECT COUNT(*) FROM dash.gov_pii_rules"
            )).scalar() or 0
            out["hooks_failing"] = c.execute(text(
                "SELECT COUNT(*) FROM dash.gov_audit_hooks WHERE status='failing'"
            )).scalar() or 0
            row = c.execute(text(
                "SELECT MIN(next_purge_at) AS lp FROM dash.gov_retention"
            )).fetchone()
            lp = row[0] if row else None
            out["last_purge"] = lp.isoformat() if isinstance(lp, datetime) else None
    except Exception as e:
        logger.exception("governance summary failed")
        raise HTTPException(500, f"summary failed: {e}")
    return out
