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


def _iso_utc(v) -> Optional[str]:
    """Serialize a DB timestamp value as ISO-8601 UTC ending in 'Z'.

    The DB stores NAIVE UTC (db TIMEZONE=Etc/UTC). The frontend parses bare
    'YYYY-MM-DD HH:MM:SS' strings as LOCAL time → phantom elapsed when the
    browser is offset from UTC. So we ALWAYS emit an explicit 'Z'/offset.

    Handles None / datetime / str. Never throws.
    """
    if v is None:
        return None
    try:
        # datetime-like (has isoformat)
        iso = getattr(v, "isoformat", None)
        if callable(iso):
            s = v.isoformat()
        else:
            s = str(v)
        s = s.strip()
        if not s:
            return None
        # naive "YYYY-MM-DD HH:MM:SS[.ffffff]" → ISO 'T'
        s = s.replace(" ", "T", 1)
        if s.endswith("Z"):
            return s
        # Already tz-aware? An offset (+HH:MM / -HH:MM) lives in the TIME portion
        # (after the 'T'); the date portion before it also contains '-' so only
        # inspect the part after 'T'.
        t_idx = s.find("T")
        time_part = s[t_idx + 1:] if t_idx >= 0 else s
        if "+" in time_part or "-" in time_part:
            return s  # explicit offset present — leave as-is
        return s + "Z"
    except Exception:
        try:
            return str(v) if v is not None else None
        except Exception:
            return None

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
# Display masking — hides internal tool / engine / table names from the UI and
# the API response so the live training flow cannot be reverse-engineered.
# ON by default; set FLOW_OBFUSCATE=0 for internal debugging (shows real names).
# Internal labels are still used for log-matching BEFORE the mask is applied.
# ---------------------------------------------------------------------------
import re as _re_mask

_OBFUSCATE = os.getenv("FLOW_OBFUSCATE", "1") not in ("0", "false", "False", "")

