"""Admin settings HTTP API."""
import logging
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_user(request: Request) -> dict:
    from app.auth import get_current_user
    user = getattr(getattr(request, "state", None), "user", None) or get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


@router.get("/settings")
def list_settings_endpoint(request: Request, project_slug: str = ""):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.settings import list_settings
    return {"settings": list_settings(project_slug=project_slug or None)}


@router.get("/settings/{key}")
def get_setting_endpoint(key: str, request: Request, project_slug: str = ""):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.settings import get_setting, REGISTRY
    if key not in REGISTRY:
        raise HTTPException(404, f"unknown setting: {key}")
    return {
        "key": key,
        "value": get_setting(key, project_slug=project_slug or None),
        "default": REGISTRY[key].get("default"),
        "type": REGISTRY[key]["type"],
        "description": REGISTRY[key].get("desc", ""),
    }


@router.post("/settings")
async def set_settings_endpoint(request: Request):
    user = _get_user(request)
    _require_super(user)
    body = await request.json()
    items = body.get("settings", [])
    if not isinstance(items, list):
        raise HTTPException(400, "settings must be list of {key,value,scope,project_slug}")

    from dash.admin.settings import set_setting
    results = []
    for item in items:
        ok, err = set_setting(
            item.get("key"), item.get("value"),
            scope=item.get("scope", "global"),
            project_slug=item.get("project_slug"),
            user_id=user.get("user_id"),
        )
        results.append({"key": item.get("key"), "ok": ok, "error": err})
    return {"results": results}


@router.post("/settings/reset")
async def reset_setting_endpoint(request: Request):
    user = _get_user(request)
    _require_super(user)
    body = await request.json()
    from dash.admin.settings import reset_setting
    ok = reset_setting(
        body.get("key"),
        scope=body.get("scope", "global"),
        project_slug=body.get("project_slug"),
    )
    return {"reset": ok}


@router.get("/settings/effective/{slug}/{key}")
def get_effective(slug: str, key: str, request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.settings import get_setting, REGISTRY
    if key not in REGISTRY:
        raise HTTPException(404)
    return {
        "key": key, "slug": slug,
        "effective": get_setting(key, project_slug=slug),
    }


@router.post("/skills/cache/invalidate")
async def invalidate_skill_prefix_cache(request: Request):
    """Issue #12: flush the in-process `_SKILL_CACHE` used by
    `dash/dashboards/agent.py::_skill_prefix` so SkillRefinery edits take
    effect immediately instead of waiting up to 5 min for TTL expiry.

    Body (optional JSON): {"skill_id": "skl_..."} to evict a single entry.
    Omitted body or empty body clears the whole cache. Super-admin gated.
    """
    user = _get_user(request)
    _require_super(user)
    skill_id: str | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            sid = body.get("skill_id")
            if isinstance(sid, str) and sid.strip():
                skill_id = sid.strip()
    except Exception:
        pass
    try:
        from dash.dashboards.agent import invalidate_skill_cache
    except ImportError as e:
        raise HTTPException(503, f"skill cache helper not available: {e}")
    removed = invalidate_skill_cache(skill_id)
    return {"ok": True, "removed": removed, "skill_id": skill_id}


@router.get("/llm-costs")
def llm_costs_endpoint(request: Request, days: int = 30):
    """Daily LLM spend per project + model from public.dash_llm_costs.

    Powers the Cost Analytics telemetry tab. Returns {items:[{day,project,model,cost}]}.
    Fail-soft: empty list if the cost ledger table is absent.
    """
    user = _get_user(request)
    _require_super(user)
    try:
        days_i = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days_i = 30

    from sqlalchemy import text
    from db import get_sql_engine

    eng = get_sql_engine()
    items: list[dict] = []
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS day, "
                    "COALESCE(NULLIF(project_slug, ''), '—') AS project, "
                    "COALESCE(model, '') AS model, "
                    "SUM(cost_usd) AS cost "
                    "FROM public.dash_llm_costs "
                    "WHERE ts >= now() - make_interval(days => :d) "
                    "GROUP BY 1, 2, 3 ORDER BY 1"
                ),
                {"d": days_i},
            ).fetchall()
        items = [
            {"day": r[0], "project": r[1], "model": r[2], "cost": float(r[3] or 0)}
            for r in rows
        ]
    except Exception as e:  # table missing / DB hiccup → fail-soft empty
        logger.warning("llm-costs query failed: %s", e)
        items = []
    # NOTE: get_sql_engine() returns the cached shared engine — never dispose it.
    return {"items": items, "days": days_i}


