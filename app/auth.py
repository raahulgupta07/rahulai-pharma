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
    """Create auth tables if they don't exist."""
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
            logger.exception("auth: add projects.slug unique constraint failed")
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
    try:
        # Lazy import — avoid circular dep with dash.policy.loader → app.auth.
        from dash.policy.loader import _ensure_visibility_policy_table
        _ensure_visibility_policy_table()
    except Exception:
        logger.exception("auth: ensure visibility policy table failed")


def _create_default_admin():
    """Create admin user if no users exist. Username and password from env vars."""
    import os
    admin_user = os.getenv("SUPER_ADMIN", "admin")
    admin_pass = os.getenv("SUPER_ADMIN_PASS", admin_user)  # default password = username
    with _engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM public.dash_users")).scalar()
        if count == 0:
            conn.execute(text(
                "INSERT INTO public.dash_users (username, password_hash) VALUES (:u, :p)"
            ), {"u": admin_user, "p": _hash_password(admin_pass)})
            conn.commit()


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
            if info["expiry"] > time.time():
                if "aad_groups" not in info:
                    info["aad_groups"] = _extract_aad_groups(token)
                return info
            else:
                del _token_cache[token]

    # Check DB
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT user_id, username, expiry FROM public.dash_tokens WHERE token = :t"
            ), {"t": token}).fetchone()
            if row and row[2] > time.time():
                info = {"user_id": row[0], "username": row[1], "expiry": row[2], "is_super": row[1] == SUPER_ADMIN, "aad_groups": _extract_aad_groups(token)}
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


def _validate_api_key(key: str) -> Optional[dict]:
    """Validate a dash-key-* API key."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, username FROM public.dash_users WHERE api_key = :k"
            ), {"k": key}).fetchone()
            if row:
                return {"user_id": row[0], "username": row[1], "expiry": float('inf'), "is_super": row[1] == SUPER_ADMIN, "aad_groups": []}
    except Exception:
        logger.exception("auth: api key validation failed")
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

        # Owner has full access
        if row[1] == user["user_id"] or user.get("username") == SUPER_ADMIN:
            return {"project_id": row[0], "role": "owner", "agent_name": row[2]}

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
            row = conn.execute(text(
                "SELECT id, username, password_hash FROM public.dash_users WHERE username = :u"
            ), {"u": req.username}).fetchone()

            if not row or not _verify_password(req.password, row[2]):
                raise HTTPException(401, "Invalid username or password")

            # Generate token
            token = secrets.token_urlsafe(32)
            expiry = int(time.time()) + TOKEN_EXPIRY

            conn.execute(text(
                "INSERT INTO public.dash_tokens (token, user_id, username, expiry) VALUES (:t, :uid, :u, :e)"
            ), {"t": token, "uid": row[0], "u": row[1], "e": expiry})
            conn.commit()

            is_super = row[1] == SUPER_ADMIN
            _token_cache[token] = {"user_id": row[0], "username": row[1], "expiry": expiry, "is_super": is_super}

            return {"status": "ok", "token": token, "username": row[1], "user_id": row[0], "is_super": is_super}
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


def _require_super(request: Request):
    """Raise 403 if not super admin."""
    user = get_current_user(request)
    if not user or not user.get("is_super"):
        raise HTTPException(403, "Super admin access required")
    return user


@router.get("/users")
def list_users(request: Request):
    """List all users with full profiles. Super admin only."""
    _require_super(request)
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, username, email, first_name, last_name, department, job_title, "
                "auth_provider, is_active, last_login, created_at FROM public.dash_users ORDER BY id"
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
                }
                pc = conn.execute(text("SELECT COUNT(*) FROM public.dash_projects WHERE user_id = :uid"), {"uid": row[0]}).scalar() or 0
                u["project_count"] = pc
                users.append(u)
            return {"users": users, "super_admin": SUPER_ADMIN}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/users/create")
def create_user(request: Request, username: str, password: str, email: str = "", role: str = "user"):
    """Create a new user. Super admin only."""
    _require_super(request)
    if not username or len(username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if not password or len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    with _engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM public.dash_users WHERE username = :u"), {"u": username}).fetchone()
        if exists:
            raise HTTPException(409, f"User '{username}' already exists")
        conn.execute(text(
            "INSERT INTO public.dash_users (username, password_hash, email) VALUES (:u, :p, :e)"
        ), {"u": username, "p": _hash_password(password), "e": email or None})
        conn.commit()

    admin_user = getattr(getattr(request, 'state', None), 'user', None)
    log_action(admin_user, "create_user", "user", username, f"email={email}")
    return {"status": "ok", "username": username}


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
    """Enable/disable a user. Super admin only."""
    _require_super(request)
    if username == SUPER_ADMIN:
        raise HTTPException(403, "Cannot disable super admin")

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
    """Delete a user and their schema. Super admin only."""
    _require_super(request)
    if username == SUPER_ADMIN:
        raise HTTPException(403, "Cannot delete super admin")

    try:
        with _engine.connect() as conn:
            # Drop user schema
            schema_name = f"user_{username.lower()}"
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
    """Reset a user's password. Super admin only."""
    _require_super(request)
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
