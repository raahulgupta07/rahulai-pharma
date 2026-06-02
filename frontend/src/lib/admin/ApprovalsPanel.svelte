<script lang="ts">
  import { onMount } from 'svelte';

  type PendingRow = {
    request_id: string | number;
    action_name?: string;
    name?: string;
    url?: string;
    url_template?: string;
    method?: string;
    body?: any;
    body_preview?: string;
    headers?: any;
    requested_by?: string;
    user?: string;
    reason?: string;
    requested_at?: string;
    created_at?: string;
  };

  type AuditRow = {
    request_id?: string | number;
    action_name?: string;
    name?: string;
    status?: string; // executed | rejected | approved
    url?: string;
    method?: string;
    requested_by?: string;
    reason?: string;
    executed_at?: string;
    rejected_at?: string;
    ts?: string;
  };

  let projectId = $state<string>('');
  let view = $state<'pending' | 'audit'>('pending');
  let pending = $state<PendingRow[]>([]);
  let audit = $state<AuditRow[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let unavailable = $state(false);

  // Reject modal state
  let rejectId = $state<string | number | null>(null);
  let rejectReason = $state('');
  let rejectBusy = $state(false);

  function authHeaders(): Record<string, string> {
    const tok = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    const h: Record<string, string> = { 'Accept': 'application/json' };
    if (tok) h['Authorization'] = `Bearer ${tok}`;
    return h;
  }

  function qs(): string {
    return projectId.trim() ? `?project_id=${encodeURIComponent(projectId.trim())}` : '';
  }

  async function loadPending() {
    loading = true;
    error = null;
    unavailable = false;
    try {
      const r = await fetch(`/api/actions/pending${qs()}`, { headers: authHeaders() });
      if (r.status === 503) { unavailable = true; pending = []; return; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      pending = Array.isArray(j) ? j : (j.items || j.pending || j.requests || []);
    } catch (e: any) {
      error = e?.message || String(e);
      pending = [];
    } finally {
      loading = false;
    }
  }

  async function loadAudit() {
    loading = true;
    error = null;
    unavailable = false;
    try {
      const r = await fetch(`/api/actions/audit${qs() ? qs() + '&days=30' : '?days=30'}`, { headers: authHeaders() });
      if (r.status === 503) { unavailable = true; audit = []; return; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      audit = Array.isArray(j) ? j : (j.items || j.audit || j.events || []);
    } catch (e: any) {
      error = e?.message || String(e);
      audit = [];
    } finally {
      loading = false;
    }
  }

  async function refresh() {
    if (view === 'pending') await loadPending();
    else await loadAudit();
  }

  async function approve(id: string | number) {
    try {
      const r = await fetch(`/api/actions/pending/${encodeURIComponent(String(id))}/approve`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: '{}'
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadPending();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  function openReject(id: string | number) {
    rejectId = id;
    rejectReason = '';
  }

  async function submitReject() {
    if (rejectId == null) return;
    rejectBusy = true;
    try {
      const r = await fetch(`/api/actions/pending/${encodeURIComponent(String(rejectId))}/reject`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: rejectReason })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      rejectId = null;
      rejectReason = '';
      await loadPending();
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      rejectBusy = false;
    }
  }

  function cancelReject() {
    rejectId = null;
    rejectReason = '';
  }

  function fmtBody(b: any): string {
    if (b == null) return '';
    try {
      const s = typeof b === 'string' ? b : JSON.stringify(b, null, 2);
      return s.length > 200 ? s.slice(0, 200) + '…' : s;
    } catch {
      return String(b).slice(0, 200);
    }
  }

  function fmtUrl(u: string | undefined): string {
    if (!u) return '—';
    return u.length > 60 ? u.slice(0, 60) + '…' : u;
  }

  function fmtWhen(s: string | undefined): string {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }

  onMount(loadPending);
</script>

<div class="ap-shell">
  <header class="ap-head">
    <div>
      <h1>Approvals Inbox</h1>
      <p class="muted">Review and approve pending action requests</p>
    </div>
    <div class="ctrls">
      <input
        type="text"
        placeholder="Project ID (optional)"
        bind:value={projectId}
        onkeydown={(e) => { if (e.key === 'Enter') refresh(); }}
      />
      <div class="seg">
        <button
          class:active={view === 'pending'}
          onclick={() => { view = 'pending'; loadPending(); }}
        >Pending</button>
        <button
          class:active={view === 'audit'}
          onclick={() => { view = 'audit'; loadAudit(); }}
        >Audit (30d)</button>
      </div>
      <button class="refresh" onclick={refresh} disabled={loading}>{loading ? '…' : 'Refresh'}</button>
    </div>
  </header>

  {#if unavailable}
    <div class="warn">Action registry not yet migrated — apply migration first.</div>
  {:else if error}
    <p class="err">Failed: {error}</p>
  {/if}

  {#if view === 'pending'}
    {#if loading && !pending.length}
      <p class="muted">Loading…</p>
    {:else if !loading && pending.length === 0 && !unavailable}
      <div class="empty">No pending approvals</div>
    {:else if pending.length}
      <section class="card">
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>URL</th>
              <th>Body</th>
              <th>Requested by</th>
              <th>Reason</th>
              <th>When</th>
              <th class="ta-r">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#each pending as p (p.request_id)}
              <tr>
                <td><strong>{p.action_name || p.name || '—'}</strong></td>
                <td><code title={p.url || p.url_template || ''}>{fmtUrl(p.url || p.url_template)}</code></td>
                <td><pre class="body">{fmtBody(p.body ?? p.body_preview)}</pre></td>
                <td>{p.requested_by || p.user || '—'}</td>
                <td class="reason">{p.reason || '—'}</td>
                <td>{fmtWhen(p.requested_at || p.created_at)}</td>
                <td class="ta-r">
                  {#if rejectId === p.request_id}
                    <div class="reject-inline">
                      <input
                        type="text"
                        placeholder="Reason for rejection"
                        bind:value={rejectReason}
                        disabled={rejectBusy}
                        onkeydown={(e) => { if (e.key === 'Enter') submitReject(); if (e.key === 'Escape') cancelReject(); }}
                      />
                      <button class="btn-reject" onclick={submitReject} disabled={rejectBusy || !rejectReason.trim()}>{rejectBusy ? '…' : 'Confirm'}</button>
                      <button class="btn-cancel" onclick={cancelReject} disabled={rejectBusy}>Cancel</button>
                    </div>
                  {:else}
                    <button class="btn-approve" onclick={() => approve(p.request_id)}>Approve</button>
                    <button class="btn-reject" onclick={() => openReject(p.request_id)}>Reject</button>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {:else}
    {#if loading && !audit.length}
      <p class="muted">Loading…</p>
    {:else if !loading && audit.length === 0 && !unavailable}
      <div class="empty">No audit events in the last 30 days</div>
    {:else if audit.length}
      <section class="card">
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Status</th>
              <th>URL</th>
              <th>Requested by</th>
              <th>Reason</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {#each audit as a, i (a.request_id ?? i)}
              <tr>
                <td><strong>{a.action_name || a.name || '—'}</strong></td>
                <td>
                  <span class="pill" class:p-exec={a.status === 'executed' || a.status === 'approved'} class:p-rej={a.status === 'rejected'}>{a.status || '—'}</span>
                </td>
                <td><code title={a.url || ''}>{fmtUrl(a.url)}</code></td>
                <td>{a.requested_by || '—'}</td>
                <td class="reason">{a.reason || '—'}</td>
                <td>{fmtWhen(a.executed_at || a.rejected_at || a.ts)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</div>

<style>
  .ap-shell {
    padding: 24px 32px;
    max-width: 1200px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .ap-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .ap-head h1 {
    font-size: 22px;
    margin: 0 0 4px 0;
    font-weight: 600;
    font-family: 'Source Serif Pro', Georgia, serif;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .err { color: #b3261e; font-size: 13px; }
  .warn {
    background: #fff7e6;
    border: 1px solid #f0c674;
    color: #8a5a00;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 16px;
  }
  .ctrls { display: flex; gap: 8px; align-items: center; }
  .ctrls input[type="text"] {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    min-width: 180px;
  }
  .ctrls button {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  .ctrls button:hover { background: #f7f3e9; }
  .ctrls button:disabled { opacity: 0.5; cursor: default; }
  .seg { display: inline-flex; border: 1px solid #d6d1c2; border-radius: 4px; overflow: hidden; }
  .seg button { border: none; border-right: 1px solid #d6d1c2; border-radius: 0; }
  .seg button:last-child { border-right: none; }
  .seg button.active { background: #c96342; color: #fff; }
  .seg button.active:hover { background: #b85638; }

  .card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 0;
    overflow: hidden;
  }
  .empty {
    background: #fff;
    border: 1px dashed #d6d1c2;
    border-radius: 6px;
    padding: 32px;
    text-align: center;
    color: #777;
    font-size: 14px;
  }

  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
    vertical-align: top;
  }
  th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
    background: #faf7f0;
  }
  td code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
    font-family: ui-monospace, Menlo, monospace;
  }
  td pre.body {
    background: #f7f3e9;
    border-radius: 3px;
    padding: 6px 8px;
    margin: 0;
    font-size: 11px;
    font-family: ui-monospace, Menlo, monospace;
    max-width: 280px;
    max-height: 90px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
  td.reason { max-width: 200px; word-break: break-word; }
  td.ta-r, th.ta-r { text-align: right; }

  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
    background: #eee;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .pill.p-exec { background: #e6f4ea; color: #1e7e34; }
  .pill.p-rej { background: #fce8e6; color: #b3261e; }

  .btn-approve, .btn-reject, .btn-cancel {
    padding: 5px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
    margin-left: 4px;
  }
  .btn-approve { background: #c96342; color: #fff; border-color: #c96342; }
  .btn-approve:hover { background: #b85638; }
  .btn-reject:hover { background: #fce8e6; color: #b3261e; }
  .btn-cancel:hover { background: #f7f3e9; }

  .reject-inline {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .reject-inline input {
    padding: 5px 8px;
    border: 1px solid #c96342;
    border-radius: 4px;
    font-size: 12px;
    min-width: 180px;
  }
</style>
