"""End-to-end tests for hybrid_search + RLS on dash_vectors.

Layout in dash_vectors (created on the fly by the fixture if missing):

    project_slug='retail-test'  store_id=catalog (scope_attrs={})
        SKU-123  -> "Widget Blue 10oz"
        SKU-456  -> "Gadget Red 5oz"
    project_slug='retail-test'  inventory rows (scope_attrs.store_id)
        SKU-123-S1 (store 1) -> "SKU-123 stock 50 units store 1"
        SKU-123-S2 (store 2) -> "SKU-123 stock 200 units store 2"

RLS rule: a row is visible iff
    - app.bypass_rls == 'true', OR
    - row.scope_attrs == '{}'::jsonb (catalog), OR
    - app.user_attrs ->> 'store_id' = scope_attrs ->> 'store_id'

Tests run against the live Postgres pointed at by db.db_url.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool

# Force the deterministic hash embedder so tests don't depend on the network.
os.environ.setdefault("EMBEDDINGS_HELPER_FORCE_HASH", "1")

try:
    from db import db_url  # type: ignore
except Exception:  # pragma: no cover
    db_url = os.environ.get("DATABASE_URL", "")

from dash.tools.embeddings_helper import embed_text, vec_to_pg  # noqa: E402
from dash.tools.hybrid_search import hybrid_search  # noqa: E402


TEST_SLUG = "retail-test"


def _can_connect() -> bool:
    if not db_url:
        return False
    try:
        eng = create_engine(db_url, poolclass=NullPool)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _can_connect(),
    reason="No reachable Postgres at db_url; vector RLS tests need a live DB.",
)


# ---------------------------------------------------------------------------
# Fixture: bootstrap table + RLS policy + 4 rows. Teardown removes the rows.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def vectors_table():
    eng = create_engine(db_url, poolclass=NullPool)
    with eng.begin() as conn:
        # pgvector extension (idempotent; needs superuser on first run).
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except (OperationalError, ProgrammingError):
            pytest.skip("pgvector extension unavailable; cannot run vector RLS tests.")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dash_vectors (
                id           BIGSERIAL PRIMARY KEY,
                project_slug TEXT NOT NULL,
                namespace    TEXT NOT NULL,
                source_id    TEXT NOT NULL,
                text         TEXT NOT NULL,
                embedding    vector(1536),
                tsv          tsvector GENERATED ALWAYS AS (
                                 to_tsvector('english', coalesce(text, ''))
                             ) STORED,
                scope_attrs  JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS dash_vectors_tsv_idx "
            "ON dash_vectors USING GIN (tsv)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS dash_vectors_uniq "
            "ON dash_vectors (project_slug, namespace, source_id)"
        ))

        # Enable RLS — defence in depth around app-level filters.
        conn.execute(text("ALTER TABLE dash_vectors ENABLE ROW LEVEL SECURITY"))
        # Drop + recreate policy so we can iterate.
        conn.execute(text("DROP POLICY IF EXISTS dash_vectors_rls ON dash_vectors"))
        conn.execute(text("""
            CREATE POLICY dash_vectors_rls ON dash_vectors
            USING (
                current_setting('app.bypass_rls', true) = 'true'
                OR project_slug = current_setting('app.project_slug', true)
                AND (
                    scope_attrs = '{}'::jsonb
                    OR scope_attrs ->> 'store_id'
                       = (current_setting('app.user_attrs', true)::jsonb ->> 'store_id')
                )
            )
        """))

        # Bypass RLS to seed.
        conn.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        conn.execute(text(
            "DELETE FROM dash_vectors WHERE project_slug = :p"
        ), {"p": TEST_SLUG})

        rows = [
            ("products",  "SKU-123",    {},               "Widget Blue 10oz"),
            ("products",  "SKU-456",    {},               "Gadget Red 5oz"),
            ("inventory", "SKU-123-S1", {"store_id": 1},  "SKU-123 stock 50 units store 1"),
            ("inventory", "SKU-123-S2", {"store_id": 2},  "SKU-123 stock 200 units store 2"),
        ]
        for ns, sid, scope, body in rows:
            conn.execute(
                text("""
                    INSERT INTO dash_vectors
                        (project_slug, namespace, source_id, text, embedding, scope_attrs)
                    VALUES (:p, :ns, :sid, :t, CAST(:e AS vector), CAST(:s AS jsonb))
                """),
                {
                    "p": TEST_SLUG,
                    "ns": ns,
                    "sid": sid,
                    "t": body,
                    "e": vec_to_pg(embed_text(body)),
                    "s": json.dumps(scope),
                },
            )

    yield eng

    with eng.begin() as conn:
        conn.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        conn.execute(
            text("DELETE FROM dash_vectors WHERE project_slug = :p"),
            {"p": TEST_SLUG},
        )
    eng.dispose()


def _set_ctx(conn, slug: str, user_attrs: dict | None, bypass: bool = False):
    conn.execute(
        text("SELECT set_config('app.bypass_rls', :v, true)"),
        {"v": "true" if bypass else "false"},
    )
    conn.execute(text("SELECT set_config('app.project_slug', :v, true)"), {"v": slug})
    conn.execute(
        text("SELECT set_config('app.user_attrs', :v, true)"),
        {"v": json.dumps(user_attrs or {})},
    )


def _select_visible(conn, slug: str, user_attrs: dict | None) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT namespace, source_id, text, scope_attrs "
            "FROM dash_vectors WHERE project_slug = :p"
        ),
        {"p": slug},
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_store1_sees_catalog_only_own_stock(vectors_table):
    eng = vectors_table
    with eng.connect() as conn:
        with conn.begin():
            _set_ctx(conn, TEST_SLUG, {"store_id": 1})
            visible = _select_visible(conn, TEST_SLUG, {"store_id": 1})

    sids = {r["source_id"] for r in visible}
    # Both catalog products visible (scope_attrs={}).
    assert "SKU-123" in sids
    assert "SKU-456" in sids
    # Only own store's inventory.
    assert "SKU-123-S1" in sids
    assert "SKU-123-S2" not in sids


def test_store2_isolation(vectors_table):
    eng = vectors_table
    with eng.connect() as conn:
        with conn.begin():
            _set_ctx(conn, TEST_SLUG, {"store_id": 2})
            visible = _select_visible(conn, TEST_SLUG, {"store_id": 2})

    sids = {r["source_id"] for r in visible}
    assert "SKU-123" in sids and "SKU-456" in sids
    assert "SKU-123-S2" in sids
    assert "SKU-123-S1" not in sids


def test_leak_detector_response_scan(vectors_table):
    """Defence-in-depth: scan returned text for any cross-store leakage."""
    eng = vectors_table
    with eng.connect() as conn:
        with conn.begin():
            _set_ctx(conn, TEST_SLUG, {"store_id": 1})
            visible = _select_visible(conn, TEST_SLUG, {"store_id": 1})

    blob = " ".join(r["text"] for r in visible)
    assert "200 units" not in blob
    assert "store 2" not in blob.lower()
    # Sanity: own data still surfaces.
    assert "50 units" in blob


def test_hybrid_rrf_returns_results(vectors_table):
    async def _go():
        return await hybrid_search(
            TEST_SLUG,
            "widget stock",
            k=5,
            alpha=0.5,
            user_attrs={"store_id": 1},
        )

    results = asyncio.run(_go())
    assert isinstance(results, list)
    assert 0 < len(results) <= 5
    # Sorted descending by fused score.
    scores = [r["score_fused"] for r in results]
    assert scores == sorted(scores, reverse=True)
    # Required keys present.
    for r in results:
        assert {"source_id", "text", "score_fused",
                "vec_rank", "bm_rank", "namespace",
                "scope_attrs", "metadata"} <= set(r.keys())
    # Store-1 user must NEVER see store-2's row through hybrid_search.
    assert all(r["source_id"] != "SKU-123-S2" for r in results)


def test_bypass_admin(vectors_table):
    eng = vectors_table
    with eng.connect() as conn:
        with conn.begin():
            _set_ctx(conn, TEST_SLUG, None, bypass=True)
            visible = _select_visible(conn, TEST_SLUG, None)
    sids = {r["source_id"] for r in visible}
    assert sids == {"SKU-123", "SKU-456", "SKU-123-S1", "SKU-123-S2"}
