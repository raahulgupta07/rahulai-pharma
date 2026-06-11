"""
Shared settings for Dash agents.

Centralizes the database, model, knowledge bases, and learning config
so all agents share the same resources.
"""

from os import getenv

# Silence Agno/OpenTelemetry cross-thread context detach errors (cosmetic only —
# they're benign warnings from attach/detach happening on different threads, but
# they spam logs ~10x per chat). OTEL_SDK_DISABLED disables exports but the
# Context API still emits these. Just mute the noisy logger.
import logging as _otel_logging
_otel_logging.getLogger("opentelemetry.context").setLevel(_otel_logging.CRITICAL)

# ── LLM concurrency control ────────────────────────────────────────────────
# Prevents OpenRouter rate-limit failures when many parallel chat / training
# requests overwhelm the upstream provider. Three knobs:
#   * Per-tier asyncio.Semaphore caps simultaneous in-flight requests.
#   * Dedicated ThreadPoolExecutor offloads the blocking httpx.post() call
#     so the event loop stays free while we hold the semaphore.
#   * Semaphores are lazy-initialized via _get_sem() to avoid binding to the
#     wrong event loop at import time (each loop needs its own semaphore).
import asyncio as _asyncio
import concurrent.futures as _futures
import threading as _llm_threading
import time as _llm_time
import logging as _llm_logging

_LLM_LOG = _llm_logging.getLogger(__name__)

LLM_PARALLEL_CAP = int(getenv("LLM_PARALLEL_CAP") or "5")
LLM_PARALLEL_CAP_CHAT = int(getenv("LLM_PARALLEL_CAP_CHAT") or "10")
LLM_PARALLEL_CAP_DEEP = int(getenv("LLM_PARALLEL_CAP_DEEP") or "3")
LLM_PARALLEL_CAP_LITE = int(getenv("LLM_PARALLEL_CAP_LITE") or "20")

# Pool size = sum of all 3 tier caps + 5 buffer. The chat cap is now live-tunable
# from the UI (admin setting llm_parallel_cap_chat), so size the pool with headroom
# (>= 50 chat threads) — the pool is import-time fixed and can't grow, so this lets
# the UI raise the chat cap up to ~50 without starving the executor.
_CHAT_POOL_HEADROOM = max(LLM_PARALLEL_CAP_CHAT, 50)
_DEFAULT_POOL_SIZE = _CHAT_POOL_HEADROOM + LLM_PARALLEL_CAP_DEEP + LLM_PARALLEL_CAP_LITE + 5
LLM_POOL_SIZE = int(getenv("LLM_POOL_SIZE") or str(_DEFAULT_POOL_SIZE))

_LLM_POOL = _futures.ThreadPoolExecutor(
    max_workers=LLM_POOL_SIZE,
    thread_name_prefix="llm-call",
)

# Per-(loop, tier) semaphore cache. Keyed by id(loop) so each event loop gets
# its own semaphore (asyncio.Semaphore is loop-bound and unsafe to share).
_LLM_SEM_CACHE: dict = {}
_LLM_SEM_LOCK = _llm_threading.Lock()


def _tier_for_task(task: str) -> str:
    """Map a TRAINING_CONFIGS task name to a concurrency tier.

    Tiers:
      * "deep" — heavy reasoning tasks (deep_analysis, relationships, evolve, ...)
      * "lite" — cheap/fast tasks (scoring, routing, extraction, meta_learning, ...)
      * "chat" — everything else (qa_generation, dashboard_gen, synthesis, ...)
    """
    cfg = TRAINING_CONFIGS.get(task) if "TRAINING_CONFIGS" in globals() else None
    if cfg:
        model = (cfg.get("model") or "") if isinstance(cfg, dict) else ""
        # Tier picked by per-task model (set in TRAINING_CONFIGS) — falls through
        # to "chat" when the task uses the default TRAINING_MODEL.
        if model and model == DEEP_MODEL:
            return "deep"
        if model and model == LITE_MODEL:
            return "lite"
    return "chat"


def _chat_cap_live() -> int:
    """Chat-tier cap — live from admin setting (DB ► env ► default), capped at
    the pool headroom so a UI bump can never exceed the executor size."""
    try:
        from dash.admin.settings import get_setting
        v = int(get_setting("llm_parallel_cap_chat") or LLM_PARALLEL_CAP_CHAT)
        if v < 1:
            v = 1
        return min(v, _CHAT_POOL_HEADROOM)
    except Exception:
        return LLM_PARALLEL_CAP_CHAT


