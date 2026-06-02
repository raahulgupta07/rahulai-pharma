<script lang="ts">
 import type { TraceItem } from './api';

 let { items = [], elapsedMs = 0, live = false, mode = '', analysis = '', usage = null }: {
  items: TraceItem[];
  elapsedMs: number;
  live: boolean;
  mode?: string;
  analysis?: string;
  usage?: { input_tokens: number; output_tokens: number; model?: string } | null;
 } = $props();

 // Guard against non-array props.
 const safeItems = $derived(Array.isArray(items) ? items : []);

 // Default: expanded while live, auto-collapse when done.
 let collapsed = $state(false);
 let userToggled = $state(false);

 $effect(() => {
  if (!userToggled) {
   collapsed = !live;
  }
 });

 function toggle() {
  userToggled = true;
  collapsed = !collapsed;
 }

 const elapsedS = $derived((Math.max(0, elapsedMs) / 1000).toFixed(1));

 // ── Per-1M-token price map (rough). Fallback 0 when model unknown. ──
 const PRICE: Record<string, { in: number; out: number }> = {
  'gemini-3-flash': { in: 0.3, out: 2.5 },
  'gemini-3.1-flash-lite': { in: 0.1, out: 0.4 },
  'gpt-5.4-mini': { in: 0.4, out: 1.6 }
 };

 function priceFor(model: string | undefined): { in: number; out: number } {
  if (!model || typeof model !== 'string') return { in: 0, out: 0 };
  const m = model.toLowerCase();
  for (const key of Object.keys(PRICE)) {
   if (m.includes(key)) return PRICE[key];
  }
  return { in: 0, out: 0 };
 }

 function costOf(tin: number, tout: number, model: string | undefined): number {
  const p = priceFor(model);
  return (tin / 1e6) * p.in + (tout / 1e6) * p.out;
 }

 // Totals: prefer the usage prop; otherwise sum per-item tokens.
 const totals = $derived.by(() => {
  let tin = 0;
  let tout = 0;
  let model: string | undefined;
  if (usage && typeof usage.input_tokens === 'number') {
   tin = usage.input_tokens || 0;
   tout = usage.output_tokens || 0;
   model = usage.model;
  } else {
   for (const it of safeItems) {
    if (!it) continue;
    const a = it as any;
    if (typeof a.tokensIn === 'number') tin += a.tokensIn;
    if (typeof a.tokensOut === 'number') tout += a.tokensOut;
    if (typeof a.model === 'string' && a.model) model = a.model;
   }
  }
  return { tin, tout, model, cost: costOf(tin, tout, model) };
 });

 // ── Retry-fold: hide an 'err' tool immediately followed by a 'done' of the
 // same name; replace with a single "auto-corrected" notice. ──
 const folded = $derived.by(() => {
  const out: { item: TraceItem; idx: number; corrected: boolean }[] = [];
  for (let i = 0; i < safeItems.length; i++) {
   const it = safeItems[i];
   if (!it) continue;
   const next = safeItems[i + 1];
   if (
    it.kind === 'tool' && it.status === 'err' &&
    next && next.kind === 'tool' && next.status === 'done' && next.name === it.name
   ) {
    out.push({ item: next, idx: i + 1, corrected: true });
    i++; // skip the err + consume the done together
    continue;
   }
   out.push({ item: it, idx: i, corrected: false });
  }
  return out;
 });

 const nSteps = $derived(folded.length);

 function glyph(item: TraceItem, idx: number): string {
  if (item.kind === 'tool') {
   if (item.status === 'run') return '◐';
   if (item.status === 'err') return '✗';
   return '✓';
  }
  if (live && idx === safeItems.length - 1) return '◐';
  return '✓';
 }

 function isRunning(item: TraceItem, idx: number): boolean {
  if (item.kind === 'tool') return item.status === 'run';
  return live && idx === safeItems.length - 1;
 }

 // ── Args helpers ──
 function sqlFromArgs(args: any): string {
  if (args == null) return '';
  if (typeof args === 'string') {
   return /\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|EXPLAIN)\b/i.test(args) ? args : '';
  }
  if (typeof args === 'object') {
   for (const k of ['query', 'sql', 'statement', 'sql_query', 'q']) {
    const v = (args as any)[k];
    if (typeof v === 'string' && v.trim()) return v;
   }
  }
  return '';
 }

 // Pretty-print SQL: put major clauses on their own lines.
 function prettySql(sql: string): string {
  if (!sql || typeof sql !== 'string') return '';
  let s = sql.replace(/\s+/g, ' ').trim();
  s = s.replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|RIGHT JOIN|INNER JOIN|JOIN|UNION)\b/gi, '\n$1');
  return s;
 }

 // key/value lines for non-SQL args (no JSON braces/quotes).
 function argLines(args: any): { k: string; v: string }[] {
  if (args == null || typeof args !== 'object' || Array.isArray(args)) return [];
  const out: { k: string; v: string }[] = [];
  for (const k of Object.keys(args)) {
   if (['query', 'sql', 'statement', 'sql_query', 'q'].includes(k)) continue;
   let v = (args as any)[k];
   if (v == null) continue;
   let s: string;
   if (typeof v === 'string') s = v;
   else { try { s = JSON.stringify(v); } catch { s = String(v); } }
   s = s.replace(/^["']|["']$/g, '');
   // No visible truncation on args — show full. Generous cap only to avoid pathological dumps.
   if (s.length > 4000) s = s.slice(0, 4000);
   if (s.trim()) out.push({ k, v: s });
  }
  return out;
 }

 function cleanText(v: unknown, max = 90): string {
  let s = typeof v === 'string' ? v : (() => { try { return JSON.stringify(v); } catch { return String(v); } })();
  if (s == null) return '';
  s = s.replace(/[{}"]/g, '').replace(/\s+/g, ' ').trim();
  if (s.length > max) s = s.slice(0, max) + '…';
  return s;
 }

 function snakeToTitle(name: string): string {
  if (!name) return 'Step';
  return name
   .replace(/[_-]+/g, ' ')
   .replace(/\s+/g, ' ')
   .trim()
   .replace(/\b\w/g, (c) => c.toUpperCase());
 }

 // Count rows of a result string (best-effort).
 function resultSummary(item: any): string {
  let raw = item && item.result != null ? item.result : '';
  if (raw === '') return '';
  let parsed: any = null;
  if (typeof raw === 'string') {
   try { parsed = JSON.parse(raw); } catch { parsed = null; }
  } else if (typeof raw === 'object') {
   parsed = raw;
  }
  if (Array.isArray(parsed)) {
   if (parsed.length === 0) return '0 rows';
   if (parsed.length === 1 && parsed[0] && typeof parsed[0] === 'object') {
    const pairs = Object.keys(parsed[0]).slice(0, 4).map((k) => `${k}: ${cleanText(parsed[0][k], 30)}`);
    return pairs.join(' · ');
   }
   const first = parsed[0] && typeof parsed[0] === 'object'
    ? Object.keys(parsed[0]).slice(0, 3).map((k) => `${k}: ${cleanText(parsed[0][k], 24)}`).join(' · ')
    : cleanText(parsed[0], 60);
   return `${parsed.length} rows · first: ${first}`;
  }
  if (parsed && typeof parsed === 'object') {
   const pairs = Object.keys(parsed).slice(0, 4).map((k) => `${k}: ${cleanText(parsed[k], 30)}`);
   return pairs.join(' · ');
  }
  return cleanText(raw, 120);
 }

 // ── humanizeStep: rules-based natural headline (no LLM). ──
 function humanizeStep(item: TraceItem): string {
  if (item.kind === 'step') {
   return item.title || 'Reasoning';
  }
  const name = (item.name || '').toLowerCase();
  const args: any = item.args;
  const res = typeof item.result === 'string' ? item.result : '';

  // helper: detect "N found" from result
  const countFound = (): string => {
   let parsed: any = null;
   try { parsed = typeof res === 'string' ? JSON.parse(res) : res; } catch { parsed = null; }
   if (Array.isArray(parsed)) return parsed.length > 0 ? `${parsed.length} found` : 'none found';
   const m = res.match(/(\d+)/);
   if (m) return `${m[1]} found`;
   return res && res.length > 2 ? 'some found' : 'none found';
  };
  const countRelevant = (): string => {
   let parsed: any = null;
   try { parsed = typeof res === 'string' ? JSON.parse(res) : res; } catch { parsed = null; }
   if (Array.isArray(parsed)) return `${parsed.length} relevant`;
   const m = res.match(/(\d+)/);
   return m ? `${m[1]} relevant` : 'searched';
  };

  if (name === 'search_learnings') {
   return `Checked memory for past learnings — ${countFound()}`;
  }
  if (name === 'delegate_task_to_member') {
   const member = (args && (args.member_id || args.member || args.member_name || args.agent)) || 'a teammate';
   let task = '';
   if (args && typeof args === 'object') {
    task = args.task || args.task_description || args.message || args.instruction || '';
   }
   const gist = cleanText(task, 4000);
   return gist ? `Planned with ${member}: ${gist}` : `Planned with ${member}`;
  }
  if (name === 'search_all' || name === 'recall' || name === 'search') {
   let extra = '';
   if (/BRAIN|FORMULA/i.test(res)) {
    const fm = res.match(/(?:BRAIN|FORMULA)[:\s]+([A-Za-z0-9_ %().+-]{2,40})/i);
    extra = ` · applied Brain ${fm ? fm[1].trim() : 'formula'}`;
   }
   return `Searched knowledge — ${countRelevant()}${extra}`;
  }
  if (name === 'introspect_schema') {
   const table = (args && (args.table || args.table_name || args.name)) || '';
   let cols = '';
   try {
    const parsed = typeof res === 'string' ? JSON.parse(res) : res;
    if (Array.isArray(parsed)) cols = `${parsed.length} cols`;
    else if (parsed && Array.isArray(parsed.columns)) cols = `${parsed.columns.length} cols`;
   } catch { /* ignore */ }
   const tablePart = table ? `\`${table}\`` : 'a table';
   return cols ? `Inspected schema of ${tablePart} (${cols})` : `Inspected schema of ${tablePart}`;
  }
  if (name === 'run_sql_query' || name === 'run_sql') {
   const sql = sqlFromArgs(args);
   if (sql) {
    if (/\bcount\s*\(/i.test(sql)) return 'Queried — counted';
    if (/\bavg\s*\(/i.test(sql)) return 'Queried — averaged';
    if (/\bsum\s*\(/i.test(sql)) return 'Queried — summed';
    const gb = sql.match(/group\s+by\s+([a-z_][a-z0-9_.]*)/i);
    if (gb) return `Queried — by ${gb[1]}`;
   }
   return 'Queried the database';
  }
  return snakeToTitle(item.name || 'step');
 }

 function metaLine(item: any): string {
  if (!item) return '';
  const parts: string[] = [];
  if (typeof item.model === 'string' && item.model) parts.push(item.model);
  if (typeof item.cost === 'number' && !isNaN(item.cost)) parts.push(`⚡cost ${item.cost}`);
  if (item.duration != null && (typeof item.duration === 'string' || typeof item.duration === 'number')) {
   parts.push(String(item.duration));
  }
  const tin = typeof item.tokensIn === 'number' ? item.tokensIn : null;
  const tout = typeof item.tokensOut === 'number' ? item.tokensOut : null;
  if (tin != null || tout != null) {
   parts.push(`↑${tin ?? 0} ↓${tout ?? 0}`);
  }
  return parts.join(' · ');
 }
</script>

{#if safeItems.length > 0}
 {#if collapsed}
  <div class="rt-collapsed" onclick={toggle} role="button" tabindex="0"
       onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggle(); }}>
   <span class="rt-chev">▸</span>
   <span class="rt-label">thinking</span>
   {#if mode}<span class="rt-dot">·</span><span class="rt-meta">{mode}</span>{/if}
   {#if analysis}<span class="rt-dot">·</span><span class="rt-meta">{analysis}</span>{/if}
   <span class="rt-dot">·</span>
   <span class="rt-clock">⏱ {elapsedS}s</span>
   <span class="rt-dot">·</span>
   <span class="rt-meta">↑{totals.tin} ↓{totals.tout}</span>
   <span class="rt-dot">·</span>
   <span class="rt-meta">${totals.cost.toFixed(4)}</span>
   <span class="rt-expand">[expand]</span>
  </div>
 {:else}
  <div class="rt-block">
   <div class="rt-header" onclick={toggle} role="button" tabindex="0"
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggle(); }}>
    <span class="rt-title">thinking</span>
    {#if mode}<span class="rt-sep">·</span><span class="rt-mode">{mode}</span>{/if}
    {#if analysis}<span class="rt-sep">·</span><span class="rt-mode">{analysis}</span>{/if}
    <span class="rt-sep">·</span>
    <span class="rt-clock">⏱ {elapsedS}s</span>
    <span class="rt-sep">·</span>
    <span class="rt-tok">↑{totals.tin} ↓{totals.tout}</span>
    <span class="rt-sep">·</span>
    <span class="rt-cost">${totals.cost.toFixed(4)}</span>
    <span class="rt-rule"></span>
    <span class="rt-toggle">[▾]</span>
   </div>

   <div class="rt-body">
    {#each folded as entry (entry.item.id + '_' + entry.idx)}
     {#if entry.item.kind === 'step'}
      <div class="rt-row">
       <span class="rt-glyph" class:rt-pulse={isRunning(entry.item, entry.idx)}>{glyph(entry.item, entry.idx)}</span>
       <span class="rt-headline">{humanizeStep(entry.item)}</span>
       {#if entry.item.agent}<span class="rt-agent">{entry.item.agent}</span>{/if}
      </div>
      {#if entry.item.text}
       <div class="rt-sub rt-narration"><span class="rt-tick">╎</span> {entry.item.text}</div>
      {/if}
      {#if metaLine(entry.item)}<div class="rt-sub rt-meta-line">{metaLine(entry.item)}</div>{/if}
     {:else}
      <div class="rt-row">
       <span class="rt-glyph" class:rt-pulse={isRunning(entry.item, entry.idx)}>{glyph(entry.item, entry.idx)}</span>
       <span class="rt-headline">{humanizeStep(entry.item)}</span>
       {#if entry.item.agent}<span class="rt-agent">{entry.item.agent}</span>{/if}
      </div>

      {#if entry.corrected}
       <div class="rt-sub rt-corrected">↻ auto-corrected one query — retried clean.</div>
      {/if}

      {#if sqlFromArgs(entry.item.args)}
       <div class="rt-sub rt-sql">{prettySql(sqlFromArgs(entry.item.args))}</div>
      {:else}
       {#each argLines(entry.item.args) as a}
        <div class="rt-sub rt-arg"><span class="rt-arg-k">{a.k}</span><span class="rt-arg-v">{a.v}</span></div>
       {/each}
      {/if}

      {#if entry.item.status === 'run'}
       <div class="rt-sub rt-result"><span class="rt-running">running…</span></div>
      {:else if resultSummary(entry.item)}
       <div class="rt-sub rt-result">→ {resultSummary(entry.item)}</div>
      {/if}

      {#if metaLine(entry.item)}<div class="rt-sub rt-meta-line">{metaLine(entry.item)}</div>{/if}
     {/if}
    {/each}
   </div>

   <div class="rt-foot">total · {nSteps} steps · ↑{totals.tin} ↓{totals.tout} · ${totals.cost.toFixed(4)}</div>
  </div>
 {/if}
{/if}

<style>
 .rt-block {
  font-family: var(--pw-mono, monospace);
  font-size: 12px;
  line-height: 1.55;
  border: 1px solid var(--pw-border, #e8e6dd);
  border-radius: var(--pw-radius-sm, 8px);
  background: var(--pw-bg-alt, #f7f6f3);
  color: var(--pw-ink-soft, #4a4a48);
  padding: 6px 10px 4px;
  margin: 8px 0;
  overflow-x: auto;
 }
 .rt-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: var(--pw-muted, #6f6e69);
  user-select: none;
 }
 .rt-title { font-weight: 600; letter-spacing: 0.04em; color: var(--pw-ink-soft, #4a4a48); }
 .rt-mode { color: var(--pw-muted, #6f6e69); text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.06em; }
 .rt-sep { color: var(--pw-dim, #97968f); }
 .rt-rule { flex: 1; }
 .rt-clock { color: var(--pw-accent-ink, #b04f30); white-space: nowrap; }
 .rt-tok { color: var(--pw-muted, #6f6e69); white-space: nowrap; }
 .rt-cost { color: var(--pw-muted, #6f6e69); white-space: nowrap; }
 .rt-toggle { color: var(--pw-dim, #97968f); }

 .rt-body { margin: 4px 0 2px; }
 .rt-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  white-space: nowrap;
 }
 .rt-glyph { width: 1em; text-align: center; color: var(--pw-success, #2d6a4f); flex: 0 0 auto; }
 .rt-headline { color: var(--pw-ink-soft, #4a4a48); font-weight: 500; white-space: normal; }
 .rt-agent { color: var(--pw-dim, #97968f); font-size: 11px; font-style: italic; }

 .rt-sub {
  padding-left: 24px;
  color: var(--pw-muted, #6f6e69);
  white-space: pre-wrap;
  word-break: break-word;
 }
 .rt-narration { font-style: italic; color: var(--pw-ink-soft, #4a4a48); }
 .rt-tick { color: var(--pw-dim, #97968f); }
 .rt-sql {
  color: var(--pw-ink-soft, #4a4a48);
  font-family: var(--pw-mono, monospace);
  white-space: pre-wrap;
  line-height: 1.5;
  opacity: 0.92;
 }
 .rt-arg { display: flex; gap: 10px; }
 .rt-arg-k { color: var(--pw-dim, #97968f); min-width: 80px; }
 .rt-arg-v { color: var(--pw-muted, #6f6e69); }
 .rt-result { color: var(--pw-muted, #6f6e69); }
 .rt-running { color: var(--pw-accent-ink, #b04f30); }
 .rt-corrected { color: var(--pw-dim, #97968f); font-style: italic; }
 .rt-meta-line { color: var(--pw-dim, #97968f); font-size: 10.5px; }
 .rt-foot {
  color: var(--pw-dim, #97968f);
  font-size: 10.5px;
  border-top: 1px dashed var(--pw-border-strong, #d8d5cb);
  padding-top: 4px;
  margin-top: 2px;
 }

 .rt-collapsed {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--pw-mono, monospace);
  font-size: 12px;
  color: var(--pw-muted, #6f6e69);
  background: var(--pw-bg-alt, #f7f6f3);
  border: 1px solid var(--pw-border, #e8e6dd);
  border-radius: var(--pw-radius-sm, 8px);
  padding: 5px 10px;
  margin: 8px 0;
  cursor: pointer;
  user-select: none;
 }
 .rt-chev { color: var(--pw-dim, #97968f); }
 .rt-label { font-weight: 600; letter-spacing: 0.04em; color: var(--pw-ink-soft, #4a4a48); }
 .rt-dot { color: var(--pw-dim, #97968f); }
 .rt-meta { color: var(--pw-muted, #6f6e69); }
 .rt-expand { margin-left: auto; color: var(--pw-accent-ink, #b04f30); }

 .rt-pulse { animation: rt-pulse 1s ease-in-out infinite; }
 @keyframes rt-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
 }
</style>
