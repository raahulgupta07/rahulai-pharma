<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Entry = {
 id?: string | number;
 kind?: string;
 decision?: string;
 hook?: string;
 project?: string;
 at?: string;
 reason?: string;
 [k: string]: any;
 };

 let rows = $state<Entry[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let notFound = $state(false);
 let days = $state(7);
 let kindFilter = $state('all');
 let decisionFilter = $state('all');

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 loading = true;
 notFound = false;
 try {
 const r = await fetch(`/api/hooks/audit?days=${days}`, { headers: authHeaders() });
 if (r.status === 404) {
 notFound = true;
 rows = [];
 error = null;
 return;
 }
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.entries || j.audit || [];
 rows = list;
 error = null;
 } catch (e: any) {
 error = e?.message || 'Failed to load hook audit';
 } finally {
 loading = false;
 }
 }

 const kinds = $derived(Array.from(new Set(rows.map((r) => r.kind || r.event || '').filter(Boolean))));
 const decisions = $derived(Array.from(new Set(rows.map((r) => r.decision || r.result || '').filter(Boolean))));

 const filtered = $derived(
 rows.filter((r) => {
 const k = r.kind || r.event || '';
 const d = r.decision || r.result || '';
 if (kindFilter !== 'all' && k !== kindFilter) return false;
 if (decisionFilter !== 'all' && d !== decisionFilter) return false;
 return true;
 })
 );

 function fmtTime(iso?: string): string {
 if (!iso) return '—';
 try {
 return new Date(iso).toLocaleString();
 } catch {
 return iso;
 }
 }

 function decisionClass(d?: string): string {
 const k = (d || '').toLowerCase();
 if (k === 'allow' || k === 'pass' || k === 'ok') return 'ok';
 if (k === 'deny' || k === 'block' || k === 'reject') return 'no';
 return 'info';
 }

 onMount(load);
</script>

<p class="muted">Hook audit log over the last {days} days. Shows decisions made by pre/post-execution hooks.</p>

<div class="toolbar">
  <span class="chip">ENTRIES {filtered.length}</span>
  <span class="muted-sm">Kind:</span>
  <button class="filter" class:active={kindFilter === 'all'} onclick={() => (kindFilter = 'all')}>All</button>
  {#each kinds as k}
    <button class="filter" class:active={kindFilter === k} onclick={() => (kindFilter = k)}>{k}</button>
  {/each}
  <span class="muted-sm">Decision:</span>
  <button class="filter" class:active={decisionFilter === 'all'} onclick={() => (decisionFilter = 'all')}>All</button>
  {#each decisions as d}
    <button class="filter" class:active={decisionFilter === d} onclick={() => (decisionFilter = d)}>{d}</button>
  {/each}
  <select bind:value={days} onchange={load} class="sel">
    <option value={1}>1 day</option>
    <option value={7}>7 days</option>
    <option value={14}>14 days</option>
    <option value={30}>30 days</option>
  </select>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading…</div>
{:else if notFound}
  <div class="empty">Hook audit endpoint not available on this deployment.</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if filtered.length === 0}
  <div class="empty">No hook audit entries.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr>
        <th>Kind</th>
        <th>Decision</th>
        <th>Hook</th>
        <th>Project</th>
        <th>Reason</th>
        <th>At</th>
      </tr>
    </thead>
    <tbody>
      {#each filtered as r, i (r.id ?? i)}
        <tr>
          <td>{r.kind || r.event || '—'}</td>
          <td><span class="pill {decisionClass(r.decision || r.result)}">{(r.decision || r.result || '—').toString().toUpperCase()}</span></td>
          <td class="mono">{r.hook || r.name || '—'}</td>
          <td>{r.project || r.project_slug || '—'}</td>
          <td class="small">{(r.reason || r.message || '—').toString().slice(0, 80)}</td>
          <td class="small">{fmtTime(r.at || r.created_at || r.ts)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin-left: 4px; }
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
 .pill { display: inline-block; border-radius: 0; padding: 2px 8px; font: 600 10px Inter, system-ui, sans-serif; letter-spacing: 0.04em; }
 .pill.ok { background: rgba(16, 185, 129, 0.15); color: #10b981; }
 .pill.no { background: rgba(239, 68, 68, 0.15); color: #ef4444; }
 .pill.info { background: var(--pw-bg-alt, #f1ede4); color: var(--pw-ink-soft, #87837a); }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
