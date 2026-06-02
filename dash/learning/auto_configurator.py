"""
Auto-Configurator — Vertical Classification Orchestrator.

Detects the industry vertical of a Dash project after Train, then maps it
to one of the 26 agent templates registered in `dash/templates/registry.py`.

Strategy
--------
1. Gather signals from project schema + knowledge directory + persona.
2. Run the RULE ENGINE (`vertical_lexicon.rank_verticals`) — instant, $0.
3. If top score >= 0.75 → return rule pick (high confidence, no LLM).
4. Else if top score >= 0.30 → call LLM tie-breaker (LITE_MODEL,
   `training_llm_call(prompt, "extraction")`), blend rule (60%) + LLM (40%).
5. Else → return `{vertical: "generic", confidence: 0.0, method: "no_signal"}`.

This module does NOT apply anything, hit any API endpoint, or touch the UI.
Other agents handle apply + API + UI. We just classify and return.

Public entry points
-------------------
- classify_vertical(project_slug)           → dict (DB-backed, full pipeline)
- classify_from_signals(tables, cols, docs) → dict (no DB, unit-test friendly)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from dash.learning.vertical_lexicon import (
    LEXICONS,
    get_lexicon,
    rank_verticals,
    score_vertical_detail,
)

log = logging.getLogger(__name__)

# Wall-clock timeout (seconds) for any blocking LLM call made during vertical
# classification. classify_vertical runs as a tail step of TRAIN ALL, so a hung
# upstream (OpenRouter via httpx) must never block the whole training pipeline.
_VERTICAL_LLM_TIMEOUT_S = 60


def _call_with_timeout(fn, *args, timeout: float = _VERTICAL_LLM_TIMEOUT_S, **kwargs):
    """Run ``fn(*args, **kwargs)`` with a hard wall-clock timeout.

    Returns the function's result, or ``None`` if it raises or exceeds the
    timeout. We deliberately avoid ``with ThreadPoolExecutor()`` because its
    ``__exit__`` joins worker threads (blocking until the hung call returns),
    which would defeat the timeout. Instead we shut the executor down with
    ``wait=False`` so a stuck thread is abandoned rather than awaited.
    """
    import concurrent.futures

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            log.warning(
                "classify_vertical LLM call timed out after %ss — falling back",
                timeout,
            )
            ex.shutdown(wait=False, cancel_futures=True)
            return None
        except Exception as e:
            log.warning("classify_vertical LLM call failed: %s", e)
            return None
    finally:
        ex.shutdown(wait=False)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

RULE_STRONG_THRESHOLD = 0.45   # >= this → use rule pick directly, no LLM
RULE_WEAK_THRESHOLD = 0.12     # >= this → invoke LLM tie-breaker
RULE_WEIGHT_IN_BLEND = 0.60    # rule contribution in blended confidence
LLM_WEIGHT_IN_BLEND = 0.40     # llm contribution in blended confidence

SAMPLE_ROWS_PER_TABLE = 5
SAMPLE_TABLES_CAP = 20         # avoid massive schemas blowing up signal blob
SAMPLE_COLS_CAP = 400


# ---------------------------------------------------------------------------
# Signal gathering (DB-backed)
# ---------------------------------------------------------------------------

def _get_engine(project_slug: str | None = None):
    """Return a NullPool SQLAlchemy engine for read-only schema introspection.

    Prefers `db.session.get_sql_engine()` for env consistency. If that import
    chain isn't available (unit-test sandbox, partial deploy), build a fresh
    engine from `DATABASE_URL` / `DB_URL` env. Returns None if no URL set.
    """
    try:
        from db.session import get_sql_engine  # absolute import per spec
        return get_sql_engine()
    except Exception as e:
        log.debug("db.session.get_sql_engine() unavailable: %s", e)

    url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not url:
        return None
    try:
        return create_engine(url, poolclass=NullPool)
    except Exception as e:
        log.warning("create_engine fallback failed: %s", e)
        return None


def _list_tables(engine, schema: str) -> list[str]:
    """Tables in given schema via information_schema."""
    sql = text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = :schema
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        LIMIT :cap
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"schema": schema, "cap": SAMPLE_TABLES_CAP * 5}).fetchall()
    return [r[0] for r in rows]


def _list_columns(engine, schema: str) -> list[str]:
    """All column names in given schema via information_schema."""
    sql = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :schema
        ORDER BY table_name, ordinal_position
        LIMIT :cap
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"schema": schema, "cap": SAMPLE_COLS_CAP}).fetchall()
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for (c,) in rows:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _sample_rows(engine, schema: str, tables: list[str]) -> list[str]:
    """Sample up to SAMPLE_ROWS_PER_TABLE rows from each table. Returns a
    flat list of stringified cell values (used as extra signal tokens)."""
    samples: list[str] = []
    if not tables:
        return samples
    with engine.connect() as conn:
        for tbl in tables[:SAMPLE_TABLES_CAP]:
            # parameterized table/schema not supported by PG — must validate
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tbl):
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
                continue
            try:
                q = text(f'SELECT * FROM "{schema}"."{tbl}" LIMIT :n')
                rows = conn.execute(q, {"n": SAMPLE_ROWS_PER_TABLE}).fetchall()
                for row in rows:
                    for val in row:
                        if val is None:
                            continue
                        s = str(val)[:60]
                        if s:
                            samples.append(s)
            except Exception as e:
                log.debug("sample_rows failed on %s.%s: %s", schema, tbl, e)
    return samples


def _list_docs(project_slug: str) -> list[str]:
    """Doc filenames from `knowledge/{slug}/docs/`."""
    base = Path("knowledge") / project_slug / "docs"
    if not base.exists() or not base.is_dir():
        return []
    out: list[str] = []
    try:
        for p in base.iterdir():
            if p.is_file():
                out.append(p.name)
    except Exception as e:
        log.debug("list_docs failed for %s: %s", project_slug, e)
    return out


def _load_persona(project_slug: str) -> str:
    """Persona text from `knowledge/{slug}/persona.json`."""
    p = Path("knowledge") / project_slug / "persona.json"
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("load_persona failed for %s: %s", project_slug, e)
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        parts = []
        for k in ("persona", "description", "role", "title", "audience", "text"):
            v = data.get(k)
            if isinstance(v, str):
                parts.append(v)
        return " ".join(parts)
    return ""


def _project_schema_exists(engine, schema: str) -> bool:
    sql = text(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = :s LIMIT 1"
    )
    with engine.connect() as conn:
        return conn.execute(sql, {"s": schema}).first() is not None


# ---------------------------------------------------------------------------
# LLM tie-breaker
# ---------------------------------------------------------------------------

def _build_llm_prompt(
    tables: list[str],
    columns: list[str],
    docs: list[str],
    rule_top: list[tuple[str, float]],
) -> str:
    verticals = list(LEXICONS.keys())
    tables_blob = ", ".join(tables[:30]) or "(none)"
    cols_blob = ", ".join(columns[:60]) or "(none)"
    docs_blob = ", ".join(docs[:20]) or "(none)"
    rule_blob = ", ".join(f"{k}={s:.2f}" for k, s in rule_top[:5]) or "(none)"

    return f"""You are classifying a database project into ONE industry vertical.