def _cap_for_tier(tier: str) -> int:
    if tier == "deep":
        return LLM_PARALLEL_CAP_DEEP
    if tier == "lite":
        return LLM_PARALLEL_CAP_LITE
    if tier == "chat":
        return _chat_cap_live()
    return LLM_PARALLEL_CAP


def _get_sem(task: str) -> "_asyncio.Semaphore":
    """Lazy-init and return the asyncio.Semaphore for the task's tier.

    MUST be called from inside an async context (needs a running event loop).
    Each event loop gets its own semaphore — sharing across loops is unsafe.
    """
    loop = _asyncio.get_event_loop()
    tier = _tier_for_task(task)
    cap = _cap_for_tier(tier)
    # Cap is part of the cache key: when a super-admin changes llm_parallel_cap_chat
    # the new value yields a new key → a fresh Semaphore at the new cap, no restart.
    # (Old semaphores for the prior cap simply go unused and are GC'd.)
    key = (id(loop), tier, cap)
    with _LLM_SEM_LOCK:
        sem = _LLM_SEM_CACHE.get(key)
        if sem is None:
            sem = _asyncio.Semaphore(cap)
            _LLM_SEM_CACHE[key] = sem
    return sem


async def training_llm_call_async(
    prompt: str, task: str = "extraction", model: str | None = None
) -> str | None:
    """Async wrapper around the sync `training_llm_call`.

    Acquires a per-tier semaphore (cap from LLM_PARALLEL_CAP_*), then offloads
    the blocking httpx call to the dedicated _LLM_POOL ThreadPoolExecutor.
    Logs an INFO line when queue wait exceeds 500ms so ops can tune the cap.

    Existing sync callers are unaffected — this is opt-in for async paths only
    (e.g. embed chat endpoint, parallel training loops).
    """
    sem = _get_sem(task)
    t_wait_start = _llm_time.monotonic()
    async with sem:
        wait_ms = (_llm_time.monotonic() - t_wait_start) * 1000.0
        if wait_ms > 500.0:
            tier = _tier_for_task(task)
            cap = _cap_for_tier(tier)
            _LLM_LOG.info(
                "training_llm_call_async queue wait %.0fms (task=%s tier=%s cap=%d)",
                wait_ms, task, tier, cap,
            )
        loop = _asyncio.get_event_loop()
        return await loop.run_in_executor(
            _LLM_POOL, training_llm_call, prompt, task, model
        )


async def training_vision_call_async(
    prompt: str, images: list, task: str = "vision"
) -> str | None:
    """Async wrapper around the sync `training_vision_call`. Same semaphore +
    pool offload pattern as `training_llm_call_async`."""
    sem = _get_sem(task)
    t_wait_start = _llm_time.monotonic()
    async with sem:
        wait_ms = (_llm_time.monotonic() - t_wait_start) * 1000.0
        if wait_ms > 500.0:
            tier = _tier_for_task(task)
            cap = _cap_for_tier(tier)
            _LLM_LOG.info(
                "training_vision_call_async queue wait %.0fms (task=%s tier=%s cap=%d)",
                wait_ms, task, tier, cap,
            )
        loop = _asyncio.get_event_loop()
        return await loop.run_in_executor(
            _LLM_POOL, training_vision_call, prompt, images, task
        )

from agno.learn import LearnedKnowledgeConfig, LearningMachine, LearningMode
from agno.models.openrouter import OpenRouter, OpenRouterResponses

from db import create_knowledge, get_postgres_db


# ── Monkey-patch: sanitize tool schemas at runtime ─────────────────────────
# Google Gemini's strict tool-schema validator rejects function declarations
# where `required[N]` references a property name not in `properties`. Agno's
# `Function.to_dict` inherits Pydantic JSON Schema generation that occasionally
# emits stale required[] entries (notably from *args/**kwargs in decorated
# callables — Agno introspects signature, marks them required, but never adds
# them to properties). The error:
#   `required fields ['args', 'kwargs'] are not defined in the schema properties`
# To stop the entire fleet from dying, wrap to_dict so every emitted schema is
# pruned to `required ⊆ properties.keys()` at every LLM call (idempotent).
try:
    from agno.tools.function import Function as _AgnoFunction
    if not getattr(_AgnoFunction, "_dash_to_dict_patched", False):
        _orig_to_dict = _AgnoFunction.to_dict

        def _prune_required(schema):
            if not isinstance(schema, dict):
                return schema
            props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            req = schema.get("required")
            if isinstance(req, list) and props:
                valid = [r for r in req if isinstance(r, str) and r in props]
                if valid != req:
                    schema["required"] = valid
            elif isinstance(req, list) and not props:
                schema["required"] = []
            for v in props.values():
                if isinstance(v, dict):
                    _prune_required(v)
                    if isinstance(v.get("items"), dict):
                        _prune_required(v["items"])
            return schema

        def _patched_to_dict(self, *args, **kwargs):
            data = _orig_to_dict(self, *args, **kwargs)
            try:
                params = data.get("parameters") if isinstance(data, dict) else None
                if isinstance(params, dict):
                    _prune_required(params)
            except Exception:
                pass
            return data

        _AgnoFunction.to_dict = _patched_to_dict
        _AgnoFunction._dash_to_dict_patched = True
        import logging as _log
        _log.getLogger(__name__).info("Patched agno Function.to_dict for Gemini strict-schema compat")
