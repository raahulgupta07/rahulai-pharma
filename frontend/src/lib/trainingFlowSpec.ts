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
    title: 'STAGE 0 · INTAKE  (review before load)',
    short: 'INTAKE',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'receive_files', model: '', detail: 'accept upload(s), batch group', writesTo: '(intake)', gate: null },
      { label: 'split_sheets', model: '', detail: 'workbook → one entry per sheet', writesTo: '(intake)', gate: null },
      { label: 'fingerprint', model: '', detail: 'content hash — dedup key', writesTo: '(intake)', gate: null },
      { label: 'quality_scan', model: '', detail: 'empty/unreadable → quarantine', writesTo: '(intake)', gate: null },
      { label: 'record_intake', model: '', detail: 'status · files · hashes', writesTo: '(intake)', gate: null },
    ],
  },
  {
    idx: 1,
    title: 'STAGE 1 · PREFLIGHT  (preview, no write)',
    short: 'PREFLIGHT',
    color: 'cyan',
    gate: null,
    steps: [
      { label: 'infer_schema', model: '', detail: 'column types + load key', writesTo: '(plan)', gate: null },
      { label: 'check_drift', model: '', detail: 'schema drift vs prior', writesTo: '(plan)', gate: null },
      { label: 'detect_key', model: '', detail: 'primary / composite key', writesTo: '(plan)', gate: null },
    ],
  },
  {
    idx: 2,
    title: 'STAGE 2 · LOAD  (staged → store)',
    short: 'LOAD',
    color: 'coral',
    gate: null,
    steps: [
      { label: 'drift_gate', model: '', detail: 'drift → quarantine else proceed', writesTo: '(intake)', gate: null },
      { label: 'dedup_files', model: '', detail: 'skip already-loaded files', writesTo: '(plan)', gate: null },
      { label: 'clean_reload', model: '', detail: 'clean reload of same period', writesTo: '(primary store)', gate: null },
      { label: 'preserve_codes', model: '', detail: 'ID/code cols kept as text', writesTo: '(primary store)', gate: null },
      { label: 'stream_load', model: '', detail: 'streaming load (no OOM)', writesTo: '(primary store)', gate: null },
      { label: 'stamp_lineage', model: '', detail: 'source · period · batch · hash', writesTo: '(primary store)', gate: null },
    ],
  },
  {
    idx: 3,
    title: 'STAGE 3 · PER-TABLE LEARNING  (×N tables)',
    short: 'LEARNING',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'drift_detect', model: 'SQL', detail: 'fingerprint — skip unchanged', writesTo: '(metadata)', gate: null },
      { label: 'profile', model: 'SQL', detail: 'types · roles · stats', writesTo: '(metadata)', gate: null },
      { label: 'dimension_catalog', model: 'SQL', detail: 'top distinct values per dim', writesTo: '(metadata)', gate: null },
      { label: 'analyze', model: 'DEEP', detail: 'narrative table analysis', writesTo: '(knowledge)', gate: null },
      { label: 'gen_pairs', model: 'FLASH', detail: 'question→answer training pairs', writesTo: 'Training Q&A', gate: null },
      { label: 'persona', model: 'FLASH', detail: 'agent persona for data', writesTo: '(knowledge)', gate: null },
      { label: 'synthesize', model: 'FLASH', detail: 'table summary', writesTo: '(knowledge)', gate: null },
      { label: 'workflows', model: 'FLASH', detail: 'common multi-step flows', writesTo: '(knowledge)', gate: null },
      { label: 'index', model: 'embed', detail: 'chunk + embed → vectors', writesTo: 'Vector index', gate: null },
      { label: 'facts_fill', model: '', detail: 'company facts / rules', writesTo: '(company brain)', gate: null },
      { label: 'domain_rules', model: 'DEEP', detail: 'domain rules + glossary', writesTo: '(knowledge)', gate: null },
      { label: 'persona_enrich', model: 'FLASH', detail: 'enrich persona', writesTo: '(knowledge)', gate: null },
    ],
  },
  {
    idx: 4,
    title: 'STAGE 4 · LINKING  (cross-table)',
    short: 'LINKING',
    color: 'cyan',
    gate: null,
    steps: [
      { label: 'propose_links', model: 'FLASH', detail: 'propose join candidates', writesTo: '(links)', gate: null },
      { label: 'verify_links', model: 'SQL', detail: 'overlap % · timeout 30s', writesTo: '(links)', gate: null },
      { label: 'gen_join_pairs', model: 'FLASH', detail: 'join question→answer pairs', writesTo: 'Training Q&A', gate: null },
    ],
  },
  {
    idx: 5,
    title: 'STAGE 5 · MODEL DESIGN  (internal optimizer)',
    short: 'MODEL DESIGN',
    color: 'coral',
    gate: 'ENGINEER',
    steps: [
      { label: 'probe_structure', model: 'DEEP', detail: 'read-only: cols + roles', writesTo: '(internal)', gate: 'ENGINEER' },
      { label: 'probe_links', model: 'DEEP', detail: 'read-only: join keys', writesTo: '(internal)', gate: 'ENGINEER' },
      { label: 'probe_samples', model: 'DEEP', detail: 'read-only: sample rows', writesTo: '(internal)', gate: 'ENGINEER' },
      { label: 'validate_candidate', model: 'DEEP', detail: 'dry check candidate, iterate', writesTo: '(internal)', gate: 'ENGINEER' },
      { label: '→ plan (sealed)', model: '', detail: 'trust boundary: model stops here', writesTo: '(sealed)', gate: 'ENGINEER' },
      { label: 'validate_spec', model: '', detail: 'whitelist gate', writesTo: '(gate)', gate: 'ENGINEER' },
      { label: 'server_check', model: 'SQL', detail: 'reject on error / timeout', writesTo: '(gate)', gate: 'ENGINEER' },
      { label: 'materialize', model: 'SQL', detail: 'build managed view (1 txn)', writesTo: '(managed view)', gate: 'ENGINEER' },
      { label: 'register', model: '', detail: 'mark managed · refresh rule', writesTo: '(metadata)', gate: 'ENGINEER' },
    ],
  },
  {
    idx: 6,
    title: 'STAGE 6 · NETWORK + BACKFILL',
    short: 'NETWORK',
    color: 'amber',
    gate: null,
    steps: [
      { label: 'build_network', model: '', detail: 'entities + edges', writesTo: 'Graph store', gate: null },
      { label: 'cross_synth', model: 'FLASH', detail: 'cross-source synthesis', writesTo: '(knowledge)', gate: null },
      { label: 'vector_backfill', model: 'embed', detail: 'embed rows missing vectors', writesTo: 'Vector index', gate: null },
      { label: 'logic_enrich', model: '', detail: 'pipeline-logic enrichment', writesTo: '(metadata)', gate: null },
    ],
  },
  {
    idx: 7,
    title: 'STAGE 7 · FINISHING  (concurrent)',
    short: 'FINISHING',
    color: 'cyan',
    gate: null,
    steps: [
      { label: 'scope', model: '', detail: 'derive feature / answer scope', writesTo: '(config)', gate: null },
      { label: 'goals', model: '', detail: 'learning goals', writesTo: '(knowledge)', gate: null },
      { label: 'ml', model: '', detail: 'no-op (disabled)', writesTo: '—', gate: null },
      { label: 'evals', model: 'FLASH', detail: 'golden set → run → score', writesTo: '(eval store)', gate: null },
      { label: 'auto_configure', model: '', detail: 'vertical detect + pack apply', writesTo: '(history)', gate: null },
    ],
  },
  {
    idx: 8,
    title: 'STAGE 8 · POST-PROCESSING  (sequential)',
    short: 'POST-PROC',
    color: 'coral',
    gate: 'ENRICH',
    steps: [
      { label: 'bilingual_pairs', model: 'FLASH', detail: 'bilingual question→answer twins', writesTo: 'Training Q&A', gate: null },
      { label: 'catalog_vectors', model: 'embed', detail: 'embed catalog → hybrid search', writesTo: 'Vector index', gate: null },
      { label: '◆ enriched_view', model: 'SQL', detail: 'always: merged + flag', writesTo: '(managed view)', gate: null },
      { label: '◆ detect_gaps', model: 'SQL', detail: 'count blank fields per col', writesTo: '(report)', gate: 'ENRICH' },
      { label: '◆ retrieve_examples', model: 'SQL', detail: 'ground on labeled rows', writesTo: '(internal)', gate: 'ENRICH' },
      { label: '◆ suggest_fills', model: 'FLASH', detail: 'suggest missing → pending', writesTo: '(enrichment)', gate: 'ENRICH' },
      { label: '◆ auto_apply', model: '', detail: 'apply low-risk; clinical never', writesTo: '(enrichment)', gate: 'ENRICH' },
      { label: '◆ rebuild_view', model: 'SQL', detail: 'reflect new approvals', writesTo: '(managed view)', gate: 'ENRICH' },
      { label: 'denorm_build', model: 'SQL', detail: 'flatten + join; orphans flagged', writesTo: '(primary store)', gate: null },
      { label: '◆ refresh_views', model: 'SQL', detail: 'refresh managed views', writesTo: '(managed view)', gate: 'ENGINEER' },
    ],
  },
  {
    idx: 9,
    title: 'STAGE 9 · DONE',
    short: 'DONE',
    color: 'coral',
    gate: null,
    steps: [
      { label: 'finalize', model: '', detail: 'flip after all hooks', writesTo: '(run state)', gate: null },
      { label: 'watchdog_clear', model: '', detail: 'stop tracking', writesTo: '(run state)', gate: null },
      { label: 'panels_live', model: '', detail: 'dashboards live', writesTo: '—', gate: null },
    ],
  },
];

// ─── Stores ──────────────────────────────────────────────────────────────────

export const STORES: StoreChip[] = [
  { key: 'STAGE', label: 'Intake', table: 'intake' },
  { key: 'PG', label: 'Primary store', table: 'data store' },
  { key: 'META', label: 'Metadata', table: 'metadata' },
  { key: 'QA', label: 'Training Q&A', table: 'training Q&A' },
  { key: 'VEC', label: 'Vector index', table: 'vector index' },
  { key: 'BRAIN', label: 'Knowledge base', table: 'knowledge' },
  { key: 'REL', label: 'Relationships', table: 'links' },
  { key: 'MV', label: 'Managed views', table: 'managed views' },
  { key: 'AGE', label: 'Graph store', table: 'graph store' },
  { key: 'ENR', label: 'Enrichment', table: 'enrichment' },
  { key: 'EVAL', label: 'Eval runs', table: 'eval runs' },
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
