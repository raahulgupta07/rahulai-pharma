<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';

 type Gate = {
 id: string | number;
 gate?: string;
 agent?: string;
 project?: string;
 expires_at?: string;
 created_at?: string;
 question?: string;
 [k: string]: any;
 };

 let rows = $state<Gate[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let busy = $state<Record<string, boolean>>({});
 let now = $state(Date.now());
 let pollId: ReturnType<typeof setInterval> | null = null;
 let tickId: ReturnType<typeof setInterval> | null = null;

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 try {
 const r = await fetch('/api/hitl/pending', { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.pending || j.gates || [];
 rows = list;
 error = null;
 } catch (e: any) {
 error = e?.message || 'Failed to load HITL gates';
 } finally {
 loading = false;
 }
 }

 async function decide(row: Gate, action: 'approve' | 'reject') {
 const key = String(row.id);
 busy[key] = true;
 try {
 const r = await fetch(`/api/hitl/${encodeURIComponent(key)}/${action}`, {
 method: 'POST',
 headers: { ...authHeaders(), 'Content-Type': 'application/json' }
 });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 await load();
 } catch (e: any) {
 alert(`${action} failed: ` + (e?.message || e));
 } finally {
 busy[key] = false;
 }
 }

 function countdown(expires?: string): string {
 if (!expires) return '—';
 try {
 const t = new Date(expires).getTime();
 const diff = t - now;
 if (diff <= 0) return 'EXPIRED';
 const sec = Math.floor(diff / 1000);
 const m = Math.floor(sec / 60);
 const s = sec % 60;
 return `${m}m ${s}s`;
 } catch {
 return expires;
 }
 }

 function fmtTime(iso?: string): string {
 if (!iso) return '—';
 try {
 return new Date(iso).toLocaleString();
 } catch {
 return iso;
 }
 }

 onMount(() => {
 load();
 pollId = setInterval(load, 15000);
 tickId = setInterval(() => (now = Date.now()), 1000);
 });

 onDestroy(() => {
 if (pollId) clearInterval(pollId);
 if (tickId) clearInterval(tickId);
 });
</script>

<p class="muted">Human-in-the-loop gates awaiting decision. Auto-refreshes every 15s.</p>

<div class="toolbar">
  <span class="chip">PENDING {rows.length}</span>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading HITL gates…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if rows.length === 0}
  <div class="empty">No pending gates.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr>
        <th>ID</th>
        <th>Gate</th>
        <th>Agent</th>
        <th>Project</th>
        <th>Question</th>
        <th>Expires</th>
        <th class="ra">Actions</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as r (r.id)}
        {@const cd = countdown(r.expires_at)}
        <tr>
          <td class="mono">{r.id}</td>
          <td>{r.gate || r.kind || '—'}</td>
          <td>{r.agent || '—'}</td>
          <td>{r.project || r.project_slug || '—'}</td>
          <td class="small">{(r.question || r.prompt || '—').toString().slice(0, 60)}</td>
          <td class="mono small" class:expired={cd === 'EXPIRED'}>{cd}</td>
          <td class="ra">
            <button class="btn ok" disabled={busy[String(r.id)]} onclick={() => decide(r, 'approve')}>Approve</button>
            <button class="btn no" disabled={busy[String(r.id)]} onclick={() => decide(r, 'reject')}>Reject</button>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
 .chip { display: inline-block; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; padding: 2px 8px; font: 600 10px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-ink, #2c2a26); }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .ra { text-align: right; }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .expired { color: #ef4444; font-weight: 600; }
 .btn { padding: 4px 10px; border-radius: 0; border: 1px solid var(--pw-border, #e7e3da); background: var(--pw-surface, #faf9f5); cursor: pointer; font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.04em; margin-left: 4px; }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn.ok { color: #10b981; border-color: #10b981; }
 .btn.no { color: #ef4444; border-color: #ef4444; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