except Exception as _patch_err:
    import logging as _log
    _log.getLogger(__name__).warning(f"Failed to patch Function.to_dict: {_patch_err}")

# Database
agent_db = get_postgres_db()

# ═══ 3-MODEL ARCHITECTURE ═══
# Gemini 3 Flash  — chat + SQL + vision + Q&A (workhorse: best SQL, fastest TTFT, best vision)
# GPT-5.4 Mini    — deep analysis + relationships + domain knowledge + auto-evolve (deep thinker)
# Flash Lite      — scoring + routing + bulk extraction (router: cheapest, fastest output)

# Boot-time defaults from env. May be overridden live via dash_admin_settings
# (Command Center → LLM config). Read via get_chat_model()/get_deep_model()/
# get_lite_model() for runtime resolution (DB-first → env fallback → default).
_DEFAULT_CHAT = "google/gemini-3-flash-preview"
_DEFAULT_DEEP = "openai/gpt-5.4-mini"
_DEFAULT_LITE = "google/gemini-3-flash-preview"  # Burmese+English agent: 3.1-flash-lite is weak on Burmese, so FAST tier uses 3-flash too
_DEFAULT_EMBED = "openai/text-embedding-3-small"

CHAT_MODEL = getenv("CHAT_MODEL") or _DEFAULT_CHAT
TRAINING_MODEL = getenv("TRAINING_MODEL") or CHAT_MODEL
DEEP_MODEL = getenv("DEEP_MODEL") or _DEFAULT_DEEP
LITE_MODEL = getenv("LITE_MODEL") or _DEFAULT_LITE


def _model_from_settings(key: str, fallback: str) -> str:
    """Read model name from dash_admin_settings (5s TTL cached), fallback to module constant."""
    try:
        from dash.admin.settings import get_setting
        v = get_setting(key)
        if v and isinstance(v, str) and "/" in v:
            return v
    except Exception:
        pass
    return fallback


def get_chat_model() -> str:
    return _model_from_settings("chat_model", CHAT_MODEL)


def get_deep_model() -> str:
    return _model_from_settings("deep_model", DEEP_MODEL)


def get_lite_model() -> str:
    return _model_from_settings("lite_model", LITE_MODEL)


def get_embedding_model() -> str:
    return _model_from_settings("embedding_model", getenv("EMBEDDING_MODEL") or _DEFAULT_EMBED)


def get_training_model() -> str:
    # DB-editable training_model → env TRAINING_MODEL → fall back to CHAT model.
    return _model_from_settings("training_model", "") or get_chat_model()


def get_mid_model() -> str:
    return _model_from_settings("mid_model", MID_MODEL)


def get_reasoning_model() -> str:
    return _model_from_settings("reasoning_model", REASONING_MODEL)


def get_ultra_model() -> str:
    return _model_from_settings("ultra_model", ULTRA_MODEL)
# Mid tier — used by the complexity router's ANALYSIS tier only. Defaults to
# CHAT_MODEL so behavior is unchanged unless MID_MODEL is set in the env.
MID_MODEL = getenv("MID_MODEL") or CHAT_MODEL
# Reasoning tier — heaviest multi-step questions. Defaults to DEEP_MODEL.
REASONING_MODEL = getenv("REASONING_MODEL") or DEEP_MODEL
# Ultra tier — hardest multi-dataset planning. Flagship model. Defaults to REASONING.
ULTRA_MODEL = getenv("ULTRA_MODEL") or REASONING_MODEL
OPENROUTER_API_KEY = getenv("OPENROUTER_API_KEY", "")

# OpenRouter auto-fallback chains (comma-separated model ids).
# Sent as `models: [primary, *fallbacks]` — OpenRouter tries each on rate-limit/timeout/error.
def _parse_fallbacks(env_name: str) -> list[str]:
    raw = getenv(env_name, "") or ""
    return [m.strip() for m in raw.split(",") if m.strip()]