# real step label -> (display label, display detail, display writes_to)
_MASK_STEPS: dict[str, tuple] = {
    # L0 intake
    "POST /upload/stage":        ("receive_files",  "accept upload(s), batch group",     "(intake)"),
    "sheet split":               ("split_sheets",   "workbook → one entry per sheet",    "(intake)"),
    "content_hash":              ("fingerprint",    "content hash — dedup key",          "(intake)"),
    "quality scan":              ("quality_scan",   "empty/unreadable → quarantine",     "(intake)"),
    "write_manifest":            ("record_intake",  "status · files · hashes",           "(intake)"),
    # L1 preflight
    "infer_contract":            ("infer_schema",   "column types + load key",           "(plan)"),
    "check_against_contract":    ("check_drift",    "schema drift vs prior",             "(plan)"),
    "detect_load_key":           ("detect_key",     "primary / composite key",           "(plan)"),
    # L2 load
    "drift gate":                ("drift_gate",     "drift → quarantine else proceed",   "(intake)"),
    "file_hash_seen":            ("dedup_files",    "skip already-loaded files",         "(plan)"),
    "delete_where_period/batch": ("clean_reload",   "clean reload of same period",       "(primary store)"),
    "_is_id_colname → TEXT":     ("preserve_codes", "ID/code cols kept as text",         "(primary store)"),
    "copy_csv / stream_xlsx":    ("stream_load",    "streaming load (no OOM)",           "(primary store)"),
    "stamp_lineage":             ("stamp_lineage",  "source · period · batch · hash",    "(primary store)"),
    # L3 per-table learning
    "drift detect":              ("drift_detect",   "fingerprint — skip unchanged",      "(metadata)"),
    "profile_v2":                ("profile",        "types · roles · stats",             "(metadata)"),
    "dimension_catalog":         ("dimension_catalog", "top distinct values per dim",    "(metadata)"),
    "deep_analysis":             ("analyze",        "narrative table analysis",          "(knowledge)"),
    "qa_generation":             ("gen_pairs",      "question→answer training pairs",    "Training Q&A"),
    "persona":                   ("persona",        "agent persona for data",            "(knowledge)"),
    "synthesis":                 ("synthesize",     "table summary",                     "(knowledge)"),
    "workflows":                 ("workflows",      "common multi-step flows",           "(knowledge)"),
    "knowledge index":           ("index",          "chunk + embed → vectors",           "Vector index"),
    "brain fill":                ("facts_fill",     "company facts / rules",             "(company brain)"),
    "domain_knowledge":          ("domain_rules",   "domain rules + glossary",           "(knowledge)"),
    "persona enrich":            ("persona_enrich", "enrich persona",                    "(knowledge)"),
    # L4 linking
    "_discover_relationships":   ("propose_links",  "propose join candidates",           "(links)"),
    "verify (SQL containment)":  ("verify_links",   "overlap % · timeout 30s",           "(links)"),
    "_seed_cross_table_qa":      ("gen_join_pairs", "join question→answer pairs",        "Training Q&A"),
    # L5 model design
    "Engineer.inspect_schema":   ("probe_structure",   "read-only: cols + roles",        "(internal)"),
    "Engineer.get_relationships":("probe_links",       "read-only: join keys",           "(internal)"),
    "Engineer.sample_rows":      ("probe_samples",     "read-only: sample rows",         "(internal)"),
    "Engineer.dry_run_sql":      ("validate_candidate","dry check candidate, iterate",   "(internal)"),
    "→ SemanticLayerPlan (struct)": ("→ plan (sealed)","trust boundary: model stops here","(sealed)"),
    "validate_matview_spec":     ("validate_spec",  "whitelist gate",                    "(gate)"),
    "EXPLAIN dry-run (server)":  ("server_check",   "reject on error / timeout",         "(gate)"),
    "build_ddl + CREATE (1 txn)":("materialize",    "build managed view (1 txn)",        "(managed view)"),
    "register":                  ("register",       "mark managed · refresh rule",       "(metadata)"),
    # L6 network + backfill
    "knowledge_graph":           ("build_network",  "entities + edges",                  "Graph store"),
    "subagent_synthesis":        ("cross_synth",    "cross-source synthesis",            "(knowledge)"),
    "vector_backfill":           ("vector_backfill","embed rows missing vectors",        "Vector index"),
    "codex_code_enrich":         ("logic_enrich",   "pipeline-logic enrichment",         "(metadata)"),
    # L7 finishing
    "scope":                     ("scope",          "derive feature / answer scope",     "(config)"),
    "goals":                     ("goals",          "learning goals",                    "(knowledge)"),
    "ml":                        ("ml",             "no-op (disabled)",                  "—"),
    "evals (gen + run)":         ("evals",          "golden set → run → score",          "(eval store)"),
    "auto_configure":            ("auto_configure", "vertical detect + pack apply",      "(history)"),
    # L8 post-processing
    "bilingual twins":           ("bilingual_pairs","bilingual question→answer twins",   "Training Q&A"),
    "catalog vectors":           ("catalog_vectors","embed catalog → hybrid search",     "Vector index"),
    "◆ articles_enriched view":  ("◆ enriched_view","always: merged + flag",             "(managed view)"),
    "◆ detect_gaps":             ("◆ detect_gaps",  "count blank fields per col",        "(report)"),
    "◆ retrieve_examples":       ("◆ retrieve_examples","ground on labeled rows",        "(internal)"),
    "◆ run_enrichment":          ("◆ suggest_fills","suggest missing → pending",         "(enrichment)"),
    "◆ auto_apply_low_risk":     ("◆ auto_apply",   "apply low-risk; clinical never",    "(enrichment)"),
    "◆ rebuild enriched view":   ("◆ rebuild_view", "reflect new approvals",             "(managed view)"),
    "shop_flat build":           ("denorm_build",   "flatten + join; orphans flagged",   "(primary store)"),
    "◆ matview refresh":         ("◆ refresh_views","refresh managed views",             "(managed view)"),
    # L9 done
    "status finalizing→done":    ("finalize",       "flip after all hooks",              "(run state)"),
    "watchdog clear":            ("watchdog_clear", "stop tracking",                     "(run state)"),
    "UI panels live":            ("panels_live",    "dashboards live",                   "—"),
}

