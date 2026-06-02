<script module lang="ts">
  import Icon from '$lib/Icon.svelte';
 /**
 * Module-level exports for chat-renderer helpers shared across surfaces.
 * Both routes/chat/+page.svelte and routes/project/[slug]/+page.svelte
 * import these directly — do NOT redefine in pages (see CHAT_RENDERER.md
 * and the chat-mirror linter at frontend/scripts/check-chat-mirror.mjs).
 */
 export function formatCell(cell: string): string {
 if (!cell || typeof cell !== 'string') return cell || '';
 let s = cell.trim();
 const dirMatch = s.match(/^\[?(UP|DOWN|FLAT):?\s*([+-]?[\d.]+)\]?$/i);
 if (dirMatch) {
 const dir = dirMatch[1].toUpperCase();
 const val = dirMatch[2];
 if (dir === 'UP') return `<span style="color:#16a34a;font-weight:700;">▲ +${val.replace(/^\+/, '')}</span>`;
 if (dir === 'DOWN') return `<span style="color:#dc2626;font-weight:700;">▼ ${val.startsWith('-') ? val : '-' + val}</span>`;
 return `<span style="color:#a3a3a3;font-weight:700;">━ ${val}</span>`;
 }
 if (/^[▲↑]/.test(s)) return `<span style="color:#16a34a;font-weight:700;">${s}</span>`;
 if (/^[▼↓]/.test(s)) return `<span style="color:#dc2626;font-weight:700;">${s}</span>`;
 if (/^[━→]/.test(s)) return `<span style="color:#a3a3a3;font-weight:700;">${s}</span>`;
 s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
 const pctMatch = s.match(/^([+-]?\d+\.?\d*)%$/);
 if (pctMatch) {
 const v = parseFloat(pctMatch[1]);
 if (v > 50) return `<span style="color:#16a34a;font-weight:600;">${s}</span>`;
 if (v < 20) return `<span style="color:#dc2626;font-weight:600;">${s}</span>`;
 }
 return s;
 }

 export function generateChartCaption(tbl: { headers: string[]; rows: string[][] }): string {
 if (!tbl.headers.length || !tbl.rows.length) return '';
 const label = tbl.headers[0] || 'Category';
 let numIdx = -1;
 let numHeader = '';
 for (let c = 1; c < tbl.headers.length; c++) {
 const vals = tbl.rows.map((r) => parseFloat((r[c] || '').replace(/[$,%,]/g, '')));
 if (vals.filter((v) => !isNaN(v)).length >= tbl.rows.length * 0.5) {
 numIdx = c; numHeader = tbl.headers[c]; break;
 }
 }
 if (numIdx < 0) return `${tbl.rows.length} items by ${label}.`;
 const values = tbl.rows.map((r) => ({
 label: (r[0] || '').replace(/\*\*/g, ''),
 val: parseFloat((r[numIdx] || '').replace(/[$,%,]/g, '')),
 })).filter((v) => !isNaN(v.val));
 if (values.length === 0) return '';
 const sorted = [...values].sort((a, b) => b.val - a.val);
 const top = sorted[0]; const bottom = sorted[sorted.length - 1];
 const total = values.reduce((s, v) => s + v.val, 0);
 const avg = total / values.length;
 const fmt = (n: number) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : n % 1 === 0 ? n.toLocaleString() : n.toFixed(1);
 const allSame = sorted.length > 1 && top.val === bottom.val;
 if (allSame) return `Flat at ${fmt(top.val)} ${numHeader} across all ${values.length} periods.`;
 let caption = `Highest ${numHeader}: ${top.label} (${fmt(top.val)})`;
 if (sorted.length > 1 && top.label !== bottom.label) caption += `. Lowest: ${bottom.label} (${fmt(bottom.val)})`;
 if (sorted.length >= 3) caption += `. Average: ${fmt(avg)}`;
 if (values.length >= 4) {
 let ups = 0, downs = 0;
 for (let i = 1; i < values.length; i++) { if (values[i].val > values[i - 1].val) ups++; else if (values[i].val < values[i - 1].val) downs++; }
 if (ups > values.length * 0.7) caption += '. Trend: increasing ▲';
 else if (downs > values.length * 0.7) caption += '. Trend: decreasing ▼';
 }
 return caption + '.';
 }

 /**
  * Collapse consecutive duplicate sentences/paragraphs. Fixes the persona
  * greeting that sometimes renders twice back-to-back. If the same ~60+ char
  * trimmed segment repeats consecutively, keep only the first occurrence.
  */
 export function collapseDuplicateSentences(s: string): string {
 if (!s || typeof s !== 'string') return s || '';
 // Work line-by-line so markdown STRUCTURE survives (tables, ### headers,
 // lists, code fences). Joining everything with a space — the old bug —
 // flattened table rows + headers onto one line and broke GFM parsing.
 const lines = s.split('\n');
 const outLines: string[] = [];
 let prevKey = '';
 for (const line of lines) {
 // Within one line, dedup consecutive duplicate sentences. Skip splitting
 // table/separator/code lines so their pipes never get reflowed.
 const isStructural = /^\s*(\||#{1,6}\s|```|[-*+]\s|\d+\.\s|>)/.test(line);
 if (isStructural) { outLines.push(line); prevKey = ''; continue; }
 const segs = line.split(/(?<=[.!?])\s+/);
 const kept: string[] = [];
 for (const seg of segs) {
 const norm = seg.trim();
 const key = norm.toLowerCase().replace(/\s+/g, ' ');
 if (key.length >= 60 && key === prevKey) continue;
 kept.push(seg);
 if (norm) prevKey = key;
 }
 outLines.push(kept.join(' '));
 }
 return outLines.join('\n');
 }

 /**
  * Strip ALL structured render tags from prose so they never leak into the
  * displayed text. Shared by BOTH the done-branch prose render and the
  * streaming-branch render. The structured parsers that READ these tags
  * (parseChartHint, KPI cards, etc.) run separately on the RAW content — this
  * only cleans the human-facing prose. KPI tags become inline bold (mirrors
  * the done-branch KPI markdown replace) rather than being dropped.
  */
 export function stripStructureTags(s: string): string {
 if (!s || typeof s !== 'string') return s || '';
 const out = s
 .replace(/\[ANALYSIS:[^\]]+\]/g, '')
 .replace(/\[MODE:\w+\]/g, '')
 .replace(/\[CHARTS?:[^\]]+\]/g, '')
 .replace(/\[CHART_CONFIG:[^\]]+\]/g, '')
 .replace(/\[HEADLINE:[^\]]+\]/g, '')
 .replace(/\[SO[_ ]WHAT[^\]]*\]/gi, '')
 .replace(/\[KPI:([^\]]+)\]/g, (_m: string, inner: string) => {
 const parts = inner.split('|');
 const val = (parts[0] || '').trim();
 const label = (parts[1] || '').trim();
 let change = (parts[2] || '').trim();
 if (/^[━—\-–\s.]*$/.test(change)) change = '';
 const tail = [label, change].filter(Boolean).join(', ');
 return tail ? `**${val}** (${tail})` : `**${val}**`;
 })
 .replace(/\[CONFIDENCE_BREAKDOWN:[^\]]*\]/gi, '')
 .replace(/\[CONFIDENCE:[^\]]+\]/g, '')
 // [VERIFIED:…] → unicode placeholder ‹‹VERIFIED:…›› that survives both:
 //   (a) the [A-Z_]+:… catch-all strip later in this chain
 //   (b) markdown.ts:inlineFormat escapeHtml (only escapes < > & ")
 // Post-processed back to <span> AFTER markdownToHtml at render site.
 .replace(/\[VERIFIED:([^\]]+)\]/g, (_m: string, inner: string) => ` ‹‹VERIFIED:${inner.trim()}››`)
 .replace(/\[IMPACT:[^\]]+\]/g, '')
 .replace(/\[RELATED:[^\]]+\]/g, '')
 .replace(/\[VERDICT:[^\]]+\]/g, '')
 .replace(/\[METRIC:[^\]]+\]/g, '')
 .replace(/\[FINDING:[^\]]*\]/gi, '')
 .replace(/\[ANCHOR:[^\]]*\]/gi, '')
 .replace(/\[SEGMENT:[^\]]*\]/gi, '')
 .replace(/\[KILL:[^\]]*\]/gi, '')
 .replace(/\[ASSUME(?:S|PTION)?:[^\]]*\]/gi, '')
 .replace(/\[BECAUSE:[^\]]*\]/gi, '')
 .replace(/\[[A-Z][A-Z0-9_]{2,}:[^\]]*\]/g, '')
 .replace(/(?:\n\s*---\s*)?\n?\s*SOURCES:[\s\S]*$/i, '');
 return collapseDuplicateSentences(out).trim();
 }
</script>

<script lang="ts">
 import type { Snippet } from 'svelte';
 import { markdownToHtml, formatInline, parseMarkdownTables, tableToCsv, hasNumericData, detectChartType, getAvailableTypes, parseChartHint, parseFreshness, parseLineage } from '$lib';
