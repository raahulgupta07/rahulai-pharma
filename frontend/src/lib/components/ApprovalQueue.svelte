<script lang="ts">
  /**
   * ApprovalQueue.svelte
   *
   * Generalized approval queue widget. Polls /api/approvals/pending every
   * 30s. Color-codes action_type, shows signature progress, and exposes
   * APPROVE / REJECT actions.
   *
   * Not auto-mounted anywhere — callers drop it where needed (Phase 8 will
   * unify into Settings + Command Center).
   */
  import { onMount, onDestroy } from 'svelte';

  let { projectSlug = undefined as string | undefined,
        compact = false } = $props();

  type Sig = {
    approver_id: number;
    decision: 'approve' | 'reject';
    reason?: string | null;
    signed_at?: string;
  };
  type Req = {
    id: string;
    project_slug?: string | null;
    action_type: string;
    resource_id?: string | null;
    payload?: any;
    requested_by: number;
    required_approvers: number;
    allowed_roles?: string[];
    status: string;
    created_at?: string;
    expires_at?: string;
    signatures?: Sig[];
    signatures_collected?: number;
  };

  let requests = $state<Req[]>([]);
  let loading = $state<boolean>(false);
  let error = $state<string>('');
  let expanded = $state<Record<string, boolean>>({});
  let rejectFor = $state<string>('');
  let rejectReason = $state<string>('');

  function authHeaders(): Record<string, string> {
    if (typeof localStorage === 'undefined') return {};
    const t = localStorage.getItem('dash_token');
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  function actionColor(action: string): string {
    const a = (action || '').toLowerCase();
    if (a.includes('delete') || a.includes('drop') || a.includes('purge')) return '#c96342'; // coral
    if (a.includes('apply') || a.includes('promote') || a.includes('publish')) return '#3a8dff'; // blue
    if (a.includes('launch') || a.includes('schedule') || a.includes('run')) return '#f59e0b'; // orange
    return '#6b6862';
  }

  function relTime(iso?: string | null): string {
    if (!iso) return '';
    try {
      const ts = new Date(iso).getTime();
      const diff = (Date.now() - ts) / 1000;
      if (diff < 60) return `${Math.floor(diff)}s ago`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      return `${Math.floor(diff / 86400)}d ago`;
    } catch {
      return '';
    }
  }

  async function refresh() {
    loading = true;
    error = '';
    try {
      const qs = new URLSearchParams();
      if (projectSlug) qs.set('project_slug', projectSlug);
      qs.set('limit', '50');
      const r = await fetch(`/api/approvals/pending?${qs.toString()}`, {
        headers: authHeaders(),
      });
      if (!r.ok) {
        error = `HTTP ${r.status}`;
        loading = false;
        return;
      }
      const data = await r.json();
      requests = (data?.requests || []) as Req[];
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function approve(req: Req) {
    // Optimistic UI: bump local count.
    const prev = req.signatures_collected || 0;
    req.signatures_collected = prev + 1;
    requests = [...requests];
    try {
      const r = await fetch(`/api/approvals/${req.id}/sign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ decision: 'approve' }),
      });
      if (!r.ok) {
        const txt = await r.text();
        error = `approve failed: ${txt}`;
        req.signatures_collected = prev;
      }
    } catch (e: any) {
      error = e?.message || String(e);
      req.signatures_collected = prev;
    } finally {
      await refresh();
    }
  }

  function openReject(req: Req) {
    rejectFor = req.id;
    rejectReason = '';
  }

  function cancelReject() {
    rejectFor = '';
    rejectReason = '';
  }

  async function confirmReject() {
    const id = rejectFor;
    if (!id) return;
    if (!rejectReason.trim()) {
      error = 'reason required to reject';
      return;
    }
    try {
      const r = await fetch(`/api/approvals/${id}/sign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ decision: 'reject', reason: rejectReason }),
      });
      if (!r.ok) {
        error = `reject failed: ${await r.text()}`;
      }
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      cancelReject();
      await refresh();
    }
  }

  function toggle(id: string) {
    expanded = { ...expanded, [id]: !expanded[id] };
  }

  let pollHandle: ReturnType<typeof setInterval> | undefined;
  onMount(() => {
    refresh();
    pollHandle = setInterval(refresh, 30_000);
  });
  onDestroy(() => {
    if (pollHandle) clearInterval(pollHandle);
  });
