#!/usr/bin/env python3
"""Animated CLI visualization of the FULL CityPharma training pipeline.

Super-detailed: every layer (staging → ingest → per-table → relationships →
semantic layer → graph/backfill → tail → post-hooks → done), every sub-step,
the model each step uses, and the data store each step writes to.

Usage:
  python3 scripts/show_training_workflow.py            # animated
  python3 scripts/show_training_workflow.py --static   # final snapshot
  python3 scripts/show_training_workflow.py --fast      # quick animation
  python3 scripts/show_training_workflow.py --no-color  # plain
  ENGINEER_SEMANTIC_LAYER=0 CATALOG_ENRICH=1 python3 scripts/show_training_workflow.py
"""
from __future__ import annotations
import os, sys, time

ENGINEER = os.environ.get("ENGINEER_SEMANTIC_LAYER", "1") in ("1", "true", "True")
ENRICH   = os.environ.get("CATALOG_ENRICH", "0") in ("1", "true", "True")

ARGS = set(sys.argv[1:])
STATIC  = "--static" in ARGS
FAST    = "--fast" in ARGS
NOCOLOR = "--no-color" in ARGS or not sys.stdout.isatty()
SPEED   = 0.0 if STATIC else (0.10 if FAST else 0.30)
SPIN    = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

def _c(x): return "" if NOCOLOR else x
RESET=_c("\033[0m"); BOLD=_c("\033[1m"); DIM=_c("\033[2m")
CYAN=_c("\033[36m"); GREEN=_c("\033[32m"); YEL=_c("\033[33m")
MAG=_c("\033[35m"); BLUE=_c("\033[34m"); GREY=_c("\033[90m"); RED=_c("\033[31m")
MC={"FLASH":MAG,"DEEP":BLUE,"LITE":YEL,"embed":CYAN,"SQL":GREY,"VISION":MAG,"":""}
def badge(m): return f"{MC.get(m,'')}[{m}]{RESET}" if m else ""

# Each step: (label, model, detail, writes_to, gate)
def phases():
    return [
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

def gated(g):
    return (g=="ENGINEER" and not ENGINEER) or (g=="ENRICH" and not ENRICH)

def render(step, status):
    label, model, detail, writes, gate = step
    if gated(gate):
        return f"     {GREY}○ {label}  ·  gated off{RESET}"
    mark = {"done":f"{GREEN}✓{RESET}","run":"{spin}","todo":f"{GREY}·{RESET}"}[status]
    pad = " " * max(1, 30-len(label))
    b = badge(model); bpad = " " if b else ""
    wr = f"{DIM}→ {writes}{RESET}" if writes and writes not in ("—","(gate)","(ctx)","(struct)","(plan)","(check)","(report)","(in-mem)","(agent ctx)") else f"{DIM}{writes}{RESET}"
    return f"     {mark} {label}{pad}{b}{bpad} {DIM}{detail}{RESET}  {wr}"

def ntot(ph): return sum(1 for _,steps in ph for s in steps if not gated(s[4]))

def run():
    ph=phases(); tot=ntot(ph); done=0
    print(f"\n{BOLD}{CYAN}╔════ CityPharma — FULL Training Pipeline (all layers) ════╗{RESET}")
    print(f"   {BOLD}flags{RESET}  ENGINEER_SEMANTIC_LAYER={GREEN if ENGINEER else RED}{'ON' if ENGINEER else 'OFF'}{RESET}  ·  "
          f"CATALOG_ENRICH={GREEN if ENRICH else RED}{'ON' if ENRICH else 'OFF'}{RESET}   {DIM}({tot} steps){RESET}")
    print(f"   {DIM}models {MAG}[FLASH]{DIM} chat+train · {BLUE}[DEEP]{DIM} reason · {YEL}[LITE]{DIM} routers · {CYAN}[embed]{DIM} vectors · {GREY}[SQL]{DIM} deterministic{RESET}")
    print(f"   {DIM}stores: Postgres tables · PgVector · Apache AGE graph · matviews · dash_* meta{RESET}\n")
    for title, steps in ph:
        print(f"{BOLD}{CYAN}▸ {title}{RESET}")
        for s in steps:
            if gated(s[4]):
                print(render(s,"todo")); continue
            if STATIC:
                print(render(s,"done")); done+=1; continue
            te=time.time()+SPEED; i=0
            while time.time()<te:
                sp=f"{YEL}{SPIN[i%len(SPIN)]}{RESET}"
                sys.stdout.write("\r"+render(s,"run").replace("{spin}",sp)+"\033[K"); sys.stdout.flush()
                time.sleep(0.07); i+=1
            done+=1
            sys.stdout.write("\r"+render(s,"done")+"\033[K\n"); sys.stdout.flush()
        fill=int(28*done/tot)
        print(f"   {DIM}{GREEN}{'█'*fill}{GREY}{'░'*(28-fill)}{RESET} {DIM}{done}/{tot}{RESET}\n")
    print(f"{BOLD}{GREEN}✓ pipeline complete{RESET}  {DIM}status=done · 3 matviews · gaps reviewable · eval 86.7%{RESET}\n")

if __name__=="__main__":
    try: run()
    except KeyboardInterrupt: print(f"\n{RED}interrupted{RESET}")
