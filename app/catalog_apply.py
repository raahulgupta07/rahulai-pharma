"""Catalog APPLY layer — make APPROVED enrichment suggestions go live without
ever mutating the source table.

`citypharma.articles_enriched` is a VIEW over `articles_clean`: for each
enrichable field the value is COALESCE(source, approved suggestion). The source
CSV table stays 100% pristine and re-uploadable; rejecting an approval (or a
re-upload) instantly reverts the value — nothing is destructive.

Read path: pharma tools / shop_flat / catalog vectors that want the gap-filled
catalog read `articles_enriched` instead of `articles_clean`. Where a tool needs
the raw source (audit, re-export) it keeps reading `articles_clean`.

A VIEW is always live, so there is no "refresh" — `refresh_or_create` is an alias
provided for symmetry with the matview refresh hook.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text

SCHEMA = "citypharma"
SOURCE_TABLE = "articles_clean"
ENRICH_TABLE = "catalog_enrichment"
ENRICHED_VIEW = "articles_enriched"

#: Fields the view COALESCEs with an approved suggestion. Must match
#: app.catalog_enrich.ENRICHABLE_FIELDS.
_ENRICHED_FIELDS = ("generic_name", "composition", "category",
                    "indication", "dosage", "side_effect")


def _coalesce(field: str) -> str:
    """COALESCE(NULLIF(btrim(source), ''), approved) — source wins; an approved
    suggestion only fills a genuinely blank field."""
    return f"COALESCE(NULLIF(btrim(a.{field}), ''), p.e_{field}) AS {field}"


def _enriched_from_blank(field: str) -> str:
    """True iff this field was blank in source AND an approval supplied it."""
    return (f"(NULLIF(btrim(a.{field}), '') IS NULL AND p.e_{field} IS NOT NULL)")


# Built once — pure string, no interpolation of untrusted input (field names are
# the fixed module constant above).
ENRICHED_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA}.{ENRICHED_VIEW} AS
WITH appr AS (
    SELECT DISTINCT ON (article_code, field)
           article_code::text AS ac, field, suggested_value
    FROM {SCHEMA}.{ENRICH_TABLE}
    WHERE status = 'approved'
    ORDER BY article_code, field, id DESC
),
piv AS (
    SELECT ac,
        MAX(suggested_value) FILTER (WHERE field = 'generic_name') AS e_generic_name,
        MAX(suggested_value) FILTER (WHERE field = 'composition')  AS e_composition,
        MAX(suggested_value) FILTER (WHERE field = 'category')     AS e_category,
        MAX(suggested_value) FILTER (WHERE field = 'indication')   AS e_indication,
        MAX(suggested_value) FILTER (WHERE field = 'dosage')       AS e_dosage,
        MAX(suggested_value) FILTER (WHERE field = 'side_effect')  AS e_side_effect
    FROM appr
    GROUP BY ac
)
SELECT
    a.id,
    a.article_code,
    a.brand_name,
    {_coalesce('generic_name')},
    {_coalesce('composition')},
    {_coalesce('category')},
    a.mm_reg,
    a.mm_label,
    a.other,
    {_coalesce('indication')},
    {_coalesce('dosage')},
    {_coalesce('side_effect')},
    a.status,
    a.created_at,
    a.updated_at,
    (p.ac IS NOT NULL AND (
        {_enriched_from_blank('generic_name')} OR
        {_enriched_from_blank('composition')} OR
        {_enriched_from_blank('category')} OR
        {_enriched_from_blank('indication')} OR
        {_enriched_from_blank('dosage')} OR
        {_enriched_from_blank('side_effect')}
    )) AS is_enriched
FROM {SCHEMA}.{SOURCE_TABLE} a
LEFT JOIN piv p ON p.ac = a.article_code::text
"""


def build_articles_enriched_view(db_url: str, log=print) -> bool:
    """CREATE OR REPLACE the articles_enriched view. Idempotent, non-destructive
    (the source table is untouched). Returns True on success."""
    try:
        eng = create_engine(db_url)
        with eng.begin() as conn:
            conn.execute(text(f"SET search_path = {SCHEMA}, public"))
            conn.execute(text(ENRICHED_VIEW_SQL))
        log(f"[catalog_apply] {SCHEMA}.{ENRICHED_VIEW} view created/replaced")
        return True
    except Exception as e:  # fail-soft — never break training
        log(f"[catalog_apply] view build failed: {str(e)[:160]}")
        return False


def refresh_or_create(db_url: str, log=print) -> bool:
    """Alias — a VIEW is always live, so this just (re)creates it. Provided for
    symmetry with the matview refresh post-hook."""
    return build_articles_enriched_view(db_url, log=log)