</script>

<div class="aq-root" class:aq-compact={compact}>
  <div class="aq-head">
    <div class="aq-title">APPROVAL QUEUE</div>
    <div class="aq-meta">
      {#if loading}<span class="aq-dim">loading...</span>{/if}
      <span class="aq-count">{requests.length}</span>
      <button type="button" class="aq-refresh" onclick={refresh} aria-label="Refresh">↻</button>
    </div>
  </div>

  {#if error}
    <div class="aq-error">{error}</div>
  {/if}

  {#if !loading && requests.length === 0}
    <div class="aq-empty">
      <div class="aq-ghost-icon" aria-hidden="true">○</div>
      <div>No pending approvals</div>
    </div>
  {:else}
    <div class="aq-list" role="list">
      {#each requests as req (req.id)}
        {@const collected = req.signatures_collected ?? 0}
        {@const required = req.required_approvers ?? 1}
        {@const pct = Math.min(100, Math.round((collected / Math.max(1, required)) * 100))}
        {@const color = actionColor(req.action_type)}
        <div class="aq-row" role="listitem">
          <div class="aq-row-head">
            <span class="aq-badge" style="background:{color}1A; color:{color}; border-color:{color}55;">
              {req.action_type}
            </span>
            <span class="aq-resource" title={req.resource_id || ''}>
              {req.resource_id || '—'}
            </span>
            <span class="aq-requester aq-dim">by #{req.requested_by}</span>
            <span class="aq-time aq-dim">{relTime(req.created_at)}</span>
            <span class="aq-progress">
              <span class="aq-progress-bar"><span class="aq-progress-fill" style="width:{pct}%; background:{color};"></span></span>
              <span class="aq-progress-label">[{collected}/{required}]</span>
            </span>
            <span class="aq-actions">
              <button type="button" class="aq-btn aq-btn-primary" onclick={() => approve(req)}>APPROVE</button>
              <button type="button" class="aq-btn aq-btn-ghost" onclick={() => openReject(req)}>REJECT</button>
              <button type="button" class="aq-btn aq-btn-link" onclick={() => toggle(req.id)} aria-expanded={!!expanded[req.id]}>
                {expanded[req.id] ? '−' : '+'}
              </button>
            </span>
          </div>
          {#if expanded[req.id]}
            <div class="aq-row-body">
              <div class="aq-meta-grid">
                <div><span class="aq-dim">id</span> {req.id}</div>
                <div><span class="aq-dim">project</span> {req.project_slug || '—'}</div>
                <div><span class="aq-dim">expires</span> {relTime(req.expires_at) || req.expires_at}</div>
                <div><span class="aq-dim">roles</span> {(req.allowed_roles || []).join(', ')}</div>
              </div>
              <pre class="aq-payload">{JSON.stringify(req.payload || {}, null, 2)}</pre>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if rejectFor}
    <div class="aq-modal-back" role="presentation" onclick={cancelReject}>
      <div class="aq-modal" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
        <div class="aq-modal-title">Reason for rejection</div>
        <textarea class="aq-modal-input" rows="4" bind:value={rejectReason}
                  placeholder="Required: explain why this is being rejected"></textarea>
        <div class="aq-modal-row">
          <button type="button" class="aq-btn aq-btn-ghost" onclick={cancelReject}>CANCEL</button>
          <button type="button" class="aq-btn aq-btn-primary" onclick={confirmReject}
                  disabled={!rejectReason.trim()}>REJECT</button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  /* Match Dash design tokens used in command-center / settings rails. */
  .aq-root {
    font-family: inherit;
    font-size: 11px;
    color: var(--pw-ink, #2c2a26);
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    border-radius: 0;
    padding: 10px 12px;
  }
  .aq-compact { font-size: 11px; padding: 6px 8px; }
  .aq-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .aq-title {
    font-size: 11px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--pw-ink-soft, #6b6862);
    font-weight: 600;
  }
  .aq-meta { display: flex; gap: 8px; align-items: center; }
  .aq-count {
    background: var(--pw-bg-alt, #f3eee6);
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    padding: 1px 6px;
    border-radius: 0;
    font-size: 11px;
  }
  .aq-refresh {
    background: transparent;
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    color: var(--pw-ink, #2c2a26);
    border-radius: 0;
    padding: 1px 6px;
    cursor: pointer;
    font-size: 11px;
  }
  .aq-refresh:hover { background: var(--pw-bg-alt, #f3eee6); }
  .aq-dim { color: var(--pw-ink-soft, #6b6862); }
  .aq-error {
    background: rgba(201,99,66,0.1);
    color: var(--pw-accent, #c96342);
    border: 1px solid rgba(201,99,66,0.3);
    padding: 4px 8px;
    border-radius: 0;
    margin-bottom: 6px;
  }
  .aq-empty {
    display: flex; flex-direction: column; align-items: center;
    gap: 4px;
    padding: 24px 0;
    color: var(--pw-ink-soft, #6b6862);
  }
  .aq-ghost-icon {
    font-size: 18px;
    opacity: 0.4;
    line-height: 1;
  }
  .aq-list { display: flex; flex-direction: column; gap: 4px; }
  .aq-row {
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    border-radius: 0;
    background: var(--pw-bg-alt, #faf7f2);
  }
  .aq-row-head {
    display: grid;
    grid-template-columns: auto 1fr auto auto auto auto;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
  }
  .aq-badge {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid transparent;
  }
  .aq-resource {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .aq-requester { font-size: 11px; }
  .aq-time { font-size: 11px; }
  .aq-progress {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    min-width: 80px;
  }
  .aq-progress-bar {
    width: 50px;
    height: 4px;
    background: var(--pw-border, rgba(0,0,0,0.08));
    border-radius: 0;
    overflow: hidden;
  }
  .aq-progress-fill {
    display: block;
    height: 100%;
    background: var(--pw-accent, #c96342);
    transition: width 0.2s ease;
  }
  .aq-progress-label {
    font-size: 11px;
    color: var(--pw-ink-soft, #6b6862);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .aq-actions { display: inline-flex; gap: 4px; }
  .aq-btn {
    font-size: 11px;
    border-radius: 0;
    padding: 3px 8px;
    cursor: pointer;
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    background: var(--pw-surface, #fff);
    color: var(--pw-ink, #2c2a26);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .aq-btn:hover { background: var(--pw-bg-alt, #f3eee6); }
  .aq-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .aq-btn-primary {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }
  .aq-btn-primary:hover { background: #b55436; }
  .aq-btn-ghost {
    background: transparent;
  }
  .aq-btn-link {
    background: transparent;
    border: none;
    padding: 0 4px;
    font-size: 11px;
    line-height: 1;
  }

  .aq-row-body {
    border-top: 1px dashed var(--pw-border, rgba(0,0,0,0.08));
    padding: 6px 8px;
  }
  .aq-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 4px;
    margin-bottom: 6px;
    font-size: 11px;
  }
  .aq-payload {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10.5px;
    background: var(--pw-bg, #fff);
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    padding: 6px 8px;
    border-radius: 0;
    max-height: 200px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }

  .aq-modal-back {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
  }
  .aq-modal {
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    border-radius: 0;
    padding: 14px 16px;
    width: 420px;
    max-width: 90vw;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
  }
  .aq-modal-title {
    font-size: 11px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .aq-modal-input {
    width: 100%;
    border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
    border-radius: 0;
    padding: 6px 8px;
    font-family: inherit;
    font-size: 11px;
    color: var(--pw-ink, #2c2a26);
    background: var(--pw-bg-alt, #faf7f2);
    resize: vertical;
  }
  .aq-modal-row {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    margin-top: 8px;
  }
</style>
