"""
Database Session
================

PostgreSQL database connection for AgentOS.

Two schemas:
- ``public``: Company data (loaded externally). Read-only for agents.
- ``dash``: Agent-managed data (views, summary tables). Owned by Engineer.

All engines route through PgBouncer (transaction mode).  PgBouncer ignores
the ``options`` startup parameter and runs DISCARD ALL between server
assignments, so we set ``search_path`` and ``default_transaction_read_only``
via ``SET LOCAL`` after each BEGIN using SQLAlchemy's ``after_begin`` event.
"""

import gc
import re
import threading
import time as _time
from datetime import datetime, timezone

from agno.db.postgres import PostgresDb
from agno.knowledge import Knowledge
from agno.knowledge.embedder.openai import OpenAIEmbedder
from os import getenv as _getenv
from agno.vectordb.pgvector import PgVector, SearchType
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.pool import NullPool

from db.url import db_url

DB_ID = "dash-db"

# PostgreSQL schema for agent-managed tables (views, summaries, computed data).
# Company data stays in "public". Agno framework tables use the default schema.
DASH_SCHEMA = "dash"

# Cached engines — one per access pattern, created on first use.
_dash_engine: Engine | None = None
_readonly_engine: Engine | None = None


# ---------------------------------------------------------------------------
# Helpers — set session variables via SET LOCAL (PgBouncer transaction-safe)
# ---------------------------------------------------------------------------

def _make_search_path_listener(search_path: str):
    """Return a 'begin' event listener that sets search_path via SET LOCAL."""
    def set_search_path(conn):
        conn.execute(text(f"SET LOCAL search_path TO {search_path}"))
    return set_search_path


def _make_readonly_listener():
    """Return a 'begin' event listener that sets read-only + timeout."""
    def set_readonly(conn):
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = 30000"))
    return set_readonly


def _dash_engine_timeouts(conn):
    """Begin-event listener: cooperative cancellation for the dash main engine.

    Less aggressive than per-source providers (60s vs 110s) because dash
    engine queries are usually metadata lookups that should never run
    that long anyway. Pairs with asyncio.wait_for in dash.learning.cycle
    so timed-out queries are killed server-side, not left as zombies.
    """
    try:
        conn.execute(text("SET LOCAL statement_timeout = '60s'"))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public-schema write guard (Engineer connection)
# ---------------------------------------------------------------------------
_PUBLIC_WRITE_RE = re.compile(
    r"""(?ix)
    # DDL targeting public schema
    (?:create|alter|drop)\s+
    (?:or\s+replace\s+)?
    (?:(?:temp|temporary|unlogged|materialized)\s+)?
    (?:table|view|index|sequence|function|procedure|trigger|type)\s+
    (?:if\s+(?:not\s+)?exists\s+)?
    "?public"?\s*\.
    |
    # DML targeting public schema
    insert\s+into\s+"?public"?\s*\.
    |
    update\s+"?public"?\s*\.
    |
    delete\s+from\s+"?public"?\s*\.
    |
    truncate\s+(?:table\s+)?"?public"?\s*\.
    """,
)


def _guard_public_schema(conn, cursor, statement, parameters, context, executemany):
    """Block DDL/DML targeting the public schema on the Engineer's connection."""
    if _PUBLIC_WRITE_RE.search(statement):
        raise RuntimeError(
            "Cannot write to the public schema. "
            "Use the dash schema for all CREATE, ALTER, DROP, INSERT, UPDATE, and DELETE operations."
        )


def get_sql_engine() -> Engine:
    """SQLAlchemy engine scoped to the dash schema (cached).

    Bootstraps by creating the schema if it doesn't exist, then returns
    an engine with search_path=dash,public so the Engineer can read company
    data in public and write to dash.
    """
    global _dash_engine
    if _dash_engine is not None:
        return _dash_engine
    bootstrap = create_engine(db_url, poolclass=NullPool)
    with bootstrap.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DASH_SCHEMA}"))
        conn.commit()
    bootstrap.dispose()
    _dash_engine = create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    event.listen(_dash_engine, "begin", _make_search_path_listener(f"{DASH_SCHEMA},public"))
    event.listen(_dash_engine, "before_cursor_execute", _guard_public_schema)
    try:
        event.listen(_dash_engine, "begin", _dash_engine_timeouts)
    except Exception:
        pass
    return _dash_engine


_write_engine: Engine | None = None


