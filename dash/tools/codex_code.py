"""Real Codex code enrichment — read pipeline CODE (view DDL, table DDL,
transformation SQL) for every table/view in a project schema, LLM-enrich the
logic, and persist into ``dash_table_metadata.metadata['pipeline_logic']``.

OpenAI's #1 quality unlock: "meaning lives in code." Profiling data samples
tells you *what* the values are; reading the view definition / CREATE DDL /
saved transformation SQL tells you *why* a column exists, its grain, what
populations are included/excluded, and how it's derived.

Public contract (other agents depend on this — do NOT change the signature):

    def run_codex_code_enrichment(project_slug: str) -> dict:
        # Returns {"tables_enriched": int, "skipped": int, "errors": int}

Everything is fail-soft: per-table try/except, warnings logged, never raises.
LLM calls are capped to bound cost.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from sqlalchemy import text

from dash.tools.skill_refinery import _get_engine
from dash.settings import training_llm_call

logger = logging.getLogger("dash.codex_code")

# Cap LLM calls per run to bound cost.
_MAX_LLM_TABLES = 30
# DDL stored in metadata is truncated to this many chars.
_DDL_STORE_CAP = 4000
# DDL fed to the LLM prompt is capped too (views can be huge).
_DDL_PROMPT_CAP = 8000

_LLM_TASK = "deep_analysis"  # valid task key in TRAINING_CONFIGS


# --------------------------------------------------------------------------- #
# DDL reconstruction
# --------------------------------------------------------------------------- #
def _qualified(slug: str, table: str) -> str:
    """Return a safe double-quoted schema-qualified identifier for ::regclass."""
    return f'"{slug}"."{table}"'


def _list_tables(conn, slug: str) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :slug ORDER BY table_name"
        ),
        {"slug": slug},
    ).fetchall()
    return [r[0] for r in rows]


def _is_view(conn, slug: str, table: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_schema = :slug AND table_name = :t LIMIT 1"
        ),
        {"slug": slug, "t": table},
    ).fetchone()
    return row is not None


def _view_ddl(conn, slug: str, table: str) -> str:
    qualified = _qualified(slug, table)
    sql = conn.execute(
        text("SELECT pg_get_viewdef(:q ::regclass, true)"),
        {"q": qualified},
    ).scalar()
    body = (sql or "").strip()
    return f"CREATE OR REPLACE VIEW {qualified} AS\n{body}"


def _table_ddl(conn, slug: str, table: str) -> str:
    """Reconstruct a CREATE-style summary from information_schema."""
    cols = conn.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = :slug AND table_name = :t "
            "ORDER BY ordinal_position"
        ),
        {"slug": slug, "t": table},
    ).fetchall()

    col_lines = []
    for name, dtype, nullable, default in cols:
        piece = f'  "{name}" {dtype}'
        if (nullable or "").upper() == "NO":
            piece += " NOT NULL"
        if default:
            piece += f" DEFAULT {str(default)[:80]}"
        col_lines.append(piece)

    # PK / FK constraints
    constraints = conn.execute(
        text(
            "SELECT tc.constraint_type, kcu.column_name, tc.constraint_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "WHERE tc.table_schema = :slug AND tc.table_name = :t "
            "  AND tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY') "
            "ORDER BY tc.constraint_type, kcu.ordinal_position"
        ),
        {"slug": slug, "t": table},
    ).fetchall()

    pk_cols = [c for ctype, c, _ in constraints if ctype == "PRIMARY KEY"]
    fk_cols = [c for ctype, c, _ in constraints if ctype == "FOREIGN KEY"]

    extra = []
    if pk_cols:
        extra.append("  PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk_cols) + ")")
    if fk_cols:
        # We can't cheaply resolve target tables here; note the FK columns.
        extra.append("  -- FOREIGN KEY columns: " + ", ".join(fk_cols))

    body = ",\n".join(col_lines + extra)
    return f'CREATE TABLE {_qualified(slug, table)} (\n{body}\n);'


def _saved_query_for(conn, slug: str, table: str) -> str | None:
    """Best-effort: find a saved transformation SQL that built this table.

    Queries dash_query_patterns (if it exists) for a pattern whose SQL
    references the table name. Ignores any error.
    """
    try:
        rows = conn.execute(
            text(
                "SELECT sql FROM public.dash_query_patterns "
                "WHERE project_slug = :s AND sql ILIKE :pat "
                "ORDER BY COALESCE(uses, 0) DESC LIMIT 1"
            ),
            {"s": slug, "pat": f"%{table}%"},
        ).fetchall()
        if rows and rows[0][0]:
            return str(rows[0][0]).strip()
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# LLM enrichment
# --------------------------------------------------------------------------- #
def _strip_json_fences(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        # remove leading ```json / ``` and trailing ```
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _parse_llm_json(raw: str | None) -> dict:
    if not raw:
        return {}
    s = _strip_json_fences(raw)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        # last-ditch: grab first {...} block
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}


def _enrich_with_llm(kind: str, table: str, ddl: str, saved_sql: str | None) -> dict:
    prompt_parts = [
        "You are a data pipeline analyst. Below is the CODE that defines a "
        f"database {kind} named `{table}`. Read it carefully — the *meaning* "
        "of the data lives in this code, not in the column names alone.\n",
        f"--- {kind.upper()} DEFINITION ---\n{ddl[:_DDL_PROMPT_CAP]}\n",
    ]
    if saved_sql:
        prompt_parts.append(
            "--- A SAVED TRANSFORMATION QUERY THAT REFERENCES THIS OBJECT ---\n"
            f"{saved_sql[:2000]}\n"
            "⚠ NOTE: this saved query is illustrative — it does NOT define the "
            "grain. Grain = what one row of the BASE TABLE represents, derived "
            "from the DDL above (primary key cols, column semantics). DO NOT "
            "let aggregation in the saved query (SUM/AVG/COUNT/GROUP BY) bleed "
            "into your grain description. E.g. if DDL has (site_id, sku_id, "
            "stock_qty) and saved query is SUM(stock_qty), grain is still "
            "'one row per (site_id, sku_id)', NOT 'total stock across sites'.\n"
        )
    prompt_parts.append(
        "Extract the pipeline logic and respond with ONLY a JSON object "
        "(no prose, no code fences) with EXACTLY these keys:\n"
        "{\n"
        '  "grain": "what one row represents",\n'
        '  "derived_columns": [{"col": "name", "formula": "how it is computed"}],\n'
        '  "populations_included": "rows/filters this includes",\n'
        '  "populations_excluded": "rows/filters this excludes",\n'
        '  "refresh_hint": "how/when this is refreshed if inferable, else empty",\n'
        '  "summary": "one-paragraph plain-English description of what this object means"\n'
        "}"
    )
    prompt = "\n".join(prompt_parts)
    try:
        raw = training_llm_call(prompt, _LLM_TASK)
    except Exception as e:  # noqa: BLE001
        logger.warning("codex_code: LLM call failed for %s: %s", table, e)
        return {}
    return _parse_llm_json(raw)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _existing_fp(conn, slug: str, table: str) -> str | None:
    try:
        row = conn.execute(
            text(
                "SELECT metadata #>> '{pipeline_logic,fp}' "
                "FROM public.dash_table_metadata "
                "WHERE project_slug = :s AND table_name = :t"
            ),
            {"s": slug, "t": table},
        ).fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def _persist(conn, slug: str, table: str, payload: dict) -> None:
    """UPSERT pipeline_logic into dash_table_metadata.metadata.

    jsonb_set on a possibly-missing row: INSERT a fresh metadata object if no
    row exists, else jsonb_set the existing metadata. Uses CAST(:p AS jsonb)
    (NEVER :p::jsonb — SQLAlchemy named-param collision).
    """
    p = json.dumps(payload, default=str)

    # Does a row already exist?
    exists = conn.execute(
        text(
            "SELECT 1 FROM public.dash_table_metadata "
            "WHERE project_slug = :s AND table_name = :t"
        ),
        {"s": slug, "t": table},
    ).fetchone()

    if exists:
        conn.execute(
            text(
                "UPDATE public.dash_table_metadata "
                "SET metadata = jsonb_set("
                "  COALESCE(metadata, '{}'::jsonb), "
                "  '{pipeline_logic}', CAST(:p AS jsonb), true), "
                "  updated_at = NOW() "
                "WHERE project_slug = :s AND table_name = :t"
            ),
            {"p": p, "s": slug, "t": table},
        )
    else:
        conn.execute(
            text(
                "INSERT INTO public.dash_table_metadata "
                "(project_slug, table_name, metadata) "
                "VALUES (:s, :t, jsonb_build_object('pipeline_logic', CAST(:p AS jsonb)))"
            ),
            {"s": slug, "t": table, "p": p},
        )
    conn.commit()


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
def run_codex_code_enrichment(project_slug: str) -> dict:
    """Read pipeline code for every table/view in the project schema, LLM-enrich,
    persist into dash_table_metadata.metadata['pipeline_logic']. Fail-soft.
    Returns {"tables_enriched": int, "skipped": int, "errors": int}."""
    result = {"tables_enriched": 0, "skipped": 0, "errors": 0}

    try:
        engine = _get_engine()
    except Exception as e:  # noqa: BLE001
        logger.warning("codex_code: could not get engine: %s", e)
        result["errors"] += 1
        return result

    try:
        with engine.connect() as conn:
            try:
                tables = _list_tables(conn, project_slug)
            except Exception as e:  # noqa: BLE001
                logger.warning("codex_code: list tables failed for %s: %s", project_slug, e)
                result["errors"] += 1
                return result

            llm_calls = 0
            for table in tables:
                try:
                    is_view = _is_view(conn, project_slug, table)
                    kind = "view" if is_view else "table"

                    if is_view:
                        ddl = _view_ddl(conn, project_slug, table)
                    else:
                        ddl = _table_ddl(conn, project_slug, table)

                    saved_sql = _saved_query_for(conn, project_slug, table)

                    # Fingerprint over the DDL (+ saved sql) for caching.
                    fp_src = ddl + (saved_sql or "")
                    fp = hashlib.sha256(fp_src.encode("utf-8", "ignore")).hexdigest()

                    if _existing_fp(conn, project_slug, table) == fp:
                        result["skipped"] += 1
                        continue

                    if llm_calls >= _MAX_LLM_TABLES:
                        logger.info(
                            "codex_code: LLM cap (%d) reached for %s; skipping %s",
                            _MAX_LLM_TABLES, project_slug, table,
                        )
                        result["skipped"] += 1
                        continue

                    enriched = _enrich_with_llm(kind, table, ddl, saved_sql)
                    llm_calls += 1

                    payload = dict(enriched) if isinstance(enriched, dict) else {}
                    payload["kind"] = kind
                    payload["ddl"] = ddl[:_DDL_STORE_CAP]
                    payload["code_enriched"] = True
                    payload["fp"] = fp

                    _persist(conn, project_slug, table, payload)
                    result["tables_enriched"] += 1

                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "codex_code: enrichment failed for %s.%s: %s",
                        project_slug, table, e,
                    )
                    result["errors"] += 1
                    continue
    except Exception as e:  # noqa: BLE001
        logger.warning("codex_code: run failed for %s: %s", project_slug, e)
        result["errors"] += 1

    logger.info("codex_code: %s done: %s", project_slug, result)
    return result
