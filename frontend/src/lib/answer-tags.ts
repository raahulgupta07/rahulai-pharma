// Tag parser module for exec-view layout.
// Each parser matches a bracketed tag, returns { items, stripped }.
// Pure functions, zero dependencies. Mirrors parseHeadline/parseBecause/etc.

function _matches(content: string, re: RegExp): string[] {
  const out: string[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (raw) out.push(raw);
  }
  return out;
}

function _strip(content: string, re: RegExp): string {
  return content.replace(re, '');
}

function _parts(raw: string, n: number): string[] {
  const parts = raw.split('|').map((p) => p.trim());
  while (parts.length < n) parts.push('');
  return parts;
}

// 1. [ACTION_TITLE: text]
export function parseActionTitle(content: string): { items: string[]; stripped: string } {
  const re = /\[ACTION_TITLE:\s*([^\]]+)\]/g;
  return { items: _matches(content, re), stripped: _strip(content, re) };
}

// 2. [NARRATION: paragraph]
export function parseNarration(content: string): { items: string[]; stripped: string } {
  const re = /\[NARRATION:\s*([^\]]+)\]/g;
  return { items: _matches(content, re), stripped: _strip(content, re) };
}

// 3. [KPI: value|label|change]
export interface KpiItem {
  value: string;
  label: string;
  change: string;
}
export function parseKpis(content: string): { items: KpiItem[]; stripped: string } {
  const re = /\[KPI:\s*([^\]]+)\]/g;
  const items: KpiItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [value, label, change] = _parts(raw, 3);
    items.push({ value, label, change });
  }
  return { items, stripped: _strip(content, re) };
}

// 4. [ATTENTION: sku|name|days_out|daily_demand|loss_per_day|action]
export interface AttentionItem {
  sku: string;
  name: string;
  days_out: string;
  daily_demand: string;
  loss_per_day: string;
  action: string;
}
export function parseAttention(content: string): { items: AttentionItem[]; stripped: string } {
  const re = /\[ATTENTION:\s*([^\]]+)\]/g;
  const items: AttentionItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [sku, name, days_out, daily_demand, loss_per_day, action] = _parts(raw, 6);
    items.push({ sku, name, days_out, daily_demand, loss_per_day, action });
  }
  return { items, stripped: _strip(content, re) };
}

// 5. [SEGMENT: name|value|share|delta|status] — named parseSegmentBreakdown to avoid collision
export interface SegmentBreakdownItem {
  name: string;
  value: string;
  share: string;
  delta: string;
  status: string;
}
export function parseSegmentBreakdown(content: string): { items: SegmentBreakdownItem[]; stripped: string } {
  const re = /\[SEGMENT:\s*([^\]]+)\]/g;
  const items: SegmentBreakdownItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [name, value, share, delta, status] = _parts(raw, 5);
    items.push({ name, value, share, delta, status });
  }
  return { items, stripped: _strip(content, re) };
}

// 6. [RECOMMENDATION: priority|action|impact|effort|cta_label]
export interface RecommendationItem {
  priority: string;
  action: string;
  impact: string;
  effort: string;
  cta_label: string;
}
export function parseRecommendations(content: string): { items: RecommendationItem[]; stripped: string } {
  const re = /\[RECOMMENDATION:\s*([^\]]+)\]/g;
  const items: RecommendationItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [priority, action, impact, effort, cta_label] = _parts(raw, 5);
    items.push({ priority, action, impact, effort, cta_label });
  }
  return { items, stripped: _strip(content, re) };
}

// 7. [BENCHMARK: metric|yours|industry|rank|status]
export interface BenchmarkItem {
  metric: string;
  yours: string;
  industry: string;
  rank: string;
  status: string;
}
export function parseBenchmarks(content: string): { items: BenchmarkItem[]; stripped: string } {
  const re = /\[BENCHMARK:\s*([^\]]+)\]/g;
  const items: BenchmarkItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [metric, yours, industry, rank, status] = _parts(raw, 5);
    items.push({ metric, yours, industry, rank, status });
  }
  return { items, stripped: _strip(content, re) };
}

// 8. [SCENARIO: question|outcome|impact|recovery|mitigation]
export interface ScenarioItem {
  question: string;
  outcome: string;
  impact: string;
  recovery: string;
  mitigation: string;
}
export function parseScenarios(content: string): { items: ScenarioItem[]; stripped: string } {
  const re = /\[SCENARIO:\s*([^\]]+)\]/g;
  const items: ScenarioItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [question, outcome, impact, recovery, mitigation] = _parts(raw, 5);
    items.push({ question, outcome, impact, recovery, mitigation });
  }
  return { items, stripped: _strip(content, re) };
}

