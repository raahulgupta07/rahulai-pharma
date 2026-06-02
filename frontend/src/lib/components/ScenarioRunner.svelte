<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { simRun, simGet, simList, type SimRun } from '$lib/api';

  const SEED_TABLE_OPTIONS = ['fuel_stock', 'sites', 'sales', 'customers', 'orders'];
  const HORIZONS = ['1d', '7d', '30d', '90d'];
  const ACTOR_OPTIONS = [
    { label: 'Just me', value: 1 },
    { label: 'Team', value: 10 },
    { label: 'Org', value: 100 }
  ];

  let scenario = $state('');
  let horizon = $state('7d');
  let actors = $state(1);
  let seedTables = $state<string[]>([]);

  let currentRun = $state<SimRun | null>(null);
  let running = $state(false);
  let elapsedMs = $state(0);
  let runStartedAt = $state(0);
  let history = $state<SimRun[]>([]);
  let errorMsg = $state('');

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let tickTimer: ReturnType<typeof setInterval> | null = null;

  const charCount = $derived(scenario.length);
  const canRun = $derived(scenario.trim().length > 0 && !running);
  const reportParagraphs = $derived(
    currentRun?.report_md ? currentRun.report_md.split(/\n\n+/).filter((p) => p.trim()) : []
  );

  function toggleSeedTable(t: string) {
    seedTables = seedTables.includes(t) ? seedTables.filter((x) => x !== t) : [...seedTables, t];
  }

  function clearPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
  }

  async function refreshHistory() {
    try {
      const all = await simList();
      history = all.slice(0, 5);
    } catch (e) {
      // ignore
    }
  }

  async function pollOnce(simId: string) {
    try {
      const r = await simGet(simId);
      currentRun = r;
      if (r.status === 'done' || r.status === 'failed') {
        running = false;
        clearPoll();
        refreshHistory();
      }
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : 'Poll failed';
    }
  }

  async function startRun() {
    if (!canRun) return;
    errorMsg = '';
    running = true;
    elapsedMs = 0;
    runStartedAt = Date.now();
    try {
      const { sim_id } = await simRun(scenario, horizon, seedTables, actors);
      currentRun = {
        sim_id, status: 'queued', progress: 0, scenario,
        horizon, created_at: new Date().toISOString()
      };
      tickTimer = setInterval(() => { elapsedMs = Date.now() - runStartedAt; }, 200);
      pollTimer = setInterval(() => pollOnce(sim_id), 1500);
      pollOnce(sim_id);
    } catch (e) {
      running = false;
      errorMsg = e instanceof Error ? e.message : 'Run failed';
    }
  }

  function rerun() {
    if (currentRun) {
      scenario = currentRun.scenario;
      if (currentRun.horizon) horizon = currentRun.horizon;
      startRun();
    }
  }

  function exportReport() {
    if (!currentRun?.report_md) return;
    const blob = new Blob([currentRun.report_md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `simulation-${currentRun.sim_id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function loadHistoryRun(sim: SimRun) {
    try {
      currentRun = await simGet(sim.sim_id);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : 'Load failed';
    }
  }

  function fmtElapsed(ms: number): string {
    const s = Math.floor(ms / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  }

  function fmtTs(iso: string): string {
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  }

  onMount(() => { refreshHistory(); });
  onDestroy(() => { clearPoll(); });
</script>

<div class="sr-wrap">
  <!-- Scenario input -->
  <section class="sr-card">
    <div class="sr-label">Scenario</div>
    <textarea
      class="sr-textarea"
      rows="5"
      placeholder="Describe what-if scenario..."
      bind:value={scenario}
    ></textarea>
    <div class="sr-counter">{charCount} chars</div>
  </section>

  <!-- Controls -->
  <section class="sr-card">
    <div class="sr-controls">
      <div class="sr-ctrl">
        <div class="sr-label">Horizon</div>
        <select class="sr-select" bind:value={horizon}>
          {#each HORIZONS as h}<option value={h}>{h}</option>{/each}
        </select>
      </div>

      <div class="sr-ctrl">
        <div class="sr-label">Actors</div>
        <div class="sr-pills">
          {#each ACTOR_OPTIONS as opt}
            <button
              type="button"
              class="sr-pill"
              class:active={actors === opt.value}
              onclick={() => (actors = opt.value)}
            >{opt.label} ({opt.value})</button>
          {/each}
        </div>
      </div>

      <div class="sr-ctrl sr-ctrl-wide">
        <div class="sr-label">Seed tables</div>
        <div class="sr-chips">
          {#each SEED_TABLE_OPTIONS as t}
            <button
              type="button"
              class="sr-chip"
              class:active={seedTables.includes(t)}
              onclick={() => toggleSeedTable(t)}
            >{t}</button>
          {/each}
        </div>
      </div>
    </div>
  </section>

  <!-- Run button -->
  <button class="sr-run" disabled={!canRun} onclick={startRun}>
    {running ? 'RUNNING...' : '▶ RUN SIMULATION'}
  </button>

  {#if errorMsg}
    <div class="sr-error">{errorMsg}</div>
  {/if}

  <!-- Live run panel -->
  {#if currentRun}
    <section class="sr-card">
      <div class="sr-run-head">
        <div class="sr-label">Run · {currentRun.sim_id.slice(0, 8)}</div>
        <span class="sr-status sr-status-{currentRun.status}">{currentRun.status}</span>
      </div>
      <div class="sr-progress-bar">
        <div class="sr-progress-fill" style="width: {Math.max(0, Math.min(100, currentRun.progress || 0))}%"></div>
      </div>
      <div class="sr-run-meta">
        <span>{currentRun.progress || 0}%</span>
        <span>elapsed {fmtElapsed(elapsedMs)}</span>
      </div>
      {#if currentRun.error}
        <div class="sr-error">{currentRun.error}</div>
      {/if}
    </section>
  {/if}

  <!-- Report block -->
  {#if currentRun?.status === 'done'}
    <section class="sr-card">
      <div class="sr-report-head">
        <div class="sr-label">Report</div>
        <div class="sr-report-actions">
          <button class="sr-btn-sm" onclick={exportReport}>Export</button>
          <button class="sr-btn-sm" onclick={rerun}>Re-run</button>
        </div>
      </div>
      {#if reportParagraphs.length > 0}
        <div class="sr-report">
          {#each reportParagraphs as p}<p>{p}</p>{/each}
        </div>
      {:else}
        <div class="sr-muted">No report content.</div>
      {/if}
    </section>
  {/if}

  <!-- History -->
  <section class="sr-card">
    <div class="sr-label">Recent runs</div>
    {#if history.length === 0}
      <div class="sr-muted">No runs yet.</div>
    {:else}
      <ul class="sr-history">
        {#each history as h}
          <li>
            <button class="sr-history-row" onclick={() => loadHistoryRun(h)}>
              <span class="sr-history-text">{h.scenario.slice(0, 60)}{h.scenario.length > 60 ? '…' : ''}</span>
              <span class="sr-history-meta">
                <span class="sr-status sr-status-{h.status}">{h.status}</span>
                <span class="sr-history-ts">{fmtTs(h.created_at)}</span>
              </span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

<style>
  .sr-wrap {
    display: flex; flex-direction: column; gap: 16px;
    color: var(--pw-ink);
    font-family: var(--pw-sans);
  }
  .sr-card {
    background: var(--pw-surface-warm, #f4f3ee);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 16px;
  }
  .sr-label {
    font-size: 11.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--pw-muted);
    font-weight: 600;
    margin-bottom: 8px;
  }
  .sr-textarea {
    width: 100%;
    background: var(--pw-surface);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 12px;
    font-family: var(--pw-sans);
    font-size: 11px;
    color: var(--pw-ink);
    resize: vertical;
  }
  .sr-textarea:focus { outline: 2px solid var(--pw-accent); outline-offset: -2px; }
  .sr-counter {
    margin-top: 6px;
    font-size: 11px;
    color: var(--pw-dim);
    text-align: right;
  }
  .sr-controls {
    display: flex; flex-wrap: wrap; gap: 16px;
  }
  .sr-ctrl { flex: 1 1 180px; min-width: 180px; }
  .sr-ctrl-wide { flex: 2 1 320px; }
  .sr-select {
    width: 100%;
    background: var(--pw-surface);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 8px 10px;
    font-size: 11px;
    color: var(--pw-ink);
  }
  .sr-pills, .sr-chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .sr-pill, .sr-chip {
    background: var(--pw-surface);
    border: 1px solid var(--pw-border);
    border-radius: 0;
    padding: 6px 12px;
    font-size: 11px;
    color: var(--pw-ink);
    cursor: pointer;
    transition: all 0.15s;
  }
  .sr-pill:hover, .sr-chip:hover { border-color: var(--pw-accent); }
  .sr-pill.active, .sr-chip.active {
    background: var(--pw-accent);
    border-color: var(--pw-accent);
    color: #fff;
    font-weight: 600;
  }
  .sr-run {
    width: 100%;
    background: var(--pw-accent);
    color: #fff;
    border: none;
    border-radius: 0;
    padding: 16px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: pointer;
    transition: background 0.15s;
  }
  .sr-run:hover:not(:disabled) { background: var(--pw-accent-strong); }
  .sr-run:disabled {
    background: var(--pw-border-strong);
    color: var(--pw-muted);
    cursor: not-allowed;
  }
  .sr-error {
    background: var(--pw-error-soft);
    border: 1px solid var(--pw-error);
    color: var(--pw-error);
    padding: 10px 12px;
    border-radius: 0;
    font-size: 11px;
  }
  .sr-run-head, .sr-report-head {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
  }
  .sr-status {
    font-size: 10.5px; letter-spacing: 0.05em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 0; font-weight: 600;
  }
  .sr-status-queued { background: var(--pw-bg-alt); color: var(--pw-muted); }
  .sr-status-running { background: rgba(201,99,66,0.14); color: var(--pw-accent); }
  .sr-status-done { background: var(--pw-success-soft); color: var(--pw-success); }
  .sr-status-failed { background: var(--pw-error-soft); color: var(--pw-error); }
  .sr-progress-bar {
    height: 8px; background: var(--pw-bg-alt);
    border-radius: 0; overflow: hidden;
  }
  .sr-progress-fill {
    height: 100%; background: var(--pw-accent);
    transition: width 0.3s ease;
  }
  .sr-run-meta {
    display: flex; justify-content: space-between; margin-top: 6px;
    font-size: 11.5px; color: var(--pw-muted);
  }
  .sr-report-actions { display: flex; gap: 6px; }
  .sr-btn-sm {
    background: var(--pw-surface); border: 1px solid var(--pw-border);
    border-radius: 0; padding: 6px 12px; font-size: 11px;
    color: var(--pw-ink); cursor: pointer;
  }
  .sr-btn-sm:hover { border-color: var(--pw-accent); color: var(--pw-accent); }
  .sr-report {
    background: var(--pw-surface); border: 1px solid var(--pw-border);
    border-radius: 0; padding: 14px; font-size: 13.5px; line-height: 1.6;
    white-space: pre-wrap; color: var(--pw-ink);
  }
  .sr-report p { margin: 0 0 12px 0; }
  .sr-report p:last-child { margin-bottom: 0; }
  .sr-muted { color: var(--pw-muted); font-size: 11px; }
  .sr-history { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .sr-history-row {
    width: 100%;
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
    background: var(--pw-surface); border: 1px solid var(--pw-border);
    border-radius: 0; padding: 10px 12px; cursor: pointer;
    text-align: left; color: var(--pw-ink);
  }
  .sr-history-row:hover { border-color: var(--pw-accent); }
  .sr-history-text { font-size: 11px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sr-history-meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .sr-history-ts { font-size: 11px; color: var(--pw-dim); }
</style>
