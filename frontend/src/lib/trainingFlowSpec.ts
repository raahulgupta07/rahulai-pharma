/**
 * trainingFlowSpec.ts
 * Pure static data describing the CityPharma 10-layer / ~55-step training pipeline.
 * Source of truth: scripts/show_training_workflow.py phases()
 * No runtime logic, no Svelte — just typed exported constants.
 */

export type StepModel = 'SQL' | 'FLASH' | 'DEEP' | 'embed' | '';

export interface FlowStep {
  label: string;
  model: StepModel;
  detail: string;
  writesTo: string;
  gate: 'ENGINEER' | 'ENRICH' | null;
}

export interface FlowLayer {
  idx: number;
  title: string;
  short: string;
  color: 'amber' | 'cyan' | 'coral';
  gate: 'ENGINEER' | 'ENRICH' | null;
  steps: FlowStep[];
}

export interface StoreChip {
  key: string;
  label: string;
  table: string;
}

// ─── 10 Layers ──────────────────────────────────────────────────────────────

export const FLOW_LAYERS: FlowLayer[] = [
  {
    idx: 0,
    title: 'LAYER 0 · STAGING  (dash/ingest/ — review before data hits a table)',
    short: 'STAGING',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'POST /upload/stage',  model: '', detail: 'receive file(s), multi-file batch via batch_id',  writesTo: 'knowledge/<proj>/staging/', gate: null },
      { label: 'sheet split',         model: '', detail: 'xlsx → one staged entry per sheet',               writesTo: 'staging/',                  gate: null },
      { label: 'content_hash',        model: '', detail: 'sha of bytes — dedup key',                        writesTo: 'manifest.json',             gate: null },
      { label: 'quality scan',        model: '', detail: 'empty/unreadable → quarantine',                   writesTo: 'manifest(status)',          gate: null },
      { label: 'write_manifest',      model: '', detail: 'status=staged, files[], hashes',                  writesTo: 'manifest.json',             gate: null },
    ],
  },
  {
    idx: 1,
    title: 'LAYER 1 · DRY-RUN  (preview, no DB write)',
    short: 'DRY-RUN',
    color: 'cyan',
    gate: null,
    steps: [
      { label: 'infer_contract',        model: '', detail: 'column types + load-key detect',          writesTo: 'dash_dataset_contracts', gate: null },
      { label: 'check_against_contract', model: '', detail: 'schema-drift vs prior contract',        writesTo: '(plan)',                  gate: null },
      { label: 'detect_load_key',       model: '', detail: 'PK / composite key for upserts',         writesTo: '(plan)',                  gate: null },
    ],
  },
  {
    idx: 2,
    title: 'LAYER 2 · PROMOTE → INGEST  (staged → Postgres)',
    short: 'PROMOTE',
    color: 'coral',
    gate: null,
    steps: [
      { label: 'drift gate',                  model: '', detail: 'schema drift → quarantine, else proceed',          writesTo: 'manifest',             gate: null },
      { label: 'file_hash_seen',              model: '', detail: 'skip already-loaded files (dedup)',                writesTo: '(check)',              gate: null },
      { label: 'delete_where_period/batch',   model: '', detail: 'clean reload of same period',                     writesTo: 'citypharma.<table>',   gate: null },
      { label: '_is_id_colname → TEXT',       model: '', detail: 'ID/code cols kept as text (Excel-safe)',          writesTo: 'citypharma.<table>',   gate: null },
      { label: 'copy_csv / stream_xlsx',      model: '', detail: 'streaming COPY (big files, no OOM)',              writesTo: 'citypharma.<table>',   gate: null },
      { label: 'stamp_lineage',               model: '', detail: '_source_file/_period/_batch_id/_hash',            writesTo: 'citypharma.<table>',   gate: null },
    ],
  },
  {
    idx: 3,
    title: 'LAYER 3 · PER-TABLE TRAINING LOOP  (×N source tables; derived excluded)',
    short: 'TRAIN',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'drift detect',       model: 'SQL',   detail: 'fingerprint — skip unchanged table',                  writesTo: 'dash_table_metadata',              gate: null },
      { label: 'profile_v2',         model: 'SQL',   detail: 'types · roles(id/dim/measure) · pg_stats',            writesTo: 'dash_table_metadata.profile_v2',   gate: null },
      { label: 'dimension_catalog',  model: 'SQL',   detail: 'top-20 distinct values per dim col',                  writesTo: 'dash_table_metadata.dimensions',   gate: null },
      { label: 'deep_analysis',      model: 'DEEP',  detail: 'narrative analysis of the table',                     writesTo: 'knowledge/<proj>/',                gate: null },
      { label: 'qa_generation',      model: 'FLASH', detail: 'Q→SQL training pairs',                               writesTo: 'dash_training_qa',                 gate: null },
      { label: 'persona',            model: 'FLASH', detail: 'agent persona for the data',                         writesTo: 'knowledge persona',                gate: null },
      { label: 'synthesis',          model: 'FLASH', detail: 'table synthesis summary',                            writesTo: 'knowledge synthesis',              gate: null },
      { label: 'workflows',          model: 'FLASH', detail: 'common multi-step workflows',                        writesTo: 'knowledge workflows',              gate: null },
      { label: 'knowledge index',    model: 'embed', detail: 'chunk + embed → vector store',                       writesTo: 'PgVector',                         gate: null },
      { label: 'brain fill',         model: '',      detail: 'company-brain facts/rules',                          writesTo: 'dash_company_brain',               gate: null },
      { label: 'domain_knowledge',   model: 'DEEP',  detail: 'domain rules + glossary',                            writesTo: 'dash_memories/rules_db',           gate: null },
      { label: 'persona enrich',     model: 'FLASH', detail: 'enrich persona (lenient JSON)',                      writesTo: 'knowledge persona',                gate: null },
    ],
  },
  {
    idx: 4,
    title: 'LAYER 4 · RELATIONSHIPS  (cross-table)',
    short: 'RELATE',
    color: 'cyan',
    gate: null,
    steps: [
      { label: '_discover_relationships',   model: 'FLASH', detail: 'LLM proposes FK/join candidates',              writesTo: '(in-mem)',         gate: null },
      { label: 'verify (SQL containment)',  model: 'SQL',   detail: 'directional overlap %, timeout 30s',           writesTo: 'dash_relationships', gate: null },
      { label: '_seed_cross_table_qa',      model: 'FLASH', detail: 'JOIN Q→SQL pairs from verified links',         writesTo: 'dash_training_qa', gate: null },
    ],
  },
  {
    idx: 5,
    title: 'LAYER 5 · ◆ SEMANTIC LAYER  (Engineer agent designs matviews)',
    short: 'SEMANTIC',
    color: 'coral',
    gate: 'ENGINEER',
    steps: [
      { label: 'Engineer.inspect_schema',      model: 'DEEP', detail: 'READ-ONLY: cols + roles',                           writesTo: '(agent ctx)', gate: 'ENGINEER' },
      { label: 'Engineer.get_relationships',   model: 'DEEP', detail: 'READ-ONLY: verified join keys',                     writesTo: '(agent ctx)', gate: 'ENGINEER' },
      { label: 'Engineer.sample_rows',         model: 'DEEP', detail: 'READ-ONLY: 5 rows (real formats)',                  writesTo: '(agent ctx)', gate: 'ENGINEER' },
      { label: 'Engineer.dry_run_sql',         model: 'DEEP', detail: 'EXPLAIN candidate SELECT, iterate',                 writesTo: '(agent ctx)', gate: 'ENGINEER' },
      { label: '→ SemanticLayerPlan (struct)',  model: '',     detail: '═ TRUST BOUNDARY: model stops here ═',             writesTo: '(struct)',     gate: 'ENGINEER' },
      { label: 'validate_matview_spec',        model: '',     detail: 'whitelist: no DDL/;/comments/x-schema',            writesTo: '(gate)',       gate: 'ENGINEER' },
      { label: 'EXPLAIN dry-run (server)',      model: 'SQL',  detail: 'reject on error/timeout',                          writesTo: '(gate)',       gate: 'ENGINEER' },
      { label: 'build_ddl + CREATE (1 txn)',    model: 'SQL',  detail: 'DROP→CREATE MATVIEW→unique idx',                   writesTo: 'citypharma.<matview>', gate: 'ENGINEER' },
      { label: 'register',                     model: '',     detail: 'semantic_layer=true, refresh_sql',                  writesTo: 'dash_table_metadata', gate: 'ENGINEER' },
    ],
  },
  {
    idx: 6,
    title: 'LAYER 6 · GRAPH + BACKFILL',
    short: 'GRAPH',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'knowledge_graph',    model: '',      detail: 'entities + edges (openCypher)',                        writesTo: 'Apache AGE graph',                         gate: null },
      { label: 'subagent_synthesis', model: 'FLASH', detail: 'cross-source synthesis (needs KG)',                   writesTo: 'knowledge',                                gate: null },
      { label: 'vector_backfill',    model: 'embed', detail: 'embed any rows missing vectors',                      writesTo: 'PgVector',                                 gate: null },
      { label: 'codex_code_enrich',  model: '',      detail: 'pipeline_logic enrichment',                           writesTo: 'dash_table_metadata.pipeline_logic',       gate: null },
    ],
  },
  {
    idx: 7,
    title: 'LAYER 7 · TAIL  (4 concurrent)',
    short: 'TAIL',
    color: 'cyan',
    gate: null,
    steps: [
      { label: 'scope',           model: '',      detail: 'derive feature/answer scope',                   writesTo: 'feature_config.scope',         gate: null },
      { label: 'goals',           model: '',      detail: 'learning_goals.md',                             writesTo: 'knowledge goals',              gate: null },
      { label: 'ml',              model: '',      detail: 'NO-OP — AutoML removed (purges row)',            writesTo: '—',                            gate: null },
      { label: 'evals (gen + run)', model: 'FLASH', detail: 'golden set → run → score',                   writesTo: 'dash_evals / dash_eval_runs',  gate: null },
      { label: 'auto_configure',  model: '',      detail: 'vertical detect + pack apply',                  writesTo: 'dash_auto_apply_history',      gate: null },
    ],
  },
  {
    idx: 8,
    title: 'LAYER 8 · POST-HOOKS  (sequential — order matters)',
    short: 'POST-HOOKS',
    color: 'coral',
    gate: 'ENRICH',
    steps: [
      { label: 'bilingual twins',              model: 'FLASH', detail: '53 Burmese Q→SQL twins',                                          writesTo: 'dash_training_qa',               gate: null },
      { label: 'catalog vectors',              model: 'embed', detail: 'embed catalog → hybrid search',                                   writesTo: 'PgVector',                        gate: null },
      { label: '◆ articles_enriched view',     model: 'SQL',   detail: 'ALWAYS: COALESCE(source,approved)+is_enriched',                  writesTo: 'citypharma.articles_enriched',    gate: null },
      { label: '◆ detect_gaps',                model: 'SQL',   detail: 'count blank fields per col',                                      writesTo: '(report)',                        gate: 'ENRICH' },
      { label: '◆ retrieve_examples',          model: 'SQL',   detail: 'ground on labeled rows (few-shot)',                               writesTo: '(ctx)',                           gate: 'ENRICH' },
      { label: '◆ run_enrichment',             model: 'FLASH', detail: 'suggest missing → pending only',                                  writesTo: 'citypharma.catalog_enrichment',   gate: 'ENRICH' },
      { label: '◆ auto_apply_low_risk',        model: '',      detail: 'category/indication ≥0.9; clinical NEVER',                        writesTo: 'catalog_enrichment.status',       gate: 'ENRICH' },
      { label: '◆ rebuild enriched view',      model: 'SQL',   detail: 'reflect new approvals',                                           writesTo: 'articles_enriched',               gate: 'ENRICH' },
      { label: 'shop_flat build',              model: 'SQL',   detail: 'reads enriched · _norm join · orphans linked=false',              writesTo: 'citypharma.shop_flat',            gate: null },
      { label: '◆ matview refresh',            model: 'SQL',   detail: 'REFRESH MATERIALIZED VIEW CONCURRENTLY',                         writesTo: 'citypharma.<matview>',            gate: 'ENGINEER' },
    ],
  },
  {
    idx: 9,
    title: 'LAYER 9 · DONE',
    short: 'DONE',
    color: 'coral',
    gate: null,
    steps: [
      { label: 'status finalizing→done', model: '', detail: 'flip only after all post-hooks',          writesTo: 'dash_training_runs', gate: null },
      { label: 'watchdog clear',         model: '', detail: '_reap_stale_runs stops tracking',         writesTo: 'dash_training_runs', gate: null },
      { label: 'UI panels live',         model: '', detail: 'Semantic Layer + Catalog Gaps + flow',    writesTo: '—',                  gate: null },
    ],
  },
];