// 9. [FORECAST: target_date|value|confidence_interval|method]
export interface ForecastItem {
  target_date: string;
  value: string;
  confidence_interval: string;
  method: string;
}
export function parseForecasts(content: string): { items: ForecastItem[]; stripped: string } {
  const re = /\[FORECAST:\s*([^\]]+)\]/g;
  const items: ForecastItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [target_date, value, confidence_interval, method] = _parts(raw, 4);
    items.push({ target_date, value, confidence_interval, method });
  }
  return { items, stripped: _strip(content, re) };
}

// 10. [ROOT_CAUSE: driver|pct_contribution|description]
export interface RootCauseItem {
  driver: string;
  pct_contribution: string;
  description: string;
}
export function parseRootCause(content: string): { items: RootCauseItem[]; stripped: string } {
  const re = /\[ROOT_CAUSE:\s*([^\]]+)\]/g;
  const items: RootCauseItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [driver, pct_contribution, description] = _parts(raw, 3);
    items.push({ driver, pct_contribution, description });
  }
  return { items, stripped: _strip(content, re) };
}

// 11. [AUDIT: field|value]
export interface AuditItem {
  field: string;
  value: string;
}
export function parseAudit(content: string): { items: AuditItem[]; stripped: string } {
  const re = /\[AUDIT:\s*([^\]]+)\]/g;
  const items: AuditItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [field, value] = _parts(raw, 2);
    items.push({ field, value });
  }
  return { items, stripped: _strip(content, re) };
}

// 12. [MEANS: line]
export function parseMeans(content: string): { items: string[]; stripped: string } {
  const re = /\[MEANS:\s*([^\]]+)\]/g;
  return { items: _matches(content, re), stripped: _strip(content, re) };
}

// 13. [FRESHNESS: tbl|asof]
export interface FreshnessItem {
  table: string;
  as_of: string;
}
export function parseFreshness(content: string): { items: FreshnessItem[]; stripped: string } {
  const re = /\[FRESHNESS:\s*([^\]]+)\]/g;
  const items: FreshnessItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    const [table, as_of] = _parts(raw, 2);
    items.push({ table, as_of });
  }
  return { items, stripped: _strip(content, re) };
}

// 14. [LINEAGE: upstream→table]  (also supports -> )
export interface LineageItem {
  upstream: string;
  table: string;
}
export function parseLineage(content: string): { items: LineageItem[]; stripped: string } {
  const re = /\[LINEAGE:\s*([^\]]+)\]/g;
  const items: LineageItem[] = [];
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(content)) !== null) {
    const raw = (m[1] || '').trim();
    if (!raw) continue;
    // Split on → or ->
    const parts = raw.split(/→|->/).map((p) => p.trim());
    if (parts.length < 2) continue;
    const upstream = parts[0] || '';
    const table = parts[1] || '';
    if (!upstream || !table) continue;
    items.push({ upstream, table });
  }
  return { items, stripped: _strip(content, re) };
}

// 15. [SKILL_USED: name|id]  — single
export interface SkillUsedItem {
  name: string;
  id: string;
}
export function parseSkillUsed(content: string): SkillUsedItem | null {
  const re = /\[SKILL_USED:\s*([^\]]+)\]/;
  const m = content.match(re);
  if (!m) return null;
  const raw = (m[1] || '').trim();
  if (!raw) return null;
  const [name, id] = _parts(raw, 2);
  if (!name) return null;
  return { name, id };
}

// ── Clinical Monograph (chemist → chemist drug card) ──────────────────
// Agent emits these for drug / salt / substitute / stock-by-name questions:
//   [DRUG: salt|brand|status|class|article]   (required to trigger monograph)
//   [COMPOSITION: text]
//   [INDICATION: text]
//   [DOSE: text]
//   [CAUTION: text]        (repeatable — safety, red strip)
//   [INTERACTS: text]      (repeatable — safety, red strip)
//   [STOCK: qty|skus|branch|cost|status]
//   [EQUIV: name|qty|cost|article]   (repeatable — therapeutic equivalents)
//   [EVIDENCE: article|table]
export interface EquivItem { name: string; qty: string; cost: string; article: string; }
export interface Monograph {
  salt: string;
  brand: string;
  status: string;       // Rx | OTC | Controlled | ...
  klass: string;        // drug class
  article: string;      // primary article_code
  composition: string;
  indication: string;
  dose: string;
  caution: string[];
  interacts: string[];
  stock: { qty: string; skus: string; branch: string; cost: string; status: string } | null;
  equiv: EquivItem[];
  evidence: { article: string; table: string } | null;
}