_MASK_TITLES: dict[int, str] = {
    0: "STAGE 0 · INTAKE  (review before load)",
    1: "STAGE 1 · PREFLIGHT  (preview, no write)",
    2: "STAGE 2 · LOAD  (staged → store)",
    3: "STAGE 3 · PER-TABLE LEARNING  (×N tables)",
    4: "STAGE 4 · LINKING  (cross-table)",
    5: "STAGE 5 · MODEL DESIGN  (internal optimizer)",
    6: "STAGE 6 · NETWORK + BACKFILL",
    7: "STAGE 7 · FINISHING  (concurrent)",
    8: "STAGE 8 · POST-PROCESSING  (sequential)",
    9: "STAGE 9 · DONE",
}

# scrub proprietary tokens out of dynamic (log-derived) value text
_SCRUB = [(_re_mask.compile(p, _re_mask.I), r) for p, r in [
    (r"apache\s+age\s+graph", "graph store"),
    (r"apache\s+age", "graph store"),
    (r"\bage\s+graph\b", "graph store"),
    (r"\bpgvector\b", "vector index"),
    (r"\bopencypher\b", "graph"),
    (r"\bcypher\b", "graph"),
    (r"materiali[sz]ed\s+view", "managed view"),
    (r"\bmatview(s)?\b", "managed view"),
    (r"\bcitypharma\.", ""),
    (r"\bshop_flat\b", "dataset"),
    (r"\barticles_enriched\b", "managed view"),
    (r"\barticles_clean\b", "dataset"),
    (r"\bbalance_stock\b", "dataset"),
    (r"\bdash_table_metadata\b", "metadata"),
    (r"\bdash_training_qa\b", "training Q&A"),
    (r"\bdash_training_runs\b", "run"),
    (r"\bdash_relationships\b", "links"),
    (r"\bdash_eval_runs\b", "eval"),
    (r"\bdash_evals\b", "eval"),
    (r"\bdash_company_brain\b", "brain"),
    (r"\bcatalog_enrichment\b", "enrichment"),
    (r"\bpostgresql\b", "primary store"),
    (r"\bpostgres\b", "primary store"),
    (r"\b_norm\b", ""),
]]


# identifier-like real step labels (knowledge_graph, qa_generation, …) can also
# appear verbatim inside dynamic log values ("step knowledge_graph: ran 4910ms")
# and in run.progress.step — map those tokens to their masked label too.
_LABEL_SCRUB = sorted(
    [(_re_mask.compile(r"\b" + _re_mask.escape(real) + r"\b"), mk[0])
     for real, mk in _MASK_STEPS.items()
     if _re_mask.fullmatch(r"[A-Za-z0-9_]+", real) and len(real) > 3],
    key=lambda x: -len(x[0].pattern),
)


def _scrub(s):
    if not s:
        return s
    for rx, rep in _LABEL_SCRUB:
        s = rx.sub(rep, s)
    for rx, rep in _SCRUB:
        s = rx.sub(rep, s)
    return s


def _mask_step(step: dict) -> dict:
    out = dict(step)
    m = _MASK_STEPS.get(step.get("label", ""))
    if m:
        out["label"], out["detail"], out["writes_to"] = m[0], m[1], m[2]
    if out.get("value"):
        out["value"] = _scrub(out["value"])
    return out


def _mask_flow(result: dict) -> dict:
    """In-place mask of a derive_flow() result. No internal names survive."""
    for L in result.get("layers", []):
        L["title"] = _MASK_TITLES.get(L.get("idx"), L.get("title"))
        L["steps"] = [_mask_step(s) for s in L.get("steps", [])]
    run = result.get("run") or {}
    if run.get("current_step"):
        run["current_step"] = _scrub(run["current_step"])
    prog = run.get("progress")
    if isinstance(prog, dict):
        for k in ("step", "table"):
            if prog.get(k):
                prog[k] = _scrub(prog[k])
    return result


# ---------------------------------------------------------------------------
# Layer-progress keyword mapping (edit here to tune)
#
# Used to derive a MONOTONIC `done_through_layer` from the run's ordered log
# stream + current_step. Every layer 0..9 has at least one keyword so a forward
# scan can map any progress signal to the LAYER it belongs to. We always pick
# the EARLIEST layer a token matches (so an ambiguous token marks earlier work
# done, never later — the contract's forward-only rule).
# ---------------------------------------------------------------------------