def get_write_engine() -> Engine:
    """Cached read-write engine for platform metadata tables in `public`
    (e.g. dash_ingest_*). Unlike get_sql_engine() it has NO public-schema
    write guard and is not read-only. search_path = public,dash.
    Shared + cached — never dispose it.
    """
    global _write_engine
    if _write_engine is not None:
        return _write_engine
    _write_engine = create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    event.listen(_write_engine, "begin", _make_search_path_listener("public,dash"))
    return _write_engine


def get_readonly_engine() -> Engine:
    """SQLAlchemy engine with read-only transactions (cached).

    Uses PostgreSQL's ``default_transaction_read_only`` so any INSERT,
    UPDATE, DELETE, CREATE, DROP, or ALTER is rejected at the database level.
    """
    global _readonly_engine
    if _readonly_engine is not None:
        return _readonly_engine
    _readonly_engine = create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    event.listen(_readonly_engine, "begin", _make_readonly_listener())
    return _readonly_engine


def get_postgres_db(contents_table: str | None = None) -> PostgresDb:
    """Create a PostgresDb instance."""
    if contents_table is not None:
        return PostgresDb(id=DB_ID, db_url=db_url, knowledge_table=contents_table)
    return PostgresDb(id=DB_ID, db_url=db_url)


# ---------------------------------------------------------------------------
# Per-user schema management
# ---------------------------------------------------------------------------
_user_engines: dict[str, Engine] = {}
_user_ro_engines: dict[str, Engine] = {}
_engine_timestamps: dict[str, float] = {}
_engine_lock = threading.Lock()
_ENGINE_CACHE_MAX = 100
_ENGINE_CACHE_TTL = 1800  # 30 minutes


def _dispose_key(k: str) -> bool:
    """Dispose+remove a cached engine across all caches. Returns True if anything removed."""
    removed = False
    for cache in (_user_engines, _user_ro_engines, _project_engines, _project_ro_engines):
        eng = cache.pop(k, None)
        if eng:
            removed = True
            try:
                eng.dispose()
            except Exception:
                pass
    _engine_timestamps.pop(k, None)
    if removed:
        # Aggressive reclaim: SQLAlchemy engine + pool retains memory until GC.
        try:
            gc.collect()
        except Exception:
            pass
    return removed


def _evict_stale_engines():
    """Remove expired engines + enforce LRU cap. Call under _engine_lock."""
    now = _time.time()
    # 1. TTL eviction (30 min)
    expired = [k for k, ts in _engine_timestamps.items() if now - ts > _ENGINE_CACHE_TTL]
    for k in expired:
        _dispose_key(k)
    # 2. LRU eviction — keep cache <= max by dropping oldest timestamps
    if len(_engine_timestamps) > _ENGINE_CACHE_MAX:
        ordered = sorted(_engine_timestamps.items(), key=lambda kv: kv[1])
        overflow = len(_engine_timestamps) - _ENGINE_CACHE_MAX
        for k, _ in ordered[:overflow]:
            _dispose_key(k)


def get_engine_cache_stats() -> dict:
    """Snapshot of the per-tenant engine cache for /api/admin/engines/stats."""
    with _engine_lock:
        now = _time.time()
        ts = dict(_engine_timestamps)
        user_count = len(_user_engines)
        user_ro_count = len(_user_ro_engines)
        proj_count = len(_project_engines)
        proj_ro_count = len(_project_ro_engines)
        cached_total = user_count + user_ro_count + proj_count + proj_ro_count
        oldest_age_s = None
        oldest_key = None
        if ts:
            oldest_key, oldest_ts = min(ts.items(), key=lambda kv: kv[1])
            oldest_age_s = round(now - oldest_ts, 1)
        # Rough memory estimate: SQLAlchemy Engine + pool ~ 250KB baseline,
        # +50KB per pooled conn. Engines here use small pools (2+3) or NullPool.
        est_kb_per_engine = 300
        est_memory_kb = cached_total * est_kb_per_engine
    return {
        "cached_total": cached_total,
        "by_kind": {
            "user_rw": user_count,
            "user_ro": user_ro_count,
            "project_rw": proj_count,
            "project_ro": proj_ro_count,
        },
        "max_entries": _ENGINE_CACHE_MAX,
        "ttl_seconds": _ENGINE_CACHE_TTL,
        "tracked_keys": len(ts),
        "oldest_key": oldest_key,
        "oldest_age_seconds": oldest_age_s,
        "est_memory_kb": est_memory_kb,
        "est_memory_mb": round(est_memory_kb / 1024, 2),
    }