// ── Universal "chemist-grade" card tags (v1 locked contract) ──────────
// The backend emits this lean set for analytical answers:
//   [HEADLINE: title] [LEAD: one sentence] [CONFIDENCE: HIGH|MEDIUM|LOW]
//   [SOURCE: name] [KPI: value|label|change?] [WHY: point]
//   [SO_WHAT: action|owner|effort] [RELATED: question]
// HEADLINE / CONFIDENCE / KPI / SO_WHAT / RELATED already have parsers
// (parseActionTitle fallback + AnswerCard inline regexes). These three are new.

// [LEAD: one sentence] — single. The one-line "so what does this answer say".
export function parseLead(content: string): { lead: string; stripped: string } {
  const re = /\[LEAD:\s*([^\]]+)\]/g;
  const items = _matches(content, re);
  return { lead: items[0] || '', stripped: _strip(content, re) };
}

// [WHY: point] — repeatable (1-3). The reasoning bullets behind the headline.
export function parseWhy(content: string): { items: string[]; stripped: string } {
  const re = /\[WHY:\s*([^\]]+)\]/g;
  return { items: _matches(content, re), stripped: _strip(content, re) };
}

// [SOURCE: name] — single. The table / dataset the answer came from.
export function parseSource(content: string): { source: string; stripped: string } {
  const re = /\[SOURCE:\s*([^\]]+)\]/g;
  const items = _matches(content, re);
  return { source: items[0] || '', stripped: _strip(content, re) };
}

// Catch-all strip for legacy / now-unused tags so a stray (or truncated) one
// never renders raw. This was a real bug: a half-streamed
// `[CONFIDENCE_BREAKDOWN:100|100|` leaked into the UI once. We strip both the
// well-formed `[TAG:...]` form AND a trailing truncated `[TAG:...` (no closing
// bracket, e.g. a stream cut mid-tag) for the legacy set.
const _LEGACY_STRIP_TAGS =
  'FINDING|ANCHOR|BECAUSE|KILL|ASSUMPTION|SEGMENT|CONFIDENCE_BREAKDOWN|MODE';
export function stripLegacyTags(content: string): string {
  if (!content) return content;
  const closed = new RegExp(`\\[(?:${_LEGACY_STRIP_TAGS}):[^\\]]*\\]`, 'g');
  // Truncated tag at end-of-buffer (streaming): `[TAG:....` with no `]` before EOL/EOS.
  const truncated = new RegExp(`\\[(?:${_LEGACY_STRIP_TAGS}):[^\\]\\n]*$`, 'gm');
  return content.replace(closed, '').replace(truncated, '');
}

export function parseMonograph(content: string): { mono: Monograph | null; stripped: string } {
  const reDrug = /\[DRUG:\s*([^\]]+)\]/;
  const dm = content.match(reDrug);
  if (!dm) return { mono: null, stripped: content };

  const [salt, brand, status, klass, article] = _parts((dm[1] || '').trim(), 5);
  if (!salt) return { mono: null, stripped: content };

  const one = (tag: string): string => {
    const m = content.match(new RegExp(`\\[${tag}:\\s*([^\\]]+)\\]`));
    return m ? (m[1] || '').trim() : '';
  };
  const many = (tag: string): string[] =>
    _matches(content, new RegExp(`\\[${tag}:\\s*([^\\]]+)\\]`, 'g'));

  let stock: Monograph['stock'] = null;
  const sm = content.match(/\[STOCK:\s*([^\]]+)\]/);
  if (sm) {
    const [qty, skus, branch, cost, st] = _parts((sm[1] || '').trim(), 5);
    stock = { qty, skus, branch, cost, status: st };
  }

  const equiv: EquivItem[] = [];
  {
    const re = /\[EQUIV:\s*([^\]]+)\]/g;
    let m: RegExpExecArray | null;
    re.lastIndex = 0;
    while ((m = re.exec(content)) !== null) {
      const [name, qty, cost, art] = _parts((m[1] || '').trim(), 4);
      if (name) equiv.push({ name, qty, cost, article: art });
    }
  }

  let evidence: Monograph['evidence'] = null;
  const em = content.match(/\[EVIDENCE:\s*([^\]]+)\]/);
  if (em) {
    const [art, table] = _parts((em[1] || '').trim(), 2);
    evidence = { article: art, table };
  }

  const mono: Monograph = {
    salt, brand, status, klass, article,
    composition: one('COMPOSITION'),
    indication: one('INDICATION'),
    dose: one('DOSE'),
    caution: many('CAUTION'),
    interacts: many('INTERACTS'),
    stock, equiv, evidence,
  };

  // strip every monograph tag so prose/markdown below stays clean
  const stripped = content.replace(
    /\[(DRUG|COMPOSITION|INDICATION|DOSE|CAUTION|INTERACTS|STOCK|EQUIV|EVIDENCE):\s*[^\]]+\]/g,
    '',
  );
  return { mono, stripped };
}