# Ordered (layer_idx, [keywords]). Internal real labels — matched BEFORE masking.
_LAYER_KEYWORDS: list[tuple[int, list[str]]] = [
    (0, ["upload", "stage", "staging", "manifest", "intake", "content_hash", "quality scan", "receive"]),
    (1, ["infer_contract", "contract", "dry-run", "dry run", "preflight", "detect_load_key", "load key"]),
    (2, ["promote", "ingest", "copy_csv", "stream_xlsx", "stream load", "loading", "delete_where", "stamp_lineage", "load table", "loaded"]),
    (3, ["training table", "profile", "deep_analysis", "qa_generation", "qa generation",
         "persona", "synthesis", "workflows", "knowledge index", "knowledge indexed",
         "indexing knowledge", "brain fill", "domain_knowledge", "per-table", "table training"]),
    (4, ["relationship", "discover_relationship", "verify (sql", "cross_table_qa", "join candidate", "cross-table"]),
    (5, ["semantic layer", "engineer", "matview", "materialized view", "semantic_layer", "designing semantic"]),
    (6, ["knowledge_graph", "knowledge graph", "subagent_synthesis", "cross-source", "vector_backfill",
         "vector backfill", "codex_code_enrich", "pipeline code", "pipeline logic", "graph"]),
    (7, ["scope", "guardrail", "goals", "learning_goals", "learning goals", "evals", "eval", "auto_configure",
         "auto-detect", "vertical"]),
    (8, ["catalog", "enrich", "shop_flat", "shop flat", "bilingual", "twins", "articles_enriched",
         "detect_gaps", "matview refresh", "post-hook", "post hook"]),
    (9, ["finalizing", "finalize", "training done", "watchdog", "panels live", "complete", "done"]),
]

# Backwards-compat alias (some callers/tests referenced KEYWORDS).
KEYWORDS: dict[int, list[str]] = {idx: kws for idx, kws in _LAYER_KEYWORDS}


