"""Autonomy signal collection — PURE SQL, ZERO LLM, ZERO tokens.

`collect_signals(slug)` returns a dict of cheap, deterministic signals read
from the project schema (== slug) and the platform `public`/`dash` tables. The
heartbeat loop diffs successive snapshots to decide if anything PAID needs to
run. Every sub-query is wrapped individually and fails soft: a missing table
just omits its key — collection never raises.
"""
from __future__ import annotations

import hashlib
import logging
import re

log = logging.getLogger("dash.heartbeat")


def _safe_schema(slug: str) -> str:
    """Project schema name, derived the same way as db/session.py."""
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


def collect_signals(slug: str) -> dict:
    """Collect cheap SQL-only signals for the project. Never raises."""
    from sqlalchemy import text

    schema = _safe_schema(slug)
    out: dict = {}

    try:
        eng = _engine()
    except Exception as e:  # pragma: no cover — DB unreachable
        log.debug("heartbeat: engine unavailable for %s: %s", slug, e)
        return out

    # --- base-table fingerprints + schema hash --------------------------------
    # {table: "<rowcount>:<colcount>"} for BASE TABLE in the project schema,
    # plus a stable hash over the sorted "table.column" list.
    try:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            tables = [r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ), {"s": schema}).fetchall()]

            cols = conn.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :s ORDER BY table_name, column_name"
            ), {"s": schema}).fetchall()

            colcount: dict[str, int] = {}
            tabcol_list: list[str] = []
            for tname, cname in cols:
                colcount[tname] = colcount.get(tname, 0) + 1
                tabcol_list.append(f"{tname}.{cname}")

            fingerprints: dict[str, str] = {}
            for tname in tables:
                try:
                    n = conn.execute(text(
                        'SELECT count(*) FROM "%s"."%s"' % (schema, tname)
                    )).scalar()
                except Exception:
                    n = -1
                fingerprints[tname] = f"{int(n) if n is not None else -1}:{colcount.get(tname, 0)}"

            out["table_fingerprints"] = fingerprints
            out["schema_hash"] = hashlib.sha256(
                "\n".join(sorted(tabcol_list)).encode("utf-8")
            ).hexdigest()[:16]
    except Exception as e:
        log.debug("heartbeat: fingerprint/schema query failed for %s: %s", slug, e)

    # --- shop_flat linkage counts ---------------------------------------------
    # {both, catalog_only, stock_only} from the denormalized shop_flat table if
    # it exists for this schema; omit the key entirely if it doesn't.
    try:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            present = conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = 'shop_flat'"
            ), {"s": schema}).fetchone()
            if present:
                row = conn.execute(text(
                    'SELECT '
                    '  count(*) FILTER (WHERE linked AND stock_qty IS NOT NULL) AS both, '
                    '  count(*) FILTER (WHERE linked AND stock_qty IS NULL) AS catalog_only, '
                    '  count(*) FILTER (WHERE NOT linked) AS stock_only '
                    'FROM "%s".shop_flat' % schema
                )).fetchone()
                if row is not None:
                    out["shop_flat_links"] = {
                        "both": int(row[0] or 0),
                        "catalog_only": int(row[1] or 0),
                        "stock_only": int(row[2] or 0),
                    }
    except Exception as e:
        log.debug("heartbeat: shop_flat query failed for %s: %s", slug, e)

    # --- training queue depth -------------------------------------------------
    try:
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM public.dash_training_runs "
                "WHERE project_slug = :s AND status IN ('running', 'queued')"
            ), {"s": slug}).scalar()
        out["queue_depth"] = int(n or 0)
    except Exception as e:
        log.debug("heartbeat: queue_depth query failed for %s: %s", slug, e)

    # --- pipeline incomplete --------------------------------------------------
    # Base tables that have a dash_table_metadata row but ZERO dash_training_qa
    # rows (profiled but never Q&A-generated → pipeline not complete).
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT m.table_name FROM public.dash_table_metadata m "
                "WHERE m.project_slug = :s AND NOT EXISTS ("
                "  SELECT 1 FROM public.dash_training_qa q "
                "  WHERE q.project_slug = :s AND q.table_name = m.table_name) "
                "ORDER BY m.table_name"
            ), {"s": slug}).fetchall()
        out["pipeline_incomplete"] = [r[0] for r in rows]
    except Exception as e:
        log.debug("heartbeat: pipeline_incomplete query failed for %s: %s", slug, e)

    # --- last chat ts ---------------------------------------------------------
    try:
        with eng.connect() as conn:
            ts = conn.execute(text(
                "SELECT EXTRACT(EPOCH FROM max(ts)) FROM public.dash_traces "
                "WHERE project_slug = :s"
            ), {"s": slug}).scalar()
        out["last_chat_ts"] = float(ts) if ts is not None else None
    except Exception as e:
        log.debug("heartbeat: last_chat_ts query failed for %s: %s", slug, e)

    # --- last upload ts -------------------------------------------------------
    try:
        with eng.connect() as conn:
            ts = conn.execute(text(
                "SELECT EXTRACT(EPOCH FROM max(updated_at)) "
                "FROM public.dash_table_metadata WHERE project_slug = :s"
            ), {"s": slug}).scalar()
        out["last_upload_ts"] = float(ts) if ts is not None else None
    except Exception as e:
        log.debug("heartbeat: last_upload_ts query failed for %s: %s", slug, e)

    # --- last eval score + ABSOLUTE run timestamp -----------------------------
    # Store the eval's absolute epoch, NOT an age-from-now: a relative age would
    # change every tick and trip the diff forever (defeats the frugal design).
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT average_score, EXTRACT(EPOCH FROM run_at) "
                "FROM public.dash_eval_runs WHERE project_slug = :s "
                "ORDER BY run_at DESC LIMIT 1"
            ), {"s": slug}).fetchone()
        if row is not None:
            out["last_eval_score"] = float(row[0]) if row[0] is not None else None
            out["last_eval_ts"] = float(row[1]) if row[1] is not None else None
    except Exception as e:
        log.debug("heartbeat: eval query failed for %s: %s", slug, e)

    return out
