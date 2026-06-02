"""Auto-load domain-specific brain seeds after training.

Reads detected domain from knowledge/{slug}/source_{id}/domain.json
(written by domain_detector). Loads matching seed packs from
knowledge/seeds/{domain}_brain_*.json into dash_company_brain.

Tags entries with source='auto_seed:{domain}' for traceability.
Skips entries already in Brain (dedup by name via ON CONFLICT).

Generic seeds always loaded as fallback.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")
SEEDS_DIR = Path("knowledge/seeds")


@dataclass
class LoadResult:
    """Per-domain seed load summary."""

    domain: str
    files_loaded: list[str] = field(default_factory=list)
    entries_inserted: int = 0
    entries_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def load_seeds_for_domain(
    project_slug: str,
    domain: str,
    *,
    seeds_dir: Path = SEEDS_DIR,
    dash_engine=None,
) -> LoadResult:
    """Load all seed files matching ``{domain}_brain_*.json`` into Brain.

    Idempotent: ``ON CONFLICT (project_slug, name) DO NOTHING`` skips
    already-present entries. Returns counts plus error list.
    """
    result = LoadResult(domain=domain)

    if not seeds_dir.exists():
        result.errors.append(f"seeds dir missing: {seeds_dir}")
        return result

    pattern = f"{domain}_brain_*.json"
    matching = sorted(seeds_dir.glob(pattern))
    if not matching:
        logger.debug("seed_loader: no files match %s", pattern)
        return result

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
    except Exception as e:  # noqa: BLE001
        result.errors.append(f"engine load: {e}")
        return result

    for seed_file in matching:
        try:
            data = json.loads(seed_file.read_text())
            if not isinstance(data, list):
                result.errors.append(f"{seed_file.name}: not a list")
                continue

            inserted = 0
            skipped = 0
            with eng.connect() as conn:
                for entry in data:
                    name = ""
                    try:
                        name = entry.get("name") or ""
                        if not name:
                            continue
                        # Cap value to keep Brain clean
                        value = (entry.get("value") or "")[:2000]
                        category = entry.get("category") or "glossary"
                        scope = entry.get("scope") or "project"
                        confidence = float(entry.get("confidence", 0.85))

                        # Coerce non-canonical to glossary
                        brain_category = _map_category(category)

                        r = conn.execute(text(
                            "INSERT INTO public.dash_company_brain "
                            "(project_slug, name, definition, category, metadata) "
                            "VALUES (:slug, :name, :defn, :cat, :meta) "
                            "ON CONFLICT (project_slug, name) DO NOTHING "
                            "RETURNING id"
                        ), {
                            "slug": project_slug,
                            "name": name[:200],
                            "defn": value,
                            "cat": brain_category,
                            "meta": json.dumps({
                                "source": f"auto_seed:{domain}",
                                "seed_category": category,
                                "scope": scope,
                                "confidence": confidence,
                                "seed_file": seed_file.name,
                            }),
                        }).fetchone()
                        if r:
                            inserted += 1
                        else:
                            skipped += 1
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append(
                            f"{seed_file.name}/{name[:50]}: {str(exc)[:100]}"
                        )
                conn.commit()

            result.files_loaded.append(seed_file.name)
            result.entries_inserted += inserted
            result.entries_skipped += skipped
            logger.info(
                "seed_loader: %s -> +%d -%d for %s",
                seed_file.name, inserted, skipped, project_slug,
            )
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{seed_file.name}: {str(e)[:200]}")

    return result


def auto_load(
    project_slug: str,
    source_id: int,
    *,
    knowledge_dir: Path = KNOWLEDGE_DIR,
    seeds_dir: Path = SEEDS_DIR,
    dash_engine=None,
) -> dict:
    """Top-level: read detected domain, load primary + secondaries + generic.

    Returns aggregate stats dict. Always loads ``generic`` as fallback.
    """
    domain_path = (
        knowledge_dir / project_slug / f"source_{source_id}" / "domain.json"
    )
    if not domain_path.exists():
        logger.debug(
            "seed_loader: no domain.json for %s/source_%s; skipping",
            project_slug, source_id,
        )
        return {"loaded": False, "reason": "no domain detected"}

    try:
        domain_data = json.loads(domain_path.read_text())
    except Exception as e:  # noqa: BLE001
        return {"loaded": False, "reason": f"parse: {e}"}

    primary = domain_data.get("primary", "generic")
    secondaries = domain_data.get("secondaries", []) or []

    domains_to_load = [primary]
    domains_to_load.extend(secondaries)
    if "generic" not in domains_to_load:
        domains_to_load.append("generic")

    aggregate: dict = {
        "loaded": True,
        "domains": domains_to_load,
        "by_domain": [],
        "total_inserted": 0,
        "total_skipped": 0,
        "errors": [],
    }

    for d in domains_to_load:
        result = load_seeds_for_domain(
            project_slug, d,
            seeds_dir=seeds_dir, dash_engine=dash_engine,
        )
        aggregate["by_domain"].append({
            "domain": d,
            "files": result.files_loaded,
            "inserted": result.entries_inserted,
            "skipped": result.entries_skipped,
        })
        aggregate["total_inserted"] += result.entries_inserted
        aggregate["total_skipped"] += result.entries_skipped
        aggregate["errors"].extend(result.errors[:5])

    logger.info(
        "auto_load: %s domains=%s +%d -%d",
        project_slug, domains_to_load,
        aggregate["total_inserted"], aggregate["total_skipped"],
    )
    return aggregate


def _map_category(seed_category: str) -> str:
    """Coerce seed-pack categories to ``dash_company_brain`` canonical set."""
    canonical = {
        "metric": "glossary",
        "formula": "formula",
        "alias": "alias",
        "pattern": "pattern",
        "threshold": "threshold",
        "glossary": "glossary",
        "negative_example": "rule",
    }
    return canonical.get((seed_category or "").lower(), "glossary")
