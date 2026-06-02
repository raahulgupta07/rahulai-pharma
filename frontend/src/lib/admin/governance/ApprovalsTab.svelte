<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';

 type Approval = {
 id: string | number;
 action?: string;
 requester?: string;
 project?: string;
 requested_at?: string;
 payload?: any;
 risk?: string;
 [k: string]: any;
 };

 let rows = $state<Approval[]>([]);
 let loading = $state(true);
 let error = $state<string | null>(null);
 let busy = $state<Record<string, boolean>>({});
 let rejectTarget = $state<Approval | null>(null);
 let rejectReason = $state('');
 let pollId: ReturnType<typeof setInterval> | null = null;

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 async function load() {
 try {
 const r = await fetch('/api/approvals/pending', { headers: authHeaders() });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.pending || j.approvals || [];
 rows = list;
 error = null;
 } catch (e: any) {
 error = e?.message || 'Failed to load approvals';
 } finally {
 loading = false;
 }
 }

 async function approve(row: Approval) {
 const key = String(row.id);
 busy[key] = true;
 try {
 const r = await fetch(`/api/approvals/${encodeURIComponent(key)}/approve`, {
 method: 'POST',
 headers: { ...authHeaders(), 'Content-Type': 'application/json' }
 });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 await load();
 } catch (e: any) {
 alert('Approve failed: ' + (e?.message || e));
 } finally {
 busy[key] = false;
 }
 }

 function openReject(row: Approval) {
 rejectTarget = row;
 rejectReason = '';
 }

 async function confirmReject() {
 if (!rejectTarget) return;
 const key = String(rejectTarget.id);
 busy[key] = true;
 try {
 const r = await fetch(`/api/approvals/${encodeURIComponent(key)}/reject`, {
 method: 'POST',
 headers: { ...authHeaders(), 'Content-Type': 'application/json' },
 body: JSON.stringify({ reason: rejectReason })
 });
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 rejectTarget = null;
 rejectReason = '';
 await load();
 } catch (e: any) {
 alert('Reject failed: ' + (e?.message || e));
 } finally {
 busy[key] = false;
 }
 }

 function fmtTime(iso?: string): string {
 if (!iso) return '—';
 try {
 const d = new Date(iso);
 return d.toLocaleString();
 } catch {
 return iso;
 }
 }

 function summarize(p: any): string {
 if (p == null) return '—';
 if (typeof p === 'string') return p.slice(0, 80);
 try {
 const s = JSON.stringify(p);
 return s.length > 80 ? s.slice(0, 80) + '…' : s;
 } catch {
 return String(p);
 }
 }

 onMount(() => {
 load();
 pollId = setInterval(load, 30000);
 });

 onDestroy(() => {
 if (pollId) clearInterval(pollId);
 });
</script>

<p class="muted">Pending approval requests requiring admin sign-off. Auto-refreshes every 30s.</p>

<div class="toolbar">
  <span class="chip">PENDING {rows.length}</span>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading approvals…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if rows.length === 0}
  <div class="empty">No pending approvals.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr>
        <th>ID</th>
        <th>Action</th>
        <th>Requester</th>
        <th>Project</th>
        <th>Payload</th>
        <th>Requested</th>
        <th class="ra">Actions</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as r (r.id)}
        <tr>
          <td class="mono">{r.id}</td>
          <td>{r.action || r.kind || '—'}</td>
          <td>{r.requester || r.user || '—'}</td>
          <td>{r.project || r.project_slug || '—'}</td>
          <td class="mono small">{summarize(r.payload || r.body || r.detail)}</td>
          <td class="small">{fmtTime(r.requested_at || r.created_at)}</td>
          <td class="ra">
            <button
              class="btn ok"
              disabled={busy[String(r.id)]}
              onclick={() => approve(r)}
            >Approve</button>
            <button
              class="btn no"
              disabled={busy[String(r.id)]}
              onclick={() => openReject(r)}
            >Reject</button>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

{#if rejectTarget}
  <div class="modal-bg" onclick={() => (rejectTarget = null)} role="presentation">
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog">
      <header><strong>Reject approval {rejectTarget.id}</strong></header>
      <p class="muted">Reason will be recorded in audit log.</p>
      <textarea
        bind:value={rejectReason}
        placeholder="Enter rejection reason…"
        rows="4"
      ></textarea>
      <div class="modal-actions">
        <button class="btn" onclick={() => (rejectTarget = null)}>Cancel</button>
        <button class="btn no" disabled={!rejectReason.trim()} onclick={confirmReject}>
          Reject
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
 .muted {
 color: var(--pw-ink-soft, #87837a);
 font-size: 12px;
 margin: 0 0 12px;
 }
 .toolbar {
 display: flex;
 gap: 12px;
 align-items: center;
 margin-bottom: 12px;
 }
 .chip {
 display: inline-block;
 background: var(--pw-bg-alt, #f1ede4);
 border-radius: 0;
 padding: 2px 8px;
 font: 600 10px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink, #2c2a26);
 }
 .link {
 background: none;
 border: none;
 color: var(--pw-accent, #c96342);
 cursor: pointer;
 font: 12px Inter, system-ui, sans-serif;
 }
 .tbl {
 width: 100%;
 border-collapse: collapse;
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 overflow: hidden;
 font-size: 13px;
 }
 .tbl th,
 .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th {
 background: var(--pw-bg-alt, #f1ede4);
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.05em;
 color: var(--pw-ink-soft, #87837a);
 }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .ra { text-align: right; }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .btn {
 padding: 4px 10px;
 border-radius: 0;
 border: 1px solid var(--pw-border, #e7e3da);
 background: var(--pw-surface, #faf9f5);
 cursor: pointer;
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 margin-left: 4px;
 }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn.ok { color: #10b981; border-color: #10b981; }
 .btn.no { color: #ef4444; border-color: #ef4444; }
 .empty {
 padding: 40px;
 text-align: center;
 color: var(--pw-ink-soft, #87837a);
 background: var(--pw-surface, #faf9f5);
 border: 1px dashed var(--pw-border, #e7e3da);
 border-radius: 0;
 }
 .empty.err { color: #ef4444; }
 .modal-bg {
 position: fixed;
 inset: 0;
 background: rgba(0, 0, 0, 0.4);
 display: flex;
 align-items: center;
 justify-content: center;
 z-index: 999;
 }
 .modal {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 20px;
 width: 90%;
 max-width: 480px;
 }
 .modal header { margin-bottom: 8px; }
 .modal textarea {
 width: 100%;
 padding: 8px;
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 background: var(--pw-bg-alt, #f1ede4);
 color: var(--pw-ink, #2c2a26);
 font: 13px Inter, system-ui, sans-serif;
 resize: vertical;
 }
 .modal-actions {
 display: flex;
 justify-content: flex-end;
 gap: 8px;
 margin-top: 12px;
 }
</style>
