"""
Database Module
===============

Database connection utilities.

All connections route through PgBouncer (transaction mode).
Use ``get_engine()`` for ad-hoc queries instead of ``create_engine(db_url)``.
"""

from sqlalchemy import create_engine as _sa_create_engine, Engine
from sqlalchemy.pool import NullPool

from db.session import (
    DASH_SCHEMA,
    create_knowledge,
    create_project_knowledge,
    create_project_learnings,
    create_project_schema,
    create_user_knowledge,
    create_user_learnings,
    create_user_schema,
    get_postgres_db,
    get_project_engine,
    get_project_readonly_engine,
    get_readonly_engine,
    get_sql_engine,
    get_user_engine,
    get_user_readonly_engine,
)
from db.url import db_url

# Shared NullPool engine for ad-hoc queries (PgBouncer handles pooling).
# Use this instead of create_engine(db_url) throughout the app.
_shared_nullpool_engine: Engine | None = None


def get_engine() -> Engine:
    """Return a shared NullPool engine for ad-hoc queries.

    PgBouncer manages server-side pooling, so we use NullPool to avoid
    double-pooling and connection exhaustion.
    """
    global _shared_nullpool_engine
    if _shared_nullpool_engine is None:
        _shared_nullpool_engine = _sa_create_engine(db_url, poolclass=NullPool)
    return _shared_nullpool_engine


__all__ = [
    "DASH_SCHEMA",
    "create_knowledge",
    "create_user_knowledge",
    "create_user_learnings",
    "create_user_schema",
    "db_url",
    "get_engine",
    "get_postgres_db",
    "get_readonly_engine",
    "get_sql_engine",
    "get_user_engine",
    "get_user_readonly_engine",
]
