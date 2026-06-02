<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Leak = {
 id?: string | number;
 pattern?: string;
 severity?: string;
 source?: string;
 project?: string;
 matched_at?: string;
 snippet?: string;
 [k: string]: any;
 };

 let rows = $state<Leak[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let days = $state(14);
 let severityFilter = $state<string>('all');

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 loading = true;
 try {
 const r = await fetch(`/api/evals/secret-leaks?days=${days}`, { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.leaks || j.results || [];
 rows = list;
 error = null;
 } catch (e: any) {
 error = e?.message || 'Failed to load secret leaks';
 } finally {
 loading = false;
 }
 }

 const filtered = $derived(
 severityFilter === 'all' ? rows : rows.filter((r) => (r.severity || '').toLowerCase() === severityFilter)
 );

 function sevColor(s?: string): string {
 const k = (s || '').toLowerCase();
 if (k === 'critical' || k === 'high') return 'sev-high';
 if (k === 'medium' || k === 'med') return 'sev-med';
 if (k === 'low') return 'sev-low';
 return 'sev-info';
 }

 function fmtTime(iso?: string): string {
 if (!iso) return '—';
 try {
 return new Date(iso).toLocaleString();
 } catch {
 return iso;
 }
 }

 onMount(load);
</script>

<p class="muted">Secret-leak detector scans over the last {days} days. Patterns matched against agent outputs.</p>

<div class="toolbar">
  <span class="chip">FOUND {filtered.length}</span>
  <button class="filter" class:active={severityFilter === 'all'} onclick={() => (severityFilter = 'all')}>All</button>
  <button class="filter" class:active={severityFilter === 'critical'} onclick={() => (severityFilter = 'critical')}>Critical</button>
  <button class="filter" class:active={severityFilter === 'high'} onclick={() => (severityFilter = 'high')}>High</button>
  <button class="filter" class:active={severityFilter === 'medium'} onclick={() => (severityFilter = 'medium')}>Medium</button>
  <button class="filter" class:active={severityFilter === 'low'} onclick={() => (severityFilter = 'low')}>Low</button>
  <select bind:value={days} onchange={load} class="sel">
    <option value={7}>7 days</option>
    <option value={14}>14 days</option>
    <option value={30}>30 days</option>
  </select>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if filtered.length === 0}
  <div class="empty">No secret leaks detected.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr>
        <th>Pattern</th>
        <th>Severity</th>
        <th>Source</th>
        <th>Project</th>
        <th>Snippet</th>
        <th>Matched</th>
      </tr>
    </thead>
    <tbody>
      {#each filtered as r, i (r.id ?? i)}
        <tr>
          <td class="mono">{r.pattern || r.rule || '—'}</td>
          <td><span class="sev {sevColor(r.severity)}">{(r.severity || 'info').toUpperCase()}</span></td>
          <td>{r.source || r.origin || '—'}</td>
          <td>{r.project || r.project_slug || '—'}</td>
          <td class="mono small">{(r.snippet || r.match || '—').toString().slice(0, 50)}</td>
          <td class="small">{fmtTime(r.matched_at || r.created_at)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
 .chip { display: inline-block; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; padding: 2px 8px; font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-ink, #2c2a26); }
 .filter { padding: 4px 10px; border-radius: 0; border: 1px solid var(--pw-border, #e7e3da); background: var(--pw-surface, #faf9f5); cursor: pointer; font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-ink-soft, #87837a); }
 .filter.active { background: var(--pw-accent, #c96342); color: white; border-color: var(--pw-accent, #c96342); }
 .sel { padding: 4px 8px; border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); font-size: 12px; }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .sev { display: inline-block; border-radius: 0; padding: 2px 8px; font: 600 10px Inter, system-ui, sans-serif; letter-spacing: 0.04em; }
 .sev-high { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
 .sev-med { background: rgba(245, 158, 11, 0.15); color: #f59e0b; }
 .sev-low { background: rgba(59, 130, 246, 0.15); color: #3b82f6; }
 .sev-info { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