// ─── Stores ──────────────────────────────────────────────────────────────────

export const STORES: StoreChip[] = [
  { key: 'STAGE', label: 'Staging files',        table: 'staging files' },
  { key: 'PG',    label: 'Postgres tables',       table: 'citypharma.<tables>' },
  { key: 'META',  label: 'Table metadata',        table: 'dash_table_metadata' },
  { key: 'QA',    label: 'Training Q&A',          table: 'dash_training_qa' },
  { key: 'VEC',   label: 'PgVector',              table: 'PgVector' },
  { key: 'BRAIN', label: 'Company brain',         table: 'dash_company_brain' },
  { key: 'REL',   label: 'Relationships',         table: 'dash_relationships' },
  { key: 'MV',    label: 'Matviews',              table: 'matviews' },
  { key: 'AGE',   label: 'Apache AGE graph',      table: 'Apache AGE graph' },
  { key: 'ENR',   label: 'Catalog enrichment',    table: 'catalog_enrichment' },
  { key: 'EVAL',  label: 'Eval runs',             table: 'dash_eval_runs' },
];

// ─── Derived constants ───────────────────────────────────────────────────────

export const LAYER_COLORS: Array<'amber' | 'cyan' | 'coral'> = [
  'amber', // 0
  'cyan',  // 1
  'coral', // 2
  'amber', // 3
  'cyan',  // 4
  'coral', // 5
  'amber', // 6
  'cyan',  // 7
  'coral', // 8
  'coral', // 9
];

export const TOTAL_STEPS: number = FLOW_LAYERS.reduce(
  (sum, layer) => sum + layer.steps.length,
  0
);
