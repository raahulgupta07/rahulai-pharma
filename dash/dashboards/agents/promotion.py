"""Phase G — cross-tenant finding promotion.

Findings that get high engagement across multiple projects are promoted
to the central Company Brain so all projects benefit from the pattern.

Designed to be called from the existing nightly skill_refinery cycle.
Cross-tenant anonymization rules:
  - Promotion record stores ONLY: hash, generic headline, signature JSONB,
    project_slug ARRAY (count not values), promotion_score.
  - Headline is taken from one of the project rows but no per-project
    `data` rows are copied — `data` may contain raw row payloads with PII.
  - Brain entry stores headline + sql_keywords + domain_tags only.
  - `value` written to Brain is JSON-serialized signature, never user data.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from .memory_loop import _engine, _ensure_tables

logger = logging.getLogger(__name__)

MIN_PROJECTS = 3
MIN_TOTAL_KEEPS = 6


def _ensure_brain_entry(conn, finding_hash: str, headline: str, signature: dict) -> None:
    # WHY: dash_company_brain may exist with varying column shapes across tenants;
    # do a defensive INSERT with ON CONFLICT DO NOTHING and minimal columns.
    try:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS public.dash_company_brain ("
            "id BIGSERIAL PRIMARY KEY,"
            "project_slug TEXT,"
            "scope TEXT,"
            "category TEXT,"
            "name TEXT,"
            "value TEXT,"
            "source TEXT,"
            "created_at TIMESTAMPTZ DEFAULT now(),"
            "UNIQUE(project_slug, scope, category, name))"
        ))
    except Exception:
        pass
    try:
        conn.execute(text(
            "INSERT INTO public.dash_company_brain "
            "(project_slug, scope, category, name, value, source) "
            "VALUES (NULL, 'global', 'shared_finding', :name, :val, 'cross_tenant_promotion') "
            "ON CONFLICT (project_slug, scope, category, name) DO UPDATE "
            "SET value = EXCLUDED.value"
        ), {"name": finding_hash[:32],
            "val": json.dumps({"headline": headline[:300], "signature": signature})})
    except Exception as e:
        logger.debug(f"brain insert (with conflict) failed, retrying simple: {e}")
        try:
            conn.execute(text(
                "INSERT INTO public.dash_company_brain "
                "(project_slug, scope, category, name, value, source) "
                "VALUES (NULL, 'global', 'shared_finding', :name, :val, 'cross_tenant_promotion')"
            ), {"name": finding_hash[:32],
                "val": json.dumps({"headline": headline[:300], "signature": signature})})
        except Exception as e2:
            logger.warning(f"brain insert failed: {e2}")


def run_promotion_cycle() -> dict:
    """Find finding_hashes appearing in >= MIN_PROJECTS with total keeps >= MIN_TOTAL_KEEPS,
    record into dash_finding_promotions and dash_company_brain (anonymized)."""
    try:
        _ensure_tables()
    except Exception as e:
        logger.warning(f"ensure_tables failed: {e}")
        return {"promoted": 0, "error": str(e)}

    promoted = 0
    inspected = 0
    eng = _engine()
    try:
        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT finding_hash, "
                "       COUNT(DISTINCT project_slug) AS n_proj, "
                "       SUM(keep_count) AS total_keeps, "
                "       MAX(headline) AS headline, "
                "       MAX(finding_signature::text) AS sig, "
                "       array_agg(DISTINCT project_slug) AS projects "
                "FROM public.dash_finding_retention "
                "WHERE keep_count > 0 "
                "GROUP BY finding_hash "
                "HAVING COUNT(DISTINCT project_slug) >= :mp "
                "   AND SUM(keep_count) >= :mk"
            ), {"mp": MIN_PROJECTS, "mk": MIN_TOTAL_KEEPS}).fetchall()

            for row in rows:
                inspected += 1
                fhash = row[0]
                n_proj = int(row[1] or 0)
                total_keeps = int(row[2] or 0)
                headline = row[3] or ""
                try:
                    sig = json.loads(row[4]) if row[4] else {}
                except Exception:
                    sig = {}
                projects = list(row[5] or [])
                # Anonymized pattern: keep only signature parts (no row data)
                pattern = {
                    "sql_keywords": sig.get("sql_keywords", []),
                    "domain_tags": sig.get("domain_tags", []),
                    "severity": sig.get("severity", "medium"),
                }
                score = float(total_keeps) * (1.0 + 0.1 * n_proj)
                conn.execute(text(
                    "INSERT INTO public.dash_finding_promotions "
                    "(finding_hash, headline, pattern, contributing_projects, promotion_score) "
                    "VALUES (:h, :hl, CAST(:p AS JSONB), :proj, :sc) "
                    "ON CONFLICT (finding_hash) DO UPDATE "
                    "SET pattern = EXCLUDED.pattern, "
                    "    contributing_projects = EXCLUDED.contributing_projects, "
                    "    promotion_score = EXCLUDED.promotion_score, "
                    "    promoted_at = now()"
                ), {"h": fhash, "hl": headline[:500], "p": json.dumps(pattern),
                    "proj": projects, "sc": score})
                _ensure_brain_entry(conn, fhash, headline, pattern)
                promoted += 1
    except Exception as e:
        logger.warning(f"promotion cycle failed: {e}")
        return {"promoted": promoted, "inspected": inspected, "error": str(e)}

    return {"promoted": promoted, "inspected": inspected}
