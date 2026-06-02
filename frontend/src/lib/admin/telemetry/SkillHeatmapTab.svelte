<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';

 type Inv = {
 skill_id: string;
 name?: string;
 category?: string;
 invoked_at?: string;
 latency_ms?: number;
 };

 let days = $state(30);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let invocations = $state<Inv[]>([]);
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
 const r = await fetch(`/api/skills/invocations?days=${days}&limit=2000`, { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.invocations || [];
 invocations = list.map((x: any) => ({
 skill_id: x.skill_id || x.id || x.skill || '—',
 name: x.name || x.skill_name || x.skill_id || '—',
 category: x.category || x.skill_category || '—',
 invoked_at: x.invoked_at || x.created_at || x.timestamp,
 latency_ms: Number(x.latency_ms ?? x.duration_ms ?? 0)
 }));
 } catch (e: any) {
 error = e?.message || 'Failed to load';
 invocations = [];
 } finally {
 loading = false;
 }
 queueMicrotask(renderChart);
 }

 const grouped = $derived.by(() => {
 const m = new Map<string, { name: string; category: string; count: number; lastUsed: string; totalLat: number }>();
 for (const i of invocations) {
 const k = i.skill_id;
 const ex = m.get(k);
 if (ex) {
 ex.count++;
 ex.totalLat += i.latency_ms || 0;
 if (i.invoked_at && i.invoked_at > ex.lastUsed) ex.lastUsed = i.invoked_at;
 } else {
 m.set(k, {
 name: i.name || k,
 category: i.category || '—',
 count: 1,
 lastUsed: i.invoked_at || '',
 totalLat: i.latency_ms || 0
 });
 }
 }
 return Array.from(m.entries())
 .map(([id, v]) => ({ id, ...v, avgLat: v.count ? v.totalLat / v.count : 0 }))
 .sort((a, b) => b.count - a.count);
 });

 async function renderChart() {
 if (!chartEl) return;
 try {
 const echarts: any = await import('echarts');
 if (chart) { chart.dispose(); chart = null; }
 chart = echarts.init(chartEl);
 const top = grouped.slice(0, 10).reverse();
 chart.setOption({
 tooltip: { trigger: 'axis' },
 grid: { left: 140, right: 30, top: 20, bottom: 30 },
 xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
 yAxis: { type: 'category', data: top.map((t) => t.name), axisLabel: { fontSize: 11 } },
 series: [{
 type: 'bar',
 data: top.map((t) => t.count),
 itemStyle: { color: '#c96342' },
 label: { show: true, position: 'right', fontSize: 10 }
 }]
 });
 } catch (e) {
 console.error(e);
 }
 }

 function setDays(d: number) { days = d; load(); }
 function fmtTime(s?: string) {
 if (!s) return '—';
 try { return new Date(s).toLocaleString(); } catch { return s; }
 }

 onMount(load);
 onDestroy(() => { if (chart) chart.dispose(); });
</script>

<p class="muted">Skill invocation frequency. Source: <code>/api/skills/invocations</code>.</p>

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
{:else if grouped.length === 0}
  <div class="empty">No skill invocations.</div>
{:else}
  <div class="chart" bind:this={chartEl}></div>

  <table class="tbl">
    <thead>
      <tr><th>Name</th><th>Category</th><th class="ra">Invocations</th><th>Last Used</th><th class="ra">Avg Latency</th></tr>
    </thead>
    <tbody>
      {#each grouped as r}
        <tr>
          <td class="mono">{r.name}</td>
          <td>{r.category}</td>
          <td class="ra mono">{r.count}</td>
          <td class="small">{fmtTime(r.lastUsed)}</td>
          <td class="ra mono">{r.avgLat.toFixed(0)}ms</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
 .toolbar { display: flex; gap: 6px; align-items: center; margin-bottom: 16px; }
 .chip-btn { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 4px 10px; font: 600 11px Inter, system-ui, sans-serif; cursor: pointer; color: var(--pw-ink-soft, #87837a); }
 .chip-btn.active { color: var(--pw-accent, #c96342); border-color: var(--pw-accent, #c96342); }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; margin-left: auto; }
 .chart { width: 100%; height: 320px; margin-bottom: 20px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 8px; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .ra { text-align: right; }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