CHAT_FALLBACKS = _parse_fallbacks("CHAT_FALLBACKS")
DEEP_FALLBACKS = _parse_fallbacks("DEEP_FALLBACKS")
LITE_FALLBACKS = _parse_fallbacks("LITE_FALLBACKS")

def _fallbacks_for(model: str) -> list[str]:
    if model == DEEP_MODEL:
        return DEEP_FALLBACKS
    if model == LITE_MODEL:
        return LITE_FALLBACKS
    return CHAT_FALLBACKS

# Chat agents use CHAT_MODEL (Gemini 3 Flash — LiveCodeBench 2316, TTFT 1.3s)
# Use OpenRouter chat-completions (NOT Responses API) — Responses passes tool
# schemas through OpenAI's strict validator which rejects mixed required/optional
# params on Gemini ("required[0]: property is not defined"). Plain chat completions
# accepts standard JSON Schema and Gemini handles optional fields correctly.
# temperature pinned low so the same question yields the same SQL across chats
# (non-deterministic answers — CRM feedback 2026-05-21). Determinism > creativity
# for a data agent. Override per-call only where variety is wanted.
# extra_body.provider.data_collection="allow" — opt OpenRouter calls into
# providers that may train on inputs. Without it, the account's restrictive
# data policy (openrouter.ai/settings/privacy) can yield "No endpoints available
# matching your data policy" (404) when the only policy-compliant provider for
# the model is unavailable for the full tool-using request. Trade-off: inputs
# may be used for provider training. Shared constant so EVERY OpenRouter model
# (MODEL here + router/reporter/reasoner sub-agents + the per-tier override in
# app/projects.py) opts in identically — miss one and the team still 404s.
OR_DATA_POLICY = {"provider": {"data_collection": "allow"}}
MODEL = OpenRouter(id=CHAT_MODEL, temperature=0.1, extra_body=OR_DATA_POLICY)

# Per-task config: model, temperature, max_tokens, thinking level, timeout
# Tasks without "model" key use TRAINING_MODEL (Gemini 3 Flash)
TRAINING_CONFIGS = {
    # ── Gemini 3 Flash tasks (workhorse: SQL, vision, structured output) ──
    "qa_generation":    {"temp": 0.3, "tokens": 2000, "thinking": "medium"},
    "persona":          {"temp": 0.2, "tokens": 1000, "thinking": "low"},
    "workflows":        {"temp": 0.3, "tokens": 500,  "thinking": "minimal"},
    "synthesis":        {"temp": 0.1, "tokens": 1000, "thinking": "minimal"},
    "insights":         {"temp": 0.1, "tokens": 500,  "thinking": "minimal"},
    "dashboard":        {"temp": 0.2, "tokens": 3000, "thinking": "medium"},
    "dashboard_gen":    {"temp": 0.3, "tokens": 8000, "thinking": "low", "model": CHAT_MODEL, "timeout": 60},
    "fact_extraction":  {"temp": 0.1, "tokens": 2000, "thinking": "medium"},
    "vision":           {"temp": 0.1, "tokens": 1000, "thinking": "minimal"},
    "excel_analysis":   {"temp": 0.1, "tokens": 4000, "thinking": "medium", "timeout": 60},

    # ── GPT-5.4 Mini tasks (deep thinker: xhigh reasoning, offline) ──
    "deep_analysis":    {"temp": 0.1, "tokens": 8000, "thinking": "medium", "model": DEEP_MODEL, "timeout": 60},
    "relationships":    {"temp": 0.1, "tokens": 500,  "thinking": "high",  "model": DEEP_MODEL, "timeout": 60},
    "evolve":           {"temp": 0.1, "tokens": 800,  "thinking": "high",  "model": DEEP_MODEL, "timeout": 60},
    "consolidation":    {"temp": 0.1, "tokens": 800,  "thinking": "high",  "model": DEEP_MODEL, "timeout": 60},
    "domain_knowledge": {"temp": 0.1, "tokens": 1000, "thinking": "high",  "model": DEEP_MODEL, "timeout": 60},

    # ── ML prediction (needs strong reasoning for numerical trends) ──
    "ml_prediction":    {"temp": 0.1, "tokens": 1000, "thinking": "high",  "model": DEEP_MODEL, "timeout": 30},

    # ── Flash Lite tasks (router: fastest, cheapest, bulk) ──
    "scoring":          {"temp": 0.0, "tokens": 100,  "thinking": "none",    "model": LITE_MODEL},
    "routing":          {"temp": 0.0, "tokens": 80,   "thinking": "none",    "model": LITE_MODEL},
    "extraction":       {"temp": 0.1, "tokens": 800,  "thinking": "minimal", "model": LITE_MODEL},
    "meta_learning":    {"temp": 0.0, "tokens": 300,  "thinking": "none",    "model": LITE_MODEL},
    "mining":           {"temp": 0.2, "tokens": 1000, "thinking": "low",     "model": LITE_MODEL},
    # Ingest router: decide append-vs-new-table per uploaded file. temp 0 so the
    # same file always routes the same way (determinism > creativity for ingest).
    "ingest_router":    {"temp": 0.0, "tokens": 600,  "thinking": "minimal", "model": LITE_MODEL},
    # Complexity router: classify a chat message LOOKUP/ANALYSIS/AGENTIC. temp 0
    # for deterministic tiebreaks; only invoked in the ambiguous boundary band.
    "complexity_router": {"temp": 0.0, "tokens": 200, "thinking": "minimal", "model": LITE_MODEL},
    # Viz router: pick the best chart type when the rules engine is unsure.
    "viz_router":        {"temp": 0.0, "tokens": 120, "thinking": "minimal", "model": LITE_MODEL},
}


