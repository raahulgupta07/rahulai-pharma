<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/stores';
 import { base } from '$app/paths';

 const slug = $derived($page.params.slug);

 let token = $state('');
 let loading = $state(false);
 let err = $state('');
 let info = $state('');

 let minions = $state<any[]>([]);
 let statusFilter = $state<string>('all');
 let kindFilter = $state<string>('');
 let limit = $state(50);

 let autoRefresh = $state(true);
 let timer: any = null;

 const KINDS = [
 'dedupe_entities',
 'recompile_stale_pages',
 'reembed_stale_chunks',
 'prune_old_evidence',
 ];

 function _h(): Record<string, string> {
 return token ? { Authorization: `Bearer ${token}` } : {};
 }

 async function load() {
 if (!slug) return;
 loading = true; err = '';
 try {
 const p = new URLSearchParams({ project: slug, limit: String(limit) });
 if (statusFilter && statusFilter !== 'all') p.set('status', statusFilter);
 if (kindFilter) p.set('kind', kindFilter);
 const r = await fetch(`/api/minions?${p}`, { headers: _h() });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'load failed');
 minions = d.minions || [];
 } catch (e: any) {
 err = e.message; minions = [];
 }
 loading = false;
 }

 async function enqueueDream() {
 err = ''; info = '';
 try {
 const p = new URLSearchParams({ project: slug });
 const r = await fetch(`/api/minions/dream?${p}`, { method: 'POST', headers: _h() });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'enqueue failed');
 info = `Dream cycle enqueued: ${(d.minion_ids || []).join(', ')}`;
 await load();
 } catch (e: any) { err = e.message; }
 }

 async function runOne() {
 err = ''; info = '';
 try {
 const r = await fetch(`/api/minions/run-one`, { method: 'POST', headers: _h() });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || d.error || 'run failed');
 info = d.worked ? 'Ran one minion.' : 'No pending minions to run.';
 await load();
 } catch (e: any) { err = e.message; }
 }

 async function cancel(id: number) {
 if (!confirm(`Cancel minion #${id}?`)) return;
 try {
 const r = await fetch(`/api/minions/${id}/cancel`, { method: 'POST', headers: _h() });
 if (!r.ok) throw new Error((await r.json()).detail || 'cancel failed');
 await load();
 } catch (e: any) { err = e.message; }
 }

 function fmtTime(s: string | null | undefined) {
 if (!s) return '—';
 try { return new Date(s).toLocaleString(); } catch { return s; }
 }

 function statusClass(s: string): string {
 return `st-${s || 'unknown'}`;
 }

 function tickRefresh() {
 if (timer) clearInterval(timer);
 if (autoRefresh) {
 timer = setInterval(() => { load(); }, 5000);
 }
 }

 onMount(() => {
 token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
 load();
 tickRefresh();
 });

 onDestroy(() => { if (timer) clearInterval(timer); });

 $effect(() => { void autoRefresh; tickRefresh(); });
 $effect(() => { if (slug) { void statusFilter; void kindFilter; load(); } });
</script>

<svelte:head><title>Minions · {slug}</title></svelte:head>

