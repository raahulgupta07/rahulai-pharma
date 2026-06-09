"""Catalog enrichment — SUGGESTION ONLY.

This module proposes values for missing fields in ``citypharma.articles_clean``
(generic_name, composition, category, indication, dosage, side_effect) using
``gemini-3-flash-preview`` via OpenRouter, grounded on real rows that already
have those fields filled.

HARD INVARIANTS (do not violate):
  * It NEVER mutates ``citypharma.articles_clean`` (or any source table).
  * It NEVER auto-applies a suggestion. Every suggestion is written to
    ``citypharma.catalog_enrichment`` with ``status='pending'``.
  * The apply step (promoting a suggestion into the source table) is built
    elsewhere and is expected to gate clinical fields (see ``CLINICAL_FIELDS``).

Everything here is fail-soft: a failed LLM call or a bad row is logged and
skipped, never raised up to crash a batch.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import create_engine, text

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SCHEMA = "citypharma"
SOURCE_TABLE = "articles_clean"
ENRICH_TABLE = "catalog_enrichment"

#: Fields this module is allowed to suggest values for. ``brand_name`` is never
#: enriched (every row already has it and it is the grounding key).
ENRICHABLE_FIELDS: List[str] = [
    "generic_name",
    "composition",
    "category",
    "indication",
    "dosage",
    "side_effect",
]

#: High-risk fields. A wrong generic_name / composition is a patient-safety
#: issue, so the downstream apply gate MUST require human review for these even
#: at high confidence. This module only flags them; it does not apply.
CLINICAL_FIELDS = frozenset({"generic_name", "composition"})

#: Sentinel the model is told to emit when it genuinely does not know. We never
#: store these — a blank gap is safer than a fabricated value.
UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Engine helper
# --------------------------------------------------------------------------- #

def _engine(db_url: str):
    """Build a short-lived engine. Caller owns the url; we never cache it."""
    return create_engine(db_url)


def _is_blank(v: Any) -> bool:
    """True for NULL / empty / whitespace-only values."""
    return v is None or (isinstance(v, str) and not v.strip())


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

def ensure_enrichment_table(db_url: str) -> None:
    """Create ``citypharma.catalog_enrichment`` if it does not exist.

    Idempotent — safe to call on every run. One pending suggestion per
    (article_code, field) is enforced by a UNIQUE constraint so re-runs do not
    pile up duplicates.
    """
    ddl = text(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.{ENRICH_TABLE} (
            id              serial PRIMARY KEY,
            article_code    text,
            field           text,
            original_value  text,
            suggested_value text,
            confidence      real,
            model           text,
            status          text DEFAULT 'pending',
            reason          text,
            created_at      timestamptz DEFAULT now(),
            UNIQUE (article_code, field)
        )
        """
    )
    eng = _engine(db_url)
    with eng.begin() as conn:
        conn.execute(ddl)


# --------------------------------------------------------------------------- #
# Gap detection
# --------------------------------------------------------------------------- #

def _gap_predicate(field: str) -> str:
    """SQL boolean that is TRUE when ``field`` is NULL or blank.

    Factored out (and unit-tested) so the gap query is deterministic and the
    field name is whitelisted before it ever touches SQL.
    """
    if field not in ENRICHABLE_FIELDS:
        raise ValueError(f"not an enrichable field: {field!r}")
    return f"({field} IS NULL OR btrim({field}::text) = '')"


