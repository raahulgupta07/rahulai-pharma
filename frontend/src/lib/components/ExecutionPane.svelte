<script lang="ts">
  interface Step {
    id: string;
    name: string;
    status: 'pending' | 'running' | 'done' | 'failed';
    duration_ms?: number;
    rows?: number;
    sql?: string;
    error?: string;
  }

  let {
    events = [],
    status = 'queued',
    elapsed = 0,
    stepsDone = 0,
    stepsTotal = 0,
    workflowName = '',
    onCancel = () => {},
    onReRun = () => {},
  }: {
    events?: any[];
    status?: string;
    elapsed?: number;
    stepsDone?: number;
    stepsTotal?: number;
    workflowName?: string;
    onCancel?: () => void;
    onReRun?: () => void;
  } = $props();

  let expandedStepId = $state<string | null>(null);
  let stayOnRun = $state(true);

  // Init localStorage
  $effect(() => {
    if (typeof window !== 'undefined') {
      try {
        const v = localStorage.getItem('dash.wf.stayOnRun');
        if (v !== null) stayOnRun = v === '1';
      } catch {}
    }
  });

  function persistStay() {
    if (typeof window === 'undefined') return;
    try { localStorage.setItem('dash.wf.stayOnRun', stayOnRun ? '1' : '0'); } catch {}
  }

  // Derive steps from events
  let steps = $derived.by(() => {
    const byId = new Map<string, Step>();
    for (const ev of events) {
      const t = ev?.type || ev?.event;
      if (t !== 'step_start' && t !== 'step_done' && t !== 'step_error') continue;
      const id = String(ev?.step_id ?? ev?.id ?? ev?.name ?? ev?.step ?? 'step');
      const name = ev?.name || ev?.step || ev?.title || id;
      const existing = byId.get(id) || { id, name, status: 'pending' as const };
      if (t === 'step_start') {
        existing.status = 'running';
        existing.name = name;
        if (ev?.sql) existing.sql = String(ev.sql);
      } else if (t === 'step_done') {
        existing.status = 'done';
        if (ev?.duration_ms != null) existing.duration_ms = Number(ev.duration_ms);
        if (ev?.rows != null) existing.rows = Number(ev.rows);
        if (ev?.row_count != null && existing.rows == null) existing.rows = Number(ev.row_count);
        if (ev?.sql) existing.sql = String(ev.sql);
      } else if (t === 'step_error') {
        existing.status = 'failed';
        existing.error = ev?.error || ev?.message;
        if (ev?.duration_ms != null) existing.duration_ms = Number(ev.duration_ms);
      }
      byId.set(id, existing);
    }
    return Array.from(byId.values());
  });

  function glyph(s: Step): string {
    if (s.status === 'done') return '✓';
    if (s.status === 'running') return '●';
    if (s.status === 'failed') return '✗';
    return '○';
  }

  function fmtMs(ms?: number): string {
    if (ms == null) return '';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function progressBar(): string {
    const total = Math.max(stepsTotal || steps.length || 1, 1);
    const done = Math.min(stepsDone, total);
    const W = 10;
    const filled = Math.round((done / total) * W);
    return '▓'.repeat(filled) + '░'.repeat(W - filled);
  }

  function statusDotColor(): string {
    if (status === 'done') return '#16a34a';
    if (status === 'failed' || status === 'cancelled') return '#dc2626';
    if (status === 'running') return '#c96342';
    return '#a06000';
  }

  function toggleStep(id: string) {
    expandedStepId = expandedStepId === id ? null : id;
  }

  function copySQL(sql: string, e: Event) {
    e.stopPropagation();
    try { navigator.clipboard?.writeText(sql); } catch {}
  }
</script>

<div class="ep-root">
  <!-- Dark top strip -->
  <div class="ep-cli">
    <div class="cli-line">RUN{workflowName ? ` · ${workflowName}` : ''}</div>
    <div class="cli-line">
      <span class="bar">{progressBar()}</span>
      <span class="bar-meta">{stepsDone}/{stepsTotal || steps.length || 0} · {elapsed}s</span>
    </div>
    <div class="cli-line">
      <span class="dot" style="background: {statusDotColor()}"></span>
      <span class="cli-status">{status}</span>
    </div>
  </div>

  <!-- Step list -->
  <div class="ep-steps">
    {#if steps.length === 0}
      <div class="ep-empty">waiting for first event…</div>
    {:else}
      {#each steps as s (s.id)}
        <div
          class="step"
          class:running={s.status === 'running'}
          class:done={s.status === 'done'}
          class:failed={s.status === 'failed'}
        >
          <button class="step-row" type="button" onclick={() => toggleStep(s.id)}>
            <span class="step-glyph">{glyph(s)}</span>
            <span class="step-name">{s.name}</span>
            <span class="step-meta">
              {#if s.duration_ms != null}<span>{fmtMs(s.duration_ms)}</span>{/if}
              {#if s.rows != null}<span class="sep">·</span><span>{s.rows} rows</span>{/if}
              {#if s.error}<span class="sep">·</span><span class="err">{s.error}</span>{/if}
            </span>
          </button>
          {#if expandedStepId === s.id && s.sql}
            <div class="step-sql">
              <pre>{s.sql.length > 180 ? s.sql.slice(0, 180) + '…' : s.sql}</pre>
              <button class="copy-btn" type="button" onclick={(e) => copySQL(s.sql || '', e)}>COPY</button>
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>

  <!-- On complete options -->
  <div class="ep-opts">
    <div class="opts-title">on complete:</div>
    <label class="opt">
      <input type="radio" name="onComplete" checked={stayOnRun} onchange={() => { stayOnRun = true; persistStay(); }} />
      <span>stay on this page</span>
    </label>
    <label class="opt">
      <input type="radio" name="onComplete" checked={!stayOnRun} onchange={() => { stayOnRun = false; persistStay(); }} />
      <span>go to workflow library</span>
    </label>
  </div>

  <!-- Action buttons -->
  <div class="ep-actions">
    {#if status === 'running' || status === 'queued'}
      <button class="btn-cancel" type="button" onclick={onCancel}>✕ CANCEL</button>
    {/if}
    {#if status === 'done' || status === 'failed' || status === 'cancelled'}
      <button class="btn-rerun" type="button" onclick={onReRun}>↻ RE-RUN</button>
    {/if}
  </div>
</div>

<style>
  .ep-root {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--pw-bg-alt, #f1ede4);
  }

  .ep-cli {
    background: #1a1614;
    color: #e8e3d6;
    padding: 14px 16px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    line-height: 1.7;
    min-height: 110px;
    flex-shrink: 0;
  }
  .cli-line { display: flex; gap: 8px; align-items: center; }
  .bar { color: var(--pw-accent, #c96342); letter-spacing: 1px; }
  .bar-meta { color: #a59f92; font-size: 11px; margin-left: 6px; }
  .dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
  }
  .cli-status { text-transform: lowercase; color: #e8e3d6; }

  .ep-steps {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
    background: var(--pw-bg-alt, #f1ede4);
  }
  .ep-empty {
    text-align: center;
    color: var(--pw-muted, #87837a);
    font-family: ui-monospace, monospace;
    font-size: 11px;
    padding: 24px;
  }
  .step {
    border-bottom: 1px solid var(--pw-border-soft, #efeae0);
  }
  .step-row {
    width: 100%;
    height: 48px;
    display: grid;
    grid-template-columns: 24px 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 0 16px;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    font: inherit;
  }
  .step-row:hover { background: rgba(0,0,0,0.025); }
  .step-glyph {
    font-family: ui-monospace, monospace;
    font-weight: 700;
    color: var(--pw-muted, #87837a);
    text-align: center;
  }
  .step.running .step-glyph {
    color: var(--pw-accent, #c96342);
    animation: pulse 1.5s ease-in-out infinite;
  }
  .step.done .step-glyph { color: #16a34a; }
  .step.failed .step-glyph { color: #dc2626; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .step-name {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 14px;
    color: var(--pw-ink, #2c2a26);
  }
  .step-meta {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--pw-muted, #87837a);
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .step-meta .sep { color: var(--pw-border, #e7e3da); }
  .step-meta .err { color: #dc2626; }

  .step-sql {
    background: #1a1614;
    color: #e8e3d6;
    padding: 10px 16px;
    display: flex;
    gap: 10px;
    align-items: flex-start;
    border-top: 1px solid #2a2522;
  }
  .step-sql pre {
    flex: 1;
    margin: 0;
    font-family: ui-monospace, monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .copy-btn {
    background: transparent;
    color: #e8e3d6;
    border: 1px solid #4a4642;
    padding: 3px 8px;
    font: inherit;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    cursor: pointer;
    border-radius: 3px;
  }
  .copy-btn:hover { background: #2a2522; }

  .ep-opts {
    padding: 12px 16px;
    border-top: 1px solid var(--pw-border, #e7e3da);
    background: var(--pw-surface, #faf9f5);
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex-shrink: 0;
  }
  .opts-title {
    font-family: ui-monospace, monospace;
    font-size: 10px;
    text-transform: uppercase;
    color: var(--pw-muted, #87837a);
    margin-bottom: 4px;
    letter-spacing: 0.05em;
  }
  .opt {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    font-size: 12px;
    cursor: pointer;
    color: var(--pw-ink, #2c2a26);
  }

  .ep-actions {
    padding: 12px 16px;
    border-top: 1px solid var(--pw-border, #e7e3da);
    background: var(--pw-bg-alt, #f1ede4);
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    flex-shrink: 0;
  }
  .btn-cancel, .btn-rerun {
    font: inherit;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 6px 12px;
    background: transparent;
    border: 1px solid var(--pw-accent, #c96342);
    color: var(--pw-accent, #c96342);
    border-radius: 4px;
    cursor: pointer;
  }
  .btn-cancel:hover, .btn-rerun:hover {
    background: var(--pw-accent, #c96342);
    color: #fff;
  }
</style>