@router.get("/sse-audit")
def sse_audit(
    request: Request,
    days: int = 7,
    missing_event: str | None = None,
    limit: int = 100,
):
    """Phase 7: SSE audit query — observability for emit events.

    Default mode returns a rollup of event_name → count/errors/avg_bytes over
    the lookback window. When `missing_event` is provided, returns the list of
    sessions that emitted at least one event in the window but never emitted
    that target event (catches broken streams where e.g. TeamRunContent never
    fired). Super-admin gated, fail-soft empty on missing audit table.
    """
    user = _get_user(request)
    _require_super(user)

    from sqlalchemy import text
    from db import get_sql_engine

    eng = get_sql_engine()
    try:
        if missing_event:
            sql = """
            WITH recent AS (
              SELECT DISTINCT session_id FROM public.dash_sse_audit
              WHERE ts > now() - (:d || ' days')::interval AND session_id IS NOT NULL
            ),
            had AS (
              SELECT DISTINCT session_id FROM public.dash_sse_audit
              WHERE event_name = :ev AND ts > now() - (:d || ' days')::interval
            )
            SELECT r.session_id FROM recent r
            WHERE r.session_id NOT IN (SELECT session_id FROM had)
            LIMIT :lim
            """
            with eng.connect() as conn:
                rows = conn.execute(
                    text(sql),
                    {"d": days, "ev": missing_event, "lim": limit},
                ).fetchall()
            return {
                "broken_sessions": [r[0] for r in rows],
                "missing_event": missing_event,
                "days": days,
            }
        # Default: rollup by event_name
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT event_name, COUNT(*) c, COUNT(error) errs, "
                    "AVG(bytes_emitted)::int avg_bytes "
                    "FROM public.dash_sse_audit "
                    "WHERE ts > now() - (:d || ' days')::interval "
                    "GROUP BY event_name ORDER BY c DESC LIMIT 50"
                ),
                {"d": days},
            ).fetchall()
        return {
            "days": days,
            "by_event": [
                {"event": r[0], "count": r[1], "errors": r[2], "avg_bytes": r[3]}
                for r in rows
            ],
        }
    except Exception as e:
        logger.warning("sse-audit query failed: %s", e)
        return {"days": days, "by_event": [], "error": str(e)}


@router.get("/openrouter/pool")
def openrouter_pool_stats(request: Request):
    """Multi-key OpenRouter pool stats: in-flight, total OK, total 429s, cooldown remaining per key."""
    user = _get_user(request)
    _require_super(user)
    from dash.llm_client import get_pool
    pool = get_pool()
    return {"keys": pool.stats(), "has_keys": pool.has_keys()}


# ── LLM keys (encrypted, UI-managed) ────────────────────────────────────────
from pydantic import BaseModel


class _AddKeyBody(BaseModel):
    label: str
    raw_key: str
    notes: str | None = None


@router.get("/llm/keys")
def llm_keys_list(request: Request):
    """List LLM keys (no plaintext — only last 6 chars via key_suffix)."""
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_keys import list_keys
    return {"keys": list_keys()}


