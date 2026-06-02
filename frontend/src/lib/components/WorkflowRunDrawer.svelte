<!--
  @deprecated for new runs — workflow runs now use the split-page route
  at /ui/project/{slug}/agent-os/run/{run_id} (ExecutionPane + LiveDashboardPane).
  This drawer is still used by the run detail readonly view at
  /ui/agent-os/workflows/runs/[run_id]. Do NOT remove without updating callers.
-->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  interface Step {
    name: string;
    status: 'pending' | 'running' | 'done' | 'failed';
    duration_ms?: number;
    rows?: number;
    error?: string;
  }

  let {
    run_id,
    workflow_name = 'workflow',
    project_slug = '',
    onClose = () => {},
    onComplete = (_dashboard_id: any) => {},
  }: {
    run_id: number;
    workflow_name?: string;
    project_slug?: string;
    onClose?: () => void;
    onComplete?: (dashboard_id: any) => void;
  } = $props();

  let status = $state<'queued' | 'running' | 'done' | 'failed'>('queued');
  let steps = $state<Step[]>([]);
  let stepsTotal = $state(0);
  let stepsDone = $state(0);
  let dashboardId = $state<any>(null);
  let errorMsg = $state<string | null>(null);
  let startTs = $state(Date.now());
  let elapsedS = $state(0);
  let autoOpen = $state(true);
  let notifyInFeed = $state(true);
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  function initPrefs() {
    if (typeof window === 'undefined') return;
    try {
      const v = localStorage.getItem('dash.wf.autoOpen');
      if (v !== null) autoOpen = v === '1';
    } catch {}
  }

  function persistAutoOpen() {
    if (typeof window === 'undefined') return;
    try { localStorage.setItem('dash.wf.autoOpen', autoOpen ? '1' : '0'); } catch {}
  }

  function findStep(name: string): Step | undefined {
    return steps.find((s) => s.name === name);
  }

  function upsertStep(name: string, patch: Partial<Step>) {
    const existing = findStep(name);
    if (existing) {
      Object.assign(existing, patch);
      steps = [...steps];
    } else {
      steps = [...steps, { name, status: 'pending', ...patch } as Step];
    }
  }

  function handleEvent(type: string, payload: any) {
    if (type === 'queued') {
      status = 'queued';
      if (typeof payload?.steps_total === 'number') stepsTotal = payload.steps_total;
    } else if (type === 'started' || type === 'running') {
      status = 'running';
      if (typeof payload?.steps_total === 'number') stepsTotal = payload.steps_total;
    } else if (type === 'step_start') {
      const name = payload?.name || payload?.step || 'step';
      upsertStep(name, { status: 'running' });
      status = 'running';
    } else if (type === 'step_done') {
      const name = payload?.name || payload?.step || 'step';
      upsertStep(name, {
        status: 'done',
        duration_ms: payload?.duration_ms,
        rows: payload?.rows ?? payload?.row_count,
      });
      stepsDone = steps.filter((s) => s.status === 'done').length;
    } else if (type === 'step_error') {
      const name = payload?.name || payload?.step || 'step';
      upsertStep(name, {
        status: 'failed',
        duration_ms: payload?.duration_ms,
        error: payload?.error || payload?.message,
      });
    } else if (type === 'done') {
      status = 'done';
      dashboardId = payload?.dashboard_id ?? null;
      try { es?.close(); } catch {}
      es = null;
      try { onComplete(dashboardId); } catch {}
      if (autoOpen && dashboardId && project_slug) {
        try {
          goto(`${base}/project/${project_slug}/studio/${dashboardId}`);
        } catch {}
      }
    } else if (type === 'error') {
      status = 'failed';
      errorMsg = payload?.error || payload?.message || 'Run failed';
      try { es?.close(); } catch {}
      es = null;
    }
  }

  function startStream() {
    if (typeof window === 'undefined') return;
    try {
      const url = `/api/agent-os/workflows/runs/${run_id}/stream`;
      es = new EventSource(url);
      const named = ['queued', 'started', 'running', 'step_start', 'step_done', 'step_error', 'done', 'error'];
      named.forEach((t) => {
        es!.addEventListener(t, (msg: MessageEvent) => {
          let data: any = {};
          try { data = JSON.parse(msg.data); } catch { data = { raw: msg.data }; }
          handleEvent(t, data);
        });
      });
      es.onmessage = (msg) => {
        try {
          const d = JSON.parse(msg.data);
          const t = d?.type || d?.event;
          if (t) handleEvent(t, d);
        } catch {}
      };
      es.onerror = () => {
        // browser auto-retries
      };
    } catch (e) {
      errorMsg = `Stream failed: ${e}`;
    }
  }

  async function doCancel() {
    try {
      await fetch(`/api/agent-os/workflows/runs/${run_id}/cancel`, { method: 'POST' });
    } catch {}
    try { es?.close(); } catch {}
    es = null;
    onClose();
  }

  function runInBg() {
    // do NOT close es — let it keep running in background
    es = null; // detach handle so onDestroy doesn't kill it... actually we want it to keep streaming server-side; client cleanup is fine
    onClose();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      try { es?.close(); } catch {}
      es = null;
      onClose();
    }
  }

  function fmtMs(ms?: number): string {
    if (ms == null) return '';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function fmtElapsed(s: number): string {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m > 0 ? `${m}m ${r}s` : `${r}s`;
  }

  function progressBlocks(): string {
    const total = Math.max(stepsTotal || steps.length || 1, 1);
    const done = Math.min(stepsDone, total);
    const width = 20;
    const filled = Math.round((done / total) * width);
    return '▓'.repeat(filled) + '░'.repeat(width - filled);
  }

  function glyph(s: Step): string {
    if (s.status === 'done') return '✓';
    if (s.status === 'running') return '●';
    if (s.status === 'failed') return '✗';
    return '○';
  }

  function statusColor(): string {
    if (status === 'done') return '#16a34a';
    if (status === 'failed') return '#dc2626';
    if (status === 'running') return '#c96342';
    return '#6b7280';
  }

  onMount(() => {
    initPrefs();
    startTs = Date.now();
    timer = setInterval(() => {
      elapsedS = Math.floor((Date.now() - startTs) / 1000);
    }, 500);
    startStream();
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
    try { es?.close(); } catch {}
    es = null;
  });
</script>

<svelte:window onkeydown={onKey} />

<div class="wfd-backdrop" onclick={onClose} role="presentation"></div>

<aside class="wfd" role="dialog" aria-label="Workflow run">
  <!-- Top dark CLI panel -->
  <div class="wfd-cli">
    <div class="cli-row">
      <span class="cli-prompt">$</span>
      <span class="cli-cmd">RUN #{run_id}</span>
      <span class="cli-sep">·</span>
      <span class="cli-wf">{workflow_name}</span>
      <span class="status-badge" style="color: {statusColor()}; border-color: {statusColor()};">
        {status.toUpperCase()}
      </span>
    </div>
    <div class="cli-row dim">
      <span>elapsed {fmtElapsed(elapsedS)}</span>
      <span class="cli-sep">·</span>
      <span>{stepsDone}/{stepsTotal || steps.length || 0} steps</span>
    </div>
    <div class="cli-progress">
      <span class="cli-bar">{progressBlocks()}</span>
    </div>
    {#if errorMsg}
      <div class="cli-err">! {errorMsg}</div>
    {/if}
  </div>

  <!-- Step list -->
  <div class="wfd-steps">
    {#if steps.length === 0}
      <div class="empty">waiting for first event…</div>
    {:else}
      {#each steps as s (s.name)}
        <div class="step-row" class:running={s.status === 'running'} class:done={s.status === 'done'} class:failed={s.status === 'failed'}>
          <span class="step-glyph">{glyph(s)}</span>
          <span class="step-name">{s.name}</span>
          <span class="step-meta">
            {#if s.duration_ms != null}<span>{fmtMs(s.duration_ms)}</span>{/if}
            {#if s.rows != null}<span class="sep">·</span><span>{s.rows} rows</span>{/if}
            {#if s.error}<span class="sep">·</span><span class="err-inline">{s.error}</span>{/if}
          </span>
        </div>
      {/each}
    {/if}
  </div>

  <!-- Options -->
  <div class="wfd-options">
    <label class="opt"><input type="checkbox" bind:checked={autoOpen} onchange={persistAutoOpen} /><span>Auto-open dashboard when done</span></label>
    <label class="opt"><input type="checkbox" bind:checked={notifyInFeed} /><span>Notify in Feed</span></label>
  </div>

  <!-- Footer -->
  <div class="wfd-foot">
    <button class="btn-ghost" onclick={doCancel} disabled={status === 'done' || status === 'failed'}>CANCEL</button>
    <button class="btn-ghost" onclick={runInBg}>RUN IN BG</button>
    {#if status === 'done' && dashboardId && project_slug}
      <a class="btn-primary" href="{base}/project/{project_slug}/studio/{dashboardId}">OPEN DASHBOARD ↗</a>
    {/if}
  </div>
</aside>

<style>
  .wfd-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.25);
    z-index: 8999;
  }
  .wfd {
    position: fixed; top: 0; right: 0; bottom: 0;
    width: 480px; max-width: 95vw;
    background: var(--pw-surface, #faf9f5);
    border-left: 1px solid var(--pw-border, #e7e3da);
    box-shadow: -8px 0 24px rgba(0,0,0,0.15);
    z-index: 9000;
    display: flex; flex-direction: column;
    animation: wfd-slide 0.18s ease-out;
  }
  @keyframes wfd-slide { from { transform: translateX(100%); } to { transform: translateX(0); } }

  .wfd-cli {
    background: #1a1614; color: #e8e3d6;
    padding: 14px 16px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    border-bottom: 1px solid #2a2522;
    min-height: 160px;
  }
  .cli-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; line-height: 1.6; }
  .cli-row.dim { color: #a59f92; font-size: 11px; }
  .cli-prompt { color: var(--pw-accent, #c96342); font-weight: 700; }
  .cli-cmd { color: #fcd34d; }
  .cli-wf { color: #e8e3d6; }
  .cli-sep { color: #6b6760; }
  .status-badge {
    margin-left: auto;
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
    padding: 2px 8px; border: 1px solid; border-radius: 0;
  }
  .cli-progress { margin-top: 8px; }
  .cli-bar { color: var(--pw-accent, #c96342); letter-spacing: 1px; }
  .cli-err { margin-top: 6px; color: #fca5a5; }

  .wfd-steps {
    flex: 1; overflow-y: auto;
    padding: 12px 16px;
    background: var(--pw-bg-alt, #f1ede4);
  }
  .empty {
    text-align: center; color: var(--pw-muted, #87837a);
    font-family: ui-monospace, monospace; font-size: 11px;
    padding: 24px;
  }
  .step-row {
    display: grid;
    grid-template-columns: 18px 1fr auto;
    gap: 8px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--pw-border-soft, #efeae0);
    font-size: 12px;
    align-items: center;
  }
  .step-row:last-child { border-bottom: none; }
  .step-glyph { font-family: ui-monospace, monospace; font-weight: 700; color: var(--pw-muted, #87837a); }
  .step-row.running .step-glyph { color: var(--pw-accent, #c96342); animation: pulse 1.2s ease-in-out infinite; }
  .step-row.done .step-glyph { color: #16a34a; }
  .step-row.failed .step-glyph { color: #dc2626; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.45; } }
  .step-name { color: var(--pw-ink, #2c2a26); }
  .step-meta { font-family: ui-monospace, monospace; font-size: 11px; color: var(--pw-muted, #87837a); display: inline-flex; gap: 4px; align-items: center; }
  .step-meta .sep { color: var(--pw-border, #e7e3da); }
  .err-inline { color: #dc2626; }

  .wfd-options {
    padding: 10px 16px;
    border-top: 1px solid var(--pw-border, #e7e3da);
    background: var(--pw-surface, #faf9f5);
    display: flex; flex-direction: column; gap: 6px;
  }
  .opt { display: inline-flex; gap: 8px; align-items: center; font-size: 12px; cursor: pointer; }

  .wfd-foot {
    padding: 12px 16px;
    border-top: 1px solid var(--pw-border, #e7e3da);
    background: var(--pw-bg-alt, #f1ede4);
    display: flex; gap: 8px; justify-content: flex-end; align-items: center;
  }
  .btn-ghost {
    font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    padding: 6px 12px; background: transparent; color: var(--pw-ink, #2c2a26);
    border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--r-sm, 6px);
    cursor: pointer;
  }
  .btn-ghost:hover:not(:disabled) { background: var(--pw-bg, #faf9f5); }
  .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary {
    font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    padding: 6px 12px; background: var(--pw-accent, #c96342); color: #fff;
    border: 1px solid var(--pw-accent, #c96342); border-radius: var(--r-sm, 6px);
    cursor: pointer; text-decoration: none;
  }
  .btn-primary:hover { filter: brightness(0.95); }
</style>
