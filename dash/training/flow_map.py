"""
Training flow map — static layer/step registry + live state derivation.

Exports:
  LAYERS  — static list of 10 layer dicts (idx, title, color, gate, steps)
  STORES  — ordered data-store chip list [{key, label}]
  derive_flow(slug, db_url) -> dict  — live training state
"""
from __future__ import annotations

import os
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LAYERS — ported verbatim from scripts/show_training_workflow.py phases()
# Each step: (label, model, detail, writes_to, gate)
# color palette in order [amber, cyan, coral, amber, cyan, coral, amber, cyan, coral, coral]
# ---------------------------------------------------------------------------

_PALETTE = [
    "amber",   # LAYER 0
    "cyan",    # LAYER 1
    "coral",   # LAYER 2
    "amber",   # LAYER 3
    "cyan",    # LAYER 4
    "coral",   # LAYER 5
    "amber",   # LAYER 6
    "cyan",    # LAYER 7
    "coral",   # LAYER 8
    "coral",   # LAYER 9
]

_RAW_PHASES = [
    ("LAYER 0 · STAGING  (dash/ingest/ — review before data hits a table)", [
        ("POST /upload/stage", "", "receive file(s), multi-file batch via batch_id", "knowledge/<proj>/staging/", None),
        ("sheet split", "", "xlsx → one staged entry per sheet", "staging/", None),
        ("content_hash", "", "sha of bytes — dedup key", "manifest.json", None),
        ("quality scan", "", "empty/unreadable → quarantine", "manifest(status)", None),
        ("write_manifest", "", "status=staged, files[], hashes", "manifest.json", None),
    ]),
    ("LAYER 1 · DRY-RUN  (preview, no DB write)", [
        ("infer_contract", "", "column types + load-key detect", "dash_dataset_contracts", None),
        ("check_against_contract", "", "schema-drift vs prior contract", "(plan)", None),
        ("detect_load_key", "", "PK / composite key for upserts", "(plan)", None),
    ]),
    ("LAYER 2 · PROMOTE → INGEST  (staged → Postgres)", [
        ("drift gate", "", "schema drift → quarantine, else proceed", "manifest", None),
        ("file_hash_seen", "", "skip already-loaded files (dedup)", "(check)", None),
        ("delete_where_period/batch", "", "clean reload of same period", "citypharma.<table>", None),
        ("_is_id_colname → TEXT", "", "ID/code cols kept as text (Excel-safe)", "citypharma.<table>", None),
        ("copy_csv / stream_xlsx", "", "streaming COPY (big files, no OOM)", "citypharma.<table>", None),
        ("stamp_lineage", "", "_source_file/_period/_batch_id/_hash", "citypharma.<table>", None),
    ]),
    ("LAYER 3 · PER-TABLE TRAINING LOOP  (×N source tables; derived excluded)", [
        ("drift detect", "SQL", "fingerprint — skip unchanged table", "dash_table_metadata", None),
        ("profile_v2", "SQL", "types · roles(id/dim/measure) · pg_stats", "dash_table_metadata.profile_v2", None),
        ("dimension_catalog", "SQL", "top-20 distinct values per dim col", "dash_table_metadata.dimensions", None),
        ("deep_analysis", "DEEP", "narrative analysis of the table", "knowledge/<proj>/", None),
        ("qa_generation", "FLASH", "Q→SQL training pairs", "dash_training_qa", None),
        ("persona", "FLASH", "agent persona for the data", "knowledge persona", None),
        ("synthesis", "FLASH", "table synthesis summary", "knowledge synthesis", None),
        ("workflows", "FLASH", "common multi-step workflows", "knowledge workflows", None),
        ("knowledge index", "embed", "chunk + embed → vector store", "PgVector", None),
        ("brain fill", "", "company-brain facts/rules", "dash_company_brain", None),
        ("domain_knowledge", "DEEP", "domain rules + glossary", "dash_memories/rules_db", None),
        ("persona enrich", "FLASH", "enrich persona (lenient JSON)", "knowledge persona", None),
    ]),
    ("LAYER 4 · RELATIONSHIPS  (cross-table)", [
        ("_discover_relationships", "FLASH", "LLM proposes FK/join candidates", "(in-mem)", None),
        ("verify (SQL containment)", "SQL", "directional overlap %, timeout 30s", "dash_relationships", None),
        ("_seed_cross_table_qa", "FLASH", "JOIN Q→SQL pairs from verified links", "dash_training_qa", None),
    ]),
    ("LAYER 5 · ◆ SEMANTIC LAYER  (Engineer agent designs matviews)", [
        ("Engineer.inspect_schema", "DEEP", "READ-ONLY: cols + roles", "(agent ctx)", "ENGINEER"),
        ("Engineer.get_relationships", "DEEP", "READ-ONLY: verified join keys", "(agent ctx)", "ENGINEER"),
        ("Engineer.sample_rows", "DEEP", "READ-ONLY: 5 rows (real formats)", "(agent ctx)", "ENGINEER"),
        ("Engineer.dry_run_sql", "DEEP", "EXPLAIN candidate SELECT, iterate", "(agent ctx)", "ENGINEER"),
        ("→ SemanticLayerPlan (struct)", "", "═ TRUST BOUNDARY: model stops here ═", "(struct)", "ENGINEER"),
        ("validate_matview_spec", "", "whitelist: no DDL/;/comments/x-schema", "(gate)", "ENGINEER"),
        ("EXPLAIN dry-run (server)", "SQL", "reject on error/timeout", "(gate)", "ENGINEER"),
        ("build_ddl + CREATE (1 txn)", "SQL", "DROP→CREATE MATVIEW→unique idx", "citypharma.<matview>", "ENGINEER"),
        ("register", "", "semantic_layer=true, refresh_sql", "dash_table_metadata", "ENGINEER"),
    ]),
    ("LAYER 6 · GRAPH + BACKFILL", [
        ("knowledge_graph", "", "entities + edges (openCypher)", "Apache AGE graph", None),
        ("subagent_synthesis", "FLASH", "cross-source synthesis (needs KG)", "knowledge", None),
        ("vector_backfill", "embed", "embed any rows missing vectors", "PgVector", None),
        ("codex_code_enrich", "", "pipeline_logic enrichment", "dash_table_metadata.pipeline_logic", None),
    ]),
    ("LAYER 7 · TAIL  (4 concurrent)", [
        ("scope", "", "derive feature/answer scope", "feature_config.scope", None),
        ("goals", "", "learning_goals.md", "knowledge goals", None),
        ("ml", "", "NO-OP — AutoML removed (purges row)", "—", None),
        ("evals (gen + run)", "FLASH", "golden set → run → score", "dash_evals / dash_eval_runs", None),
        ("auto_configure", "", "vertical detect + pack apply", "dash_auto_apply_history", None),
    ]),
    ("LAYER 8 · POST-HOOKS  (sequential — order matters)", [
        ("bilingual twins", "FLASH", "53 Burmese Q→SQL twins", "dash_training_qa", None),
        ("catalog vectors", "embed", "embed catalog → hybrid search", "PgVector", None),
        ("◆ articles_enriched view", "SQL", "ALWAYS: COALESCE(source,approved)+is_enriched", "citypharma.articles_enriched", None),
        ("◆ detect_gaps", "SQL", "count blank fields per col", "(report)", "ENRICH"),
        ("◆ retrieve_examples", "SQL", "ground on labeled rows (few-shot)", "(ctx)", "ENRICH"),
        ("◆ run_enrichment", "FLASH", "suggest missing → pending only", "citypharma.catalog_enrichment", "ENRICH"),
        ("◆ auto_apply_low_risk", "", "category/indication ≥0.9; clinical NEVER", "catalog_enrichment.status", "ENRICH"),
        ("◆ rebuild enriched view", "SQL", "reflect new approvals", "articles_enriched", "ENRICH"),
        ("shop_flat build", "SQL", "reads enriched · _norm join · orphans linked=false", "citypharma.shop_flat", None),
        ("◆ matview refresh", "SQL", "REFRESH MATERIALIZED VIEW CONCURRENTLY", "citypharma.<matview>", "ENGINEER"),
    ]),
    ("LAYER 9 · DONE", [
        ("status finalizing→done", "", "flip only after all post-hooks", "dash_training_runs", None),
        ("watchdog clear", "", "_reap_stale_runs stops tracking", "dash_training_runs", None),
        ("UI panels live", "", "Semantic Layer + Catalog Gaps + flow", "—", None),
    ]),
]

