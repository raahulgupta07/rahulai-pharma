<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Row = {
 run_id: string;
 project?: string;
 user?: string;
 agent?: string;
 trigger_kind?: string;
 intent?: string;
 created_at?: string;
 };

 let days = $state(7);
 let projectFilter = $state('');
 let triggerFilter = $state('');
 let loading = $state(true);
 let error = $state<string | null>(null);
 let rows = $state<Row[]>([]);

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 loading = true;
 error = null;
 try {
 const r = await fetch(`/api/memory/run-context/audit?days=${days}&limit=200`, { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.audit || [];
 rows = list.map((x: any) => ({
 run_id: x.run_id || x.id || '—',
 project: x.project || x.project_slug || '—',
 user: x.user || x.user_id || x.username || '—',
 agent: x.agent || x.agent_id || '—',
 trigger_kind: x.trigger_kind || x.trigger || x.source || '—',
 intent: x.intent || x.purpose || '—',
 created_at: x.created_at || x.timestamp || ''
 }));
 } catch (e: any) {
 error = e?.message || 'Failed to load';
 rows = [];
 } finally {
 loading = false;
 }
 }

 const projects = $derived(Array.from(new Set(rows.map((r) => r.project || ''))).filter(Boolean).sort());
 const triggers = $derived(Array.from(new Set(rows.map((r) => r.trigger_kind || ''))).filter(Boolean).sort());

 const filtered = $derived(rows.filter((r) =>
 (!projectFilter || r.project === projectFilter) &&
 (!triggerFilter || r.trigger_kind === triggerFilter)
 ));

 function triggerColor(t?: string): string {
 switch ((t || '').toLowerCase()) {
 case 'chat': return '#3b82f6';
 case 'schedule': return '#8b5cf6';
 case 'workflow': return '#f59e0b';
 case 'channel': return '#10b981';
 case 'eval': return '#87837a';
 default: return '#87837a';
 }
 }
 function fmtTime(s?: string) {
 if (!s) return '—';
 try { return new Date(s).toLocaleString(); } catch { return s; }
 }

 function setDays(d: number) { days = d; load(); }

 onMount(load);
</script>

<p class="muted">Recent run context audit. Source: <code>/api/memory/run-context/audit</code>.</p>

<div class="toolbar">
  <span class="muted-sm">Days:</span>
  {#each [1, 7, 14, 30, 90] as d}
    <button class="chip-btn" class:active={days === d} onclick={() => setDays(d)}>{d}</button>
  {/each}
  <label class="muted-sm" for="rp">Project:</label>
  <select id="rp" bind:value={projectFilter}>
    <option value="">All</option>
    {#each projects as p}<option value={p}>{p}</option>{/each}
  </select>
  <label class="muted-sm" for="rt">Trigger:</label>
  <select id="rt" bind:value={triggerFilter}>
    <option value="">All</option>
    {#each triggers as t}<option value={t}>{t}</option>{/each}
  </select>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error} — Wire endpoint to enable.</div>
{:else if filtered.length === 0}
  <div class="empty">No audit entries.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr><th>Run ID</th><th>Project</th><th>User</th><th>Agent</th><th>Trigger</th><th>Intent</th><th>Created</th></tr>
    </thead>
    <tbody>
      {#each filtered as r}
        <tr>
          <td class="mono">{r.run_id}</td>
          <td>{r.project}</td>
          <td>{r.user}</td>
          <td>{r.agent}</td>
          <td>
            <span class="chip" style="background: {triggerColor(r.trigger_kind)}1a; color: {triggerColor(r.trigger_kind)};">
              {r.trigger_kind}
            </span>
          </td>
          <td class="small">{r.intent}</td>
          <td class="small">{fmtTime(r.created_at)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
 .toolbar { display: flex; gap: 6px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
 .toolbar select {
 padding: 4px 8px;
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 background: var(--pw-surface, #faf9f5);
 font: 12px Inter, system-ui, sans-serif;
 color: var(--pw-ink, #2c2a26);
 }
 .chip-btn { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 4px 10px; font: 600 11px Inter, system-ui, sans-serif; cursor: pointer; color: var(--pw-ink-soft, #87837a); }
 .chip-btn.active { color: var(--pw-accent, #c96342); border-color: var(--pw-accent, #c96342); }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; margin-left: auto; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .chip { display: inline-block; border-radius: 0; padding: 2px 8px; font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