def _sanitize_user_id(user_id: str) -> str:
    """Sanitize user_id for use as PostgreSQL schema name."""
    safe = re.sub(r"[^a-z0-9_]", "_", str(user_id).lower())
    return f"user_{safe}"[:63]


def create_user_schema(user_id: str) -> str:
    """Create a per-user PostgreSQL schema. Returns the schema name."""
    schema = _sanitize_user_id(user_id)
    bootstrap = create_engine(db_url, poolclass=NullPool)
    with bootstrap.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        conn.commit()
    bootstrap.dispose()
    return schema


def get_user_engine(user_id: str) -> Engine:
    """SQLAlchemy engine scoped to user's schema (cached)."""
    schema = _sanitize_user_id(user_id)
    with _engine_lock:
        _evict_stale_engines()
        if schema in _user_engines:
            _engine_timestamps[schema] = _time.time()
            return _user_engines[schema]

    create_user_schema(user_id)
    eng = create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
    event.listen(eng, "begin", _make_search_path_listener(f'"{schema}",public'))
    event.listen(eng, "before_cursor_execute", _guard_public_schema)
    with _engine_lock:
        _user_engines[schema] = eng
        _engine_timestamps[schema] = _time.time()
    return eng


def get_user_readonly_engine(user_id: str) -> Engine:
    """Read-only engine scoped to user's schema (cached)."""
    schema = _sanitize_user_id(user_id)
    if schema in _user_ro_engines:
        return _user_ro_engines[schema]

    create_user_schema(user_id)
    eng = create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
    event.listen(eng, "begin", _make_search_path_listener(f'"{schema}",public'))
    event.listen(eng, "begin", _make_readonly_listener())
    _user_ro_engines[schema] = eng
    return eng


def create_user_knowledge(user_id: str) -> Knowledge:
    """Create a per-user Knowledge instance with PgVector hybrid search."""
    schema = _sanitize_user_id(user_id)
    return create_knowledge(f"Knowledge ({user_id})", f"{schema}_knowledge")


def create_user_learnings(user_id: str) -> Knowledge:
    """Create a per-user Learnings instance."""
    schema = _sanitize_user_id(user_id)
    return create_knowledge(f"Learnings ({user_id})", f"{schema}_learnings")


# ---------------------------------------------------------------------------
# Per-project schema management
# ---------------------------------------------------------------------------
_project_engines: dict[str, Engine] = {}
_project_ro_engines: dict[str, Engine] = {}


def create_project_schema(slug: str) -> str:
    """Create a project PostgreSQL schema. Returns the schema name."""
    # Single-tenant guard: refuse to spawn any schema other than the locked one.
    # This is the only place a new tenant schema can be born, so blocking it here
    # makes a second tenant structurally impossible even from internal callers.
    try:
        from dash.single_agent import is_single_agent, locked_slug
        if is_single_agent() and slug != locked_slug():
            raise RuntimeError(
                f"single-tenant lock: refusing to create schema for '{slug}' "
                f"(only '{locked_slug()}' is allowed)"
            )
    except ImportError:
        pass
    safe = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    bootstrap = create_engine(db_url, poolclass=NullPool)
    with bootstrap.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{safe}"'))
        conn.commit()
    bootstrap.dispose()
    return safe


def get_project_engine(slug: str) -> Engine:
    """Engine scoped to project schema (cached, NullPool — PgBouncer handles pooling).

    Per-access LRU + TTL eviction (30 min TTL, 100 cap) to keep memory bounded
    on multi-project workloads. ``gc.collect()`` after dispose to reclaim pool
    memory (SQLAlchemy Engine + dialect state ~250KB each).
    """
    safe = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    with _engine_lock:
        _evict_stale_engines()
        if safe in _project_engines:
            _engine_timestamps[safe] = _time.time()
            return _project_engines[safe]
    create_project_schema(slug)
    eng = create_engine(
        db_url,
        poolclass=NullPool,
    )
    event.listen(eng, "begin", _make_search_path_listener(f'"{safe}"'))
    with _engine_lock:
        _project_engines[safe] = eng
        _engine_timestamps[safe] = _time.time()
    return eng


