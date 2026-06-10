<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/state';
  import TrainingFlow from '$lib/TrainingFlow.svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  const slug = $derived(page.params.slug || '');

  let loading = $state(true);
  let lastUpdate = $state<string>('');
  let auto = $state(true);
  let timer: any = null;

  // data buckets — each fail-soft
  let health = $state<any>(null);
  let daemons = $state<any>(null);
  let ov = $state<any>(null);           // /overview (kpis, pharma, top_questions)
  let ds = $state<any>(null);           // /datasource summary
  let dq = $state<any>(null);           // /data-quality
  let insights = $state<any[]>([]);
  let tools = $state<any[]>([]);
  let runs = $state<any[]>([]);
  let log = $state<any[]>([]);
  let gateway = $state<any>(null);
  let chem = $state<any>(null);          // /chemist (clinical coverage)
  let evalHealth = $state<any>(null);    // /eval-health (latest golden-eval run)
  let summary = $state<any>(null);       // /dashboard-summary (per-tab chip counts + brain breakdown)
  let agentsN = $state<number>(0);       // /agents length (team size)
  let docsN = $state<number>(0);         // /docs length
  let filesN = $state<number>(0);        // /knowledge-files length

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  async function _j(url: string): Promise<any> {
    try {
      const r = await fetch(url, { headers: _h() });
      if (!r.ok) return null;
      return await r.json();
    } catch { return null; }
  }

  async function load() {
    const s = slug;
    const [h, dm, o, d, q, ins, th, tr, lg, gw, ch, eh] = await Promise.all([
      _j(`/api/health`),
      _j(`/api/health/daemons`),
      _j(`/api/projects/${s}/overview`),
      _j(`/api/projects/${s}/datasource?quality=false&preview=false`),
      _j(`/api/projects/${s}/data-quality`),
      _j(`/api/projects/${s}/insights`),
      _j(`/api/projects/${s}/tool-health`),
      _j(`/api/projects/${s}/training-runs`),
      _j(`/api/projects/${s}/auto-train/log?limit=12`),
      _j(`/api/auth/apigw-usage`),
      _j(`/api/projects/${s}/chemist`),
      _j(`/api/projects/${s}/eval-health`),
    ]);
    health = h; daemons = dm; ov = o; ds = d?.summary || null; dq = q; chem = ch; evalHealth = eh;
    insights = Array.isArray(ins) ? ins : (ins?.insights || []);
    tools = th?.scores || [];
    runs = tr?.runs || [];
    log = lg?.events || [];
    gateway = gw;
    // at-a-glance chip counts (one summary call + 3 light list calls)
    const [sm, ag, dc, fl] = await Promise.all([
      _j(`/api/projects/${s}/dashboard-summary`),
      _j(`/api/projects/${s}/agents`),
      _j(`/api/docs?project=${s}`),
      _j(`/api/knowledge-files?project=${s}`),
    ]);
    summary = sm;
    agentsN = ag?.agents?.length ?? 0;
    docsN = dc?.docs?.length ?? 0;
    filesN = fl?.files?.length ?? 0;
    lastUpdate = new Date().toLocaleTimeString();
    loading = false;
  }

  // ---- clinical golden eval (P3) ----
  let evalRunning = $state(false);
  async function runChemEval() {
    if (evalRunning) return;
    evalRunning = true;
    try {
      const r = await fetch(`/api/projects/${slug}/chemist/eval`, { method: 'POST', headers: _h() });
      if (r.ok) {
        const d = await r.json();
        if (d?.ok && chem) chem = { ...chem, accuracy: { passed: d.passed, total: d.total, pct: d.pct, ran_at: new Date().toISOString() } };
      }
    } catch { /* fail-soft */ }
    evalRunning = false;
  }

  // ---- inline mini graph — declarative SVG (always paints; no canvas/WebGL) ----
  // Fixed viewBox so SVG scales to the card regardless of pixel size — no clientWidth race.
  const GW = 900, GH = 240;
  let gNodes: any[] = $state([]);
  let gEdges: any[] = $state([]);

  async function buildMiniGraph() {
    try {
      const r = await fetch(`/api/projects/${slug}/graph?source=pharma&limit=260`, { headers: _h() });
      if (!r.ok) return;
      const data = await r.json();
      const rawNodes = data?.nodes || [];
      const rawEdges = data?.edges || [];
      if (!rawNodes.length) return;

      const idx = new Map<string, number>();
      const N = rawNodes.map((n: any, i: number) => {
        idx.set(n.id, i);
        return {
          x: 20 + Math.random() * (GW - 40),
          y: 14 + Math.random() * (GH - 28),
          vx: 0, vy: 0,
          r: Math.max(2, 1.6 + Math.min(6, Math.sqrt(n.val || 1) * 1.0)),
          color: n.color || '#7c9cff',
        };
      });
      const E: number[][] = [];
      for (const e of rawEdges) {
        const a = idx.get(e.source), b = idx.get(e.target);
        if (a != null && b != null) E.push([a, b]);
      }

      // force settle (plain numbers, no rendering) — spread the origin pile
      const cx = GW / 2, cy = GH / 2;
      for (let iter = 0; iter < 120; iter++) {
        for (let i = 0; i < N.length; i++) { const p = N[i]; p.vx += (cx - p.x) * 0.002; p.vy += (cy - p.y) * 0.004; }
        for (let i = 0; i < N.length; i++) {
          for (let j = i + 1; j < N.length; j++) {
            const a = N[i], b = N[j];
            let dx = a.x - b.x, dy = a.y - b.y;
            const d2 = dx * dx + dy * dy || 0.01;
            if (d2 < 14000) { const f = 160 / d2; dx *= f; dy *= f; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy; }
          }
        }
        for (const [a, b] of E) {
          const p = N[a], q = N[b];
          const dx = q.x - p.x, dy = q.y - p.y;
          p.vx += dx * 0.0010; p.vy += dy * 0.0010; q.vx -= dx * 0.0010; q.vy -= dy * 0.0010;
        }
        for (const p of N) {
          p.x += Math.max(-5, Math.min(5, p.vx)); p.y += Math.max(-5, Math.min(5, p.vy));
          p.vx *= 0.86; p.vy *= 0.86;
          p.x = Math.max(8, Math.min(GW - 8, p.x)); p.y = Math.max(8, Math.min(GH - 8, p.y));
        }
      }

      gEdges = E.map(([a, b]) => ({ x1: N[a].x, y1: N[a].y, x2: N[b].x, y2: N[b].y }));
      gNodes = N;
    } catch { /* fail-soft */ }
  }

  onMount(() => {
    load();
    buildMiniGraph();
    timer = setInterval(() => { if (auto) load(); }, 30000);
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
  });

  // ---- formatters ----
  function fmtN(v: any): string {
    const n = Number(v);
    if (!isFinite(n)) return '—';
    return n.toLocaleString();
  }
  function fmtMMK(v: any): string {
    const n = Number(v);
    if (!isFinite(n) || n === 0) return '0';
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(Math.round(n));
  }
  function ago(ts: any): string {
    if (!ts) return '';
    const t = typeof ts === 'number' ? ts : new Date(ts).getTime();
    if (!isFinite(t)) return '';
    const diff = Date.now() - t;
    if (diff < 0) return 'now';
    if (diff < 60000) return Math.floor(diff / 1000) + 's';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
    return Math.floor(diff / 86400000) + 'd';
  }
  function sevColor(s: string): string {
    s = (s || '').toLowerCase();
    if (s === 'high' || s === 'error' || s === 'critical') return '#c0392b';
    if (s === 'medium' || s === 'warn' || s === 'warning') return '#a06000';
    return 'var(--color-primary)';
  }
  function qScoreColor(n: number): string {
    if (n >= 80) return 'var(--color-primary)';
    if (n >= 60) return '#a06000';
    return '#c0392b';
  }

  const kpis = $derived(ov?.kpis || {});
  const pharma = $derived(ov?.pharma || {});

  // health rollups
  const apiUp = $derived(!!(health && (health.ok || health.status === 'ok' || health.db === 'ok')));
  const stale = $derived(!!health?.staleness_warning);
  const imgAge = $derived(health?.image_age_hours);
  const daemonList = $derived(daemons?.per_daemon_effective_on_this_worker || daemons?.per_daemon_env_enabled || null);
  const daemonOn = $derived(daemonList ? Object.values(daemonList).filter(Boolean).length : null);
  const daemonTot = $derived(daemonList ? Object.keys(daemonList).length : null);

  // tools: top by calls
  const topTools = $derived([...tools].sort((a, b) => (b.calls || 0) - (a.calls || 0)).slice(0, 5));

  async function dismissInsight(id: any) {
    insights = insights.filter((x) => x.id !== id);
    try { await fetch(`/api/projects/${slug}/insights/${id}/dismiss`, { method: 'POST', headers: _h() }); } catch {}
  }

  // ---- at-a-glance chips: route to the matching Cockpit (settings) tab via #hash ----
  function goTab(hash: string) {
    if (hash === 'graph') { goto(`${base}/project/${slug}/graph`); return; }
    if (hash === 'pipeline') { document.querySelector('.ov-tflow')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    goto(`${base}/project/${slug}/settings#${hash}`);
  }
  // chip groups — count resolved live from summary/ds/ov + the light list calls
  const chipGroups = $derived([
    { sect: 'WORKSPACE', chips: [
      { k: 'Data Source', tab: 'upload',  v: ds ? fmtN(ds.tables) : '—',           s: ds ? fmtN(ds.rows ?? kpis.tables) + ' rows' : '' },
      { k: 'Training',    tab: 'training', v: summary ? String(summary.training_runs) : '—', s: ds ? (ds.trained_tables + '/' + ds.tables + ' trained') : '' },
      { k: 'Docs',        tab: 'docs',     v: String(docsN),                         s: 'documents' },
      { k: 'Queries',     tab: 'queries',  v: summary ? String(summary.queries) : '—', s: 'saved' },
      { k: 'Lineage',     tab: 'lineage',  v: summary ? String(summary.lineage) : '—', s: 'links' },
      { k: 'Files',       tab: 'knowledge', v: String(filesN),                       s: 'knowledge' },
    ]},
    { sect: 'BRAIN', chips: [
      { k: 'Rules',       tab: 'rules',           v: summary ? String(summary.brain?.rules ?? 0) : '—',       s: 'domain rules' },
      { k: 'Graph',       tab: 'graph',           v: summary ? String(summary.triples) : '—',                 s: 'triples' },
      { k: 'Schema',      tab: 'datasets',        v: summary ? String(summary.schema?.tables ?? 0) : '—',     s: (summary?.schema?.columns ?? 0) + ' cols' },
    ]},
    { sect: 'AGENTS', chips: [
      { k: 'Agents',      tab: 'agents',    v: String(agentsN),                       s: 'in team' },
      { k: 'Schedules',   tab: 'schedules', v: summary ? String(summary.schedules) : '—', s: 'scheduled' },
      { k: 'Evals',       tab: 'evals',     v: summary ? String(summary.evals?.golden ?? 0) : '—', s: (summary?.evals?.score ?? 0) + '/5 · ' + (summary?.evals?.runs ?? 0) + ' runs' },
    ]},
    { sect: 'INTELLIGENCE', chips: [
      { k: 'Learn',       tab: 'self-learn', v: summary ? String(summary.learn) : '—', s: 'self-learn runs' },
      { k: 'Pipeline',    tab: 'pipeline',   v: '10', s: 'layers' },
    ]},
  ]);
  // brain rich-card breakdown
  const brainBars = $derived(summary?.brain ? [
    { l: 'Definitions', v: summary.brain.definitions, c: '#c96342' },
    { l: 'Glossary',    v: summary.brain.glossary,    c: '#0ca6b0' },
    { l: 'KPI',         v: summary.brain.kpi,         c: '#d4930e' },
    { l: 'Patterns',    v: summary.brain.patterns,    c: '#7c3aed' },
    { l: 'Rules',       v: summary.brain.rules,       c: '#2d6a4f' },
  ] : []);
  const brainMax = $derived(Math.max(1, ...brainBars.map((b) => b.v || 0)));
</script>

<div class="ov-root">
  <!-- header -->
  <div class="ov-head">
    <div>
      <div class="ov-title">Dashboard</div>
      <div class="ov-sub">{slug}{ov?.schema ? ' · ' + ov.schema : ''}</div>
    </div>
    <div class="ov-head-actions">
      <span class="ov-upd">{loading ? 'loading…' : (lastUpdate ? 'updated ' + lastUpdate : '')}</span>
      <button class="ov-btn" class:on={auto} onclick={() => (auto = !auto)}>{auto ? '⟳ auto 30s' : '⏸ paused'}</button>
      <button class="ov-btn" onclick={() => load()}>↻ refresh</button>
      <button class="ov-btn ov-btn-primary" onclick={() => goto(`${base}/project/${slug}`)}>Open chat →</button>
    </div>
  </div>

  {#if loading}
    <div class="ov-loading">Loading cockpit…</div>
  {:else}

  <!-- KPI RAIL -->
  <div class="ov-kpis">
    {#each [
      { l: 'CHATS 24h', v: fmtN(kpis.chats_24h), s: (kpis.chats_total ?? 0) + ' total' },
      { l: 'CATALOG SKUs', v: fmtN(kpis.catalog_skus), s: (kpis.sites ?? '—') + ' sites' },
      { l: 'TABLES', v: fmtN(kpis.tables), s: ds ? (ds.trained_tables + '/' + ds.tables + ' trained') : '' },
      { l: 'STOCK VALUE', v: fmtMMK(kpis.stock_value), s: 'MMK' },
      { l: 'STOCK UNITS', v: fmtMMK(kpis.stock_units), s: 'units' },
      { l: 'Q&A · VECTORS', v: ds ? fmtN(ds.qa) : '—', s: ds ? fmtN(ds.vectors) + ' vec' : '' },
    ] as k}
      <div class="ov-kpi">
        <div class="ov-kpi-l">{k.l}</div>
        <div class="ov-kpi-v">{k.v}</div>
        <div class="ov-kpi-s">{k.s}</div>
      </div>
    {/each}
  </div>

  <!-- AT A GLANCE — per-tab summary chips (click → Cockpit tab) + Brain rich card -->
  <div class="ov-glance">
    <div class="ov-chips">
      {#each chipGroups as g}
        <div class="ov-chip-sect">{g.sect}</div>
        <div class="ov-chip-row">
          {#each g.chips as c}
            <button class="ov-chip" onclick={() => goTab(c.tab)} title="open {c.k}">
              <div class="ov-chip-v">{c.v}</div>
              <div class="ov-chip-k">{c.k}</div>
              {#if c.s}<div class="ov-chip-s">{c.s}</div>{/if}
            </button>
          {/each}
        </div>
      {/each}
    </div>

    <div class="ov-card ov-brain">
      <div class="ov-card-h">BRAIN <span class="ov-brain-tot">{summary?.brain?.total ?? 0} entries</span></div>
      {#if brainBars.length}
        <div class="ov-brain-bars">
          {#each brainBars as b}
            <button class="ov-brain-row" onclick={() => goTab(b.l === 'Rules' ? 'rules' : b.l === 'Glossary' ? 'brain-glossary' : 'brain-definitions')}>
              <span class="ov-brain-l">{b.l}</span>
              <span class="ov-brain-track"><span class="ov-brain-fill" style="width:{((b.v || 0) / brainMax) * 100}%;background:{b.c}"></span></span>
              <span class="ov-brain-n">{b.v}</span>
            </button>
          {/each}
        </div>
      {:else}
        <div class="ov-brain-empty">no brain entries yet</div>
      {/if}
    </div>
  </div>

  <!-- TRAINING PIPELINE — live boiler schematic + 60-step detail -->
  <div class="ov-card ov-tflow">
    {#if slug}<TrainingFlow {slug} />{/if}
  </div>

  <!-- ROW: HEALTH | QUALITY -->
  <div class="ov-grid2">
    <div class="ov-card">
      <div class="ov-card-h">SYSTEM HEALTH</div>
      <div class="ov-rows">
        <div class="ov-r"><span class="dot" style="background:{apiUp ? 'var(--color-primary)' : '#c0392b'}"></span>API<span class="ov-r-x">{apiUp ? 'up' : 'down'}{imgAge != null ? ' · img ' + Number(imgAge).toFixed(1) + 'h' : ''}</span></div>
        <div class="ov-r"><span class="dot" style="background:{apiUp ? 'var(--color-primary)' : '#c0392b'}"></span>Database<span class="ov-r-x">pg18 + AGE</span></div>
        <div class="ov-r"><span class="dot" style="background:{daemonOn ? 'var(--color-primary)' : '#a06000'}"></span>Daemons<span class="ov-r-x">{daemonOn != null ? daemonOn + '/' + daemonTot + ' on' : 'n/a'}</span></div>
        <div class="ov-r"><span class="dot" style="background:{stale ? '#a06000' : 'var(--color-primary)'}"></span>Freshness<span class="ov-r-x">{stale ? 'stale build' : '✓ fresh'}</span></div>
        <div class="ov-r"><span class="dot" style="background:var(--color-primary)"></span>Backup<span class="ov-r-x">{health?.last_backup ? ago(health.last_backup) + ' ago' : '—'}</span></div>
      </div>
    </div>

    <div class="ov-card">
      <div class="ov-card-h">DATA QUALITY</div>
      {#if dq}
        <div class="ov-q-score">
          <div class="ov-bar"><div class="ov-bar-fill" style="width:{dq.score}%;background:{qScoreColor(dq.score)}"></div></div>
          <span class="ov-q-num" style="color:{qScoreColor(dq.score)}">{dq.score}%</span>
        </div>
        <div class="ov-rows">
          <div class="ov-r">Trained<span class="ov-r-x">{ds ? ds.trained_tables + ' / ' + ds.tables + ' tables' : '—'}</span></div>
          <div class="ov-r">Issues<span class="ov-r-x">{fmtN(dq.issue_count)} ({dq.by_severity?.high || 0} high · {dq.by_severity?.medium || 0} med)</span></div>
          <div class="ov-r">Tables scanned<span class="ov-r-x">{fmtN(dq.table_count)}</span></div>
          <div class="ov-r">Last scan<span class="ov-r-x">{dq.last_scanned ? ago(dq.last_scanned) + ' ago' : '—'}</span></div>
        </div>
      {:else}
        <div class="ov-empty">No quality scan yet</div>
      {/if}
    </div>
  </div>

  <!-- ROW: PHARMA | TOOL HEALTH -->
  <div class="ov-grid2">
    <div class="ov-card">
      <div class="ov-card-h">PHARMA SIGNALS <span class="ov-card-hx">live</span></div>
      {#if pharma && pharma.stock_rows}
        <div class="ov-rows">
          <div class="ov-r"><span class="warn">▴</span>Stock-outs (qty≤0)<span class="ov-r-x">{fmtN(pharma.stockouts)}</span></div>
          <div class="ov-r"><span class="warn">▴</span>Low stock (&lt;10)<span class="ov-r-x">{fmtN(pharma.low_stock)}</span></div>
          <div class="ov-r">Total units<span class="ov-r-x">{fmtMMK(pharma.total_units)}</span></div>
          <div class="ov-r">Stock value<span class="ov-r-x">{fmtMMK(pharma.stock_value)} MMK</span></div>
          <div class="ov-r">Sites covered<span class="ov-r-x">{fmtN(pharma.sites)}</span></div>
          <div class="ov-r">Top category<span class="ov-r-x">{pharma.top_category || '—'}</span></div>
        </div>
      {:else}
        <div class="ov-empty">No stock table found</div>
      {/if}
    </div>

    <div class="ov-card">
      <div class="ov-card-h">EVAL HEALTH <span class="ov-card-hx">golden</span></div>
      {#if evalHealth?.has_data}
        {@const ev = evalHealth}
        {@const pct = (n) => ev.total ? Math.round((n / ev.total) * 100) : 0}
        <div class="ov-eval-bar" title="{ev.passed} pass · {ev.partial} partial · {ev.failed} fail">
          <span class="ov-seg pass" style="width:{pct(ev.passed)}%"></span>
          <span class="ov-seg part" style="width:{pct(ev.partial)}%"></span>
          <span class="ov-seg fail" style="width:{pct(ev.failed)}%"></span>
        </div>
        <div class="ov-rows">
          <div class="ov-r"><span class="dot" style="background:var(--color-primary)"></span>Pass<span class="ov-r-x">{ev.passed}/{ev.total} · {Math.round(ev.pass_rate * 100)}%</span></div>
          <div class="ov-r"><span class="dot" style="background:#a06000"></span>Partial<span class="ov-r-x">{ev.partial}</span></div>
          <div class="ov-r"><span class="dot" style="background:#b3261e"></span>Fail<span class="ov-r-x">{ev.failed}</span></div>
          <div class="ov-r">Avg score<span class="ov-r-x">{(ev.average_score ?? 0).toFixed(1)} / 5</span></div>
          <div class="ov-r">Last run<span class="ov-r-x">{ev.run_at ? new Date(ev.run_at).toLocaleDateString() : '—'}</span></div>
        </div>
      {:else}
        <div class="ov-empty">No eval runs yet</div>
      {/if}
    </div>
  </div>

  <!-- PHARMA CHEMIST — clinical coverage + substitute web -->
  {#if chem?.ok}
    <div class="ov-card">
      <div class="ov-card-h">🧪 PHARMA CHEMIST <span class="ov-card-hx">clinical brain</span></div>
      <div class="ov-chem-stats">
        <div class="ov-chem-stat"><div class="ov-chem-n">{fmtN(chem.total_skus)}</div><div class="ov-chem-l">SKUs</div></div>
        <div class="ov-chem-stat"><div class="ov-chem-n">{fmtN(chem.distinct_generics)}</div><div class="ov-chem-l">generics</div></div>
        <div class="ov-chem-stat"><div class="ov-chem-n">{fmtN(chem.distinct_categories)}</div><div class="ov-chem-l">categories</div></div>
        <div class="ov-chem-stat"><div class="ov-chem-n">{fmtN(chem.drugs_with_substitutes)}</div><div class="ov-chem-l">w/ substitutes</div></div>
      </div>
      <div class="ov-chem-cov">
        {#each Object.entries(chem.coverage || {}) as [col, c]}
          <div class="ov-chem-row">
            <span class="ov-chem-col">{col.replace('_', ' ')}</span>
            <div class="ov-chem-bar"><div class="ov-chem-fill" style="width:{(c as any).pct}%;background:{(c as any).pct >= 75 ? 'var(--color-primary)' : (c as any).pct >= 50 ? '#a06000' : '#c0392b'}"></div></div>
            <span class="ov-chem-pct">{(c as any).pct}%</span>
          </div>
        {/each}
      </div>
      <div class="ov-chem-acc">
        <div class="ov-chem-acc-l">
          <span class="ov-chem-acc-t">Clinical accuracy</span>
          {#if chem.accuracy}
            <span class="ov-chem-acc-n" style="color:{chem.accuracy.pct >= 80 ? 'var(--color-primary)' : chem.accuracy.pct >= 60 ? '#a06000' : '#c0392b'}">{chem.accuracy.pct}%</span>
            <span class="ov-chem-acc-s">{chem.accuracy.passed}/{chem.accuracy.total} golden checks · forward+inverse</span>
          {:else}
            <span class="ov-chem-acc-s">not run yet — held-out forward + inverse golden set</span>
          {/if}
        </div>
        <button class="ov-chem-run" onclick={runChemEval} disabled={evalRunning}>{evalRunning ? 'running…' : 'Run eval'}</button>
      </div>
      <div class="ov-chem-foot">Forward: drug→profile · Inverse: symptom→drug · substitutes by generic — every answer audited to source SKU</div>
    </div>
  {/if}

  <!-- KNOWLEDGE GRAPH — live inline force-map -->
  <div class="ov-graph-card">
    <svg class="ov-graph-canvas" viewBox="0 0 {GW} {GH}" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Drug substitute knowledge graph">
      {#each gEdges as e}
        <line x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} stroke="rgba(190,180,210,0.16)" stroke-width="0.5" />
      {/each}
      {#each gNodes as n}
        <circle cx={n.x} cy={n.y} r={n.r} fill={n.color} />
      {/each}
    </svg>
    <div class="ov-graph-fade"></div>
    <div class="ov-graph-l">
      <div class="ov-graph-t">KNOWLEDGE GRAPH</div>
      <div class="ov-graph-s">Drug substitute web · {fmtN(chem?.drugs_with_substitutes)} drugs · live force-map</div>
    </div>
    <button class="ov-graph-cta" onclick={() => goto(`${base}/project/${slug}/graph`)}>Explore ⛶</button>
  </div>

  <!-- BRAIN WIKI launcher -->
  <button class="ov-wiki-card" onclick={() => goto(`${base}/project/${slug}/wiki`)}>
    <div>
      <div class="ov-wiki-t">📖 BRAIN WIKI</div>
      <div class="ov-wiki-s">Readable concept pages — glossary · formulas · KPIs · backlinks, auto-built from the agent's brain</div>
    </div>
    <div class="ov-wiki-cta">Read →</div>
  </button>

  <!-- INSIGHTS -->
  <div class="ov-card">
    <div class="ov-card-h">INSIGHTS <span class="ov-card-hx">auto · dismissable</span></div>
    {#if insights.length}
      <div class="ov-ins">
        {#each insights.slice(0, 6) as i}
          <div class="ov-ins-row">
            <span class="ov-ins-dot" style="background:{sevColor(i.severity)}"></span>
            <span class="ov-ins-txt">{(i.insight || '').split('\n')[0].slice(0, 140)}</span>
            <span class="ov-ins-age">{ago(i.created_at)}</span>
            <button class="ov-x" onclick={() => dismissInsight(i.id)}>×</button>
          </div>
        {/each}
      </div>
    {:else}
      <div class="ov-empty">No active insights</div>
    {/if}
  </div>

  <!-- ROW: ACTIVITY | LIVE LOG -->
  <div class="ov-grid2">
    <div class="ov-card">
      <div class="ov-card-h">ACTIVITY <span class="ov-card-hx">training runs</span></div>
      {#if runs.length}
        <div class="ov-rows">
          {#each runs.slice(0, 5) as r}
            <div class="ov-r">
              <span class="dot" style="background:{r.status === 'done' ? 'var(--color-primary)' : r.status === 'failed' ? '#c0392b' : '#a06000'}"></span>
              <span class="ov-mono">{r.status === 'done' ? '✓' : r.status === 'failed' ? '✗' : '●'} {(r.tables || 'run').toString().slice(0, 22)}</span>
              <span class="ov-r-x">{ago(r.finished_at || r.started_at)} ago</span>
            </div>
          {/each}
        </div>
      {:else}
        <div class="ov-empty">No training runs</div>
      {/if}
    </div>

    <div class="ov-card">
      <div class="ov-card-h">LIVE LOG <span class="ov-card-hx">auto-train + crons</span></div>
      {#if log.length}
        <div class="ov-loglines">
          {#each log.slice(-12) as e}
            <div class="ov-logline">{e.ts ? String(e.ts).slice(11, 19) : ''} <span>{(e.msg || '').slice(0, 80)}</span></div>
          {/each}
        </div>
      {:else}
        <div class="ov-empty">No recent log events</div>
      {/if}
    </div>
  </div>

  <!-- ROW: TOP QUESTIONS | GATEWAY -->
  <div class="ov-grid2">
    <div class="ov-card">
      <div class="ov-card-h">TOP QUESTIONS <span class="ov-card-hx">30d</span></div>
      {#if ov?.top_questions?.length}
        <div class="ov-rows">
          {#each ov.top_questions as q, idx}
            <div class="ov-r"><span class="ov-q-rank">{idx + 1}</span>{q.q}<span class="ov-r-x">{q.n}×</span></div>
          {/each}
        </div>
      {:else}
        <div class="ov-empty">No chat history</div>
      {/if}
    </div>

    <div class="ov-card">
      <div class="ov-card-h">API GATEWAY</div>
      {#if gateway?.totals}
        <div class="ov-rows">
          <div class="ov-r">Active keys<span class="ov-r-x">{fmtN(gateway.by_key?.length ?? 0)}</span></div>
          <div class="ov-r">Requests {gateway.days || 7}d<span class="ov-r-x">{fmtN(gateway.totals.calls)}</span></div>
          <div class="ov-r">Tokens<span class="ov-r-x">{fmtMMK(gateway.totals.tokens)}</span></div>
          <div class="ov-r">Rate-limited<span class="ov-r-x">{fmtN(gateway.totals.rate_limited)}</span></div>
        </div>
      {:else}
        <div class="ov-empty">No gateway usage (or not permitted)</div>
      {/if}
    </div>
  </div>

  {/if}
</div>

<style>
  .ov-root { max-width: 1280px; margin: 0 auto; padding: 20px 28px 80px; }
  .ov-head { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }
  .ov-title { font-size: 22px; font-weight: 900; letter-spacing: -0.01em; color: var(--color-on-surface); }
  .ov-sub { font-size: 11px; color: var(--color-on-surface-dim); margin-top: 2px; }
  .ov-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .ov-upd { font-size: 10px; color: var(--color-on-surface-dim); margin-right: 4px; }
  .ov-btn { font-size: 11px; padding: 5px 11px; border: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface); color: var(--color-on-surface); cursor: pointer; font-weight: 700; }
  .ov-btn.on { border-color: var(--color-primary); color: var(--color-primary); }
  .ov-btn-primary { background: var(--color-primary); color: #fff; border-color: var(--color-primary); }
  .ov-loading { padding: 60px; text-align: center; color: var(--color-on-surface-dim); font-size: 13px; }

  .ov-kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 14px; }
  .ov-kpi { border: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface-bright, var(--color-surface)); padding: 12px 14px; }
  .ov-kpi-l { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--color-on-surface-dim); }
  .ov-kpi-v { font-size: 26px; font-weight: 900; color: var(--color-on-surface); line-height: 1.1; margin: 4px 0 2px; }
  .ov-kpi-s { font-size: 10px; color: var(--color-on-surface-dim); }

  /* AT A GLANCE: chip grid (left) + brain rich card (right) */
  .ov-glance { display: grid; grid-template-columns: 1fr 290px; gap: 12px; margin-bottom: 14px; align-items: start; }
  .ov-chips { display: flex; flex-direction: column; gap: 7px; }
  .ov-chip-sect { font-size: 9px; font-weight: 800; letter-spacing: 0.09em; color: var(--color-on-surface-dim); margin-top: 4px; }
  .ov-chip-sect:first-child { margin-top: 0; }
  .ov-chip-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; }
  .ov-chip { text-align: left; border: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-surface, #fff); padding: 9px 10px; cursor: pointer; transition: 0.15s; border-top: 2px solid var(--pw-accent, #c96342); }
  .ov-chip:hover { background: var(--pw-bg-alt, #f6f2ea); transform: translateY(-2px); box-shadow: 0 4px 10px rgba(0,0,0,0.07); }
  .ov-chip-v { font-size: 19px; font-weight: 900; color: var(--color-on-surface); line-height: 1; font-variant-numeric: tabular-nums; }
  .ov-chip-k { font-size: 10px; font-weight: 700; color: var(--color-on-surface); margin-top: 4px; }
  .ov-chip-s { font-size: 8.5px; color: var(--color-on-surface-dim); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .ov-brain { align-self: stretch; }
  .ov-brain-tot { font-size: 9px; color: var(--pw-muted, #877f74); font-weight: 600; }
  .ov-brain-bars { padding: 10px 12px; display: flex; flex-direction: column; gap: 9px; }
  .ov-brain-row { display: grid; grid-template-columns: 64px 1fr 30px; gap: 8px; align-items: center; background: none; border: none; padding: 0; cursor: pointer; text-align: left; }
  .ov-brain-row:hover .ov-brain-l { color: var(--pw-accent, #c96342); }
  .ov-brain-l { font-size: 10px; font-weight: 700; color: var(--color-on-surface); }
  .ov-brain-track { height: 8px; background: var(--pw-bg-alt, #f0ebe2); overflow: hidden; }
  .ov-brain-fill { display: block; height: 100%; transition: width 0.5s; }
  .ov-brain-n { font-size: 11px; font-weight: 800; text-align: right; font-variant-numeric: tabular-nums; color: var(--color-on-surface); }
  .ov-brain-empty { padding: 16px; font-size: 11px; color: var(--color-on-surface-dim); }

  .ov-tflow { margin-bottom: 12px; }
  .ov-tflow :global(.tf) { padding: 14px; }
  @media (max-width: 900px) {
    .ov-glance { grid-template-columns: 1fr; }
    .ov-chip-row { grid-template-columns: repeat(3, 1fr); }
  }
  .ov-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .ov-card { border: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-surface, #fff); padding: 0; overflow: hidden; }
  .ov-card-h { background: var(--pw-bg-alt, #f6f2ea); color: var(--pw-accent, #c96342); border-bottom: 1px solid var(--pw-border, #e5ddcf); padding: 8px 14px; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; display: flex; justify-content: space-between; align-items: center; }
  .ov-card-hx { font-size: 9px; color: var(--pw-muted, #877f74); font-weight: 600; letter-spacing: 0.05em; }

  .ov-rows { padding: 10px 14px; display: flex; flex-direction: column; gap: 7px; }
  .ov-r { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-on-surface); }
  .ov-r-x { margin-left: auto; font-weight: 700; color: var(--color-on-surface); font-size: 12px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
  .warn { color: #a06000; font-weight: 900; }
  .ov-mono { font-family: ui-monospace, monospace; font-size: 11.5px; }
  .ov-q-rank { display: inline-flex; width: 16px; height: 16px; align-items: center; justify-content: center; background: var(--color-on-surface); color: var(--color-surface); font-size: 9px; font-weight: 900; flex-shrink: 0; }

  .ov-q-score { display: flex; align-items: center; gap: 10px; padding: 12px 14px 4px; }
  .ov-bar { flex: 1; height: 10px; background: var(--color-surface); border: 1px solid var(--pw-border, #e5ddcf); overflow: hidden; }
  .ov-bar-fill { height: 100%; }
  .ov-q-num { font-size: 18px; font-weight: 900; }

  .ov-ins { padding: 6px 10px 10px; display: flex; flex-direction: column; }
  .ov-ins-row { display: flex; align-items: center; gap: 8px; padding: 6px 4px; border-bottom: 1px solid var(--color-surface); font-size: 12px; }
  .ov-ins-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .ov-ins-txt { flex: 1; color: var(--color-on-surface); }
  .ov-ins-age { font-size: 10px; color: var(--color-on-surface-dim); }
  .ov-x { background: none; border: none; cursor: pointer; color: var(--color-on-surface-dim); font-size: 16px; line-height: 1; padding: 0 4px; }
  .ov-x:hover { color: #c0392b; }

  .ov-loglines { padding: 10px 14px; font-family: ui-monospace, monospace; font-size: 10.5px; max-height: 180px; overflow-y: auto; background: #1a1614; }
  .ov-logline { color: #7c8; padding: 1px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ov-logline span { color: #e8e3d6; }

  .ov-empty { padding: 24px 14px; text-align: center; color: var(--color-on-surface-dim); font-size: 11px; }
  .ov-eval-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 4px 0 10px; background: var(--color-surface-variant, #eee); }
  .ov-seg { height: 100%; transition: width .3s ease; }
  .ov-seg.pass { background: var(--color-primary); }
  .ov-seg.part { background: #a06000; }
  .ov-seg.fail { background: #b3261e; }

  .ov-chem-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--color-on-surface); border-bottom: 1px solid var(--pw-border, #e5ddcf); }
  .ov-chem-stat { background: var(--color-surface); padding: 12px 10px; text-align: center; }
  .ov-chem-n { font-size: 20px; font-weight: 900; color: var(--color-on-surface); line-height: 1; }
  .ov-chem-l { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-on-surface-dim); margin-top: 4px; }
  .ov-chem-cov { padding: 12px 14px; display: flex; flex-direction: column; gap: 7px; }
  .ov-chem-row { display: flex; align-items: center; gap: 10px; font-size: 11.5px; }
  .ov-chem-col { flex: 0 0 96px; text-transform: capitalize; color: var(--color-on-surface); }
  .ov-chem-bar { flex: 1; height: 7px; background: var(--color-surface-dim, rgba(0,0,0,0.06)); border-radius: 4px; overflow: hidden; }
  .ov-chem-fill { height: 100%; border-radius: 4px; transition: width 0.4s; }
  .ov-chem-pct { flex: 0 0 40px; text-align: right; font-weight: 700; color: var(--color-on-surface-dim); font-size: 10.5px; }
  .ov-chem-acc { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 14px; border-top: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface-bright, var(--color-surface)); }
  .ov-chem-acc-l { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .ov-chem-acc-t { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-on-surface-dim); font-weight: 700; }
  .ov-chem-acc-n { font-size: 18px; font-weight: 900; }
  .ov-chem-acc-s { font-size: 10.5px; color: var(--color-on-surface-dim); }
  .ov-chem-run { background: var(--color-primary); color: #fff; border: none; font-size: 11px; font-weight: 700; padding: 6px 13px; cursor: pointer; }
  .ov-chem-run:disabled { opacity: 0.5; cursor: default; }
  .ov-chem-foot { padding: 0 14px 12px; font-size: 10px; color: var(--color-on-surface-dim); font-style: italic; }

  .ov-graph-card { position: relative; width: 100%; height: 240px; margin-bottom: 12px; padding: 0; background: #16131a; border: 1px solid #3a3346; cursor: pointer; text-align: left; overflow: hidden; display: block; }
  .ov-graph-card:hover { border-color: #c96342; }
  .ov-graph-canvas { position: absolute; inset: 0; display: block; width: 100%; height: 100%; }
  .ov-graph-fade { position: absolute; inset: 0; pointer-events: none; background: linear-gradient(90deg, rgba(22,19,26,0.85) 0%, rgba(22,19,26,0.35) 26%, rgba(22,19,26,0) 50%); }
  .ov-graph-l { position: absolute; left: 20px; top: 18px; z-index: 2; pointer-events: none; }
  .ov-graph-t { font-size: 14px; font-weight: 900; letter-spacing: 0.05em; color: #fff; }
  .ov-graph-s { font-size: 11px; color: #b6aecb; margin-top: 4px; }
  .ov-graph-cta { position: absolute; right: 18px; bottom: 16px; z-index: 2; font-size: 12px; font-weight: 700; color: #fff; background: #c96342; padding: 6px 12px; border-radius: 4px; }

  .ov-wiki-card { width: 100%; display: flex; align-items: center; gap: 16px; margin-bottom: 12px; padding: 16px 18px; background: var(--color-surface-bright, var(--color-surface)); border: 1px solid var(--pw-border, #e5ddcf); cursor: pointer; text-align: left; }
  .ov-wiki-card:hover { border-color: var(--color-primary); }
  .ov-wiki-t { font-size: 13px; font-weight: 900; letter-spacing: 0.04em; color: var(--color-on-surface); }
  .ov-wiki-s { font-size: 11px; color: var(--color-on-surface-dim); margin-top: 3px; }
  .ov-wiki-cta { margin-left: auto; font-size: 12px; font-weight: 700; color: var(--color-primary); white-space: nowrap; }

  @media (max-width: 1000px) {
    .ov-kpis { grid-template-columns: repeat(3, 1fr); }
    .ov-grid2 { grid-template-columns: 1fr; }
  }
  @media (max-width: 560px) {
    .ov-kpis { grid-template-columns: repeat(2, 1fr); }
    .ov-root { padding: 16px 14px 60px; }
  }
</style>
