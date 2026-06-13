<script lang="ts">
  // DeepDeckPanel — 6-stage research-then-present pipeline UI.
  // SSE-streamed from POST /api/slides/deep-build.
  // On stage 6 done → calls onComplete(pres_id) to hand off to SlidesPanel.
  import { onMount, onDestroy } from 'svelte';

  let {
    projectSlug,
    agentName,
    messages,
    sessionId,
    onComplete,
    onClose,
  }: {
    projectSlug: string;
    agentName: string;
    messages: { role: string; content: string }[];
    sessionId?: number | null;
    onComplete?: (presId: number) => void;
    onClose?: () => void;
  } = $props();

  type StageKey = 'ingest' | 'gaps' | 'plan' | 'approval' | 'execute' | 'synthesize' | 'build' | 'critique' | 'codegen' | 'render';
  const stageOrder: StageKey[] = ['ingest', 'gaps', 'plan', 'approval', 'execute', 'synthesize', 'build', 'critique', 'codegen', 'render'];
  const stageLabel: Record<StageKey, string> = {
    ingest: 'Ingest chat + persona',
    gaps: 'Identify analysis gaps',
    plan: 'Plan SQL queries',
    approval: 'Outline approval',
    execute: 'Execute queries',
    synthesize: 'Synthesize insights',
    build: 'Build deck',
    critique: 'Slide critic review',
    codegen: 'Generate pptxgenjs spec',
    render: 'Render rich .pptx (Node)',
  };

  let stageStatus: Record<StageKey, 'pending'|'running'|'waiting'|'done'|'error'> = $state({
    ingest: 'pending', gaps: 'pending', plan: 'pending', approval: 'pending',
    execute: 'pending', synthesize: 'pending', build: 'pending',
    critique: 'pending', codegen: 'pending', render: 'pending',
  });
  let stageMessage: Record<StageKey, string> = $state({
    ingest: '', gaps: '', plan: '', approval: '', execute: '', synthesize: '', build: '',
    critique: '', codegen: '', render: '',
  });
  let approvalData: { plan: any[]; gaps: any[]; run_id: number } | null = $state(null);
  // Track which gap indices the user wants to keep (default: all)
  let keptGapIndices: Set<number> = $state(new Set());
  let showEditModal: boolean = $state(false);
  let approving: boolean = $state(false);
  let waitForApproval: boolean = $state(true);  // user toggle on launch screen
  let liveData: any = $state(null);   // shown in preview box during execute
  let errorText: string = $state('');
  let started: boolean = $state(false);
  let finished: boolean = $state(false);
  let abortCtrl: AbortController | null = null;
  let presId: number | null = $state(null);
  let audience: string = $state('');  // '' = standard, 'exec' | 'team' | 'external' | 'board'
  let critique: boolean = $state(true);

  const audienceOptions = [
    { value: '', label: 'Standard (8 slides)' },
    { value: 'exec', label: 'Exec (5 slides, headlines)' },
    { value: 'team', label: 'Team (10 slides, detailed)' },
    { value: 'external', label: 'External (8, no PII)' },
    { value: 'board', label: 'Board (12 + appendix)' },
  ];

  function pctProgress(): number {
    const total = stageOrder.length;
    const done = stageOrder.filter(s => stageStatus[s] === 'done').length;
    const running = stageOrder.some(s => stageStatus[s] === 'running');
    return Math.round((done + (running ? 0.5 : 0)) / total * 100);
  }

  async function start() {
    if (started) return;
    started = true;
    abortCtrl = new AbortController();
    const token = localStorage.getItem('dash_token') || '';

    try {
      const res = await fetch('/api/slides/deep-build', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          project_slug: projectSlug,
          agent_name: agentName,
          messages: messages.slice(-30).map(m => ({ role: m.role, content: m.content })),
          session_id: sessionId,
          config: { audience: audience || null, critique, wait_for_approval: waitForApproval },
        }),
        signal: abortCtrl.signal,
      });

      if (!res.ok || !res.body) {
        errorText = `HTTP ${res.status}`;
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const chunk = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (!chunk.startsWith('data:')) continue;
          const payload = chunk.slice(5).trim();
          if (!payload) continue;
          try {
            const evt = JSON.parse(payload);
            handleEvent(evt);
          } catch {}
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        errorText = e?.message || String(e);
      }
    } finally {
      finished = true;
    }
  }

  function handleEvent(evt: any) {
    const stage = evt.stage as StageKey;
    if (stage === 'error') {
      errorText = evt.message || 'pipeline failed';
      return;
    }
    if (!stageOrder.includes(stage)) return;

    if (evt.status === 'waiting' && stage === 'approval') {
      stageStatus[stage] = 'waiting';
      stageMessage[stage] = evt.message || '';
      approvalData = evt.data || null;
      if (approvalData?.plan) {
        keptGapIndices = new Set(approvalData.plan.map((_: any, i: number) => i));
      }
      return;
    }
    if (evt.status === 'running') {
      stageStatus[stage] = 'running';
      stageMessage[stage] = evt.message || '';
      if (stage === 'execute' && evt.data?.rows_preview) {
        liveData = evt.data;
      }
    } else if (evt.status === 'done') {
      stageStatus[stage] = 'done';
      stageMessage[stage] = evt.message || '';
      if (stage === 'approval') {
        approvalData = null;  // hide preview, resume pipeline UI
      }
      if (stage === 'execute' && evt.data?.executed) {
        liveData = { executed: evt.data.executed };
      }
      if (stage === 'build' && evt.data?.pres_id) {
        presId = evt.data.pres_id;
      }
    } else if (evt.status === 'error' || evt.status === 'failed') {
      stageStatus[stage] = 'error';
      stageMessage[stage] = evt.message || '';
    }
  }

  async function approve() {
    if (!approvalData) return;
    approving = true;
    try {
      const token = localStorage.getItem('dash_token') || '';
      const kept = Array.from(keptGapIndices).sort((a, b) => a - b);
      const res = await fetch(`/api/slides/deep-runs/${approvalData.run_id}/approve`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ kept_gap_indices: kept }),
      });
      if (!res.ok) {
        errorText = `approve HTTP ${res.status}`;
      }
    } catch (e: any) {
      errorText = e?.message || String(e);
    } finally {
      approving = false;
      showEditModal = false;
    }
  }

  function toggleGap(i: number) {
    const next = new Set(keptGapIndices);
    if (next.has(i)) next.delete(i); else next.add(i);
    keptGapIndices = next;
  }

  function handoff() {
    if (presId !== null && onComplete) onComplete(presId);
  }

  function stop() {
    if (abortCtrl) abortCtrl.abort();
    if (onClose) onClose();
  }

  // Don't auto-start — wait for user to pick audience + click START
  onDestroy(() => { if (abortCtrl) abortCtrl.abort(); });