def _repair_json(s: str) -> str:
    """Try to repair truncated or malformed JSON from LLM output."""
    import json
    s = s.strip()
    if not s:
        return s
    # Quick check — if it parses fine, return as-is
    try:
        json.loads(s)
        return s
    except Exception:
        pass
    # Fix: truncated array — close open strings, objects, arrays
    # Count unbalanced brackets
    fixed = s
    open_braces = fixed.count("{") - fixed.count("}")
    open_brackets = fixed.count("[") - fixed.count("]")
    # If truncated mid-string, close the string
    in_string = False
    escape = False
    for ch in fixed:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        fixed += '"'
    # Close open braces/brackets
    fixed += "}" * max(0, open_braces)
    fixed += "]" * max(0, open_brackets)
    # Remove trailing commas before closing brackets
    import re
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    try:
        json.loads(fixed)
        return fixed
    except Exception:
        return s  # Return original if still broken


# LLM call observers — keyed by an arbitrary tag (typically project_slug).
# Module-level dict is shared across threads so observer set in _bg() also fires
# from any sub-thread spawned during training.
import threading as _threading
_LLM_OBSERVERS: dict = {}
_LLM_OBSERVERS_LOCK = _threading.Lock()

# Per-thread current project slug. When set, `_emit_llm_stats` will record the
# call into `dash_llm_costs` and pre-call budget checks know which cap to enforce.
_LLM_CTX = _threading.local()


def set_llm_observer(fn, tag: str = "_default"):
    """Register a callback(stats: dict) invoked after every LLM call.

    Pass a project-scoped tag if multiple observers may be active. Returns the
    same tag so callers can pass it to reset_llm_observer().
    """
    with _LLM_OBSERVERS_LOCK:
        _LLM_OBSERVERS[tag] = fn
    return tag


def reset_llm_observer(tag):
    if not tag:
        return
    with _LLM_OBSERVERS_LOCK:
        _LLM_OBSERVERS.pop(tag, None)


def set_llm_project(slug: str | None):
    """Bind the current thread's LLM activity to a project for cost tracking."""
    _LLM_CTX.project_slug = slug or None


def get_llm_project() -> str | None:
    return getattr(_LLM_CTX, "project_slug", None)


def set_llm_actor(username: str | None):
    """Bind the current thread's LLM activity to a user for usage attribution.

    Powers the Admin Usage dashboard's per-user / "who used what" rollups. Like
    set_llm_project, this is thread-local and read by _emit_llm_stats.
    """
    _LLM_CTX.actor = username or None


def get_llm_actor() -> str | None:
    return getattr(_LLM_CTX, "actor", None)


class _BudgetExceeded(Exception):
    """Raised when a project's daily LLM cost cap is reached."""
    def __init__(self, slug: str, today: float, cap: float):
        super().__init__(f"daily LLM cost cap reached for {slug}: ${today:.4f} >= ${cap:.4f}")
        self.slug = slug
        self.today = today
        self.cap = cap


# Tiny in-process cache to avoid hitting the DB on every LLM call.
# {slug: (over_budget, expires_epoch, today_spend, cap)}
_BUDGET_CACHE: dict = {}
_BUDGET_TTL_S = 5.0