def get_project_readonly_engine(slug: str) -> Engine:
    """Read-only engine scoped to project schema ONLY (cached, NullPool).

    Shares timestamps + LRU cap with read-write engines under one project key.
    """
    safe = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    with _engine_lock:
        _evict_stale_engines()
        if safe in _project_ro_engines:
            _engine_timestamps[safe] = _time.time()
            return _project_ro_engines[safe]
    create_project_schema(slug)
    eng = create_engine(
        db_url,
        poolclass=NullPool,
    )
    event.listen(eng, "begin", _make_search_path_listener(f'"{safe}"'))
    event.listen(eng, "begin", _make_readonly_listener())
    with _engine_lock:
        _project_ro_engines[safe] = eng
        _engine_timestamps[safe] = _time.time()
    return eng


def create_project_knowledge(slug: str) -> Knowledge:
    """Per-project Knowledge with PgVector."""
    safe = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    return create_knowledge(f"Knowledge ({slug})", f"{safe}_knowledge")


def create_project_learnings(slug: str) -> Knowledge:
    """Per-project Learnings."""
    safe = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    return create_knowledge(f"Learnings ({slug})", f"{safe}_learnings")


import logging as _logging
_embed_logger = _logging.getLogger("embedding")

# Embedding model cascade — try in order, first success wins.
# All via OpenRouter (same API key). Add/remove/reorder as needed.
# Order matters — first success wins. Put native-1536 models first to avoid
# lossy truncation from 3072→1536 (vector(1536) schema in dash_vectors).
_EMBEDDING_MODELS = [
    "openai/text-embedding-3-small",        # 1536 native — no truncation, matches schema exactly
    "openai/text-embedding-3-large",        # 3072 native — clean dim reduction via `dimensions` param
    "google/gemini-embedding-2-preview",    # 3072 native — truncated to 1536 (lossy but functional)
    "cohere/embed-v4.0",                    # last resort
]

# Track which model is active (for model-change detection)
_active_embedding_model: str = ""

# ---------------------------------------------------------------------------
# Embedding health — in-memory status exposed via GET /api/health/embeddings
# ---------------------------------------------------------------------------
_embedding_health: dict = {
    "last_successful_model": None,
    "last_success_at": None,
    "last_failure_at": None,
    "last_failure_reason": None,
    "consecutive_failures": 0,
    "total_cascade_failures": 0,
}
_embedding_health_lock = threading.Lock()


def get_embedding_health() -> dict:
    """Return a snapshot of the embedding health dict (used by health endpoint)."""
    with _embedding_health_lock:
        return dict(_embedding_health)


def _record_embedding_success(model: str) -> None:
    with _embedding_health_lock:
        _embedding_health["last_successful_model"] = model
        _embedding_health["last_success_at"] = datetime.now(timezone.utc).isoformat()
        _embedding_health["consecutive_failures"] = 0


def _record_embedding_failure(reason: str, full_cascade_failed: bool = False) -> None:
    with _embedding_health_lock:
        _embedding_health["last_failure_at"] = datetime.now(timezone.utc).isoformat()
        _embedding_health["last_failure_reason"] = reason[:500]
        _embedding_health["consecutive_failures"] += 1
        if full_cascade_failed:
            _embedding_health["total_cascade_failures"] += 1


def _notify_full_cascade_failure(project_slug: str | None, attempted_models: list[str]) -> None:
    """Write a system-level notification to dash_notifications for super admins.

    Fail-soft: never raise. Routes to every user with is_super_admin=TRUE so
    operators are alerted even though dash_notifications.user_id is NOT NULL.
    """
    try:
        bootstrap = create_engine(db_url, poolclass=NullPool)
        try:
            title = "All embedding models failed"
            slug_part = f" for project '{project_slug}'" if project_slug else ""
            message = (
                f"All {len(attempted_models)} embedding models failed{slug_part}. "
                f"Knowledge index is degraded — search and indexing unavailable. "
                f"Attempted: {', '.join(attempted_models)}"
            )
            with bootstrap.begin() as conn:
                # Best-effort discovery of super admin users. Falls back silently.
                try:
                    admins = conn.execute(text(
                        "SELECT id FROM public.dash_users WHERE is_super_admin = TRUE"
                    )).fetchall()
                except Exception:
                    admins = []
                if not admins:
                    # No super admin column or no admins — try notifying user_id=1 (typical bootstrap admin).
                    admins = [(1,)]
                for (uid,) in admins:
                    try:
                        conn.execute(text(
                            "INSERT INTO public.dash_notifications "
                            "(user_id, type, title, message) "
                            "VALUES (:uid, 'error', :title, :msg)"
                        ), {"uid": uid, "title": title, "msg": message})
                    except Exception:
                        continue
        finally:
            bootstrap.dispose()
    except Exception as _e:
        _embed_logger.warning(f"notify_full_cascade_failure failed: {_e}")


