<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 onMount(load);

 type Refusal = {
 project: string;
 question?: string;
 reason?: string;
 classifier?: string;
 refusal_message?: string;
 user?: string;
 created_at?: string;
 [k: string]: any;
 };

 let rows = $state<Refusal[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let projectFilter = $state('all');
 let progress = $state({ done: 0, total: 0 });

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 loading = true;
 rows = [];
 try {
 const r = await fetch('/api/projects', { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status} (projects)`);
 const j = await r.json();
 const all = Array.isArray(j) ? j : j.items || j.projects || [];
 const first10 = all.slice(0, 10);
 progress = { done: 0, total: first10.length };

 const promises = first10.map(async (p: any) => {
 const slug = p.slug || p.project_slug || p.name;
 if (!slug) return [];
 try {
 const rr = await fetch(`/api/projects/${encodeURIComponent(slug)}/guardrail-audit`, { headers: authHeaders() });
 if (!rr.ok) return [];
 const jj = await rr.json();
 const list = Array.isArray(jj) ? jj : jj.items || jj.audit || jj.refusals || [];
 return list.map((x: any) => ({ ...x, project: slug }));
 } catch {
 return [];
 } finally {
 progress.done += 1;
 }
 });

 const settled = await Promise.all(promises);
 rows = settled.flat();
 error = null;
 } catch (e: any) {
 error = e?.message || 'Failed to load refusals';
 } finally {
 loading = false;
 }
 }

 const projects = $derived(Array.from(new Set(rows.map((r) => r.project).filter(Boolean))));
 const filtered = $derived(projectFilter === 'all' ? rows : rows.filter((r) => r.project === projectFilter));

 function fmtTime(iso?: string): string {
 if (!iso) return '—';
 try {
 return new Date(iso).toLocaleString();
 } catch {
 return iso;
 }
 }
</script>

<svelte:head><title>Refusals</title></svelte:head>

<p class="muted">Aggregated guardrail refusals from the first 10 projects. Shows off-topic / off-scope questions caught by the auto-scope guardrail.</p>

<div class="toolbar">
  <span class="chip">REFUSALS {filtered.length}</span>
  <span class="muted-sm">Project:</span>
  <button class="filter" class:active={projectFilter === 'all'} onclick={() => (projectFilter = 'all')}>All</button>
  {#each projects as p}
    <button class="filter" class:active={projectFilter === p} onclick={() => (projectFilter = p)}>{p}</button>
  {/each}
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading refusals… {progress.done}/{progress.total}</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if filtered.length === 0}
  <div class="empty">No refusals recorded.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr>
        <th>Project</th>
        <th>Question</th>
        <th>Reason</th>
        <th>Classifier</th>
        <th>User</th>
        <th>At</th>
      </tr>
    </thead>
    <tbody>
      {#each filtered as r, i (i)}
        <tr>
          <td class="mono">{r.project}</td>
          <td class="small">{(r.question || '—').toString().slice(0, 80)}</td>
          <td class="small">{(r.reason || '—').toString().slice(0, 60)}</td>
          <td>{r.classifier || r.source || '—'}</td>
          <td>{r.user || r.user_id || '—'}</td>
          <td class="small">{fmtTime(r.created_at || r.at)}</td>
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
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