import AnswerCard from './AnswerCard.svelte';
 import type { ToolCall } from '$lib/api';
 import EChartView from '$lib/echart.svelte';
 import {
 parseHeadline,
 parseBecause,
 parseActions,
 parseCaveat,
 parseConfidenceBreakdown,
 parseAnomalies,
 parseSoWhat,
 parseFindings,
 parseKillCriteria,
 parseSegments,
 parseAnchors,
 parseAssumptions,
 actionIcon,
 } from '$lib/chat/tag-parsers';
 // formatCell + generateChartCaption are defined in the <script module> block above
 // and accessible directly in the instance script without import.

 // Minimal shape needed for rendering. Host page owns the full ChatMessage type.
 export interface ChatMessage {
 role: 'user' | 'assistant';
 content: string;
 timestamp: string;
 status?: 'streaming' | 'done' | 'error';
 suggestions?: string[];
 toolCalls?: ToolCall[];
 workflowExpanded?: boolean;
 sqlQueries?: string[];
 showSql?: boolean;
 showChart?: boolean;
 chartType?: any;
 routing?: { routed_to: string; slug: string; reason: string };
 activeTab?: 'insight' | 'analysis' | 'data' | 'query' | 'chart' | 'sources';
 qualityScore?: number;
 showTrace?: boolean;
 dataTableIndex?: number;
 reasoningUsed?: string;
 analysisUsed?: string;
 id?: string;
 sources_used?: any[];
 federated_meta?: any;
 [k: string]: any;
 }

 interface Props {
 messages: ChatMessage[];
 isStreaming?: boolean;
 /** Update a single message in-place (component dispatches all mutations through this). */
 updateMessage: (index: number, patch: Partial<ChatMessage>) => void;
 /** Header label shown next to "dash route". Host typically passes mode/agent. */
 routeLabel?: string;
 /** Show/hide a feature (e.g. featureConfig gating). Defaults to all enabled. */
 tabEnabled?: (name: 'insight' | 'analysis' | 'data' | 'query' | 'chart' | 'sources') => boolean;
 /** Show inline charts inside analysis tab. */
 chartsEnabled?: boolean;
 /** Agent label shown in SOURCES tab "AGENT" stat. */
 agentName?: string;
 /** Whether copy-message-N was just pressed (host owns the timer). */
 copiedIndex?: number;
 /** Whether message-N is pinned. */
 pinnedMap?: Record<number, boolean>;
 /** Action callbacks (host wires them — keeps API/auth/state out of this component). */
 onCopy?: (index: number) => void;
 onSend?: (text: string) => void;
 onFeedback?: (index: number, rating: 'up' | 'down') => void;
 onSaveMemory?: (index: number) => void;
 onExportCsv?: (index: number, tables: any[]) => void;
 onExportPdf?: (index: number) => void;
 onPin?: (index: number, tables: any[]) => void;
 onSchedule?: (index: number) => void;
 onCopySql?: (sql: string) => void;
 onRetry?: (index: number) => void;
 onTrackPreference?: (kind: string, value: string) => void;
 /** Storytelling [ACTION:...] click handler. Host wires routing/sendMessage. */
 onAction?: (act: { label: string; type: string; param: string }) => void;
 /** Snippet for host-specific overlays inside the analysis bubble (e.g. campaign proposals). */
 analysisExtras?: Snippet<[{ msg: ChatMessage; index: number }]>;
 /** Snippet for an action toolbar inside analysis (host owns PIN/SAVE/CSV/PDF + fb-row layout). */
 analysisActions?: Snippet<[{ msg: ChatMessage; index: number; tables: any[]; hasTables: boolean }]>;
 /** Snippet rendered after the trace toggle for host-specific per-message footer
 (learning approval cards, suggestion pills, drift bell, etc). */
 messageFooter?: Snippet<[{ msg: ChatMessage; index: number }]>;
 }

 let {
 messages,
 isStreaming = false,
 updateMessage,
 routeLabel = 'auto',
 tabEnabled = () => true,
 chartsEnabled = true,
 agentName = 'Agent',
 copiedIndex = -1,
 pinnedMap = {},
 onCopy,
 onSend,
 onFeedback,
 onSaveMemory,
 onExportCsv,
 onExportPdf,
 onPin,
 onSchedule,
 onCopySql,
 onRetry,
 onTrackPreference,
 onAction,
 analysisExtras,
 analysisActions,
 messageFooter,
 }: Props = $props();

 // --- pure helpers (duplicated from host pages so component stands alone) ---

 function parseClarify(content: string): string[] | null {
 const match = content.match(/\[CLARIFY:\s*(.+?)\]/);
 if (match) return match[1].split('|').map(s => s.trim());
 return null;
 }

 // Fallback related questions when the LLM emits no [RELATED:] tags ($0, rules-based).
 // On data-limitation answers, offer constructive PIVOTS (not nothing) — turn a
 // dead-end into actionable next steps.
 function fallbackRelated(msg: ChatMessage, hasData: boolean): string[] {
  const c = (msg?.content || '').toLowerCase();
  if (!c) return [];
  const isLimited = /\b(cannot|unable|can'?t|does not contain|no shared|not possible|impossible|unavailable|data limitation|lacks|missing)\b/.test(c);
  if (isLimited) {
   // Pivot to what the answer references as possible alternatives.
   const out: string[] = [];
   if (/categor/.test(c)) out.push('Break down stock by therapeutic category');
   if (/\bsite|location\b/.test(c)) out.push('Audit stock coverage by site');
   if (/search/.test(c)) out.push('Show search demand trends by hour');
   if (out.length === 0) out.push('What analysis can this data support?');
   return out.slice(0, 3);
  }
  if (hasData) {
   return ['Break this down by site', 'Show the trend over time', 'Compare the top and bottom performers'];
  }
  return ['Show me the data behind this', 'Break this down by category', 'What changed over time?'];
 }

 function toggleWorkflow(i: number) {
 updateMessage(i, { workflowExpanded: !messages[i]?.workflowExpanded });
 }

 // Svelte action: mounts a 200x80 ECharts sparkline into a node from spec
 // spec shape: { chart_type: 'line'|'bar'|'area', sparkline_data: number[] | {value:number,label?:string}[] }
 function miniChart(node: HTMLElement, spec: any) {
   let chart: any = null;
   const mount = async (s: any) => {
     try {
       const echarts: any = await import('echarts');
       if (chart) { chart.dispose(); chart = null; }
       const raw = s?.sparkline_data || [];
       const data = (Array.isArray(raw) ? raw : []).map((v: any) => typeof v === 'number' ? v : (v?.value ?? 0));
       if (!data.length) return;
       const ct = (s?.chart_type || 'line').toLowerCase();
       chart = echarts.init(node);
       const series: any = ct === 'bar'
         ? [{ type: 'bar', data, itemStyle: { color: '#c96342' } }]
         : [{ type: 'line', data, smooth: true, symbol: 'none', lineStyle: { color: '#c96342', width: 1.5 }, areaStyle: ct === 'area' ? { color: 'rgba(201,99,66,0.18)' } : undefined }];
       chart.setOption({
         grid: { left: 2, right: 2, top: 4, bottom: 4 },
         xAxis: { type: 'category', show: false, data: data.map((_: any, i: number) => i) },
         yAxis: { type: 'value', show: false },
         series,
         tooltip: { show: false },
         animation: false,
       });
     } catch (_e) { /* fail-soft */ }
   };
   mount(spec);
   return {
     update(newSpec: any) { mount(newSpec); },
     destroy() { try { chart?.dispose(); } catch (_e) {} },
   };
 }

 function scrollToPanel(panelId: string) {
   try {
     const el = document.querySelector(`[data-panel-id="${CSS.escape(panelId)}"]`);
     if (el && typeof (el as any).scrollIntoView === 'function') {
       (el as any).scrollIntoView({ behavior: 'smooth', block: 'center' });
     }
   } catch (_e) { /* no-op */ }
 }

 function mlToolMeta(name: string) {
 switch (name) {
 case 'predict': return { label: 'FORECAST', algo: 'statsforecast/AutoARIMA', color: 'var(--pw-success)', tier: 'ML', cost: '$0' };
 case 'llm_predict': return { label: 'FORECAST', algo: 'LLM Trend Analysis', color: '#6b21a8', tier: 'LLM', cost: '$0.02' };
 case 'detect_anomalies_ml': return { label: 'ANOMALY', algo: 'IsolationForest', color: '#dc2626', tier: 'ML', cost: '$0' };
 case 'feature_importance': return { label: 'DRIVERS', algo: 'LightGBM', color: '#d97706', tier: 'ML', cost: '$0' };
 case 'classify': return { label: 'CLASSIFY', algo: 'GradientBoosting', color: '#0369a1', tier: 'ML', cost: '$0' };
 case 'cluster': return { label: 'CLUSTER', algo: 'K-Means', color: '#0d9488', tier: 'ML', cost: '$0' };
 case 'decompose': return { label: 'DECOMPOSE', algo: 'Seasonal Decompose', color: '#7c3aed', tier: 'ML', cost: '$0' };
 default: return { label: 'ML', algo: name, color: '#6b7280', tier: 'ML', cost: '$0' };
 }
 }
</script>

{#each messages as msg, i (i)}
  {#if msg.role === 'assistant'}
    <div class="msg-row" style="display: flex; gap: 12px; align-items: flex-start; margin-bottom: 16px;">
      <div style="width: 28px; height: 28px; border-radius: 0; background: var(--pw-bg-alt); display: flex; align-items: center; justify-content: center; flex-shrink: 0; color: var(--pw-accent);">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="0"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/><path d="M7 15h0M17 15h0M9 18h6"/></svg>
      </div>
      <div style="flex: 1; min-width: 0;">

        <!-- Complexity Router badge (Feature A) — tier · model · score -->
        {#if (msg as any).routing?.tier}
          {@const rd = (msg as any).routing}
          {@const tColor = rd.tier === 'TRIVIAL' ? 'var(--pw-muted)' : rd.tier === 'LOOKUP' ? 'var(--pw-success)' : rd.tier === 'AGENTIC' ? '#6b21a8' : rd.tier === 'REASONING' ? '#4c1d95' : rd.tier === 'ULTRA' ? '#1e1b4b' : 'var(--pw-accent)'}
          <div title={`${rd.reason || ''}${rd.cached ? ' · cached' : ''}${Array.isArray(rd.signals) ? '\n' + rd.signals.join(', ') : ''}`}
               style="display:inline-flex; align-items:center; gap:6px; margin-bottom:6px; font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.06em; padding:2px 7px; border-radius: 0; color:white; background:{tColor};">
            <span>{rd.override ? 'MANUAL' : rd.tier}</span>
            <span style="opacity:0.85; font-weight:700; text-transform:none;">{(rd.model || '').split('/').pop()}</span>
            {#if rd.override && rd.suggested_tier}<span style="opacity:0.6; font-weight:700; text-transform:none;">· auto: {rd.suggested_tier.toLowerCase()}</span>{:else if typeof rd.score === 'number'}<span style="opacity:0.7; font-weight:700;">{rd.score.toFixed(2)}</span>{/if}
            {#if rd.effort}<span style="opacity:0.6; font-weight:700; text-transform:none;">· {rd.effort}</span>{/if}
          </div>
        {/if}

        <!-- Legacy dark CLI route/exec terminal + standalone live-SQL tool-spinner
             REMOVED: ReasoningTrace (mounted via analysisExtras snippet) is now the
             single reasoning trace. It already renders routing, per-tool status,
             running/done state and the live SQL preview. -->

        <!-- Response Tabs -->
        {#if (msg.status === 'done' || msg.status === 'complete' || msg.status === 'error' || msg.status === 'streaming') && msg.content}
          {@const tables = parseMarkdownTables(msg.content)}
          {@const hasTables = tables.length > 0 && tables[0].headers?.length > 0}
          {@const hasChartData = hasTables && hasNumericData(tables[0])}
          {@const hasQueries = !!(msg.sqlQueries && msg.sqlQueries.length > 0)}
          {@const totalTableRows = tables.reduce((sum: number, t: any) => sum + (t.rows?.length || 0), 0)}
          {@const currentTab = msg.activeTab || 'analysis'}
          {@const chartHint = parseChartHint(msg.content)}

          <!-- ML Cards -->
          {@const mlTools = (msg.toolCalls || []).filter((t: any) => ['predict', 'feature_importance', 'detect_anomalies_ml', 'llm_predict', 'classify', 'cluster', 'decompose'].includes(t.name) && t.status === 'done')}
          {#if mlTools.length > 0}
            <div style="margin: 8px 0; border: 2px solid var(--pw-ink); background: var(--pw-surface);">
              <div style="padding: 6px 12px; background: var(--pw-ink); color: var(--pw-bg); font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em;">MACHINE LEARNING &middot; {mlTools.length} {mlTools.length === 1 ? 'MODEL' : 'MODELS'} USED</div>
              <div style="padding: 10px; display: flex; gap: 8px; flex-wrap: wrap;">
                {#each mlTools as mlTool}
                  {@const isLLM = mlTool.name === 'llm_predict'}
                  {@const meta = mlToolMeta(mlTool.name)}
                  <div style="flex: 1; min-width: 180px; max-width: 280px; border: 2px solid var(--pw-ink); background: var(--pw-surface); padding: 10px;">
                    <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 8px;">
                      <span style="font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; color: {meta.color};">{meta.label}</span>
                      <span style="font-size: 11px; font-weight: 900; padding: 1px 5px; border-radius: 0; color: white; background: {isLLM ? '#6b21a8' : 'var(--pw-success)'};">{meta.tier}</span>
                    </div>
                    <div style="font-size: 11px; font-weight: 700; margin-bottom: 4px;">{meta.algo}</div>
                    {#if mlTool.duration}<div style="font-size: 10px; color: var(--pw-muted);">Completed in {mlTool.duration}</div>{/if}
                    <div style="font-size: 11px; color: var(--pw-muted); margin-top: 6px; text-transform: uppercase;">
                      {isLLM ? 'LLM (' + meta.cost + ')' : '' + (mlTool.name === 'feature_importance' || mlTool.name === 'classify' || mlTool.name === 'cluster' || mlTool.name === 'decompose' ? 'On-demand' : 'Pre-trained') + ' (' + meta.cost + ')'}
                    </div>
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Thinking trace renders ABOVE the tab bar so the user reads the
               reasoning first, then picks a tab. Also keeps it visible on every
               tab (DATA/SQL/CHART), not just INSIGHT. -->
          {#if analysisExtras}{@render analysisExtras({ msg, index: i })}{/if}

          <!-- Design A: horizontal top tabs — only ONE tab content renders below -->
          {@const _rawTab = msg.activeTab}
          {@const currentTabA = (!_rawTab || _rawTab === 'analysis') ? 'insight' : _rawTab}
          {@const insightCountA = (msg.content.match(/\[INSIGHT:/g) || []).length}
          {@const sqlCountA = (msg.sqlQueries?.length) || 0}
          {@const rowCountA = tables.reduce((s: number, t: any) => s + (t.rows?.length || 0), 0)}
          {@const chartCountA = tables.filter((t: any) => hasNumericData(t)).length}
          {@const sourceCountA = (() => {
            const names = new Set<string>();
            for (const sql of (msg.sqlQueries || [])) {
              const fromMatches = sql.match(/(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)/gi) || [];
              for (const m of fromMatches) {
                const t = m.replace(/^(?:FROM|JOIN)\s+/i, '').replace(/^[a-z_]+\./i, '').trim();
                if (t) names.add(t);
              }
            }
            return names.size;
          })()}
          <div class="response-tabs-top">
            <button class:active={currentTabA === 'insight'} onclick={() => { onTrackPreference?.('tab_click', 'insight'); updateMessage(i, { activeTab: 'insight' }); }}>Insight{insightCountA > 0 ? ` (${insightCountA})` : ''}</button>
            {#if tabEnabled('data')}
              <button class:active={currentTabA === 'data'} onclick={() => { onTrackPreference?.('tab_click', 'data'); updateMessage(i, { activeTab: 'data' }); }}>Data{rowCountA > 0 ? ` (${rowCountA})` : ''}</button>
            {/if}
            {#if tabEnabled('query')}
              <button class:active={currentTabA === 'query'} onclick={() => { onTrackPreference?.('tab_click', 'query'); updateMessage(i, { activeTab: 'query' }); }}>SQL{sqlCountA > 0 ? ` (${sqlCountA})` : ''}</button>
            {/if}
            {#if tabEnabled('chart')}
              <button class:active={currentTabA === 'chart'} onclick={() => { onTrackPreference?.('tab_click', 'chart'); updateMessage(i, { activeTab: 'chart', chartType: msg.chartType || chartHint?.type || (hasChartData ? detectChartType(tables[0]) : undefined) }); }}>Chart{chartCountA > 0 ? ` (${chartCountA})` : ''}</button>
            {/if}
            {#if tabEnabled('sources')}
              <button class:active={currentTabA === 'sources'} onclick={() => { onTrackPreference?.('tab_click', 'sources'); updateMessage(i, { activeTab: 'sources' }); }}>Sources{sourceCountA > 0 ? ` (${sourceCountA})` : ''}</button>
            {/if}
          </div>

          <!-- INSIGHT tab content (default) -->
          {#if currentTabA === 'insight'}
            <!-- Exec View — gated ONLY by featureConfig.tabs.exec_view. Always
                 renders when feature on, even for legacy assistant messages
                 (AnswerCard maps legacy tags → exec blocks internally). -->
            {@const _execEnabled = tabEnabled('exec_view')}
            {@const _execTier = ((msg as any)?.routing?.tier || (msg as any)?.routing?.reasoning_effort || (msg.reasoningUsed === 'fast' ? 'instant' : msg.reasoningUsed === 'deep' ? 'deep' : msg.reasoningUsed === 'ultra' ? 'ultra' : 'standard'))}
            {#if _execEnabled}
              <AnswerCard
                content={msg.content}
                tier={_execTier}
                msg={msg}
                onAction={(act, payload) => {
                  // "Do it" on a recommendation → fire follow-up chat
                  if (act === 'followup' && payload?.question) {
                    onSend?.(`How do I ${payload.question}? Give me a step-by-step plan with the SQL/data needed.`);
                    return;
                  }
                  // Diary save → forward to parent
                  if (act === 'diary') {
                    onAction?.(`save_decision:${JSON.stringify(payload || {})}`);
                    return;
                  }
                  // Action bar buttons
                  if (act === 'copy') { onCopy?.(i); return; }
                  if (act === 'pin')  { onAction?.('pin'); return; }
                  if (act === 'star' || act === 'save') { onAction?.('save'); return; }
                  if (act === 'excel') { onAction?.('csv'); return; }
                  if (act === 'email' || act === 'share') { onAction?.('share'); return; }
                  // Default — bubble up
                  onAction?.(act);
                }}
              />
            {:else}
            {@const insightAnalysisMatch = msg.content.match(/\[ANALYSIS:([^\]]+)\]/)}
            {@const insightAnalysisTypes = insightAnalysisMatch ? insightAnalysisMatch[1].split(',').map((t: string) => t.trim()) : []}
            {@const modeMatch = msg.content.match(/\[MODE:(\w+)\]/)}
            {@const actualMode = modeMatch ? modeMatch[1] : ((msg.reasoningUsed && msg.reasoningUsed !== 'auto') ? msg.reasoningUsed : (msg.content.length > 800 ? 'deep' : 'fast'))}
            {@const _storyH = parseHeadline(msg.content)}
            {@const _storyB = parseBecause(_storyH.stripped)}
            {@const _storyA = parseActions(_storyB.stripped)}
            {@const _storyC = parseCaveat(_storyA.stripped)}
            {@const _storyCB = parseConfidenceBreakdown(_storyC.stripped)}
            {@const _storySW = parseSoWhat(_storyCB.stripped)}
            {@const _storyFD = parseFindings(_storySW.stripped)}
            {@const _storyKL = parseKillCriteria(_storyFD.stripped)}
            {@const _storySG = parseSegments(_storyKL.stripped)}
            {@const _storyAN = parseAnchors(_storySG.stripped)}
            {@const _storyAS = parseAssumptions(_storyAN.stripped)}
            {@const analysisContent = _storyAS.stripped
              .replace(/\[ANALYSIS:[^\]]+\]/g, '')
              .replace(/\[MODE:\w+\]/g, '')
              .replace(/\[CHART:[^\]]+\]/g, '')
              .replace(/\[CHART_CONFIG:[^\]]+\]/g, '')
              .replace(/\[DASHBOARD:\d+\]/g, '')
              .replace(/\[PRESENTATION:\d+\]/g, '')
              .replace(/\[CONFIRM_OUTLINE\]/g, '')
              .replace(/\[CLARIFY:[^\]]+\]/g, '')
              .replace(/\[ANOMALY:[^\]]+\]/g, '')
              .replace(/\[KPI:([^\]]+)\]/g, (_m: string, inner: string) => {
                const parts = inner.split('|');
                const val = (parts[0] || '').trim();
                const label = (parts[1] || '').trim();
                let change = (parts[2] || '').trim();
                if (/^[━—\-–\s.]*$/.test(change)) change = '';
                const tail = [label, change].filter(Boolean).join(', ');
                return tail ? `**${val}** (${tail})` : `**${val}**`;
              })
              .replace(/\[CONFIDENCE:[^\]]+\]/g, '')
              // [VERIFIED:…] → unicode placeholder; post-process to <span>
              // after markdownToHtml. Avoids escapeHtml + catch-all strip.
              .replace(/\[VERIFIED:([^\]]+)\]/g, (_m: string, inner: string) => ` ‹‹VERIFIED:${inner.trim()}››`)
              .replace(/\[IMPACT:[^\]]+\]/g, '')
              .replace(/\[RELATED:[^\]]+\]/g, '')
              .replace(/\[CAMPAIGN_PROPOSAL:[^\]]+\]/g, '')
              .replace(/\[SIM_LAUNCHED:[a-z0-9_]+\]/gi, '')
              .replace(/\[VERDICT:[^\]]+\]/g, '')
              .replace(/\[ML:[^\]]+\]/g, '')
              .replace(/\[METRIC:[^\]]+\]/g, '')
              .replace(/---\s*\n\s*SOURCES:[\s\S]*$/, '')
              .replace(/```sql[\s\S]*?```/g, '')
              .replace(/(?:^|\n)\s*#{0,6}\s*(?:Related questions?|Follow[- ]?up questions?|Suggested questions?|Next questions?)\s*:?\s*\n[\s\S]*?(?=\n\s*(?:#{1,6}\s|\*\*[A-Z]|$))/gi, '\n')
              .replace(/(?:^|\n)\s*\*\*\s*(?:Related questions?|Follow[- ]?up questions?|Suggested questions?|Next questions?)\s*\*\*\s*:?\s*\n[\s\S]*?(?=\n\s*(?:#{1,6}\s|\*\*[A-Z]|$))/gi, '\n')
              .replace(/\[HEADLINE:[^\]]+\]/g, '')
              .replace(/\[SO[_ ]WHAT[^\]]*\]/gi, '')
              .replace(/\[CHARTS?:[^\]]+\]/g, '')
              .replace(/\[CONFIDENCE_BREAKDOWN:[^\]]*\]/gi, '')
              .replace(/\[FINDING:[^\]]*\]/gi, '')
              .replace(/\[ANCHOR:[^\]]*\]/gi, '')
              .replace(/\[SEGMENT:[^\]]*\]/gi, '')
              .replace(/\[KILL:[^\]]*\]/gi, '')
              .replace(/\[ASSUME(?:S|PTION)?:[^\]]*\]/gi, '')
              .replace(/\[BECAUSE:[^\]]*\]/gi, '')
              // Catch-all: any remaining UPPER_SNAKE structured tag (e.g. [FOO_BAR:…]).
              .replace(/\[[A-Z][A-Z0-9_]{2,}:[^\]]*\]/g, '')
              .replace(/(?:\n\s*---\s*)?\n?\s*SOURCES:[\s\S]*$/i, '')
              .trim()}
            {@const analysisContentDeduped = collapseDuplicateSentences(analysisContent)}
            <div class="bubble-assistant">
              <!-- P3: mode chips hidden; KPIs rendered inline within prose via markdown replace -->

              <!-- P3: KPI tags stripped from analysisContent above; inline render handled by markdown post-processor below -->


              <!-- IC Verdict cards (investment / acquisition DD) -->
              {#if (msg.content.match(/\[VERDICT:(ACQUIRE|DEFER|PASS|BUY|HOLD|SELL)\|(\d)\|([^\]]+)\]/g) || []).length > 0}
                {@const verdictMatches = msg.content.match(/\[VERDICT:(ACQUIRE|DEFER|PASS|BUY|HOLD|SELL)\|(\d)\|([^\]]+)\]/g) || []}
                <div style="display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px;">
                  {#each verdictMatches as vTag}
                    {@const vParts = vTag.replace('[VERDICT:', '').replace(/\]$/, '').split('|')}
                    {@const vVerdict = vParts[0] || 'DEFER'}
                    {@const vConv = Math.max(0, Math.min(5, parseInt(vParts[1]) || 0))}
                    {@const vRationale = (vParts.slice(2).join('|') || '').trim()}
                    {@const vKind = (vVerdict === 'ACQUIRE' || vVerdict === 'BUY') ? 'acquire' : (vVerdict === 'DEFER' || vVerdict === 'HOLD') ? 'defer' : 'pass'}
                    <div class="verdict-card {vKind}">
                      <div class="verdict-head">
                        <span class="verdict-icon"><Icon name="briefcase" size={14} /></span>
                        <span class="verdict-label">IC VERDICT: {vVerdict}</span>
                      </div>
                      <div class="verdict-conviction">
                        Conviction:
                        {#each [0,1,2,3,4] as ix}
                          <span class="star {ix < vConv ? 'filled' : ''}"><Icon name="star" size={14} /></span>
                        {/each}
                        ({vConv}/5)
                      </div>
                      <div class="verdict-rationale">{vRationale}</div>
                      <div class="verdict-actions">
                        <button onclick={async () => {
                          try {
                            const slug = (msg as any).routing?.slug || (typeof window !== 'undefined' ? (window.location.pathname.match(/\/project\/([^\/]+)/) || [])[1] : '');
                            if (!slug) return;
                            const token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
                            await fetch(`/api/projects/${slug}/investment/memos`, {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                              body: JSON.stringify({ verdict: vVerdict, conviction: vConv, rationale: vRationale })
                            }).catch(() => {});
                          } catch {}
                        }}>SAVE TO MEMOS</button>
                        <button onclick={async () => {
                          try {
                            const token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
                            const res = await fetch('/api/export/pdf', {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                              body: JSON.stringify({ title: `IC Verdict: ${vVerdict}`, content: `# IC Verdict: ${vVerdict}\n\n**Conviction:** ${vConv}/5\n\n${vRationale}` })
                            });
                            if (res.ok) {
                              const blob = await res.blob();
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url; a.download = `verdict-${Date.now()}.pdf`; a.click();
                              URL.revokeObjectURL(url);
                            }
                          } catch {}
                        }}>EXPORT PDF</button>
                      </div>
                    </div>
                  {/each}
                </div>
              {/if}

              <!-- (analysisExtras — thinking trace + proposals — now rendered above the tab bar) -->

              <!-- P3: Confidence bar hidden from default view (kept in DOM under .confidence-row for expanded trace) -->
              {#if msg.content.match(/\[CONFIDENCE:(\w+(?:\s+\w+)?)\]/)}
                {@const confMatch = msg.content.match(/\[CONFIDENCE:(\w+(?:\s+\w+)?)\]/)!}
                {@const confLevel = confMatch[1]}
                {@const confWidth = confLevel === 'VERY HIGH' ? 100 : confLevel === 'HIGH' ? 80 : confLevel === 'MEDIUM' ? 50 : 30}
                <div class="confidence-row" style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                  <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--pw-muted);">CONFIDENCE</span>
                  <div class="confidence-bar-inline" style="flex: 1; max-width: 120px; height: 6px; background: var(--pw-bg-alt); border: 1px solid #555;">
                    <div class="conf-fill" style="width: {confWidth}%; height: 100%; background: {confWidth >= 80 ? 'var(--pw-accent)' : confWidth >= 50 ? '#a06000' : 'var(--pw-error)'};"></div>
                  </div>
                  <span style="font-size: 11px; font-weight: 700; color: {confWidth >= 80 ? 'var(--pw-accent)' : confWidth >= 50 ? '#a06000' : 'var(--pw-error)'};">{confLevel}</span>
                </div>
              {/if}

              <!-- Storytelling: HEADLINE strip -->
              {#if _storyH.headline}
                <div class="cp-headline-strip">
                  <span class="cp-headline-icon"><Icon name="star" size={14} /></span>
                  <span class="cp-headline-text">{@html formatInline(_storyH.headline)}</span>
                </div>
              {/if}

              <!-- One-line confidence summary (replaces 3 all-100 bars when no signal) -->
              {#if _storyCB.breakdown}
                {@const _cb = _storyCB.breakdown}
                {@const _cavg = Math.round(((_cb.dq || 0) + (_cb.qm || 0) + (_cb.rp || 0)) / 3)}
                <div class="cp-conf-line">Confidence: <b style="color:{_cavg >= 80 ? '#2e7d32' : _cavg >= 60 ? '#a06000' : '#c0392b'};">{_cavg >= 80 ? 'High' : _cavg >= 60 ? 'Medium' : 'Low'}</b>{#if _cavg < 100} · {_cb.dq}/{_cb.qm}/{_cb.rp}{/if}</div>
              {/if}

              <!-- Secondary detail folded into one collapsible row (so-what / findings / context / confidence) -->
              {#if _storySW.soWhat || _storyFD.findings.length > 0 || _storySG.segments.length > 0 || _storyAN.anchors.length > 0 || _storyKL.criteria.length > 0 || _storyAS.assumptions.length > 0 || _storyB.causes.length > 0 || _storyCB.breakdown}
              <details class="cp-fold">
                <summary class="cp-fold-sum">So what · key findings · context</summary>
                <div class="cp-fold-body">

              <!-- SO WHAT card (decision-ready, with Save Decision) -->
              {#if _storySW.soWhat}
                {@const _sw = _storySW.soWhat}
                {@const _riskColor = (_sw.risk || '').toLowerCase().includes('high') ? 'var(--pw-error)' : (_sw.risk || '').toLowerCase().includes('med') ? '#a06000' : 'var(--pw-success)'}
                <div style="border:2px solid var(--pw-accent); background:rgba(201,99,66,0.06); padding:10px 12px; margin-bottom:10px; display:flex; flex-direction:column; gap:6px;">
                  <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.08em; color:var(--pw-accent);">▸ SO WHAT</div>
                    <button onclick={async () => {
                      try {
                        const slug = (typeof window !== 'undefined' ? (window.location.pathname.match(/\/project\/([^\/]+)/) || [])[1] : '');
                        if (!slug) return;
                        const token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
                        const btn = (event?.target as HTMLButtonElement);
                        const res = await fetch(`/api/projects/${slug}/decisions`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                          body: JSON.stringify({ action: _sw.action, owner: _sw.owner, effort: _sw.effort, risk: _sw.risk, chat_msg_id: msg.id || null, source_content: msg.content || '' })
                        });
                        if (btn) { btn.textContent = res.ok ? 'SAVED' : 'FAIL'; setTimeout(() => { if (btn) btn.textContent = ' SAVE DECISION'; }, 1500); }
                      } catch {}
                    }} style="font-size:10px; font-weight:800; padding:3px 8px; background:var(--pw-accent); color:white; border:none; cursor:pointer; letter-spacing:0.05em;"><Icon name="star" size={14} /> SAVE DECISION</button>
                  </div>
                  <div style="font-size:14px; font-weight:700; color:var(--pw-ink); line-height:1.45;">{_sw.action}</div>
                  <div style="display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:var(--pw-muted);">
                    {#if _sw.owner}<span><b style="color:var(--pw-ink);">Owner:</b> {_sw.owner}</span>{/if}
                    {#if _sw.effort}<span><b style="color:var(--pw-ink);">Effort:</b> {_sw.effort}</span>{/if}
                    {#if _sw.risk}<span><b style="color:var(--pw-ink);">Risk:</b> <span style="color:{_riskColor}; font-weight:700;">{_sw.risk}</span></span>{/if}
                  </div>
                </div>
              {/if}

              <!-- KEY FINDINGS (MECE, ranked, click to drill) -->
              {#if _storyFD.findings.length > 0}
                <div style="border:1px solid var(--pw-ink); background:var(--pw-surface); padding:10px 12px; margin-bottom:10px;">
                  <div style="font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.08em; color:var(--pw-muted); margin-bottom:8px;">▸ KEY FINDINGS <span style="font-weight:500; color:#999; text-transform:none; letter-spacing:0;">· click row to drill</span></div>
                  {#each _storyFD.findings as f}
                    {@const _sevBg = f.severity === 'HIGH' ? 'var(--pw-error)' : f.severity === 'MED' || f.severity === 'MEDIUM' ? '#a06000' : '#888'}
                    <div role="button" tabindex="0"
                      onclick={() => onSend?.(`Drill into finding: ${f.text}`)}
                      onkeydown={(e) => { if (e.key === 'Enter') onSend?.(`Drill into finding: ${f.text}`); }}
                      style="display:flex; align-items:flex-start; gap:10px; padding:6px 4px; border-bottom:1px dashed #e5e5e0; cursor:pointer; transition:background 0.15s;"
                      onmouseenter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'rgba(201,99,66,0.06)'; }}
                      onmouseleave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                    >
                      <span style="font-weight:900; color:var(--pw-accent); min-width:18px;">{f.rank}.</span>
                      <span style="flex:1; font-size:13px; color:var(--pw-ink); line-height:1.45;">{f.text}</span>
                      {#if f.impact}<span style="font-size:12px; font-weight:700; color:var(--pw-ink);">{f.impact}</span>{/if}
                      {#if f.severity}<span style="font-size:10px; font-weight:900; padding:2px 6px; background:{_sevBg}; color:white;">{f.severity}</span>{/if}
                      <span style="color:var(--pw-accent); font-size:14px; font-weight:900;">⤵</span>
                    </div>
                  {/each}
                </div>
              {/if}

              <!-- SEGMENT BREAKDOWN (clickable sparkline bars) -->
              {#if _storySG.segments.length > 0}
                <div style="border:1px solid var(--pw-ink); background:var(--pw-surface); padding:10px 12px; margin-bottom:10px;">
                  <div style="font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.08em; color:var(--pw-muted); margin-bottom:8px;">▸ BY SEGMENT <span style="font-weight:500; color:#999; text-transform:none; letter-spacing:0;">· click bar to drill</span></div>
                  {#each _storySG.segments as s}
                    <div role="button" tabindex="0"
                      onclick={() => onSend?.(`Drill into segment: ${s.label}`)}
                      onkeydown={(e) => { if (e.key === 'Enter') onSend?.(`Drill into segment: ${s.label}`); }}
                      style="display:flex; align-items:center; gap:8px; padding:3px 4px; font-size:12px; cursor:pointer; transition:background 0.15s;"
                      onmouseenter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'rgba(201,99,66,0.06)'; }}
                      onmouseleave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                    >
                      <span style="min-width:90px; color:var(--pw-ink); font-weight:600;">{s.label}</span>
                      <div style="flex:1; height:10px; background:var(--pw-bg-alt); border:1px solid #ddd; position:relative;">
                        <div style="width:{Math.min(100, Math.max(0, s.pct))}%; height:100%; background:var(--pw-accent);"></div>
                      </div>
                      <span style="min-width:70px; text-align:right; font-weight:700; color:var(--pw-ink);">{s.value}</span>
                      <span style="min-width:42px; text-align:right; color:var(--pw-muted); font-size:11px;">{s.pct.toFixed(1)}%</span>
                    </div>
                  {/each}
                </div>
              {/if}

              <!-- COMPARISON ANCHORS -->
              {#if _storyAN.anchors.length > 0}
                <div style="display:flex; flex-direction:column; gap:4px; margin-bottom:10px; padding:8px 12px; background:var(--pw-bg-alt); border-left:3px solid var(--pw-accent);">
                  <div style="font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.08em; color:var(--pw-muted);">▸ FOR CONTEXT</div>
                  {#each _storyAN.anchors as a}
                    <div style="font-size:12px; color:var(--pw-ink); line-height:1.5;">= {a}</div>
                  {/each}
                </div>
              {/if}

              <!-- KILL CRITERIA (counter-hypothesis) -->
              {#if _storyKL.criteria.length > 0}
                <div style="border:1px dashed var(--pw-error); background:rgba(220,53,69,0.04); padding:8px 12px; margin-bottom:10px;">
                  <div style="font-size:10px; font-weight:900; text-transform:uppercase; letter-spacing:0.08em; color:var(--pw-error); margin-bottom:6px;">▸ WOULD INVALIDATE IF</div>
                  {#each _storyKL.criteria as c}
                    <div style="font-size:12px; color:var(--pw-ink); line-height:1.5;"><Icon name="x" size={14} /> {c}</div>
                  {/each}
                </div>
              {/if}

              <!-- ASSUMPTIONS -->
              {#if _storyAS.assumptions.length > 0}
                <div style="font-size:11px; color:var(--pw-muted); margin-bottom:10px; padding:6px 12px; border-left:2px solid #aaa;">
                  <b style="color:var(--pw-ink); text-transform:uppercase; letter-spacing:0.06em; font-size:10px;">Assumes:</b>
                  {#each _storyAS.assumptions as a, ai}
                    <span> {a}{ai < _storyAS.assumptions.length - 1 ? ' ·' : ''}</span>
                  {/each}
                </div>
              {/if}

              <!-- Storytelling: BECAUSE card -->
              {#if _storyB.causes.length > 0}
                <div class="cp-because-card">
                  <div class="cp-because-head"><Icon name="compass" size={14} /> Why this is happening</div>
                  {#each _storyB.causes as cause, ci}
                    <div class="cp-because-row">
                      <span class="cp-because-num">{ci + 1}</span>
                      <span class="cp-because-text">Likely {cause}</span>
                    </div>
                  {/each}
                </div>
              {/if}

              <!-- Storytelling: CONFIDENCE_BREAKDOWN (3 bars with tooltips) -->
              {#if _storyCB.breakdown}
                <div class="cp-conf3-card">
                  <div class="cp-conf3-row" title="Data quality — % of source rows that are non-null, deduped, schema-valid. {_storyCB.breakdown.dq}% means {_storyCB.breakdown.dq < 70 ? 'noisy data, treat findings as directional' : 'clean data, numbers trustworthy'}.">
                    <span class="cp-conf3-label">Data quality</span>
                    <div class="cp-conf3-bar"><div class="cp-conf3-fill" style="width:{_storyCB.breakdown.dq}%; background:#2e7d32;"></div></div>
                    <span class="cp-conf3-val">{_storyCB.breakdown.dq}%</span>
                  </div>
                  <div class="cp-conf3-row" title="Query match — how well SQL captures the question intent. {_storyCB.breakdown.qm}% means {_storyCB.breakdown.qm < 70 ? 'partial match, may miss edge cases' : 'tight match, answer is on-target'}.">
                    <span class="cp-conf3-label">Query match</span>
                    <div class="cp-conf3-bar"><div class="cp-conf3-fill" style="width:{_storyCB.breakdown.qm}%; background:#3a8dff;"></div></div>
                    <span class="cp-conf3-val">{_storyCB.breakdown.qm}%</span>
                  </div>
                  <div class="cp-conf3-row" title="Reasoning — judge score on the agent's logic chain. {_storyCB.breakdown.rp}% means {_storyCB.breakdown.rp < 70 ? 'shallow analysis, verify before acting' : 'sound reasoning, conclusions defensible'}.">
                    <span class="cp-conf3-label">Reasoning</span>
                    <div class="cp-conf3-bar"><div class="cp-conf3-fill" style="width:{_storyCB.breakdown.rp}%; background:#9b6dff;"></div></div>
                    <span class="cp-conf3-val">{_storyCB.breakdown.rp}%</span>
                  </div>
                </div>
              {/if}

                </div>
              </details>
              {/if}

              <div class="prose-chat">
                {@html markdownToHtml(analysisContentDeduped)
                  .replace(/\[UP:([^\]]+)\]/g, '<span style="color: var(--pw-success); font-weight: 900;">&#9650; $1</span>')
                  .replace(/\[DOWN:([^\]]+)\]/g, '<span style="color: var(--pw-error); font-weight: 900;">&#9660; $1</span>')
                  .replace(/\[FLAT:([^\]]+)\]/g, '<span style="color: #a06000; font-weight: 900;">&#9679; $1</span>')
                  .replace(/\[RISK:HIGH\]/g, '<span style="font-size: 11px;font-weight:900;padding:1px 6px;background:var(--pw-error);color:white;">&#9888; HIGH</span>')
                  .replace(/\[RISK:MEDIUM\]/g, '<span style="font-size: 11px;font-weight:900;padding:1px 6px;background:#a06000;color:var(--pw-ink);">&#9888; MEDIUM</span>')
                  .replace(/\[RISK:LOW\]/g, '<span style="font-size: 11px;font-weight:900;padding:1px 6px;background:var(--pw-success);color:white;">&#10003; LOW</span>')
                  .replace(/(▲|↑)\s*\+?(\d+(?:\.\d+)?)\s*%/g, '<span style="color:var(--pw-success);font-weight:700;">▲ +$2%</span>')
                  .replace(/(▼|↓)\s*-?(\d+(?:\.\d+)?)\s*%/g, '<span style="color:var(--pw-error);font-weight:700;">▼ -$2%</span>')
                  .replace(/(━|→)\s*([+-]?\d+(?:\.\d+)?)\s*%/g, '<span style="color:#888;font-weight:700;">━ $2%</span>')
                  .replace(/(?<![\d.])\+(\d+(?:\.\d+)?%)/g, '<span style="color:var(--pw-success);font-weight:700;">+$1</span>')
                  .replace(/(?<![\d.\w-])-(\d+(?:\.\d+)?%)/g, '<span style="color:var(--pw-error);font-weight:700;">-$1</span>')
                  .replace(/‹‹VERIFIED:([^›]+)››/g, (_m: string, inner: string) =>
                    `<span style="display:inline-block;background:rgba(22,163,74,0.12);color:#15803d;font-size:10px;font-weight:700;letter-spacing:0.04em;padding:2px 6px;border-radius:3px;margin-left:6px;text-transform:uppercase;vertical-align:middle;">✓ ${inner}</span>`)
                }
              </div>

              <!-- Inline Charts -->
              {#if hasTables && tables.length > 0 && chartsEnabled}
                {@const inlineCharts = tables.filter((t: any) => hasNumericData(t)).slice(0, 3)}
                {#if inlineCharts.length > 0}
                  <div style="margin-top: 14px; display: flex; flex-direction: column; gap: 10px;">
                    {#each inlineCharts as tbl}
                      <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                        <div style="padding: 6px 12px; background: var(--pw-ink); color: var(--pw-bg); font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; display: flex; justify-content: space-between; align-items: center;">
                          <span>{chartHint?.title || tbl.headers.slice(0, 3).join(' vs ')}</span>
                          <span style="color: var(--pw-accent); font-size: 11px;">{tbl.rows.length} ROWS</span>
                        </div>
                        <div style="padding: 8px;">
                          <EChartView headers={tbl.headers} rows={tbl.rows} chartType={chartHint?.type || detectChartType(tbl)} />
                        </div>
                        {#if generateChartCaption(tbl)}
                          <div style="padding: 6px 12px; font-size: 11px; color: var(--pw-muted); border-top: 1px solid #e5e5e0; line-height: 1.5;">{generateChartCaption(tbl)}</div>
                        {/if}
                      </div>
                    {/each}
                  </div>
                {/if}
              {/if}

              <!-- Storytelling: ACTION buttons -->
              {#if _storyA.actions.length > 0}
                <div class="cp-actions-row">
                  {#each _storyA.actions as act}
                    <button class="cp-action-btn" data-type={act.type} onclick={() => onAction?.(act)}>
                      <span class="cp-action-icon">{actionIcon(act.type)}</span>
                      <span class="cp-action-label">{act.label}</span>
                    </button>
                  {/each}
                </div>
              {/if}

              <!-- Storytelling: CAVEAT strip -->
              {#if _storyC.caveat}
                <div class="cp-caveat-strip">
                  <span class="cp-caveat-icon"><Icon name="alert-triangle" size={14} /></span>
                  <span class="cp-caveat-text">{_storyC.caveat}</span>
                </div>
              {/if}

              <!-- Impact Summary -->
<!-- IMPACT SUMMARY removed per user request -->
              {#if false}{/if}

              <!-- Clarifying Questions -->
              {#if parseClarify(msg.content)}
                <div class="followup-wrap" style="margin-top: 12px;">
                  <div class="followup-label">Did you mean:</div>
                  <div class="followup-pills">
                    {#each parseClarify(msg.content) || [] as option}
                      <button class="followup-pill" onclick={() => onSend?.(option)} disabled={isStreaming}>{option}</button>
                    {/each}
                  </div>
                </div>
              {/if}

              <!-- Related Questions -->
              {#if (msg.content.match(/\[RELATED:([^\]]+)\]/g) || []).length > 0}
                {@const relatedMatches = msg.content.match(/\[RELATED:([^\]]+)\]/g) || []}
                <div class="followup-wrap">
                  <div class="followup-label">Related questions</div>
                  <div class="followup-pills">
                    {#each relatedMatches as rq}
                      <button class="followup-pill" onclick={() => onSend?.(rq.replace('[RELATED:', '').replace(']', ''))} disabled={isStreaming}>{rq.replace('[RELATED:', '').replace(']', '')}</button>
                    {/each}
                  </div>
                </div>
              {:else}
                {@const fb = fallbackRelated(msg, hasTables)}
                {#if fb.length > 0}
                  <div class="followup-wrap">
                    <div class="followup-label">Related questions</div>
                    <div class="followup-pills">
                      {#each fb as rq}
                        <button class="followup-pill" onclick={() => onSend?.(rq)} disabled={isStreaming}>{rq}</button>
                      {/each}
                    </div>
                  </div>
                {/if}
              {/if}

            </div>
            {/if}
          {/if}

          <!-- TAB: Data -->
          {#if currentTabA === 'data' && hasTables}
            {@const dataIdx = msg.dataTableIndex || 0}
            {@const activeTable = tables[dataIdx] || tables[0]}
            {@const _anom = parseAnomalies(msg.content).anomalies}
            {@const anomalyMap = (() => {
              const m = new Map<string, string>();
              for (const a of _anom) m.set(`${a.column.toLowerCase()}|${a.value}`, a.reason);
              return m;
            })()}
            {@const escapeAttr = (s: string) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}
            <div class="bubble-assistant">
              {#if tables.length > 1}
                <div style="display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap;">
                  {#each tables as _t, ti}
                    <button
                      style="font-size: 11px; font-weight: 900; padding: 3px 10px; text-transform: uppercase; letter-spacing: 0.05em; border: 2px solid {dataIdx === ti ? 'var(--pw-ink)' : 'var(--pw-muted)'}; background: {dataIdx === ti ? 'var(--pw-ink)' : 'transparent'}; color: {dataIdx === ti ? 'var(--pw-bg)' : 'var(--pw-muted)'}; cursor: pointer;"
                      onclick={() => updateMessage(i, { dataTableIndex: ti })}
                    >Table {ti + 1}</button>
                  {/each}
                </div>
              {/if}
              <div style="font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; color: var(--pw-muted);">
                {activeTable.rows.length} ROWS &middot; {activeTable.headers.length} COLUMNS{#if tables.length > 1} &middot; TABLE {dataIdx + 1} OF {tables.length}{/if}
              </div>
              <div style="overflow-x: auto;">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th style="width: 36px; text-align: center; color: var(--pw-muted);">#</th>
                      {#each activeTable.headers as h}<th>{h.replace(/\*\*/g, '')}</th>{/each}
                    </tr>
                  </thead>
                  <tbody>
                    {#each activeTable.rows as row, ri}
                      <tr>
                        <td style="text-align: center; color: var(--pw-muted); font-size: 10px;">{ri + 1}</td>
                        {#each row as cell, ci}
                          {@const _col = (activeTable.headers[ci] || '').replace(/\*\*/g, '').toLowerCase()}
                          {@const _val = String(cell || '').trim()}
                          {@const _anomReason = anomalyMap.get(`${_col}|${_val}`)}
                          {#if _anomReason}
                            <td><span class="cell-anomaly" title={_anomReason}>{@html formatCell(cell)}</span></td>
                          {:else}
                            <td>{@html formatCell(cell)}</td>
                          {/if}
                        {/each}
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              <div style="display: flex; gap: 8px; margin-top: 12px;">
                <button class="feedback-btn" onclick={() => onExportCsv?.(i, [activeTable])} style="font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 4px 10px;">DOWNLOAD CSV</button>
                <button class="feedback-btn" onclick={() => onPin?.(i, tables)} style="font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 4px 10px;">{pinnedMap[i] ? 'PINNED' : 'PIN TO DASHBOARD'}</button>
              </div>
            </div>

          {/if}

          <!-- TAB: Query -->
          {#if currentTabA === 'query'}
            {#if hasQueries}
              <div style="display: flex; flex-direction: column; gap: 10px;">
                {#each msg.sqlQueries || [] as sql, si}
                  <div style="border: 1px solid var(--pw-border); border-radius: var(--pw-radius-sm); overflow: hidden; background: var(--pw-surface);">
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 14px; background: var(--pw-bg-alt); border-bottom: 1px solid var(--pw-border);">
                      <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.02em; color: var(--pw-ink-soft); font-family: 'JetBrains Mono', ui-monospace, monospace;">▶ query {si + 1}</span>
                      <button class="feedback-btn" onclick={() => onCopySql ? onCopySql(sql) : navigator.clipboard.writeText(sql)} style="font-size: 10px; font-weight: 600; padding: 4px 12px; color: var(--pw-accent); background: transparent; border: 1px solid var(--pw-accent); border-radius: var(--pw-radius-pill); cursor: pointer;">Copy</button>
                    </div>
                    <pre style="margin: 0; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12.5px; color: #e0e0e0; white-space: pre-wrap; word-break: break-all; line-height: 1.55; background: #1a1a1a; padding: 14px 16px;"><code>{sql}</code></pre>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="query-empty" style="padding: 24px; text-align: center; color: var(--pw-muted);">
                <div style="font-size: 19px; margin-bottom: 6px;"></div>
                <div style="font-weight: 700; margin-bottom: 4px;">No SQL executed</div>
                <div style="font-size: 11px;">Agent answered using semantic knowledge — no database query was run for this response.</div>
              </div>
            {/if}

          {/if}

          <!-- TAB: Chart -->
          {#if currentTabA === 'chart' && hasChartData}
            {@const chartTables = tables.filter((t: any) => hasNumericData(t))}
            {@const chartIdx = msg.dataTableIndex || 0}
            {@const activeChartTable = chartTables[chartIdx] || chartTables[0]}
            <div class="bubble-assistant" style="padding: 12px;">
              {#if chartTables.length > 1}
                <div style="display: flex; gap: 4px; margin-bottom: 10px; flex-wrap: wrap;">
                  {#each chartTables as _ct, ci}
                    <button
                      style="font-size: 11px; font-weight: 900; padding: 3px 10px; text-transform: uppercase; letter-spacing: 0.05em; border: 2px solid {chartIdx === ci ? 'var(--pw-ink)' : 'var(--pw-muted)'}; background: {chartIdx === ci ? 'var(--pw-ink)' : 'transparent'}; color: {chartIdx === ci ? 'var(--pw-bg)' : 'var(--pw-muted)'}; cursor: pointer;"
                      onclick={() => updateMessage(i, { dataTableIndex: ci, chartType: undefined })}
                    >Chart {ci + 1}</button>
                  {/each}
                </div>
              {/if}
              {#if chartHint?.title}
                <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; color: var(--pw-ink);">{chartHint.title}</div>
              {/if}
              <div style="display: flex; gap: 0; margin-bottom: 8px;">
                {#each getAvailableTypes(activeChartTable) as ct}
                  <button class="chart-type-btn" class:chart-type-btn-active={(msg.chartType || chartHint?.type || detectChartType(activeChartTable)) === ct} onclick={() => { onTrackPreference?.('chart_type', ct); updateMessage(i, { chartType: ct }); }}>{ct.toUpperCase()}</button>
                {/each}
              </div>
              <div style="background: var(--pw-surface); padding: 8px; border: 2px solid var(--pw-ink);">
                <EChartView headers={activeChartTable.headers} rows={activeChartTable.rows} chartType={msg.chartType || chartHint?.type || detectChartType(activeChartTable)} />
              </div>
              <div style="display: flex; gap: 8px; margin-top: 12px;">
                <button class="feedback-btn" onclick={() => onPin?.(i, tables)} style="font-size: 10px; font-weight: 700; text-transform: uppercase; padding: 4px 10px;">{pinnedMap[i] ? 'PINNED' : 'PIN TO DASHBOARD'}</button>
              </div>
            </div>

          {/if}

          <!-- TAB: Sources -->
          {#if currentTabA === 'sources'}
            {@const sqlTableNames = (() => {
              const names = new Set<string>();
              for (const sql of (msg.sqlQueries || [])) {
                const fromMatches = sql.match(/(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)/gi) || [];
                for (const m of fromMatches) {
                  const t = m.replace(/^(?:FROM|JOIN)\s+/i, '').replace(/^[a-z_]+\./i, '').trim();
                  if (t && !['select','where','and','or','on','as','group','order','limit','having','union','case','when','then','else','end','not','in','is','null','like','between','exists','all','any','inner','outer','left','right','cross','natural','using','lateral','with','recursive','distinct','count','sum','avg','max','min','coalesce','cast','extract','date_trunc','to_char','row_number','rank','dense_rank','lag','lead','over','partition'].includes(t.toLowerCase())) names.add(t);
                }
              }
              return [...names];
            })()}
            {@const confMatch3 = msg.content.match(/\[CONFIDENCE:(\w+[^\]]*)\]/)}
            {@const confidence = confMatch3 ? confMatch3[1].trim() : (msg.qualityScore ? (msg.qualityScore >= 4 ? 'HIGH' : msg.qualityScore >= 3 ? 'MEDIUM' : 'LOW') : '')}
            {@const confColor = confidence.includes('VERY HIGH') ? '#c96342' : confidence === 'HIGH' ? '#c96342' : confidence === 'MEDIUM' ? '#a06000' : confidence === 'LOW' ? '#c0392b' : '#888'}
            {@const confPct = confidence.includes('VERY HIGH') ? 95 : confidence === 'HIGH' ? 85 : confidence === 'MEDIUM' ? 60 : confidence === 'LOW' ? 30 : 0}
            {@const srcAgent = msg.routing?.routed_to || agentName}
            {@const srcAnalysisType = msg.analysisUsed || msg.content.match(/\[ANALYSIS:([^\]]+)\]/)?.[1] || ''}
            {@const srcToolCalls = (msg.toolCalls || []).filter((t: any) => (t.name || '') !== 'transfer_to_team_member')}
            {@const srcTotalDuration = (() => { let s = 0; for (const t of srcToolCalls) { if (t.duration) s += parseFloat(t.duration); } return s > 0 ? s.toFixed(1) + 's' : ''; })()}
            <div style="display: flex; flex-direction: column; gap: 12px;">
              {#if msg.sources_used && msg.sources_used.length >= 2}
                <div class="ink-border" style="padding:10px 12px; background:var(--pw-accent-bg, var(--pw-bg-alt)); border:1px solid var(--pw-accent-soft, var(--pw-border)); border-radius:var(--pw-radius-sm);">
                  <strong>FEDERATED</strong> · {msg.sources_used.length} sources joined
                  {#if msg.federated_meta}
                    <span style="opacity:0.7;">· {msg.federated_meta.latency_ms}ms · ${msg.federated_meta.cost_usd?.toFixed(4) || '0.0000'} · engine: {msg.federated_meta.engine || 'duckdb'}</span>
                  {/if}
                </div>
              {/if}
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px;">
                <div style="padding: 12px 14px; border: 2px solid var(--pw-ink); border-bottom-width: 3px; background: var(--pw-surface);">
                  <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">AGENT</div>
                  <div style="font-size: 14px; font-weight: 900; text-transform: uppercase;">{srcAgent}</div>
                </div>
                <div style="padding: 12px 14px; border: 2px solid var(--pw-ink); border-bottom-width: 3px; background: var(--pw-surface);">
                  <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">MODE</div>
                  <div style="font-size: 14px; font-weight: 900; text-transform: uppercase;">{msg.reasoningUsed || 'AUTO'}</div>
                </div>
                {#if msg.sqlQueries?.length}
                  <div style="padding: 12px 14px; border: 2px solid var(--pw-ink); border-bottom-width: 3px; background: var(--pw-surface);">
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">QUERIES</div>
                    <div style="font-size: 14px; font-weight: 900;">{msg.sqlQueries.length}</div>
                  </div>
                {/if}
                {#if hasTables}
                  <div style="padding: 12px 14px; border: 2px solid var(--pw-ink); border-bottom-width: 3px; background: var(--pw-surface);">
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">RESULT TABLES</div>
                    <div style="font-size: 14px; font-weight: 900;">{tables.length}</div>
                  </div>
                {/if}
                {#if confidence}
                  <div style="padding: 12px 14px; border: 2px solid {confColor}; border-bottom-width: 3px; background: {confColor}08;">
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">CONFIDENCE</div>
                    <div style="font-size: 14px; font-weight: 900; color: {confColor};">{confidence}</div>
                    <div style="height: 4px; background: #e5e5e0; margin-top: 6px;"><div style="height: 100%; width: {confPct}%; background: {confColor};"></div></div>
                  </div>
                {/if}
                {#if srcAnalysisType}
                  <div style="padding: 12px 14px; border: 2px solid var(--pw-ink); border-bottom-width: 3px; background: var(--pw-surface);">
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">ANALYSIS</div>
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase;">{srcAnalysisType}</div>
                  </div>
                {/if}
                {#if msg.verified && msg.verified.verified && msg.verified.verified !== 'unknown'}
                  {@const vpass = msg.verified.verified === 'pass'}
                  {@const vcolor = vpass ? '#c96342' : '#c0392b'}
                  <div style="padding: 12px 14px; border: 2px solid {vcolor}; border-bottom-width: 3px; background: {vcolor}08;">
                    <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 6px;">VERIFIED vs TRUTH</div>
                    <div style="font-size: 13px; font-weight: 900; color: {vcolor};">{vpass ? '✓ Matches' : '✗ Differs'}</div>
                    <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">
                      {#if vpass}matches pinned truth: {msg.verified.expected}{:else}got {msg.verified.got} · truth {msg.verified.expected}{/if}
                    </div>
                  </div>
                {/if}
              </div>
              {#if sqlTableNames.length}
                <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                  <div style="padding: 8px 14px; background: var(--pw-ink); color: var(--pw-bg); font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em;">DATA SOURCES &middot; {sqlTableNames.length} {sqlTableNames.length === 1 ? 'TABLE' : 'TABLES'}</div>
                  <div style="padding: 10px 14px; display: flex; flex-wrap: wrap; gap: 6px;">
                    {#each sqlTableNames as tbl}
                      <span style="padding: 5px 12px; background: var(--pw-bg-alt); color: var(--pw-accent-ink, var(--pw-accent)); font-size: 11px; font-weight: 600; font-family: 'JetBrains Mono', ui-monospace, monospace; border: 1px solid var(--pw-border); border-radius: var(--pw-radius-pill);">{tbl}</span>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if hasTables}
                <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                  <div style="padding: 8px 14px; background: var(--pw-ink); color: var(--pw-bg); font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em;">RESULT DATA &middot; {tables.length} {tables.length === 1 ? 'TABLE' : 'TABLES'}</div>
                  {#each tables as tbl, ti}
                    <div style="padding: 8px 14px; border-bottom: 1px solid #e5e5e0; display: flex; justify-content: space-between; align-items: center;">
                      <div>
                        <span style="font-size: 10px; font-weight: 900; color: var(--pw-muted); margin-right: 8px;">T{ti+1}</span>
                        <span style="font-size: 11px; font-weight: 700;">{tbl.headers.slice(0, 5).join(' · ')}{tbl.headers.length > 5 ? ` +${tbl.headers.length - 5}` : ''}</span>
                      </div>
                      <div style="display: flex; gap: 10px; font-size: 11px; color: var(--pw-muted);">
                        <span>{tbl.rows.length} rows</span>
                        <span>{tbl.headers.length} cols</span>
                      </div>
                    </div>
                  {/each}
                </div>
              {/if}
              {#if srcToolCalls.length}
                <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                  <div style="padding: 8px 14px; background: var(--pw-ink); color: var(--pw-bg); font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; display: flex; justify-content: space-between;">
                    <span>EXECUTION LOG &middot; {srcToolCalls.length} STEPS</span>
                    {#if srcTotalDuration}<span style="color: var(--pw-accent);">{srcTotalDuration} total</span>{/if}
                  </div>
                  {#each srcToolCalls as tc, ti}
                    <div style="padding: 6px 14px; border-bottom: 1px solid #f0f0ea; display: flex; align-items: center; gap: 10px; font-size: 11px;">
                      <span style="font-size: 10px; color: var(--pw-muted); font-weight: 700; min-width: 18px;">{ti+1}</span>
                      <span style="width: 8px; height: 8px; background: {tc.status === 'done' ? 'var(--pw-accent, #c96342)' : tc.status === 'error' ? '#c0392b' : '#a06000'}; flex-shrink: 0;"></span>
                      <span style="font-weight: 700; font-family: 'JetBrains Mono', ui-monospace, monospace; flex: 1;">{tc.name}</span>
                      {#if tc.duration}<span style="font-size: 10px; color: var(--pw-muted); font-family: 'JetBrains Mono', ui-monospace, monospace;">{tc.duration}</span>{/if}
                    </div>
                  {/each}
                </div>
              {/if}
              {#if msg.sqlQueries?.length}
                <div style="border: 1px solid var(--pw-border); border-radius: var(--pw-radius-sm); overflow: hidden;">
                  <div style="padding: 9px 14px; background: var(--pw-bg-alt); color: var(--pw-ink); font-size: 11px; font-weight: 600; letter-spacing: 0.02em; border-bottom: 1px solid var(--pw-border);">▶ sql queries</div>
                  {#each msg.sqlQueries as sql, si}
                    <div style="background: #1a1a1a; padding: 12px 14px; border-bottom: 1px solid #2a2a2a;">
                      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 10px; font-weight: 600; color: var(--pw-accent); letter-spacing: 0.02em; font-family: 'JetBrains Mono', ui-monospace, monospace;">▶ query {si+1}</span>
                        <button style="font-size: 10px; font-weight: 600; padding: 3px 10px; color: var(--pw-accent); border: 1px solid var(--pw-accent); background: transparent; border-radius: var(--pw-radius-pill); cursor: pointer;" onclick={() => onCopySql ? onCopySql(sql) : navigator.clipboard.writeText(sql)}>Copy</button>
                      </div>
                      <pre style="margin: 0; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11.5px; color: #e0e0e0; white-space: pre-wrap; word-break: break-all; line-height: 1.55;">{sql}</pre>
                    </div>
                  {/each}
                </div>
              {/if}

              <!-- Freshness sub-section: [FRESHNESS: tbl|asof] -->
              {#if true}
              {@const _freshnessParsed = parseFreshness(msg.content || '')}
              {#if _freshnessParsed.items.length > 0}
                <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                  <div class="block-label" style="padding: 8px 14px; background: var(--pw-ink); color: var(--pw-bg); font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0;">FRESHNESS &middot; {_freshnessParsed.items.length} {_freshnessParsed.items.length === 1 ? 'TABLE' : 'TABLES'}</div>
                  <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
                    <thead>
                      <tr>
                        <th style="text-align: left; padding: 6px 14px; border-bottom: 1px solid #e5e5e0; font-size: 10px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--pw-ink-muted, var(--pw-muted));">Table</th>
                        <th style="text-align: left; padding: 6px 14px; border-bottom: 1px solid #e5e5e0; font-size: 10px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--pw-ink-muted, var(--pw-muted));">As of</th>
                      </tr>
                    </thead>
                    <tbody>
                      {#each _freshnessParsed.items as fr}
                        <tr>
                          <td style="padding: 6px 14px; border-bottom: 1px solid #f0f0ea; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11.5px;">{fr.table || '—'}</td>
                          <td style="padding: 6px 14px; border-bottom: 1px solid #f0f0ea; color: var(--pw-ink-muted, var(--pw-muted));">{fr.as_of && fr.as_of.trim() ? fr.as_of : '—'}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </div>
              {/if}
              {/if}

              <!-- Lineage sub-section: [LINEAGE: upstream→table] grouped by destination -->
              {#if true}
              {@const _lineageParsed = parseLineage(msg.content || '')}
              {#if _lineageParsed.items.length > 0}
                {@const _lineageGrouped = (() => {
                  const groups = new Map<string, string[]>();
                  for (const ln of _lineageParsed.items) {
                    const key = ln.table || '—';
                    if (!groups.has(key)) groups.set(key, []);
                    groups.get(key)!.push(ln.upstream);
                  }
                  return [...groups.entries()].map(([table, upstreams]) => ({ table, upstreams }));
                })()}
                <div style="border: 2px solid var(--pw-ink); background: var(--pw-surface);">
                  <div class="block-label" style="padding: 8px 14px; background: var(--pw-ink); color: var(--pw-bg); font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0;">LINEAGE &middot; {_lineageGrouped.length} {_lineageGrouped.length === 1 ? 'TABLE' : 'TABLES'}</div>
                  <div style="padding: 10px 14px; display: flex; flex-direction: column; gap: 8px;">
                    {#each _lineageGrouped as grp}
                      <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12px;">
                        {#each grp.upstreams as up, ui}
                          <span style="padding: 3px 10px; background: var(--pw-bg-alt); color: var(--pw-ink); font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11px; border: 1px solid var(--pw-border); border-radius: var(--pw-radius-pill);">{up}</span>
                          {#if ui < grp.upstreams.length - 1}
                            <span style="color: var(--pw-ink-muted, var(--pw-muted)); font-weight: 700;">·</span>
                          {/if}
                        {/each}
                        <span style="color: var(--pw-accent); font-weight: 700; font-size: 14px; margin: 0 4px;">→</span>
                        <span style="padding: 3px 10px; background: var(--pw-accent); color: #fff; font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11px; font-weight: 700; border-radius: var(--pw-radius-pill);">{grp.table}</span>
                      </div>
                    {/each}
                  </div>
                </div>
              {/if}
              {/if}
            </div>
          {/if}

          <!-- Live "generating" indicator while the answer is still streaming -->
          {#if msg.status === 'streaming'}
            <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--pw-accent)" stroke-width="2"><rect x="3" y="11" width="18" height="10"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/></svg><div class="typing-indicator"><span></span><span></span><span></span></div></div>
          {/if}

          <!-- panel_announcement pills: mini-thumbnail + ✓ Added X (N rows). Click → scroll right pane to that panel. -->
          {#if Array.isArray((msg as any).panelAnnouncements) && (msg as any).panelAnnouncements.length > 0}
            <div class="panel-anno-list">
              {#each (msg as any).panelAnnouncements as anno (anno.panel_id || anno.id)}
                <button class="panel-anno-pill"
                        title={anno.message || 'View panel'}
                        onclick={() => scrollToPanel(anno.panel_id)}>
                  <span class="panel-anno-thumb" use:miniChart={anno.mini_thumbnail_spec || {}}></span>
                  <span class="panel-anno-msg">{anno.message || '✓ Added panel'}</span>
                </button>
              {/each}
            </div>
          {/if}

          <!-- Always-visible action row (below tab container, regardless of activeTab) -->
          {#if msg.status !== 'streaming'}
          {#if analysisActions}
            {@render analysisActions({ msg, index: i, tables, hasTables })}
          {:else}
            <div class="msg-actions">
              <button class="msg-action-icon" title="Helpful" aria-label="Helpful" onclick={() => onFeedback?.(i, 'up')}><Icon name="thumbs-up" size={15} /></button>
              <button class="msg-action-icon" title="Not helpful" aria-label="Not helpful" onclick={() => onFeedback?.(i, 'down')}><Icon name="thumbs-down" size={15} /></button>
              <button class="msg-action-icon" title={(typeof copiedIndex === 'number' && copiedIndex >= 0 && copiedIndex === i) ? 'Copied' : 'Copy'} aria-label="Copy" onclick={() => onCopy?.(i)}><Icon name={(typeof copiedIndex === 'number' && copiedIndex >= 0 && copiedIndex === i) ? 'check' : 'clipboard'} size={15} /></button>
              {#if hasTables}<button class="msg-action-icon" title="Export CSV" aria-label="Export CSV" onclick={() => onExportCsv?.(i, tables)}><Icon name="download" size={15} /></button>{/if}
              <button class="msg-action-icon" title="Save to memory" aria-label="Save" onclick={() => onSaveMemory?.(i)}><Icon name="star" size={15} /></button>
              <button class="msg-action-icon" title={pinnedMap[i] ? 'Pinned' : 'Pin to dashboard'} aria-label="Pin" aria-pressed={!!pinnedMap[i]} onclick={() => onPin?.(i, tables)} style={pinnedMap[i] ? 'color: var(--pw-accent, #c96342);' : ''}><Icon name="pin" size={15} /></button>
              {#if onSchedule && msg.role === 'assistant' && (msg.status === 'done' || (msg as any).status === 'complete')}
                <button class="msg-action-icon" title="Schedule as workflow" aria-label="Schedule" onclick={() => onSchedule?.(i)}><Icon name="calendar" size={15} /></button>
              {/if}
              <button class="msg-action-icon" title="Export PDF" aria-label="Export PDF" onclick={() => onExportPdf?.(i)}><Icon name="file-text" size={15} /></button>
            </div>
          {/if}
          {/if}

        <!-- Still streaming (no content yet) -->
        {:else}
          <!-- Live reasoning trace — shows thinking/tools in real time, before the answer renders -->
          {#if analysisExtras}{@render analysisExtras({ msg, index: i })}{/if}
          {@const streamProse = stripStructureTags(msg.content || '')}
          <div class="bubble-assistant">
            {#if streamProse}
              <div class="prose-chat">{@html markdownToHtml(streamProse)
                .replace(/‹‹VERIFIED:([^›]+)››/g, (_m: string, inner: string) =>
                  `<span style="display:inline-block;background:rgba(22,163,74,0.12);color:#15803d;font-size:10px;font-weight:700;letter-spacing:0.04em;padding:2px 6px;border-radius:3px;margin-left:6px;text-transform:uppercase;vertical-align:middle;">✓ ${inner}</span>`)
              }</div>
              {#if msg.status === 'streaming'}
                <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--pw-accent)" stroke-width="2"><rect x="3" y="11" width="18" height="10"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/></svg><div class="typing-indicator"><span></span><span></span><span></span></div></div>
              {/if}
            {:else if msg.status === 'streaming'}
              <div style="display: flex; align-items: center; gap: 8px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--pw-accent)" stroke-width="2"><rect x="3" y="11" width="18" height="10"/><path d="M12 2v4"/><circle cx="12" cy="7" r="1"/></svg><div class="typing-indicator"><span></span><span></span><span></span></div></div>
            {/if}
            {#if Array.isArray((msg as any).panelAnnouncements) && (msg as any).panelAnnouncements.length > 0}
              <div class="panel-anno-list">
                {#each (msg as any).panelAnnouncements as anno (anno.panel_id || anno.id)}
                  <button class="panel-anno-pill"
                          title={anno.message || 'View panel'}
                          onclick={() => scrollToPanel(anno.panel_id)}>
                    <span class="panel-anno-thumb" use:miniChart={anno.mini_thumbnail_spec || {}}></span>
                    <span class="panel-anno-msg">{anno.message || '✓ Added panel'}</span>
                  </button>
                {/each}
              </div>
            {/if}
          </div>
        {/if}

        <!-- Deep agent trace panel REMOVED: duplicated the tool list now shown by ReasoningTrace. -->

        <!-- Host-provided per-message footer (learning cards, suggestions, etc) -->
        {#if messageFooter}{@render messageFooter({ msg, index: i })}{/if}
      </div>
    </div>
  {:else}
    <!-- User message -->
    <div class="msg-row msg-user" style="display: flex; gap: 12px; margin-bottom: 16px; justify-content: flex-end;">
      <div class="dash-user-bubble bubble-user">{msg.content}</div>
    </div>
  {/if}
{/each}

<style>
 /* Legacy .trace-line / .trace-expanded / .mode-chip / .ana-chip / live-sql styles
    REMOVED — that trace UI is now owned entirely by ReasoningTrace.svelte. */

 .panel-anno-list {
   display: flex;
   flex-direction: column;
   gap: 6px;
   margin-top: 8px;
 }
 .panel-anno-pill {
   display: inline-flex;
   align-items: center;
   gap: 10px;
   padding: 6px 10px 6px 6px;
   border: 1px solid rgba(201, 99, 66, 0.25);
   background: rgba(201, 99, 66, 0.05);
   border-radius: 0;
   cursor: pointer;
   font-family: 'Inter', system-ui, sans-serif;
   font-size: 12px;
   color: var(--pw-ink, #1a1614);
   transition: background 120ms, border-color 120ms;
   text-align: left;
   max-width: 100%;
   align-self: flex-start;
 }
 .panel-anno-pill:hover {
   background: rgba(201, 99, 66, 0.1);
   border-color: rgba(201, 99, 66, 0.45);
 }
 .panel-anno-thumb {
   display: inline-block;
   width: 200px;
   height: 80px;
   flex-shrink: 0;
   background: #fff;
   border: 1px solid rgba(0,0,0,0.06);
 }
 .panel-anno-msg {
   font-weight: 600;
   color: #c96342;
   white-space: nowrap;
   overflow: hidden;
   text-overflow: ellipsis;
 }

 /* P8: Hover-reveal icon-only action row (Claude-style) */
 .msg-actions {
 display: flex;
 gap: 4px;
 margin-top: 12px;
 opacity: 0.3;
 transition: opacity 200ms;
 }
 :global(.bubble-assistant):hover .msg-actions,
 :global(.msg-row):hover .msg-actions,
 .msg-actions:hover,
 .msg-actions:focus-within {
 opacity: 1;
 }
 .msg-action-icon {
 background: none;
 border: none;
 padding: 6px;
 border-radius: 0;
 font-size: 13px;
 line-height: 1;
 cursor: pointer;
 color: #6b6b6b;
 width: 28px;
 height: 28px;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 }
 .msg-action-icon:hover { background: #f5f5f5; color: #1a1614; }
 .msg-action-icon:focus-visible { outline: 2px solid var(--pw-accent, #c96342); outline-offset: 1px; }
 /* Touch / coarse pointers: keep actions always visible (no hover affordance). */
 @media (hover: none), (pointer: coarse), (max-width: 640px) {
 .msg-actions { opacity: 1; }
 }

 /* P3: Drop response card chrome — Claude-style plain prose */
 :global(.bubble-assistant) {
 background: transparent !important;
 border: none !important;
 padding: 0 !important;
 box-shadow: none !important;
 max-width: 100%;
 }
 /* P3: hide FAST/DEEP mode chips + confidence bar from default view */
 :global(.fast-badge),
 :global(.deep-badge),
 :global(.mode-pill) { display: none !important; }
 :global(.confidence-bar-inline),
 :global(.confidence-row) { display: none !important; }

 /* Design A: pill-segment top tab row (matches ALL/MINE/FAVORITES filter style) */
 .response-tabs-top {
 display: inline-flex;
 gap: 2px;
 background: #f0ece4; /* warm cream pill track */
 border-radius: 0;
 padding: 4px;
 margin-bottom: 16px;
 flex-wrap: wrap;
 }
 .response-tabs-top button {
 background: transparent;
 border: none;
 border-radius: 0;
 padding: 6px 16px;
 font-size: 11px;
 font-weight: 600;
 letter-spacing: 0.04em;
 text-transform: uppercase;
 color: #6b6b6b;
 cursor: pointer;
 transition: background 150ms, color 150ms, box-shadow 150ms;
 font-family: inherit;
 }
 .response-tabs-top button:hover {
 color: #1a1614;
 }
 .response-tabs-top button.active {
 background: #ffffff;
 color: #1a1614;
 box-shadow: 0 1px 3px rgba(0,0,0,0.08);
 }

 /* Related/Clarify followup pills — left-aligned text, fit-content width */
 :global(.followup-pills) {
 flex-direction: column !important;
 align-items: stretch !important;
 gap: 6px !important;
 }
 :global(.followup-pill) {
 text-align: left !important;
 width: 100% !important;
 justify-content: flex-start !important;
 padding: 10px 16px !important;
 font-size: 12px !important;
 line-height: 1.4 !important;
 border-radius: 0!important;
 display: flex !important;
 align-items: center !important;
 }
 :global(.followup-pill::before) {
 content: '▸';
 margin-right: 8px;
 color: var(--pw-muted, #6b6b6b);
 flex-shrink: 0;
 }

 /* P7: inline data table */
 .response-table-inline {
 width: 100%;
 border-collapse: collapse;
 margin: 16px 0;
 font-size: 12px;
 }
 .response-table-inline :global(th),
 .response-table-inline :global(td) {
 padding: 6px 10px;
 border-bottom: 1px solid #ebebeb;
 text-align: left;
 }
 .response-table-inline :global(th) {
 font-weight: 600;
 color: #6b6b6b;
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }
 .response-table-inline :global(tbody tr:hover) { background: #fafafa; }
 .show-all-link {
 background: none;
 border: none;
 color: #6b6b6b;
 font-size: 11px;
 cursor: pointer;
 margin-top: -8px;
 padding: 4px 0;
 font-family: inherit;
 }
 .show-all-link:hover { color: #1a1614; }

 /* Issue #25 — canonical user bubble lives in app.css `.dash-user-bubble`.
 Keep this class as a no-op marker for backwards compat / selector hooks.
 Do not re-declare styles here; compose via class on the element. */
 /* Issue #25 — `.bubble-user` retained as a legacy class hook for selectors and
 tests. Visuals live in app.css `.dash-user-bubble`. Empty declaration keeps
 Svelte from warning about an unused class. */
 .bubble-user { display: inline-block; }
 .verdict-card {
 padding: 14px;
 border-radius: 0;
 border-left: 4px solid;
 background: var(--pw-surface, #fff);
 }
 .verdict-card.acquire { background: rgba(16,185,129,0.08); border-color: #10b981; }
 .verdict-card.defer { background: rgba(245,158,11,0.08); border-color: #f59e0b; }
 .verdict-card.pass { background: rgba(220,38,38,0.08); border-color: #dc2626; }
 .verdict-head { font-weight: 700; font-size: 13px; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
 .verdict-icon { font-size: 14px; }
 .verdict-label { letter-spacing: 0.04em; }
 .verdict-conviction { font-size: 12px; color: var(--pw-muted, #555); margin-bottom: 8px; display: flex; align-items: center; gap: 4px; }
 .verdict-conviction .star { color: #ccc; font-size: 13px; }
 .verdict-conviction .star.filled { color: #f59e0b; }
 .verdict-rationale { font-size: 12px; line-height: 1.5; margin-bottom: 10px; color: var(--pw-ink); }
 .verdict-actions { display: flex; gap: 8px; }
 .verdict-actions button {
 padding: 4px 12px;
 font-size: 11px;
 font-weight: 700;
 letter-spacing: 0.04em;
 border: 1px solid var(--pw-accent, #c96342);
 background: var(--pw-surface, #fff);
 color: var(--pw-accent, #c96342);
 cursor: pointer;
 border-radius: 0;
 }
 .verdict-actions button:hover { background: var(--pw-accent, #c96342); color: #fff; }

 /* Storytelling: HEADLINE strip */
 /* One-line confidence + folded secondary detail */
 .cp-conf-line { font-size: 12px; color: var(--pw-muted, #6f6e69); margin: 0 0 10px; }
 .cp-fold { margin: 0 0 12px; }
 .cp-fold-sum {
  list-style: none; cursor: pointer; user-select: none;
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--pw-muted, #6f6e69);
  padding: 5px 0; display: flex; align-items: center; gap: 6px;
 }
 .cp-fold-sum::-webkit-details-marker { display: none; }
 .cp-fold-sum::before { content: '▸'; color: var(--pw-dim, #97968f); transition: transform 0.15s ease; }
 .cp-fold[open] > .cp-fold-sum::before { transform: rotate(90deg); }
 .cp-fold-sum:hover { color: var(--pw-ink, #2c2a26); }
 .cp-fold-body { padding-top: 6px; }

 .cp-headline-strip {
 display: flex; align-items: flex-start; gap: 12px;
 padding: 14px 18px;
 background: linear-gradient(135deg, rgba(201,99,66,0.06) 0%, var(--pw-surface) 60%);
 border-left: 4px solid var(--pw-accent);
 border-radius: 0;
 margin: 0 0 14px 0;
 font-family: var(--pw-font-serif, var(--pw-font-body));
 }
 .cp-headline-icon { font-size: 18px; flex-shrink: 0; margin-top: 1px; }
 .cp-headline-text {
 font-size: 16px;
 line-height: 1.45;
 color: var(--pw-ink);
 font-weight: 500;
 letter-spacing: -0.005em;
 display: -webkit-box;
 -webkit-line-clamp: 2;
 -webkit-box-orient: vertical;
 overflow: hidden;
 }

 /* Storytelling: BECAUSE card */
 .cp-because-card {
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 12px 14px;
 margin: 0 0 14px 0;
 }
 .cp-because-head {
 font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
 text-transform: uppercase; color: var(--pw-muted);
 margin-bottom: 8px;
 }
 .cp-because-row {
 display: flex; align-items: flex-start; gap: 8px;
 padding: 4px 0;
 font-size: 13px; color: var(--pw-ink);
 }
 .cp-because-num {
 display: inline-flex; align-items: center; justify-content: center;
 width: 18px; height: 18px;
 background: var(--pw-accent); color: white;
 border-radius: 50%;
 font-size: 10px; font-weight: 700; flex-shrink: 0;
 margin-top: 1px;
 }
 .cp-because-text { line-height: 1.45; }

 /* Storytelling: CONFIDENCE_BREAKDOWN (3 bars) */
 .cp-conf3-card {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 10px 14px;
 margin: 0 0 14px 0;
 display: flex; flex-direction: column; gap: 6px;
 }
 .cp-conf3-row { display: flex; align-items: center; gap: 10px; font-size: 11px; }
 .cp-conf3-label { width: 90px; color: var(--pw-muted); }
 .cp-conf3-bar { flex: 1; height: 6px; background: var(--pw-bg-alt); border-radius: 0; overflow: hidden; }
 .cp-conf3-fill { height: 100%; border-radius: 0; transition: width .3s; }
 .cp-conf3-val { width: 36px; text-align: right; font-weight: 700; color: var(--pw-ink); font-variant-numeric: tabular-nums; }

 /* Storytelling: ACTION buttons */
 .cp-actions-row {
 display: flex; flex-wrap: wrap; gap: 8px;
 margin: 14px 0;
 }
 .cp-action-btn {
 display: inline-flex; align-items: center; gap: 6px;
 padding: 6px 14px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 font-size: 12px; font-weight: 600;
 color: var(--pw-accent);
 cursor: pointer;
 transition: all .15s;
 font-family: inherit;
 }
 .cp-action-btn:hover {
 background: var(--pw-accent);
 color: white;
 border-color: var(--pw-accent);
 transform: translateY(-1px);
 box-shadow: 0 2px 6px rgba(201,99,66,0.20);
 }
 .cp-action-icon { font-size: 13px; }

 /* Storytelling: CAVEAT strip */
 .cp-caveat-strip {
 display: flex; align-items: flex-start; gap: 8px;
 padding: 8px 12px;
 background: rgba(245,158,11,0.06);
 border-left: 3px solid #f59e0b;
 border-radius: 0;
 margin: 14px 0 0 0;
 font-size: 11px;
 font-style: italic;
 color: var(--pw-muted);
 }
 .cp-caveat-icon { font-size: 13px; }

 /* Anomaly cell highlight (DATA tab) */
 :global(.cell-anomaly) {
 background: rgba(220,38,38,0.10);
 color: #b91c1c;
 font-weight: 700;
 padding: 1px 4px;
 border-radius: 0;
 cursor: help;
 }
 :global(.cell-anomaly[data-severity="warn"]) {
 background: rgba(245,158,11,0.10);
 color: #b87100;
 }
</style>
