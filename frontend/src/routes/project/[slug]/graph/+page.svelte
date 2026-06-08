<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  const slug = $derived(page.params.slug || '');

  let container: HTMLDivElement;
  let renderer: any = null;
  let graph: any = null;
  let layout: any = null;            // continuous FA2 supervisor
  let Graph: any, Sigma: any, forceAtlas2: any, FA2Worker: any;
  let animating = $state(true);

  let source = $state<'pharma' | 'brain'>('pharma');
  let focus = $state('');
  let searchInput = $state('');
  let live = $state(false);
  let loading = $state(true);
  let meta = $state<{ nodes: number; edges: number }>({ nodes: 0, edges: 0 });
  let selected = $state<any>(null);
  let detail = $state<any>(null);
  let detailLoading = $state(false);
  let clinicalOpen = $state(false);
  let timer: any = null;

  async function loadDetail(id: string) {
    detail = null; detailLoading = true; clinicalOpen = false;
    try {
      const r = await fetch(`/api/projects/${slug}/graph/node?id=${encodeURIComponent(id)}`, { headers: _h() });
      if (r.ok) detail = await r.json();
    } catch {} finally { detailLoading = false; }
  }
  function closeDetail() { selected = null; detail = null; }
  function fmt(n: number | null | undefined) {
    return (n === null || n === undefined) ? '—' : Number(n).toLocaleString();
  }

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  async function fetchGraph(): Promise<any> {
    const params = new URLSearchParams();
    params.set('source', source);
    if (source === 'pharma' && focus) { params.set('focus', focus); params.set('hops', '2'); }
    params.set('limit', source === 'pharma' ? (focus ? '600' : '4000') : '2000');
    try {
      const r = await fetch(`/api/projects/${slug}/graph?${params}`, { headers: _h() });
      if (!r.ok) return null;
      return await r.json();
    } catch { return null; }
  }

  function buildGraph(data: any) {
    if (!Graph || !container) return;
    if (layout) { try { layout.kill(); } catch {} layout = null; }
    if (renderer) { renderer.kill(); renderer = null; }
    graph = new Graph();
    const nodes = data?.nodes || [];
    const edges = data?.edges || [];
    meta = { nodes: data?.node_count || nodes.length, edges: data?.edge_count || edges.length };

    for (const n of nodes) {
      if (graph.hasNode(n.id)) continue;
      graph.addNode(n.id, {
        label: n.label,
        size: 2 + Math.min(14, Math.sqrt(n.val || 1) * 1.6),
        color: n.color || '#9aa0b5',
        x: Math.random(), y: Math.random(),
        group: n.group,
      });
    }
    for (const e of edges) {
      if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) continue;
      try { graph.addEdge(e.source, e.target, { color: 'rgba(180,170,200,0.10)', size: 0.4 }); } catch {}
    }

    if (graph.order > 0) {
      const settings = forceAtlas2.inferSettings(graph);
      settings.barnesHutOptimize = graph.order > 1000;
      settings.scalingRatio = 9;
      settings.gravity = 1.2;
      settings.slowDown = 4;
      // quick pre-settle so it opens already spread out…
      forceAtlas2.assign(graph, { iterations: graph.order > 1500 ? 60 : 150, settings });
      // …then keep running continuously for live Obsidian-style motion
      layout = new FA2Worker(graph, { settings });
      if (animating) layout.start();
    }

    // dark hover label (sigma default draws a WHITE pill → white-on-white). Override.
    const drawDarkHover = (ctx: CanvasRenderingContext2D, data: any, settings: any) => {
      const size = settings.labelSize, font = settings.labelFont, weight = settings.labelWeight;
      ctx.font = `${weight} ${size}px ${font}`;
      const label = data.label;
      if (!label) return;
      const w = ctx.measureText(label).width + 16;
      const x = Math.round(data.x), y = Math.round(data.y), h = size + 10, r = 5;
      ctx.beginPath();
      ctx.moveTo(x + data.size + 6, y - h / 2);
      ctx.arcTo(x + data.size + 6 + w, y - h / 2, x + data.size + 6 + w, y + h / 2, r);
      ctx.arcTo(x + data.size + 6 + w, y + h / 2, x + data.size + 6, y + h / 2, r);
      ctx.arcTo(x + data.size + 6, y + h / 2, x + data.size + 6, y - h / 2, r);
      ctx.arcTo(x + data.size + 6, y - h / 2, x + data.size + 6 + w, y - h / 2, r);
      ctx.closePath();
      ctx.fillStyle = 'rgba(25,21,33,0.92)';
      ctx.fill();
      ctx.strokeStyle = '#3a3346'; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = '#f5f2fb';
      ctx.fillText(label, x + data.size + 14, y + size / 3);
    };

    renderer = new Sigma(graph, container, {
      defaultEdgeColor: 'rgba(180,170,200,0.10)',
      labelColor: { color: '#f5f2fb' },
      labelFont: 'ui-sans-serif, system-ui, sans-serif',
      labelSize: 12,
      labelWeight: '600',
      labelRenderedSizeThreshold: source === 'brain' ? 0 : 11, // brain: always; pharma: on zoom
      labelDensity: 0.7,
      zIndex: true,
      defaultDrawNodeHover: drawDarkHover,
    });

    let hovered: string | null = null;
    let neighbors = new Set<string>();

    renderer.setSetting('nodeReducer', (node: string, data: any) => {
      if (hovered && node !== hovered && !neighbors.has(node)) {
        return { ...data, color: '#322e3a', label: '', zIndex: 0 };
      }
      if (hovered && (node === hovered || neighbors.has(node))) {
        return { ...data, zIndex: 2, label: data.label };
      }
      return data;
    });
    renderer.setSetting('edgeReducer', (edge: string, data: any) => {
      if (hovered && !graph.hasExtremity(edge, hovered)) return { ...data, hidden: true };
      if (hovered) return { ...data, color: 'rgba(201,99,66,0.5)', size: 1 };
      return data;
    });

    renderer.on('enterNode', (e: any) => {
      hovered = e.node;
      neighbors = new Set(graph.neighbors(e.node));
      renderer.refresh();
    });
    renderer.on('leaveNode', () => { hovered = null; neighbors = new Set(); renderer.refresh(); });
    renderer.on('clickNode', (e: any) => {
      selected = { id: e.node, ...graph.getNodeAttributes(e.node), degree: graph.degree(e.node) };
      if (source === 'pharma') loadDetail(e.node);
    });
  }

  async function reload() {
    loading = true;
    const data = await fetchGraph();
    buildGraph(data);
    loading = false;
  }

  function setSource(s: 'pharma' | 'brain') {
    if (s === source) return;
    source = s; focus = ''; searchInput = ''; closeDetail();
    reload();
  }
  function doSearch() {
    focus = searchInput.trim();
    closeDetail();
    source = 'pharma';
    reload();
  }
  function egoFocus(id: string) {
    searchInput = id; focus = id; source = 'pharma'; closeDetail(); reload();
  }
  function clearFocus() { focus = ''; searchInput = ''; reload(); }
  function onKey(e: KeyboardEvent) { if (e.key === 'Escape') closeDetail(); }

  onMount(async () => {
    const [g, s, f, fw] = await Promise.all([
      import('graphology'),
      import('sigma'),
      import('graphology-layout-forceatlas2'),
      import('graphology-layout-forceatlas2/worker'),
    ]);
    Graph = g.default; Sigma = s.default; forceAtlas2 = f.default; FA2Worker = fw.default;
    await reload();
    timer = setInterval(() => { if (live) reload(); }, 15000);
  });
  function toggleAnimate() {
    animating = !animating;
    if (!layout) return;
    try { animating ? layout.start() : layout.stop(); } catch {}
  }

  onDestroy(() => {
    if (timer) clearInterval(timer);
    if (layout) { try { layout.kill(); } catch {} }
    if (renderer) renderer.kill();
  });
