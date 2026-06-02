"""OpenRouter model catalog sync + search.

Persists snapshot of https://openrouter.ai/api/v1/models into
dash.dash_llm_model_catalog so the admin UI can browse, filter, sort
without paying live-fetch latency.

Public API:
    sync_catalog()                -> {count, synced_at}
    search_catalog(...)           -> {items, total, has_more}
    get_catalog_entry(model_id)   -> dict | None
    get_sync_status()             -> {count, last_synced_at}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import text

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_HTTP_TIMEOUT_S = 30.0
_POPULAR_PROVIDERS = ("anthropic", "openai", "google", "deepseek", "meta-llama")


def _write_engine():
    from db.session import get_write_engine
    return get_write_engine()


def _read_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── helpers ────────────────────────────────────────────────────────────

def _to_num(v: Any) -> Optional[float]:
    """OpenRouter pricing fields are strings of dollars-per-token. Convert
    to dollars per 1M tokens. Returns None on parse failure."""
    if v is None:
        return None
    try:
        return float(v) * 1_000_000.0
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _provider_of(model_id: str) -> str:
    if not model_id or "/" not in model_id:
        return model_id or ""
    return model_id.split("/", 1)[0]


def _parse_entry(m: dict) -> Optional[dict]:
    mid = (m or {}).get("id")
    if not mid or not isinstance(mid, str):
        return None
    arch = m.get("architecture") or {}
    pricing = m.get("pricing") or {}
    modalities = arch.get("input_modalities") or []
    if not isinstance(modalities, list):
        modalities = []
    supported = m.get("supported_parameters") or []
    if not isinstance(supported, list):
        supported = []
    return {
        "id": mid,
        "name": m.get("name") or mid,
        "provider": _provider_of(mid),
        "description": m.get("description"),
        "context_length": _safe_int(m.get("context_length")),
        "pricing_prompt": _to_num(pricing.get("prompt")),
        "pricing_completion": _to_num(pricing.get("completion")),
        "modalities": modalities,
        "supported_params": supported,
        "top_provider": m.get("top_provider"),
        "raw": m,
    }


# ── sync ───────────────────────────────────────────────────────────────

def sync_catalog() -> dict:
    """Fetch OpenRouter /models + upsert all rows. Fail-soft on network."""
    try:
        r = httpx.get(OPENROUTER_MODELS_URL, timeout=_HTTP_TIMEOUT_S)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning("llm_catalog.sync_catalog fetch failed: %s", e)
        return {"count": 0, "synced_at": None, "error": str(e)[:200]}

    data = (payload or {}).get("data") or []
    parsed = [p for p in (_parse_entry(m) for m in data) if p]
    if not parsed:
        logger.warning("llm_catalog.sync_catalog: empty data from OpenRouter")
        return {"count": 0, "synced_at": None}

    now = datetime.now(timezone.utc)
    sql = text("""
        INSERT INTO dash.dash_llm_model_catalog
            (id, name, provider, description, context_length,
             pricing_prompt, pricing_completion,
             modalities, supported_params, top_provider, raw, synced_at)
        VALUES
            (:id, :name, :provider, :description, :context_length,
             :pricing_prompt, :pricing_completion,
             CAST(:modalities AS jsonb),
             CAST(:supported_params AS jsonb),
             CAST(:top_provider AS jsonb),
             CAST(:raw AS jsonb),
             :synced_at)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            provider = EXCLUDED.provider,
            description = EXCLUDED.description,
            context_length = EXCLUDED.context_length,
            pricing_prompt = EXCLUDED.pricing_prompt,
            pricing_completion = EXCLUDED.pricing_completion,
            modalities = EXCLUDED.modalities,
            supported_params = EXCLUDED.supported_params,
            top_provider = EXCLUDED.top_provider,
            raw = EXCLUDED.raw,
            synced_at = EXCLUDED.synced_at
    """)
    count = 0
    eng = _write_engine()
    with eng.begin() as cn:
        for p in parsed:
            try:
                cn.execute(sql, {
                    "id": p["id"],
                    "name": p["name"],
                    "provider": p["provider"],
                    "description": p["description"],
                    "context_length": p["context_length"],
                    "pricing_prompt": p["pricing_prompt"],
                    "pricing_completion": p["pricing_completion"],
                    "modalities": json.dumps(p["modalities"]),
                    "supported_params": json.dumps(p["supported_params"]),
                    "top_provider": json.dumps(p["top_provider"]) if p["top_provider"] is not None else None,
                    "raw": json.dumps(p["raw"]),
                    "synced_at": now,
                })
                count += 1
            except Exception:
                logger.exception("llm_catalog upsert failed id=%s", p.get("id"))
    logger.info("llm_catalog.sync_catalog: upserted %d rows", count)
    return {"count": count, "synced_at": now.isoformat()}


# ── search ─────────────────────────────────────────────────────────────

def _row_to_item(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "provider": r.get("provider"),
        "description": r.get("description"),
        "context_length": r.get("context_length"),
        "pricing_prompt": float(r["pricing_prompt"]) if r.get("pricing_prompt") is not None else None,
        "pricing_completion": float(r["pricing_completion"]) if r.get("pricing_completion") is not None else None,
        "modalities": r.get("modalities") or [],
        "supported_params": r.get("supported_params") or [],
        "is_free": bool(r.get("is_free")),
    }


def search_catalog(
    q: Optional[str] = None,
    provider: Optional[str] = None,
    min_ctx: Optional[int] = None,
    max_price: Optional[float] = None,
    free_only: bool = False,
    tools_only: bool = False,
    vision_only: bool = False,
    reasoning_only: bool = False,
    sort: str = "popularity",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search catalog. Returns {items, total, has_more, synced_at}."""
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 50
    try:
        offset = max(0, int(offset))
    except Exception:
        offset = 0

    where: list[str] = []
    params: dict[str, Any] = {}

    if q:
        where.append("(id ILIKE :q OR name ILIKE :q OR provider ILIKE :q)")
        params["q"] = f"%{q}%"
    if provider:
        where.append("provider = :provider")
        params["provider"] = provider
    if min_ctx is not None:
        where.append("context_length >= :min_ctx")
        params["min_ctx"] = int(min_ctx)
    if max_price is not None:
        where.append("pricing_prompt <= :max_price")
        params["max_price"] = float(max_price)
    if free_only:
        where.append("is_free = TRUE")
    if tools_only:
        where.append("supported_params @> CAST(:tools_arr AS jsonb)")
        params["tools_arr"] = json.dumps(["tools"])
    if vision_only:
        where.append("modalities @> CAST(:vision_arr AS jsonb)")
        params["vision_arr"] = json.dumps(["image"])
    if reasoning_only:
        where.append("supported_params @> CAST(:reasoning_arr AS jsonb)")
        params["reasoning_arr"] = json.dumps(["reasoning"])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    if sort == "price":
        order_sql = "ORDER BY pricing_prompt ASC NULLS LAST, id ASC"
    elif sort == "context":
        order_sql = "ORDER BY context_length DESC NULLS LAST, id ASC"
    elif sort == "newest":
        order_sql = "ORDER BY synced_at DESC, id ASC"
    else:  # popularity (default)
        order_sql = (
            "ORDER BY (provider IN ('anthropic','openai','google','deepseek','meta-llama')) DESC, "
            "context_length DESC NULLS LAST, id ASC"
        )

    items_sql = text(f"""
        SELECT id, name, provider, description, context_length,
               pricing_prompt, pricing_completion,
               modalities, supported_params, is_free
        FROM dash.dash_llm_model_catalog
        {where_sql}
        {order_sql}
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(f"SELECT COUNT(*) FROM dash.dash_llm_model_catalog {where_sql}")
    status_sql = text("SELECT MAX(synced_at) FROM dash.dash_llm_model_catalog")

    with _read_engine().connect() as cn:
        total_row = cn.execute(count_sql, params).first()
        total = int(total_row[0]) if total_row and total_row[0] is not None else 0
        params_with_paging = {**params, "limit": limit, "offset": offset}
        rows = cn.execute(items_sql, params_with_paging).mappings().all()
        synced_row = cn.execute(status_sql).first()
        synced_at = synced_row[0].isoformat() if synced_row and synced_row[0] else None

    items = [_row_to_item(dict(r)) for r in rows]
    has_more = (offset + len(items)) < total
    return {
        "items": items,
        "total": total,
        "has_more": has_more,
        "synced_at": synced_at,
    }


def get_catalog_entry(model_id: str) -> Optional[dict]:
    if not model_id:
        return None
    sql = text("""
        SELECT id, name, provider, description, context_length,
               pricing_prompt, pricing_completion,
               modalities, supported_params, top_provider,
               is_free, raw, synced_at
        FROM dash.dash_llm_model_catalog
        WHERE id = :id
    """)
    with _read_engine().connect() as cn:
        r = cn.execute(sql, {"id": model_id}).mappings().first()
    if not r:
        return None
    out = dict(r)
    if out.get("pricing_prompt") is not None:
        out["pricing_prompt"] = float(out["pricing_prompt"])
    if out.get("pricing_completion") is not None:
        out["pricing_completion"] = float(out["pricing_completion"])
    if out.get("synced_at") is not None:
        try:
            out["synced_at"] = out["synced_at"].isoformat()
        except Exception:
            pass
    return out


def get_sync_status() -> dict:
    sql = text("SELECT COUNT(*) AS c, MAX(synced_at) AS last FROM dash.dash_llm_model_catalog")
    try:
        with _read_engine().connect() as cn:
            r = cn.execute(sql).mappings().first()
    except Exception as e:
        logger.warning("llm_catalog.get_sync_status failed: %s", e)
        return {"count": 0, "last_synced_at": None}
    if not r:
        return {"count": 0, "last_synced_at": None}
    last = r.get("last")
    return {
        "count": int(r.get("c") or 0),
        "last_synced_at": last.isoformat() if last else None,
    }


__all__ = [
    "sync_catalog",
    "search_catalog",
    "get_catalog_entry",
    "get_sync_status",
]