def _build_layers() -> list[dict]:
    layers = []
    for idx, (title, raw_steps) in enumerate(_RAW_PHASES):
        # Layer-level gate: LAYER 5 is ENGINEER-gated
        layer_gate = "ENGINEER" if idx == 5 else None
        steps = []
        for (label, model, detail, writes_to, step_gate) in raw_steps:
            # Step gate: ENRICH for ◆ENRICH steps in LAYER 8; ENGINEER for LAYER 5 steps
            # (gate already in the tuple; use it directly)
            steps.append({
                "label": label,
                "model": model,
                "detail": detail,
                "writes_to": writes_to,
                "gate": step_gate,
            })
        layers.append({
            "idx": idx,
            "title": title,
            "color": _PALETTE[idx],
            "gate": layer_gate,
            "steps": steps,
        })
    return layers


LAYERS: list[dict] = _build_layers()

# ---------------------------------------------------------------------------
# STORES — ordered data-store chips
# ---------------------------------------------------------------------------

STORES: list[dict] = [
    {"key": "STAGE",  "label": "staging files"},
    {"key": "PG",     "label": "citypharma tables"},
    {"key": "META",   "label": "dash_table_metadata"},
    {"key": "QA",     "label": "dash_training_qa"},
    {"key": "VEC",    "label": "PgVector"},
    {"key": "BRAIN",  "label": "dash_company_brain"},
    {"key": "REL",    "label": "dash_relationships"},
    {"key": "MV",     "label": "matviews"},
    {"key": "AGE",    "label": "graph"},
    {"key": "ENR",    "label": "catalog_enrichment"},
    {"key": "EVAL",   "label": "dash_eval_runs"},
]

