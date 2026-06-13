<script lang="ts">
  /* ════════════════════════════════════════════════════════════════════════
     BRAIN CORTEX — animated "real brain" view of the Agent Brain.
     ADDITIVE: renders inside BrainHub as the '__cortex__' tab. Reuses existing
     endpoints only (no backend change). Four modes: Anatomy / Synapses /
     Memory / Vitals. All animation is CSS + requestAnimationFrame.
     ════════════════════════════════════════════════════════════════════════ */
  import { onMount, onDestroy } from 'svelte';

  let { slug = 'citypharma' as string } = $props();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  async function _get(url: string): Promise<any> {
    try { const r = await fetch(url, { headers: _h() }); if (r.ok) return await r.json(); } catch {}
    return null;
  }

  let mode = $state<'anatomy' | 'synapses' | 'memory' | 'vitals'>('anatomy');

  /* ─── live data ─── */
  let stats = $state<any>({ by_category: {} });
  let mem = $state<any[]>([]);          // long-term (active facts)
  let insights = $state<any[]>([]);     // pending / approved / rejected (review gate)
  let log = $state<any[]>([]);          // access log → firing
  let graph = $state<any>({ nodes: [], edges: [] });
  let loaded = $state(false);

  function cat(k: string): number { return Number(stats?.by_category?.[k] ?? 0); }

  // region model: lobe → which categories feed it
  const REGIONS = [
    { id: 'frontal',  name: 'Frontal',   sub: 'KPI · Rules',        cats: ['kpi', 'rule', 'threshold', 'pattern'], color: 'var(--pw-accent, #c2683f)', cx: 250, cy: 150, r: 64, jump: 'rules' },
    { id: 'parietal', name: 'Parietal',  sub: 'Schema · Formulas',  cats: ['formula', 'definition', 'definitions'], color: '#4a7fa3', cx: 470, cy: 138, r: 56, jump: 'schema' },
    { id: 'temporal', name: 'Temporal',  sub: 'Glossary · Memory',  cats: ['glossary', 'alias'],            color: '#c2683f', cx: 300, cy: 290, r: 70, jump: 'glossary', hippo: true },
    { id: 'occipital',name: 'Occipital', sub: 'Graph',              cats: ['org'],                          color: '#9b6bb0', cx: 545, cy: 268, r: 60, jump: 'graph' },
  ];
  function regionCount(rg: any): number {
    if (rg.id === 'occipital') return (graph?.edges?.length ?? 0) || cat('org');
    if (rg.hippo) return rg.cats.reduce((s: number, c: string) => s + cat(c), 0) + mem.length;
    return rg.cats.reduce((s: number, c: string) => s + cat(c), 0);
  }
  // heat 0..1 from access log recency touching this region's name keywords
  function regionHeat(rg: any): number {
    const now = Date.now();
    let hot = 0;
    for (const e of log) {
      const t = new Date(e.accessed_at || e.created_at || e.ts || 0).getTime();
      if (!t || now - t > 864e5) continue;          // last 24h
      const name = ((e.category || e.entry_name || e.name || e.detail || '') + '').toLowerCase();
      if (name === 'all' || rg.cats.some((c: string) => name.includes(c)) || name.includes(rg.name.toLowerCase())) hot++;
    }
    if (mem.length && rg.hippo) hot += 2;
    return Math.min(1, hot / 4);
  }

  /* ─── vitals (computed client-side) ─── */
  let pendCount = $derived(insights.filter((i) => i.status === 'pending').length);
  let activeCount = $derived(mem.length + insights.filter((i) => i.status === 'approved').length);
  let totalMem = $derived(activeCount + pendCount);
  let freshness = $derived.by(() => {
    if (!mem.length) return 0;
    const now = Date.now();
    const fresh = mem.filter((m) => { const t = new Date(m.created_at || 0).getTime(); return t && now - t < 14 * 864e5; }).length;
    return Math.round((fresh / mem.length) * 100);
  });
  let blindSpots = $derived(REGIONS.filter((r) => regionCount(r) === 0).length);
  let learnedWk = $derived.by(() => {
    const now = Date.now();
    return [...mem, ...insights].filter((m) => { const t = new Date(m.created_at || 0).getTime(); return t && now - t < 7 * 864e5; }).length;
  });

  /* ─── memory lanes ─── */
  let lanePending = $derived(insights.filter((i) => i.status === 'pending'));
  let laneActive = $derived(mem.slice(0, 8));
  let laneLesion = $derived(insights.filter((i) => i.status === 'rejected').slice(0, 4));
  function freshPct(m: any): number {
    const t = new Date(m.created_at || 0).getTime();
    if (!t) return 60;
    const age = (Date.now() - t) / 864e5;
    return Math.max(8, Math.round(100 - age * 4));
  }
  async function approveInsight(id: number) {
    await fetch(`/api/insights/${id}/approve`, { method: 'POST', headers: _h() }).catch(() => {});
    insights = insights.map((i) => (i.id === id ? { ...i, status: 'approved' } : i));
  }
  async function rejectInsight(id: number) {
    await fetch(`/api/insights/${id}/reject`, { method: 'POST', headers: _h() }).catch(() => {});
    insights = insights.map((i) => (i.id === id ? { ...i, status: 'rejected' } : i));
  }

  /* ─── synapses (custom animated SVG) ─── */
  type Neuron = { x: number; y: number; vx: number; vy: number; r: number; c: string; label?: string };
  let neurons = $state<Neuron[]>([]);
  let synapses = $state<Array<{ a: number; b: number; fire: number }>>([]);
  let raf = 0;
  const CAT_COLORS = ['#c2683f', '#4a7fa3', '#9b6bb0', '#5a9367', '#c5934a'];

  function buildNet() {
    const W = 760, H = 400;
    const nodes = (graph?.nodes || []).slice(0, 46);
    if (!nodes.length) {
      // fallback synthetic net so the mode is never empty
      for (let i = 0; i < 34; i++) nodes.push({ id: i, label: '', group: i % 5 });
    }
    const N: Neuron[] = nodes.map((n: any, i: number) => ({
      x: 60 + Math.random() * 640, y: 40 + Math.random() * 320,
      vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
      r: 5 + Math.random() * 9, c: CAT_COLORS[i % 5],
      label: i < 6 ? (n.label || n.name || '') : '',
    }));
    const E: Array<{ a: number; b: number; fire: number }> = [];
    const ge = graph?.edges || [];
    if (ge.length) {
      const idMap = new Map(nodes.map((n: any, i: number) => [String(n.id), i]));
      for (const e of ge.slice(0, 90)) {
        const a = idMap.get(String(e.source)), b = idMap.get(String(e.target));
        if (a != null && b != null && a < N.length && b < N.length) E.push({ a, b, fire: 0 });
      }
    }
    if (!E.length) for (let i = 0; i < N.length; i++) for (let j = 0; j < 2; j++) { const t = Math.floor(Math.random() * N.length); if (t !== i) E.push({ a: i, b: t, fire: 0 }); }
    neurons = N; synapses = E;
  }
  function tick() {
    const W = 760, H = 400;
    const N = neurons;
    for (const n of N) {
      n.x += n.vx; n.y += n.vy;
      if (n.x < 30 || n.x > W - 30) n.vx *= -1;
      if (n.y < 30 || n.y > H - 30) n.vy *= -1;
    }
    // randomly fire a few synapses
    for (const s of synapses) { if (s.fire > 0) s.fire -= 0.02; }
    if (Math.random() < 0.25 && synapses.length) { const s = synapses[Math.floor(Math.random() * synapses.length)]; s.fire = 1; }
    neurons = [...N]; synapses = [...synapses];
    if (mode === 'synapses') raf = requestAnimationFrame(tick);
  }

  /* ─── EEG line (vitals) ─── */
  let eegPts = $state('');
  let eegRaf = 0; let eegT = 0;
  function eegTick() {
    eegT += 1;
    let pts = '';
    const beat = Math.max(0.3, Math.min(2.5, (log.length % 7) / 3 + 0.6));
    for (let x = 0; x <= 740; x += 6) {
      const phase = (x + eegT * 3) / 18;
      const y = 45 + Math.sin(phase) * 14 * (0.5 + 0.5 * Math.sin(phase / 4)) + Math.sin(phase * 2.7) * 6 * beat;
      pts += `${x},${y.toFixed(1)} `;
    }
    eegPts = pts;
    if (mode === 'vitals') eegRaf = requestAnimationFrame(eegTick);
  }

  $effect(() => {
    cancelAnimationFrame(raf); cancelAnimationFrame(eegRaf);
    if (mode === 'synapses') { if (!neurons.length) buildNet(); raf = requestAnimationFrame(tick); }
    if (mode === 'vitals') { eegRaf = requestAnimationFrame(eegTick); }
  });

  onMount(async () => {
    const [st, m, ins, lg, gr] = await Promise.all([
      _get('/api/brain/stats'),
      _get(`/api/projects/${slug}/memories`),
      _get(`/api/projects/${slug}/insights`),
      _get('/api/brain/log'),
      _get(`/api/projects/${slug}/graph?source=pharma`),
    ]);
    if (st) stats = st;
    if (m) mem = m.memories || m || [];
    if (ins) insights = ins.insights || [];
    if (lg) log = lg.logs || lg.log || lg.entries || lg || [];
    if (gr) graph = gr;
    loaded = true;
    buildNet();
  });
  onDestroy(() => { cancelAnimationFrame(raf); cancelAnimationFrame(eegRaf); });

  function jump(item: string) {
    // drive the settings/brain rail to the existing tab via hash, then scroll up
    try { window.location.hash = item; } catch {}
    window.dispatchEvent(new CustomEvent('brain-jump', { detail: item }));
  }
