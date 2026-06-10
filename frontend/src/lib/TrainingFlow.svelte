<script lang="ts">
  // Live training-flow visualization — full mockup parity (docs/training_flow_prototype.html).
  //   • rich header   : run # · layer X/10 · elapsed · ETA · rows/s + LIVE/DONE badge
  //   • KPI strip      : tables / rows / Q&A / links / ◆matviews / gaps / eval
  //   • band progress  : staging (accent) + training (green) segments
  //   • schematic      : boiler cards w/ inner step-preview + animated connectors
  //   • 2-col body     : all-layer step detail (left) + DATA STORES vertical rail + dark log (right)
  // Real data: GET /api/projects/{slug}/training-flow + /auto-train/log?since=N
  import { onMount, onDestroy } from 'svelte';
  import { FLOW_LAYERS, STORES, TOTAL_STEPS } from '$lib/trainingFlowSpec';

  let { slug = '', autopoll = true } = $props();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  // per-layer schematic accent (boiler colors), matching the mockup LCOLOR
  const LCOLOR = ['#d4930e', '#d4930e', '#d4930e', '#c96342', '#c96342', '#0ca6b0', '#c96342', '#c96342', '#0ca6b0', '#2d6a4f'];
  const ink = (c: string) => (c === '#d4930e' ? '#a8740b' : c);
  // footer store tags per layer (mockup parity)
  const LTAGS = ['STAGE', 'CONTRACT', 'STAGE,PG', 'META,PG', 'REL,QA', 'MV,META', 'AGE,PG', 'META,EVAL', 'QA,VEC', 'EVAL'];
  const MC: Record<string, string> = { FLASH: '#9a4fb0', DEEP: '#3a63b0', LITE: '#b07e2f', embed: '#2f8a9a', SQL: '#7d756a' };
  const SICON: Record<string, string> = {
    STAGE: '📥', PG: '🗄️', META: '🏷️', QA: '❓', VEC: '🔢', BRAIN: '🧠',
    REL: '🔗', MV: '◆', AGE: '🕸️', ENR: '💊', EVAL: '✅',
  };

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
  let drilled = $state<number>(-1);
  let loaded = $state(false);
  let prevStores = $state<Record<string, number>>({});
  let flashStore = $state<Record<string, boolean>>({});
  let logEl = $state<HTMLDivElement | null>(null);
  let nowTs = $state(Date.now());

  const status = $derived(flow?.run?.status || 'idle');
  const running = $derived(['running', 'queued', 'finalizing'].includes(status));
  const done = $derived(status === 'done');

  function layerStatus(idx: number): 'idle' | 'running' | 'done' | 'skipped' | 'error' {
    const l = flow?.layers?.find((x) => x.idx === idx);
    const s = (l?.status || 'idle') as any;
    return ['idle', 'running', 'done', 'skipped', 'error'].includes(s) ? s : 'idle';
  }
  // does this step run, given the live engineer/enrich flags?
  function gated(g: string | null): boolean {
    if (g === 'ENGINEER') return !(flow?.flags?.engineer ?? true);
    if (g === 'ENRICH') return !(flow?.flags?.enrich ?? false);
    return false;
  }
  function liveStepCount(L: (typeof FLOW_LAYERS)[number]): number {
    return L.steps.filter((s) => !gated(s.gate)).length;
  }
  // per-step display state derived from its layer's live status
  function stepState(idx: number, gate: string | null): 'idle' | 'run' | 'done' | 'gated' {
    if (gated(gate)) return 'gated';
    const ls = layerStatus(idx);
    if (ls === 'done') return 'done';
    if (ls === 'running') return 'run';
    return 'idle';
  }

  function nfmt(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    return n.toLocaleString();
  }

  // ── header meta ────────────────────────────────────────────────
  const doneLayers = $derived(flow?.layers?.filter((l) => l.status === 'done').length ?? 0);
  const layerLabel = $derived(`layer ${doneLayers}/10`);
  const elapsedSec = $derived.by(() => {
    const s = flow?.run?.started_at ? Date.parse(flow.run.started_at) : NaN;
    if (Number.isNaN(s)) return 0;
    const e = flow?.run?.finished_at ? Date.parse(flow.run.finished_at) : nowTs;
    return Math.max(0, Math.floor((e - s) / 1000));
  });
  function fmtClock(sec: number): string {
    const m = Math.floor(sec / 60), x = Math.floor(sec % 60);
    return `${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}`;
  }
  const rps = $derived.by(() => {
    const rows = flow?.kpis?.rows;
    if (!running || rows == null || elapsedSec < 1) return '—';
    return Math.round(rows / elapsedSec).toLocaleString();
  });
  // band: staging layers 0-2, training 3-9
  const bandStg = $derived.by(() => {
    const d = flow?.layers?.filter((l) => l.idx < 3 && l.status === 'done').length ?? 0;
    return (d / 3) * 100;
  });
  const bandTrn = $derived.by(() => {
    const d = flow?.layers?.filter((l) => l.idx >= 3 && l.status === 'done').length ?? 0;
    return (d / 7) * 100;
  });

  // KPI tiles
  const KPI_DEFS = [
    { k: 'tables', label: 'Tables' },
    { k: 'rows', label: 'Rows' },
    { k: 'qa', label: 'Q&A' },
    { k: 'rels', label: 'Links' },
    { k: 'matviews', label: '◆ Matviews' },
    { k: 'gaps', label: 'Gaps' },
    { k: 'eval_score', label: 'Eval' },
  ];

  function drill(idx: number) {
    drilled = idx;
    requestAnimationFrame(() => {
      document.getElementById(`tf-ly-${idx}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    setTimeout(() => (drilled = -1), 1200);
  }

  // ── polling ────────────────────────────────────────────────────
  async function fetchFlow() {
    try {
      const r = await fetch(`/api/projects/${slug}/training-flow`, { headers: _h() });
      if (!r.ok) return;
      const next: FlowResp = await r.json();
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
  let slowTimer: ReturnType<typeof setInterval> | null = null;
  let clockTimer: ReturnType<typeof setInterval> | null = null;
  async function tick() { await Promise.all([fetchFlow(), fetchLog()]); }
  function startPoll() {
    if (timer) return;
    timer = setInterval(() => { tick(); if (timer && !running) { clearInterval(timer); timer = null; slowPoll(); } }, 2000);
  }
  function slowPoll() {
    if (slowTimer) return;
    slowTimer = setInterval(() => { fetchFlow(); if (running && slowTimer) { clearInterval(slowTimer); slowTimer = null; startPoll(); } }, 15000);
  }

  onMount(() => {
    clockTimer = setInterval(() => (nowTs = Date.now()), 1000);
    tick().then(() => { if (autopoll) (running ? startPoll() : slowPoll()); });
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
    if (slowTimer) clearInterval(slowTimer);
    if (clockTimer) clearInterval(clockTimer);
  });

  function storeCount(k: string): string {
    const v = flow?.stores?.[k];
    return v == null ? '—' : nfmt(v);
  }
  function storeHas(k: string): boolean {
    const v = flow?.stores?.[k];
    return v != null && v > 0;
  }
</script>

<div class="tf">
  <!-- header -->
  <div class="tf-hd">
    <h1>Full Training Pipeline</h1>
    {#if done}
      <span class="tf-badge tf-badge-done">✓ DONE</span>
    {:else if running}
      <span class="tf-badge tf-badge-live"><span class="tf-blink"></span>LIVE</span>
    {:else}
      <span class="tf-badge tf-badge-idle">IDLE</span>
    {/if}
    <span class="tf-sp"></span>
    <span class="tf-meta">
      run <b>#{flow?.run?.id ?? '—'}</b> · <b>{layerLabel}</b> · elapsed <b>{fmtClock(elapsedSec)}</b> · ETA <b>—</b> · <b>{rps}</b> rows/s
    </span>
  </div>

  <!-- KPI strip -->
  <div class="tf-kpi">
    {#each KPI_DEFS as d}
      <div class="tf-k">
        <div class="tf-n">{flow?.kpis?.[d.k] == null ? '0' : nfmt(flow.kpis[d.k])}</div>
        <div class="tf-l">{d.label}</div>
      </div>
    {/each}
  </div>

  <!-- band -->
  <div class="tf-band"><div class="tf-band-stg" style="width:{bandStg}%"></div><div class="tf-band-trn" style="width:{bandTrn}%"></div></div>

  <!-- schematic -->
  <div class="tf-schemwrap">
    <p class="tf-schemh">Pipeline schematic <span class="tf-hint">— click any stage to drill into its steps below</span></p>
    <div class="tf-schem">
      {#each FLOW_LAYERS as L, li}
        {@const ls = layerStatus(L.idx)}
        {@const c = LCOLOR[li]}
        <div class="tf-scard tf-sc-{ls}" class:tf-on={drilled === L.idx} style="--c:{c};--ci:{ink(c)}" onclick={() => drill(L.idx)} role="button" tabindex="0">
          <div class="tf-st"><span>L{li}</span><span class="tf-ck">{ls === 'done' ? '✓' : ''}</span></div>
          <div class="tf-sl">{L.short}</div>
          <div class="tf-sd">{L.steps[0]?.label}{L.steps.length > 1 ? '\n' + L.steps[1].label : ''}</div>
          <div class="tf-sf">{liveStepCount(L)} steps · → {LTAGS[li]}</div>
        </div>
        {#if li < FLOW_LAYERS.length - 1}
          <div class="tf-conn" class:tf-flow={ls === 'running'} class:tf-dn={ls === 'done'} style="--c:{c}"><span class="tf-ptcl"></span></div>
        {/if}
      {/each}
    </div>
  </div>

  <!-- body: steps (left) + rail (right) -->
  <div class="tf-body">
    <div class="tf-flow">
      {#each FLOW_LAYERS as L}
        <div id="tf-ly-{L.idx}" class="tf-layer" class:tf-ly-on={drilled === L.idx}>
          <div class="tf-ltitle">{L.title}{#if L.gate}<span class="tf-gate">{L.gate}{gated(L.steps[0]?.gate) ? ' · OFF' : ''}</span>{/if}</div>
          {#each L.steps as st}
            {@const ss = stepState(L.idx, st.gate)}
            <div class="tf-step tf-s-{ss}">
              <span class="tf-g">{ss === 'gated' ? '⊘' : ss === 'done' ? '✓' : ss === 'run' ? '◉' : '·'}</span>
              <span class="tf-nm">{st.label}</span>
              {#if st.model}<span class="tf-badge2" style="background:{MC[st.model]}">{st.model}</span>{/if}
              <span class="tf-dt">{st.detail}</span>
              <span class="tf-wr">→ {st.writesTo}</span>
            </div>
          {/each}
        </div>
      {/each}
    </div>

    <div class="tf-rail">
      <div class="tf-stores">
        <p class="tf-rh">data stores (writes)</p>
        {#each STORES as s}
          <div class="tf-store" class:tf-has={storeHas(s.key)} class:tf-hit={flashStore[s.key]} title={s.table}>
            <span class="tf-si">{SICON[s.key] ?? '•'}</span>
            <span class="tf-sn">{s.label}</span>
            <span class="tf-sc">{storeCount(s.key)}</span>
          </div>
        {/each}
      </div>
      <div class="tf-log" bind:this={logEl}>
        {#each logEvents as e}
          <div class="tf-log-row"><span class="tf-log-ts">{e.ts}</span>{e.msg}</div>
        {:else}
          <div class="tf-log-row tf-log-empty">{running ? 'waiting for log…' : 'idle — no active run'}</div>
        {/each}
      </div>
    </div>
  </div>

  <!-- legend -->
  <div class="tf-legend">
    states: ◉ running · ✓ done · · idle · ⊘ gated &nbsp; models:
    <b style="background:{MC.FLASH}">FLASH</b><b style="background:{MC.DEEP}">DEEP</b><b style="background:{MC.LITE}">LITE</b><b style="background:{MC.embed}">EMBED</b><b style="background:{MC.SQL}">SQL</b>
    <span class="tf-note">{TOTAL_STEPS} steps · 10 layers · live · binds /training-flow + /auto-train/log</span>
  </div>

  {#if !loaded}<div class="tf-loading">loading training flow…</div>{/if}
</div>

<style>
  .tf {
    --pw-bg: #fff; --pw-bg-alt: #f7f6f3; --pw-surface-warm: #f4f3ee;
    --pw-ink: #2c2c2c; --pw-ink-soft: #4a4a48; --pw-muted: #6f6e69; --pw-dim: #97968f;
    --pw-border: #e8e6dd; --pw-border-soft: #efeee6;
    --pw-accent: #c96342; --pw-accent-ink: #b04f30; --pw-accent-soft: #fdebe1; --pw-accent-wash: rgba(201, 99, 66, .10);
    --pw-success: #2d6a4f; --pw-success-soft: #d8e4dd;
    --pw-mono: 'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace;
    font-family: 'Inter', system-ui, -apple-system, Arial, sans-serif; color: var(--pw-ink); font-size: 13px;
  }

  /* header */
  .tf-hd { display: flex; align-items: center; gap: 12px; padding: 4px 2px 14px; }
  .tf-hd h1 { font-size: 14px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; margin: 0; }
  .tf-sp { flex: 1; }
  .tf-meta { font-size: 11px; color: var(--pw-muted); font-family: var(--pw-mono); }
  .tf-meta b { color: var(--pw-ink); }
  .tf-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700; padding: 3px 9px; border: 1px solid transparent; }
  .tf-badge-live { color: var(--pw-accent-ink); background: var(--pw-accent-soft); border-color: var(--pw-accent); }
  .tf-badge-done { color: var(--pw-success); background: var(--pw-success-soft); border-color: var(--pw-success); }
  .tf-badge-idle { color: #9a8e80; background: #f0ebe2; }
  .tf-blink { width: 8px; height: 8px; border-radius: 50%; background: var(--pw-accent); animation: tf-blink 1.1s infinite; }
  @keyframes tf-blink { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }

  /* KPI */
  .tf-kpi { display: flex; border: 1px solid var(--pw-border); }
  .tf-k { flex: 1; padding: 12px 16px; border-right: 1px solid var(--pw-border-soft); }
  .tf-k:last-child { border-right: none; }
  .tf-n { font-size: 21px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1; }
  .tf-l { font-size: 9px; text-transform: uppercase; letter-spacing: .05em; color: var(--pw-muted); margin-top: 5px; }

  /* band */
  .tf-band { display: flex; height: 5px; background: var(--pw-border-soft); border: 1px solid var(--pw-border); border-top: none; }
  .tf-band-stg { background: var(--pw-accent); transition: width .4s; }
  .tf-band-trn { background: var(--pw-success); transition: width .4s; }

  /* schematic */
  .tf-schemwrap { padding: 14px 16px 16px; border: 1px solid var(--pw-border); border-top: none; background: var(--pw-surface-warm); }
  .tf-schemh { font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; color: var(--pw-muted); margin: 0 0 11px; }
  .tf-hint { font-size: 9px; font-weight: 500; color: var(--pw-dim); text-transform: none; letter-spacing: 0; }
  .tf-schem { display: flex; align-items: stretch; overflow-x: auto; padding-bottom: 4px; }
  .tf-scard { flex: 0 0 138px; background: #fff; border: 1px solid var(--pw-border); border-top: 3px solid var(--c); padding: 9px 11px; cursor: pointer; transition: .2s; min-height: 84px; display: flex; flex-direction: column; gap: 3px; }
  .tf-scard:hover { box-shadow: 0 4px 12px rgba(0, 0, 0, .09); transform: translateY(-2px); }
  .tf-on { box-shadow: 0 0 0 2px var(--c); }
  .tf-sc-done { background: #fbfaf7; }
  .tf-st { font-size: 8px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: var(--pw-dim); display: flex; justify-content: space-between; }
  .tf-ck { color: var(--pw-success); }
  .tf-sl { font-size: 11.5px; font-weight: 800; color: var(--ci); }
  .tf-sd { font-size: 9px; color: #4a4438; font-family: var(--pw-mono); line-height: 1.4; white-space: pre-line; }
  .tf-sf { font-size: 8.5px; color: var(--pw-dim); margin-top: auto; font-family: var(--pw-mono); }

  .tf-conn { flex: 0 0 30px; position: relative; align-self: center; height: 24px; }
  .tf-conn::before { content: ''; position: absolute; top: 50%; left: 0; right: 8px; height: 2px; transform: translateY(-50%); background-image: repeating-linear-gradient(90deg, var(--c) 0 6px, transparent 6px 11px); background-size: 22px 2px; opacity: .4; }
  .tf-conn.tf-flow::before { animation: tf-sdash 1.3s linear infinite; opacity: 1; }
  .tf-conn.tf-dn::before { opacity: 1; }
  .tf-conn::after { content: ''; position: absolute; top: 50%; right: 0; transform: translateY(-50%); width: 0; height: 0; border-left: 6px solid var(--c); border-top: 4px solid transparent; border-bottom: 4px solid transparent; opacity: .4; }
  .tf-conn.tf-flow::after, .tf-conn.tf-dn::after { opacity: 1; }
  .tf-ptcl { position: absolute; top: 50%; width: 5px; height: 5px; border-radius: 50%; margin-top: -2.5px; background: var(--c); filter: drop-shadow(0 0 3px var(--c)); opacity: 0; }
  .tf-conn.tf-flow .tf-ptcl { animation: tf-strav 1.3s linear infinite; }
  @keyframes tf-sdash { to { background-position: 22px 0; } }
  @keyframes tf-strav { 0% { left: 0; opacity: 0; } 15% { opacity: 1; } 85% { opacity: 1; } 100% { left: calc(100% - 8px); opacity: 0; } }

  /* body */
  .tf-body { display: grid; grid-template-columns: 1fr 290px; border: 1px solid var(--pw-border); border-top: none; }
  .tf-flow { padding: 8px 0; max-height: 560px; overflow: auto; border-right: 1px solid var(--pw-border); }
  .tf-layer { padding: 10px 18px; border-bottom: 1px solid var(--pw-border-soft); }
  .tf-ly-on { background: linear-gradient(90deg, var(--pw-accent-wash), transparent); }
  .tf-ltitle { font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; color: var(--pw-muted); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
  .tf-gate { font-size: 9px; font-weight: 800; letter-spacing: .05em; color: #a8740b; background: #faf2e0; padding: 1px 6px; }
  .tf-step { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 12px; color: var(--pw-dim); }
  .tf-g { width: 14px; text-align: center; flex: none; }
  .tf-nm { color: var(--pw-ink-soft); min-width: 175px; }
  .tf-badge2 { font-size: 8px; font-weight: 800; padding: 1px 4px; color: #fff; flex: none; }
  .tf-dt { font-size: 10px; color: var(--pw-muted); font-family: var(--pw-mono); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tf-wr { font-size: 9px; color: var(--pw-dim); font-family: var(--pw-mono); flex: none; }
  .tf-s-done .tf-nm { color: var(--pw-ink); font-weight: 600; }
  .tf-s-done .tf-g { color: var(--pw-success); }
  .tf-s-run { color: var(--pw-accent-ink); animation: tf-rowglow 1.4s infinite; }
  .tf-s-run .tf-nm { color: var(--pw-accent-ink); font-weight: 700; }
  .tf-s-run .tf-g { color: var(--pw-accent); }
  @keyframes tf-rowglow { 0%, 100% { background: transparent; } 50% { background: var(--pw-accent-wash); } }
  .tf-s-gated { opacity: .4; }
  .tf-s-gated .tf-nm { text-decoration: line-through; }

  /* rail */
  .tf-rail { display: flex; flex-direction: column; }
  .tf-stores { padding: 14px 16px; border-bottom: 1px solid var(--pw-border); }
  .tf-rh { font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; color: var(--pw-muted); margin: 0 0 10px; }
  .tf-store { display: flex; align-items: center; gap: 8px; padding: 7px 9px; border: 1px solid var(--pw-border); margin-bottom: 6px; transition: .25s; background: var(--pw-bg); }
  .tf-si { font-size: 13px; width: 18px; text-align: center; }
  .tf-sn { font-size: 11px; font-weight: 600; flex: 1; }
  .tf-sc { font-size: 11px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--pw-muted); }
  .tf-has .tf-sc { color: var(--pw-success); }
  .tf-hit { border-color: var(--pw-accent); background: var(--pw-accent-soft); transform: translateX(-2px); }
  .tf-log { flex: 1; padding: 12px 14px; background: #1c1a18; color: #d8d2c8; font-family: var(--pw-mono); font-size: 11px; overflow: auto; max-height: 240px; line-height: 1.65; }
  .tf-log-row { white-space: pre-wrap; word-break: break-word; }
  .tf-log-ts { color: #6a655c; margin-right: 6px; }
  .tf-log-empty { color: #6a655c; }

  /* legend */
  .tf-legend { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-size: 9px; color: var(--pw-muted); padding: 12px 2px 2px; }
  .tf-legend b { color: #fff; padding: 1px 5px; font-size: 8px; }
  .tf-note { color: var(--pw-dim); margin-left: auto; }

  .tf-loading { font-size: 12px; color: var(--pw-muted); padding: 12px 0; }

  @media (max-width: 820px) {
    .tf-body { grid-template-columns: 1fr; }
    .tf-rail { border-top: 1px solid var(--pw-border); }
    .tf-flow { border-right: none; }
    .tf-dt, .tf-wr { display: none; }
  }
</style>
