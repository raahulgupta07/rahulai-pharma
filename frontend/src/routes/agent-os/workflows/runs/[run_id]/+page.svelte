<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import WorkflowRunDrawer from '$lib/components/WorkflowRunDrawer.svelte';

  const runId = $derived(Number($page.params.run_id));

  let run = $state<any>(null);
  let steps = $state<any[]>([]);
  let loading = $state(true);
  let errorMsg = $state<string | null>(null);
  let rerunDrawerId = $state<number | null>(null);
  let rerunWorkflow = $state<any>(null);

  async function load() {
    loading = true;
    errorMsg = null;
    try {
      const res = await fetch(`/api/agent-os/workflows/runs/${runId}`, { credentials: 'include' });
      if (!res.ok) throw new Error(`runs/${runId} → ${res.status}`);
      const data = await res.json();
      run = data?.run || data;
      steps = data?.steps || run?.steps || [];
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  async function doReRun() {
    if (!run?.workflow_id) return;
    try {
      const res = await fetch(`/api/agent-os/workflows/${run.workflow_id}/run`, {
        method: 'POST', credentials: 'include',
      });
      const body = await res.json().catch(() => ({}));
      if (body?.run_id) {
        rerunDrawerId = body.run_id;
        rerunWorkflow = {
          name: run.workflow_name || `wf ${run.workflow_id}`,
          project_slug: run.project_slug || '',
        };
      }
    } catch (e) {
      errorMsg = `Re-run failed: ${e}`;
    }
  }

  function fmtMs(ms?: number): string {
    if (ms == null) return '—';
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const m = Math.floor(ms / 60000);
    const r = Math.floor((ms % 60000) / 1000);
    return `${m}m ${r}s`;
  }

  function glyph(s: any): string {
    const st = (s?.status || '').toLowerCase();
    if (st === 'done' || st === 'ok' || st === 'success') return '✓';
    if (st === 'running') return '●';
    if (st === 'failed' || st === 'error' || st === 'fail') return '✗';
    return '○';
  }

  function statusColor(s?: string): string {
    const v = (s || '').toLowerCase();
    if (['done', 'ok', 'success'].includes(v)) return '#16a34a';
    if (['failed', 'error', 'fail'].includes(v)) return '#dc2626';
    if (v === 'running') return '#c96342';
    return '#6b7280';
  }
</script>

<div class="page">
  <div class="head">
    <a class="back" href="{base}/agent-os/workflows">← back to workflows</a>
    <h1 class="title">
      RUN #{runId}
      {#if run?.workflow_name}<span class="wf-name">· {run.workflow_name}</span>{/if}
    </h1>
    {#if run}
      <div class="meta">
        <span class="status" style="color:{statusColor(run.status)};">{(run.status || '—').toUpperCase()}</span>
        <span class="sep">·</span>
        <span>started {run.started_at || run.created_at || '—'}</span>
        {#if run.duration_ms != null || run.duration_s != null}
          <span class="sep">·</span>
          <span>{fmtMs(run.duration_ms ?? (run.duration_s != null ? run.duration_s * 1000 : null))}</span>
        {/if}
        {#if run.steps_done != null || run.steps_total != null}
          <span class="sep">·</span>
          <span>{run.steps_done ?? '—'}/{run.steps_total ?? '—'} steps</span>
        {/if}
      </div>
    {/if}
  </div>

  <div class="actions">
    {#if run?.dashboard_id && run?.project_slug}
      <a class="btn-primary" href="{base}/project/{run.project_slug}/studio/{run.dashboard_id}">OPEN DASHBOARD ↗</a>
    {:else}
      <button class="btn-primary" disabled>OPEN DASHBOARD ↗</button>
    {/if}
    <button class="btn-ghost" onclick={doReRun} disabled={!run?.workflow_id}>↻ RE-RUN</button>
  </div>

  {#if errorMsg}
    <div class="err">! {errorMsg}</div>
  {/if}

  {#if loading}
    <div class="empty">loading…</div>
  {:else if !run}
    <div class="empty">Run not found.</div>
  {:else}
    <div class="steps-card">
      <div class="card-h">STEPS</div>
      {#if steps.length === 0}
        <div class="empty">No step records.</div>
      {:else}
        {#each steps as s}
          <div class="step-row">
            <span class="step-glyph" style="color:{statusColor(s.status)};">{glyph(s)}</span>
            <span class="step-name">{s.name || s.step || 'step'}</span>
            <span class="step-meta">
              <span>{fmtMs(s.duration_ms)}</span>
              {#if s.rows != null || s.row_count != null}
                <span class="sep">·</span><span>{s.rows ?? s.row_count} rows</span>
              {/if}
              {#if s.error}<span class="sep">·</span><span class="err-inline">{s.error}</span>{/if}
            </span>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</div>

{#if rerunDrawerId && rerunWorkflow}
  <WorkflowRunDrawer
    run_id={rerunDrawerId}
    workflow_name={rerunWorkflow.name}
    project_slug={rerunWorkflow.project_slug}
    onClose={() => { rerunDrawerId = null; rerunWorkflow = null; }}
    onComplete={() => {}}
  />
{/if}

<style>
  .page {
    padding: 20px 32px 80px;
    max-width: 1000px;
    margin: 0 auto;
    color: var(--pw-ink);
    font-size: 13px;
  }
  .head { margin-bottom: 16px; }
  .back { font-size: 12px; color: var(--pw-muted); text-decoration: none; }
  .back:hover { color: var(--pw-accent); }
  .title { font-family: var(--pw-serif, Georgia, serif); font-size: 22px; font-weight: 500; margin: 6px 0; }
  .wf-name { font-size: 16px; color: var(--pw-muted); font-weight: 400; }
  .meta { font-family: ui-monospace, monospace; font-size: 11px; color: var(--pw-muted); display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .status { font-weight: 700; letter-spacing: 0.06em; }
  .sep { color: var(--pw-border); }
  .actions { display: flex; gap: 8px; margin-bottom: 18px; }
  .btn-primary {
    font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    padding: 6px 14px; background: var(--pw-accent, #c96342); color: #fff;
    border: 1px solid var(--pw-accent, #c96342); border-radius: var(--r-sm, 6px);
    cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
  }
  .btn-primary:hover:not(:disabled) { filter: brightness(0.95); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-ghost {
    font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    padding: 6px 14px; background: transparent; color: var(--pw-ink);
    border: 1px solid var(--pw-border); border-radius: var(--r-sm, 6px);
    cursor: pointer;
  }
  .btn-ghost:hover:not(:disabled) { background: var(--pw-bg-alt); }
  .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
  .err {
    background: rgba(220,38,38,0.08); color: #991b1b;
    border: 1px solid rgba(220,38,38,0.3);
    padding: 8px 12px; margin-bottom: 12px;
    font-family: ui-monospace, monospace; font-size: 12px;
  }
  .empty {
    text-align: center; padding: 30px;
    color: var(--pw-muted); font-family: ui-monospace, monospace; font-size: 11px;
  }
  .steps-card {
    background: var(--pw-surface, #faf9f5);
    border: 1px solid var(--pw-border, #e7e3da);
    border-radius: var(--r-md, 8px);
    overflow: hidden;
  }
  .card-h {
    font-size: 10px; font-weight: 800; letter-spacing: 0.1em;
    color: var(--pw-muted); text-transform: uppercase;
    padding: 10px 16px;
    background: var(--pw-bg-alt, #f1ede4);
    border-bottom: 1px solid var(--pw-border);
  }
  .step-row {
    display: grid;
    grid-template-columns: 24px 1fr auto;
    gap: 10px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--pw-border-soft, #efeae0);
    font-size: 12px;
    align-items: center;
  }
  .step-row:last-child { border-bottom: none; }
  .step-glyph { font-family: ui-monospace, monospace; font-weight: 700; }
  .step-name { color: var(--pw-ink); }
  .step-meta { font-family: ui-monospace, monospace; font-size: 11px; color: var(--pw-muted); display: inline-flex; gap: 4px; }
  .step-meta .sep { color: var(--pw-border); }
  .err-inline { color: #dc2626; }
</style>