# ---------------------------------------------------------------------------
# Layer status keyword mapping (edit here to tune)
# ---------------------------------------------------------------------------

KEYWORDS: dict[int, list[str]] = {
    3: ["qa_generation", "profile", "deep_analysis", "persona", "knowledge"],
    4: ["relationship"],
    5: ["semantic", "engineer", "matview"],
    6: ["knowledge_graph", "vector_backfill"],
    8: ["catalog", "enrich", "shop_flat", "bilingual"],
    9: ["finalizing", "done"],
}

# ---------------------------------------------------------------------------
# derive_flow
# ---------------------------------------------------------------------------

def derive_flow(slug: str, db_url: str) -> dict:
    """Return live training state for slug.

    Fail-soft: every DB query is wrapped in try/except so a missing table
    never causes a 500. Returns zeros/nulls for missing data.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    eng = create_engine(
        db_url,
        poolclass=NullPool,
        connect_args={"options": "-c statement_timeout=10000"},
    )

    # ---- flags -----------------------------------------------------------------
    engineer_flag = os.environ.get("ENGINEER_SEMANTIC_LAYER", "0") in ("1", "true", "True")
    enrich_flag   = os.environ.get("CATALOG_ENRICH", "0") in ("1", "true", "True")

    # ---- latest training run ---------------------------------------------------
    run: Optional[dict] = None
    try:
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT id, status, current_step, stage_progress, current_progress,
                       tables_trained, started_at, finished_at
                FROM public.dash_training_runs
                WHERE project_slug = :slug
                ORDER BY started_at DESC NULLS LAST
                LIMIT 1
            """), {"slug": slug}).mappings().fetchone()
            if row:
                run = {
                    "id": row["id"],
                    "status": row["status"],
                    "current_step": row["current_step"],
                    "started_at": str(row["started_at"]) if row["started_at"] else None,
                    "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
                    "stage_progress": row["stage_progress"],
                    "progress": row["current_progress"] if row["current_progress"] else {},
                }
    except Exception as exc:
        logger.debug("flow_map: run query failed: %s", exc)

    # ---- counts ----------------------------------------------------------------
    tables_count = 0
    rows_count   = 0
    qa_count     = 0
    rels_count   = 0
    matviews_count = 0
    gaps_count   = 0
    eval_score: Optional[float] = None
    eval_count   = 0
    graph_nodes  = 0

    try:
        with eng.connect() as conn:
            tables_count = int(conn.execute(text("""
                SELECT COUNT(*)
                FROM information_schema.tables t
                WHERE t.table_schema = :schema
                  AND t.table_type = 'BASE TABLE'
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_matviews mv
                      WHERE mv.schemaname = :schema AND mv.matviewname = t.table_name
                  )
            """), {"schema": slug}).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: tables count failed: %s", exc)

    try:
        with eng.connect() as conn:
            rows_count = int(conn.execute(text("""
                SELECT COALESCE(SUM(c.reltuples::bigint), 0)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relkind = 'r'
            """), {"schema": slug}).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: rows count failed: %s", exc)

    try:
        with eng.connect() as conn:
            qa_count = int(conn.execute(text("""
                SELECT COUNT(*) FROM public.dash_training_qa
                WHERE project_slug = :slug
            """), {"slug": slug}).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: qa count failed: %s", exc)

    try:
        with eng.connect() as conn:
            # dash_relationships has project_slug col (confirmed)
            rels_count = int(conn.execute(text("""
                SELECT COUNT(*) FROM public.dash_relationships
                WHERE project_slug = :slug
            """), {"slug": slug}).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: rels count failed: %s", exc)

    try:
        with eng.connect() as conn:
            matviews_count = int(conn.execute(text("""
                SELECT COUNT(*) FROM pg_matviews WHERE schemaname = :schema
            """), {"schema": slug}).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: matviews count failed: %s", exc)

    try:
        with eng.connect() as conn:
            # to_regclass guard for articles_clean
            exists = conn.execute(text(
                "SELECT to_regclass(:tbl)"
            ), {"tbl": f"{slug}.articles_clean"}).scalar()
            if exists:
                gaps_count = int(conn.execute(text(f"""
                    SELECT COUNT(*) FROM {slug}.articles_clean
                    WHERE (generic_name IS NULL OR generic_name = '')
                """)).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: gaps count failed: %s", exc)

    try:
        with eng.connect() as conn:
            er = conn.execute(text("""
                SELECT total, passed, average_score
                FROM public.dash_eval_runs
                WHERE project_slug = :slug
                ORDER BY run_at DESC NULLS LAST
                LIMIT 1
            """), {"slug": slug}).mappings().fetchone()
            if er:
                eval_count = int(er["total"] or 0)
                eval_score = float(er["average_score"]) if er["average_score"] is not None else None
    except Exception as exc:
        logger.debug("flow_map: eval query failed: %s", exc)

    try:
        with eng.connect() as conn:
            # AGE graph — wrap in own txn with ag_catalog search_path
            conn.execute(text(
                "SET LOCAL search_path = ag_catalog, \"$user\", public"
            ))
            graph_nodes = int(conn.execute(text("""
                SELECT COUNT(*) FROM cypher('citypharma', $$MATCH (n) RETURN n$$) AS (n agtype)
            """)).scalar() or 0)
    except Exception as exc:
        logger.debug("flow_map: AGE graph query failed (expected if AGE off): %s", exc)

    # ---- per-layer status ------------------------------------------------------
    def _layer_status(idx: int) -> str:
        if idx == 5 and not engineer_flag:
            return "skipped"
        if run is None:
            return "idle"
        r_status = run.get("status", "")
        current = (run.get("current_step") or "").lower()
        progress_step = ""
        prog = run.get("progress") or {}
        if isinstance(prog, dict):
            progress_step = (prog.get("step") or "").lower()

        active_hint = current + " " + progress_step

        if r_status == "done":
            # ENGINEER layer → 'done' only if flag was on (else already 'skipped')
            if idx == 5:
                return "done" if engineer_flag else "skipped"
            return "done"

        if r_status in ("running", "finalizing"):
            kws = KEYWORDS.get(idx, [])
            if kws and any(k in active_hint for k in kws):
                return "running"
            # layers before the active one are done
            for check_idx, check_kws in KEYWORDS.items():
                if check_idx > idx:
                    continue
                if check_kws and any(k in active_hint for k in check_kws):
                    return "done"
            return "idle"

        return "idle"

    layers_out = []
    for layer in LAYERS:
        idx = layer["idx"]
        layers_out.append({
            **layer,
            "status": _layer_status(idx),
        })

    # ---- assemble stores dict --------------------------------------------------
    stores_out = {
        "STAGE": None,       # disk — not queryable simply
        "PG":    tables_count,
        "META":  None,       # not separately counted
        "QA":    qa_count,
        "VEC":   None,       # pgvector namespace count omitted (expensive)
        "BRAIN": None,
        "REL":   rels_count,
        "MV":    matviews_count,
        "AGE":   graph_nodes,
        "ENR":   None,
        "EVAL":  eval_count,
    }

    return {
        "run": run,
        "layers": layers_out,
        "stores": stores_out,
        "kpis": {
            "tables":     tables_count,
            "rows":       rows_count,
            "qa":         qa_count,
            "rels":       rels_count,
            "matviews":   matviews_count,
            "gaps":       gaps_count,
            "eval_score": eval_score,
            "eval_count": eval_count,
        },
        "flags": {
            "engineer": engineer_flag,
            "enrich":   enrich_flag,
        },
    }