</script>

<div class="gv-root">
  <!-- toolbar -->
  <div class="gv-bar">
    <button class="gv-back" onclick={() => goto(`${base}/project/${slug}/overview`)}>← Dashboard</button>
    <div class="gv-seg">
      <button class:on={source === 'pharma'} onclick={() => setSource('pharma')}>AGE Pharma</button>
      <button class:on={source === 'brain'} onclick={() => setSource('brain')}>Brain KG</button>
    </div>
    {#if source === 'pharma'}
      <div class="gv-search">
        <input placeholder="focus a drug (brand name)…" bind:value={searchInput}
          onkeydown={(e) => e.key === 'Enter' && doSearch()} />
        <button onclick={doSearch}>Focus</button>
        {#if focus}<button class="gv-clear" onclick={clearFocus}>✕ {focus}</button>{/if}
      </div>
    {/if}
    <div class="gv-spacer"></div>
    <span class="gv-count">{meta.nodes.toLocaleString()} nodes · {meta.edges.toLocaleString()} edges</span>
    <button class="gv-live" class:on={animating} onclick={toggleAnimate}>{animating ? '✦ animating' : '❄ frozen'}</button>
    <button class="gv-live" class:on={live} onclick={() => (live = !live)}>{live ? '⟳ live 15s' : '⏸ static'}</button>
    <button class="gv-live" onclick={reload}>↻</button>
  </div>

  <!-- canvas -->
  <div class="gv-canvas-wrap">
    <div bind:this={container} class="gv-canvas"></div>
    {#if loading}<div class="gv-loading">building force layout…</div>{/if}
    {#if !loading && meta.nodes === 0}
      <div class="gv-loading">no graph data{source === 'pharma' && focus ? ` for “${focus}”` : ''}</div>
    {/if}

    <!-- legend -->
    <div class="gv-legend">
      {#if source === 'pharma'}
        <span><i style="background:#c96342"></i> drug · size = substitute count</span>
      {:else}
        <span><i style="background:#9aa0b5"></i> KG entity · size = links</span>
      {/if}
      <span class="gv-hint">drag · scroll zoom · hover = neighbors · click = node</span>
    </div>

    <!-- right detail panel -->
    {#if selected}
      <aside class="gv-detail">
        <header class="gd-head">
          <span class="gd-dot" style="background:{selected.color || '#c96342'}"></span>
          <h3>{selected.label}</h3>
          <button class="gd-x" onclick={closeDetail} aria-label="close">✕</button>
        </header>
        {#if (detail?.identity?.category) || selected.group}
          <div class="gd-chip">{detail?.identity?.category || selected.group}</div>
        {/if}

        {#if source !== 'pharma'}
          <section class="gd-sec">
            <div class="gd-row"><label>group</label><span>{selected.group || '—'}</span></div>
            <div class="gd-row"><label>links</label><span>{selected.degree}</span></div>
          </section>
        {:else if detailLoading}
          <div class="gd-load">loading detail…</div>
        {:else if detail}
          <!-- IDENTITY -->
          <section class="gd-sec">
            <h4>Identity</h4>
            <div class="gd-row"><label>generic</label><span>{detail.identity.generic || '—'}</span></div>
            {#if detail.identity.composition}<div class="gd-row"><label>compose</label><span>{detail.identity.composition}</span></div>{/if}
            <div class="gd-row"><label>code</label><span>{detail.identity.article_code || '—'}{#if detail.identity.status} · <i class="gd-stat">{detail.identity.status}</i>{/if}</span></div>
            {#if detail.identity.mmreg}<div class="gd-row"><label>reg</label><span>{detail.identity.mmreg}</span></div>{/if}
          </section>

          <!-- CLINICAL -->
          {#if detail.clinical.indication || detail.clinical.dosage || detail.clinical.side_effect}
            <section class="gd-sec">
              <h4 class="gd-coll" onclick={() => (clinicalOpen = !clinicalOpen)}>Clinical <span>{clinicalOpen ? '▾' : '▸'}</span></h4>
              {#if clinicalOpen}
                {#if detail.clinical.indication}<div class="gd-row"><label>indication</label><span>{detail.clinical.indication}</span></div>{/if}
                {#if detail.clinical.dosage}<div class="gd-row"><label>dosage</label><span>{detail.clinical.dosage}</span></div>{/if}
                {#if detail.clinical.side_effect}<div class="gd-row gd-warn"><label>⚠ side fx</label><span>{detail.clinical.side_effect}</span></div>{/if}
              {/if}
            </section>
          {/if}

          <!-- AVAILABILITY -->
          <section class="gd-sec">
            <h4>Availability</h4>
            {#if detail.stock && (detail.stock.total || detail.stock.stores)}
              <div class="gd-stockline"><b>{fmt(detail.stock.total)}</b> units · {fmt(detail.stock.stores)} stores</div>
              {#if detail.stock.avg_cost}<div class="gd-row"><label>avg cost</label><span>{fmt(detail.stock.avg_cost)} MMK</span></div>{/if}
              {#each detail.stores as st}
                {@const max = detail.stores[0]?.qty || 1}
                <div class="gd-bar"><span class="gd-site">{st.site}</span><i style="width:{Math.max(4, (st.qty / max) * 100)}%"></i><span class="gd-q">{fmt(st.qty)}</span></div>
              {/each}
            {:else}
              <div class="gd-empty">no stock records</div>
            {/if}
          </section>

          <!-- SUBSTITUTES -->
          {#if detail.substitutes?.length}
            <section class="gd-sec">
              <h4>Substitutes ({detail.substitutes.length})</h4>
              {#each detail.substitutes as s}
                <button class="gd-sub" onclick={() => egoFocus(s.brand)}>
                  <span class="gd-arrow">→</span>
                  <span class="gd-subname">{s.brand}</span>
                  {#if s.qty !== null && s.qty !== undefined}
                    <span class="gd-badge" class:out={s.qty === 0}>{s.qty === 0 ? '○' : '▣'} {fmt(s.qty)}</span>
                  {/if}
                </button>
              {/each}
            </section>
          {/if}

          <!-- GRAPH -->
          <section class="gd-sec">
            <h4>Graph</h4>
            <div class="gd-row"><label>direct subs</label><span>{selected.degree}</span></div>
            <div class="gd-row"><label>cluster</label><span>{detail.identity.category || selected.group || '—'}</span></div>
            <div class="gd-note">node size = substitute count</div>
          </section>
        {/if}

        {#if source === 'pharma'}
          <div class="gd-acts">
            <button class="gd-focus" onclick={() => egoFocus(selected.id)}>Focus 2-hop</button>
          </div>
        {/if}
      </aside>
    {/if}
  </div>
</div>

<svelte:window onkeydown={onKey} />

<style>
  .gv-root { position: fixed; inset: 76px 0 0 0; display: flex; flex-direction: column; background: #16131a; }
  .gv-bar { display: flex; align-items: center; gap: 14px; margin: 18px 12px 0; padding: 12px 16px; background: rgba(29,25,37,0.82); border: 1px solid #2a2533; border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,0.45); backdrop-filter: blur(6px); flex-wrap: wrap; position: relative; z-index: 5; }
  .gv-back { background: none; border: 1px solid #3a3346; color: #cfc9dd; font-size: 12px; padding: 5px 10px; cursor: pointer; border-radius: 4px; }
  .gv-back:hover { border-color: #c96342; color: #fff; }
  .gv-seg { display: flex; border: 1px solid #3a3346; border-radius: 4px; overflow: hidden; }
  .gv-seg button { background: #221d2b; border: none; color: #9088a0; font-size: 12px; padding: 5px 12px; cursor: pointer; font-weight: 600; }
  .gv-seg button.on { background: #c96342; color: #fff; }
  .gv-search { display: flex; align-items: center; gap: 6px; }
  .gv-search input { background: #221d2b; border: 1px solid #3a3346; color: #e8e3f0; font-size: 12px; padding: 5px 10px; border-radius: 4px; width: 200px; }
  .gv-search button { background: #2c2636; border: 1px solid #3a3346; color: #cfc9dd; font-size: 12px; padding: 5px 10px; cursor: pointer; border-radius: 4px; }
  .gv-clear { color: #e0a458 !important; }
  .gv-spacer { flex: 1; }
  .gv-count { font-size: 11px; color: #a9a0c0; font-family: ui-monospace, monospace; background: #221d2b; border: 1px solid #3a3346; padding: 4px 10px; border-radius: 20px; }
  .gv-live { background: #221d2b; border: 1px solid #3a3346; color: #9088a0; font-size: 11px; padding: 5px 10px; cursor: pointer; border-radius: 4px; }
  .gv-live.on { border-color: #3ec9a7; color: #3ec9a7; }

  .gv-canvas-wrap { position: relative; flex: 1; overflow: hidden; }
  .gv-canvas { position: absolute; inset: 0; }
  .gv-loading { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #6e6680; font-size: 13px; pointer-events: none; }

  .gv-legend { position: absolute; bottom: 12px; left: 14px; display: flex; flex-direction: column; gap: 4px; font-size: 10.5px; color: #8880a0; pointer-events: none; }
  .gv-legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
  .gv-hint { opacity: 0.6; }

  /* ---- right detail panel (overlay) ---- */
  .gv-detail { position: absolute; top: 0; right: 0; bottom: 0; width: 340px;
    background: rgba(23,20,29,0.97); border-left: 1px solid #2a2533;
    box-shadow: -10px 0 40px rgba(0,0,0,0.5); backdrop-filter: blur(8px);
    overflow-y: auto; z-index: 6; padding: 0 0 20px; }
  .gd-head { display: flex; align-items: flex-start; gap: 8px; padding: 16px 16px 8px;
    position: sticky; top: 0; background: rgba(23,20,29,0.98); border-bottom: 1px solid #2a2533; }
  .gd-dot { width: 11px; height: 11px; border-radius: 50%; margin-top: 4px; flex: none; }
  .gd-head h3 { flex: 1; margin: 0; font-size: 14px; font-weight: 700; color: #f3eef9; line-height: 1.35; }
  .gd-x { background: none; border: none; color: #9088a0; font-size: 14px; cursor: pointer; padding: 2px 4px; }
  .gd-x:hover { color: #fff; }
  .gd-chip { margin: 10px 16px 0; display: inline-block; align-self: flex-start;
    background: #2c2636; color: #cdb4ff; font-size: 11px; font-weight: 600;
    padding: 4px 11px; border-radius: 20px; border: 1px solid #3a3346; }
  .gd-load, .gd-empty, .gd-note { color: #6e6680; font-size: 11.5px; padding: 8px 16px; }
  .gd-empty { padding: 4px 0; }
  .gd-note { padding: 6px 0 0; font-style: italic; }
  .gd-sec { padding: 12px 16px; border-bottom: 1px solid #221d2b; }
  .gd-sec h4 { margin: 0 0 8px; font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase;
    color: #8880a0; font-weight: 700; }
  .gd-coll { cursor: pointer; display: flex; justify-content: space-between; }
  .gd-coll span { color: #c96342; }
  .gd-row { display: flex; gap: 10px; font-size: 12px; padding: 3px 0; align-items: baseline; }
  .gd-row label { color: #7e7690; flex: none; width: 84px; }
  .gd-row span { color: #ddd6ea; flex: 1; word-break: break-word; }
  .gd-stat { color: #3ec9a7; font-style: normal; }
  .gd-warn span { color: #e0a458; }
  .gd-stockline { font-size: 13px; color: #ddd6ea; margin-bottom: 6px; }
  .gd-stockline b { color: #3ec9a7; font-size: 15px; }
  .gd-bar { display: flex; align-items: center; gap: 6px; font-size: 11px; margin: 3px 0; }
  .gd-site { width: 42px; color: #9088a0; font-family: ui-monospace, monospace; flex: none; }
  .gd-bar i { height: 8px; background: linear-gradient(90deg,#c96342,#e0a458); border-radius: 3px; min-width: 4px; }
  .gd-q { color: #b9b0cc; margin-left: auto; font-family: ui-monospace, monospace; }
  .gd-sub { display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
    background: #1f1a28; border: 1px solid #2a2533; color: #cfc9dd; border-radius: 5px;
    padding: 7px 9px; margin: 4px 0; cursor: pointer; font-size: 11.5px; }
  .gd-sub:hover { border-color: #c96342; background: #251f30; }
  .gd-arrow { color: #c96342; flex: none; }
  .gd-subname { flex: 1; line-height: 1.3; }
  .gd-badge { flex: none; font-family: ui-monospace, monospace; font-size: 10.5px; color: #3ec9a7; }
  .gd-badge.out { color: #6e6680; }
  .gd-acts { padding: 14px 16px 0; }
  .gd-focus { width: 100%; background: #c96342; border: none; color: #fff; font-size: 12px;
    padding: 9px; cursor: pointer; border-radius: 6px; font-weight: 600; }
  .gd-focus:hover { background: #d77452; }
</style>
