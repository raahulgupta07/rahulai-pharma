"""
Authentication API
==================

Simple token-based auth. Users table + tokens table in public schema.

Endpoints:
    POST /api/auth/register  — create user
    POST /api/auth/login     — returns token
    GET  /api/auth/check     — validate token
    POST /api/auth/logout    — invalidate token
"""

import hashlib
try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False
import logging
import os
import secrets
import threading
import time
from os import getenv
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine as _sa_create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_engine = _sa_create_engine(db_url, poolclass=NullPool)
_token_cache: dict[str, dict] = {}
_token_cache_lock = threading.Lock()
_TOKEN_CACHE_MAX = 5000  # Cap to prevent unbounded growth
# Per-worker cache is NOT shared across gunicorn workers, so a logout (which
# deletes the DB row on whatever worker handled it) would not take effect on the
# other workers until the 7-day token expiry. Re-validate against the DB after
# this many seconds so revocation/logout propagates everywhere within the window.
_TOKEN_CACHE_FRESH_TTL = 60  # seconds
import re as _re_auth

TOKEN_EXPIRY = 86400 * 7  # 7 days

# Super admin username from env — this user can manage all users and see all data
SUPER_ADMIN = os.getenv("SUPER_ADMIN", "admin")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Hash password with bcrypt (falls back to SHA256 if bcrypt not available)."""
    if _HAS_BCRYPT:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash. Supports both bcrypt and legacy SHA256."""
    if _HAS_BCRYPT and stored_hash.startswith("$2b$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    # Legacy SHA256 fallback
    return hashlib.sha256(password.encode()).hexdigest() == stored_hash


def _bootstrap_tables():
    """Create auth/core tables — serialized across workers via advisory lock.

    `CREATE TABLE IF NOT EXISTS` is NOT concurrency-safe: parallel DDL from
    multiple gunicorn workers on a fresh DB throws "tuple concurrently
    updated" / "relation already exists" and aborts the whole bootstrap txn →
    partial or empty schema (the root cause of UndefinedTable errors on a clean
    cloud install). We take pg_advisory_lock(72157424) on a DIRECT
    (pgbouncer-bypassing) connection so the first worker builds the schema while
    the rest wait, then run their IF NOT EXISTS as no-ops. The lock is held on a
    direct conn because a transaction-mode pgbouncer would release a
    session-level lock at txn end.
    """
    from sqlalchemy.pool import NullPool as _NP
    try:
        from dash.db_runner.migrate import _direct_db_url as _ddu
        _lock_eng = _sa_create_engine(_ddu(), poolclass=_NP)
    except Exception:
        _lock_eng = _sa_create_engine(db_url, poolclass=_NP)
    _lock_conn = _lock_eng.connect()
    try:
        _lock_conn.execute(text("SELECT pg_advisory_lock(72157424)"))
        _bootstrap_tables_locked()
    finally:
        try:
            _lock_conn.execute(text("SELECT pg_advisory_unlock(72157424)"))
        except Exception:
            pass
        try:
            _lock_conn.close()
            _lock_eng.dispose()
        except Exception:
            pass


def _bootstrap_tables_locked():
    """Create auth tables if they don't exist. Caller holds the advisory lock."""
    with _engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                api_key TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES public.dash_users(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                expiry BIGINT NOT NULL
            )
        """))
        try:
            conn.execute(text("ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS api_key TEXT"))
        except Exception:
            logger.exception("auth: add api_key column failed")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_projects (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES public.dash_users(id) ON DELETE CASCADE,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                agent_role TEXT DEFAULT '',
                agent_personality TEXT DEFAULT 'friendly',
                schema_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_project_shares (
                id SERIAL PRIMARY KEY,
                project_id INTEGER REFERENCES public.dash_projects(id) ON DELETE CASCADE,
                shared_with_user_id INTEGER REFERENCES public.dash_users(id) ON DELETE CASCADE,
                shared_by TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(project_id, shared_with_user_id)
            )
        """))
        try:
            conn.execute(text("ALTER TABLE public.dash_projects ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN DEFAULT FALSE"))
        except Exception:
            logger.exception("auth: add is_favorite column failed")
        try:
            conn.execute(text("ALTER TABLE public.dash_project_shares ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'viewer'"))
        except Exception:
            logger.exception("auth: add project_shares.role column failed")
        # User profile columns
        for col in [
            "email TEXT", "first_name TEXT", "last_name TEXT", "avatar_url TEXT",
            "department TEXT", "job_title TEXT", "phone TEXT", "bio TEXT",
            "timezone TEXT DEFAULT 'UTC'", "language TEXT DEFAULT 'en'",
            "notification_prefs JSONB DEFAULT '{\"email\": true, \"in_app\": true}'::jsonb",
            "auth_provider TEXT DEFAULT 'local'", "external_id TEXT",
            "is_active BOOLEAN DEFAULT TRUE", "last_login TIMESTAMP",
        ]:
            try:
                conn.execute(text(f"ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS {col}"))
            except Exception:
                logger.exception("auth: add user profile column failed (%s)", col)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_audit_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                message TEXT,
                read BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_schedules (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                cron TEXT NOT NULL DEFAULT '0 8 * * 1',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                output_type TEXT NOT NULL DEFAULT 'dashboard',
                email_to TEXT,
                last_run_at TIMESTAMP,
                last_result JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_suggested_rules (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'business_rule',
                definition TEXT NOT NULL,
                source_session_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_quality_scores (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                session_id TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
                reasoning TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_dashboards (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT 'Dashboard',
                widgets JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_chat_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES public.dash_users(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                project_slug TEXT,
                first_message TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(session_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_memories (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                project_slug TEXT,
                scope TEXT NOT NULL DEFAULT 'project',
                fact TEXT NOT NULL,
                source TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_feedback (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                project_slug TEXT NOT NULL,
                session_id TEXT,
                question TEXT NOT NULL,
                answer TEXT,
                sql_query TEXT,
                rating TEXT NOT NULL DEFAULT 'up',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_annotations (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                annotation TEXT NOT NULL,
                updated_by TEXT,
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(project_slug, table_name, column_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_evals (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                question TEXT NOT NULL,
                expected_sql TEXT NOT NULL,
                last_result TEXT,
                last_score TEXT,
                last_run_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_query_patterns (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                question TEXT NOT NULL,
                sql TEXT NOT NULL,
                uses INTEGER DEFAULT 1,
                last_used TIMESTAMP DEFAULT NOW(),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_training_runs (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                tables_trained INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                steps TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT NOW(),
                finished_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_relationships (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                from_table TEXT NOT NULL,
                from_column TEXT NOT NULL,
                to_table TEXT NOT NULL,
                to_column TEXT NOT NULL,
                rel_type TEXT DEFAULT 'fk',
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'auto',
                UNIQUE(project_slug, from_table, from_column, to_table, to_column)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_table_metadata (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                table_name TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(project_slug, table_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_business_rules_db (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                table_name TEXT NOT NULL,
                rules JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(project_slug, table_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_rules_db (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'business_rule',
                category TEXT DEFAULT 'general',
                definition TEXT NOT NULL,
                source TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(project_slug, rule_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_training_qa (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                table_name TEXT,
                question TEXT NOT NULL,
                sql TEXT,
                answer_template TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_personas (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL UNIQUE,
                persona JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_documents (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT,
                file_type TEXT,
                file_size INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_drift_alerts (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                table_name TEXT NOT NULL,
                alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_workflows_db (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                steps JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # --- Self-Evolution Tables ---
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_proactive_insights (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                user_id INTEGER,
                insight TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                tables_involved TEXT[] DEFAULT '{}',
                sql_used TEXT,
                dismissed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_user_preferences (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                project_slug TEXT NOT NULL,
                preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, project_slug)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_query_plans (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                tables_involved TEXT[] NOT NULL,
                join_strategy TEXT,
                filters_used TEXT,
                success BOOLEAN NOT NULL DEFAULT TRUE,
                execution_time_ms INTEGER,
                question TEXT,
                sql_used TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # ALTER existing tables for Phase 2 compatibility
        try:
            conn.execute(text("ALTER TABLE public.dash_memories ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE"))
        except Exception:
            logger.exception("auth: add memories.archived column failed")
        try:
            conn.execute(text("ALTER TABLE public.dash_workflows_db ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'training'"))
        except Exception:
            logger.exception("auth: add workflows_db.source column failed")
        # Auto-Evolving Instructions table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_evolved_instructions (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                instructions TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                reasoning TEXT,
                chat_count_at_generation INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # --- Phase 3: Advanced Self-Evolution Tables ---
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_meta_learnings (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                error_type TEXT NOT NULL,
                fix_strategy TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_eval_history (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                eval_id INTEGER,
                score TEXT NOT NULL,
                result TEXT,
                run_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_eval_runs (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                passed INTEGER NOT NULL DEFAULT 0,
                partial INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                average_score REAL,
                regression_report TEXT,
                run_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # --- Autogenesis Protocol Tables ---
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_resource_registry (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_count INTEGER DEFAULT 0,
                health_score INTEGER DEFAULT 0,
                staleness_days INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT NOW(),
                metadata JSONB DEFAULT '{}'::jsonb,
                UNIQUE(project_slug, resource_type)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_evolution_runs (
                id SERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                steps_completed JSONB DEFAULT '[]'::jsonb,
                reflect_result TEXT,
                select_result TEXT,
                improve_result TEXT,
                evaluate_result TEXT,
                commit_result TEXT,
                started_at TIMESTAMP DEFAULT NOW(),
                finished_at TIMESTAMP
            )
        """))
        # Version tracking columns
        try:
            conn.execute(text("ALTER TABLE public.dash_memories ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1"))
            conn.execute(text("ALTER TABLE public.dash_memories ADD COLUMN IF NOT EXISTS parent_id INTEGER"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS parent_id INTEGER"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS tables_used TEXT"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS join_strategy TEXT"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS filters TEXT"))
            conn.execute(text("ALTER TABLE public.dash_query_patterns ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'user'"))
            conn.execute(text("ALTER TABLE public.dash_training_runs ADD COLUMN IF NOT EXISTS logs JSONB DEFAULT '[]'::jsonb"))
            conn.execute(text("ALTER TABLE public.dash_rules_db ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1"))
            conn.execute(text("ALTER TABLE public.dash_rules_db ADD COLUMN IF NOT EXISTS previous_definition TEXT"))
        except Exception:
            logger.exception("auth: add version tracking columns failed")
        # Unique constraints to prevent duplicates
        try:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup ON public.dash_memories (project_slug, scope, md5(fact)) WHERE archived IS NULL OR archived = FALSE"))
        except Exception:
            logger.exception("auth: create memories dedup index failed")
        try:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_patterns_dedup ON public.dash_query_patterns (project_slug, md5(sql))"))
        except Exception:
            logger.exception("auth: create patterns dedup index failed")
        try:
            conn.execute(text("ALTER TABLE public.dash_projects ADD CONSTRAINT dash_projects_slug_unique UNIQUE (slug)"))
        except Exception:
            # idempotent — constraint already exists on re-boot; not an error
            logger.debug("auth: projects.slug unique constraint already exists")
        # Smart upload fingerprints
        try:
            conn.execute(text("ALTER TABLE public.dash_table_metadata ADD COLUMN IF NOT EXISTS fingerprint TEXT"))
            conn.execute(text("ALTER TABLE public.dash_table_metadata ADD COLUMN IF NOT EXISTS row_count INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE public.dash_table_metadata ADD COLUMN IF NOT EXISTS col_hash TEXT"))
        except Exception:
            conn.rollback()
        # Per-user scope claims (Phase 1 RLS scope thread)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.dash_user_scopes (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL REFERENCES public.dash_users(id) ON DELETE CASCADE,
                project_slug TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                scope_label TEXT NOT NULL,
                role TEXT DEFAULT 'staff',
                is_default BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, project_slug, scope_id)
            )
        """))
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_scopes_user ON public.dash_user_scopes(user_id, project_slug)"))
        except Exception:
            logger.exception("auth: create user_scopes index failed")
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_presentations (
                    id SERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    user_id INTEGER,
                    title TEXT NOT NULL,
                    version INTEGER DEFAULT 1,
                    thinking JSONB,
                    slides JSONB NOT NULL DEFAULT '[]',
                    source_messages JSONB,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
        except Exception:
            conn.rollback()
        conn.commit()
    # API gateway store-scoping (Phase 0): bind a key to one store + a scope mode.
    #   site_code  — staff/service branch (already queried by the chat SHOP CONTEXT)
    #   store_id   — store the API key is bound to (defaults to site_code if unset)
    #   scope_mode — 'store' (Tier-1 own / Tier-2 masked others) | 'global' (no mask)
    # Own connection + commit so a rollback elsewhere in bootstrap can't discard them.
    try:
        with _engine.connect() as _sc_conn:
            for _col, _ddl in (
                ("site_code",  "ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS site_code TEXT"),
                ("store_id",   "ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS store_id TEXT"),
                ("store_ids",  "ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS store_ids TEXT"),
                ("scope_mode", "ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS scope_mode TEXT DEFAULT 'global'"),
                # Access tier: 'user' (default) | 'admin' (day-to-day ops). super = SUPER_ADMIN username.
                ("role",       "ALTER TABLE public.dash_users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'"),
            ):
                try:
                    _sc_conn.execute(text(_ddl))
                except Exception:
                    logger.exception("auth: add %s column failed", _col)
            _sc_conn.commit()
    except Exception:
        logger.exception("auth: store-scope columns bootstrap failed")
    try:
        # Lazy import — avoid circular dep with dash.policy.loader → app.auth.
        from dash.policy.loader import _ensure_visibility_policy_table
        _ensure_visibility_policy_table()
    except Exception:
        logger.exception("auth: ensure visibility policy table failed")


def _create_default_admin():
    """Ensure the SUPER_ADMIN env account exists AND is privileged (role='super').

    Self-healing on EVERY boot so a fresh AWS deploy where the operator only sets
    SUPER_ADMIN / SUPER_ADMIN_PASS in env (no manual SQL) gets a working super-admin
    with Upload + Force-Train rights immediately. Upload/train visibility derives from
    the project role, which derives from is_super/is_admin, which derives from this
    account being role='super' — so if the seed isn't privileged, the buttons vanish.

    - Account missing       -> create with env password + role='super'.
    - Account exists, role<super -> promote to 'super' (heals an old 'user'-role seed).
    - Password is only (re)set on create. To force-resync it from env on an existing
      account (e.g. operator forgot it), set SUPER_ADMIN_RESET_PASS=1 — otherwise a
      password changed in the UI survives reboots.
    """
    import os
    admin_user = os.getenv("SUPER_ADMIN", "admin")
    admin_pass = os.getenv("SUPER_ADMIN_PASS", admin_user)  # default password = username
    reset_pass = os.getenv("SUPER_ADMIN_RESET_PASS", "0").strip().lower() in ("1", "true", "yes")
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, COALESCE(role, 'user') FROM public.dash_users WHERE username = :u"
            ), {"u": admin_user}).fetchone()
            if not row:
                conn.execute(text(
                    "INSERT INTO public.dash_users (username, password_hash, role) "
                    "VALUES (:u, :p, 'super')"
                ), {"u": admin_user, "p": _hash_password(admin_pass)})
                conn.commit()
                logger.info("auth: seeded super-admin '%s' (role=super)", admin_user)
                return
            uid, role = row[0], row[1]
            if role != "super":
                conn.execute(text(
                    "UPDATE public.dash_users SET role = 'super' WHERE id = :i"
                ), {"i": uid})
                logger.info("auth: promoted '%s' to role=super (was '%s')", admin_user, role)
            if reset_pass:
                conn.execute(text(
                    "UPDATE public.dash_users SET password_hash = :p WHERE id = :i"
                ), {"i": uid, "p": _hash_password(admin_pass)})
                logger.info("auth: reset super-admin '%s' password from env", admin_user)
            conn.commit()
    except Exception:
        logger.exception("auth: ensure super-admin failed")


def init_auth():
    """Initialize auth tables and default user. Call on app startup."""
    _bootstrap_tables()
    _create_default_admin()


def _extract_aad_groups(token: str) -> list:
    """§9: extract `groups` claim from JWT if present, else []. Non-breaking."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return []
        import base64 as _b64
        import json as _json

        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64).decode("utf-8", "ignore"))
        groups = payload.get("groups")
        if isinstance(groups, list):
            return [str(g) for g in groups]
        return []
    except Exception:
        return []


def validate_token(token: str) -> Optional[dict]:
    """Validate a token. Returns {user_id, username, aad_groups, ...} or None.

    §9: `aad_groups` populated from JWT `groups` claim if token is a JWT, else [].
    Backward compatible — existing tokens get an empty list.
    """
    # Check cache (thread-safe read)
    with _token_cache_lock:
        if token in _token_cache:
            info = _token_cache[token]
            _fresh = (time.time() - info.get("cached_at", 0)) < _TOKEN_CACHE_FRESH_TTL
            if info["expiry"] > time.time() and _fresh:
                if "aad_groups" not in info:
                    info["aad_groups"] = _extract_aad_groups(token)
                if "is_admin" not in info:
                    info["is_admin"] = info.get("is_super", False)
                return info
            else:
                # Expired OR stale → drop and re-check DB (so a logout/revoke on
                # another worker is honored within _TOKEN_CACHE_FRESH_TTL).
                del _token_cache[token]

    # Check DB
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT t.user_id, t.username, t.expiry, COALESCE(u.role, 'user') "
                "FROM public.dash_tokens t LEFT JOIN public.dash_users u ON u.id = t.user_id "
                "WHERE t.token = :t"
            ), {"t": token}).fetchone()
            if row and row[2] > time.time():
                # Super = the SUPER_ADMIN env username OR anyone holding DB role
                # 'super' (self-heal seed guarantees the env account has it). This
                # makes upload/train rights survive an env-username vs DB mismatch.
                _is_super = (row[1] == SUPER_ADMIN) or (row[3] == "super")
                _is_admin = _is_super or (row[3] == "admin")
                info = {"user_id": row[0], "username": row[1], "expiry": row[2], "is_super": _is_super, "is_admin": _is_admin, "aad_groups": _extract_aad_groups(token), "cached_at": time.time()}
                with _token_cache_lock:
                    # Enforce size limit BEFORE inserting
                    now = time.time()
                    expired = [k for k, v in _token_cache.items() if v.get("expiry", 0) < now]
                    for k in expired:
                        del _token_cache[k]
                    if len(_token_cache) >= _TOKEN_CACHE_MAX:
                        # Evict oldest half
                        oldest = sorted(_token_cache, key=lambda k: _token_cache[k].get("expiry", 0))[: len(_token_cache) // 2]
                        for k in oldest:
                            del _token_cache[k]
                    _token_cache[token] = info
                return info
            # Clean expired
            if row:
                conn.execute(text("DELETE FROM public.dash_tokens WHERE token = :t"), {"t": token})
                conn.commit()
    except Exception:
        logger.exception("auth: token validation DB lookup failed")
    return None


def _get_user(request: Request) -> dict:
    """FastAPI dependency: resolve the request user (real dict w/ is_super/is_admin),
    falling back to an anonymous record. Imported by evals_api / reporter_api / skills_api —
    without this they silently fall to an anonymous noop and 403 every gated call."""
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        user = get_current_user(request)
    if not user:
        return {"id": 0, "user_id": 0, "username": "anonymous", "is_super": False, "is_admin": False}
    # normalize id/user_id (get_current_user emits user_id; some consumers read id)
    if "id" not in user and "user_id" in user:
        try:
            user = {**user, "id": user["user_id"]}
        except Exception:
            pass
    return user


def _validate_api_key(key: str) -> Optional[dict]:
    """Validate a dash-key-* API key. Carries the key's store-scope binding
    (site_code/store_id/scope_mode) so the API gateway can enforce the
    three-tier access rule — see dash/api_scope.py."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, username, site_code, store_id, scope_mode, store_ids "
                "FROM public.dash_users WHERE api_key = :k"
            ), {"k": key}).fetchone()
            if row:
                return {
                    "user_id": row[0], "username": row[1],
                    "expiry": float('inf'),
                    "is_super": row[1] == SUPER_ADMIN, "is_admin": row[1] == SUPER_ADMIN, "aad_groups": [],
                    "site_code": row[2], "store_id": row[3],
                    "scope_mode": row[4] or "global",
                    "store_ids": row[5] or "",
                    "via_api_key": True,
                }
    except Exception:
        logger.exception("auth: api key validation failed")
    return None


def resolve_api_scope(user: Optional[dict]) -> Optional["object"]:
    """Build the StoreScope for an API-key user, or None for human/UI sessions.

    store_id falls back to site_code when unset. A user with scope_mode='global'
    (or no binding) yields a non-enforced scope so existing behaviour is unchanged.
    Returns a dash.api_scope.StoreScope (or None). Never raises.
    """
    try:
        if not user or not user.get("via_api_key"):
            return None
        from dash.api_scope import StoreScope
        store = (user.get("store_id") or user.get("site_code") or "").strip()
        ids_raw = (user.get("store_ids") or "").strip()
        stores = tuple(x.strip() for x in ids_raw.split(",") if x.strip())
        if not store and stores:
            store = stores[0]
        mode = (user.get("scope_mode") or "global").strip().lower()
        if (not store and not stores) or mode == "global":
            return StoreScope(store_id=store, stores=stores, mode="global")
        return StoreScope(store_id=store, stores=stores, mode="store")
    except Exception:
        logger.exception("auth: resolve_api_scope failed")
        return None


def get_current_user(request: Request) -> Optional[dict]:
    """Extract user from request Authorization header or API key."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        # Check if it's an API key
        if token.startswith("dash-key-"):
            return _validate_api_key(token)
        return validate_token(token)
    return None


# ---------------------------------------------------------------------------
# Helpers: Audit Logging + Notifications
# ---------------------------------------------------------------------------


def log_action(user: dict | None, action: str, resource_type: str = "", resource_id: str = "", details: str = ""):
    """Log a user action to the audit trail."""
    try:
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_audit_log (user_id, username, action, resource_type, resource_id, details) "
                "VALUES (:uid, :uname, :action, :rtype, :rid, :details)"
            ), {
                "uid": user.get("user_id") if user else None,
                "uname": user.get("username", "") if user else "",
                "action": action, "rtype": resource_type, "rid": resource_id, "details": details,
            })
            conn.commit()
    except Exception:
        logger.exception("auth: audit log insert failed")


def notify_user(user_id: int, title: str, message: str = "", ntype: str = "info"):
    """Create an in-app notification for a user."""
    try:
        with _engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_notifications (user_id, type, title, message) VALUES (:uid, :type, :title, :msg)"
            ), {"uid": user_id, "type": ntype, "title": title, "msg": message})
            conn.commit()
    except Exception:
        logger.exception("auth: notify_user insert failed")


def check_project_permission(user: dict, slug: str, required_role: str = "viewer") -> dict | None:
    """Check if user has permission to access a project. Returns project dict or raises 403."""
    role_levels = {"viewer": 0, "editor": 1, "admin": 2, "owner": 100}

    with _engine.connect() as conn:
        # Check ownership
        row = conn.execute(text(
            "SELECT id, user_id, agent_name FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()

        if not row:
            return None

        # Single-tenant admin tool: the one locked project is shared by everyone who
        # can log in. ANY authenticated user is treated as owner so Upload + Force-
        # Train are always available — no dependency on the SUPER_ADMIN env value, the
        # DB role, or a per-project share. (Project-destructive ops are still blocked
        # by guard_no_project_management in single-agent mode, so 'owner' here is safe.)
        try:
            from dash.single_agent import is_single_agent, locked_slug
            if is_single_agent() and slug == locked_slug():
                return {"project_id": row[0], "role": "owner", "agent_name": row[2]}
        except Exception:
            pass

        # Owner has full access. ANY super-admin is treated as owner of every
        # project (single-tenant admin tool) — not only the one account whose
        # username matches the SUPER_ADMIN env. Without the is_super check, a
        # second super-admin (e.g. 'admin') got no access to the locked project
        # → /api/projects/{slug} 403 → Workspace stuck "loading", Upload/Force-
        # Train-All hidden (canEdit derives from this role).
        if row[1] == user["user_id"] or user.get("username") == SUPER_ADMIN \
                or user.get("is_super") or user.get("is_super_admin"):
            return {"project_id": row[0], "role": "owner", "agent_name": row[2]}

        # Non-super system ADMINS also get edit rights on every project (single-
        # tenant admin tool) — role 'admin' (level 2) is enough to upload/train
        # (required_role 'editor'=1) and shows the canEdit/canAdmin buttons, but
        # is below 'owner' so project-level destructive ops stay super-only.
        if user.get("is_admin"):
            return {"project_id": row[0], "role": "admin", "agent_name": row[2]}

        # Check shared access
        share = conn.execute(text(
            "SELECT role FROM public.dash_project_shares s "
            "JOIN public.dash_projects p ON p.id = s.project_id "
            "WHERE p.slug = :s AND s.shared_with_user_id = :uid"
        ), {"s": slug, "uid": user["user_id"]}).fetchone()

        if not share:
            return None

        share_role = share[0] or "viewer"
        if role_levels.get(share_role, 0) < role_levels.get(required_role, 0):
            return None

        return {"project_id": row[0], "role": share_role, "agent_name": row[2]}


# ---------------------------------------------------------------------------
# Scope claims (Phase 1 — RLS scope thread)
# ---------------------------------------------------------------------------

def get_user_scopes(user_id: int, project_slug: str) -> list[dict]:
    """Return all scopes assigned to a user for a given project."""
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT scope_id, scope_label, role, is_default FROM public.dash_user_scopes "
                "WHERE user_id = :uid AND project_slug = :p ORDER BY is_default DESC, scope_id"
            ), {"uid": user_id, "p": project_slug}).fetchall()
        return [{"scope_id": r[0], "scope_label": r[1], "role": r[2] or "staff",
                 "is_default": bool(r[3])} for r in rows]
    except Exception:
        return []


def validate_scope(user_id: int, project_slug: str, scope_id: str) -> bool:
    """Return True if scope_id belongs to user for project."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM public.dash_user_scopes "
                "WHERE user_id = :uid AND project_slug = :p AND scope_id = :sid"
            ), {"uid": user_id, "p": project_slug, "sid": scope_id}).fetchone()
        return bool(row)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register")
def register(req: RegisterRequest):
    """Register a new user."""
    if not req.username or len(req.username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if not req.password or len(req.password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    try:
        with _engine.connect() as conn:
            # Check exists
            exists = conn.execute(text(
                "SELECT 1 FROM public.dash_users WHERE username = :u"
            ), {"u": req.username}).fetchone()
            if exists:
                raise HTTPException(409, "Username already taken")

            conn.execute(text(
                "INSERT INTO public.dash_users (username, password_hash) VALUES (:u, :p)"
            ), {"u": req.username, "p": _hash_password(req.password)})
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Registration failed: {str(e)}")

    return {"status": "ok", "username": req.username}


@router.post("/login")
def login(req: LoginRequest):
    """Login and get a token."""
    try:
        with _engine.connect() as conn:
            # Accept either the username OR the email as the login identifier —
            # the form labels it "email", so users naturally type the email.
            row = conn.execute(text(
                "SELECT id, username, password_hash, COALESCE(role, 'user') FROM public.dash_users "
                "WHERE username = :u OR LOWER(email) = LOWER(:u) "
                "ORDER BY (username = :u) DESC LIMIT 1"
            ), {"u": req.username}).fetchone()

            if not row or not _verify_password(req.password, row[2]):
                # Security telemetry — failed login (Usage › Security panel).
                try:
                    conn.execute(text(
                        "INSERT INTO public.dash_security_events "
                        "(kind, severity, service_account, detail) "
                        "VALUES ('auth_fail', 'WARN', :u, 'invalid username or password')"
                    ), {"u": req.username})
                    conn.commit()
                except Exception:
                    pass
                raise HTTPException(401, "Invalid username or password")

            # Generate token
            token = secrets.token_urlsafe(32)
            expiry = int(time.time()) + TOKEN_EXPIRY

            conn.execute(text(
                "INSERT INTO public.dash_tokens (token, user_id, username, expiry) VALUES (:t, :uid, :u, :e)"
            ), {"t": token, "uid": row[0], "u": row[1], "e": expiry})
            conn.commit()

            is_super = (row[1] == SUPER_ADMIN) or (row[3] == "super")
            is_admin = is_super or (row[3] == "admin")
            _token_cache[token] = {"user_id": row[0], "username": row[1], "expiry": expiry, "is_super": is_super, "is_admin": is_admin, "cached_at": time.time()}

            _surfaces = surfaces_for({"is_super": is_super, "is_admin": is_admin})
            return {"status": "ok", "token": token, "username": row[1], "user_id": row[0], "is_super": is_super, "is_admin": is_admin, "surfaces": _surfaces}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Login failed: {str(e)}")


@router.get("/check")
def check(request: Request):
    """Check if current token is valid. Echoes active scope if X-Scope-Id was honored."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    active_scope_id = getattr(getattr(request, "state", None), "scope_id", None)
    return {
        "status": "ok",
        "username": user["username"],
        "user_id": user["user_id"],
        "is_super": user.get("is_super", False),
        "is_admin": user.get("is_admin", user.get("is_super", False)),
        "surfaces": surfaces_for(user),
        "active_scope_id": active_scope_id,
    }


# ---------------------------------------------------------------------------
# Scope endpoints
# ---------------------------------------------------------------------------

@router.get("/scopes")
def list_scopes(request: Request, project_slug: str):
    """Return scopes the current user has on a project."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not project_slug:
        raise HTTPException(400, "project_slug is required")
    return {"scopes": get_user_scopes(user["user_id"], project_slug)}


class ScopeSeedItem(BaseModel):
    scope_id: str
    scope_label: str
    role: str = "staff"
    is_default: bool = False


class ScopeSeedRequest(BaseModel):
    user_id: int
    project_slug: str
    scopes: list[ScopeSeedItem]


@router.post("/scopes/seed")
def seed_scopes(req: ScopeSeedRequest, request: Request):
    """Admin-only: bulk-insert scope claims for a user. Idempotent (UPSERT)."""
    _require_super(request)
    if not req.scopes:
        return {"status": "ok", "inserted": 0}
    inserted = 0
    try:
        with _engine.connect() as conn:
            for s in req.scopes:
                conn.execute(text(
                    "INSERT INTO public.dash_user_scopes "
                    "(user_id, project_slug, scope_id, scope_label, role, is_default) "
                    "VALUES (:uid, :p, :sid, :slab, :role, :def) "
                    "ON CONFLICT (user_id, project_slug, scope_id) DO UPDATE "
                    "SET scope_label = EXCLUDED.scope_label, role = EXCLUDED.role, "
                    "is_default = EXCLUDED.is_default"
                ), {"uid": req.user_id, "p": req.project_slug, "sid": s.scope_id,
                    "slab": s.scope_label, "role": s.role, "def": s.is_default})
                inserted += 1
            conn.commit()
    except Exception as e:
        raise HTTPException(500, f"Seed failed: {e}")
    # Phase G — auto-learn scope terminology into project Brain (non-critical).
    try:
        from dash.policy.scope_brain import auto_learn_scopes
        auto_learn_scopes(req.project_slug, [(s.scope_id, s.scope_label) for s in req.scopes])
    except Exception:
        logger.exception("auth: auto_learn_scopes failed")
    return {"status": "ok", "inserted": inserted}


# ── RBAC surface access (super-admin configurable) ──────────────────────────
# Role → which surfaces are visible. Super admin is ALWAYS full. The "admin" and
# "user" rows are read from the `rbac_surface_access` admin setting (super-editable
# in Command Center → Admin Settings). Enforced both in nav (hide) and here (403).
# 7 surfaces. admin_console = Command Center governance; users_access = Users &
# Access; usage_cost = Usage & Cost; workspace = project settings; integration =
# API Gateway + Embed. Defaults: admin = everything EXCEPT admin_console; user =
# dashboard + chat only.
_SURFACES = ("dashboard", "chat", "workspace", "integration", "admin_console", "users_access", "usage_cost")
_ALL_SURFACES = {s: True for s in _SURFACES}
_SURFACE_DEFAULTS = {
    "admin": {"dashboard": True, "chat": True, "workspace": True, "integration": True, "admin_console": False, "users_access": True,  "usage_cost": True},
    "user":  {"dashboard": True, "chat": True, "workspace": False, "integration": False, "admin_console": False, "users_access": False, "usage_cost": False},
}


def surfaces_for(user: Optional[dict]) -> dict:
    """Resolve the surface-visibility map for a user. Super → all true.
    Fail-soft: any error falls back to role defaults."""
    if not user:
        return {s: False for s in _SURFACES}
    if user.get("is_super"):
        return dict(_ALL_SURFACES)
    # Single-tenant admin tool: there is ONE shared agent/project. Every account
    # that can log in is operational staff, so grant the work surfaces (dashboard,
    # chat, workspace, integration) to any authenticated user — this makes Upload +
    # Force-Train always reachable regardless of which account / role / env. The
    # genuinely admin-only surfaces (admin console, user management, usage/cost) stay
    # role-gated below.
    try:
        from dash.single_agent import is_single_agent
        if is_single_agent():
            base = {s: False for s in _SURFACES}
            for s in ("dashboard", "chat", "workspace", "integration"):
                base[s] = True
            key_admin = "admin" if (user.get("is_admin") or user.get("is_super")) else "user"
            dflt_admin = _SURFACE_DEFAULTS[key_admin]
            for s in ("admin_console", "users_access", "usage_cost"):
                base[s] = bool(dflt_admin.get(s, False))
            return base
    except Exception:
        pass
    key = "admin" if user.get("is_admin") else "user"
    cfg = None
    try:
        from dash.admin.settings import get_setting
        cfg = get_setting("rbac_surface_access")
    except Exception:
        cfg = None
    if not isinstance(cfg, dict):
        cfg = _SURFACE_DEFAULTS
    row = cfg.get(key)
    if not isinstance(row, dict):
        row = _SURFACE_DEFAULTS[key]
    dflt = _SURFACE_DEFAULTS[key]
    return {s: bool(row.get(s, dflt.get(s, False))) for s in _SURFACES}


def _require_super(request: Request):
    """Raise 403 if not super admin."""
    user = get_current_user(request)
    if not user or not user.get("is_super"):
        raise HTTPException(403, "Super admin access required")
    return user


def _require_surface(request: Request, surface: str):
    """Raise 403 if the user's role lacks access to the named surface.
    Super admin always passes."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(403, "Access required")
    if user.get("is_super"):
        return user
    if not surfaces_for(user).get(surface):
        raise HTTPException(403, f"{surface} access not permitted for your role")
    return user


def _require_admin(request: Request):
    """Raise 403 unless the user's role may see the Admin Console surface.
    Super admin always passes; admin/user gated by the rbac_surface_access matrix."""
    return _require_surface(request, "admin_console")


def _guard_admin_target(request: Request, target_username: str):
    """For destructive user-management ops: a plain admin (not super) may NOT
    act on the super admin or on any admin-tier account. Super admin can do anything."""
    actor = get_current_user(request)
    if actor and actor.get("is_super"):
        return  # super can touch anyone
    if target_username == SUPER_ADMIN:
        raise HTTPException(403, "Only the super admin can manage the super admin account")
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COALESCE(role, 'user') FROM public.dash_users WHERE username = :u"
            ), {"u": target_username}).fetchone()
        if row and (row[0] in ("admin", "super")):
            raise HTTPException(403, "Only the super admin can manage admin-tier accounts")
    except HTTPException:
        raise
    except Exception:
        logger.exception("auth: admin-target guard failed for %s", target_username)


def _clear_user_token_cache(username: str):
    """Drop cached tokens for a username so a role change takes effect immediately."""
    try:
        with _token_cache_lock:
            stale = [t for t, info in _token_cache.items() if info.get("username") == username]
            for t in stale:
                del _token_cache[t]
    except Exception:
        logger.exception("auth: clear token cache failed for %s", username)


@router.get("/users")
def list_users(request: Request):
    """List all users with full profiles. Admin tier (admin or super)."""
    _require_surface(request, "users_access")
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, username, email, first_name, last_name, department, job_title, "
                "auth_provider, is_active, last_login, created_at, COALESCE(role, 'user'), "
                "external_id, site_code "
                "FROM public.dash_users ORDER BY id"
            )).fetchall()
            users = []
            for row in rows:
                u = {
                    "id": row[0], "username": row[1], "email": row[2],
                    "first_name": row[3], "last_name": row[4],
                    "department": row[5], "job_title": row[6],
                    "auth_provider": row[7] or "local",
                    "is_active": row[8] if row[8] is not None else True,
                    "last_login": str(row[9]) if row[9] else None,
                    "created_at": str(row[10]) if row[10] else None,
                    "is_super": row[1] == SUPER_ADMIN,
                    "role": "super" if row[1] == SUPER_ADMIN else (row[11] or "user"),
                    "external_id": row[12], "site_code": row[13],
                }
                pc = conn.execute(text("SELECT COUNT(*) FROM public.dash_projects WHERE user_id = :uid"), {"uid": row[0]}).scalar() or 0
                u["project_count"] = pc
                users.append(u)
            return {"users": users, "super_admin": SUPER_ADMIN}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/users/{username}/role")
def set_user_role(request: Request, username: str, role: str):
    """Grant/revoke the 'admin' tier for a user. Super admin only.
    role ∈ {user, admin}. The super admin is a fixed username and cannot be changed here."""
    _require_super(request)
    role = (role or "").strip().lower()
    if role not in ("user", "admin"):
        raise HTTPException(400, "role must be 'user' or 'admin'")
    if username == SUPER_ADMIN:
        raise HTTPException(400, "Cannot change the super admin's role")
    with _engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if not exists:
            raise HTTPException(404, f"User '{username}' not found")
        conn.execute(text("UPDATE public.dash_users SET role = :r WHERE username = :u"), {"r": role, "u": username})
        conn.commit()
    _clear_user_token_cache(username)
    admin_user = getattr(getattr(request, 'state', None), 'user', None)
    log_action(admin_user, "set_user_role", "user", username, f"role={role}")
    return {"status": "ok", "username": username, "role": role}


@router.post("/users/create")
def create_user(request: Request, username: str, password: str, email: str = "", role: str = "user"):
    """Create a new user (users_access surface required).

    role defaults to 'user'. role='admin' is honored ONLY for the super admin —
    a plain admin asking for an admin gets 403 (admins cannot mint admins). Any
    other role value is rejected. Super may also elevate later via set_user_role.
    """
    _require_surface(request, "users_access")
    if not username or len(username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if not password or len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    role = (role or "user").strip().lower()
    if role not in ("user", "admin"):
        raise HTTPException(400, "role must be 'user' or 'admin'")
    if role == "admin":
        creator = getattr(getattr(request, 'state', None), 'user', None)
        if not (creator and creator.get("is_super")):
            raise HTTPException(403, "Only the super admin can create admin users")

    with _engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if exists:
            raise HTTPException(409, f"User '{username}' already exists")
        conn.execute(text(
            "INSERT INTO public.dash_users (username, password_hash, email, role) "
            "VALUES (:u, :p, :e, :r)"
        ), {"u": username, "p": _hash_password(password), "e": email or None, "r": role})
        conn.commit()

    admin_user = getattr(getattr(request, 'state', None), 'user', None)
    log_action(admin_user, "create_user", "user", username, f"email={email} role={role}")
    return {"status": "ok", "username": username, "role": role}


@router.get("/users/{username}/profile")
def get_user_profile(username: str, request: Request):
    """Get full user profile. Super admin or self."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401)
    if user.get("username") != username and user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Access denied")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, username, email, first_name, last_name, avatar_url, department, "
            "job_title, phone, bio, timezone, language, notification_prefs, auth_provider, "
            "is_active, last_login, created_at FROM public.dash_users WHERE username = :u"
        ), {"u": username}).fetchone()

    if not row:
        raise HTTPException(404, "User not found")

    return {
        "id": row[0], "username": row[1], "email": row[2],
        "first_name": row[3], "last_name": row[4], "avatar_url": row[5],
        "department": row[6], "job_title": row[7], "phone": row[8],
        "bio": row[9], "timezone": row[10], "language": row[11],
        "notification_prefs": row[12], "auth_provider": row[13] or "local",
        "is_active": row[14] if row[14] is not None else True,
        "last_login": str(row[15]) if row[15] else None,
        "created_at": str(row[16]) if row[16] else None,
    }


@router.put("/users/{username}/profile")
def update_user_profile(username: str, request: Request,
                        email: str = "", first_name: str = "", last_name: str = "",
                        department: str = "", job_title: str = "", phone: str = "",
                        bio: str = "", timezone: str = "", language: str = ""):
    """Update user profile. Super admin or self."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401)
    if user.get("username") != username and user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Access denied")

    updates = []
    params: dict = {"u": username}
    for field, val in [("email", email), ("first_name", first_name), ("last_name", last_name),
                       ("department", department), ("job_title", job_title), ("phone", phone),
                       ("bio", bio), ("timezone", timezone), ("language", language)]:
        if val is not None:
            updates.append(f"{field} = :{field}")
            params[field] = val or None

    if updates:
        with _engine.connect() as conn:
            conn.execute(text(f"UPDATE public.dash_users SET {', '.join(updates)} WHERE username = :u"), params)
            conn.commit()

    return {"status": "ok"}


@router.post("/users/{username}/toggle-active")
def toggle_user_active(username: str, request: Request):
    """Enable/disable a user. Admin tier; plain admins cannot touch admin-tier accounts."""
    _require_surface(request, "users_access")
    if username == SUPER_ADMIN:
        raise HTTPException(403, "Cannot disable super admin")
    _guard_admin_target(request, username)

    with _engine.connect() as conn:
        row = conn.execute(text("SELECT is_active FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if not row:
            raise HTTPException(404)
        new_val = not (row[0] if row[0] is not None else True)
        conn.execute(text("UPDATE public.dash_users SET is_active = :v WHERE username = :u"), {"v": new_val, "u": username})
        conn.commit()

    admin_user = getattr(getattr(request, 'state', None), 'user', None)
    log_action(admin_user, "toggle_user", "user", username, f"active={new_val}")
    return {"status": "ok", "is_active": new_val}


# ---------------------------------------------------------------------------
# Keycloak OIDC Integration
# ---------------------------------------------------------------------------

_KEYCLOAK_URL = getenv("KEYCLOAK_URL", "")
_KEYCLOAK_REALM = getenv("KEYCLOAK_REALM", "dash")
_KEYCLOAK_CLIENT_ID = getenv("KEYCLOAK_CLIENT_ID", "dash-app")
_KEYCLOAK_CLIENT_SECRET = getenv("KEYCLOAK_CLIENT_SECRET", "")


@router.get("/oidc/config")
def oidc_config():
    """Check if OIDC/Keycloak is enabled."""
    return {"enabled": bool(_KEYCLOAK_URL), "provider": "keycloak" if _KEYCLOAK_URL else None}


@router.get("/oidc/login")
def oidc_login(redirect_uri: str = ""):
    """Get Keycloak login URL."""
    if not _KEYCLOAK_URL:
        raise HTTPException(400, "OIDC not configured")
    callback = redirect_uri or "/api/auth/oidc/callback"
    url = (
        f"{_KEYCLOAK_URL}/realms/{_KEYCLOAK_REALM}/protocol/openid-connect/auth"
        f"?client_id={_KEYCLOAK_CLIENT_ID}&response_type=code&scope=openid+email+profile"
        f"&redirect_uri={callback}"
    )
    return {"url": url}


@router.get("/oidc/callback")
def oidc_callback(code: str, request: Request):
    """Handle Keycloak callback, create/update local user, return Dash token."""
    if not _KEYCLOAK_URL:
        raise HTTPException(400, "OIDC not configured")

    import httpx

    # Exchange code for token
    token_url = f"{_KEYCLOAK_URL}/realms/{_KEYCLOAK_REALM}/protocol/openid-connect/token"
    try:
        resp = httpx.post(token_url, data={
            "grant_type": "authorization_code",
            "client_id": _KEYCLOAK_CLIENT_ID,
            "client_secret": _KEYCLOAK_CLIENT_SECRET,
            "code": code,
            "redirect_uri": str(request.url).split("?")[0],
        }, timeout=10)
        token_data = resp.json()
    except Exception as e:
        raise HTTPException(500, f"Token exchange failed: {e}")

    if "access_token" not in token_data:
        raise HTTPException(400, token_data.get("error_description", "Token exchange failed"))

    # Get user info
    userinfo_url = f"{_KEYCLOAK_URL}/realms/{_KEYCLOAK_REALM}/protocol/openid-connect/userinfo"
    try:
        resp = httpx.get(userinfo_url, headers={"Authorization": f"Bearer {token_data['access_token']}"}, timeout=10)
        userinfo = resp.json()
    except Exception as e:
        raise HTTPException(500, f"User info failed: {e}")

    kc_username = userinfo.get("preferred_username", userinfo.get("sub", ""))
    kc_email = userinfo.get("email", "")
    kc_first = userinfo.get("given_name", "")
    kc_last = userinfo.get("family_name", "")
    kc_sub = userinfo.get("sub", "")

    if not kc_username:
        raise HTTPException(400, "No username in Keycloak response")

    # Create or update local user
    with _engine.connect() as conn:
        existing = conn.execute(text("SELECT id FROM public.dash_users WHERE username = :u"), {"u": kc_username}).fetchone()
        if existing:
            conn.execute(text(
                "UPDATE public.dash_users SET email = :e, first_name = :fn, last_name = :ln, "
                "auth_provider = 'keycloak', external_id = :sub, last_login = NOW() WHERE username = :u"
            ), {"e": kc_email, "fn": kc_first, "ln": kc_last, "sub": kc_sub, "u": kc_username})
        else:
            conn.execute(text(
                "INSERT INTO public.dash_users (username, password_hash, email, first_name, last_name, "
                "auth_provider, external_id) VALUES (:u, :p, :e, :fn, :ln, 'keycloak', :sub)"
            ), {"u": kc_username, "p": _hash_password(secrets.token_urlsafe(32)), "e": kc_email,
                "fn": kc_first, "ln": kc_last, "sub": kc_sub})
        conn.commit()

        # Get user_id
        row = conn.execute(text("SELECT id FROM public.dash_users WHERE username = :u"), {"u": kc_username}).fetchone()
        user_id = row[0]

        # Create Dash token
        dash_token = secrets.token_urlsafe(32)
        expiry = int(time.time()) + TOKEN_EXPIRY
        conn.execute(text(
            "INSERT INTO public.dash_tokens (token, user_id, username, expiry) VALUES (:t, :uid, :u, :e)"
        ), {"t": dash_token, "uid": user_id, "u": kc_username, "e": expiry})
        conn.commit()

    log_action({"user_id": user_id, "username": kc_username}, "oidc_login", "user", kc_username)

    # Redirect to frontend with token
    from starlette.responses import RedirectResponse
    return RedirectResponse(url=f"/ui/projects?token={dash_token}&username={kc_username}")


@router.delete("/users/{username}")
def delete_user(username: str, request: Request):
    """Delete a user and their schema. Admin tier; plain admins cannot delete admin-tier accounts."""
    _require_surface(request, "users_access")
    if username == SUPER_ADMIN:
        raise HTTPException(403, "Cannot delete super admin")
    _guard_admin_target(request, username)

    try:
        with _engine.connect() as conn:
            # Drop user schema. SANITIZE the username before interpolating it into
            # DDL — it is a request path param and the app DB role is superuser, so
            # an unescaped `"` could break out and DROP an arbitrary schema (e.g.
            # citypharma → total data loss). Whitelist [a-z0-9_] only.
            _safe_user = _re_auth.sub(r"[^a-z0-9_]", "_", username.lower())[:63]
            schema_name = f"user_{_safe_user}"
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            # Delete tokens
            conn.execute(text("DELETE FROM public.dash_tokens WHERE username = :u"), {"u": username})
            # Delete user
            conn.execute(text("DELETE FROM public.dash_users WHERE username = :u"), {"u": username})
            conn.commit()
        return {"status": "ok", "deleted": username}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/change-password")
def change_password(request: Request, old_password: str, new_password: str):
    """Change current user's password."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if len(new_password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT password_hash FROM public.dash_users WHERE id = :uid"
        ), {"uid": user["user_id"]}).fetchone()
        if not row or not _verify_password(old_password, row[0]):
            raise HTTPException(403, "Current password is incorrect")
        conn.execute(text(
            "UPDATE public.dash_users SET password_hash = :p WHERE id = :uid"
        ), {"p": _hash_password(new_password), "uid": user["user_id"]})
        conn.commit()
    return {"status": "ok"}


@router.post("/users/{username}/reset-password")
def reset_password(username: str, new_password: str, request: Request):
    """Reset a user's password. Admin tier; plain admins cannot reset admin-tier accounts."""
    _require_surface(request, "users_access")
    _guard_admin_target(request, username)
    if len(new_password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    with _engine.connect() as conn:
        result = conn.execute(text(
            "UPDATE public.dash_users SET password_hash = :p WHERE username = :u"
        ), {"p": _hash_password(new_password), "u": username})
        if result.rowcount == 0:
            raise HTTPException(404, "User not found")
        conn.commit()
    return {"status": "ok"}


@router.get("/api-key")
def get_api_key(request: Request):
    """Get current user's API key."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT api_key FROM public.dash_users WHERE id = :uid"
        ), {"uid": user["user_id"]}).fetchone()
        key = row[0] if row and row[0] else None
        if not key:
            key = f"dash-key-{secrets.token_urlsafe(24)}"
            conn.execute(text("UPDATE public.dash_users SET api_key = :k WHERE id = :uid"), {"k": key, "uid": user["user_id"]})
            conn.commit()
    return {"api_key": key}


@router.post("/api-key/regenerate")
def regenerate_api_key(request: Request):
    """Regenerate current user's API key."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    key = f"dash-key-{secrets.token_urlsafe(24)}"
    with _engine.connect() as conn:
        conn.execute(text("UPDATE public.dash_users SET api_key = :k WHERE id = :uid"), {"k": key, "uid": user["user_id"]})
        conn.commit()
    return {"api_key": key}


# ---------------------------------------------------------------------------
# Service-account API keys (Phase 3) — store-bound, super-admin only
# ---------------------------------------------------------------------------

class ServiceKeyMintRequest(BaseModel):
    service_account_name: str
    store_id: Optional[str] = None          # single-outlet (back-compat)
    store_ids: Optional[list[str]] = None   # multi-outlet SET (preferred)
    scope_mode: str = "store"  # 'store' (tier-scoped) | 'global' (no mask)


class ServiceKeyRevokeRequest(BaseModel):
    key: Optional[str] = None
    service_account_name: Optional[str] = None


def _require_super_session(request: Request):
    """Super-admin gate that REJECTS api-key sessions. Key management must be
    done from a logged-in human super-admin session, never via a dash-key-*
    bearer (which would let a service key mint/rotate other keys)."""
    user = get_current_user(request)
    if not user or not user.get("is_super"):
        raise HTTPException(403, "Super admin access required")
    if user.get("via_api_key"):
        raise HTTPException(403, "API keys cannot manage service accounts; use a logged-in session")
    return user


@router.post("/api-key")
def mint_service_key(req: ServiceKeyMintRequest, request: Request):
    """Mint a store-bound service-account API key. Super admin only.

    Creates/updates a service-account row in dash_users bound to store_id +
    scope_mode, and returns the plaintext dash-key-* ONCE. Keys are stored
    in plaintext in dash_users.api_key (same scheme as the per-user key) so
    _validate_api_key can match them directly.
    """
    admin = _require_super_session(request)

    name = (req.service_account_name or "").strip()
    if not name or len(name) < 2:
        raise HTTPException(400, "service_account_name must be at least 2 characters")
    scope_mode = (req.scope_mode or "store").strip().lower()
    if scope_mode not in ("store", "global"):
        raise HTTPException(400, "scope_mode must be 'store' or 'global'")

    # Build the owned-store SET — accept store_ids[] (preferred) or single store_id.
    raw_ids = list(req.store_ids or [])
    if req.store_id:
        raw_ids.append(req.store_id)
    _seen: set[str] = set()
    ids: list[str] = []
    for x in raw_ids:
        x = (x or "").strip()
        if x and x not in _seen:
            _seen.add(x)
            ids.append(x)
    if scope_mode == "store" and not ids:
        raise HTTPException(400, "at least one store_id is required for store scope")
    primary = ids[0] if ids else ""
    ids_csv = ",".join(ids)

    key = f"dash-key-{secrets.token_urlsafe(24)}"
    # Service accounts are namespaced so they never collide with human logins.
    username = f"svc:{name}"

    try:
        with _engine.connect() as conn:
            existing = conn.execute(text(
                "SELECT id FROM public.dash_users WHERE username = :u"
            ), {"u": username}).fetchone()
            if existing:
                conn.execute(text(
                    "UPDATE public.dash_users "
                    "SET api_key = :k, store_id = :sid, site_code = :sid, "
                    "    store_ids = :sids, scope_mode = :sm "
                    "WHERE username = :u"
                ), {"k": key, "sid": primary, "sids": ids_csv, "sm": scope_mode, "u": username})
                sa_id = existing[0]
            else:
                # password_hash is required NOT NULL; service accounts have no
                # interactive login, so seed an unguessable random hash.
                conn.execute(text(
                    "INSERT INTO public.dash_users "
                    "(username, password_hash, api_key, store_id, site_code, store_ids, scope_mode) "
                    "VALUES (:u, :p, :k, :sid, :sid, :sids, :sm)"
                ), {
                    "u": username, "p": _hash_password(secrets.token_urlsafe(32)),
                    "k": key, "sid": primary, "sids": ids_csv, "sm": scope_mode,
                })
                sa_id = conn.execute(text(
                    "SELECT id FROM public.dash_users WHERE username = :u"
                ), {"u": username}).fetchone()[0]

            # Grant the service account viewer access to the locked project so
            # the gateway's project_chat permission check passes. CityPharma is
            # locked to one project; share it (idempotent).
            try:
                from dash.single_agent import locked_slug as _ls
                _slug = _ls()
            except Exception:
                _slug = "citypharma"
            proj = conn.execute(text(
                "SELECT id, user_id FROM public.dash_projects WHERE slug = :s"
            ), {"s": _slug}).fetchone()
            if proj and proj[1] != sa_id:
                already = conn.execute(text(
                    "SELECT 1 FROM public.dash_project_shares "
                    "WHERE project_id = :pid AND shared_with_user_id = :uid"
                ), {"pid": proj[0], "uid": sa_id}).fetchone()
                if not already:
                    conn.execute(text(
                        "INSERT INTO public.dash_project_shares "
                        "(project_id, shared_with_user_id, shared_by, role) "
                        "VALUES (:pid, :uid, :by, 'viewer')"
                    ), {"pid": proj[0], "uid": sa_id, "by": admin.get("user_id")})
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("auth: mint_service_key failed")
        raise HTTPException(500, f"Failed to mint key: {e}")

    log_action(admin, "mint_service_key", "service_account", name,
               f"stores={ids_csv} scope_mode={scope_mode}")

    return {
        "status": "ok",
        "service_account_name": name,
        "service_account_id": sa_id,
        "store_id": primary,
        "store_ids": ids,
        "scope_mode": scope_mode,
        "api_key": key,  # plaintext — shown ONCE
    }


@router.post("/api-key/revoke")
def revoke_service_key(req: ServiceKeyRevokeRequest, request: Request):
    """Revoke a service-account API key. Super admin only.

    Identify by key OR service_account_name. Clears api_key (disables the key)
    but keeps the service-account row + its store binding for audit/reuse.
    """
    admin = _require_super_session(request)

    key = (req.key or "").strip()
    name = (req.service_account_name or "").strip()
    if not key and not name:
        raise HTTPException(400, "Provide 'key' or 'service_account_name'")

    try:
        with _engine.connect() as conn:
            if key:
                row = conn.execute(text(
                    "SELECT id, username FROM public.dash_users WHERE api_key = :k"
                ), {"k": key}).fetchone()
            else:
                row = conn.execute(text(
                    "SELECT id, username FROM public.dash_users WHERE username = :u"
                ), {"u": f"svc:{name}"}).fetchone()
            if not row:
                raise HTTPException(404, "Service account / key not found")
            sa_id, sa_username = row[0], row[1]
            conn.execute(text(
                "UPDATE public.dash_users SET api_key = NULL WHERE id = :id"
            ), {"id": sa_id})
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("auth: revoke_service_key failed")
        raise HTTPException(500, f"Failed to revoke key: {e}")

    disp = sa_username[4:] if sa_username.startswith("svc:") else sa_username
    log_action(admin, "revoke_service_key", "service_account", disp, "")
    return {"status": "ok", "revoked": disp}


@router.get("/api-keys")
def list_service_keys(request: Request):
    """List store-bound service-account API keys. Super admin only.

    Returns the service accounts (dash_users rows where username starts
    'svc:'), stripping the 'svc:' prefix to a display name. NEVER returns the
    api_key value — only whether it is currently active (non-NULL).
    """
    _require_super_session(request)

    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, username, store_id, scope_mode, "
                "(api_key IS NOT NULL) AS active, created_at, store_ids "
                "FROM public.dash_users "
                "WHERE username LIKE 'svc:%' "
                "ORDER BY username ASC"
            )).fetchall()
    except Exception as e:
        logger.exception("auth: list_service_keys failed")
        raise HTTPException(500, f"Failed to list keys: {e}")

    keys = []
    for r in rows:
        username = r[1] or ""
        name = username[4:] if username.startswith("svc:") else username
        ids_csv = (r[6] or "").strip()
        store_ids = [x.strip() for x in ids_csv.split(",") if x.strip()]
        if not store_ids and r[2]:
            store_ids = [r[2]]
        keys.append({
            "id": r[0],
            "service_account_name": name,
            "store_id": r[2],
            "store_ids": store_ids,
            "scope_mode": r[3],
            "active": bool(r[4]),
            "created_at": r[5].isoformat() if r[5] else None,
        })

    return {"keys": keys}


# ---------------------------------------------------------------------------
# Bulk per-outlet provisioning (Outlets page) — mint 1 store-locked key per
# outlet, list keyed/missing, reveal plaintext for copy/test. Super admin only.
# ---------------------------------------------------------------------------

def _provision_account_name(outlet: str) -> str:
    """Stable service-account name for a single outlet's auto-provisioned key."""
    return f"outlet-{(outlet or '').strip()}"


def _mint_store_key_conn(conn, admin_id, name: str, ids: list[str], scope_mode: str = "store") -> tuple[int, str]:
    """Mint/refresh a service-account key inside an OPEN connection (no commit).
    Mirrors mint_service_key's row + project-share logic. Returns (sa_id, key)."""
    primary = ids[0] if ids else ""
    ids_csv = ",".join(ids)
    key = f"dash-key-{secrets.token_urlsafe(24)}"
    username = f"svc:{name}"
    existing = conn.execute(text(
        "SELECT id FROM public.dash_users WHERE username = :u"
    ), {"u": username}).fetchone()
    if existing:
        conn.execute(text(
            "UPDATE public.dash_users SET api_key = :k, store_id = :sid, site_code = :sid, "
            "store_ids = :sids, scope_mode = :sm WHERE username = :u"
        ), {"k": key, "sid": primary, "sids": ids_csv, "sm": scope_mode, "u": username})
        sa_id = existing[0]
    else:
        conn.execute(text(
            "INSERT INTO public.dash_users "
            "(username, password_hash, api_key, store_id, site_code, store_ids, scope_mode) "
            "VALUES (:u, :p, :k, :sid, :sid, :sids, :sm)"
        ), {"u": username, "p": _hash_password(secrets.token_urlsafe(32)),
            "k": key, "sid": primary, "sids": ids_csv, "sm": scope_mode})
        sa_id = conn.execute(text(
            "SELECT id FROM public.dash_users WHERE username = :u"
        ), {"u": username}).fetchone()[0]
    # viewer-share the locked project so /api/v1 access check passes
    try:
        from dash.single_agent import locked_slug as _ls
        _slug = _ls()
    except Exception:
        _slug = "citypharma"
    proj = conn.execute(text(
        "SELECT id, user_id FROM public.dash_projects WHERE slug = :s"
    ), {"s": _slug}).fetchone()
    if proj and proj[1] != sa_id:
        already = conn.execute(text(
            "SELECT 1 FROM public.dash_project_shares "
            "WHERE project_id = :pid AND shared_with_user_id = :uid"
        ), {"pid": proj[0], "uid": sa_id}).fetchone()
        if not already:
            conn.execute(text(
                "INSERT INTO public.dash_project_shares "
                "(project_id, shared_with_user_id, shared_by, role) "
                "VALUES (:pid, :uid, :by, 'viewer')"
            ), {"pid": proj[0], "uid": sa_id, "by": admin_id})
    return sa_id, key


def _live_outlets(conn) -> list[str]:
    """Distinct site_codes from the latest stock upload (shared resolver)."""
    schema = "citypharma"
    try:
        from dash.tools.table_sync import latest_table_sa, STOCK_COLS
        stock_tbl = latest_table_sa(conn, schema, STOCK_COLS) or "balance_stock_07052026"
        rows = conn.execute(text(
            f'SELECT DISTINCT site_code FROM "{schema}"."{stock_tbl}" '
            "WHERE site_code IS NOT NULL AND site_code <> '' ORDER BY site_code ASC"
        )).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        logger.exception("auth: _live_outlets failed")
        return []


def _auto_provision_missing() -> int:
    """Mint a store-locked key for every live outlet that has no active svc:outlet-<code> key.

    Gated by env APIGW_AUTO_PROVISION (default '1' = on).
    Fully fail-soft: any exception is logged and swallowed; never raises.
    Returns the count of keys minted this call (0 if gate is off or nothing to do).
    """
    if os.getenv("APIGW_AUTO_PROVISION", "1") != "1":
        return 0
    try:
        with _engine.connect() as conn:
            outlets = _live_outlets(conn)
            if not outlets:
                return 0
            # Existing active outlet accounts keyed by outlet code
            acct_rows = conn.execute(text(
                "SELECT username FROM public.dash_users "
                "WHERE username LIKE 'svc:outlet-%' AND api_key IS NOT NULL"
            )).fetchall()
            existing_active: set[str] = set()
            for r in acct_rows:
                uname = r[0] or ""
                if uname.startswith("svc:outlet-"):
                    existing_active.add(uname[len("svc:outlet-"):])
            missing = [o for o in outlets if o not in existing_active]
            if not missing:
                return 0
            # Resolve the locked project owner's user_id so shared_by (TEXT NOT NULL) is valid
            try:
                from dash.single_agent import locked_slug as _ls
                _slug = _ls()
            except Exception:
                _slug = "citypharma"
            owner_row = conn.execute(text(
                "SELECT user_id FROM public.dash_projects WHERE slug = :s LIMIT 1"
            ), {"s": _slug}).fetchone()
            admin_id = str(owner_row[0]) if owner_row and owner_row[0] is not None else "system"
            minted = 0
            for o in missing:
                try:
                    name = _provision_account_name(o)
                    _mint_store_key_conn(conn, admin_id, name, [o], "store")
                    minted += 1
                except Exception:
                    logger.exception("auth: _auto_provision_missing failed for outlet %s", o)
            if minted:
                conn.commit()
            return minted
    except Exception:
        logger.exception("auth: _auto_provision_missing outer error")
        return 0


@router.get("/apigw-provision")
def apigw_provision_list(request: Request):
    """Per-outlet provisioning table: every live outlet joined with its
    auto-provisioned single-outlet key (name 'outlet-<code>') + request count.
    Super admin only. NEVER returns the plaintext key (use /apigw-key-reveal).
    Auto-provisions any missing outlet keys before building the response
    (when APIGW_AUTO_PROVISION=1, the default)."""
    _require_super_session(request)
    # Lazy auto-provision: mint keys for any new outlets before listing
    if os.getenv("APIGW_AUTO_PROVISION", "1") == "1":
        try:
            _auto_provision_missing()
        except Exception:
            logger.exception("auth: lazy auto-provision failed (ignored)")
    rows_out: list[dict] = []
    summary = {"total": 0, "keyed": 0, "missing": 0}
    try:
        with _engine.connect() as conn:
            outlets = _live_outlets(conn)
            # all auto-provisioned outlet accounts in one query
            accts = conn.execute(text(
                "SELECT username, store_id, store_ids, scope_mode, (api_key IS NOT NULL) AS active "
                "FROM public.dash_users WHERE username LIKE 'svc:outlet-%'"
            )).fetchall()
            by_outlet: dict[str, dict] = {}
            for a in accts:
                uname = a[0] or ""
                name = uname[4:] if uname.startswith("svc:") else uname  # outlet-<code>
                code = name[len("outlet-"):] if name.startswith("outlet-") else ""
                if code:
                    by_outlet[code] = {"name": name, "active": bool(a[4]), "scope_mode": a[3]}
            # request counts per service account (best-effort)
            reqs: dict[str, int] = {}
            try:
                rc = conn.execute(text(
                    "SELECT service_account, count(*) FROM public.dash_apigw_usage "
                    "GROUP BY service_account"
                )).fetchall()
                for r in rc:
                    if r[0]:
                        reqs[str(r[0])] = int(r[1])
            except Exception:
                reqs = {}
            for o in outlets:
                acct = by_outlet.get(o)
                keyed = bool(acct and acct["active"])
                rows_out.append({
                    "outlet": o,
                    "name": acct["name"] if acct else _provision_account_name(o),
                    "keyed": keyed,
                    "scope_mode": acct["scope_mode"] if acct else "store",
                    "reqs": reqs.get(f"svc:{acct['name']}", reqs.get(acct["name"], 0)) if acct else 0,
                })
            summary["total"] = len(outlets)
            summary["keyed"] = sum(1 for r in rows_out if r["keyed"])
            summary["missing"] = summary["total"] - summary["keyed"]
    except Exception as e:
        logger.exception("auth: apigw_provision_list failed")
        raise HTTPException(500, f"provision list failed: {e}")
    return {"rows": rows_out, "summary": summary}


@router.post("/apigw-provision-auto")
def apigw_provision_auto(request: Request):
    """Trigger auto-provisioning of store-locked keys for all outlets that have
    no active key yet. Respects the APIGW_AUTO_PROVISION gate (returns minted=0
    when the gate is off). Super admin only. Safe to call from a scheduler/daemon."""
    _require_super_session(request)
    minted = _auto_provision_missing()
    return {"minted": minted}


class ApigwProvisionRequest(BaseModel):
    outlets: Optional[list[str]] = None   # specific outlets; None/empty = all missing
    all_missing: bool = False
    rotate: bool = False                  # re-mint even if already keyed


@router.post("/apigw-provision")
def apigw_provision(req: ApigwProvisionRequest, request: Request):
    """Bulk-mint one store-locked key per outlet. Returns plaintext keys for the
    rows minted THIS call (shown once in UI; also retrievable via reveal).
    Super admin only."""
    admin = _require_super_session(request)
    results: list[dict] = []
    try:
        with _engine.connect() as conn:
            outlets = _live_outlets(conn)
            existing = {r[0][4:][len("outlet-"):]: bool(r[1]) for r in conn.execute(text(
                "SELECT username, (api_key IS NOT NULL) FROM public.dash_users "
                "WHERE username LIKE 'svc:outlet-%'"
            )).fetchall() if (r[0] or "").startswith("svc:outlet-")}
            # decide target set
            want = [x.strip() for x in (req.outlets or []) if x and x.strip()]
            if not want:
                want = list(outlets)  # all
            targets = []
            for o in want:
                if o not in outlets:
                    continue  # only provision real outlets
                if req.rotate or not existing.get(o, False):
                    targets.append(o)
            for o in targets:
                name = _provision_account_name(o)
                sa_id, key = _mint_store_key_conn(conn, admin.get("user_id"), name, [o], "store")
                results.append({"outlet": o, "name": name, "api_key": key})
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("auth: apigw_provision failed")
        raise HTTPException(500, f"provision failed: {e}")
    log_action(admin, "apigw_provision", "service_account", f"{len(results)} keys",
               f"rotate={req.rotate}")
    return {"status": "ok", "minted": len(results), "results": results}


@router.get("/apigw-key-reveal")
def apigw_key_reveal(request: Request, name: str = ""):
    """Reveal the plaintext key for an outlet service account (copy / test /
    .env export). Super admin only. Keys are stored plaintext in dash_users, so
    a super-admin (who can already read the DB) revealing here is consistent."""
    admin = _require_super_session(request)
    nm = (name or "").strip()
    if not nm:
        raise HTTPException(400, "name required")
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT api_key, store_ids, store_id, scope_mode FROM public.dash_users "
                "WHERE username = :u"
            ), {"u": f"svc:{nm}"}).fetchone()
    except Exception as e:
        logger.exception("auth: apigw_key_reveal failed")
        raise HTTPException(500, f"reveal failed: {e}")
    if not row or not row[0]:
        raise HTTPException(404, "no active key for that account")
    log_action(admin, "apigw_key_reveal", "service_account", nm, "")
    ids_csv = (row[1] or "").strip()
    store_ids = [x.strip() for x in ids_csv.split(",") if x.strip()] or ([row[2]] if row[2] else [])
    return {"name": nm, "api_key": row[0], "store_ids": store_ids, "scope_mode": row[3]}


@router.get("/apigw-outlets")
def apigw_outlets(request: Request):
    """Distinct outlet site_codes (for the mint-key outlet picker). Super admin only."""
    _require_super_session(request)
    outlets: list[str] = []
    schema = "citypharma"
    source_table: str | None = None
    updated_at: str | None = None
    row_count: int | None = None
    try:
        with _engine.connect() as conn:
            # Resolve the stock table to the CURRENT upload (latest
            # dash_table_metadata.updated_at) via the shared resolver — same
            # table the pharma agent tools query, so the outlet list never drifts
            # from what the agent actually sees. Data lands as dated tables
            # (balance_stock_DDMMYYYY); a re-upload is picked up automatically.
            from dash.tools.table_sync import latest_table_sa, STOCK_COLS
            stock_tbl = latest_table_sa(conn, schema, STOCK_COLS)
            if not stock_tbl:
                stock_tbl = "balance_stock_07052026"
            source_table = stock_tbl
            # stock_tbl comes from the DB catalog (not user input); still
            # double-quote it to be safe.
            rows = conn.execute(text(
                f'SELECT DISTINCT site_code FROM "{schema}"."{stock_tbl}" '
                "WHERE site_code IS NOT NULL AND site_code <> '' "
                "ORDER BY site_code ASC"
            )).fetchall()
            outlets = [r[0] for r in rows if r[0]]
            # Freshness signal — total stock rows + when the table was last
            # uploaded (NOW() written on every ingest into dash_table_metadata).
            # Lets the Outlets view prove the list reflects the latest upload.
            try:
                row_count = conn.execute(text(
                    f'SELECT count(*) FROM "{schema}"."{stock_tbl}"'
                )).scalar()
            except Exception:
                row_count = None
            try:
                ua = conn.execute(text(
                    "SELECT updated_at FROM public.dash_table_metadata "
                    "WHERE project_slug = :s AND table_name = :t "
                    "ORDER BY updated_at DESC NULLS LAST LIMIT 1"
                ), {"s": schema, "t": stock_tbl}).scalar()
                updated_at = ua.isoformat() if ua else None
            except Exception:
                updated_at = None
    except Exception:
        logger.exception("auth: apigw_outlets query failed")
    return {
        "outlets": outlets,
        "count": len(outlets),
        "source_table": source_table,
        "updated_at": updated_at,
        "row_count": row_count,
    }


# ---------------------------------------------------------------------------
# API Gateway admin (super-admin "API Gateway" page)
# ---------------------------------------------------------------------------

class ApigwConfigRequest(BaseModel):
    rate_per_min: int


def _apigw_rate_per_min() -> int:
    """Read the live rate cap from public.dash_apigw_config (singleton id=1),
    falling back to the API_GW_RATE_PER_MIN env (default 60) on any error."""
    env_default = 60
    try:
        env_default = int(os.getenv("API_GW_RATE_PER_MIN", "60"))
    except (TypeError, ValueError):
        env_default = 60
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT rate_per_min FROM public.dash_apigw_config WHERE id = 1"
            )).fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        logger.exception("auth: apigw config read failed (fallback to env)")
    return env_default


@router.get("/apigw-status")
def apigw_status(request: Request):
    """API Gateway status panel. Super admin only. Fail-soft on redis/db errors."""
    _require_super_session(request)

    redis_up = False
    try:
        import redis as _redis
        _c = _redis.from_url(
            os.getenv("REDIS_URL", "redis://dash-redis:6379"),
            socket_timeout=2, socket_connect_timeout=2,
        )
        _c.ping()
        redis_up = True
    except Exception:
        redis_up = False

    rate_per_min = _apigw_rate_per_min()

    keys_active = 0
    keys_revoked = 0
    try:
        with _engine.connect() as conn:
            keys_active = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_users "
                "WHERE username LIKE 'svc:%' AND api_key IS NOT NULL"
            )).scalar() or 0
            keys_revoked = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_users "
                "WHERE username LIKE 'svc:%' AND api_key IS NULL"
            )).scalar() or 0
    except Exception:
        logger.exception("auth: apigw_status key counts failed")

    return {
        "live": True,
        "model": "citypharma-analyst",
        "redis_up": redis_up,
        "rate_per_min": rate_per_min,
        "keys_active": int(keys_active),
        "keys_revoked": int(keys_revoked),
    }


@router.get("/apigw-config")
def apigw_config_get(request: Request):
    """Current live rate cap. Super admin only."""
    _require_super_session(request)
    return {"rate_per_min": _apigw_rate_per_min()}


@router.post("/apigw-config")
def apigw_config_set(req: ApigwConfigRequest, request: Request):
    """Update the live rate cap (singleton config). Super admin only.
    Takes effect in the gateway within ~10s (TTL cache) without a restart."""
    admin = _require_super_session(request)

    v = int(req.rate_per_min)
    if v < 1 or v > 100000:
        raise HTTPException(400, "rate_per_min must be between 1 and 100000")

    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_apigw_config "
                "SET rate_per_min = :v, updated_at = now() WHERE id = 1"
            ), {"v": v})
            conn.commit()
    except Exception as e:
        logger.exception("auth: apigw_config_set failed")
        raise HTTPException(500, f"Failed to update rate cap: {e}")

    log_action(admin, "apigw_config_set", "apigw", "rate_per_min", f"rate_per_min={v}")
    return {"ok": True, "rate_per_min": v}


@router.get("/apigw-usage")
def apigw_usage(request: Request, days: int = 7):
    """Usage analytics aggregated from public.dash_apigw_usage. Super admin only.

    NOTE: 429 rate-limit rejections are NOT logged in dash_apigw_usage (only
    completed requests are metered), so totals.rate_limited is always 0 here.
    """
    _require_super_session(request)

    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days < 1:
        days = 1
    if days > 365:
        days = 365

    out = {
        "days": days,
        "totals": {"calls": 0, "tokens": 0, "rate_limited": 0},
        "daily": [],
        "by_key": [],
    }

    try:
        with _engine.connect() as conn:
            tot = conn.execute(text(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(total_tokens), 0) AS tokens "
                "FROM public.dash_apigw_usage "
                "WHERE ts > now() - (:days || ' days')::interval"
            ), {"days": days}).fetchone()
            if tot:
                out["totals"]["calls"] = int(tot[0] or 0)
                out["totals"]["tokens"] = int(tot[1] or 0)

            daily_rows = conn.execute(text(
                "SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS d, "
                "       COUNT(*) AS calls, COALESCE(SUM(total_tokens), 0) AS tokens "
                "FROM public.dash_apigw_usage "
                "WHERE ts > now() - (:days || ' days')::interval "
                "GROUP BY date_trunc('day', ts) "
                "ORDER BY date_trunc('day', ts) ASC"
            ), {"days": days}).fetchall()
            out["daily"] = [
                {"date": r[0], "calls": int(r[1] or 0), "tokens": int(r[2] or 0)}
                for r in daily_rows
            ]

            key_rows = conn.execute(text(
                "SELECT service_account, store_id, scope_mode, "
                "       COUNT(*) AS calls, COALESCE(SUM(total_tokens), 0) AS tokens, "
                "       MAX(ts) AS last_ts "
                "FROM public.dash_apigw_usage "
                "WHERE ts > now() - (:days || ' days')::interval "
                "GROUP BY service_account, store_id, scope_mode "
                "ORDER BY calls DESC"
            ), {"days": days}).fetchall()
            by_key = []
            for r in key_rows:
                sa = r[0] or ""
                if sa.startswith("svc:"):
                    sa = sa[4:]
                by_key.append({
                    "service_account": sa,
                    "store_id": r[1],
                    "scope_mode": r[2],
                    "calls": int(r[3] or 0),
                    "tokens": int(r[4] or 0),
                    "last": r[5].isoformat() if r[5] else None,
                })
            out["by_key"] = by_key
    except Exception:
        logger.exception("auth: apigw_usage aggregation failed")

    return out


@router.get("/users/{username}/projects")
def user_projects(username: str, request: Request):
    """List a user's projects with stats. Super admin only."""
    _require_super(request)
    with _engine.connect() as conn:
        user_row = conn.execute(text("SELECT id FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if not user_row:
            raise HTTPException(404, "User not found")

        rows = conn.execute(text(
            "SELECT id, slug, name, agent_name, agent_role, schema_name, created_at FROM public.dash_projects WHERE user_id = :uid ORDER BY created_at DESC"
        ), {"uid": user_row[0]}).fetchall()

        from sqlalchemy import inspect as sa_inspect
        insp = sa_inspect(_engine)
        projects = []
        for r in rows:
            schema = r[5]
            tables = 0
            total_rows = 0
            knowledge = 0
            try:
                tbl_names = insp.get_table_names(schema=schema)
                tables = len(tbl_names)
                for t in tbl_names:
                    try:
                        c = conn.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')).scalar() or 0
                        total_rows += c
                    except Exception:
                        logger.exception("auth: count rows failed for %s.%s", schema, t)
            except Exception:
                logger.exception("auth: list tables failed for schema %s", schema)
            try:
                knowledge = conn.execute(text(f'SELECT COUNT(*) FROM ai."{schema}_knowledge"')).scalar() or 0
            except Exception:
                logger.exception("auth: knowledge count failed for schema %s", schema)

            projects.append({
                "id": r[0], "slug": r[1], "name": r[2], "agent_name": r[3], "agent_role": r[4],
                "schema_name": schema, "tables": tables, "rows": total_rows, "knowledge": knowledge,
                "created_at": str(r[6]) if r[6] else None,
            })
    return {"username": username, "projects": projects}


# ---------------------------------------------------------------------------
# Command Center Admin Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/projects")
def admin_list_projects(request: Request):
    """List ALL projects across all users with details."""
    _require_super(request)
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.slug, p.name, p.agent_name, p.schema_name, p.created_at, p.updated_at, p.user_id,
                   u.username as owner,
                   (SELECT COUNT(*) FROM public.dash_project_shares s WHERE s.project_id = p.id) as shared_count,
                   (SELECT finished_at FROM public.dash_training_runs t WHERE t.project_slug = p.slug AND t.status = 'done' ORDER BY t.finished_at DESC LIMIT 1) as last_trained
            FROM public.dash_projects p
            LEFT JOIN public.dash_users u ON u.id = p.user_id
            ORDER BY p.updated_at DESC
        """)).fetchall()
    projects = []
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(_engine)
    for r in rows:
        tables = 0; total_rows = 0
        try:
            tbl_names = insp.get_table_names(schema=r[4])
            tables = len(tbl_names)
            with _engine.connect() as c:
                for t in tbl_names:
                    try: total_rows += c.execute(text(f'SELECT COUNT(*) FROM "{r[4]}"."{t}"')).scalar() or 0
                    except Exception:
                        logger.exception("auth: admin project row count failed for %s.%s", r[4], t)
        except Exception:
            logger.exception("auth: admin project list tables failed for schema %s", r[4])
        # Get brain health
        brain = {}
        try:
            with _engine.connect() as c:
                for tbl, key in [('dash_memories','memory'), ('dash_query_patterns','pattern'), ('dash_rules_db','rule'), ('dash_evals','eval'), ('dash_workflows_db','workflow'), ('dash_feedback','feedback')]:
                    cnt = c.execute(text(f"SELECT COUNT(*) FROM public.{tbl} WHERE project_slug = :s"), {"s": r[1]}).scalar() or 0
                    brain[key] = cnt
        except Exception:
            logger.exception("auth: brain health count failed for project %s", r[1])
        projects.append({
            "id": r[0], "slug": r[1], "name": r[2], "agent_name": r[3], "schema": r[4],
            "created_at": str(r[5]) if r[5] else None, "updated_at": str(r[6]) if r[6] else None,
            "owner": r[8], "shared_count": r[9], "last_trained": str(r[10]) if r[10] else None,
            "tables": tables, "rows": total_rows, "brain": brain,
        })
    return {"projects": projects}


@router.get("/admin/user/{user_id}/detail")
def admin_user_detail(user_id: int, request: Request):
    """Get deep insights for a specific user."""
    _require_super(request)
    with _engine.connect() as conn:
        user = conn.execute(text("SELECT id, username, email, created_at, last_login FROM public.dash_users WHERE id = :id"), {"id": user_id}).fetchone()
        if not user: raise HTTPException(404, "User not found")

        # Projects owned
        projects = conn.execute(text("SELECT slug, name, agent_name FROM public.dash_projects WHERE user_id = :id"), {"id": user_id}).fetchall()

        # Projects shared with
        shared = conn.execute(text("""
            SELECT p.slug, p.name, s.role, s.shared_by FROM public.dash_project_shares s
            JOIN public.dash_projects p ON p.id = s.project_id WHERE s.shared_with_user_id = :id
        """), {"id": user_id}).fetchall()

        # Chat sessions
        sessions = conn.execute(text("""
            SELECT session_id, project_slug, created_at, updated_at FROM public.dash_chat_sessions
            WHERE user_id = :id ORDER BY updated_at DESC LIMIT 20
        """), {"id": user_id}).fetchall()

        # Feedback given
        feedback_up = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE rating = 'up'")).scalar() or 0
        feedback_down = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE rating = 'down'")).scalar() or 0

        # Audit log
        logs = conn.execute(text("""
            SELECT action, resource_type, resource_id, created_at FROM public.dash_audit_log
            WHERE user_id = :id ORDER BY created_at DESC LIMIT 30
        """), {"id": user_id}).fetchall()

    return {
        "user": {"id": user[0], "username": user[1], "email": user[2], "created_at": str(user[3]) if user[3] else None, "last_login": str(user[4]) if user[4] else None},
        "projects": [{"slug": p[0], "name": p[1], "agent_name": p[2]} for p in projects],
        "shared_with": [{"slug": s[0], "name": s[1], "role": s[2], "shared_by": s[3]} for s in shared],
        "sessions": [{"session_id": s[0], "project": s[1], "created_at": str(s[2]) if s[2] else None, "updated_at": str(s[3]) if s[3] else None} for s in sessions],
        "feedback": {"up": feedback_up, "down": feedback_down},
        "recent_activity": [{"action": l[0], "type": l[1], "resource": l[2], "time": str(l[3]) if l[3] else None} for l in logs],
    }


@router.get("/admin/chat-logs")
def admin_chat_logs(request: Request, project: str = "", user: str = "", limit: int = 50):
    """Get all chat messages across all users/projects."""
    _require_super(request)
    query = """
        SELECT s.session_id, s.project_slug, u.username, s.created_at, s.updated_at, s.first_message
        FROM public.dash_chat_sessions s
        LEFT JOIN public.dash_users u ON u.id = s.user_id
        WHERE 1=1
    """
    params: dict = {}
    if project:
        query += " AND s.project_slug = :proj"
        params["proj"] = project
    if user:
        query += " AND u.username = :user"
        params["user"] = user
    query += f" ORDER BY s.updated_at DESC LIMIT {min(limit, 200)}"

    with _engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return {"logs": [{"session_id": r[0], "project": r[1], "user": r[2], "created_at": str(r[3]) if r[3] else None, "updated_at": str(r[4]) if r[4] else None, "first_message": r[5]} for r in rows]}


@router.get("/admin/schemas")
def admin_schemas(request: Request):
    """List all project schemas with table details."""
    _require_super(request)
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(_engine)
    with _engine.connect() as conn:
        projects = conn.execute(text("SELECT slug, schema_name, user_id FROM public.dash_projects")).fetchall()
        users = {r[0]: r[1] for r in conn.execute(text("SELECT id, username FROM public.dash_users")).fetchall()}

    schemas = []
    for p in projects:
        schema = p[1]
        tables_info = []
        total_rows = 0
        try:
            for t in insp.get_table_names(schema=schema):
                cols = insp.get_columns(t, schema=schema)
                rows = 0
                try:
                    with _engine.connect() as c:
                        rows = c.execute(text(f'SELECT COUNT(*) FROM "{schema}"."{t}"')).scalar() or 0
                except Exception:
                    logger.exception("auth: admin schemas row count failed for %s.%s", schema, t)
                total_rows += rows
                tables_info.append({"name": t, "columns": len(cols), "rows": rows, "col_names": [c["name"] for c in cols[:10]]})
        except Exception:
            logger.exception("auth: admin schemas inspect failed for schema %s", schema)
        schemas.append({"slug": p[0], "schema": schema, "owner": users.get(p[2], "?"), "tables": tables_info, "total_rows": total_rows})
    return {"schemas": schemas}


@router.get("/admin/health")
def admin_health(request: Request):
    """System health check with details."""
    _require_super(request)
    health = {"status": "ok", "services": []}
    # DB check
    try:
        with _engine.connect() as c:
            c.execute(text("SELECT 1"))
        health["services"].append({"name": "PostgreSQL", "status": "online", "detail": "responding"})
    except:
        health["services"].append({"name": "PostgreSQL", "status": "offline", "detail": "connection failed"})
    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        health["services"].append({"name": "Memory", "status": "ok", "detail": f"{mem.used // 1048576} MB / {mem.total // 1048576} MB ({mem.percent}%)"})
    except:
        health["services"].append({"name": "Memory", "status": "unknown"})
    # Disk
    try:
        import psutil
        disk = psutil.disk_usage('/')
        health["services"].append({"name": "Disk", "status": "ok", "detail": f"{disk.used // 1048576} MB / {disk.total // 1048576} MB ({disk.percent}%)"})
    except:
        health["services"].append({"name": "Disk", "status": "unknown"})
    # Workers
    health["services"].append({"name": "Workers", "status": "running", "detail": f"{os.getenv('WORKERS', '4')} uvicorn workers"})
    # Uptime
    try:
        import psutil
        boot = psutil.boot_time()
        uptime = int(time.time() - boot)
        health["uptime"] = f"{uptime // 3600}h {(uptime % 3600) // 60}m"
    except:
        health["uptime"] = "unknown"
    return health


@router.get("/admin/stats")
def admin_stats(request: Request):
    """Platform-wide statistics."""
    _require_super(request)
    with _engine.connect() as conn:
        users = conn.execute(text("SELECT COUNT(*) FROM public.dash_users")).scalar() or 0
        projects = conn.execute(text("SELECT COUNT(*) FROM public.dash_projects")).scalar() or 0
        sessions = conn.execute(text("SELECT COUNT(*) FROM public.dash_chat_sessions")).scalar() or 0
        feedback_up = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE rating = 'up'")).scalar() or 0
        feedback_down = conn.execute(text("SELECT COUNT(*) FROM public.dash_feedback WHERE rating = 'down'")).scalar() or 0
        training_runs = conn.execute(text("SELECT COUNT(*) FROM public.dash_training_runs")).scalar() or 0
        training_done = conn.execute(text("SELECT COUNT(*) FROM public.dash_training_runs WHERE status = 'done'")).scalar() or 0
        training_failed = conn.execute(text("SELECT COUNT(*) FROM public.dash_training_runs WHERE status = 'failed'")).scalar() or 0
        memories = conn.execute(text("SELECT COUNT(*) FROM public.dash_memories")).scalar() or 0
        evals = conn.execute(text("SELECT COUNT(*) FROM public.dash_evals")).scalar() or 0
        workflows = conn.execute(text("SELECT COUNT(*) FROM public.dash_workflows_db")).scalar() or 0
        dashboards = conn.execute(text("SELECT COUNT(*) FROM public.dash_dashboards")).scalar() or 0
        # DB size
        db_size = conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar() or "0"
    return {
        "users": users, "projects": projects, "sessions": sessions,
        "feedback": {"up": feedback_up, "down": feedback_down},
        "training": {"total": training_runs, "done": training_done, "failed": training_failed},
        "content": {"memories": memories, "evals": evals, "workflows": workflows, "dashboards": dashboards},
        "db_size": db_size,
    }


@router.post("/logout")
def logout(request: Request):
    """Invalidate current token."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        _token_cache.pop(token, None)
        try:
            with _engine.connect() as conn:
                conn.execute(text("DELETE FROM public.dash_tokens WHERE token = :t"), {"t": token})
                conn.commit()
        except Exception:
            logger.exception("auth: logout token delete failed")
    return {"status": "ok"}
