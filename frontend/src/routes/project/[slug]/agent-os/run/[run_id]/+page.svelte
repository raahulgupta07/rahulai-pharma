<script lang="ts">
  import { page } from '$app/stores';
  import { base } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount, onDestroy } from 'svelte';
  import ExecutionPane from '$lib/components/ExecutionPane.svelte';
  import { dashFetch } from '$lib/api';

  let slug = $derived($page.params.slug);
  let runId = $derived($page.params.run_id);

  let workflowName = $state('');
  let workflowId = $state<number | string | null>(null);
  let status = $state<'queued' | 'running' | 'done' | 'failed' | 'cancelled'>('queued');
  let dashboardId = $state<string | null>(null);
  let panelCount = $state(0);
  let stepsTotal = $state(0);
  let stepsDone = $state(0);
  let elapsed = $state(0);
  let startedAt = $state(Date.now());
  let events = $state<any[]>([]);
  let errorMsg = $state<string | null>(null);

  let es: EventSource | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  // LiveDashboardPane is owned by another agent; load dynamically if present
  let LiveDashboardPane: any = $state(null);

  onMount(async () => {
    startedAt = Date.now();

    // Try to load LiveDashboardPane (A3 may not have shipped yet)
    try {
      // @ts-ignore — dynamic import; module may not exist yet
      const m = await import('$lib/components/LiveDashboardPane.svelte');
      LiveDashboardPane = m.default;
    } catch {
      LiveDashboardPane = null;
    }

    // Elapsed ticker
    timer = setInterval(() => {
      if (status === 'running' || status === 'queued') {
        elapsed = Math.floor((Date.now() - startedAt) / 1000);
      }
    }, 250);

    // FETCH-FIRST: seed initial state from DB so fast runs (< SSE connect time)
    // render correctly. Branch on terminal status before subscribing.
    const terminal = await seedFromRest();
    if (!terminal) startStream();
    loadWorkflowMeta();
  });

  // Seed run state from REST. Returns true if run is terminal (done/failed/cancelled).
  async function seedFromRest(): Promise<boolean> {
    try {
      const r = await dashFetch(`/api/agent-os/workflows/runs/${runId}`);
      if (!r.ok) return false;
      const d = await r.json();
      if (d?.workflow_id) workflowId = d.workflow_id;
      if (d?.workflow_name) workflowName = d.workflow_name;
      if (typeof d?.steps_total === 'number') stepsTotal = d.steps_total;
      if (typeof d?.steps_completed === 'number') stepsDone = d.steps_completed;
      if (d?.dashboard_id) dashboardId = String(d.dashboard_id);
      const s = String(d?.status || '').toLowerCase();
      // Replay past events if available (so step list populates)
      if (Array.isArray(d?.events)) {
        for (const ev of d.events) {
          const t = ev?.type || ev?.event;
          if (t) events = [...events, { type: t, ...ev, _ts: Date.now() }];
        }
      }
      if (s === 'done') {
        status = 'done';
        if (typeof d?.duration_ms === 'number') elapsed = Math.max(1, Math.round(d.duration_ms / 1000));
        // Fetch panel count via dashboard
        if (dashboardId) {
          try {
            const dr = await dashFetch(`/api/dashboards/${dashboardId}`);
            if (dr.ok) {
              const dj = await dr.json();
              const spec = dj?.spec || dj;
              const panels = spec?.panels;
              if (Array.isArray(panels)) panelCount = panels.length;
            }
          } catch {}
        }
        return true;
      }
      if (s === 'failed' || s === 'cancelled') {
        status = s as any;
        errorMsg = d?.error || `Run ${s}`;
        return true;
      }
      // queued/running → leave state, open SSE
      if (s === 'running' || s === 'queued') status = s as any;
      return false;
    } catch {
      return false;
    }
  }

  onDestroy(() => {
    if (timer) clearInterval(timer);
    try { es?.close(); } catch {}
    es = null;
  });

  function recordEvent(type: string, data: any) {
    const ev = { type, ...data, _ts: Date.now() };
    events = [...events, ev];

    if (type === 'queued') {
      status = 'queued';
      if (typeof data?.steps_total === 'number') stepsTotal = data.steps_total;
    } else if (type === 'started' || type === 'running') {
      status = 'running';
      if (typeof data?.steps_total === 'number') stepsTotal = data.steps_total;
    } else if (type === 'step_start') {
      status = 'running';
    } else if (type === 'step_done') {
      stepsDone = events.filter((e) => e.type === 'step_done').length;
    } else if (type === 'panel_ready') {
      panelCount = Math.max(panelCount, Number(data?.panel_count || 0) || panelCount + 1);
    } else if (type === 'done') {
      status = 'done';
      if (data?.dashboard_id) dashboardId = String(data.dashboard_id);
      if (typeof data?.panel_count === 'number') panelCount = data.panel_count;
      try { es?.close(); } catch {}
      es = null;
    } else if (type === 'error') {
      status = 'failed';
      errorMsg = data?.error || data?.message || 'Run failed';
      try { es?.close(); } catch {}
      es = null;
    }

    if (data?.workflow_id && !workflowId) workflowId = data.workflow_id;
    if (data?.workflow_name && !workflowName) workflowName = data.workflow_name;
  }

  function startStream() {
    if (typeof window === 'undefined') return;
    try {
      const url = `/api/agent-os/workflows/runs/${runId}/stream`;
      es = new EventSource(url);
      const named = ['queued', 'started', 'running', 'step_start', 'step_done', 'step_error', 'panel_ready', 'done', 'error'];
      named.forEach((t) => {
        es!.addEventListener(t, (msg: MessageEvent) => {
          let data: any = {};
          try { data = JSON.parse(msg.data); } catch { data = { raw: msg.data }; }
          recordEvent(t, data);
        });
      });
      es.onmessage = (msg) => {
        try {
          const d = JSON.parse(msg.data);
          const t = d?.type || d?.event;
          if (t) recordEvent(t, d);
        } catch {}
      };
      es.onerror = () => { /* browser auto-retries */ };
    } catch (e) {
      errorMsg = `Stream failed: ${e}`;
    }
  }

  async function loadWorkflowMeta() {
    // workflow_id can come from events; if not yet known, fetch run detail
    try {
      const r = await dashFetch(`/api/agent-os/workflows/runs/${runId}`);
      if (r.ok) {
        const d = await r.json();
        if (d?.workflow_id && !workflowId) workflowId = d.workflow_id;
        if (d?.workflow_name && !workflowName) workflowName = d.workflow_name;
      }
    } catch {}

    if (workflowId && !workflowName) {
      try {
        const r2 = await dashFetch(`/api/agent-os/workflows/${workflowId}`);
        if (r2.ok) {
          const d2 = await r2.json();
          workflowName = d2?.name || d2?.title || '';
        }
      } catch {}
    }
  }

  async function cancelRun() {
    try {
      await dashFetch(`/api/agent-os/workflows/runs/${runId}/cancel`, { method: 'POST' });
      status = 'cancelled';
      try { es?.close(); } catch {}
      es = null;
    } catch (e) {
      errorMsg = `Cancel failed: ${e}`;
    }
  }

  async function reRun() {
    if (!workflowId) {
      errorMsg = 'cannot re-run: workflow id unknown';
      return;
    }
    try {
      const r = await dashFetch(`/api/agent-os/workflows/${workflowId}/run`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const newRunId = d?.run_id ?? d?.id;
      if (newRunId) {
        goto(`${base}/project/${slug}/agent-os/run/${newRunId}`);
      }
    } catch (e) {
      errorMsg = `Re-run failed: ${e}`;
    }
  }

  function backToList() {
    goto(`${base}/agent-os/workflows`);
  }

  function openFull() {
    if (dashboardId) goto(`${base}/project/${slug}/studio/${dashboardId}`);
  }

  function openSchedule() {
    alert('schedule modal coming');
  }

  function statusPillClass(): string {
    if (status === 'done') return 'pill-done';
    if (status === 'failed' || status === 'cancelled') return 'pill-failed';
    if (status === 'running') return 'pill-running';
    return 'pill-queued';
  }

  function statusGlyph(): string {
    if (status === 'done') return '✓';
    if (status === 'failed' || status === 'cancelled') return '✗';
    if (status === 'running') return '●';
    return '◷';
  }
</script>

<div class="page-root">
  <!-- Topbar -->
  <header class="topbar">
    <button class="back-btn" type="button" onclick={backToList} aria-label="Back to workflows">←</button>
    <span class="divider"></span>
    <span class="wf-name">{workflowName || 'Workflow Run'}</span>
    <span class="run-id">run #{runId?.slice(-8)}</span>
    <span class="status-pill {statusPillClass()}">
      <span class="status-glyph">{statusGlyph()}</span>
      {status}
    </span>
    <span class="elapsed">{elapsed}s</span>

    <span class="spacer"></span>

    {#if status === 'done'}
      {#if dashboardId}
        <button class="topbar-btn primary" type="button" onclick={openFull}>↗ OPEN FULL</button>
      {/if}
      <button class="topbar-btn" type="button" onclick={openSchedule}>📅 SCHEDULE</button>
      <button class="topbar-btn" type="button" onclick={reRun}>↻ RE-RUN</button>
    {:else if status === 'failed' || status === 'cancelled'}
      <button class="topbar-btn" type="button" onclick={reRun}>↻ RE-RUN</button>
    {/if}
  </header>

  {#if errorMsg}
    <div class="err-banner">{errorMsg}</div>
  {/if}

  <!-- Split body -->
  <div class="split-body">
    <aside class="left-pane">
      <ExecutionPane
        events={events}
        status={status}
        elapsed={elapsed}
        stepsDone={stepsDone}
        stepsTotal={stepsTotal}
        workflowName={workflowName}
        onCancel={cancelRun}
        onReRun={reRun}
      />
    </aside>

    <main class="right-pane">
      {#if LiveDashboardPane}
        <svelte:component
          this={LiveDashboardPane}
          run_id={runId}
          project_slug={slug}
          dashboard_id={dashboardId}
          events={events}
          status={status}
          workflow_name={workflowName}
        />
      {:else}
        <div class="dash-placeholder">
          <div class="ph-title">Live Dashboard</div>
          <div class="ph-meta">
            run #{runId?.slice(-8)} · {status}{panelCount ? ` · ${panelCount} panels` : ''}
          </div>
          <div class="ph-note">LiveDashboardPane pending (A3 in flight)</div>
          {#if status === 'done' && dashboardId}
            <button class="ph-cta" type="button" onclick={openFull}>↗ Open full dashboard</button>
          {/if}
        </div>
      {/if}
    </main>
  </div>
</div>

<style>
  .page-root {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--pw-bg, #faf9f5);
    color: var(--pw-ink, #2c2a26);
  }

  .topbar {
    height: 56px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    background: var(--pw-bg, #faf9f5);
    border-bottom: 1px solid var(--pw-border, #e7e3da);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .back-btn {
    background: transparent;
    border: 1px solid var(--pw-border, #e7e3da);
    border-radius: 4px;
    padding: 4px 10px;
    font: inherit;
    font-size: 16px;
    cursor: pointer;
    color: var(--pw-ink, #2c2a26);
  }
  .back-btn:hover { background: var(--pw-bg-alt, #f1ede4); }
  .divider {
    width: 1px;
    height: 24px;
    background: var(--pw-border, #e7e3da);
  }
  .wf-name {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 15px;
    font-weight: 600;
    color: var(--pw-ink, #2c2a26);
  }
  .run-id {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--pw-muted, #87837a);
  }
  .status-pill {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 3px 8px;
    border-radius: 12px;
    border: 1px solid;
  }
  .status-glyph { font-size: 10px; }
  .pill-running {
    color: var(--pw-accent, #c96342);
    border-color: var(--pw-accent, #c96342);
    animation: pulse-pill 1.5s ease-in-out infinite;
  }
  .pill-done { color: #16a34a; border-color: #16a34a; }
  .pill-failed { color: #dc2626; border-color: #dc2626; }
  .pill-queued { color: #a06000; border-color: #a06000; }
  @keyframes pulse-pill { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }
  .elapsed {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--pw-muted, #87837a);
  }
  .spacer { flex: 1; }
  .topbar-btn {
    font: inherit;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 6px 10px;
    background: transparent;
    border: 1px solid var(--pw-border, #e7e3da);
    color: var(--pw-ink, #2c2a26);
    border-radius: 4px;
    cursor: pointer;
  }
  .topbar-btn:hover { background: var(--pw-bg-alt, #f1ede4); }
  .topbar-btn.primary {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }
  .topbar-btn.primary:hover { filter: brightness(0.95); }

  .err-banner {
    background: #fef2f2;
    color: #b91c1c;
    border-bottom: 1px solid #fecaca;
    padding: 8px 16px;
    font-size: 12px;
    flex-shrink: 0;
  }

  .split-body {
    display: flex;
    flex-direction: row;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
  .left-pane {
    flex: 0 0 38%;
    max-width: 38%;
    min-width: 320px;
    overflow-y: auto;
    border-right: 1px solid var(--pw-border, #e7e3da);
    background: var(--pw-bg-alt, #f1ede4);
  }
  .right-pane {
    flex: 1 1 0;
    min-width: 0;
    overflow-y: auto;
    background: var(--pw-bg, #faf9f5);
  }

  .dash-placeholder {
    padding: 32px;
    text-align: center;
    color: var(--pw-muted, #87837a);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .ph-title {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 20px;
    color: var(--pw-ink, #2c2a26);
  }
  .ph-meta { font-family: ui-monospace, monospace; font-size: 12px; }
  .ph-note {
    font-size: 11px;
    color: var(--pw-muted, #87837a);
    font-style: italic;
  }
  .ph-cta {
    margin-top: 12px;
    background: var(--pw-accent, #c96342);
    color: #fff;
    border: 1px solid var(--pw-accent, #c96342);
    padding: 8px 14px;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.05em;
    border-radius: 4px;
    cursor: pointer;
  }
  .ph-cta:hover { filter: brightness(0.95); }

  @media (max-width: 900px) {
    .split-body { flex-direction: column; }
    .left-pane {
      flex: 0 0 200px;
      max-width: 100%;
      min-width: 0;
      max-height: 200px;
      border-right: none;
      border-bottom: 1px solid var(--pw-border, #e7e3da);
      position: sticky;
      top: 56px;
      z-index: 50;
    }
    .right-pane { flex: 1; }
  }
</style>