@router.post("/llm/keys")
def llm_keys_add(body: _AddKeyBody, request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_keys import add_key
    if len(body.raw_key.strip()) < 16:
        raise HTTPException(400, "key too short — looks invalid")
    return add_key(label=body.label, raw_key=body.raw_key, created_by=user.get("id"), notes=body.notes)


class _ToggleBody(BaseModel):
    label: str | None = None
    notes: str | None = None
    enabled: bool | None = None
    raw_key: str | None = None


@router.patch("/llm/keys/{key_id}")
def llm_keys_toggle(key_id: int, body: _ToggleBody, request: Request):
    """Update LLM key. Backward compatible: {enabled:bool} still toggles.

    Body may also include label/notes/raw_key. If raw_key provided, the row's
    encrypted_key + key_suffix are rewritten and last_used_at is reset.
    """
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_keys import update_key
    if body.raw_key is not None and len(body.raw_key.strip()) < 16:
        raise HTTPException(400, "raw_key too short — looks invalid")
    try:
        updated = update_key(
            key_id,
            label=body.label,
            notes=body.notes,
            enabled=body.enabled,
            raw_key=body.raw_key,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "key not found")
    return updated


@router.delete("/llm/keys/{key_id}")
def llm_keys_delete(key_id: int, request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_keys import delete_key
    if not delete_key(key_id):
        raise HTTPException(404, "key not found")
    return {"id": key_id, "deleted": True}


@router.post("/llm/keys/{key_id}/test")
def llm_keys_test(key_id: int, request: Request):
    """Ping OpenRouter w/ the decrypted key for this row. Returns {ok, status, model_count}."""
    user = _get_user(request)
    _require_super(user)
    from sqlalchemy import text
    from db.session import get_write_engine
    from dash.admin.llm_keys import _decrypt
    import httpx
    with get_write_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT encrypted_key FROM dash.dash_llm_keys WHERE id=:i"
        ), {"i": key_id}).first()
    if not row:
        raise HTTPException(404, "key not found")
    try:
        raw = _decrypt(row[0])
    except Exception as e:
        return {"ok": False, "error": f"decrypt failed: {e}"}
    try:
        r = httpx.get("https://openrouter.ai/api/v1/models",
                      headers={"Authorization": f"Bearer {raw}"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {"ok": True, "status": 200, "model_count": len(data.get("data", []))}
        return {"ok": False, "status": r.status_code, "error": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.post("/llm/pool/refresh")
def llm_pool_refresh(request: Request):
    """Force-refresh pool from DB (bypass 60s TTL). Useful after add/toggle."""
    user = _get_user(request)
    _require_super(user)
    from dash.llm_client import get_pool
    pool = get_pool()
    pool._refresh_keys(force=True)
    return {"keys": pool.stats(), "has_keys": pool.has_keys()}


# ── Model config (chat/deep/lite/embedding) — backed by dash_admin_settings ─
_LLM_MODEL_KEYS = ["chat_model", "mid_model", "deep_model", "reasoning_model", "ultra_model", "lite_model", "embedding_model", "training_model"]

# Grouped view for the redesigned LLM CONFIG panel: CHAT (FAST/REASON + AUTO) /
# TRAINING / EMBEDDING. FAST=lite_model, REASON=chat_model after the tier
# collapse (see complexity_router._model_for_tier). `tools`/`tasks`/`powers`
# are display-only — what each model actually drives in the pharma counter.
_LLM_GROUPS = [
    {
        "id": "chat", "label": "CHAT", "note": "2 modes + AUTO",
        "modes": [
            {"mode": "FAST",   "key": "lite_model", "glyph": "⚡",
             "hint": "quick lookup · <500ms",
             "tools": ["stock_check", "drug_profile", "substitutes", "find_nearby_stock"]},
            {"mode": "REASON", "key": "chat_model", "glyph": "◆",
             "hint": "thinks step-by-step",
             "tools": ["run_sql", "catalog analytics", "indications", "balance_stock", "search_all"]},
        ],
        "auto": "Router auto-picks FAST vs REASON per question (chat default)",
    },
    {
        "id": "training", "label": "TRAINING", "key": "training_model",
        "hint": "follows CHAT unless overridden",
        "tasks": ["Q&A-gen", "vision OCR", "extraction", "dashboard-gen", "profiling", "enrichment"],
    },
    {
        "id": "embedding", "label": "EMBEDDING", "key": "embedding_model",
        "hint": "vector model — not a chat model",
        "powers": ["pgvector RAG", "semantic search", "KG entity match", "brain memory"],
    },
]

# Per-tier usage map — shown in UI under each model row so operator sees where
# the choice actually fires. Triggers = complexity-router classification cues.
_LLM_USAGE: dict[str, dict] = {
    "chat_model": {
        "triggers": ["Agno team default model (chat agents)", "training_llm_call when task uses CHAT_MODEL constant"],
        "used_by":  ["Leader / Analyst / Engineer / Researcher / Data Scientist", "Q&A generation", "dashboard generation", "vision tasks"],
    },
    "mid_model": {
        "triggers": ["Complexity router: ANALYSIS tier (score 0.34–0.67)", "Cues: compare / vs / trend / why / breakdown / drop / correlat / over time"],
        "used_by":  ["Per-chat model when question is mid-complexity analytical"],
    },
    "deep_model": {
        "triggers": ["Complexity router: AGENTIC tier (score 0.67–0.88)", "Cues: build / plan / step-by-step / forecast / simulate / pipeline / orchestrate / strategy"],
        "used_by":  ["DEEP synthesis (deep_deck stages)", "Auto-evolve instructions", "Knowledge graph entity standardize", "Self-learning researcher", "Meta-learning consolidator", "Skill Refinery drafter", "Deck vision judge (TACL different-model rule)"],
    },
    "reasoning_model": {
        "triggers": ["Complexity router: REASONING tier (score ≥ 0.88, no ULTRA escalation)", "Heaviest multi-step prompts"],
        "used_by":  ["Per-chat model when question is heavy multi-step reasoning"],
    },
    "ultra_model": {
        "triggers": ["Complexity router: ULTRA tier escalation", "Requires `across N datasets` + 2+ agentic verbs"],
        "used_by":  ["Per-chat model on hardest multi-dataset planning questions"],
    },
    "lite_model": {
        "triggers": ["Complexity router: TRIVIAL / LOOKUP tier (score < 0.34)", "Cues: how many / count / list / show / what is + greetings/acks"],
        "used_by":  ["Scoring / routing / extraction / meta-learning tasks", "Complexity router LLM tiebreak", "Scope classifier", "Skill audit", "Follow-up suggestion", "Context loader"],
    },
    "embedding_model": {
        "triggers": ["All vector embedding calls (any text → vector)"],
        "used_by":  ["dash_vectors PgVector index (semantic search)", "Knowledge graph entity matching", "Brain embedding tier", "Skill library similarity", "RAG retrieval"],
    },
    "training_model": {
        "triggers": ["training_llm_call tasks with no explicit model (empty = follow CHAT)"],
        "used_by":  ["Q&A generation", "vision OCR", "fact extraction", "dashboard generation", "table profiling", "persona enrichment"],
    },
}


@router.get("/llm/models")
def llm_models_get(request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.settings import get_setting, REGISTRY
    out = {}
    for k in _LLM_MODEL_KEYS:
        spec = REGISTRY.get(k, {})
        usage = _LLM_USAGE.get(k, {})
        out[k] = {
            "value":    get_setting(k),
            "default":  spec.get("default", ""),
            "env":      spec.get("env"),
            "desc":     spec.get("desc", ""),
            "triggers": usage.get("triggers", []),
            "used_by":  usage.get("used_by", []),
        }
    # training_model with no override follows CHAT — show the effective model
    # (flagged so the UI can render it as inherited rather than a hard value).
    if not out.get("training_model", {}).get("value"):
        from dash.settings import get_chat_model
        out["training_model"]["value"] = get_chat_model()
        out["training_model"]["inherited"] = True
    # Common OpenRouter models for dropdown
    out["catalog"] = {
        "chat": [
            "google/gemini-3-flash-preview",
            "google/gemini-3.1-flash-lite-preview",
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4-mini",
            "openai/gpt-5.4",
            "deepseek/deepseek-v4-pro",
        ],
        "deep": [
            "openai/gpt-5.4-mini",
            "openai/gpt-5.4",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-opus-4.7",
            "deepseek/deepseek-v4-pro",
            "google/gemini-3-flash-preview",
        ],
        "lite": [
            "google/gemini-3.1-flash-lite-preview",
            "google/gemini-3-flash-preview",
            "anthropic/claude-haiku-4.5",
            "openai/gpt-5.4-mini",
        ],
        "embedding": [
            "openai/text-embedding-3-small",
            "openai/text-embedding-3-large",
            "google/gemini-embedding-2-preview",
            "cohere/embed-v4.0",
        ],
    }
    # Grouped view (CHAT FAST/REASON + AUTO / TRAINING / EMBEDDING) — the panel
    # renders this; flat per-key rows above stay for back-compat + Advanced tiers.
    out["groups"] = _LLM_GROUPS
    return out


class _SetModelBody(BaseModel):
    key: str
    value: str


@router.patch("/llm/models")
def llm_models_set(body: _SetModelBody, request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.settings import set_setting, REGISTRY
    if body.key not in _LLM_MODEL_KEYS:
        raise HTTPException(400, f"key must be one of {_LLM_MODEL_KEYS}")
    if not body.value or "/" not in body.value:
        raise HTTPException(400, "value must be a provider/model string (e.g. 'openai/gpt-5.4-mini')")
    ok, err = set_setting(body.key, body.value, scope="global")
    if not ok:
        raise HTTPException(400, err)
    # Invalidate any module-level cached model var in dash.settings (lazy getters will re-read)
    return {"ok": True, "key": body.key, "value": body.value, "note": "live for next LLM call (no restart)"}


# ── OpenRouter model catalog (browse/search) ───────────────────────────

@router.post("/llm/models/sync")
def llm_models_sync(request: Request):
    """Fetch OpenRouter /api/v1/models + upsert into dash_llm_model_catalog."""
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_catalog import sync_catalog
    res = sync_catalog()
    return {"ok": True, "count": int(res.get("count") or 0), "synced_at": res.get("synced_at")}


@router.get("/llm/models/catalog")
def llm_models_catalog(
    request: Request,
    q: str = "",
    provider: str = "",
    min_ctx: int | None = None,
    max_price: float | None = None,
    free_only: bool = False,
    tools_only: bool = False,
    vision_only: bool = False,
    reasoning_only: bool = False,
    sort: str = "popularity",
    limit: int = 50,
    offset: int = 0,
):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_catalog import search_catalog
    return search_catalog(
        q=q or None,
        provider=provider or None,
        min_ctx=min_ctx,
        max_price=max_price,
        free_only=free_only,
        tools_only=tools_only,
        vision_only=vision_only,
        reasoning_only=reasoning_only,
        sort=sort or "popularity",
        limit=limit,
        offset=offset,
    )


@router.get("/llm/models/sync-status")
def llm_models_sync_status(request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_catalog import get_sync_status
    return get_sync_status()


@router.get("/llm/models/catalog/{model_id:path}")
def llm_models_catalog_entry(model_id: str, request: Request):
    user = _get_user(request)
    _require_super(user)
    from dash.admin.llm_catalog import get_catalog_entry
    row = get_catalog_entry(model_id)
    if not row:
        raise HTTPException(404, "model not found in catalog")
    return row


# ───────────────────────────────────────────────────────────────────────────
# Scope guardrail auto-derive (manual trigger; daemon runs nightly via cron)
# ───────────────────────────────────────────────────────────────────────────

@router.get("/scope-derive/status")
def scope_derive_status(request: Request):
    """List projects + scope coverage status. Super-admin only."""
    user = _get_user(request)
    _require_super(user)
    try:
        from db.session import get_sql_engine
        from sqlalchemy import text as _t
        eng = get_sql_engine()
        with eng.connect() as c:
            rows = c.execute(_t("""
                SELECT slug,
                  COALESCE(jsonb_array_length(feature_config->'scope'->'topics'), 0) as topics,
                  COALESCE(jsonb_array_length(feature_config->'scope'->'denied_intents'), 0) as denied,
                  (feature_config->'scope'->>'_auto') as auto_flag
                FROM public.dash_projects
                ORDER BY slug
            """)).fetchall()
        projects = [
            {
                "slug": r[0],
                "topics_count": int(r[1] or 0),
                "denied_count": int(r[2] or 0),
                "auto": r[3],
                "covered": int(r[1] or 0) > 0,
            }
            for r in rows
        ]
        return {
            "total": len(projects),
            "covered": sum(1 for p in projects if p["covered"]),
            "missing": sum(1 for p in projects if not p["covered"]),
            "projects": projects,
        }
    except Exception as e:
        raise HTTPException(500, f"status failed: {e}")


@router.post("/scope-derive/run-now")
def scope_derive_run_now(request: Request):
    """Trigger one-shot scope-derive sweep across all projects missing scope.

    Returns {checked, derived, failed, results: [...]}. Super-admin only.
    """
    user = _get_user(request)
    _require_super(user)
    try:
        from dash.cron.scope_derive_daemon import run_cycle
        return run_cycle()
    except Exception as e:
        raise HTTPException(500, f"scope-derive cycle failed: {e}")