def _check_budget(slug: str | None) -> tuple[bool, float, float]:
    """Return (over_budget, today_usd, cap_usd). cap_usd<=0 means unlimited."""
    if not slug:
        return (False, 0.0, 0.0)
    import time as _t
    now = _t.time()
    cached = _BUDGET_CACHE.get(slug)
    if cached and cached[1] > now:
        return (cached[0], cached[2], cached[3])
    over, today, cap = (False, 0.0, 0.0)
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine as _gse
        eng = _gse()
        with eng.connect() as conn:
            r = conn.execute(_text(
                "SELECT COALESCE(daily_cost_cap_usd, 0) FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).fetchone()
            cap = float(r[0]) if r else 0.0
            r2 = conn.execute(_text(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM public.dash_llm_costs "
                "WHERE project_slug = :s "
                "  AND ts >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
            ), {"s": slug}).fetchone()
            today = float((r2 or [0])[0] or 0.0)
        # cap <= 0 = unlimited
        over = bool(cap > 0 and today >= cap)
    except Exception:
        pass
    _BUDGET_CACHE[slug] = (over, now + _BUDGET_TTL_S, today, cap)
    return (over, today, cap)


def _invalidate_budget_cache(slug: str | None):
    if slug and slug in _BUDGET_CACHE:
        _BUDGET_CACHE.pop(slug, None)


# Approx prices per 1M tokens (USD). Used as fallback when API doesn't return usage.cost.
_MODEL_PRICES_PER_1M = {
    "google/gemini-3-flash-preview":            (0.30, 1.20),   # in, out
    "google/gemini-3.1-flash-lite-preview":     (0.075, 0.30),
    "openai/gpt-5.4-mini":                      (0.30, 1.50),
    "google/gemini-embedding-2-preview":        (0.10, 0.0),
}


def _compute_cost(model: str, usage: dict) -> float:
    if not usage:
        return 0.0
    if "total_cost" in usage and isinstance(usage["total_cost"], (int, float)):
        return float(usage["total_cost"])
    if "cost" in usage and isinstance(usage["cost"], (int, float)):
        return float(usage["cost"])
    pin = usage.get("prompt_tokens", 0) or 0
    pout = usage.get("completion_tokens", 0) or 0
    rates = None
    for k, v in _MODEL_PRICES_PER_1M.items():
        if k in (model or "").lower() or model in k:
            rates = v; break
    if not rates:
        return 0.0
    return (pin * rates[0] + pout * rates[1]) / 1_000_000


def _usage_token_details(usage: dict) -> tuple[int, int]:
    """Pull (cached_tokens, reasoning_tokens) from an OpenRouter usage object.
    OpenRouter returns these under usage.prompt_tokens_details.cached_tokens and
    usage.completion_tokens_details.reasoning_tokens when the model supports
    prompt caching / reasoning. Absent → 0 (back-compat)."""
    if not usage:
        return 0, 0
    cached = 0
    reasoning = 0
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        cached = int(ptd.get("cached_tokens", 0) or 0)
    ctd = usage.get("completion_tokens_details")
    if isinstance(ctd, dict):
        reasoning = int(ctd.get("reasoning_tokens", 0) or 0)
    return cached, reasoning


def _emit_llm_stats(stats: dict):
    # Persist to per-call cost ledger when a project is bound to this thread.
    slug = get_llm_project()
    if slug:
        try:
            from sqlalchemy import text as _text
            from db.session import get_sql_engine as _gse
            with _gse().connect() as conn:
                conn.execute(_text(
                    "INSERT INTO public.dash_llm_costs "
                    "(project_slug, task, model, cost_usd, tokens_in, tokens_out, cached_tokens, reasoning_tokens, ok, actor) "
                    "VALUES (:s, :t, :m, :c, :ti, :to, :cached, :reason, :ok, :actor)"
                ), {
                    "s": slug,
                    "t": stats.get("task"),
                    "m": stats.get("model"),
                    "c": float(stats.get("cost_usd", 0.0) or 0.0),
                    "ti": int(stats.get("tokens_in", 0) or 0),
                    "to": int(stats.get("tokens_out", 0) or 0),
                    "cached": int(stats.get("cached_tokens", 0) or 0),
                    "reason": int(stats.get("reasoning_tokens", 0) or 0),
                    "ok": bool(stats.get("ok", True)),
                    "actor": get_llm_actor(),
                })
                conn.commit()
            _invalidate_budget_cache(slug)
        except Exception:
            pass

    with _LLM_OBSERVERS_LOCK:
        observers = list(_LLM_OBSERVERS.values())
    for obs in observers:
        try:
            obs(stats)
        except Exception:
            pass


def training_llm_call(prompt: str, task: str = "extraction", model: str | None = None) -> str | None:
    """Call LLM using training model with task-specific settings. Returns content or None.
    `model` param overrides TRAINING_CONFIGS[task]['model'] when provided (e.g. Deep Dash
    per-stage gen_model/judge_model selection). Other config (temperature, thinking, tokens)
    still comes from the task entry."""
    import httpx, time as _time
    cfg = TRAINING_CONFIGS.get(task, TRAINING_CONFIGS["extraction"])
    if not OPENROUTER_API_KEY:
        # Pool may have keys even when OPENROUTER_API_KEY env unset
        try:
            from dash.llm_client import get_pool
            if not get_pool().has_keys():
                return None
        except Exception:
            return None
    model = model or cfg.get("model") or get_training_model()
    # Live-resolve: if model still matches boot-time CHAT/DEEP/LITE, swap to current setting
    if model == CHAT_MODEL:   model = get_chat_model()
    elif model == DEEP_MODEL: model = get_deep_model()
    elif model == LITE_MODEL: model = get_lite_model()
    # Daily cost cap gate
    _slug = get_llm_project()
    if _slug:
        _over, _today, _cap = _check_budget(_slug)
        if _over:
            _emit_llm_stats({
                "task": task, "model": model, "latency_s": 0.0,
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                "ok": False, "capped": True,
                "error": f"daily cap reached: ${_today:.4f}/${_cap:.4f}",
            })
            return None
    t0 = _time.monotonic()
    # I11: prompt-cache salt — prepend deterministic tenant/project tag so
    # OpenRouter/upstream prompt-cache hashes differ across tenants even when
    # remaining prompt body is byte-identical. Cheap (~30 tokens), prevents
    # theoretical cross-tenant cache collision.
    try:
        _salt_slug = get_llm_project() or "global"
        _salt_org = _slug if _slug else "global"
        _salted_prompt = f"# project: {_salt_slug}\n# tenant: {_salt_org}\n\n{prompt}"
    except Exception:
        _salted_prompt = prompt
    try:
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": _salted_prompt}],
            "max_tokens": cfg["tokens"],
            "temperature": cfg["temp"],
            "usage": {"include": True},
        }
        _fb = _fallbacks_for(model)
        if _fb:
            body["models"] = [model, *_fb]
        if cfg["thinking"] != "none":
            if "gemini" in model.lower():
                body["reasoning"] = {"effort": cfg["thinking"]}
            elif "gpt" in model.lower() or "openai" in model.lower():
                effort_map = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}
                body["reasoning_effort"] = effort_map.get(cfg["thinking"], "medium")
        # OpenRouter provider-side fallback: if primary 429s, OpenRouter routes to sibling
        from dash.llm_client import call_openrouter_sync, inject_provider_fallback
        body = inject_provider_fallback(body, _fb if _fb else None)
        resp = call_openrouter_sync(body, timeout=cfg.get("timeout", 30))
        latency_s = round(_time.monotonic() - t0, 2)
        result = resp.json()
        usage = result.get("usage") or {}
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        actual_model = result.get("model", model)
        cost = _compute_cost(actual_model, usage)
        _cached, _reason = _usage_token_details(usage)
        _emit_llm_stats({
            "task": task, "model": actual_model, "latency_s": latency_s,
            "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
            "cached_tokens": _cached, "reasoning_tokens": _reason,
            "cost_usd": round(cost, 6), "ok": bool(content),
            "fallback_used": actual_model != model,
        })
        # Empty-response detector: gemini-3-flash sometimes returns 0 tokens silently.
        # Auto-retry with LITE_MODEL if non-LITE returned empty.
        if (not content or len(content.strip()) < 5) and model != LITE_MODEL:
            import logging as _empty_log
            _empty_log.getLogger(__name__).warning(
                f"empty LLM response from {model} (task={task}); retrying with LITE_MODEL"
            )
            try:
                retry_body = {
                    "model": LITE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": cfg["tokens"],
                    "temperature": cfg["temp"],
                    "usage": {"include": True},
                }
                _rfb = _fallbacks_for(LITE_MODEL)
                if _rfb:
                    retry_body["models"] = [LITE_MODEL, *_rfb]
                from dash.llm_client import call_openrouter_sync as _call_or, inject_provider_fallback as _inj_fb
                retry_body = _inj_fb(retry_body, _rfb if _rfb else None)
                retry_resp = _call_or(retry_body, timeout=cfg.get("timeout", 30))
                retry_result = retry_resp.json()
                content = retry_result.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                actual_model = retry_result.get("model", LITE_MODEL)
                retry_usage = retry_result.get("usage") or {}
                _emit_llm_stats({
                    "task": task, "model": actual_model, "latency_s": 0.0,
                    "tokens_in": retry_usage.get("prompt_tokens", 0),
                    "tokens_out": retry_usage.get("completion_tokens", 0),
                    "cost_usd": round(_compute_cost(actual_model, retry_usage), 6),
                    "ok": bool(content), "retry_from_empty": True,
                })
            except Exception as _retry_e:
                import logging as _empty_log2
                _empty_log2.getLogger(__name__).warning(f"LITE_MODEL retry also failed: {_retry_e}")

        if content:
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip().strip("`").strip()
            if clean.lower().startswith("json"):
                clean = clean[4:].strip()
            clean = _repair_json(clean)
            return clean
        return None
    except Exception as _e:
        latency_s = round(_time.monotonic() - t0, 2)
        _emit_llm_stats({"task": task, "model": model, "latency_s": latency_s, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "ok": False, "error": str(_e)[:120]})
        return None


