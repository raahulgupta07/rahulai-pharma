"""LLM column-description enrichment.

Turns shallow "Text (4889 unique values)" into a rich semantic blurb,
samples, suggested questions, and quality stats per column.

Persists to `public.dash_column_meta` (migration 154). Idempotent (UPSERT
on the composite PK). Fail-soft per column — single failures must not
break the batch or the upload kickoff path.

Public entry points:
    describe_column(...)       — single column, LITE-model call
    enrich_columns_async(...)  — full table sweep (capped at 30 cols)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy import text as sa_text

from dash.util.semantic_typer import detect_semantic_type

log = logging.getLogger("dash.column_describer")

_MAX_COLS_PER_CALL = 30
_SAMPLE_LIMIT = 5
_DEFAULT_GENERATION_MODEL_TASK = "extraction"  # uses LITE in TRAINING_CONFIGS


# ---------------------------------------------------------------------------
# 4-tier JSON parser
# ---------------------------------------------------------------------------

def _first_object(text_blob: str) -> dict | None:
    """4-tier robust JSON extraction.

    1. direct json.loads
    2. strip ```json fences
    3. regex extract first {...} via balanced-brace scan
    4. trailing-comma repair
    """
    if not text_blob:
        return None
    s = text_blob.strip()

    # Tier 1: direct
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except Exception:
        pass

    # Tier 2: strip fences
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE).strip()
    if fenced != s:
        try:
            v = json.loads(fenced)
            return v if isinstance(v, dict) else None
        except Exception:
            pass
        s = fenced

    # Tier 3: balanced-brace scan for the first {...}
    start = s.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cand = s[start:i + 1]
                    try:
                        v = json.loads(cand)
                        return v if isinstance(v, dict) else None
                    except Exception:
                        # Tier 4: trailing-comma repair
                        try:
                            repaired = re.sub(r",\s*([}\]])", r"\1", cand)
                            v = json.loads(repaired)
                            return v if isinstance(v, dict) else None
                        except Exception:
                            return None
                    break
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _project_schema(slug: str) -> str:
    """Mirror create_project_schema convention without creating it."""
    sanitized = re.sub(r"[^a-z0-9_]", "_", (slug or "").lower())[:63]
    return sanitized


def _list_columns(slug: str, table_name: str) -> list[dict]:
    """Live column list from information_schema for the project schema."""
    from db.session import get_project_readonly_engine
    schema = _project_schema(slug)
    eng = get_project_readonly_engine(slug)
    rows: list[dict] = []
    with eng.connect() as conn:
        res = conn.execute(sa_text(
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t "
            "ORDER BY ordinal_position"
        ), {"s": schema, "t": table_name}).fetchall()
        for r in res:
            rows.append({"column_name": r[0], "data_type": r[1]})
    return rows


def _column_stats(slug: str, table_name: str, col: str) -> dict:
    """Cheap stats: distinct count, null pct, min/max/avg for numeric."""
    from db.session import get_project_readonly_engine
    schema = _project_schema(slug)
    eng = get_project_readonly_engine(slug)
    stats: dict[str, Any] = {}
    qcol = f'"{col}"'
    qtbl = f'"{schema}"."{table_name}"'
    with eng.connect() as conn:
        try:
            row = conn.execute(sa_text(
                f"SELECT COUNT(*) AS total, "
                f"COUNT({qcol}) AS non_null, "
                f"COUNT(DISTINCT {qcol}) AS distinct_count "
                f"FROM {qtbl}"
            )).fetchone()
            total = int(row[0] or 0)
            non_null = int(row[1] or 0)
            distinct = int(row[2] or 0)
            stats["total"] = total
            stats["distinct_count"] = distinct
            stats["null_pct"] = round(((total - non_null) / total) * 100, 2) if total else 0.0
            stats["dup_pct"] = round(((non_null - distinct) / non_null) * 100, 2) if non_null else 0.0
        except Exception:
            pass

        # min/max/avg only on numeric
        try:
            row = conn.execute(sa_text(
                f"SELECT MIN({qcol})::text, MAX({qcol})::text, AVG({qcol})::text "
                f"FROM {qtbl} "
                f"WHERE {qcol} IS NOT NULL"
            )).fetchone()
            if row:
                stats["min"] = row[0]
                stats["max"] = row[1]
                stats["avg"] = row[2]
        except Exception:
            # not numeric — that's fine
            pass
    return stats


def _column_samples(slug: str, table_name: str, col: str, limit: int = _SAMPLE_LIMIT) -> list[str]:
    """SELECT DISTINCT samples (non-null), capped."""
    from db.session import get_project_readonly_engine
    schema = _project_schema(slug)
    eng = get_project_readonly_engine(slug)
    qcol = f'"{col}"'
    qtbl = f'"{schema}"."{table_name}"'
    out: list[str] = []
    with eng.connect() as conn:
        try:
            res = conn.execute(sa_text(
                f"SELECT DISTINCT {qcol}::text FROM {qtbl} "
                f"WHERE {qcol} IS NOT NULL LIMIT :lim"
            ), {"lim": int(limit)}).fetchall()
            out = [r[0] for r in res if r[0] is not None]
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# LLM describer
# ---------------------------------------------------------------------------

async def describe_column(
    project_slug: str,
    table_name: str,
    col_name: str,
    dtype: str,
    stats: dict,
    samples: list,
) -> dict:
    """Single LITE_MODEL call. Returns dict with description, semantic_type,
    cardinality_class, suggested_questions[3]. Fail-soft."""
    from dash.settings import training_llm_call

    heuristic_type = detect_semantic_type(col_name, dtype, samples)

    sample_lines = "\n".join(f"  - {s}" for s in (samples or [])[:5]) or "  (no samples)"
    stats_lines = []
    for k in ("total", "distinct_count", "null_pct", "dup_pct", "min", "max", "avg"):
        if k in stats and stats[k] is not None:
            stats_lines.append(f"  {k}: {stats[k]}")
    stats_block = "\n".join(stats_lines) or "  (no stats)"

    prompt = (
        f"You are describing a single database column for a data analyst.\n"
        f"Project: {project_slug}\nTable: {table_name}\nColumn: {col_name}\nDtype: {dtype}\n"
        f"Heuristic semantic type: {heuristic_type}\n\n"
        f"Sample values:\n{sample_lines}\n\n"
        f"Stats:\n{stats_block}\n\n"
        f"Return ONLY a JSON object (no prose, no fences) with exactly these keys:\n"
        f'  "description":         1-2 sentence plain-English blurb of what this column represents\n'
        f'  "semantic_type":       one of CURRENCY|DATE|BARCODE|EMAIL|PHONE|URL|LANG-MY|LANG-EN|ENUM|FREE-TEXT|BOOLEAN|ID|NUMERIC|TEXT (override the heuristic only if clearly wrong)\n'
        f'  "cardinality_class":   one of PRIMARY_KEY|NEAR_UNIQUE|HIGH_CARD|MED_CARD|LOW_CARD|ENUM|BOOLEAN|LINEAGE\n'
        f'  "suggested_questions": array of EXACTLY 3 short natural-language questions an analyst might ask about this column\n'
    )

    raw = ""
    model_used = "lite"
    try:
        raw = await asyncio.to_thread(training_llm_call, prompt, _DEFAULT_GENERATION_MODEL_TASK)
    except Exception as exc:
        log.warning("describe_column LLM call failed for %s.%s: %s", table_name, col_name, exc)
        raw = ""

    parsed = _first_object(raw or "") or {}

    out = {
        "description": (parsed.get("description") or "").strip()[:1000] or None,
        "semantic_type": (parsed.get("semantic_type") or heuristic_type or "TEXT").strip().upper(),
        "cardinality_class": (parsed.get("cardinality_class") or "").strip().upper() or _fallback_cardinality(stats),
        "suggested_questions": [],
        "generation_model": model_used,
    }
    sq = parsed.get("suggested_questions")
    if isinstance(sq, list):
        out["suggested_questions"] = [str(q).strip() for q in sq if str(q).strip()][:3]
    if not out["description"]:
        out["description"] = f"{col_name} ({heuristic_type.lower()})."
    return out


def _fallback_cardinality(stats: dict) -> str:
    distinct = int(stats.get("distinct_count") or 0)
    total = int(stats.get("total") or 0)
    if total <= 0:
        return "TEXT"
    if distinct == total:
        return "PRIMARY_KEY"
    ratio = distinct / max(1, total)
    if ratio >= 0.95:
        return "NEAR_UNIQUE"
    if distinct <= 2:
        return "BOOLEAN"
    if distinct <= 50:
        return "ENUM"
    if ratio >= 0.5:
        return "HIGH_CARD"
    if ratio >= 0.1:
        return "MED_CARD"
    return "LOW_CARD"


# ---------------------------------------------------------------------------
# UPSERT
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO public.dash_column_meta (
    project_slug, table_name, column_name,
    semantic_type, cardinality_class, description,
    samples, quality, suggested_questions,
    provenance, generation_model, generated_at
) VALUES (
    :slug, :tbl, :col,
    :stype, :card, :desc,
    CAST(:samples AS jsonb), CAST(:quality AS jsonb), CAST(:sq AS jsonb),
    CAST(:prov AS jsonb), :model, now()
)
ON CONFLICT (project_slug, table_name, column_name) DO UPDATE SET
    semantic_type = EXCLUDED.semantic_type,
    cardinality_class = EXCLUDED.cardinality_class,
    description = EXCLUDED.description,
    samples = EXCLUDED.samples,
    quality = EXCLUDED.quality,
    suggested_questions = EXCLUDED.suggested_questions,
    provenance = EXCLUDED.provenance,
    generation_model = EXCLUDED.generation_model,
    generated_at = now()
"""


def _persist(slug: str, table_name: str, col_name: str,
             enriched: dict, stats: dict, samples: list,
             provenance: dict) -> None:
    from db.session import get_write_engine
    eng = get_write_engine()
    quality = {
        "null_pct": stats.get("null_pct"),
        "dup_pct": stats.get("dup_pct"),
        "distinct_count": stats.get("distinct_count"),
        "total": stats.get("total"),
    }
    params = {
        "slug": slug,
        "tbl": table_name,
        "col": col_name,
        "stype": enriched.get("semantic_type"),
        "card": enriched.get("cardinality_class"),
        "desc": enriched.get("description"),
        "samples": json.dumps([str(s) for s in (samples or [])[:5]]),
        "quality": json.dumps(quality),
        "sq": json.dumps(enriched.get("suggested_questions") or []),
        "prov": json.dumps(provenance or {}),
        "model": enriched.get("generation_model") or "lite",
    }
    with eng.begin() as conn:
        conn.execute(sa_text(_UPSERT_SQL), params)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

async def enrich_columns_async(
    project_slug: str,
    table_name: str,
    max_columns: int = _MAX_COLS_PER_CALL,
) -> dict:
    """Enrich every column in a table. Fire-and-forget safe.

    Returns {enriched, skipped, errors}. Caps at max_columns (default 30).
    Per-column errors are caught and logged — never raised.
    """
    if not project_slug or not table_name:
        return {"enriched": 0, "skipped": 0, "errors": ["missing project_slug or table_name"]}

    try:
        cols = _list_columns(project_slug, table_name)
    except Exception as exc:
        log.warning("enrich_columns_async: list_columns failed %s/%s: %s",
                    project_slug, table_name, exc)
        return {"enriched": 0, "skipped": 0, "errors": [f"list_columns: {exc}"]}

    if not cols:
        return {"enriched": 0, "skipped": 0, "errors": [f"no columns found for {project_slug}.{table_name}"]}

    cols = cols[:max_columns]
    enriched_count = 0
    errors: list[str] = []

    provenance = {"source_file": None, "source_sheet": None, "source_col": None}

    for c in cols:
        col_name = c.get("column_name")
        dtype = c.get("data_type") or ""
        # Skip auto-injected lineage cols (cheap to enrich but no value)
        if col_name and col_name.startswith("_source"):
            continue
        try:
            stats = _column_stats(project_slug, table_name, col_name)
            samples = _column_samples(project_slug, table_name, col_name, _SAMPLE_LIMIT)
            enriched = await describe_column(project_slug, table_name, col_name, dtype, stats, samples)
            _persist(project_slug, table_name, col_name, enriched, stats, samples, provenance)
            enriched_count += 1
        except Exception as exc:
            err = f"{col_name}: {exc}"
            log.warning("enrich_columns_async error %s/%s.%s: %s",
                        project_slug, table_name, col_name, exc)
            errors.append(err[:300])

    log.info("enrich_columns_async %s/%s: enriched=%d errors=%d",
             project_slug, table_name, enriched_count, len(errors))
    return {"enriched": enriched_count, "skipped": 0, "errors": errors}