</script>

<div class="cx">
  <!-- hero -->
  <div class="cx-hero">
    <span class="cx-brain">🧠</span>
    <span class="cx-title">CORTEX</span>
    <span class="cx-live"><span class="cx-dot"></span>LIVE</span>
    <div class="cx-vit">
      <span><b>{totalMem}</b> memories</span>
      <span>❤ <b>{freshness}%</b> healthy</span>
      <span>⚠ <b>{blindSpots}</b> blind</span>
      <span>▲ <b>+{learnedWk}</b>/wk</span>
    </div>
  </div>

  <!-- mode switch -->
  <div class="cx-modes">
    <button class:on={mode === 'anatomy'} onclick={() => (mode = 'anatomy')}>Anatomy</button>
    <button class:on={mode === 'synapses'} onclick={() => (mode = 'synapses')}>Synapses</button>
    <button class:on={mode === 'memory'} onclick={() => (mode = 'memory')}>Memory</button>
    <button class:on={mode === 'vitals'} onclick={() => (mode = 'vitals')}>Vitals</button>
  </div>

  <div class="cx-panel">
    {#if mode === 'anatomy'}
      <svg viewBox="0 0 760 430" width="100%" height="430">
        <path d="M120,210 C90,120 200,40 330,60 C400,30 520,40 600,100 C700,120 720,230 640,300 C620,370 520,400 420,380 C320,400 200,380 150,320 C100,300 110,250 120,210 Z"
              fill="#fbf6f1" stroke="var(--pw-accent, #c2683f)" stroke-opacity="0.35" stroke-width="2"/>
        <path d="M360,70 C380,160 360,260 380,370" fill="none" stroke="var(--pw-accent, #c2683f)" stroke-opacity="0.3" stroke-width="1.5" stroke-dasharray="3 4"/>
        {#each REGIONS as rg}
          {@const heat = regionHeat(rg)}
          <g class="cx-lobe" role="button" tabindex="0" onclick={() => jump(rg.jump)} onkeydown={(e) => e.key === 'Enter' && jump(rg.jump)}>
            <circle cx={rg.cx} cy={rg.cy} r={rg.r} fill={rg.color} opacity={0.55 + heat * 0.45}>
              {#if heat > 0.4}<animate attributeName="r" values="{rg.r};{rg.r + 4};{rg.r}" dur="2.2s" repeatCount="indefinite"/>{/if}
            </circle>
            {#if heat > 0.4}
              <circle cx={rg.cx} cy={rg.cy} r={rg.r} fill="none" stroke={rg.color} stroke-width="2" opacity="0.5">
                <animate attributeName="r" values="{rg.r};{rg.r + 16}" dur="2.2s" repeatCount="indefinite"/>
                <animate attributeName="opacity" values="0.5;0" dur="2.2s" repeatCount="indefinite"/>
              </circle>
            {/if}
            <text class="cx-ll" x={rg.cx} y={rg.cy - 14} text-anchor="middle">{rg.name}{#if rg.hippo} ⚡{/if}</text>
            <text class="cx-ln" x={rg.cx} y={rg.cy + 6} text-anchor="middle">{rg.sub}</text>
            <text class="cx-lc" x={rg.cx} y={rg.cy + 26} text-anchor="middle">{regionCount(rg)}</text>
          </g>
        {/each}
        <!-- brainstem firing -->
        <path d="M380,360 C360,392 320,412 285,424" stroke="var(--pw-accent, #c2683f)" stroke-width="3" fill="none" stroke-dasharray="4 4">
          <animate attributeName="stroke-dashoffset" values="0;-16" dur="0.8s" repeatCount="indefinite"/>
        </path>
        <circle cx="285" cy="424" r="6" fill="var(--pw-accent, #c2683f)"><animate attributeName="opacity" values="1;0.3;1" dur="1.4s" repeatCount="indefinite"/></circle>
      </svg>
      <div class="cx-leg">
        <span><i class="sw" style="background:var(--pw-accent,#c2683f)"></i> hot — queried &lt;24h (pulsing)</span>
        <span><i class="sw" style="background:#cdc6ba"></i> dim — cold / blind spot</span>
        <span>⚡ hippocampus — facts forming</span>
        <span>● brainstem — live firing</span>
        <span style="margin-left:auto;color:var(--pw-accent,#c2683f)">click a lobe → opens that tab ↑</span>
      </div>

    {:else if mode === 'synapses'}
      <div class="cx-cap">Drug network ({graph?.nodes?.length ?? 0} nodes) as a neural net · fired synapse glows · idle grey</div>
      <svg viewBox="0 0 760 400" width="100%" height="400" class="cx-net">
        {#each synapses as s}
          <line x1={neurons[s.a]?.x} y1={neurons[s.a]?.y} x2={neurons[s.b]?.x} y2={neurons[s.b]?.y}
                stroke={s.fire > 0 ? 'var(--pw-accent, #c2683f)' : '#e2dccf'}
                stroke-width={s.fire > 0 ? 1.4 + s.fire * 1.8 : 1.2}
                opacity={s.fire > 0 ? 0.5 + s.fire * 0.5 : 0.55}/>
        {/each}
        {#each neurons as n, i}
          <circle cx={n.x} cy={n.y} r={n.r} fill={n.c} opacity="0.88"/>
          {#if n.label}<text class="cx-nl" x={n.x + n.r + 3} y={n.y + 3}>{n.label}</text>{/if}
        {/each}
      </svg>

    {:else if mode === 'memory'}
      <div class="cx-cap">Consolidation — <b>{lanePending.length}</b> forming · <b>{laneActive.length}</b> long-term · <b>{laneLesion.length}</b> lesion. Approve a forming fact to cement it.</div>
      <div class="cx-lanes">
        <div class="cx-lane">
          <h4>Short-term · review gate</h4>
          {#if !lanePending.length}<div class="cx-empty">nothing forming</div>{/if}
          {#each lanePending as it}
            <div class="cx-m pending">
              <div class="cx-mt">{it.title || it.detail || 'learned fact'}</div>
              <div class="cx-mb"><button class="cx-mbtn ok" onclick={() => approveInsight(it.id)}>✓ approve</button><button class="cx-mbtn no" onclick={() => rejectInsight(it.id)}>✕ reject</button></div>
            </div>
          {/each}
        </div>
        <div class="cx-lane">
          <h4>Long-term (active)</h4>
          {#if !laneActive.length}<div class="cx-empty">no active memories yet</div>{/if}
          {#each laneActive as it}
            {@const f = freshPct(it)}
            <div class="cx-m active">
              <div class="cx-mt">{it.fact || it.title}</div>
              <div class="cx-bar"><i style="width:{f}%"></i></div>
              {#if f < 50}<div class="cx-fade">fading · {it.created_at ? new Date(it.created_at).toLocaleDateString() : ''}</div>{/if}
            </div>
          {/each}
        </div>
        <div class="cx-lane">
          <h4>Lesion (rejected)</h4>
          {#if !laneLesion.length}<div class="cx-empty">none</div>{/if}
          {#each laneLesion as it}
            <div class="cx-m lesion"><div class="cx-mt">{it.title || it.detail}</div></div>
          {/each}
        </div>
      </div>

    {:else}
      <div class="cx-eeg">
        <div class="cx-cap" style="margin:0 0 6px">Brainwave — query firing / min (live)</div>
        <svg viewBox="0 0 740 90" width="100%" height="90"><polyline points={eegPts} fill="none" stroke="var(--pw-accent, #c2683f)" stroke-width="2"/></svg>
      </div>
      <div class="cx-chips">
        <div class="cx-chip"><div class="cv">{totalMem}</div><div class="ck">❤ memories</div></div>
        <div class="cx-chip"><div class="cv">{freshness}%</div><div class="ck">◷ freshness</div></div>
        <div class="cx-chip"><div class="cv">{activeCount}</div><div class="ck">long-term</div></div>
        <div class="cx-chip"><div class="cv">{pendCount}</div><div class="ck">⏳ forming</div></div>
        <div class="cx-chip"><div class="cv">{blindSpots}</div><div class="ck">⚠ blind spots</div></div>
        <div class="cx-chip"><div class="cv">+{learnedWk}</div><div class="ck">▲ learned / wk</div></div>
      </div>
    {/if}
  </div>
</div>

<style>
  .cx { font-family: var(--pw-font-body, 'Inter', system-ui, sans-serif); }
  .cx-hero { display: flex; align-items: center; gap: 12px; background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius, 12px); padding: 14px 18px; margin-bottom: 12px; }
  .cx-brain { font-size: 22px; }
  .cx-title { font-size: 15px; font-weight: 800; letter-spacing: 0.04em; }
  .cx-live { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700; color: #5a9367; }
  .cx-dot { width: 8px; height: 8px; border-radius: 50%; background: #5a9367; animation: cxp 1.6s infinite; }
  @keyframes cxp { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .cx-vit { margin-left: auto; display: flex; gap: 16px; font-size: 12px; color: var(--pw-muted, #6b6358); }
  .cx-vit b { color: var(--pw-ink, #211e1a); }

  .cx-modes { display: flex; gap: 6px; margin-bottom: 12px; }
  .cx-modes button { border: 1px solid var(--pw-border, #e7e0d4); background: var(--pw-surface, #fff); border-radius: 999px; padding: 7px 16px; font: inherit; font-weight: 600; font-size: 12.5px; cursor: pointer; color: var(--pw-muted, #6b6358); }
  .cx-modes button.on { background: #fdf2ec; border-color: var(--pw-accent, #c2683f); color: var(--pw-accent, #c2683f); }

  .cx-panel { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius, 12px); padding: 18px; min-height: 470px; }

  .cx-lobe { cursor: pointer; transition: 0.15s; }
  .cx-lobe:hover { filter: brightness(1.05); }
  .cx-ll { font-size: 11px; font-weight: 800; fill: #fff; text-transform: uppercase; letter-spacing: 0.04em; pointer-events: none; }
  .cx-ln { font-size: 12px; font-weight: 700; fill: #fff; pointer-events: none; }
  .cx-lc { font-size: 12px; font-weight: 800; fill: #fff; fill-opacity: 0.8; pointer-events: none; }
  .cx-leg { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 12px; font-size: 11.5px; color: var(--pw-muted, #6b6358); }
  .cx-leg span { display: inline-flex; align-items: center; gap: 6px; }
  .sw { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }

  .cx-cap { font-size: 12px; color: var(--pw-muted, #6b6358); margin-bottom: 10px; }
  .cx-net { background: linear-gradient(180deg, #fbf9f5, #fff); border-radius: var(--pw-radius-sm, 8px); }
  .cx-nl { font-size: 10px; fill: var(--pw-muted, #6b6358); pointer-events: none; }

  .cx-lanes { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
  .cx-lane h4 { margin: 0 0 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted, #6b6358); }
  .cx-empty { font-size: 12px; color: #b6ad9e; padding: 10px 0; }
  .cx-m { border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius-sm, 8px); padding: 9px 11px; margin-bottom: 8px; font-size: 12px; background: var(--pw-bg-alt, #f6f2ea); }
  .cx-m.pending { border-left: 3px solid #c5934a; }
  .cx-m.active { border-left: 3px solid #5a9367; }
  .cx-m.lesion { border-left: 3px solid #c0392b; text-decoration: line-through; opacity: 0.6; }
  .cx-mt { line-height: 1.35; }
  .cx-mb { margin-top: 6px; }
  .cx-mbtn { font: inherit; font-size: 10.5px; border: 1px solid var(--pw-border, #e7e0d4); background: #fff; border-radius: 6px; padding: 3px 9px; cursor: pointer; margin-right: 5px; }
  .cx-mbtn.ok:hover { border-color: #5a9367; color: #5a9367; }
  .cx-mbtn.no:hover { border-color: #c0392b; color: #c0392b; }
  .cx-bar { height: 5px; border-radius: 3px; background: var(--pw-border, #e7e0d4); margin-top: 6px; overflow: hidden; }
  .cx-bar i { display: block; height: 100%; background: linear-gradient(90deg, #5a9367, #c5934a); }
  .cx-fade { font-size: 10px; color: var(--pw-muted, #6b6358); margin-top: 3px; }

  .cx-eeg { background: var(--pw-bg-alt, #f6f2ea); border-radius: var(--pw-radius-sm, 8px); padding: 14px; margin-bottom: 14px; }
  .cx-chips { display: flex; gap: 12px; flex-wrap: wrap; }
  .cx-chip { border: 1px solid var(--pw-border, #e7e0d4); border-radius: var(--pw-radius-sm, 8px); padding: 12px 16px; background: var(--pw-surface, #fff); flex: 1; min-width: 140px; }
  .cx-chip .cv { font-size: 22px; font-weight: 800; }
  .cx-chip .ck { font-size: 11px; color: var(--pw-muted, #6b6358); text-transform: uppercase; letter-spacing: 0.05em; }
</style>
