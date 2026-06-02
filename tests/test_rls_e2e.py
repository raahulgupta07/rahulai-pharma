"""End-to-end RLS test against real Postgres + real rewriter + real audit.

Run with: docker exec dash-api python -m pytest tests/test_rls_e2e.py -v
Or:       python -m pytest tests/test_rls_e2e.py -v   (from inside container)

Tests use a real test schema 'rls_e2e_test' that's set up + torn down per test.
"""
import json, time, uuid, pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from db import db_url

TEST_SLUG = "rls_e2e_test"
TEST_SCHEMA = "rls_e2e_test"


@pytest.fixture
def db():
    eng = create_engine(db_url, poolclass=NullPool)
    yield eng
    eng.dispose()


@pytest.fixture
def setup_project(db):
    """Create test project, schema, sales table with 4 rows across 2 stores."""
    with db.begin() as conn:
        # ensure project exists
        conn.execute(text("""
            INSERT INTO dash_projects (slug, name, agent_name, schema_name, created_at)
            VALUES (:s, :s, :s, :s, NOW())
            ON CONFLICT (slug) DO NOTHING
        """), {"s": TEST_SLUG})
        # ensure rls_config table exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dash_project_rls_config (
                project_slug TEXT PRIMARY KEY,
                enabled BOOL NOT NULL DEFAULT false,
                mode TEXT NOT NULL DEFAULT 'advisory',
                user_attr_keys TEXT[] NOT NULL DEFAULT '{}',
                table_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
                default_deny BOOL NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        # test schema + table
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"'))
        conn.execute(text(f'DROP TABLE IF EXISTS "{TEST_SCHEMA}".sales CASCADE'))
        conn.execute(text(f'''
            CREATE TABLE "{TEST_SCHEMA}".sales (
                id SERIAL PRIMARY KEY,
                store_id INT,
                amount NUMERIC,
                region TEXT
            )
        '''))
        conn.execute(text(f'''
            INSERT INTO "{TEST_SCHEMA}".sales (store_id, amount, region) VALUES
            (1, 100, 'west'), (1, 200, 'west'), (2, 150, 'east'), (2, 250, 'east')
        '''))
    yield
    # teardown
    with db.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        conn.execute(text("DELETE FROM dash_project_rls_config WHERE project_slug=:s"), {"s": TEST_SLUG})
        conn.execute(text("DELETE FROM dash_rls_audit WHERE project_slug=:s"), {"s": TEST_SLUG})


def test_rewrite_isolates_store(db, setup_project):
    """Rewriter injects WHERE store_id=N, only that store's rows returned."""
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters)
            VALUES (:s, true, 'rewrite', ARRAY['store_id'], CAST(:f AS JSONB))
            ON CONFLICT (project_slug) DO UPDATE SET
                enabled=EXCLUDED.enabled, mode=EXCLUDED.mode,
                user_attr_keys=EXCLUDED.user_attr_keys, table_filters=EXCLUDED.table_filters
        """), {"s": TEST_SLUG, "f": json.dumps({"sales": "store_id = :store_id"})})

    from dash.rls.rewriter import rewrite
    sql = f'SELECT id, store_id, amount FROM "{TEST_SCHEMA}".sales ORDER BY id'
    rewritten = rewrite(sql, TEST_SLUG, {"store_id": 1})
    assert "store_id = 1" in rewritten

    with db.connect() as conn:
        rows = conn.execute(text(rewritten)).all()
    assert len(rows) == 2
    assert all(r[1] == 1 for r in rows)


def test_default_deny_blocks_missing_attr(db, setup_project):
    """default_deny + missing user_attr → PermissionError."""
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters, default_deny)
            VALUES (:s, true, 'rewrite', ARRAY['store_id'], CAST(:f AS JSONB), true)
            ON CONFLICT (project_slug) DO UPDATE SET enabled=true, mode='rewrite',
                user_attr_keys=ARRAY['store_id'], table_filters=EXCLUDED.table_filters, default_deny=true
        """), {"s": TEST_SLUG, "f": json.dumps({"sales": "store_id = :store_id"})})

    from dash.rls.rewriter import rewrite
    with pytest.raises(PermissionError):
        rewrite(f'SELECT * FROM "{TEST_SCHEMA}".sales', TEST_SLUG, {})


def test_audit_logged_on_block(db, setup_project):
    """log_rls_event must write to dash_rls_audit on PermissionError block."""
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters, default_deny)
            VALUES (:s, true, 'rewrite', ARRAY['store_id'], CAST(:f AS JSONB), true)
            ON CONFLICT (project_slug) DO UPDATE SET enabled=true, mode='rewrite',
                user_attr_keys=ARRAY['store_id'], table_filters=EXCLUDED.table_filters, default_deny=true
        """), {"s": TEST_SLUG, "f": json.dumps({"sales": "store_id = :store_id"})})

    from dash.rls.rewriter import rewrite
    try:
        rewrite(f'SELECT * FROM "{TEST_SCHEMA}".sales', TEST_SLUG, {})
    except PermissionError:
        pass
    # daemon flusher writes async — wait up to 5s
    found = False
    for _ in range(10):
        time.sleep(0.5)
        with db.connect() as conn:
            row = conn.execute(text(
                "SELECT count(*) FROM dash_rls_audit WHERE project_slug=:s AND blocked=true"
            ), {"s": TEST_SLUG}).scalar()
        if row and row > 0:
            found = True
            break
    assert found, "audit row not written within 5s"


def test_pg_rls_session_var(db, setup_project):
    """Phase 4: pg_rls mode uses SET LOCAL app.store_id and Postgres policy."""
    # PG RLS is bypassed for superuser/BYPASSRLS roles — skip if connected as one.
    with db.connect() as conn:
        privs = conn.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )).first()
    if privs and (privs[0] or privs[1]):
        pytest.skip(f"current_user has SUPERUSER/BYPASSRLS — PG RLS won't enforce")
    # Apply policy
    from dash.rls.pg_setup import apply_policies, remove_policies
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO dash_project_rls_config
                (project_slug, enabled, mode, user_attr_keys, table_filters)
            VALUES (:s, true, 'pg_rls', ARRAY['store_id'], CAST(:f AS JSONB))
            ON CONFLICT (project_slug) DO UPDATE SET enabled=true, mode='pg_rls',
                user_attr_keys=ARRAY['store_id'], table_filters=EXCLUDED.table_filters
        """), {"s": TEST_SLUG, "f": json.dumps({"sales": "(store_id)::text = :store_id"})})

    # Override schema for apply (default uses slug→underscore; we use TEST_SCHEMA)
    result = apply_policies(TEST_SLUG, schema=TEST_SCHEMA)
    assert result["status"] in ("ok", "partial"), f"apply_policies failed: {result}"

    # Without SET LOCAL → should see 0 rows (current_setting returns '' → no match)
    with db.connect() as conn:
        with conn.begin():
            n_no_setting = conn.execute(text(f'SELECT count(*) FROM "{TEST_SCHEMA}".sales')).scalar()
    # With SET LOCAL store_id=1 → 2 rows
    with db.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.store_id = '1'"))
            n_store_1 = conn.execute(text(f'SELECT count(*) FROM "{TEST_SCHEMA}".sales')).scalar()

    # Cleanup
    remove_policies(TEST_SLUG, schema=TEST_SCHEMA)

    assert n_no_setting == 0, f"PG RLS not enforcing: got {n_no_setting} rows without setting"
    assert n_store_1 == 2, f"Expected 2 rows for store_id=1, got {n_store_1}"
