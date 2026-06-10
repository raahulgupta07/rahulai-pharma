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
    run: { id: number; status: string; phase?: string; current_step: string; started_at?: string; finished_at?: string; stage_progress?: number } | null;
    layers: { idx: number; title: string; color: string; gate: string | null; status: string }[];
    stores: Record<string, number | null>;
    kpis: Record<string, number | null>;
    flags: { engineer: boolean; enrich: boolean };
  };

  // ── UTC-safe timestamp parser ─────────────────────────────────
  // If the string has no timezone marker (no Z, no +hh:mm / -hh:mm),
  // treat it as UTC by appending Z (and converting space separator to T).
  function _ts(s?: string): number {
    if (!s) return NaN;
    let v = s.trim();
    // Convert "2026-06-10 15:46:39" → "2026-06-10T15:46:39"
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(v)) v = v.replace(' ', 'T');
    // If no timezone marker present, append Z to force UTC interpretation
    if (!/[Zz]$/.test(v) && !/[+-]\d{2}:\d{2}$/.test(v)) v = v + 'Z';
    return Date.parse(v);
  }

  let flow = $state<FlowResp | null>(null);
  let drilled = $state<number>(-1);
  let loaded = $state(false);
  let prevStores = $state<Record<string, number>>({});
  let flashStore = $state<Record<string, boolean>>({});
  let nowTs = $state(Date.now());

  const status = $derived(flow?.run?.status || 'idle');
  // phase: prefer the explicit field from the backend; fall back to deriving from status
  const phase = $derived(
    flow?.run?.phase ||
    (status === 'done' ? 'done'
      : status === 'finalizing' ? 'finalizing'
      : status === 'failed' || status === 'error' ? 'failed'
      : ['running', 'queued'].includes(status) ? 'running'
      : 'idle')
  );
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
    const s = _ts(flow?.run?.started_at);
    if (Number.isNaN(s)) return 0;
    const e = phase === 'done' || phase === 'failed'
      ? (_ts(flow?.run?.finished_at) || nowTs)
      : nowTs;
    return Math.max(0, Math.floor((e - s) / 1000));
  });
  function fmtClock(sec: number): string {
    const m = Math.floor(sec / 60), x = Math.floor(sec % 60);
    return `${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}`;
  }
  const rps = $derived.by(() => {
    const rows = flow?.kpis?.rows;
    // Only show rows/s while actively ingesting (not during finalizing/done/idle)
    if (phase !== 'running' || rows == null || elapsedSec < 1) return '—';
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

  // Serpentine 2×5 layout: row 1 (L0–L4) flows L→R, U-turns down, row 2 (L5–L9)
  // flows R→L — one continuous connected snake. PER_ROW cards per row.
  const PER_ROW = 5;
  function gridRow(li: number) { return Math.floor(li / PER_ROW) + 1; }
  function gridCol(li: number) {
    const row = Math.floor(li / PER_ROW);
    const pos = li % PER_ROW;
    return (row % 2 === 0 ? pos : PER_ROW - 1 - pos) + 1; // odd rows reversed
  }
  function connDir(li: number, total: number) {
    if (li >= total - 1) return 'none';            // last stage — no outgoing arrow
    const lastInRow = li % PER_ROW === PER_ROW - 1; // end of a row → U-turn down
    if (lastInRow) return 'down';
    return Math.floor(li / PER_ROW) % 2 === 0 ? 'right' : 'left';
  }

  // store-detail modal
  let storeOpen = $state(false);
  let storeLoading = $state(false);
  let storeSel = $state<any>(null);
  let storeDetail = $state<any>(null);
  async function openStore(s: any) {
    storeSel = s; storeDetail = null; storeOpen = true; storeLoading = true;
    try {
      const r = await fetch(`/api/projects/${slug}/store-detail/${s.key}`, { headers: _h() });
      if (r.ok) storeDetail = await r.json();
    } catch {}
    storeLoading = false;
  }
  function closeStore() { storeOpen = false; storeSel = null; storeDetail = null; }
  function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && storeOpen) closeStore(); }

  // map a step's writesTo target → a DATA STORES key (clickable link)
  function writeKey(w: string): string | null {
    const s = (w || '').toLowerCase();
    // masked (generic) targets — what the live API now emits
    if (s.includes('intake')) return 'STAGE';
    if (s.includes('training q&a')) return 'QA';
    if (s.includes('vector index')) return 'VEC';
    if (s.includes('company brain')) return 'BRAIN';
    if (s === '(links)' || s.includes('(links)')) return 'REL';
    if (s.includes('managed view')) return 'MV';
    if (s.includes('graph store')) return 'AGE';
    if (s.includes('enrichment')) return 'ENR';
    if (s.includes('eval store')) return 'EVAL';
    if (s.includes('metadata')) return 'META';
    if (s.includes('primary store')) return 'PG';
    // legacy / real targets (FLOW_OBFUSCATE=0 debug mode)
    if (s.includes('manifest') || s.includes('staging')) return 'STAGE';
    if (s.includes('dash_training_qa')) return 'QA';
    if (s.includes('dash_table_metadata')) return 'META';
    if (s.includes('pgvector')) return 'VEC';
    if (s.includes('dash_company_brain')) return 'BRAIN';
    if (s.includes('dash_relationships')) return 'REL';
    if (s.includes('matview')) return 'MV';
    if (s.includes('apache age') || s.includes('age graph')) return 'AGE';
    if (s.includes('catalog_enrichment')) return 'ENR';
    if (s.includes('dash_eval_runs')) return 'EVAL';
    if (s.includes('citypharma') || s.includes('<table>')) return 'PG';
    return null;
  }
  function storeByKey(k: string | null) { return k ? STORES.find((s: any) => s.key === k) : null; }
  function fmtMs(ms: number | null | undefined): string {
    if (ms == null) return '';
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
  }
  const STEP_ICON: Record<string, string> = { done: '✓', running: '◉', error: '✗', warn: '⚠', gated: '⊘', idle: '·' };

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
  let timer: ReturnType<typeof setInterval> | null = null;
  let slowTimer: ReturnType<typeof setInterval> | null = null;
  let clockTimer: ReturnType<typeof setInterval> | null = null;
  async function tick() { await fetchFlow(); }
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