def training_vision_call(prompt: str, images: list[dict], task: str = "vision") -> str | None:
    """Call LLM with images for vision-based extraction. images: [{"b64": str, "mime": str}]
    Uses per-task model override (defaults to TRAINING_MODEL = Gemini 3 Flash for best vision)."""
    from os import getenv
    api_key = getenv("OPENROUTER_API_KEY", "")
    if not images:
        return None
    # api_key not required directly — pool may hold OPENROUTER_API_KEYS instead
    from dash.llm_client import get_pool as _get_pool
    if not api_key and not _get_pool().has_keys():
        return None
    cfg = TRAINING_CONFIGS.get(task, TRAINING_CONFIGS["extraction"])
    model = cfg.get("model") or get_training_model()
    if model == CHAT_MODEL:   model = get_chat_model()
    elif model == DEEP_MODEL: model = get_deep_model()
    elif model == LITE_MODEL: model = get_lite_model()
    # Daily cost cap gate
    _slug = get_llm_project()
    if _slug:
        _over, _today, _cap = _check_budget(_slug)
        if _over:
            _emit_llm_stats({
                "task": task, "model": model, "latency_s": 0.0,
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                "ok": False, "vision": True, "capped": True,
                "error": f"daily cap reached: ${_today:.4f}/${_cap:.4f}",
            })
            return None
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images[:30]:
        content.append({"type": "image_url", "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"}})
    import httpx
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": cfg["tokens"],
        "temperature": cfg["temp"],
    }
    _fb = _fallbacks_for(model)
    if _fb:
        body["models"] = [model, *_fb]
    import time as _time
    t0 = _time.monotonic()
    try:
        body["usage"] = {"include": True}
        from dash.llm_client import call_openrouter_sync as _call_or_v, inject_provider_fallback as _inj_fb_v
        body = _inj_fb_v(body, _fb if _fb else None)
        resp = _call_or_v(body, timeout=45)
        latency_s = round(_time.monotonic() - t0, 2)
        result = resp.json()
        usage = result.get("usage") or {}
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        cost = _compute_cost(model, usage)
        _cached, _reason = _usage_token_details(usage)
        _emit_llm_stats({
            "task": task, "model": model, "latency_s": latency_s,
            "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
            "cached_tokens": _cached, "reasoning_tokens": _reason,
            "cost_usd": round(cost, 6), "ok": bool(content), "vision": True, "n_images": len(images),
        })
        return content
    except Exception as _e:
        latency_s = round(_time.monotonic() - t0, 2)
        _emit_llm_stats({"task": task, "model": model, "latency_s": latency_s, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "ok": False, "vision": True, "error": str(_e)[:120]})
        return None


# Slack
SLACK_TOKEN = getenv("SLACK_TOKEN", "")
SLACK_SIGNING_SECRET = getenv("SLACK_SIGNING_SECRET", "")

# Dual knowledge system
# KNOWLEDGE: Static, curated (table schemas, validated queries, business rules)
dash_knowledge = create_knowledge("Dash Knowledge", "dash_knowledge")
# LEARNINGS: Dynamic, discovered (error patterns, gotchas, user corrections)
dash_learnings = create_knowledge("Dash Learnings", "dash_learnings")

# Shared learning machine — single instance used by leader + all members.
dash_learning = LearningMachine(
    knowledge=dash_learnings,
    learned_knowledge=LearnedKnowledgeConfig(mode=LearningMode.AGENTIC),
)
