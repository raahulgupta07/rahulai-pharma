"""Admin settings — read/write runtime config from dash_admin_settings.

Resolution order:
  1. Project-scoped DB row (project_slug match)
  2. Global DB row (scope='global', project_slug NULL)
  3. Env var (legacy fallback)
  4. Hard-coded default

Settings are typed: bool, int, float, string, json, cron, enum.
Value type drives validation + serialization.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# In-process cache (TTL 30s) to avoid hot-path DB reads
_CACHE: dict[tuple[str, Optional[str]], tuple[float, Any]] = {}
_CACHE_TTL_S = 30
_lock = threading.Lock()


# ── Setting registry ────────────────────────────────────────────────────────
# Each entry: {key: (value_type, default, env_var, description, scope_allowed)}
# scope_allowed: 'global', 'project', 'both'

REGISTRY: dict[str, dict] = {
    # Self-learning
    "enable_self_learning":          {"type": "bool", "default": True,  "env": "ENABLE_SELF_LEARNING", "scope": "both", "desc": "Enable autonomous self-learning daily cycle"},
    "daily_cron_time":                {"type": "cron", "default": "0 4 * * *", "env": None, "scope": "global", "desc": "Cron expr for daily learning cycle"},
    "enable_sunday_canary":           {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Run dry-run canary every Sunday"},
    "enable_daily_decay":             {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Run forgetting curve decay daily"},
    "max_questions_per_cycle":        {"type": "int",  "default": 20,    "env": None, "scope": "both",   "desc": "Curiosity questions generated per cycle"},
    "per_question_timeout_s":         {"type": "int",  "default": 120,   "env": None, "scope": "global", "desc": "Wall-clock cap per question (seconds)"},
    "daily_cost_cap_default_usd":     {"type": "float","default": 1.0,   "env": None, "scope": "both",   "desc": "Default daily LLM cost cap per project (USD)"},
    "enable_promotion_to_central":    {"type": "bool", "default": True,  "env": None, "scope": "both",   "desc": "Promote verified hypotheses to central pool"},
    "enable_dry_run":                 {"type": "bool", "default": False, "env": None, "scope": "global", "desc": "Force dry-run mode (no LLM calls)"},

    # Providers / research tiers
    "enable_tavily":                  {"type": "bool", "default": True,  "env": "TAVILY_API_KEY",       "scope": "global", "desc": "Tavily web search (auto-disabled if no key)"},
    "enable_brave_search":            {"type": "bool", "default": True,  "env": "BRAVE_API_KEY",         "scope": "global", "desc": "Brave search"},
    "enable_perplexity":              {"type": "bool", "default": True,  "env": "PERPLEXITY_API_KEY",    "scope": "global", "desc": "Perplexity search"},
    "enable_fred":                    {"type": "bool", "default": True,  "env": "FRED_API_KEY",          "scope": "global", "desc": "FRED economic data"},
    "enable_wikipedia":               {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Wikipedia lookups"},
    "enable_world_bank":              {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "World Bank indicators"},
    "enable_alpha_vantage":           {"type": "bool", "default": True,  "env": "ALPHA_VANTAGE_API_KEY", "scope": "global", "desc": "Alpha Vantage market data"},
    "enable_wikidata":                {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Wikidata SPARQL"},
    "enable_web_fetch":               {"type": "bool", "default": True,  "env": None, "scope": "both",   "desc": "Researcher web fetch tool"},

    # Column classifier
    "auto_classify_on_train":         {"type": "bool", "default": True,  "env": None, "scope": "both",   "desc": "Run classifier after each training run"},
    "pii_default_action":             {"type": "enum", "default": "flag","env": None, "scope": "both",   "desc": "PII action: flag / mask / block",
                                        "choices": ["flag", "mask", "block"]},
    "enable_llm_typing":              {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Use LLM tier in classifier (cost control)"},
    "enable_embedding_matcher":       {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Use Brain embedding tier"},

    # Domain detection
    "auto_detect_domain":             {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Auto-detect domain after training"},
    "auto_load_seeds":                {"type": "bool", "default": True,  "env": None, "scope": "both",   "desc": "Auto-load matching brain seeds"},

    # Backup
    "enable_backup":                  {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Daily backup cron"},
    "backup_retention_days":          {"type": "int",  "default": 30,    "env": "BACKUP_RETENTION_DAILY","scope": "global", "desc": "Days to retain backups"},

    # Federation
    "enable_federation_join":         {"type": "bool",  "default": True,    "env": None, "scope": "both",   "desc": "Allow cross-source JOINs (intra-project only)"},
    "max_cross_source_rows":          {"type": "int",   "default": 50000,   "env": None, "scope": "both",   "desc": "Max rows in federated query result"},
    "federation_timeout_s":           {"type": "int",   "default": 60,      "env": None, "scope": "global", "desc": "Timeout per federated query (seconds)"},
    "federation_default_engine":      {"type": "enum",  "default": "duckdb", "env": None, "scope": "global", "desc": "Merge engine: duckdb (faster) or pandas (fallback)",
                                        "choices": ["duckdb", "pandas"]},

    # Notifications
    "enable_digest_slack":            {"type": "bool", "default": False, "env": "SLACK_LEARNING_WEBHOOK","scope": "both",   "desc": "Send digest to Slack"},
    "slack_webhook_url":              {"type": "string","default": "",   "env": "SLACK_LEARNING_WEBHOOK","scope": "global", "desc": "Slack webhook URL"},
    "enable_email_digest":            {"type": "bool", "default": False, "env": None, "scope": "both",   "desc": "Email digest to project owners"},

    # Security
    "rate_limit_per_minute":          {"type": "int",  "default": 500,   "env": "RATE_LIMIT",            "scope": "global", "desc": "Per-IP rate limit"},
    "agno_debug":                     {"type": "bool", "default": False, "env": "AGNO_DEBUG",            "scope": "global", "desc": "Agno debug logging"},

    # Scheduler
    "enable_in_process_scheduler":    {"type": "bool", "default": True,  "env": "LEARNING_SCHEDULER_DISABLED", "scope": "global", "desc": "Run scheduler inside API pod"},
    "enable_k8s_cronjob_mode":        {"type": "bool", "default": False, "env": None, "scope": "global", "desc": "Use external K8S CronJob (disables in-process)"},

    # LLM models (UI-editable via Command Center → LLM config)
    "chat_model":                     {"type": "string", "default": "google/gemini-3-flash-preview",        "env": "CHAT_MODEL",      "scope": "global", "desc": "Chat agents, SQL, vision, Q&A, dashboard"},
    "deep_model":                     {"type": "string", "default": "openai/gpt-5.4-mini",                  "env": "DEEP_MODEL",      "scope": "global", "desc": "Deep analysis, relationships, domain knowledge"},
    "lite_model":                     {"type": "string", "default": "google/gemini-3.1-flash-lite-preview", "env": "LITE_MODEL",      "scope": "global", "desc": "Scoring, routing, extraction, meta-learning"},
    "embedding_model":                {"type": "string", "default": "openai/text-embedding-3-small",        "env": "EMBEDDING_MODEL", "scope": "global", "desc": "Vector embeddings"},
    "training_model":                 {"type": "string", "default": "google/gemini-3-flash-preview",        "env": "TRAINING_MODEL",  "scope": "global", "desc": "Training pipeline: Q&A-gen, vision OCR, extraction, dashboard-gen (empty = follow CHAT)"},
    "mid_model":                      {"type": "string", "default": "google/gemini-3-flash-preview",        "env": "MID_MODEL",       "scope": "global", "desc": "Complexity-router ANALYSIS tier (compare/trend/breakdown questions)"},
    "reasoning_model":                {"type": "string", "default": "openai/gpt-5.4-mini",                  "env": "REASONING_MODEL", "scope": "global", "desc": "Complexity-router REASONING tier (heavy multi-step questions)"},
    "ultra_model":                    {"type": "string", "default": "openai/gpt-5.4-mini",                  "env": "ULTRA_MODEL",     "scope": "global", "desc": "Complexity-router ULTRA tier (hardest multi-dataset planning)"},

    # Integrations kill switches (super-admin) — when off, the surface vanishes
    # from the top-nav Integrations dropdown AND its API routes return 403.
    "gateway_enabled":                {"type": "bool", "default": False, "env": None, "scope": "global", "desc": "Enable the API Gateway (/api/v1). Default OFF — fresh installs don't expose the external API until a super-admin turns it on. Off → routes 403 + nav item hidden"},
    "embed_enabled":                  {"type": "bool", "default": True,  "env": None, "scope": "global", "desc": "Enable Embed widgets (/api/embed). Off → routes 403 + nav item hidden"},

    # Single-point brand theme — default widget appearance (JSON string). Widgets
    # with no per-store override inherit this at render time (embed ?? brand ?? hard).
    "embed_brand":                    {"type": "string", "default": "", "env": None, "scope": "global", "desc": "Default embed widget appearance JSON (single-point brand theme)"},
    # Default auth mode for newly auto-provisioned outlet widgets.
    "embed_default_auth_mode":        {"type": "enum", "default": "public", "env": None, "scope": "global", "choices": ["public", "hmac", "jwt"], "desc": "Default auth_mode for auto-provisioned outlet widgets (public = drop-in key only · hmac = server-signed · jwt = app identity)"},
    # Default Burmese opening greeting shown when a widget opens (per-widget
    # welcome_msg / brand override take precedence; this is the global fallback).
    "embed_default_welcome":          {"type": "string", "default": "မင်္ဂလာပါ — ဘာများ ကူညီပေးရမလဲ?", "env": None, "scope": "global", "desc": "Default embed widget opening greeting (Burmese). Resolution: per-widget welcome_msg ?? brand ?? this ?? hard fallback"},
    # Default Burmese starter (initial suggestion) question chips for the widget.
    # JSON list — round-trips through get_setting()/_coerce (list default returned as-is).
    "embed_default_starters":         {"type": "json", "default": ["ဒီဆေး လက်ကျန် ရှိလား?", "အစားထိုး ဆေးတွေ ပြပါ", "အနီးဆုံးဆိုင်မှာ ရှိလား?"], "env": None, "scope": "global", "desc": "Default embed widget starter-question chips (Burmese pharma). Per-widget starter_questions override this."},

    # ── Operational runtime knobs (super-admin · live, no restart) ──────────
    # Mirror the legacy env vars (kept commented in .env for documentation). DB
    # override ► env ► default. Defaults below = the current prod-effective value
    # so commenting the env line is a no-op. Edited from Command Center →
    # Admin settings → System settings.
    "llm_parallel_cap_chat":          {"type": "int",  "default": 20,    "env": "LLM_PARALLEL_CAP_CHAT",   "scope": "global", "desc": "Max concurrent LLM calls (chat+embed shared semaphore). Raising past pool headroom queues, never errors. Live — semaphore rebuilds on change."},
    # NOTE: API Gateway rate cap is NOT here — it has its own live UI control
    # (public.dash_apigw_config via the Gateway panel). Don't duplicate it.
    "apigw_cache_ttl":                {"type": "int",  "default": 90,    "env": "APIGW_CACHE_TTL",         "scope": "global", "desc": "Gateway answer cache TTL (seconds). 0 = disabled (dev). 90 = prod (hides 70-220s repeat latency)"},
    "metric_shortcut_disabled":       {"type": "bool", "default": False, "env": "METRIC_SHORTCUT_DISABLED", "scope": "global", "desc": "Disable the fast metric shortcut path. True = always run full agent (dev/eval). False = use shortcut (prod speed)"},
    "reasoning_floor":                {"type": "bool", "default": False, "env": "REASONING_FLOOR",         "scope": "global", "desc": "Force a minimum reasoning tier. Off = pharmacy-counter lookups skip reasoning (faster). On = always reason"},
    "autonomy_t3_actions":            {"type": "bool", "default": False, "env": "AUTONOMY_T3_ACTIONS",     "scope": "global", "desc": "Autonomy heartbeat real actions. Off = detect-only journal (safe). On = data/schema change auto-enqueues a retrain"},
    "catalog_enrich":                 {"type": "bool", "default": False, "env": "CATALOG_ENRICH",          "scope": "global", "desc": "LLM catalog gap-fill + low-risk auto-apply during training (cost)"},
    "catalog_enrich_limit":           {"type": "int",  "default": 200,   "env": "CATALOG_ENRICH_LIMIT",    "scope": "global", "desc": "Max articles enriched per training run (cost control)"},
    "engineer_semantic_layer":        {"type": "bool", "default": True,  "env": "ENGINEER_SEMANTIC_LAYER", "scope": "global", "desc": "Engineer agent builds the materialized-view semantic layer during training"},
    "embed_log_bodies":               {"type": "bool", "default": True,  "env": "EMBED_LOG_BODIES",        "scope": "global", "desc": "Log embed widget Q&A bodies for Monitoring QUESTION/ANSWER panels"},
    "embed_log_input":                {"type": "bool", "default": True,  "env": "EMBED_LOG_INPUT",         "scope": "global", "desc": "Log embedding-API input text (usage panels)"},
    "apigw_log_bodies":               {"type": "bool", "default": True,  "env": "APIGW_LOG_BODIES",        "scope": "global", "desc": "Log API Gateway request/response bodies"},

    # RBAC — role → surface visibility matrix (super-admin editable). Super admin
    # is ALWAYS full (not stored here). Surfaces: admin_console / dashboard / chat.
    # Enforced both in nav (hide) and backend (403). See app/auth.py:surfaces_for.
    "rbac_surface_access":            {"type": "json", "default": {
        "admin": {"dashboard": True, "chat": True, "workspace": True,  "integration": True,  "admin_console": False, "users_access": True,  "usage_cost": True},
        "user":  {"dashboard": True, "chat": True, "workspace": False, "integration": False, "admin_console": False, "users_access": False, "usage_cost": False},
    }, "env": None, "scope": "global", "desc": "Role→surface access matrix. Roles: admin, user (super always full). Surfaces: dashboard, chat, workspace, integration, admin_console, users_access, usage_cost"},
}


# ── Type validation + coercion ──────────────────────────────────────────────

def _coerce(value: Any, value_type: str) -> Any:
    """Coerce a string value to its typed form."""
    if isinstance(value, str):
        v = value
    else:
        v = str(value)

    if value_type == "bool":
        return str(v).strip().lower() in ("1", "true", "yes", "on", "y", "t")
    if value_type == "int":
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0
    if value_type == "float":
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0
    if value_type == "json":
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(v)
        except Exception:
            return None
    # 'string', 'cron', 'enum' — return as-is
    return v


def _serialize(value: Any, value_type: str) -> str:
    """Serialize typed value to string for DB storage."""
    if value_type == "bool":
        return "true" if value else "false"
    if value_type == "json":
        return json.dumps(value)
    return str(value)


def _validate(key: str, value: Any) -> tuple[bool, str]:
    """Validate value against registry. Returns (ok, error_msg)."""
    spec = REGISTRY.get(key)
    if spec is None:
        return False, f"unknown setting: {key}"

    vtype = spec["type"]
    if vtype == "enum":
        choices = spec.get("choices", [])
        if value not in choices:
            return False, f"value must be one of {choices}"
    elif vtype == "cron":
        # Basic cron validation (5 fields)
        parts = str(value).strip().split()
        if len(parts) != 5:
            return False, "cron expression must have 5 fields"
    elif vtype == "int":
        try:
            int(value)
        except (ValueError, TypeError):
            return False, "must be int"
    elif vtype == "float":
        try:
            float(value)
        except (ValueError, TypeError):
            return False, "must be float"

    return True, ""


# ── Get / set ───────────────────────────────────────────────────────────────

def get_setting(key: str, project_slug: Optional[str] = None,
                 default: Any = None) -> Any:
    """Resolve setting in order: project > global > env > registry default.

    Cached in-process for 30s.
    """
    cache_key = (key, project_slug)
    now = time.time()

    with _lock:
        cached = _CACHE.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL_S:
            return cached[1]

    spec = REGISTRY.get(key, {})
    vtype = spec.get("type", "string")

    # 1. Project scope
    value = None
    if project_slug:
        value = _read_db(key, "project", project_slug)

    # 2. Global scope
    if value is None:
        value = _read_db(key, "global", None)

    # 3. Env var
    if value is None and spec.get("env"):
        env_v = os.environ.get(spec["env"])
        if env_v is not None:
            value = env_v

    # 4. Registry default
    if value is None:
        value = spec.get("default", default)

    # Coerce
    if value is not None:
        value = _coerce(value, vtype)

    with _lock:
        _CACHE[cache_key] = (now, value)
    return value


def set_setting(key: str, value: Any, *, scope: str = "global",
                 project_slug: Optional[str] = None,
                 user_id: Optional[int] = None) -> tuple[bool, str]:
    """Validate + persist. Invalidates cache."""
    if scope not in ("global", "project"):
        return False, f"invalid scope: {scope}"
    if scope == "project" and not project_slug:
        return False, "project_slug required when scope='project'"

    spec = REGISTRY.get(key)
    if spec is None:
        return False, f"unknown setting: {key}"

    if scope == "project" and spec.get("scope") not in ("project", "both"):
        return False, f"setting '{key}' is global-only"
    if scope == "global" and spec.get("scope") not in ("global", "both"):
        return False, f"setting '{key}' is project-only"

    ok, err = _validate(key, value)
    if not ok:
        return False, err

    serialized = _serialize(value, spec["type"])

    try:
        from sqlalchemy import text
        # public.dash_* writes MUST use get_write_engine (CLAUDE.md rule)
        try:
            from db.session import get_write_engine
            eng = get_write_engine()
        except Exception:
            from db.session import get_sql_engine
            eng = get_sql_engine()
        params = {
            "k": key, "v": serialized, "vt": spec["type"],
            "s": scope, "p": project_slug,
            "d": spec.get("desc", ""), "u": user_id,
        }
        with eng.begin() as conn:
            if project_slug is None:
                # GLOBAL rows store project_slug = NULL. A unique index on
                # (key, scope, project_slug) does NOT dedup NULLs (NULL != NULL),
                # so ON CONFLICT would silently insert duplicates. Do a manual
                # UPDATE-then-INSERT against the IS NULL row instead.
                res = conn.execute(text(
                    "UPDATE public.dash_admin_settings SET "
                    " value = :v, value_type = :vt, updated_by = :u, updated_at = NOW() "
                    "WHERE key = :k AND scope = :s AND project_slug IS NULL"
                ), params)
                if (res.rowcount or 0) == 0:
                    conn.execute(text(
                        "INSERT INTO public.dash_admin_settings "
                        "(key, value, value_type, scope, project_slug, description, updated_by) "
                        "VALUES (:k, :v, :vt, :s, NULL, :d, :u)"
                    ), params)
            else:
                conn.execute(text(
                    "INSERT INTO public.dash_admin_settings "
                    "(key, value, value_type, scope, project_slug, description, updated_by) "
                    "VALUES (:k, :v, :vt, :s, :p, :d, :u) "
                    "ON CONFLICT (key, scope, project_slug) DO UPDATE SET "
                    " value = EXCLUDED.value, "
                    " updated_by = EXCLUDED.updated_by, "
                    " updated_at = NOW()"
                ), params)
    except Exception as e:
        logger.warning(f"set_setting failed: {e}")
        return False, str(e)[:200]

    # Invalidate cache for this key (both scopes)
    with _lock:
        _CACHE.pop((key, project_slug), None)
        _CACHE.pop((key, None), None)
    return True, ""


def list_settings(scope: Optional[str] = None,
                   project_slug: Optional[str] = None) -> list[dict]:
    """Return all settings w/ effective values + DB overrides."""
    out = []
    for key, spec in REGISTRY.items():
        effective = get_setting(key, project_slug=project_slug)
        out.append({
            "key": key,
            "type": spec["type"],
            "default": spec.get("default"),
            "description": spec.get("desc", ""),
            "scope_allowed": spec.get("scope", "global"),
            "choices": spec.get("choices"),
            "env_var": spec.get("env"),
            "effective_value": effective,
        })
    return out


def reset_setting(key: str, *, scope: str = "global",
                   project_slug: Optional[str] = None) -> bool:
    """Delete a DB override, falling back to env/default."""
    try:
        from sqlalchemy import text
        # public.dash_* writes MUST use get_write_engine (CLAUDE.md rule) — the
        # read engine routes through pgbouncer read paths and the DELETE never lands.
        try:
            from db.session import get_write_engine
            eng = get_write_engine()
        except Exception:
            from db.session import get_sql_engine
            eng = get_sql_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "DELETE FROM public.dash_admin_settings "
                "WHERE key = :k AND scope = :s AND "
                " (project_slug = :p OR (project_slug IS NULL AND :p IS NULL))"
            ), {"k": key, "s": scope, "p": project_slug})
        with _lock:
            _CACHE.pop((key, project_slug), None)
            _CACHE.pop((key, None), None)
        return True
    except Exception as e:
        logger.warning(f"reset_setting failed: {e}")
        return False


def integrations_enabled() -> dict:
    """Return the kill-switch state for the integration surfaces.

    {"gateway": bool, "embed": bool} — reads the global `gateway_enabled` /
    `embed_enabled` settings. Fail-open to True on any error (missing row → the
    registry default is True anyway; DB hiccup → don't lock operators out).
    """
    # Read the DB DIRECTLY (not via the 30s get_setting cache) so /api/flags is
    # always fresh — a super-admin who flips the toggle + reloads must see the
    # nav update immediately, not after a cache window.
    def _on(key: str) -> bool:
        raw = _read_db(key, "global", None)   # None when no row → registry default
        if raw is None:
            return bool(REGISTRY.get(key, {}).get("default", True))
        return _coerce(raw, "bool") is True
    try:
        return {"gateway": _on("gateway_enabled"), "embed": _on("embed_enabled")}
    except Exception:
        return {"gateway": True, "embed": True}


def _read_db(key: str, scope: str, project_slug: Optional[str]) -> Optional[str]:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            # ORDER BY updated_at DESC LIMIT 1 → latest wins (tolerate legacy dup rows)
            row = conn.execute(text(
                "SELECT value FROM public.dash_admin_settings "
                "WHERE key = :k AND scope = :s AND "
                " (project_slug = :p OR (project_slug IS NULL AND :p IS NULL)) "
                "ORDER BY updated_at DESC LIMIT 1"
            ), {"k": key, "s": scope, "p": project_slug}).fetchone()
        if not row:
            return None
        v = row[0]
        # Defensive: only accept primitive scalars from DB (not Mock objects)
        if isinstance(v, (str, int, float, bool)):
            return str(v)
        return None
    except Exception:
        return None