<div class="page">
  <header class="hd">
    <a class="back" href="{base}/project/{slug}/settings">← Settings</a>
    <h1>Minions</h1>
    <p class="sub">Durable Postgres-backed job queue. Survives crashes via lease-based claim. Dream nightly cycle enqueues dedupe / re-embed / prune / recompile maintenance.</p>
  </header>

  {#if err}<div class="err">{err}</div>{/if}
  {#if info}<div class="ok">{info}</div>{/if}

  <section class="sec">
    <div class="toolbar">
      <div class="filters">
        <label class="lbl">Status
          <select bind:value={statusFilter}>
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="running">Running</option>
            <option value="done">Done</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
        <label class="lbl">Kind
          <select bind:value={kindFilter}>
            <option value="">All kinds</option>
            {#each KINDS as k}<option value={k}>{k}</option>{/each}
          </select>
        </label>
        <label class="chk"><input type="checkbox" bind:checked={autoRefresh} /> Auto-refresh 5s</label>
      </div>
      <div class="actions-bar">
        <button class="btn" onclick={load} disabled={loading}>↻ Refresh</button>
        <button class="btn" onclick={runOne}>▶ Run one (test)</button>
        <button class="btn primary" onclick={enqueueDream}><Icon name="star" size={14} /> Enqueue Dream Cycle</button>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="row">
      <h2>Minions <span class="count">{minions.length}</span></h2>
    </div>
    {#if loading && minions.length === 0}
      <div class="muted">Loading…</div>
    {:else if minions.length === 0}
      <div class="empty">No minions match the current filters. Try enqueueing a Dream cycle.</div>
    {:else}
      <table class="tbl">
        <thead>
          <tr>
            <th>ID</th>
            <th>Kind</th>
            <th>Status</th>
            <th>Pri</th>
            <th>Attempts</th>
            <th>Scheduled</th>
            <th>Claimed by</th>
            <th>Lease until</th>
            <th>Error</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each minions as m (m.id)}
            <tr>
              <td class="mono">#{m.id}</td>
              <td class="mono">{m.kind}</td>
              <td><span class="badge {statusClass(m.status)}">{m.status}</span></td>
              <td class="num">{m.priority}</td>
              <td class="num">{m.attempts}/{m.max_attempts}</td>
              <td class="muted">{fmtTime(m.scheduled_at)}</td>
              <td class="mono">{m.claimed_by || '—'}</td>
              <td class="muted">{fmtTime(m.lease_until)}</td>
              <td class="err-prev" title={m.error || ''}>{(m.error || '').slice(0, 80)}</td>
              <td class="actions">
                {#if m.status === 'pending' || m.status === 'running'}
                  <button class="btn danger" onclick={() => cancel(m.id)}>Cancel</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
 .page { max-width: 1280px; margin: 0 auto; padding: 24px 32px 80px; }
 .hd { margin-bottom: 24px; }
 .back { font-size: 11px; color: var(--pw-ink-soft, #888); text-decoration: none; }
 .back:hover { color: var(--pw-accent, #c96342); }
 h1 { font-family: var(--pw-serif, Georgia, serif); font-size: 18px; margin: 8px 0 4px; color: var(--pw-ink, #2c2a26); }
 .sub { color: var(--pw-ink-soft, #777); font-size: 11px; max-width: 820px; margin: 0; }
 .err { background: rgba(220, 53, 53, 0.08); color: #c0392b; padding: 8px 12px; border: 1px solid rgba(220, 53, 53, 0.3); border-radius: 0; margin-bottom: 16px; font-size: 11px; }
 .ok { background: rgba(40, 160, 80, 0.08); color: #1e7a3c; padding: 8px 12px; border: 1px solid rgba(40, 160, 80, 0.3); border-radius: 0; margin-bottom: 16px; font-size: 11px; }
 .sec { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5e2dc); border-radius: 0; padding: 16px 20px; margin-bottom: 20px; }
 .toolbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
 .filters { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
 .actions-bar { display: flex; gap: 8px; }
 .lbl { font-size: 11px; color: var(--pw-ink-soft, #666); text-transform: uppercase; letter-spacing: 0.05em; display: inline-flex; gap: 6px; align-items: center; }
 .lbl select { font-size: 11px; padding: 4px 8px; border: 1px solid var(--pw-border, #d8d4cc); border-radius: 0; background: var(--pw-bg, #fff); color: var(--pw-ink, #2c2a26); }
 .chk { font-size: 11px; color: var(--pw-ink-soft, #666); cursor: pointer; display: inline-flex; gap: 6px; align-items: center; }
 .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
 h2 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); margin: 0; }
 .count { display: inline-block; margin-left: 8px; padding: 1px 8px; background: var(--pw-bg-alt, #f5f1ea); border-radius: 0; font-size: 11px; color: var(--pw-ink, #2c2a26); }
 .empty, .muted { color: var(--pw-ink-soft, #888); font-size: 11px; }
 .tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
 .tbl thead th { text-align: left; padding: 6px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); border-bottom: 1px solid var(--pw-border, #e5e2dc); background: var(--pw-bg-alt, #f5f1ea); }
 .tbl tbody td { padding: 8px; border-bottom: 1px solid var(--pw-border, #efece6); vertical-align: top; }
 .tbl tbody tr:hover { background: rgba(201, 99, 66, 0.03); }
 .num { text-align: right; font-variant-numeric: tabular-nums; }
 .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
 .err-prev { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: #c0392b; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .actions { white-space: nowrap; }
 .btn { font-size: 11px; padding: 5px 10px; border: 1px solid var(--pw-border, #d8d4cc); background: var(--pw-bg, #fff); border-radius: 0; cursor: pointer; margin-left: 4px; color: var(--pw-ink, #2c2a26); }
 .btn:hover { background: var(--pw-bg-alt, #f5f1ea); }
 .btn:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn.primary { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
 .btn.primary:hover { background: #b85432; }
 .btn.danger { color: #c0392b; border-color: rgba(220, 53, 53, 0.3); }
 .btn.danger:hover { background: rgba(220, 53, 53, 0.08); }
 .badge { display: inline-block; font-size: 10px; padding: 2px 8px; border-radius: 0; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
 .st-pending { background: rgba(120, 120, 120, 0.12); color: #555; }
 .st-running { background: rgba(58, 141, 255, 0.14); color: #1e5fb8; }
 .st-done { background: rgba(40, 160, 80, 0.14); color: #1e7a3c; }
 .st-failed { background: rgba(220, 53, 53, 0.14); color: #c0392b; }
 .st-cancelled { background: rgba(160, 100, 0, 0.14); color: #8a5a00; }
</style>