</script>

<div class="dd-panel">
  <header class="dd-header">
    <div class="dd-title">
      <span class="dd-icon">🔬</span>
      <div>
        <h3>Deep Presentation</h3>
        <div class="dd-meta">6-stage research + synthesis pipeline</div>
      </div>
    </div>
    <div class="dd-actions">
      {#if presId !== null && finished}
        <button class="dd-btn-primary" onclick={handoff}>OPEN DECK →</button>
      {/if}
      <button class="dd-btn-ghost" onclick={stop}>
        {finished ? 'CLOSE' : 'STOP'}
      </button>
    </div>
  </header>

  {#if !started}
    <div class="dd-launch">
      <label class="dd-label">
        <span>Audience</span>
        <select bind:value={audience}>
          {#each audienceOptions as o}
            <option value={o.value}>{o.label}</option>
          {/each}
        </select>
      </label>
      <label class="dd-toggle">
        <input type="checkbox" bind:checked={critique} />
        <span>Slide critic review (2-pass, ~$0.04)</span>
      </label>
      <label class="dd-toggle">
        <input type="checkbox" bind:checked={waitForApproval} />
        <span>Pause for outline approval (saves ~$0.13 if rejected)</span>
      </label>
      <button class="dd-btn-primary dd-btn-launch" onclick={start}>▶ START PIPELINE</button>
    </div>
  {/if}

  <div class="dd-progress">
    <div class="dd-bar"><div class="dd-bar-fill" style="width:{pctProgress()}%"></div></div>
    <div class="dd-pct">{pctProgress()}%</div>
  </div>

  <div class="dd-stages">
    {#each stageOrder as s, i}
      <div class="dd-stage {stageStatus[s]}">
        <span class="dd-stage-glyph">
          {#if stageStatus[s] === 'done'}✓
          {:else if stageStatus[s] === 'running'}●
          {:else if stageStatus[s] === 'waiting'}⏸
          {:else if stageStatus[s] === 'error'}✗
          {:else}○{/if}
        </span>
        <div class="dd-stage-body">
          <div class="dd-stage-label">Stage {i + 1} · {stageLabel[s]}</div>
          {#if stageMessage[s]}
            <div class="dd-stage-msg">{stageMessage[s]}</div>
          {/if}
        </div>
      </div>
    {/each}
  </div>

  {#if approvalData && stageStatus.approval === 'waiting'}
    <div class="dd-approval">
      <div class="dd-approval-hdr">
        <strong>Outline ready — approve before spending ~$0.13 on research</strong>
        <div class="dd-approval-meta">
          {approvalData.plan?.length || 0} queries planned · {approvalData.gaps?.length || 0} gaps identified
        </div>
      </div>
      {#if showEditModal}
        <div class="dd-approval-edit">
          <div class="dd-approval-edit-hdr">Uncheck any gaps to skip</div>
          {#each (approvalData.plan || []) as p, i}
            <label class="dd-approval-item">
              <input
                type="checkbox"
                checked={keptGapIndices.has(i)}
                onchange={() => toggleGap(i)}
              />
              <div class="dd-approval-item-body">
                <div class="dd-approval-q">{i + 1}. {p.question}</div>
                {#if p.expected_shape}
                  <div class="dd-approval-shape">{p.expected_shape}</div>
                {/if}
              </div>
            </label>
          {/each}
        </div>
      {:else}
        <ul class="dd-approval-list">
          {#each (approvalData.plan || []) as p, i}
            <li>{i + 1}. {p.question}</li>
          {/each}
        </ul>
      {/if}
      <div class="dd-approval-actions">
        <button class="dd-btn-primary" disabled={approving || keptGapIndices.size === 0} onclick={approve}>
          {approving ? 'APPROVING…' : `✓ APPROVE (${keptGapIndices.size})`}
        </button>
        <button class="dd-btn-ghost" onclick={() => (showEditModal = !showEditModal)}>
          {showEditModal ? 'DONE EDITING' : '✎ EDIT'}
        </button>
      </div>
    </div>
  {/if}

  {#if liveData?.executed}
    <div class="dd-live">
      <div class="dd-live-hdr">QUERY RESULTS ({liveData.executed.filter((e: any) => e.ok).length}/{liveData.executed.length} ok)</div>
      {#each liveData.executed.slice(0, 6) as e, i}
        <div class="dd-live-row">
          <div class="dd-live-q">
            <span class="dd-live-glyph">{e.ok ? '✓' : '✗'}</span>
            <span>{i + 1}. {e.question}</span>
            {#if e.ok}<span class="dd-live-count">{e.row_count} rows</span>{/if}
          </div>
          {#if e.ok && e.rows_preview && e.rows_preview.length > 0}
            <div class="dd-live-preview">
              {JSON.stringify(e.rows_preview[0]).slice(0, 180)}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if errorText}
    <div class="dd-error">⚠ {errorText}</div>
  {/if}
</div>

<style>
  .dd-panel {
    position: fixed; top: 56px; right: 0; bottom: 0;
    width: 45%; min-width: 480px; max-width: 720px;
    background: var(--pw-surface, #faf9f5);
    border-left: 1px solid var(--pw-border, #e5e1d4);
    display: flex; flex-direction: column;
    z-index: 9100;
    box-shadow: -4px 0 16px rgba(0,0,0,0.06);
    overflow-y: auto;
  }
  @media (max-width: 900px) {
    .dd-panel { width: 100%; min-width: 0; }
  }
  .dd-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--pw-border, #e5e1d4);
    background: var(--pw-bg-alt, #f3efe4);
  }
  .dd-title { display: flex; gap: 12px; align-items: center; }
  .dd-icon { font-size: 22px; }
  .dd-title h3 { margin: 0; font-size: 15px; font-weight: 600; }
  .dd-meta { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 2px; }
  .dd-actions { display: flex; gap: 8px; }
  .dd-btn-primary {
    background: var(--pw-accent, #c96342); color: #fff;
    border: none; padding: 8px 14px; font-size: 12px; font-weight: 600;
    cursor: pointer; border-radius: var(--pw-radius-sm);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .dd-btn-ghost {
    background: transparent; border: 1px solid var(--pw-border, #e5e1d4);
    padding: 8px 12px; font-size: 12px; cursor: pointer; border-radius: var(--pw-radius-sm);
    color: var(--pw-ink, #2c2a26);
  }
  .dd-launch { padding: 16px; border-bottom: 1px solid var(--pw-border, #e5e1d4); }
  .dd-label { display: flex; flex-direction: column; gap: 4px; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #6b6557);
    font-weight: 600; margin-bottom: 12px; }
  .dd-label select { border: 1px solid var(--pw-border, #e5e1d4); border-radius: var(--pw-radius-sm);
    padding: 8px; font-size: 13px; font-family: inherit; background: #fff;
    color: var(--pw-ink, #2c2a26); text-transform: none; letter-spacing: 0; font-weight: 400; }
  .dd-btn-launch { width: 100%; padding: 10px; font-size: 13px; margin-top: 8px; }
  .dd-toggle { display: flex; gap: 8px; align-items: center; font-size: 12px;
    color: var(--pw-ink, #2c2a26); margin-bottom: 6px; cursor: pointer; }
  .dd-toggle input { cursor: pointer; }
  .dd-progress { display: flex; gap: 12px; align-items: center; padding: 12px 16px; }
  .dd-bar { flex: 1; height: 4px; background: var(--pw-bg-alt, #ebe5d6); border-radius: var(--pw-radius-sm); overflow: hidden; }
  .dd-bar-fill { height: 100%; background: linear-gradient(90deg, var(--pw-accent, #c96342), #f0a030); transition: width 0.4s ease; }
  .dd-pct { font-size: 11px; color: var(--pw-ink-soft, #6b6557); font-variant-numeric: tabular-nums; min-width: 36px; text-align: right; }
  .dd-stages { display: flex; flex-direction: column; gap: 8px; padding: 4px 16px 12px; }
  .dd-stage { display: flex; gap: 10px; align-items: flex-start; padding: 8px 10px;
    background: #fff; border: 1px solid var(--pw-border, #e5e1d4); border-radius: var(--pw-radius-sm); }
  .dd-stage.running { border-color: var(--pw-accent, #c96342); box-shadow: 0 0 0 3px rgba(201,99,66,0.08); }
  .dd-stage.done { background: #fafaf4; }
  .dd-stage.error { border-color: #b94a3d; background: #fff5f3; }
  .dd-stage-glyph { font-weight: 700; min-width: 16px; }
  .dd-stage.done .dd-stage-glyph { color: #4a8a3a; }
  .dd-stage.running .dd-stage-glyph { color: var(--pw-accent, #c96342); }
  .dd-stage.error .dd-stage-glyph { color: #b94a3d; }
  .dd-stage-label { font-size: 12px; font-weight: 600; color: var(--pw-ink, #2c2a26); }
  .dd-stage-msg { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 2px; }
  .dd-live {
    margin: 0 16px 12px;
    background: #1a1614; color: #e8e3d6;
    border-radius: var(--pw-radius-sm); padding: 10px 12px; font-family: ui-monospace, monospace; font-size: 11px;
  }
  .dd-live-hdr { font-weight: 700; letter-spacing: 0.05em; margin-bottom: 6px; color: #f0a030; }
  .dd-live-row { padding: 4px 0; border-bottom: 1px solid #2a2520; }
  .dd-live-row:last-child { border: none; }
  .dd-live-q { display: flex; gap: 6px; align-items: baseline; }
  .dd-live-glyph { color: #4a8a3a; font-weight: 700; min-width: 10px; }
  .dd-live-count { margin-left: auto; color: #888; font-size: 10px; }
  .dd-live-preview { color: #aaa; margin-top: 2px; padding-left: 16px; font-size: 10px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dd-error { margin: 0 16px 12px; padding: 8px 12px; background: #fff5f3; color: #b94a3d;
    border-radius: var(--pw-radius-sm); font-size: 12px; }
  .dd-stage.waiting { border-color: #f0a030; background: #fff8eb; }
  .dd-stage.waiting .dd-stage-glyph { color: #c97a10; }
  .dd-approval { margin: 0 16px 12px; padding: 12px 14px; background: #fff8eb;
    border: 1px solid #f0c060; border-radius: var(--pw-radius-sm); }
  .dd-approval-hdr { font-size: 13px; color: #2c2a26; }
  .dd-approval-meta { font-size: 11px; color: var(--pw-ink-soft, #6b6557);
    margin-top: 4px; }
  .dd-approval-list { margin: 10px 0; padding-left: 18px; font-size: 12px;
    color: var(--pw-ink, #2c2a26); }
  .dd-approval-list li { margin: 3px 0; }
  .dd-approval-edit { margin: 10px 0; padding: 10px; background: #fff;
    border-radius: var(--pw-radius-sm); border: 1px solid var(--pw-border, #e5e1d4); }
  .dd-approval-edit-hdr { font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.05em; font-weight: 600; color: var(--pw-ink-soft, #6b6557);
    margin-bottom: 8px; }
  .dd-approval-item { display: flex; gap: 8px; padding: 6px 4px; align-items: flex-start;
    cursor: pointer; border-radius: var(--pw-radius-sm); }
  .dd-approval-item:hover { background: var(--pw-bg-alt, #f3efe4); }
  .dd-approval-item input { margin-top: 2px; cursor: pointer; }
  .dd-approval-item-body { flex: 1; }
  .dd-approval-q { font-size: 12px; color: var(--pw-ink, #2c2a26); }
  .dd-approval-shape { font-size: 10px; color: var(--pw-ink-soft, #6b6557); margin-top: 2px; }
  .dd-approval-actions { display: flex; gap: 8px; margin-top: 8px; }
</style>