def detect_gaps(db_url: str, fields: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """Return ``{field: count_of_missing}`` for each enrichable field.

    Read-only. ``fields`` defaults to every field in ``ENRICHABLE_FIELDS``.
    """
    flds = list(fields) if fields else list(ENRICHABLE_FIELDS)
    cols = ", ".join(
        f"count(*) FILTER (WHERE {_gap_predicate(f)}) AS {f}" for f in flds
    )
    sql = text(f"SELECT {cols} FROM {SCHEMA}.{SOURCE_TABLE}")
    eng = _engine(db_url)
    with eng.connect() as conn:
        row = conn.execute(sql).mappings().first()
    return {f: int(row[f]) for f in flds} if row else {f: 0 for f in flds}


# --------------------------------------------------------------------------- #
# Grounding
# --------------------------------------------------------------------------- #

def retrieve_examples(
    db_url: str,
    brand_name: str,
    want_fields: Iterable[str],
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Fetch up to ``k`` real, FILLED rows similar to ``brand_name``.

    Grounding strategy (deliberately simple + robust, no embedding plumbing):
      1. rows whose brand_name OR generic_name share a leading token with the
         target brand (ILIKE prefix) — most-similar first;
      2. all wanted fields must be non-blank so the example is actually useful.

    Returns ``[{"brand_name": ..., <field>: ...}, ...]``. Read-only; never
    returns the target row's own gaps.
    """
    want = [f for f in want_fields if f in ENRICHABLE_FIELDS]
    if not want or not brand_name or not brand_name.strip():
        return []

    # Require every wanted field present.
    filled = " AND ".join(f"NOT {_gap_predicate(f)}" for f in want)
    # Similarity key: first whitespace-delimited token of the brand.
    token = brand_name.strip().split()[0]

    sel_cols = ", ".join(["brand_name", *want])
    sql = text(
        f"""
        SELECT {sel_cols}
        FROM {SCHEMA}.{SOURCE_TABLE}
        WHERE {filled}
          AND brand_name IS NOT NULL
          AND (brand_name ILIKE :pfx OR generic_name ILIKE :pfx)
        ORDER BY
          CASE WHEN brand_name ILIKE :exact THEN 0 ELSE 1 END,
          length(brand_name)
        LIMIT :k
        """
    )
    eng = _engine(db_url)
    with eng.connect() as conn:
        rows = conn.execute(
            sql, {"pfx": f"{token}%", "exact": f"{brand_name.strip()}%", "k": int(k)}
        ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Prompt + LLM
# --------------------------------------------------------------------------- #

def build_prompt(
    brand_name: str,
    present: Dict[str, Any],
    want_fields: Iterable[str],
    examples: List[Dict[str, Any]],
) -> str:
    """Build the gemini-3-flash prompt.

    Includes: the brand, the fields already known for this article, grounded
    examples, the exact fields to fill, and a strict-JSON / "unknown"-allowed
    contract. Pure string builder — unit-tested, no I/O.
    """
    want = [f for f in want_fields if f in ENRICHABLE_FIELDS]

    present_lines = "\n".join(
        f"  - {k}: {v}" for k, v in present.items() if not _is_blank(v)
    ) or "  (none)"

    if examples:
        ex_lines = []
        for ex in examples:
            parts = [f"{k}={v}" for k, v in ex.items() if not _is_blank(v)]
            ex_lines.append("  - " + "; ".join(parts))
        examples_block = "\n".join(ex_lines)
    else:
        examples_block = "  (no close examples found — rely on general pharma knowledge)"

    fields_csv = ", ".join(want)

    return (
        "You are a pharmaceutical catalog data specialist. Fill ONLY the "
        "missing fields for the drug below, using the verified examples as "
        "grounding for style and category conventions.\n\n"
        f"DRUG BRAND NAME: {brand_name}\n\n"
        "KNOWN FIELDS FOR THIS DRUG:\n"
        f"{present_lines}\n\n"
        "VERIFIED EXAMPLES FROM THE SAME CATALOG (already-correct rows):\n"
        f"{examples_block}\n\n"
        f"FILL THESE MISSING FIELDS: {fields_csv}\n\n"
        "RULES:\n"
        f'  - If you are not confident, output the string "{UNKNOWN}" for that '
        "field. Never guess or fabricate a clinical value.\n"
        "  - generic_name and composition are patient-safety critical: only "
        "answer if certain.\n"
        "  - Return STRICT JSON only, no prose, no markdown fences.\n\n"
        "JSON SHAPE (one entry per missing field):\n"
        '{"<field>": {"suggested": "<value or \\"unknown\\">", '
        '"confidence": <0.0-1.0>, "reason": "<short justification>"}}\n'
    )


def parse_llm_json(content: str) -> Dict[str, Dict[str, Any]]:
    """Parse the model reply into ``{field: {suggested, confidence, reason}}``.

    Tolerates markdown fences (```json ... ```), leading/trailing prose, and
    drops any field whose suggested value is blank or the ``unknown`` sentinel.
    Returns ``{}`` on unparseable input (fail-soft). Pure — unit-tested.
    """
    if not content or not content.strip():
        return {}

    clean = content.strip()
    # Strip markdown fences.
    if clean.startswith("```"):
        clean = clean.split("```", 2)
        # ['', 'json\n{...}\n', ''] or ['', '{...}', ...]
        clean = clean[1] if len(clean) > 1 else content
        if clean.lower().startswith("json"):
            clean = clean[4:]
        clean = clean.strip()

    # If there is leading/trailing prose, isolate the outermost JSON object.
    if not clean.startswith("{"):
        start = clean.find("{")
        end = clean.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        clean = clean[start : end + 1]

    try:
        raw = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for field, val in raw.items():
        if field not in ENRICHABLE_FIELDS:
            continue
        if not isinstance(val, dict):
            continue
        suggested = val.get("suggested")
        if _is_blank(suggested) or str(suggested).strip().lower() == UNKNOWN:
            continue  # never store a non-answer
        try:
            conf = float(val.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        out[field] = {
            "suggested": str(suggested).strip(),
            "confidence": conf,
            "reason": str(val.get("reason", "") or "").strip(),
        }
    return out


def _call_openrouter(prompt: str, model: str, api_key: str, timeout: float = 60.0) -> str:
    """POST the prompt to OpenRouter and return the raw message content.

    Isolated so tests can inject a fake instead of hitting the network.
    Follows the same call shape used in app/upload.py.
    """
    import httpx

    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    result = resp.json()
    return result["choices"][0]["message"]["content"]


def suggest_for_article(
    article_code: str,
    row: Dict[str, Any],
    want_fields: Iterable[str],
    examples: List[Dict[str, Any]],
    *,
    caller: Callable[[str, str, str], str] = _call_openrouter,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Produce suggestions for one article's missing fields.

    Builds the grounded prompt, calls the LLM (injectable via ``caller`` for
    tests), parses strict JSON, and returns ``{field: {suggested, confidence,
    reason}}``. Fields the model answers ``"unknown"`` are absent. Fail-soft:
    returns ``{}`` on any error.

    ``model``/``api_key`` default to ``dash.settings.TRAINING_MODEL`` and the
    ``OPENROUTER_API_KEY`` env var respectively.
    """
    want = [f for f in want_fields if f in ENRICHABLE_FIELDS]
    if not want:
        return {}

    brand = row.get("brand_name") or ""
    present = {k: row.get(k) for k in ENRICHABLE_FIELDS if k not in want}
    present["brand_name"] = brand

    prompt = build_prompt(brand, present, want, examples)

    if model is None:
        try:
            from dash.settings import TRAINING_MODEL  # local import → no settings load for unit tests
            model = TRAINING_MODEL
        except Exception:
            model = "gemini-3-flash-preview"
    if api_key is None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

    try:
        content = caller(prompt, model, api_key)
    except Exception:
        return {}
    return parse_llm_json(content)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_enrichment(
    db_url: str,
    limit: Optional[int] = None,
    fields: Optional[Iterable[str]] = None,
    log: Callable[..., None] = print,
) -> Dict[str, int]:
    """Run a suggestion batch. Writes ONLY pending rows to catalog_enrichment.

    Steps:
      1. ensure the enrichment table exists;
      2. select articles that have at least one gap in ``fields`` and are not
         already covered (per (article_code, field)) in catalog_enrichment;
      3. for each, retrieve grounding examples, ask the LLM, and INSERT the
         resulting suggestions with ``status='pending'``.

    ``limit`` caps the number of ARTICLES processed (cost control). Returns
    ``{"processed": n, "suggested": n, "skipped": n}``. The source table is
    never touched. Fail-soft per article.
    """
    flds = [f for f in (fields or ENRICHABLE_FIELDS) if f in ENRICHABLE_FIELDS]
    if not flds:
        return {"processed": 0, "suggested": 0, "skipped": 0}

    ensure_enrichment_table(db_url)

    gap_any = " OR ".join(_gap_predicate(f) for f in flds)
    sel = text(
        f"""
        SELECT id, article_code, brand_name,
               {", ".join(ENRICHABLE_FIELDS)}
        FROM {SCHEMA}.{SOURCE_TABLE}
        WHERE ({gap_any})
        ORDER BY id
        {"LIMIT :lim" if limit else ""}
        """
    )

    model = None
    try:
        from dash.settings import TRAINING_MODEL
        model = TRAINING_MODEL
    except Exception:
        model = "gemini-3-flash-preview"
    api_key = os.environ.get("OPENROUTER_API_KEY", "")

    eng = _engine(db_url)
    processed = suggested = skipped = 0

    with eng.connect() as conn:
        params = {"lim": int(limit)} if limit else {}
        rows = conn.execute(sel, params).mappings().all()

    for r in rows:
        row = dict(r)
        code = row.get("article_code")
        if _is_blank(code):
            skipped += 1
            continue

        # Which fields are actually missing for THIS row.
        want = [f for f in flds if _is_blank(row.get(f))]
        if not want:
            continue

        # Drop fields already proposed for this article.
        with eng.connect() as conn:
            done = conn.execute(
                text(
                    f"SELECT field FROM {SCHEMA}.{ENRICH_TABLE} "
                    "WHERE article_code = :c"
                ),
                {"c": code},
            ).scalars().all()
        want = [f for f in want if f not in set(done)]
        if not want:
            skipped += 1
            continue

        processed += 1
        try:
            examples = retrieve_examples(db_url, row.get("brand_name") or "", want, k=5)
            picks = suggest_for_article(
                code, row, want, examples, model=model, api_key=api_key
            )
        except Exception as exc:  # fail-soft per article
            log(f"[catalog_enrich] article {code} failed: {exc}")
            continue

        if not picks:
            log(f"[catalog_enrich] article {code}: no confident suggestions")
            continue

        # Insert pending suggestions. ON CONFLICT keeps the first proposal.
        with eng.begin() as conn:
            for field, p in picks.items():
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {SCHEMA}.{ENRICH_TABLE}
                            (article_code, field, original_value,
                             suggested_value, confidence, model, status, reason)
                        VALUES
                            (:code, :field, :orig, :sug, :conf, :model,
                             'pending', :reason)
                        ON CONFLICT (article_code, field) DO NOTHING
                        """
                    ),
                    {
                        "code": code,
                        "field": field,
                        "orig": row.get(field),
                        "sug": p["suggested"],
                        "conf": p["confidence"],
                        "model": model,
                        "reason": p["reason"],
                    },
                )
                suggested += 1

    log(
        f"[catalog_enrich] done: processed={processed} "
        f"suggested={suggested} skipped={skipped}"
    )
    return {"processed": processed, "suggested": suggested, "skipped": skipped}