def _create_embedder(project_slug: str | None = None) -> OpenAIEmbedder:
    """Create embedder with automatic model cascade.

    Tries each model in _EMBEDDING_MODELS until one works.
    User can override with EMBEDDING_MODEL env var (tried first).
    All models via OpenRouter — same API key, same endpoint.

    On model change: logs warning so admin knows to retrain projects.
    On full cascade failure: records health + writes system-level notification.
    """
    global _active_embedding_model
    api_key = _getenv("OPENROUTER_API_KEY")
    user_model = _getenv("EMBEDDING_MODEL")

    # Build cascade: user override first, then defaults
    cascade = []
    if user_model:
        cascade.append(user_model)
    cascade.extend(m for m in _EMBEDDING_MODELS if m not in cascade)

    last_err = ""
    # Try each model
    for model in cascade:
        try:
            embedder = OpenAIEmbedder(
                id=model,
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                dimensions=1536,
            )
            # Validate: must return non-empty 1536-dim vector
            _vec = embedder.get_embedding("test")
            if not _vec or len(_vec) != 1536:
                raise ValueError(f"returned dim={len(_vec or [])} (expected 1536)")

            # Model change detection
            if _active_embedding_model and _active_embedding_model != model:
                _embed_logger.warning(
                    f"Embedding model changed: {_active_embedding_model} → {model}. "
                    f"Retrain projects for optimal search quality."
                )
            _active_embedding_model = model
            _embed_logger.info(
                f"Embedding cascade SELECTED: {model} (1536 dims) "
                f"[position {cascade.index(model) + 1}/{len(cascade)}]"
            )
            _record_embedding_success(model)
            return embedder
        except Exception as e:
            last_err = f"{model}: {e}"
            _embed_logger.warning(f"Embedding model {model} failed: {e}, trying next...")
            _record_embedding_failure(last_err, full_cascade_failed=False)
            continue

    # All failed — return None so callers can handle gracefully
    _embed_logger.critical(
        f"ALL {len(cascade)} embedding models failed! "
        f"Search and indexing will be unavailable. Last error: {last_err}"
    )
    _record_embedding_failure(
        f"full cascade failure ({len(cascade)} models); last={last_err}",
        full_cascade_failed=True,
    )
    _notify_full_cascade_failure(project_slug, cascade)
    _active_embedding_model = None
    return None


# Cache the embedder — created once, reused for all knowledge bases
_cached_embedder: OpenAIEmbedder | None = None


def _get_embedder() -> OpenAIEmbedder | None:
    """Get or create the cached embedder instance. Returns None if all models failed."""
    global _cached_embedder
    if _cached_embedder is None:
        _cached_embedder = _create_embedder()
    return _cached_embedder


def get_active_embedding_model() -> str | None:
    """Return the currently active embedding model ID (None if all models failed)."""
    _get_embedder()  # ensure initialized
    return _active_embedding_model


def create_knowledge(name: str, table_name: str, project_slug: str | None = None) -> Knowledge:
    """Create a Knowledge instance with PgVector hybrid search.

    Uses Gemini Embedding 2 (primary) with OpenAI fallback.
    Both via OpenRouter — single API key.

    If embedder cascade fails, logs an explicit WARN tagged with the project
    slug (when known) so operators can correlate degradation to a tenant.
    """
    embedder = _get_embedder()
    if embedder is None:
        # Best-effort slug extraction from table_name (project tables follow
        # `{slug}_knowledge` / `{slug}_learnings`) when caller didn't pass one.
        derived_slug = project_slug
        if not derived_slug and table_name:
            for suffix in ("_knowledge", "_learnings"):
                if table_name.endswith(suffix):
                    derived_slug = table_name[: -len(suffix)]
                    break
        _embed_logger.warning(
            f"create_knowledge: embedder unavailable — knowledge '{name}' "
            f"(table={table_name}, project_slug={derived_slug or 'unknown'}) "
            f"will be skipped. All embedding cascade models failed."
        )
        return None
    return Knowledge(
        name=name,
        vector_db=PgVector(
            db_url=db_url,
            table_name=table_name,
            search_type=SearchType.hybrid,
            embedder=embedder,
        ),
        contents_db=get_postgres_db(contents_table=f"{table_name}_contents"),
    )