<svelte:window onkeydown={onKey} />

<div class="tf">
  <!-- header -->
  <div class="tf-hd">
    <h1>Full Training Pipeline</h1>
    {#if phase === 'done'}
      <span class="tf-badge tf-badge-done">✓ DONE</span>
    {:else if phase === 'finalizing'}
      <span class="tf-badge tf-badge-fin"><span class="tf-blink-fin"></span>FINISHING</span>
    {:else if phase === 'running'}
      <span class="tf-badge tf-badge-live"><span class="tf-blink"></span>LIVE</span>
    {:else if phase === 'failed'}
      <span class="tf-badge tf-badge-fail">✗ FAILED</span>
    {:else}
      <span class="tf-badge tf-badge-idle">IDLE</span>
    {/if}
    <span class="tf-sp"></span>
    <span class="tf-meta">
      run <b>#{flow?.run?.id ?? '—'}</b> · <b>{layerLabel}</b> · elapsed <b>{fmtClock(elapsedSec)}</b>
      {#if phase === 'running'}
        · ETA <b>—</b> · <b>{rps}</b> rows/s
      {:else if phase === 'finalizing'}
        · <span class="tf-fin-hint">indexing knowledge base…</span>
      {/if}
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
        {@const dir = connDir(li, FLOW_LAYERS.length)}
        <div class="tf-scard tf-sc-{ls}" class:tf-on={drilled === L.idx} style="--c:{c};--ci:{ink(c)};grid-row:{gridRow(li)};grid-column:{gridCol(li)};" onclick={() => drill(L.idx)} role="button" tabindex="0">
          <div class="tf-st"><span>L{li}</span><span class="tf-ck">{ls === 'done' ? '✓' : ''}</span></div>
          <div class="tf-sl">{L.short}</div>
          <div class="tf-sd">{L.steps[0]?.label}{L.steps.length > 1 ? '\n' + L.steps[1].label : ''}</div>
          <div class="tf-sf">{liveStepCount(L)} steps · → {LTAGS[li]}</div>
          {#if dir !== 'none'}
            <span class="tf-arw tf-a-{dir}" class:tf-flow={ls === 'running'} class:tf-dn={ls === 'done'} style="--c:{c}"></span>
          {/if}
        </div>
      {/each}
    </div>
  </div>

  <!-- body: steps (left) + rail (right) -->
  <div class="tf-body">
    <div class="tf-flow">
      {#each (flow?.layers?.length ? flow.layers : FLOW_LAYERS) as L}
        {@const lstat = L.status ?? layerStatus(L.idx)}
        <div id="tf-ly-{L.idx}" class="tf-layer" class:tf-ly-on={drilled === L.idx}>
          <div class="tf-ltitle">
            <span class="tf-lbadge tf-lb-{lstat}">{STEP_ICON[lstat === 'run' ? 'running' : lstat] ?? '·'}</span>
            <span class="tf-ltxt">{L.title}</span>
            {#if L.step_total != null}<span class="tf-lmeta">{L.step_done}/{L.step_total}{#if L.ms} · {fmtMs(L.ms)}{/if}{#if L.cost} · ${L.cost}{/if}</span>{/if}
            {#if L.gate}<span class="tf-gate">{L.gate}{gated(L.steps?.[0]?.gate) ? ' · OFF' : ''}</span>{/if}
          </div>
          {#each (L.steps ?? []) as st}
            {@const ss = st.state ?? stepState(L.idx, st.gate)}
            {@const wt = st.writes_to ?? st.writesTo}
            {@const wk = writeKey(wt)}
            <div class="tf-step tf-s-{ss}">
              <span class="tf-g">{STEP_ICON[ss === 'run' ? 'running' : ss] ?? '·'}</span>
              <span class="tf-nm">{st.label}</span>
              {#if ss === 'gated'}<span class="tf-off">OFF</span>{/if}
              {#if st.model}<span class="tf-badge2" style="background:{MC[st.model]}">{st.model}</span>{/if}
              <span class="tf-dt">{st.value ?? st.detail}</span>
              {#if st.ms != null}<span class="tf-ms">{fmtMs(st.ms)}</span>{/if}
              {#if wk}
                <button type="button" class="tf-wr tf-wr-link" onclick={() => openStore(storeByKey(wk))} title="open {wt} detail">→ {wt}</button>
              {:else}
                <span class="tf-wr">→ {wt}</span>
              {/if}
            </div>
          {/each}
        </div>
      {/each}
    </div>

    <div class="tf-rail">
      <div class="tf-stores">
        <p class="tf-rh">data stores (writes)</p>
        {#each STORES as s}
          <button type="button" class="tf-store" class:tf-has={storeHas(s.key)} class:tf-hit={flashStore[s.key]} title="{s.table} — click for detail" onclick={() => openStore(s)}>
            <span class="tf-si">{SICON[s.key] ?? '•'}</span>
            <span class="tf-sn">{s.label}</span>
            <span class="tf-sc">{storeCount(s.key)}</span>
            <span class="tf-sgo">›</span>
          </button>
        {/each}
      </div>
    </div>
  </div>

  <!-- legend -->
  <div class="tf-legend">
    states: ◉ running · ✓ done · · idle · ⊘ gated &nbsp; models:
    <b style="background:{MC.FLASH}">FLASH</b><b style="background:{MC.DEEP}">DEEP</b><b style="background:{MC.LITE}">LITE</b><b style="background:{MC.embed}">EMBED</b><b style="background:{MC.SQL}">SQL</b>
    <span class="tf-note">{TOTAL_STEPS} steps · 10 layers · live · binds /training-flow</span>
  </div>

  {#if !loaded}<div class="tf-loading">loading training flow…</div>{/if}
</div>

{#if storeOpen}
  <div class="tf-mback" onclick={closeStore} role="presentation"></div>
  <div class="tf-modal" role="dialog" aria-modal="true">
    <div class="tf-mhead">
      <span class="tf-msi">{storeSel ? (SICON[storeSel.key] ?? '•') : ''}</span>
      <div class="tf-mtitle">
        <div class="tf-ml">{storeDetail?.label ?? storeSel?.label ?? ''}</div>
        <div class="tf-msub">{storeDetail?.table ?? storeSel?.table ?? ''}{#if storeDetail?.count != null} · {nfmt(storeDetail.count)} rows{/if}</div>
      </div>
      <button type="button" class="tf-mx" onclick={closeStore} aria-label="Close">✕</button>
    </div>
    <div class="tf-mbody">
      {#if storeLoading}
        <div class="tf-mempty">loading…</div>
      {:else}
        {#if storeDetail?.blurb}<p class="tf-mblurb">{storeDetail.blurb}</p>{/if}
        {#if storeDetail?.rows?.length}
          <div class="tf-mtblwrap">
            <table class="tf-mtbl">
              <thead><tr>{#each storeDetail.columns as c}<th>{c}</th>{/each}</tr></thead>
              <tbody>
                {#each storeDetail.rows as row}
                  <tr>{#each row as cell}<td title={cell ?? ''}>{cell ?? '—'}</td>{/each}</tr>
                {/each}
              </tbody>
            </table>
          </div>
          {#if storeDetail.truncated}<div class="tf-mfoot">showing {storeDetail.rows.length} of {nfmt(storeDetail.count)}</div>{/if}
        {:else}
          <div class="tf-mempty">{storeDetail?.note ?? 'no rows yet'}</div>
        {/if}
      {/if}
    </div>
  </div>
{/if}

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
  .tf-badge-fin { color: #3a63b0; background: #e8eef9; border-color: #3a63b0; }
  .tf-badge-fail { color: #c0392b; background: #fdecea; border-color: #c0392b; }
  .tf-blink { width: 8px; height: 8px; border-radius: 50%; background: var(--pw-accent); animation: tf-blink 1.1s infinite; }
  .tf-blink-fin { width: 8px; height: 8px; border-radius: 50%; background: #3a63b0; animation: tf-blink 1.1s infinite; }
  @keyframes tf-blink { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
  .tf-fin-hint { color: #3a63b0; font-style: italic; }

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
  /* serpentine 2×5 grid — no horizontal scroll, continuous connected flow */
  .tf-schem { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); column-gap: 34px; row-gap: 40px; padding: 2px 2px 6px; }
  .tf-scard { position: relative; background: #fff; border: 1px solid var(--pw-border); border-top: 3px solid var(--c); padding: 9px 11px; cursor: pointer; transition: .2s; min-height: 84px; display: flex; flex-direction: column; gap: 3px; }
  .tf-scard:hover { box-shadow: 0 4px 12px rgba(0, 0, 0, .09); transform: translateY(-2px); }
  .tf-on { box-shadow: 0 0 0 2px var(--c); }
  .tf-sc-done { background: #fbfaf7; }
  .tf-st { font-size: 8px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: var(--pw-dim); display: flex; justify-content: space-between; }
  .tf-ck { color: var(--pw-success); }
  .tf-sl { font-size: 11.5px; font-weight: 800; color: var(--ci); }
  .tf-sd { font-size: 9px; color: #4a4438; font-family: var(--pw-mono); line-height: 1.4; white-space: pre-line; }
  .tf-sf { font-size: 8.5px; color: var(--pw-dim); margin-top: auto; font-family: var(--pw-mono); }

  /* directional connectors drawn into the grid gaps (dashed line + arrowhead) */
  .tf-arw { position: absolute; pointer-events: none; }
  /* horizontal — sits in the 34px column-gap */
  .tf-a-right, .tf-a-left { top: 50%; width: 34px; height: 2px; transform: translateY(-50%); }
  .tf-a-right { right: -34px; }
  .tf-a-left  { left: -34px; }
  .tf-a-right::before, .tf-a-left::before { content: ''; position: absolute; top: 0; height: 2px; background-image: repeating-linear-gradient(90deg, var(--c) 0 6px, transparent 6px 11px); background-size: 22px 2px; opacity: .4; }
  .tf-a-right::before { left: 0; right: 8px; }
  .tf-a-left::before  { right: 0; left: 8px; }
  .tf-a-right::after, .tf-a-left::after { content: ''; position: absolute; top: 50%; transform: translateY(-50%); width: 0; height: 0; border-top: 4px solid transparent; border-bottom: 4px solid transparent; opacity: .4; }
  .tf-a-right::after { right: 0; border-left: 6px solid var(--c); }
  .tf-a-left::after  { left: 0;  border-right: 6px solid var(--c); }
  /* vertical U-turn — sits in the 40px row-gap under the end-of-row card */
  .tf-a-down { left: 50%; bottom: -40px; width: 2px; height: 40px; transform: translateX(-50%); }
  .tf-a-down::before { content: ''; position: absolute; left: 0; top: 0; bottom: 8px; width: 2px; background-image: repeating-linear-gradient(0deg, var(--c) 0 6px, transparent 6px 11px); background-size: 2px 22px; opacity: .4; }
  .tf-a-down::after { content: ''; position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 6px solid var(--c); opacity: .4; }
  /* state: done = solid, running = animated dashes */
  .tf-arw.tf-dn::before, .tf-arw.tf-dn::after, .tf-arw.tf-flow::before, .tf-arw.tf-flow::after { opacity: 1; }
  .tf-a-right.tf-flow::before, .tf-a-left.tf-flow::before { animation: tf-sdash 1.3s linear infinite; }
  .tf-a-down.tf-flow::before { animation: tf-sdashv 1.3s linear infinite; }
  @keyframes tf-sdash { to { background-position: 22px 0; } }
  @keyframes tf-sdashv { to { background-position: 0 22px; } }

  /* body */
  .tf-body { display: grid; grid-template-columns: 1fr 290px; border: 1px solid var(--pw-border); border-top: none; }
  .tf-flow { padding: 8px 0; max-height: 560px; overflow: auto; border-right: 1px solid var(--pw-border); }
  .tf-layer { padding: 10px 18px; border-bottom: 1px solid var(--pw-border-soft); }
  .tf-ly-on { background: linear-gradient(90deg, var(--pw-accent-wash), transparent); }
  .tf-ltitle { font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; color: var(--pw-muted); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
  .tf-ltxt { flex: none; }
  .tf-lbadge { width: 16px; height: 16px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 9px; color: #fff; flex: none; }
  .tf-lb-done { background: var(--pw-success); }
  .tf-lb-running, .tf-lb-run { background: var(--pw-accent); animation: tf-rowglow 1.4s infinite; }
  .tf-lb-skipped { background: var(--pw-dim); }
  .tf-lb-error { background: #c0392b; }
  .tf-lb-idle { background: var(--pw-border); color: var(--pw-dim); }
  .tf-lmeta { font-size: 9px; font-weight: 700; letter-spacing: 0; text-transform: none; color: var(--pw-dim); font-family: var(--pw-mono); }
  .tf-gate { font-size: 9px; font-weight: 800; letter-spacing: .05em; color: #a8740b; background: #faf2e0; padding: 1px 6px; margin-left: auto; }
  .tf-step { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 12px; color: var(--pw-dim); }
  .tf-g { width: 14px; text-align: center; flex: none; }
  .tf-nm { color: var(--pw-ink-soft); min-width: 175px; }
  .tf-badge2 { font-size: 8px; font-weight: 800; padding: 1px 4px; color: #fff; flex: none; }
  .tf-dt { font-size: 10px; color: var(--pw-muted); font-family: var(--pw-mono); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tf-ms { font-size: 9px; color: var(--pw-dim); font-family: var(--pw-mono); flex: none; min-width: 38px; text-align: right; }
  .tf-wr { font-size: 9px; color: var(--pw-dim); font-family: var(--pw-mono); flex: none; }
  .tf-wr-link { background: none; border: none; cursor: pointer; padding: 0; }
  .tf-wr-link:hover { color: var(--pw-accent); text-decoration: underline; }
  .tf-s-error .tf-g { color: #c0392b; }
  .tf-s-error .tf-nm { color: #c0392b; font-weight: 700; }
  .tf-s-warn .tf-g { color: #a8740b; }
  .tf-s-warn .tf-nm { color: #a8740b; }
  .tf-s-done .tf-nm { color: var(--pw-ink); font-weight: 600; }
  .tf-s-done .tf-g { color: var(--pw-success); }
  .tf-s-run { color: var(--pw-accent-ink); animation: tf-rowglow 1.4s infinite; }
  .tf-s-run .tf-nm { color: var(--pw-accent-ink); font-weight: 700; }
  .tf-s-run .tf-g { color: var(--pw-accent); }
  @keyframes tf-rowglow { 0%, 100% { background: transparent; } 50% { background: var(--pw-accent-wash); } }
  .tf-s-gated { opacity: .55; }
  .tf-s-gated .tf-nm { text-decoration: line-through; }
  .tf-off { font-size: 8px; font-weight: 800; letter-spacing: .05em; color: var(--pw-muted); background: var(--pw-border); padding: 1px 5px; border-radius: 3px; flex: none; }

  /* rail */
  .tf-rail { display: flex; flex-direction: column; }
  .tf-stores { padding: 14px 16px; border-bottom: 1px solid var(--pw-border); }
  .tf-rh { font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; color: var(--pw-muted); margin: 0 0 10px; }
  .tf-store { width: 100%; text-align: left; font: inherit; color: inherit; display: flex; align-items: center; gap: 8px; padding: 7px 9px; border: 1px solid var(--pw-border); margin-bottom: 6px; transition: .25s; background: var(--pw-bg); cursor: pointer; }
  .tf-store:hover { border-color: var(--pw-accent); box-shadow: 0 2px 8px rgba(0,0,0,.07); }
  .tf-store:hover .tf-sgo { opacity: 1; transform: translateX(0); }
  .tf-si { font-size: 13px; width: 18px; text-align: center; }
  .tf-sn { font-size: 11px; font-weight: 600; flex: 1; }
  .tf-sc { font-size: 11px; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--pw-muted); }
  .tf-sgo { font-size: 14px; font-weight: 700; color: var(--pw-accent); opacity: 0; transform: translateX(-4px); transition: .2s; margin-left: 2px; }
  .tf-has .tf-sc { color: var(--pw-success); }
  .tf-hit { border-color: var(--pw-accent); background: var(--pw-accent-soft); transform: translateX(-2px); }

  /* store-detail modal */
  .tf-mback { position: fixed; inset: 0; background: rgba(20,16,12,.45); z-index: 998; }
  .tf-modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: min(720px, 92vw); max-height: 82vh; display: flex; flex-direction: column; background: var(--pw-surface, #fff); border: 1px solid var(--pw-border); box-shadow: 0 24px 64px rgba(0,0,0,.28); z-index: 999; }
  .tf-mhead { display: flex; align-items: center; gap: 11px; padding: 14px 16px; border-bottom: 1px solid var(--pw-border); }
  .tf-msi { font-size: 20px; }
  .tf-mtitle { flex: 1; min-width: 0; }
  .tf-ml { font-size: 15px; font-weight: 800; color: var(--pw-ink); }
  .tf-msub { font-size: 11px; font-family: var(--pw-mono); color: var(--pw-muted); margin-top: 2px; }
  .tf-mx { background: none; border: none; font-size: 16px; cursor: pointer; color: var(--pw-muted); padding: 4px 8px; }
  .tf-mx:hover { color: var(--pw-ink); }
  .tf-mbody { padding: 14px 16px; overflow: auto; }
  .tf-mblurb { font-size: 12px; color: var(--pw-ink-soft); line-height: 1.5; margin: 0 0 12px; }
  .tf-mtblwrap { border: 1px solid var(--pw-border); overflow: auto; max-height: 52vh; }
  .tf-mtbl { width: 100%; border-collapse: collapse; font-size: 11.5px; }
  .tf-mtbl th { position: sticky; top: 0; background: var(--pw-bg-alt, #f6f2ec); text-align: left; padding: 6px 10px; font-size: 9.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--pw-muted); border-bottom: 1px solid var(--pw-border); }
  .tf-mtbl td { padding: 6px 10px; border-bottom: 1px solid var(--pw-border-soft, #eee); color: var(--pw-ink-soft); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tf-mtbl tr:hover td { background: var(--pw-bg-alt, #f6f2ec); }
  .tf-mfoot { font-size: 10.5px; color: var(--pw-muted); text-align: right; margin-top: 8px; }
  .tf-mempty { font-size: 12px; color: var(--pw-muted); font-style: italic; padding: 18px 4px; text-align: center; }

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