Pick exactly ONE from this list (lowercase, exact spelling):
{verticals}

Project signals
---------------
Tables: {tables_blob}
Columns: {cols_blob}
Docs: {docs_blob}

Rule-engine top picks (for reference, may be wrong):
{rule_blob}

Respond with ONLY a JSON object, no prose, no markdown fences:
{{"vertical": "<one of the list above>", "confidence_0to1": <0..1 float>, "reasoning": "<one short sentence>"}}
"""


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_FIRST_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_json(text_in: str | None) -> dict | None:
    """4-tier robust JSON parser used across Dash learning modules."""
    if not text_in:
        return None
    raw = text_in.strip()

    # Tier 1: direct
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return v
    except Exception:
        pass

    # Tier 2: strip ```fences
    m = _FENCE_RE.search(raw)
    if m:
        try:
            v = json.loads(m.group(1))
            if isinstance(v, dict):
                return v
        except Exception:
            pass

    # Tier 3: regex extract first {...}
    m = _FIRST_JSON_RE.search(raw)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict):
                return v
        except Exception:
            pass

    # Tier 4: give up
    return None


def _llm_tiebreaker(
    tables: list[str],
    columns: list[str],
    docs: list[str],
    rule_top: list[tuple[str, float]],
) -> dict | None:
    """Call LITE_MODEL via training_llm_call. Returns parsed dict or None."""
    try:
        from dash.settings import training_llm_call  # absolute import per spec
    except Exception as e:
        log.debug("training_llm_call import failed: %s", e)
        return None

    prompt = _build_llm_prompt(tables, columns, docs, rule_top)
    # Wrap the blocking network call in a wall-clock timeout so a hung
    # OpenRouter/httpx request can never stall the training pipeline. On
    # timeout/failure this returns None and we fall through (caller treats a
    # None tie-breaker as "LLM unavailable" and uses the rule pick).
    raw = _call_with_timeout(training_llm_call, prompt, "extraction")
    if raw is None:
        return None

    parsed = _parse_llm_json(raw)
    if not parsed:
        return None

    vk = str(parsed.get("vertical", "")).strip().lower()
    if vk not in LEXICONS:
        return None
    try:
        conf = float(parsed.get("confidence_0to1", 0.0))
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return {
        "vertical": vk,
        "confidence": conf,
        "reasoning": str(parsed.get("reasoning", "")).strip(),
    }


# ---------------------------------------------------------------------------
# Core classifier (signal → result)
# ---------------------------------------------------------------------------

def _build_reasoning(
    vertical: str,
    detail: dict,
    method: str,
    llm_pick: dict | None = None,
) -> str:
    sigs = detail.get("signals", [])
    n_tables = sum(1 for s in sigs if s.startswith("table:"))
    n_cols = sum(1 for s in sigs if s.startswith("col:"))
    n_docs = sum(1 for s in sigs if s.startswith("doc:"))
    n_pers = sum(1 for s in sigs if s.startswith("persona:"))

    parts = [
        f"Vertical '{vertical}' matched on "
        f"{n_tables} table(s), {n_cols} column(s), {n_docs} doc(s), {n_pers} persona hint(s)."
    ]
    if method == "llm_tiebreaker" and llm_pick:
        parts.append(f"LLM tie-breaker confirmed (LLM said '{llm_pick['vertical']}': {llm_pick['reasoning'][:140]}).")
    elif method == "no_signal":
        parts = ["No vertical scored above the minimum threshold — falling back to generic."]
    return " ".join(parts)


def _generic_result(method: str = "no_signal", all_scores: dict | None = None) -> dict:
    return {
        "vertical": "generic",
        "template": "blank",
        "confidence": 0.0,
        "method": method,
        "signals": [],
        "reasoning": "No strong vertical signal detected; defaulting to blank/generic template.",
        "runner_up": None,
        "all_scores": all_scores or {},
    }


def classify_from_signals(
    tables: Iterable[str] | None = None,
    columns: Iterable[str] | None = None,
    docs: Iterable[str] | None = None,
    persona: str = "",
) -> dict:
    """Classify a project from raw signal lists. No DB access — unit-test
    friendly entry point used by tests + by `classify_vertical` after it
    gathers signals from the live project."""
    tables = list(tables or [])
    columns = list(columns or [])
    docs = list(docs or [])

    if not tables and not columns and not docs and not persona:
        return _generic_result("no_data")

    ranked = rank_verticals(tables, columns, docs, persona)
    all_scores = {k: s for k, s in ranked}

    if not ranked or ranked[0][1] <= 0:
        return _generic_result("no_signal", all_scores)

    top_key, top_score = ranked[0]
    runner_key, runner_score = ranked[1] if len(ranked) > 1 else (None, 0.0)
    runner_up = {"vertical": runner_key, "confidence": runner_score} if runner_key else None

    top_lex = get_lexicon(top_key) or {}
    top_detail = score_vertical_detail(top_key, tables, columns, docs, persona)

    # ----- strong rule pick → done
    if top_score >= RULE_STRONG_THRESHOLD:
        return {
            "vertical": top_key,
            "template": top_lex.get("template", "blank"),
            "confidence": round(top_score, 4),
            "method": "rule_engine",
            "signals": top_detail["signals"],
            "reasoning": _build_reasoning(top_key, top_detail, "rule_engine"),
            "runner_up": runner_up,
            "all_scores": all_scores,
        }

    # ----- weak rule pick → LLM tie-breaker
    if top_score >= RULE_WEAK_THRESHOLD:
        llm_pick = _llm_tiebreaker(tables, columns, docs, ranked[:5])
        if llm_pick:
            # blend confidence; if LLM agrees with top → boost, else use LLM pick
            if llm_pick["vertical"] == top_key:
                blended = (
                    RULE_WEIGHT_IN_BLEND * top_score
                    + LLM_WEIGHT_IN_BLEND * llm_pick["confidence"]
                )
                final_vert = top_key
                final_detail = top_detail
            else:
                # LLM disagrees — pick LLM's vertical but blend its lower confidence
                # with this vertical's *own* rule score (not the disputed top score).
                llm_vert = llm_pick["vertical"]
                llm_rule_score = all_scores.get(llm_vert, 0.0)
                blended = (
                    RULE_WEIGHT_IN_BLEND * llm_rule_score
                    + LLM_WEIGHT_IN_BLEND * llm_pick["confidence"]
                )
                final_vert = llm_vert
                final_detail = score_vertical_detail(
                    final_vert, tables, columns, docs, persona
                )

            final_lex = get_lexicon(final_vert) or {}
            return {
                "vertical": final_vert,
                "template": final_lex.get("template", "blank"),
                "confidence": round(max(0.0, min(1.0, blended)), 4),
                "method": "llm_tiebreaker",
                "signals": final_detail["signals"],
                "reasoning": _build_reasoning(final_vert, final_detail, "llm_tiebreaker", llm_pick),
                "runner_up": runner_up,
                "all_scores": all_scores,
            }

        # LLM unavailable / failed — fall through to rule pick with weak confidence
        return {
            "vertical": top_key,
            "template": top_lex.get("template", "blank"),
            "confidence": round(top_score, 4),
            "method": "rule_engine",
            "signals": top_detail["signals"],
            "reasoning": _build_reasoning(top_key, top_detail, "rule_engine")
            + " (LLM tie-breaker unavailable.)",
            "runner_up": runner_up,
            "all_scores": all_scores,
        }

    # ----- below weak threshold → generic
    return _generic_result("no_signal", all_scores)


# ---------------------------------------------------------------------------
# Full pipeline (DB + filesystem signal gathering + classify)
# ---------------------------------------------------------------------------

def classify_vertical(project_slug: str) -> dict:
    """Gather signals from project schema + docs + persona, then classify.

    Returns a dict with keys: vertical, template, confidence, method,
    signals, reasoning, runner_up, all_scores. Always returns a dict —
    never raises — so callers can safely consume in pipelines.

    Missing project / empty schema → `{vertical: "generic", confidence: 0.0,
    method: "no_data"}`.
    """
    if not project_slug or not isinstance(project_slug, str):
        return _generic_result("no_data")

    engine = _get_engine(project_slug)
    if engine is None:
        log.warning("classify_vertical: no DB engine — falling back to docs/persona only")
        docs = _list_docs(project_slug)
        persona = _load_persona(project_slug)
        if not docs and not persona:
            return _generic_result("no_data")
        return classify_from_signals(tables=[], columns=[], docs=docs, persona=persona)

    try:
        schema = project_slug  # Dash convention: project slug == schema name
        if not _project_schema_exists(engine, schema):
            log.info("classify_vertical: schema %s does not exist", schema)
            docs = _list_docs(project_slug)
            persona = _load_persona(project_slug)
            if not docs and not persona:
                return _generic_result("no_data")
            return classify_from_signals(tables=[], columns=[], docs=docs, persona=persona)

        tables = _list_tables(engine, schema)
        columns = _list_columns(engine, schema)
        samples = _sample_rows(engine, schema, tables)
    except Exception as e:
        log.warning("classify_vertical signal gathering failed for %s: %s", project_slug, e)
        tables, columns, samples = [], [], []

    docs = _list_docs(project_slug)
    persona = _load_persona(project_slug)

    # Sample row values feed the "columns" signal blob as extra context.
    # (They're short strings and the lexicon matches on substring, so this
    # mostly helps when column names are opaque codes but values are
    # domain-specific, e.g. "RxNorm" in a sample cell.)
    column_signals = columns + samples

    return classify_from_signals(
        tables=tables,
        columns=column_signals,
        docs=docs,
        persona=persona,
    )


__all__ = [
    "classify_vertical",
    "classify_from_signals",
    "RULE_STRONG_THRESHOLD",
    "RULE_WEAK_THRESHOLD",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Hardcoded pharmacy signals — should classify as pharmacy w/ high confidence
    pharma_tables = [
        "articles_list",
        "balance_stock",
        "mmreg",
        "drug_master",
        "rx_dispense_log",
        "pharmacy_branch",
    ]
    pharma_columns = [
        "article_code",
        "composition",
        "dosage",
        "indication",
        "expiry_date",
        "lot_number",
        "ndc",
        "atc",
        "generic_name",
        "brand_name",
        "prescription_id",
        "rxnorm",
    ]
    pharma_docs = [
        "pharmacy_compliance.pdf",
        "drug_handling_sop.docx",
        "rx_audit_report.pdf",
    ]
    persona = "Store manager at a pharmacy chain, dispenses prescriptions daily."

    result = classify_from_signals(
        tables=pharma_tables,
        columns=pharma_columns,
        docs=pharma_docs,
        persona=persona,
    )

    print("=" * 70)
    print("SMOKE TEST — classify_from_signals(pharmacy)")
    print("=" * 70)
    print(json.dumps(result, indent=2, default=str))
    print("=" * 70)
    print(f"PASS: vertical={result['vertical']} template={result['template']} "
          f"confidence={result['confidence']:.3f} method={result['method']}")