def _layer_for_text(s: str) -> Optional[int]:
    """Map a lowercased progress string to the EARLIEST layer whose keyword it
    matches (forward order). Returns None if nothing matches."""
    if not s:
        return None
    for idx, kws in _LAYER_KEYWORDS:
        if any(k in s for k in kws):
            return idx
    return None

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
    run_logs: list = []
    try:
        with eng.connect() as conn:
            row = conn.execute(text("""
                SELECT id, status, current_step, stage_progress, current_progress,
                       tables_trained, started_at, finished_at, logs
                FROM public.dash_training_runs
                WHERE project_slug = :slug
                ORDER BY started_at DESC NULLS LAST
                LIMIT 1
            """), {"slug": slug}).mappings().fetchone()
            if row:
                _status = row["status"]
                _cstep = (row["current_step"] or "")
                # phase ∈ {running, finalizing, done, idle, failed}
                if _status == "done":
                    _phase = "done"
                elif _status == "failed":
                    _phase = "failed"
                elif _status in ("running", "queued", "finalizing"):
                    if _status == "finalizing" or _cstep.strip().lower() == "finalizing":
                        _phase = "finalizing"
                    else:
                        _phase = "running"
                else:
                    _phase = "idle"
                run = {
                    "id": row["id"],
                    "status": _status,
                    "phase": _phase,
                    "current_step": row["current_step"],
                    "started_at": _iso_utc(row["started_at"]),
                    "finished_at": _iso_utc(row["finished_at"]),
                    "stage_progress": row["stage_progress"],
                    "progress": row["current_progress"] if row["current_progress"] else {},
                }
                _raw_logs = row["logs"]
                if isinstance(_raw_logs, str):
                    try:
                        import json as _json
                        _raw_logs = _json.loads(_raw_logs)
                    except Exception:
                        _raw_logs = []
                run_logs = _raw_logs if isinstance(_raw_logs, list) else []
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

    # ---- ordered progress signal ----------------------------------------------
    # Replace keyword-matching the CURRENT step (which could mark a late layer
    # done while an earlier one is blank) with a MONOTONIC "how far did we get"
    # index. We forward-scan the run's ordered log stream (most reliable — every
    # step appends a log line), mapping each line to the earliest layer it names,
    # and take the MAX layer reached. current_step + progress.step are folded in
    # as a final signal. By construction `current_layer` only ever advances.
    r_status = (run.get("status") if run else "") or ""
    current_step_txt = (run.get("current_step") or "").lower() if run else ""
    progress_step_txt = ""
    if run:
        _prog = run.get("progress") or {}
        if isinstance(_prog, dict):
            progress_step_txt = (_prog.get("step") or "").lower()

    # max layer reached so far (the "current"/in-progress layer for a live run)
    current_layer = -1
    if run is not None:
        for ev in run_logs:
            try:
                _li = _layer_for_text(str(ev.get("msg", "")).lower())
            except Exception:
                _li = None
            if _li is not None and _li > current_layer:
                current_layer = _li
        for _txt in (progress_step_txt, current_step_txt):
            _li = _layer_for_text(_txt)
            if _li is not None and _li > current_layer:
                current_layer = _li
        # A live run is always at least staging.
        if current_layer < 0 and r_status in ("running", "queued", "finalizing"):
            current_layer = 0
        # finalizing → we've reached the DONE layer's lead-in.
        if r_status == "finalizing" and current_layer < 9:
            current_layer = 9

    def _layer_status(idx: int) -> str:
        # Gated layers first (independent of run progress).
        if idx == 5 and not engineer_flag:
            return "skipped"
        if run is None:
            return "idle"

        # Terminal: a finished run → every (non-gated) layer is done.
        if r_status == "done":
            if idx == 5:
                return "done" if engineer_flag else "skipped"
            return "done"

        # Failed: layers up to where we got = done, the layer we died on = error,
        # the rest = idle. Monotonic by construction.
        if r_status == "failed":
            if idx < current_layer:
                return "done"
            if idx == current_layer:
                return "error"
            return "idle"

        # Live (running/queued/finalizing): contiguous prefix done, exactly one
        # 'running' at current_layer, the rest idle.
        if r_status in ("running", "queued", "finalizing"):
            if idx < current_layer:
                return "done"
            if idx == current_layer:
                return "running"
            return "idle"

        # Unknown/non-terminal status → idle (never invents progress).
        return "idle"

    # ---- per-step live state from logs + dash_training_steps -------------------
    # Best-effort reconciliation of each static spec step against the run's
    # freetext log events (which already embed real values) + the sparse
    # dash_training_steps cache (tail steps carry status + elapsed_ms). No engine
    # change, no migration; works on the current/last run immediately.
    import re as _re

    step_rows: dict[str, dict] = {}
    if run is not None:
        try:
            with eng.connect() as conn:
                for r in conn.execute(text(
                    "SELECT name, status, elapsed_ms, error FROM public.dash_training_steps "
                    "WHERE project_slug = :slug"), {"slug": slug}).mappings().fetchall():
                    step_rows[str(r["name"]).lower()] = dict(r)
        except Exception as exc:
            logger.debug("flow_map: step_rows failed: %s", exc)

    _ICONS = "✓✔✗✘⚠◉·•●○└├─│┌┐ \t"
    _MS_RE = _re.compile(r"([\d.]+)\s*s\b")
    _MSMS_RE = _re.compile(r"(\d+)\s*ms\b")
    _COST_RE = _re.compile(r"\$([\d.]+)")

    def _tokens(label: str) -> list[str]:
        l = label.lower().strip()
        toks = {l, l.replace(" ", "_"), l.split()[0] if l.split() else l}
        # drop too-generic / symbol-only tokens that would mis-match
        return [t for t in toks if len(t) >= 4 and t not in ("post", "scan", "gate", "plan", "check")]

    def _match_log(label: str):
        toks = _tokens(label)
        if not toks:
            return None
        for ev in reversed(run_logs):
            msg = str(ev.get("msg", ""))
            ml = msg.lower()
            if any(t in ml for t in toks):
                return msg
        return None

    def _parse(msg: str, label: str) -> dict:
        ml = msg.lower()
        if "✗" in msg or "✘" in msg or "fail" in ml or "error" in ml:
            state = "error"
        elif "⚠" in msg or "quarantin" in ml or "skip" in ml:
            state = "warn"
        elif "✓" in msg or "✔" in msg or "done" in ml:
            state = "done"
        else:
            state = "running"
        ms = None
        mm = _MSMS_RE.search(msg)
        if mm:
            ms = int(mm.group(1))
        else:
            sm = _MS_RE.search(msg)
            if sm:
                try:
                    ms = int(float(sm.group(1)) * 1000)
                except Exception:
                    ms = None
        cost = None
        cm = _COST_RE.search(msg)
        if cm:
            try:
                cost = float(cm.group(1))
            except Exception:
                cost = None
        # clean value text: strip leading icons, "llm ·", and a leading "label:" / "label ·"
        # first non-empty line only — never leak multi-line log dumps (e.g. "[SQL:\n...")
        val = msg.strip()
        for _ln in val.splitlines():
            _ln = _ln.strip()
            if _ln:
                val = _ln
                break
        else:
            val = ""
        val = val.lstrip(_ICONS).strip()
        # drop bracketed section markers ("[SQL:", "[CONFIDENCE...]", etc.) — not real step values
        if val.startswith("[") or val.lower().startswith("[sql"):
            val = ""
        val = _re.sub(r"^llm\s*[·:]\s*", "", val, flags=_re.I)
        val = _re.sub(r"^" + _re.escape(label) + r"\s*[:·]\s*", "", val, flags=_re.I)
        val = val.strip().lstrip(_ICONS).strip()
        return {"state": state, "value": val[:80] or None, "ms": ms, "cost": cost}

    def _enrich_step(step: dict, lstatus: str, active_hint: str) -> dict:
        label = step.get("label", "")
        gate = step.get("gate")
        out = {**step, "state": "idle", "value": None, "ms": None, "cost": None}
        # gated by a disabled flag
        if (gate == "ENGINEER" and not engineer_flag) or (gate == "ENRICH" and not enrich_flag):
            out["state"] = "gated"
            return out
        # 1) sparse step cache (tail steps) wins for state + timing
        srow = step_rows.get(label.lower()) or step_rows.get(label.lower().replace(" ", "_"))
        if srow:
            st = str(srow.get("status") or "").lower()
            out["state"] = {"done": "done", "running": "running", "failed": "error",
                            "skipped": "gated", "queued": "idle"}.get(st, "idle")
            if srow.get("elapsed_ms") is not None:
                out["ms"] = int(srow["elapsed_ms"])
            if srow.get("error"):
                out["state"] = "error"; out["value"] = str(srow["error"])[:80]
        # 2) freetext log match → real value (+ ms/cost), refine state
        msg = _match_log(label)
        if msg:
            p = _parse(msg, label)
            out["value"] = p["value"] or out["value"]
            if p["ms"] is not None:
                out["ms"] = p["ms"]
            if p["cost"] is not None:
                out["cost"] = p["cost"]
            if not srow:
                out["state"] = p["state"]
        # 3) no signal at all → fall back to the layer's coarse status
        if not srow and not msg:
            if lstatus == "done":
                out["state"] = "done"
            elif lstatus == "skipped":
                out["state"] = "gated"
            elif lstatus == "running":
                out["state"] = "running" if any(t in active_hint for t in _tokens(label)) else "idle"
            else:
                out["state"] = "idle"
        # a finished layer has nothing actually running — a value-only log
        # (no ✓) must not show as live. Downgrade running→done; keep error/warn.
        if lstatus == "done" and out["state"] in ("running", "idle"):
            out["state"] = "done"
        return out

    def _active_hint() -> str:
        if run is None:
            return ""
        prog = run.get("progress") or {}
        ps = (prog.get("step") or "").lower() if isinstance(prog, dict) else ""
        return ((run.get("current_step") or "").lower() + " " + ps)

    layers_out = []
    _hint = _active_hint()
    for layer in LAYERS:
        idx = layer["idx"]
        lstatus = _layer_status(idx)
        esteps = [_enrich_step(s, lstatus, _hint) for s in layer.get("steps", [])]
        active = [s for s in esteps if s["state"] != "gated"]
        layers_out.append({
            **layer,
            "status": lstatus,
            "steps": esteps,
            "step_done": sum(1 for s in active if s["state"] == "done"),
            "step_total": len(active),
            "ms": sum(s["ms"] or 0 for s in esteps) or None,
            "cost": round(sum(s["cost"] or 0 for s in esteps), 4) or None,
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

    _result = {
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
    if _OBFUSCATE:
        _result = _mask_flow(_result)
    return _result
