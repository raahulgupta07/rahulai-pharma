<script lang="ts">
  // Live training-flow visualization for the Data Source dashboard.
  // Stitches:
  //   • TrainingSchematic.svelte — boiler-style horizontal pipeline (10 layers)
  //   • trainingFlowSpec.ts       — static 10-layer / 60-step detail map
  //   • GET /api/projects/{slug}/training-flow — live status + real counts
  //   • GET /api/projects/{slug}/auto-train/log?since=N — live log tail
  // Schematic card click → drills to that layer's step detail below.
  import { onMount, onDestroy } from 'svelte';
  import TrainingSchematic from '$lib/TrainingSchematic.svelte';
  import { FLOW_LAYERS, STORES, TOTAL_STEPS } from '$lib/trainingFlowSpec';

  let { slug = '', autopoll = true } = $props();

  // ── auth (mirror settings/+page.svelte _h) ──────────────────────
  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  // keyword color → hex (schematic wants a real CSS color)
  const CHEX: Record<string, string> = { amber: '#d4930e', cyan: '#0ecad4', coral: '#c96342' };

  type FlowResp = {
    run: { id: number; status: string; current_step: string; started_at?: string; finished_at?: string; stage_progress?: number } | null;
    layers: { idx: number; title: string; color: string; gate: string | null; status: string }[];
    stores: Record<string, number | null>;
    kpis: Record<string, number | null>;
    flags: { engineer: boolean; enrich: boolean };
  };

  let flow = $state<FlowResp | null>(null);
  let logEvents = $state<{ i: number; ts: string; msg: string; table: string }[]>([]);
  let logSince = 0;
  let expanded = $state<number>(-1);      // drilled layer idx (-1 = none)
  let fullView = $state(true);            // schematic-only vs full detail
  let loaded = $state(false);
  let prevStores = $state<Record<string, number>>({});
  let flashStore = $state<Record<string, boolean>>({});
  let logEl = $state<HTMLDivElement | null>(null);

  const status = $derived(flow?.run?.status || 'idle');
  const running = $derived(['running', 'queued', 'finalizing'].includes(status));

  // status per layer idx, from live flow (fallback idle)
  function layerStatus(idx: number): 'idle' | 'running' | 'done' | 'skipped' | 'error' {
    const l = flow?.layers?.find((x) => x.idx === idx);
    const s = (l?.status || 'idle') as any;
    return ['idle', 'running', 'done', 'skipped', 'error'].includes(s) ? s : 'idle';
  }

  function nfmt(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    return n.toLocaleString();
  }

  // representative metric shown on each schematic card
  function layerCount(idx: number): string | undefined {
    const k = flow?.kpis || {};
    const st = flow?.stores || {};
    switch (idx) {
      case 2: return k.tables != null ? `${nfmt(k.tables)} tables` : undefined;
      case 3: return k.qa != null ? `${nfmt(k.qa)} Q&A` : undefined;
      case 4: return k.rels != null ? `${nfmt(k.rels)} links` : undefined;
      case 5: return k.matviews != null ? `${nfmt(k.matviews)} matviews` : undefined;
      case 6: return st.AGE != null ? `${nfmt(st.AGE)} nodes` : undefined;
      case 7: return k.eval_score != null ? `${k.eval_score} eval` : undefined;
      case 8: return k.gaps != null ? `${nfmt(k.gaps)} gaps` : undefined;
      default: return undefined;
    }
  }

  // schematic stages built from spec + live status
  const stages = $derived(
    FLOW_LAYERS.map((L) => ({
      idx: L.idx,
      label: L.short,
      title: L.short,
      status: layerStatus(L.idx),
      color: CHEX[L.color] || '#c96342',
      count: layerCount(L.idx),
    }))
  );

  // KPI tiles
  const KPI_DEFS = [
    { k: 'tables', label: 'Tables' },
    { k: 'rows', label: 'Rows' },
    { k: 'qa', label: 'Q&A pairs' },
    { k: 'rels', label: 'Relationships' },
    { k: 'matviews', label: 'Matviews' },
    { k: 'gaps', label: 'Catalog gaps' },
    { k: 'eval_score', label: 'Eval score' },
  ];

  function onPick(idx: number) {
    expanded = expanded === idx ? -1 : idx;
    fullView = true;
    // scroll the layer detail into view
    requestAnimationFrame(() => {
      const el = document.getElementById(`tf-layer-${idx}`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  async function fetchFlow() {
    try {
      const r = await fetch(`/api/projects/${slug}/training-flow`, { headers: _h() });
      if (!r.ok) return;
      const next: FlowResp = await r.json();
      // detect store changes → flash chip
      const cur: Record<string, number> = {};
      for (const [k, v] of Object.entries(next.stores || {})) if (v != null) cur[k] = v as number;
      const fl: Record<string, boolean> = {};
      for (const k of Object.keys(cur)) if (prevStores[k] !== undefined && prevStores[k] !== cur[k]) fl[k] = true;
      flashStore = fl;
      prevStores = cur;
      flow = next;
      loaded = true;
    } catch {}
  }

  async function fetchLog() {
    try {
      const r = await fetch(`/api/projects/${slug}/auto-train/log?since=${logSince}`, { headers: _h() });
      if (!r.ok) return;
      const d = await r.json();
      if (Array.isArray(d.events) && d.events.length) {
        logEvents = [...logEvents, ...d.events].slice(-300);
        logSince = d.total ?? logSince + d.events.length;
        requestAnimationFrame(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; });
      } else if (typeof d.total === 'number') {
        logSince = d.total;
      }
    } catch {}
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  async function tick() {
    await Promise.all([fetchFlow(), fetchLog()]);
  }
  function startPoll() {
    if (timer) return;
    timer = setInterval(() => {
      // poll fast while running, slow when idle/done
      tick();
      if (timer && !running) { clearInterval(timer); timer = null; slowPoll(); }
    }, 2000);
  }
  let slowTimer: ReturnType<typeof setInterval> | null = null;
  function slowPoll() {
    if (slowTimer) return;
    slowTimer = setInterval(() => {
      fetchFlow();
      if (running && slowTimer) { clearInterval(slowTimer); slowTimer = null; startPoll(); }
    }, 15000);
  }

  onMount(() => {
    tick().then(() => { if (autopoll) (running ? startPoll() : slowPoll()); });
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
    if (slowTimer) clearInterval(slowTimer);
  });

  // store rail chips
  function storeCount(k: string): string {
    const v = flow?.stores?.[k];
    return v == null ? '—' : nfmt(v);
  }
</script>

<div class="tf">
  <!-- header / controls -->
  <div class="tf-bar">
    <div class="tf-bar-l">
      <span class="tf-h">Training Pipeline</span>
      {#if flow?.run}
        <span class="tf-run">RUN #{flow.run.id}</span>
        <span class="tf-st tf-st-{running ? 'run' : status}">{status}</span>
      {/if}
    </div>
    <div class="tf-bar-r">
      <span class="tf-steps">{TOTAL_STEPS} steps · 10 layers</span>
      <button class="tf-toggle" onclick={() => (fullView = !fullView)}>{fullView ? '▾ Hide detail' : '▸ Show detail'}</button>
    </div>
  </div>

  <!-- boiler schematic -->
  <div class="tf-schem">
    <TrainingSchematic {stages} live={running} title="" badge={flow?.run ? `RUN #${flow.run.id}` : ''} onpick={onPick} />
  </div>

  <!-- KPI strip -->
  <div class="tf-kpis">
    {#each KPI_DEFS as d}
      <div class="tf-kpi">
        <div class="tf-kpi-v">{flow?.kpis?.[d.k] == null ? '—' : nfmt(flow.kpis[d.k])}</div>
        <div class="tf-kpi-l">{d.label}</div>
      </div>
    {/each}
  </div>

  <!-- data-store rail -->
  <div class="tf-stores">
    {#each STORES as s}
      <div class="tf-chip" class:tf-chip-flash={flashStore[s.key]} class:tf-chip-empty={flow?.stores?.[s.key] == null} title={s.table}>
        <span class="tf-chip-k">{s.key}</span>
        <span class="tf-chip-v">{storeCount(s.key)}</span>
      </div>
    {/each}
  </div>

  {#if fullView}
    <!-- 10-layer / 60-step detail -->
    <div class="tf-detail">
      {#each FLOW_LAYERS as L}
        {@const ls = layerStatus(L.idx)}
        <div id="tf-layer-{L.idx}" class="tf-layer tf-l-{ls}" class:tf-l-open={expanded === L.idx} style="--tf-c: {CHEX[L.color]};">
          <button class="tf-layer-h" onclick={() => (expanded = expanded === L.idx ? -1 : L.idx)}>
            <span class="tf-dot2 tf-dot-{ls}"></span>
            <span class="tf-layer-t">{L.title}</span>
            {#if L.gate}<span class="tf-gate">{L.gate}{flow && !flow.flags[L.gate.toLowerCase() as 'engineer' | 'enrich'] ? ' · OFF' : ''}</span>{/if}
            <span class="tf-layer-n">{L.steps.length}</span>
            <span class="tf-caret">{expanded === L.idx ? '−' : '+'}</span>
          </button>
          {#if expanded === L.idx}
            <div class="tf-steps-list">
              {#each L.steps as st}
                <div class="tf-step">
                  {#if st.model}<span class="tf-mdl tf-mdl-{st.model}">{st.model}</span>{/if}
                  <span class="tf-step-l">{st.label}</span>
                  <span class="tf-step-d">{st.detail}</span>
                  <span class="tf-step-w">→ {st.writesTo}</span>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  <!-- live log console -->
  {#if running || logEvents.length}
    <div class="tf-log" bind:this={logEl}>
      {#each logEvents as e}
        <div class="tf-log-row"><span class="tf-log-ts">{e.ts}</span>{e.msg}</div>
      {:else}
        <div class="tf-log-row tf-log-empty">waiting for log…</div>
      {/each}
    </div>
  {/if}

  {#if !loaded}
    <div class="tf-loading">loading training flow…</div>
  {/if}
</div>

<style>
  .tf { font-family: var(--pw-font-body, Inter, sans-serif); color: var(--pw-ink, #2c2c2c); }

  .tf-bar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
  .tf-bar-l, .tf-bar-r { display: flex; align-items: center; gap: 10px; }
  .tf-h { font-size: 14px; font-weight: 800; }
  .tf-run { font-size: 11px; font-weight: 700; color: #0ca6b0; font-family: ui-monospace, Menlo, monospace; }
  .tf-st { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 7px; border-radius: 3px; }
  .tf-st-run { background: #fbeee7; color: var(--pw-accent, #c96342); }
  .tf-st-done { background: #eaf5ec; color: #2e7d3e; }
  .tf-st-idle, .tf-st-failed, .tf-st-error { background: #f0ebe2; color: #9a8e80; }
  .tf-steps { font-size: 11px; color: var(--pw-muted, #8a8276); }
  .tf-toggle { font-size: 11px; font-weight: 700; border: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-bg, #fff); color: var(--pw-ink, #2c2c2c); padding: 4px 10px; cursor: pointer; border-radius: 0; }
  .tf-toggle:hover { border-color: var(--pw-accent, #c96342); color: var(--pw-accent, #c96342); }

  .tf-schem { margin-bottom: 16px; }

  /* KPI strip */
  .tf-kpis { display: flex; gap: 0; border: 1px solid var(--pw-border, #e5ddcf); margin-bottom: 12px; overflow-x: auto; }
  .tf-kpi { flex: 1 1 0; min-width: 88px; padding: 10px 12px; border-right: 1px solid var(--pw-border, #e5ddcf); }
  .tf-kpi:last-child { border-right: none; }
  .tf-kpi-v { font-size: 18px; font-weight: 800; font-family: ui-monospace, Menlo, monospace; color: var(--pw-ink, #2c2c2c); }
  .tf-kpi-l { font-size: 10px; color: var(--pw-muted, #8a8276); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 2px; }

  /* store rail */
  .tf-stores { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
  .tf-chip { display: inline-flex; align-items: baseline; gap: 5px; padding: 4px 9px; border: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-surface-warm, #f4f3ee); transition: background 0.4s, border-color 0.4s; }
  .tf-chip-k { font-size: 9px; font-weight: 800; letter-spacing: 0.05em; color: #8a8070; }
  .tf-chip-v { font-size: 11px; font-weight: 700; font-family: ui-monospace, Menlo, monospace; color: var(--pw-ink, #2c2c2c); }
  .tf-chip-empty { opacity: 0.5; }
  .tf-chip-flash { background: #fbeee7; border-color: var(--pw-accent, #c96342); animation: tf-flash 0.9s ease-out; }
  @keyframes tf-flash { 0% { background: #f4d9cc; } 100% { background: var(--pw-surface-warm, #f4f3ee); } }

  /* layer detail */
  .tf-detail { border: 1px solid var(--pw-border, #e5ddcf); margin-bottom: 14px; }
  .tf-layer { border-bottom: 1px solid var(--pw-border, #e5ddcf); border-left: 3px solid var(--tf-c, #c96342); }
  .tf-layer:last-child { border-bottom: none; }
  .tf-layer-h { width: 100%; display: flex; align-items: center; gap: 9px; padding: 9px 12px; background: var(--pw-bg, #fff); border: none; cursor: pointer; text-align: left; font-family: inherit; }
  .tf-layer-h:hover { background: var(--pw-surface-warm, #f4f3ee); }
  .tf-layer-t { font-size: 12px; font-weight: 700; flex: 1; color: var(--pw-ink, #2c2c2c); }
  .tf-layer-n { font-size: 10px; font-weight: 700; color: #8a8070; font-family: ui-monospace, Menlo, monospace; }
  .tf-caret { font-size: 14px; font-weight: 700; color: #8a8070; width: 14px; text-align: center; }
  .tf-gate { font-size: 9px; font-weight: 800; letter-spacing: 0.05em; color: #a8740b; background: #faf2e0; padding: 1px 6px; }
  .tf-l-skipped .tf-layer-t { opacity: 0.55; }
  .tf-l-running { background: #fffaf7; }

  .tf-dot2 { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; background: #d4c8b8; }
  .tf-dot-running { background: var(--pw-accent, #c96342); animation: tf-pulse 1.6s ease-in-out infinite; }
  .tf-dot-done { background: #2e7d3e; }
  .tf-dot-skipped { background: #cfc7b6; }
  .tf-dot-error { background: #c0392b; }
  @keyframes tf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

  .tf-steps-list { background: var(--pw-bg-alt, #f7f6f3); padding: 4px 0; }
  .tf-step { display: grid; grid-template-columns: 56px minmax(120px, 1.2fr) minmax(160px, 2fr) minmax(120px, 1.4fr); gap: 10px; align-items: baseline; padding: 5px 14px 5px 24px; font-size: 11px; }
  .tf-mdl { font-size: 8.5px; font-weight: 800; letter-spacing: 0.04em; text-align: center; padding: 1px 0; border-radius: 2px; }
  .tf-mdl-SQL { background: #eef5fb; color: #2b6cb0; }
  .tf-mdl-FLASH { background: #fbeee7; color: #c96342; }
  .tf-mdl-DEEP { background: #efe8f7; color: #7c3aed; }
  .tf-mdl-embed { background: #eaf5ec; color: #2e7d3e; }
  .tf-step-l { font-weight: 700; color: var(--pw-ink, #2c2c2c); }
  .tf-step-d { color: #6a6258; }
  .tf-step-w { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: #9a8e80; }

  /* live log */
  .tf-log { background: #1c1a17; color: #d6cebd; font-family: ui-monospace, Menlo, monospace; font-size: 11px; line-height: 1.5; padding: 10px 12px; max-height: 220px; overflow-y: auto; border: 1px solid #2a2620; }
  .tf-log-row { white-space: pre-wrap; word-break: break-word; }
  .tf-log-ts { color: #8a8276; margin-right: 8px; }
  .tf-log-empty { color: #6a6258; }

  .tf-loading { font-size: 12px; color: var(--pw-muted, #8a8276); padding: 12px 0; }

  @media (max-width: 760px) {
    .tf-step { grid-template-columns: 48px 1fr; }
    .tf-step-d, .tf-step-w { display: none; }
  }
</style>
