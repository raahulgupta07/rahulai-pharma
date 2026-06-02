<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';

 type Row = { day: string; project: string; model?: string; cost: number };

 let days = $state(30);
 let rows = $state<Row[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let chartEl: HTMLDivElement | null = null;
 let chart: any = null;

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 loading = true;
 error = null;
 try {
 const r = await fetch(`/api/admin/llm-costs?days=${days}`, { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.rows || j.costs || [];
 rows = list.map((x: any) => ({
 day: x.day || x.date || x.created_at?.slice(0, 10) || '',
 project: x.project || x.project_slug || x.tenant || '—',
 model: x.model || x.llm_model || '',
 cost: Number(x.cost ?? x.total_cost ?? x.amount ?? 0)
 }));
 } catch (e: any) {
 error = e?.message || 'Failed to load costs';
 rows = [];
 } finally {
 loading = false;
 }
 queueMicrotask(renderChart);
 }

 const total = $derived(rows.reduce((a, r) => a + r.cost, 0));

 const byProject = $derived.by(() => {
 const m = new Map<string, number>();
 for (const r of rows) m.set(r.project, (m.get(r.project) || 0) + r.cost);
 return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
 });

 const byModel = $derived.by(() => {
 const m = new Map<string, number>();
 for (const r of rows) if (r.model) m.set(r.model, (m.get(r.model) || 0) + r.cost);
 return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
 });

 const byDay = $derived.by(() => {
 const m = new Map<string, number>();
 for (const r of rows) m.set(r.day, (m.get(r.day) || 0) + r.cost);
 return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
 });

 const avgPerDay = $derived(byDay.length ? total / byDay.length : 0);
 const topProject = $derived(byProject[0]?.[0] || '—');
 const topModel = $derived(byModel[0]?.[0] || '—');

 const tableRows = $derived.by(() => {
 const lastDay = byDay.length ? byDay[byDay.length - 1][0] : null;
 return byProject.map(([project, sum]) => {
 const projectRows = rows.filter((r) => r.project === project);
 const dayMap = new Map<string, number>();
 for (const r of projectRows) dayMap.set(r.day, (dayMap.get(r.day) || 0) + r.cost);
 const last = lastDay ? dayMap.get(lastDay) || 0 : 0;
 const avg = dayMap.size ? sum / dayMap.size : 0;
 const pct = total > 0 ? (sum / total) * 100 : 0;
 return { project, total: sum, avg, last, pct };
 });
 });

 async function renderChart() {
 if (!chartEl) return;
 try {
 const echarts: any = await import('echarts');
 if (chart) { chart.dispose(); chart = null; }
 chart = echarts.init(chartEl);

 const days = byDay.map(([d]) => d);
 const topProjects = byProject.slice(0, 10).map(([p]) => p);
 const series = topProjects.map((p) => ({
 name: p,
 type: 'line',
 stack: 'total',
 areaStyle: {},
 symbol: 'none',
 data: days.map((d) => {
 const matches = rows.filter((r) => r.day === d && r.project === p);
 return matches.reduce((a, r) => a + r.cost, 0);
 })
 }));

 chart.setOption({
 tooltip: { trigger: 'axis' },
 legend: { data: topProjects, type: 'scroll', top: 0, textStyle: { fontSize: 11 } },
 grid: { left: 50, right: 20, top: 40, bottom: 30 },
 xAxis: { type: 'category', data: days, axisLabel: { fontSize: 10 } },
 yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => '$' + v.toFixed(2) } },
 series
 });
 } catch (e) {
 console.error('chart render failed', e);
 }
 }

 onMount(() => { load(); });
 onDestroy(() => { if (chart) chart.dispose(); });

 function setDays(d: number) { days = d; load(); }
 function fmtUsd(n: number) { return '$' + n.toFixed(2); }
</script>

<p class="muted">Daily LLM cost across projects & models. Source: <code>/api/admin/llm-costs</code>.</p>

<div class="toolbar">
  <span class="muted-sm">Range:</span>
  {#each [7, 14, 30, 90] as d}
    <button class="chip-btn" class:active={days === d} onclick={() => setDays(d)}>{d}d</button>
  {/each}
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error} — Wire endpoint to enable.</div>
{:else if rows.length === 0}
  <div class="empty">No cost data. Wire endpoint to enable.</div>
{:else}
  <div class="kpis">
    <div class="kpi"><div class="kpi-label">Total</div><div class="kpi-val">{fmtUsd(total)}</div></div>
    <div class="kpi"><div class="kpi-label">Top Project</div><div class="kpi-val sm">{topProject}</div></div>
    <div class="kpi"><div class="kpi-label">Top Model</div><div class="kpi-val sm">{topModel}</div></div>
    <div class="kpi"><div class="kpi-label">Avg / Day</div><div class="kpi-val">{fmtUsd(avgPerDay)}</div></div>
  </div>

  <div class="chart" bind:this={chartEl}></div>

  <table class="tbl">
    <thead>
      <tr><th>Project</th><th class="ra">Total</th><th class="ra">Avg/Day</th><th class="ra">Last Day</th><th class="ra">% of Total</th></tr>
    </thead>
    <tbody>
      {#each tableRows as r}
        <tr>
          <td>{r.project}</td>
          <td class="ra mono">{fmtUsd(r.total)}</td>
          <td class="ra mono">{fmtUsd(r.avg)}</td>
          <td class="ra mono">{fmtUsd(r.last)}</td>
          <td class="ra mono">{r.pct.toFixed(1)}%</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
 .toolbar { display: flex; gap: 6px; align-items: center; margin-bottom: 16px; }
 .chip-btn {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 4px 10px;
 font: 600 11px Inter, system-ui, sans-serif;
 cursor: pointer;
 color: var(--pw-ink-soft, #87837a);
 }
 .chip-btn.active { color: var(--pw-accent, #c96342); border-color: var(--pw-accent, #c96342); }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; margin-left: auto; }
 .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
 .kpi {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 14px 16px;
 }
 .kpi-label { font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); margin-bottom: 6px; }
 .kpi-val { font: 600 22px 'Source Serif 4', Georgia, serif; color: var(--pw-ink, #2c2a26); }
 .kpi-val.sm { font-size: 14px; word-break: break-word; }
 .chart { width: 100%; height: 320px; margin-bottom: 20px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 8px; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .ra { text-align: right; }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
