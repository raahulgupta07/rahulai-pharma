"""Campaign Management Lite API.

CRUD for marketing campaigns + segment targeting + status lifecycle +
metric recording + ROI rollup. No actual email/SMS sending — this layer
tracks intent, audience, schedule, and outcomes.

Endpoints:
- POST   /api/projects/{slug}/campaigns
- GET    /api/projects/{slug}/campaigns
- GET    /api/projects/{slug}/campaigns/summary
- GET    /api/projects/{slug}/campaigns/{id}
- PATCH  /api/projects/{slug}/campaigns/{id}
- DELETE /api/projects/{slug}/campaigns/{id}
- POST   /api/projects/{slug}/campaigns/{id}/launch
- POST   /api/projects/{slug}/campaigns/{id}/pause
- POST   /api/projects/{slug}/campaigns/{id}/resume
- POST   /api/projects/{slug}/campaigns/{id}/complete
- POST   /api/projects/{slug}/campaigns/{id}/metric
- GET    /api/projects/{slug}/campaigns/{id}/audience
- GET    /api/projects/{slug}/campaigns/{id}/audience.csv  (FULL audience, streaming)
- GET    /api/projects/{slug}/campaigns/{id}/roi
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as _t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Campaigns"])


# ── Allowed values ───────────────────────────────────────────────────

ALLOWED_TYPES = {"manual", "email", "sms", "push", "web", "auto"}
ALLOWED_STATUSES = {"draft", "scheduled", "active", "paused", "completed", "cancelled"}

# RFM segment names produced by dash.tools.customer_intelligence.rfm_score
RFM_SEGMENTS = {
    "Champions",
    "Loyal Customers",
    "Potential Loyalists",
    "New Customers",
    "Promising",
    "Need Attention",
    "About To Sleep",
    "At Risk",
    "Cannot Lose Them",
    "Hibernating",
    "Lost",
}

UPDATABLE_FIELDS = {
    "name",
    "description",
    "type",
    "status",
    "target_segment",
    "target_filter",
    "offer",
    "starts_at",
    "ends_at",
    "cost_budget",
    "audience_size",
}


# ── Auth helpers ─────────────────────────────────────────────────────


def _user(request: Request) -> dict:
    from app.auth import get_current_user
    u = get_current_user(request)
    if not u:
        raise HTTPException(401, "auth required")
    return u


def _require_role(user: dict, slug: str, role: str) -> dict:
    from app.auth import check_project_permission
    proj = check_project_permission(user, slug, role)
    if not proj:
        raise HTTPException(403, f"{role} role required for project '{slug}'")
    return proj


def _viewer(user: dict, slug: str) -> dict:
    return _require_role(user, slug, "viewer")


def _editor(user: dict, slug: str) -> dict:
    return _require_role(user, slug, "editor")


# ── Engine helper ────────────────────────────────────────────────────


def _engine():
    from db.session import get_sql_engine
    eng = get_sql_engine()
    if eng is None:
        raise HTTPException(503, "db engine unavailable")
    return eng


# ── Bootstrap (idempotent) ──────────────────────────────────────────


_BOOTSTRAP_SQL = [
    """
    CREATE TABLE IF NOT EXISTS dash_campaigns (
        id SERIAL PRIMARY KEY,
        project_slug TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        type TEXT NOT NULL DEFAULT 'manual',
        status TEXT NOT NULL DEFAULT 'draft',
        target_segment TEXT,
        target_filter JSONB DEFAULT '{}'::jsonb,
        audience_size INTEGER DEFAULT 0,
        offer JSONB DEFAULT '{}'::jsonb,
        starts_at TIMESTAMPTZ,
        ends_at TIMESTAMPTZ,
        cost_budget NUMERIC(12,2),
        created_by TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_campaigns_project ON dash_campaigns(project_slug, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_status ON dash_campaigns(status)",
    """
    CREATE TABLE IF NOT EXISTS dash_campaign_events (
        id SERIAL PRIMARY KEY,
        campaign_id INTEGER REFERENCES dash_campaigns(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        actor TEXT,
        payload JSONB DEFAULT '{}'::jsonb,
        occurred_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_campaign_events_camp ON dash_campaign_events(campaign_id, occurred_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS dash_campaign_metrics (
        id SERIAL PRIMARY KEY,
        campaign_id INTEGER REFERENCES dash_campaigns(id) ON DELETE CASCADE,
        metric_name TEXT NOT NULL,
        value NUMERIC,
        period_start TIMESTAMPTZ,
        period_end TIMESTAMPTZ,
        recorded_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_campaign_metrics_camp ON dash_campaign_metrics(campaign_id, recorded_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_campaign_metrics_name ON dash_campaign_metrics(campaign_id, metric_name)",
]


def _bootstrap_tables() -> None:
    try:
        from db.session import get_sql_engine
        eng = get_sql_engine()
        if eng is None:
            logger.warning("campaigns: no engine available for bootstrap")
            return
        with eng.begin() as cn:
            for stmt in _BOOTSTRAP_SQL:
                try:
                    cn.execute(_t(stmt))
                except Exception as e:
                    logger.warning("campaigns bootstrap stmt failed: %s", e)
    except Exception:
        logger.exception("campaigns bootstrap failed")


try:
    _bootstrap_tables()
except Exception:
    logger.exception("campaigns bootstrap top-level failure")


# ── Utility ─────────────────────────────────────────────────────────


def _to_iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return v.isoformat()
    except Exception:
        return str(v)


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    # Tolerate trailing 'Z'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None


def _row_to_dict(r: Any) -> dict:
    md = r[16] if len(r) > 16 else None
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except Exception:
            md = {}
    return {
        "id": r[0],
        "project_slug": r[1],
        "name": r[2],
        "description": r[3],
        "type": r[4],
        "status": r[5],
        "target_segment": r[6],
        "target_filter": r[7] or {},
        "audience_size": r[8] or 0,
        "offer": r[9] or {},
        "starts_at": _to_iso(r[10]),
        "ends_at": _to_iso(r[11]),
        "cost_budget": float(r[12]) if r[12] is not None else None,
        "created_by": r[13],
        "created_at": _to_iso(r[14]),
        "updated_at": _to_iso(r[15]),
        "metadata": md or {},
    }


SELECT_COLS = (
    "id, project_slug, name, description, type, status, target_segment, "
    "target_filter, audience_size, offer, starts_at, ends_at, cost_budget, "
    "created_by, created_at, updated_at, "
    "COALESCE(metadata, '{}'::jsonb) AS metadata"
)


def _log_event(cn, campaign_id: int, event_type: str, actor: str, payload: dict | None = None) -> None:
    try:
        cn.execute(
            _t(
                "INSERT INTO dash_campaign_events (campaign_id, event_type, actor, payload) "
                "VALUES (:cid, :et, :a, CAST(:p AS jsonb))"
            ),
            {
                "cid": campaign_id,
                "et": event_type,
                "a": actor or "",
                "p": json.dumps(payload or {}),
            },
        )
    except Exception:
        logger.exception("campaign event log failed")


def _compute_audience(slug: str, target_segment: Optional[str]) -> int:
    if not target_segment:
        return 0
    seg_name = target_segment.strip()
    try:
        from dash.tools.customer_intelligence import rfm_score
    except Exception:
        logger.warning("rfm_score not importable")
        return 0
    try:
        if seg_name.lower().startswith("rfm:"):
            key = seg_name[4:].strip()
            rfm = rfm_score(slug)
            if not rfm.get("ok"):
                return 0
            segs = rfm.get("segments") or {}
            # rfm:555 means {R:5,F:5,M:5} grade Champions; we accept either segment
            # name OR raw rfm code by direct lookup, falling back to 0.
            if key in segs:
                return int(segs[key])
            # Allow raw code passthrough — count members whose rfm code matches
            top = rfm.get("top_customers") or []
            return int(sum(1 for c in top if str(c.get("rfm")) == key))
        if seg_name in RFM_SEGMENTS:
            rfm = rfm_score(slug)
            if not rfm.get("ok"):
                return 0
            return int((rfm.get("segments") or {}).get(seg_name, 0))
        return 0
    except Exception:
        logger.exception("audience compute failed")
        return 0


def _load_campaign(cn, slug: str, cid: int) -> dict:
    row = cn.execute(
        _t(f"SELECT {SELECT_COLS} FROM dash_campaigns WHERE id=:i AND project_slug=:s"),
        {"i": cid, "s": slug},
    ).fetchone()
    if not row:
        raise HTTPException(404, "campaign not found")
    return _row_to_dict(row)


# ── CREATE ──────────────────────────────────────────────────────────


@router.post("/projects/{slug}/campaigns")
async def create_campaign(slug: str, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json() or {}

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")

    ctype = (body.get("type") or "manual").strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(400, f"invalid type '{ctype}', allowed: {sorted(ALLOWED_TYPES)}")

    description = body.get("description") or ""
    target_segment = (body.get("target_segment") or None) or None
    target_filter = body.get("target_filter") or {}
    if not isinstance(target_filter, dict):
        raise HTTPException(400, "target_filter must be a JSON object")
    offer = body.get("offer") or {}
    if not isinstance(offer, dict):
        raise HTTPException(400, "offer must be a JSON object")

    starts_at = _parse_dt(body.get("starts_at"))
    ends_at = _parse_dt(body.get("ends_at"))
    cost_budget = body.get("cost_budget")
    if cost_budget is not None:
        try:
            cost_budget = float(cost_budget)
        except Exception:
            raise HTTPException(400, "cost_budget must be numeric")

    audience_size = _compute_audience(slug, target_segment)
    actor = user.get("username") or str(user.get("user_id") or "")

    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as cn:
        row = cn.execute(
            _t(
                """
                INSERT INTO dash_campaigns
                  (project_slug, name, description, type, status, target_segment,
                   target_filter, audience_size, offer, starts_at, ends_at,
                   cost_budget, created_by)
                VALUES
                  (:slug, :name, :desc, :type, 'draft', :seg,
                   CAST(:tf AS jsonb), :asize, CAST(:offer AS jsonb), :sa, :ea,
                   :cb, :cb_by)
                RETURNING id
                """
            ),
            {
                "slug": slug,
                "name": name,
                "desc": description,
                "type": ctype,
                "seg": target_segment,
                "tf": json.dumps(target_filter),
                "asize": audience_size,
                "offer": json.dumps(offer),
                "sa": starts_at,
                "ea": ends_at,
                "cb": cost_budget,
                "cb_by": actor,
            },
        ).fetchone()
        cid = int(row[0])
        _log_event(cn, cid, "created", actor, {
            "name": name, "type": ctype, "target_segment": target_segment,
            "audience_size": audience_size,
        })
        camp = _load_campaign(cn, slug, cid)
    return camp


# ── LIST ────────────────────────────────────────────────────────────


@router.get("/projects/{slug}/campaigns")
def list_campaigns(slug: str, request: Request, status: str | None = None, limit: int = 50):
    user = _user(request)
    _viewer(user, slug)
    limit = max(1, min(int(limit or 50), 500))
    eng = _engine()
    params: dict = {"slug": slug, "lim": limit}
    where = "project_slug=:slug"
    if status:
        if status not in ALLOWED_STATUSES:
            raise HTTPException(400, f"invalid status filter '{status}'")
        where += " AND status=:st"
        params["st"] = status
    with eng.begin() as cn:
        rows = cn.execute(
            _t(
                f"SELECT {SELECT_COLS} FROM dash_campaigns "
                f"WHERE {where} ORDER BY created_at DESC LIMIT :lim"
            ),
            params,
        ).fetchall()
    return {"campaigns": [_row_to_dict(r) for r in rows], "count": len(rows)}


# ── SUMMARY ─────────────────────────────────────────────────────────


@router.get("/projects/{slug}/campaigns/summary")
def campaigns_summary(slug: str, request: Request):
    user = _user(request)
    _viewer(user, slug)
    eng = _engine()
    with eng.begin() as cn:
        rows = cn.execute(
            _t("SELECT status, COUNT(*) FROM dash_campaigns WHERE project_slug=:s GROUP BY status"),
            {"s": slug},
        ).fetchall()
        by_status = {r[0]: int(r[1]) for r in rows}
        agg = cn.execute(
            _t(
                "SELECT COUNT(*), COALESCE(SUM(audience_size),0), COALESCE(SUM(cost_budget),0) "
                "FROM dash_campaigns WHERE project_slug=:s"
            ),
            {"s": slug},
        ).fetchone()
        recent = cn.execute(
            _t(
                f"SELECT {SELECT_COLS} FROM dash_campaigns "
                f"WHERE project_slug=:s ORDER BY created_at DESC LIMIT 5"
            ),
            {"s": slug},
        ).fetchall()
    total = int(agg[0]) if agg else 0
    total_audience = int(agg[1]) if agg else 0
    total_budget = float(agg[2]) if agg and agg[2] is not None else 0.0
    return {
        "total": total,
        "by_status": by_status,
        "total_audience": total_audience,
        "total_budget": total_budget,
        "recent": [_row_to_dict(r) for r in recent],
    }


# ── DETAIL ──────────────────────────────────────────────────────────


@router.get("/projects/{slug}/campaigns/{cid}")
def get_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _viewer(user, slug)
    eng = _engine()
    with eng.begin() as cn:
        camp = _load_campaign(cn, slug, cid)
        ev_rows = cn.execute(
            _t(
                "SELECT id, event_type, actor, payload, occurred_at "
                "FROM dash_campaign_events WHERE campaign_id=:c "
                "ORDER BY occurred_at DESC LIMIT 200"
            ),
            {"c": cid},
        ).fetchall()
        m_rows = cn.execute(
            _t(
                "SELECT id, metric_name, value, period_start, period_end, recorded_at "
                "FROM dash_campaign_metrics WHERE campaign_id=:c "
                "ORDER BY recorded_at DESC LIMIT 500"
            ),
            {"c": cid},
        ).fetchall()
    events = [
        {
            "id": r[0],
            "event_type": r[1],
            "actor": r[2],
            "payload": r[3] or {},
            "occurred_at": _to_iso(r[4]),
        }
        for r in ev_rows
    ]
    metrics = [
        {
            "id": r[0],
            "metric_name": r[1],
            "value": float(r[2]) if r[2] is not None else None,
            "period_start": _to_iso(r[3]),
            "period_end": _to_iso(r[4]),
            "recorded_at": _to_iso(r[5]),
        }
        for r in m_rows
    ]
    return {**camp, "events": events, "metrics": metrics}


# ── PATCH ───────────────────────────────────────────────────────────


@router.patch("/projects/{slug}/campaigns/{cid}")
async def update_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json() or {}

    sets: list[str] = []
    params: dict = {"i": cid, "s": slug}
    json_fields = {"target_filter", "offer"}
    dt_fields = {"starts_at", "ends_at"}
    changed: dict = {}

    for key, raw in body.items():
        if key not in UPDATABLE_FIELDS:
            continue
        val = raw
        if key == "type":
            v = (raw or "").strip().lower()
            if v not in ALLOWED_TYPES:
                raise HTTPException(400, f"invalid type '{v}'")
            val = v
        elif key == "status":
            v = (raw or "").strip().lower()
            if v not in ALLOWED_STATUSES:
                raise HTTPException(400, f"invalid status '{v}'")
            val = v
        elif key in json_fields:
            if not isinstance(raw, dict):
                raise HTTPException(400, f"{key} must be a JSON object")
            sets.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = json.dumps(raw)
            changed[key] = raw
            continue
        elif key in dt_fields:
            val = _parse_dt(raw)
        elif key == "cost_budget" and raw is not None:
            try:
                val = float(raw)
            except Exception:
                raise HTTPException(400, "cost_budget must be numeric")
        elif key == "audience_size" and raw is not None:
            try:
                val = int(raw)
            except Exception:
                raise HTTPException(400, "audience_size must be integer")
        sets.append(f"{key} = :{key}")
        params[key] = val
        changed[key] = val if not isinstance(val, datetime) else _to_iso(val)

    if not sets:
        raise HTTPException(400, "no updatable fields supplied")

    sets.append("updated_at = NOW()")
    actor = user.get("username") or str(user.get("user_id") or "")

    from db.session import get_write_engine
    eng = get_write_engine()
    with eng.begin() as cn:
        # Ensure exists + project match
        _load_campaign(cn, slug, cid)
        cn.execute(
            _t(
                f"UPDATE dash_campaigns SET {', '.join(sets)} "
                f"WHERE id=:i AND project_slug=:s"
            ),
            params,
        )
        # If target_segment changed and audience_size not explicitly set, recompute
        if "target_segment" in changed and "audience_size" not in changed:
            new_seg = changed.get("target_segment")
            new_size = _compute_audience(slug, new_seg)
            cn.execute(
                _t("UPDATE dash_campaigns SET audience_size=:a WHERE id=:i"),
                {"a": new_size, "i": cid},
            )
            changed["audience_size"] = new_size
        _log_event(cn, cid, "updated", actor, {"changes": changed})
        camp = _load_campaign(cn, slug, cid)
    return camp


# ── DELETE (soft) ───────────────────────────────────────────────────


@router.delete("/projects/{slug}/campaigns/{cid}")
def delete_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    actor = user.get("username") or str(user.get("user_id") or "")
    eng = _engine()
    with eng.begin() as cn:
        _load_campaign(cn, slug, cid)
        cn.execute(
            _t(
                "UPDATE dash_campaigns SET status='cancelled', updated_at=NOW() "
                "WHERE id=:i AND project_slug=:s"
            ),
            {"i": cid, "s": slug},
        )
        _log_event(cn, cid, "cancelled", actor, {})
        camp = _load_campaign(cn, slug, cid)
    return {"ok": True, "campaign": camp}


# ── Lifecycle transitions ───────────────────────────────────────────


def _transition(slug: str, cid: int, new_status: str, event_type: str, actor: str,
                set_starts_if_null: bool = False) -> dict:
    eng = _engine()
    with eng.begin() as cn:
        _load_campaign(cn, slug, cid)
        if set_starts_if_null:
            cn.execute(
                _t(
                    "UPDATE dash_campaigns SET status=:st, "
                    "starts_at = COALESCE(starts_at, NOW()), updated_at=NOW() "
                    "WHERE id=:i AND project_slug=:s"
                ),
                {"st": new_status, "i": cid, "s": slug},
            )
        else:
            cn.execute(
                _t(
                    "UPDATE dash_campaigns SET status=:st, updated_at=NOW() "
                    "WHERE id=:i AND project_slug=:s"
                ),
                {"st": new_status, "i": cid, "s": slug},
            )
        _log_event(cn, cid, event_type, actor, {"status": new_status})
        camp = _load_campaign(cn, slug, cid)
    return camp


@router.post("/projects/{slug}/campaigns/{cid}/launch")
def launch_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    actor = user.get("username") or str(user.get("user_id") or "")
    return _transition(slug, cid, "active", "launched", actor, set_starts_if_null=True)


@router.post("/projects/{slug}/campaigns/{cid}/pause")
def pause_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    actor = user.get("username") or str(user.get("user_id") or "")
    return _transition(slug, cid, "paused", "paused", actor)


@router.post("/projects/{slug}/campaigns/{cid}/resume")
def resume_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    actor = user.get("username") or str(user.get("user_id") or "")
    return _transition(slug, cid, "active", "resumed", actor)


@router.post("/projects/{slug}/campaigns/{cid}/complete")
def complete_campaign(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    actor = user.get("username") or str(user.get("user_id") or "")
    return _transition(slug, cid, "completed", "completed", actor)


# ── Metric record ────────────────────────────────────────────────────


@router.post("/projects/{slug}/campaigns/{cid}/metric")
async def record_metric(slug: str, cid: int, request: Request):
    user = _user(request)
    _editor(user, slug)
    body = await request.json() or {}
    metric_name = (body.get("metric_name") or "").strip()
    if not metric_name:
        raise HTTPException(400, "metric_name required")
    value = body.get("value")
    if value is None:
        raise HTTPException(400, "value required")
    try:
        value = float(value)
    except Exception:
        raise HTTPException(400, "value must be numeric")
    period_start = _parse_dt(body.get("period_start"))
    period_end = _parse_dt(body.get("period_end"))
    actor = user.get("username") or str(user.get("user_id") or "")
    eng = _engine()
    with eng.begin() as cn:
        _load_campaign(cn, slug, cid)
        row = cn.execute(
            _t(
                "INSERT INTO dash_campaign_metrics "
                "(campaign_id, metric_name, value, period_start, period_end) "
                "VALUES (:c, :n, :v, :ps, :pe) RETURNING id, recorded_at"
            ),
            {"c": cid, "n": metric_name, "v": value, "ps": period_start, "pe": period_end},
        ).fetchone()
        _log_event(cn, cid, "metric_recorded", actor, {
            "metric_name": metric_name, "value": value,
        })
    return {
        "ok": True,
        "id": int(row[0]),
        "campaign_id": cid,
        "metric_name": metric_name,
        "value": value,
        "period_start": _to_iso(period_start),
        "period_end": _to_iso(period_end),
        "recorded_at": _to_iso(row[1]),
    }


# ── Audience preview ────────────────────────────────────────────────


@router.get("/projects/{slug}/campaigns/{cid}/audience")
def campaign_audience(slug: str, cid: int, request: Request):
    user = _user(request)
    _viewer(user, slug)
    eng = _engine()
    with eng.begin() as cn:
        camp = _load_campaign(cn, slug, cid)

    seg = camp.get("target_segment") or ""
    target_filter = camp.get("target_filter") or {}
    customers: list[dict] = []
    audience_total = 0

    try:
        from dash.tools.customer_intelligence import rfm_score
        rfm = rfm_score(slug)
        if rfm.get("ok"):
            top = rfm.get("top_customers") or []
            seg_norm = seg.strip()
            if seg_norm.lower().startswith("rfm:"):
                key = seg_norm[4:].strip()
                pool = [c for c in top if str(c.get("rfm")) == key]
            elif seg_norm in RFM_SEGMENTS:
                pool = [c for c in top if c.get("segment") == seg_norm]
            else:
                pool = top

            # Apply JSON filter post-process: simple equality match on top-level keys
            def _matches(c: dict, flt: dict) -> bool:
                for k, v in (flt or {}).items():
                    cv = c.get(k)
                    if isinstance(v, dict):
                        if "min" in v and cv is not None and cv < v["min"]:
                            return False
                        if "max" in v and cv is not None and cv > v["max"]:
                            return False
                    elif isinstance(v, list):
                        if cv not in v:
                            return False
                    else:
                        if cv != v:
                            return False
                return True

            filtered = [c for c in pool if _matches(c, target_filter)]
            customers = filtered[:100]
            audience_total = (
                int((rfm.get("segments") or {}).get(seg_norm, 0))
                if seg_norm in RFM_SEGMENTS
                else len(filtered)
            )
    except Exception:
        logger.exception("audience query failed")

    return {
        "campaign_id": cid,
        "target_segment": seg,
        "target_filter": target_filter,
        "audience_total": audience_total,
        "audience_sample": customers,
        "sample_count": len(customers),
    }


# ── Audience full CSV export ────────────────────────────────────────


@router.get("/projects/{slug}/campaigns/{cid}/audience.csv")
def campaign_audience_csv(slug: str, cid: int, request: Request):
    """Stream the FULL audience for a campaign as CSV (no 100-row cap).

    Filters dash_customer_scores by project + campaign target_segment
    (and rfm:NNN code variant). target_filter equality / min / max / IN
    on top-level score columns is applied per-row (matching the JSON-flat
    semantics used by the audience preview endpoint).
    """
    from fastapi.responses import StreamingResponse
    import csv as _csv
    import io as _io

    user = _user(request)
    _viewer(user, slug)
    eng = _engine()
    with eng.begin() as cn:
        camp = _load_campaign(cn, slug, cid)

    seg = (camp.get("target_segment") or "").strip()
    target_filter = camp.get("target_filter") or {}
    if not isinstance(target_filter, dict):
        target_filter = {}

    columns = [
        "customer_id", "rfm_segment", "churn_risk", "clv_predicted",
        "total_spend", "order_count", "last_purchase", "days_since_last",
    ]

    # Build SQL — parameterized
    where = ["project_slug = :slug"]
    params: dict[str, Any] = {"slug": slug}
    if seg:
        if seg.lower().startswith("rfm:"):
            code = seg[4:].strip()
            # RFM code format e.g. "555" stored as concat of rfm_r/f/m
            where.append(
                "(COALESCE(rfm_r,0)::text || COALESCE(rfm_f,0)::text || COALESCE(rfm_m,0)::text) = :rfm"
            )
            params["rfm"] = code
        elif seg in RFM_SEGMENTS:
            where.append("rfm_segment = :seg")
            params["seg"] = seg
        # else: unknown segment string → no segment filter (export all in project)

    # Note: dash_customer_scores has no `last_purchase` column; we derive it
    # from last_computed - days_since_last when available, else leave blank.
    sql = (
        "SELECT customer_id, rfm_segment, churn_risk, clv_predicted, "
        "total_spend, order_count, "
        "(last_computed - (COALESCE(days_since_last,0)::int * INTERVAL '1 day'))::date AS last_purchase, "
        "days_since_last "
        "FROM dash.dash_customer_scores WHERE " + " AND ".join(where) + " "
        "ORDER BY total_spend DESC NULLS LAST, customer_id"
    )

    def _matches(row: dict, flt: dict) -> bool:
        for k, v in (flt or {}).items():
            cv = row.get(k)
            if isinstance(v, dict):
                if "min" in v and cv is not None and cv < v["min"]:
                    return False
                if "max" in v and cv is not None and cv > v["max"]:
                    return False
            elif isinstance(v, list):
                if cv not in v:
                    return False
            else:
                if cv != v:
                    return False
        return True

    BATCH = 500

    def _stream():
        try:
            buf = _io.StringIO()
            writer = _csv.writer(buf)
            writer.writerow(columns)
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

            with eng.connect() as cn:
                # server-side streaming if supported; else fallback
                try:
                    result = cn.execution_options(stream_results=True).execute(_t(sql), params)
                except Exception:
                    result = cn.execute(_t(sql), params)
                count = 0
                while True:
                    rows = result.fetchmany(BATCH)
                    if not rows:
                        break
                    for r in rows:
                        rd = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
                        if target_filter and not _matches(rd, target_filter):
                            continue
                        writer.writerow([rd.get(c, "") if rd.get(c) is not None else "" for c in columns])
                        count += 1
                    yield buf.getvalue()
                    buf.seek(0); buf.truncate(0)
        except Exception as e:
            logger.exception("audience CSV stream failed")
            # Surface error as a CSV comment line so client sees something
            yield f"# error,{str(e)[:200]}\n"

    headers = {
        "Content-Disposition": f'attachment; filename="campaign_{cid}_audience.csv"',
        "Cache-Control": "no-store",
    }
    return StreamingResponse(_stream(), media_type="text/csv", headers=headers)


# ── ROI rollup ──────────────────────────────────────────────────────


@router.get("/projects/{slug}/campaigns/{cid}/roi")
def campaign_roi(slug: str, cid: int, request: Request):
    user = _user(request)
    _viewer(user, slug)
    eng = _engine()
    with eng.begin() as cn:
        camp = _load_campaign(cn, slug, cid)
        rows = cn.execute(
            _t(
                "SELECT metric_name, COALESCE(SUM(value),0) "
                "FROM dash_campaign_metrics WHERE campaign_id=:c GROUP BY metric_name"
            ),
            {"c": cid},
        ).fetchall()

    totals = {r[0]: float(r[1] or 0) for r in rows}
    revenue = float(totals.get("revenue", 0.0))
    conversions = float(totals.get("conversions", 0.0))
    impressions = float(totals.get("impressions", 0.0))
    clicks = float(totals.get("clicks", 0.0))
    opt_outs = float(totals.get("opt_outs", 0.0))
    cost = float(camp.get("cost_budget") or 0.0)

    if cost > 0:
        roi_pct = (revenue - cost) / cost * 100.0
    else:
        roi_pct = None

    audience = float(camp.get("audience_size") or 0)
    conversion_rate = (conversions / audience * 100.0) if audience > 0 else None
    ctr = (clicks / impressions * 100.0) if impressions > 0 else None

    return {
        "campaign_id": cid,
        "revenue_total": revenue,
        "conversions_total": conversions,
        "impressions_total": impressions,
        "clicks_total": clicks,
        "opt_outs_total": opt_outs,
        "cost": cost,
        "audience": int(audience),
        "roi_pct": roi_pct,
        "conversion_rate": conversion_rate,
        "ctr": ctr,
        "totals": totals,
    }


# ── Auto-Campaign Daemon endpoints ──────────────────────────────────


@router.post("/projects/{slug}/auto-campaign/run-now")
def auto_campaign_run_now(slug: str, request: Request):
    """Trigger one cycle of the auto-campaign daemon for this project.

    Owner-or-admin gated. Returns counts + diagnostics:
      {drafts_created, rules_fired: [...], cooldown_skipped: [...],
       skipped_reason, snapshot_id}
    """
    user = _user(request)
    # Editor (owner/admin) is the closest existing role gate; matches
    # other write surfaces on this router.
    _editor(user, slug)
    try:
        from dash.cron.auto_campaign_daemon import run_cycle_for_project
    except Exception as e:
        raise HTTPException(503, f"auto_campaign daemon unavailable: {e}")
    try:
        result = run_cycle_for_project(slug)
    except Exception:
        logger.exception("auto_campaign run-now crashed")
        raise HTTPException(500, "auto_campaign cycle failed")
    return {"ok": True, **result}


@router.post("/projects/auto-campaign/cycle-all")
def auto_campaign_cycle_all(request: Request):
    """Run a single cycle across every project. Super-admin only.

    Used by the K8s CronJob; saves the cron from having to enumerate
    project slugs externally.
    """
    user = _user(request)
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "super-admin required")
    try:
        from dash.cron.auto_campaign_daemon import run_cycle
    except Exception as e:
        raise HTTPException(503, f"auto_campaign daemon unavailable: {e}")
    try:
        result = run_cycle()
    except Exception:
        logger.exception("auto_campaign cycle-all crashed")
        raise HTTPException(500, "auto_campaign cycle-all failed")
    return {"ok": True, **result}
