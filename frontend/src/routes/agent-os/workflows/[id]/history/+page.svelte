<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import { dashFetch, getWorkflowHistory } from '$lib/api';

  const wfId = $derived(Number($page.params.id));

  let wf = $state<any>(null);
  let runs = $state<any[]>([]);
  let loading = $state(true);
  let errorMsg = $state<string | null>(null);

  async function load() {
    loading = true;
    errorMsg = null;
    try {
      // Workflow detail (auth-guarded via dashFetch)
      try {
        const r = await dashFetch(`/api/agent-os/workflows/${wfId}`);
        wf = r.ok ? await r.json().catch(() => null) : null;
      } catch {
        wf = null;
      }
      // Run history via canonical helper (correct URL + auth)
      try {
        runs = await getWorkflowHistory(wfId, 50);
      } catch (e) {
        runs = [];
        errorMsg = e instanceof Error ? e.message : String(e);
      }
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  const stats = $derived.by(() => {
    const total = runs.length;
    const ok = runs.filter((r) => ['done', 'ok', 'success'].includes((r.status || '').toLowerCase())).length;
    const successRate = total ? Math.round((ok / total) * 100) : 0;
    const now = Date.now();
    const last7d = runs.filter((r) => {
      const t = Date.parse(r.started_at || r.created_at || '');
      return !Number.isNaN(t) && (now - t) < 7 * 86400000;
    }).length;
    return { total, successRate, last7d };
  });

  function relTime(iso?: string): string {
    if (!iso) return '—';
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return iso;
    const diff = Math.max(0, Date.now() - t);
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  function fmtDuration(ms?: number, s?: number): string {
    let v = ms != null ? ms : (s != null ? s * 1000 : null);
    if (v == null) return '—';
    if (v < 1000) return `${v}ms`;
    if (v < 60000) return `${(v / 1000).toFixed(1)}s`;
    const m = Math.floor(v / 60000);
    const r = Math.floor((v % 60000) / 1000);
    return `${m}m ${r}s`;
  }

  function statusPill(s?: string) {
    const v = (s || '').toLowerCase();
    if (['done', 'ok', 'success'].includes(v)) return { label: 'DONE', color: '#16a34a', bg: 'rgba(22,163,74,0.10)' };
    if (['fail', 'failed', 'error'].includes(v)) return { label: 'FAILED', color: '#dc2626', bg: 'rgba(220,38,38,0.10)' };
    if (v === 'running') return { label: 'RUNNING', color: '#c96342', bg: 'rgba(201,99,66,0.10)' };
    if (v === 'queued') return { label: 'QUEUED', color: '#6b7280', bg: 'rgba(107,114,128,0.10)' };
    if (v === 'cancelled' || v === 'canceled') return { label: 'CANCELLED', color: '#87837a', bg: 'rgba(135,131,122,0.10)' };
    return { label: (s || '—').toUpperCase(), color: '#6b7280', bg: 'rgba(107,114,128,0.10)' };
  }

  function sourcePill(src?: string) {
    const v = (src || 'manual').toLowerCase();
    if (v === 'cron' || v === 'scheduled') return { label: 'CRON', color: '#2563eb', bg: 'rgba(37,99,235,0.10)' };
    return { label: 'MANUAL', color: '#c96342', bg: 'rgba(201,99,66,0.10)' };
  }
</script>

<div class="page">
  <div class="head">
    <a class="back" href="{base}/agent-os/workflows">← back to workflows</a>
    <h1 class="title">{wf?.name || 'Workflow'} — run history</h1>
    <div class="stats">
      <span><b>{stats.total}</b> total runs</span>
      <span class="sep">·</span>
      <span><b>{stats.successRate}%</b> success</span>
      <span class="sep">·</span>
      <span><b>{stats.last7d}</b> last 7d</span>
    </div>
  </div>

  {#if errorMsg}
    <div class="err">! {errorMsg}</div>
  {/if}

  {#if loading}
    <div class="empty">loading…</div>
  {:else if runs.length === 0}
    <div class="empty">No runs yet.</div>
  {:else}
    <table class="tbl">
      <thead>
        <tr>
          <th>STATUS</th>
          <th>STARTED</th>
          <th>DURATION</th>
          <th>STEPS</th>
          <th>SOURCE</th>
          <th>DASHBOARD</th>
        </tr>
      </thead>
      <tbody>
        {#each runs as r}
          {@const sp = statusPill(r.status)}
          {@const sp2 = sourcePill(r.source || r.trigger)}
          <tr class="row" onclick={() => goto(`${base}/agent-os/workflows/runs/${r.id}`)}>
            <td><span class="pill" style="color:{sp.color};background:{sp.bg};">{sp.label}</span></td>
            <td class="mono">{relTime(r.started_at || r.created_at)}</td>
            <td class="mono">{fmtDuration(r.duration_ms, r.duration_s)}</td>
            <td class="mono">{r.steps_done ?? '—'}/{r.steps_total ?? '—'}</td>
            <td><span class="pill" style="color:{sp2.color};background:{sp2.bg};">{sp2.label}</span></td>
            <td>
              {#if r.dashboard_id}
                <a class="open-link" href="{base}/project/{r.project_slug || wf?.project_slug || ''}/studio/{r.dashboard_id}" onclick={(e) => e.stopPropagation()}>OPEN ↗</a>
              {:else}
                <span class="dim">—</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .page {
    padding: 20px 32px 80px;
    max-width: 1200px;
    margin: 0 auto;
    color: var(--pw-ink);
    font-size: 13px;
  }
  .head { margin-bottom: 18px; }
  .back { font-size: 12px; color: var(--pw-muted); text-decoration: none; }
  .back:hover { color: var(--pw-accent); }
  .title { font-family: var(--pw-serif, Georgia, serif); font-size: 22px; font-weight: 500; margin: 6px 0; color: var(--pw-ink); }
  .stats { font-size: 12px; color: var(--pw-muted); display: flex; gap: 6px; align-items: center; }
  .stats b { color: var(--pw-ink); font-weight: 700; }
  .sep { color: var(--pw-border); }
  .err {
    background: rgba(220,38,38,0.08); color: #991b1b;
    border: 1px solid rgba(220,38,38,0.3);
    padding: 8px 12px; margin-bottom: 12px;
    font-family: ui-monospace, monospace; font-size: 12px;
  }
  .empty {
    text-align: center; padding: 40px;
    color: var(--pw-muted); font-family: ui-monospace, monospace;
  }
  .tbl {
    width: 100%; border-collapse: collapse;
    background: var(--pw-surface, #faf9f5);
    border: 1px solid var(--pw-border, #e7e3da);
  }
  .tbl th {
    text-align: left; font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
    color: var(--pw-muted, #87837a); text-transform: uppercase;
    padding: 8px 10px;
    background: var(--pw-bg-alt, #f1ede4);
    border-bottom: 1px solid var(--pw-border, #e7e3da);
  }
  .tbl td { padding: 8px 10px; border-bottom: 1px solid var(--pw-border-soft, #efeae0); font-size: 12px; }
  .row { cursor: pointer; }
  .row:hover { background: rgba(201,99,66,0.04); }
  .pill {
    font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
    padding: 2px 8px; border-radius: 0;
  }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .dim { color: var(--pw-muted); }
  .open-link {
    font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    color: var(--pw-accent, #c96342); text-decoration: none;
  }
  .open-link:hover { text-decoration: underline; }
</style>
