<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();

  // ---- state ---------------------------------------------------------------
  let portcos = $state<any[]>([]);
  let selected = $state<any | null>(null);
  let kpis = $state<any[]>([]);
  let anomalies = $state<any[]>([]);
  let initiatives = $state<any[]>([]);
  let health = $state<any | null>(null);
  let xPortfolio = $state(false);
  let boardPackDate = $state<string>(new Date().toISOString().slice(0, 10));
  let showBoardPackPop = $state(false);
  let busy = $state(false);
  let err = $state<string | null>(null);

  let expandedCell = $state<string | null>(null); // "metric|period"
  let showNewInitiative = $state(false);
  let dragInitiativeId = $state<string | null>(null);
  let dragOverCol = $state<string | null>(null);

  // new initiative form
  let niTitle = $state('');
  let niPlay = $state('cost_out');
  let niOwner = $state('');
  let niDue = $state('');
  let niTarget = $state(0);

  const KANBAN_COLS = ['proposed', 'approved', 'in_progress', 'done'];
  const PLAY_TYPES = ['cost_out', 'revenue_uplift', 'margin_expansion', 'tech_migration', 'hire', 'ma_addon', 'exit_prep'];

  // ---- load portco list ----------------------------------------------------
  async function loadPortcos() {
    busy = true; err = null;
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos`);
      if (!r.ok) { err = `portcos: ${r.status}`; portcos = []; return; }
      const data = await r.json();
      portcos = data?.portcos ?? data ?? [];
      if (portcos.length && !selected) {
        selected = portcos[0];
      }
    } catch (e: any) {
      err = String(e?.message || e);
    } finally {
      busy = false;
    }
  }

  async function loadKpis(pid: string) {
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos/${pid}/kpis`);
      if (!r.ok) { kpis = []; return; }
      const data = await r.json();
      kpis = data?.kpis ?? data ?? [];
    } catch { kpis = []; }
  }

  async function loadAnomalies(pid: string) {
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos/${pid}/anomalies`);
      if (!r.ok) { anomalies = []; return; }
      const data = await r.json();
      anomalies = data?.anomalies ?? data ?? [];
    } catch { anomalies = []; }
  }

  async function loadInitiatives(pid: string) {
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos/${pid}/initiatives`);
      if (!r.ok) { initiatives = []; return; }
      const data = await r.json();
      initiatives = data?.initiatives ?? data ?? [];
    } catch { initiatives = []; }
  }

  async function loadHealth() {
    try {
      const r = await dashFetch(`/api/ops/${slug}/health`);
      if (!r.ok) { health = null; return; }
      health = await r.json();
    } catch { health = null; }
  }

  // refetch on selection change
  $effect(() => {
    if (!selected?.id) return;
    const pid = selected.id;
    loadKpis(pid);
    loadAnomalies(pid);
    loadInitiatives(pid);
    if (xPortfolio) loadHealth();
  });

  $effect(() => {
    if (xPortfolio && !health) loadHealth();
  });

  // ---- KPI grid derived ----------------------------------------------------
  let metricNames = $derived.by(() => {
    const s = new Set<string>();
    for (const k of kpis) if (k?.metric_name) s.add(k.metric_name);
    return Array.from(s);
  });

  let periods = $derived.by(() => {
    const s = new Set<string>();
    for (const k of kpis) if (k?.period) s.add(k.period);
    return Array.from(s).sort().slice(-12);
  });

  function kpiCell(metric: string, period: string): any | null {
    return kpis.find(k => k?.metric_name === metric && k?.period === period) ?? null;
  }

  function varianceClass(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return 'chip-na';
    if (v > -5) return 'chip-green';
    if (v >= -15) return 'chip-yellow';
    return 'chip-red';
  }

  function fmtVal(v: any): string {
    if (v == null || v === '') return '—';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(2);
  }

  function fmtVar(v: any): string {
    if (v == null || isNaN(Number(v))) return '';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
  }

  function sparklinePath(metric: string): string {
    const series = periods
      .map(p => kpiCell(metric, p))
      .map(c => c?.actual != null ? Number(c.actual) : null);
    const vals = series.filter((x): x is number => x != null && !isNaN(x));
    if (vals.length < 2) return '';
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;
    const w = 200, h = 40;
    const step = w / (series.length - 1);
    let d = '';
    series.forEach((v, i) => {
      if (v == null || isNaN(v)) return;
      const x = i * step;
      const y = h - ((v - min) / range) * h;
      d += (d ? ' L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
    });
    return d;
  }

  function toggleCell(metric: string, period: string) {
    const key = `${metric}|${period}`;
    expandedCell = expandedCell === key ? null : key;
  }

  // ---- anomalies -----------------------------------------------------------
  let anomGroups = $derived.by(() => {
    const g: Record<string, any[]> = { critical: [], warn: [], info: [] };
    for (const a of anomalies) {
      const s = (a?.severity || 'info').toLowerCase();
      (g[s] ?? g.info).push(a);
    }
    return g;
  });

  async function ackAnomaly(aid: string) {
    // optimistic
    anomalies = anomalies.filter(a => a.id !== aid);
    try {
      await dashFetch(`/api/ops/${slug}/portcos/${selected?.id}/anomalies/${aid}/ack`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}'
      });
    } catch { /* keep optimistic */ }
  }

  async function detectAnomalies() {
    if (!selected?.id) return;
    try {
      await dashFetch(`/api/ops/${slug}/portcos/${selected.id}/anomalies/detect`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: '{}'
      });
      await loadAnomalies(selected.id);
    } catch {}
  }

  // ---- initiatives kanban --------------------------------------------------
  function initiativesIn(col: string): any[] {
    return initiatives.filter(i => (i?.status ?? 'proposed') === col);
  }

  function onDragStart(e: DragEvent, id: string) {
    dragInitiativeId = id;
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }

  function onDragOver(e: DragEvent, col: string) {
    e.preventDefault();
    dragOverCol = col;
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  }

  function onDragLeave(col: string) {
    if (dragOverCol === col) dragOverCol = null;
  }

  async function onDrop(e: DragEvent, col: string) {
    e.preventDefault();
    dragOverCol = null;
    const iid = dragInitiativeId;
    dragInitiativeId = null;
    if (!iid) return;
    // optimistic
    const idx = initiatives.findIndex(i => i.id === iid);
    if (idx < 0) return;
    const prevStatus = initiatives[idx].status;
    initiatives[idx] = { ...initiatives[idx], status: col };
    initiatives = [...initiatives];
    try {
      const r = await dashFetch(`/api/ops/${slug}/initiatives/${iid}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status: col })
      });
      if (!r.ok) {
        initiatives[idx] = { ...initiatives[idx], status: prevStatus };
        initiatives = [...initiatives];
      }
    } catch {
      initiatives[idx] = { ...initiatives[idx], status: prevStatus };
      initiatives = [...initiatives];
    }
  }

  async function createInitiative() {
    if (!niTitle.trim() || !selected?.id) return;
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos/${selected.id}/initiatives`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          title: niTitle.trim(),
          play_type: niPlay,
          owner: niOwner,
          due_date: niDue || null,
          target_value_usd: niTarget,
          status: 'proposed'
        })
      });
      if (r.ok) {
        niTitle = ''; niOwner = ''; niDue = ''; niTarget = 0;
        showNewInitiative = false;
        await loadInitiatives(selected.id);
      }
    } catch {}
  }

  // ---- board pack ----------------------------------------------------------
  async function generateBoardPack() {
    if (!selected?.id) return;
    try {
      const r = await dashFetch(`/api/ops/${slug}/portcos/${selected.id}/board-pack`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ meeting_date: boardPackDate })
      });
      if (r.ok) {
        const data = await r.json();
        const url = data?.url;
        if (url) {
          const a = document.createElement('a');
          a.href = url;
          a.target = '_blank';
          a.rel = 'noopener';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }
        showBoardPackPop = false;
      }
    } catch {}
  }

  // ---- cross-portfolio heatmap --------------------------------------------
  let heatmapData = $derived.by(() => {
    if (!health) return null;
    const portcosH = health?.portcos ?? health ?? [];
    if (!Array.isArray(portcosH)) return null;
    const kpiSet = new Set<string>();
    for (const p of portcosH) {
      const ks = p?.top_kpis ?? p?.kpis ?? [];
      for (const k of ks) if (k?.name) kpiSet.add(k.name);
    }
    const topKpis = Array.from(kpiSet).slice(0, 5);
    return { portcos: portcosH, kpis: topKpis };
  });

  function heatCellVar(pname: string, kname: string): number | null {
    if (!heatmapData) return null;
    const p = heatmapData.portcos.find((x: any) => x?.name === pname || x?.legal_name === pname);
    if (!p) return null;
    const ks = p?.top_kpis ?? p?.kpis ?? [];
    const k = ks.find((x: any) => x?.name === kname);
    const v = k?.variance_pct ?? k?.variance ?? null;
    return v != null && !isNaN(Number(v)) ? Number(v) : null;
  }

  function onHeatCellClick(pname: string, v: number | null) {
    if (v == null || v >= -15) return; // only red cells
    const p = portcos.find(x => x?.name === pname || x?.legal_name === pname);
    if (p) {
      selected = p;
      xPortfolio = false;
    }
  }

  // ---- header info ---------------------------------------------------------
  function fmtPct(n: any): string {
    if (n == null || isNaN(Number(n))) return '—';
    return Number(n).toFixed(1) + '%';
  }

  function fmtDate(d: any): string {
    if (!d) return '—';
    try { return new Date(d).toISOString().slice(0, 10); } catch { return String(d); }
  }

  onMount(loadPortcos);
</script>

<div class="ops-root">
  <!-- top bar: portco picker + cross-portfolio toggle + board pack -->
  <div class="ops-topbar">
    <select class="ops-select" bind:value={selected} disabled={busy || !portcos.length}>
      {#if !portcos.length}
        <option value={null}>No portcos</option>
      {/if}
      {#each portcos as p}
        <option value={p}>{p.legal_name || p.name || p.id}</option>
      {/each}
    </select>

    <div class="ops-toggle">
      <button
        class:active={!xPortfolio}
        onclick={() => xPortfolio = false}
      >Single Portco</button>
      <button
        class:active={xPortfolio}
        onclick={() => xPortfolio = true}
      >Cross-Portfolio</button>
    </div>

    <div class="ops-spacer"></div>

    <div class="ops-bp-wrap">
      <button class="ops-btn-coral" onclick={() => showBoardPackPop = !showBoardPackPop} disabled={!selected}>
        Generate Board Pack ▾
      </button>
      {#if showBoardPackPop}
        <div class="ops-bp-pop">
          <label>Meeting date
            <input type="date" bind:value={boardPackDate} />
          </label>
          <div class="ops-bp-actions">
            <button class="ops-btn-ghost" onclick={() => showBoardPackPop = false}>Cancel</button>
            <button class="ops-btn-coral" onclick={generateBoardPack}>Generate</button>
          </div>
        </div>
      {/if}
    </div>
  </div>

  {#if err}<div class="ops-err">{err}</div>{/if}

  {#if selected}
    <!-- header strip -->
    <div class="ops-header">
      <div class="ops-h-name">{selected.legal_name || selected.name || '—'}</div>
      <div class="ops-h-meta">
        <span><b>{fmtPct(selected.ownership_pct)}</b> ownership</span>
        <span class="dot">·</span>
        <span>invest <b>{fmtDate(selected.investment_date)}</b></span>
        <span class="dot">·</span>
        <span>stage <b>{selected.stage_at_invest || selected.stage || '—'}</b></span>
        <span class="dot">·</span>
        <span>MOIC <b>{selected.moic != null ? Number(selected.moic).toFixed(2) + '×' : '—'}</b></span>
      </div>
    </div>

    {#if !xPortfolio}
      <!-- KPI grid + anomaly tray -->
      <div class="ops-mid">
        <div class="ops-kpi-grid">
          <div class="ops-section-h">KPI Grid · last 12 periods</div>
          {#if !kpis.length}
            <div class="ops-empty">No KPIs ingested yet.</div>
          {:else}
            <div class="ops-grid-scroll">
              <table class="ops-grid">
                <thead>
                  <tr>
                    <th class="sticky">Metric</th>
                    {#each periods as p}<th>{p}</th>{/each}
                  </tr>
                </thead>
                <tbody>
                  {#each metricNames as m}
                    <tr>
                      <td class="sticky">{m}</td>
                      {#each periods as p}
                        {@const c = kpiCell(m, p)}
                        {@const v = c?.variance_pct ?? null}
                        <td class="ops-cell" onclick={() => toggleCell(m, p)}>
                          {#if c}
                            <div class="ops-cell-val">{fmtVal(c.actual)}</div>
                            {#if v != null}
                              <span class="chip {varianceClass(v)}">{fmtVar(v)}</span>
                            {/if}
                          {:else}
                            <span class="ops-cell-na">—</span>
                          {/if}
                        </td>
                      {/each}
                    </tr>
                    {#if expandedCell && expandedCell.startsWith(m + '|')}
                      <tr class="ops-spark-row">
                        <td colspan={periods.length + 1}>
                          <svg viewBox="0 0 200 40" width="100%" height="40" preserveAspectRatio="none">
                            <path d={sparklinePath(m)} fill="none" stroke="#C96342" stroke-width="1.5" />
                          </svg>
                        </td>
                      </tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </div>

        <aside class="ops-anom">
          <div class="ops-anom-h">
            <div class="ops-section-h">Anomaly Tray</div>
            <button class="ops-btn-ghost-sm" onclick={detectAnomalies}>Detect Now</button>
          </div>
          <div class="ops-anom-counts">
            <span class="chip chip-red">{anomGroups.critical.length} critical</span>
            <span class="chip chip-yellow">{anomGroups.warn.length} warn</span>
            <span class="chip chip-gray">{anomGroups.info.length} info</span>
          </div>
          {#if !anomalies.length}
            <div class="ops-empty">No active anomalies.</div>
          {:else}
            {#each ['critical', 'warn', 'info'] as sev}
              {#each anomGroups[sev] as a}
                <div class="ops-anom-row sev-{sev}">
                  <div class="ops-anom-top">
                    <span class="ops-anom-metric">{a.metric_name}</span>
                    <span class="ops-anom-period">{a.period}</span>
                  </div>
                  <div class="ops-anom-z">z={a.z_score != null ? Number(a.z_score).toFixed(2) : '—'}</div>
                  {#if a.explanation}<div class="ops-anom-exp">{a.explanation}</div>{/if}
                  <button class="ops-btn-ghost-sm" onclick={() => ackAnomaly(a.id)}>ack</button>
                </div>
              {/each}
            {/each}
          {/if}
        </aside>
      </div>

      <!-- initiatives kanban -->
      <div class="ops-kanban">
        <div class="ops-section-h">Initiatives</div>
        <div class="ops-kanban-cols">
          {#each KANBAN_COLS as col}
            <div
              class="ops-kanban-col"
              class:drag-over={dragOverCol === col}
              ondragover={(e) => onDragOver(e, col)}
              ondragleave={() => onDragLeave(col)}
              ondrop={(e) => onDrop(e, col)}
              role="region"
            >
              <div class="ops-kanban-h">
                <span class="ops-kanban-label">{col.replace('_', ' ')}</span>
                <span class="ops-kanban-count">{initiativesIn(col).length}</span>
                {#if col === 'proposed'}
                  <button class="ops-btn-ghost-sm" onclick={() => showNewInitiative = true}>+ New</button>
                {/if}
              </div>
              <div class="ops-kanban-body">
                {#each initiativesIn(col) as i}
                  <div
                    class="ops-init-card"
                    draggable="true"
                    ondragstart={(e) => onDragStart(e, i.id)}
                    role="button"
                    tabindex="0"
                  >
                    <div class="ops-init-title">{i.title}</div>
                    <div class="ops-init-meta">
                      {#if i.play_type}<span class="chip chip-coral">{i.play_type}</span>{/if}
                      {#if i.owner}<span class="ops-init-owner">{i.owner}</span>{/if}
                    </div>
                    <div class="ops-init-foot">
                      {#if i.due_date}<span>due {fmtDate(i.due_date)}</span>{/if}
                      {#if i.target_value_usd}<span class="ops-init-target">{fmtVal(i.target_value_usd)}</span>{/if}
                    </div>
                  </div>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      </div>
    {:else}
      <!-- cross-portfolio heatmap -->
      <div class="ops-heatmap">
        <div class="ops-section-h">Cross-Portfolio Heatmap · variance %</div>
        {#if !heatmapData || !heatmapData.kpis.length}
          <div class="ops-empty">No portfolio health data.</div>
        {:else}
          <table class="ops-heat-table">
            <thead>
              <tr>
                <th>Portco</th>
                {#each heatmapData.kpis as k}<th>{k}</th>{/each}
              </tr>
            </thead>
            <tbody>
              {#each heatmapData.portcos as p}
                {@const pname = p?.name || p?.legal_name || '—'}
                <tr>
                  <td><b>{pname}</b></td>
                  {#each heatmapData.kpis as k}
                    {@const v = heatCellVar(pname, k)}
                    <td
                      class="ops-heat-cell {varianceClass(v)}"
                      onclick={() => onHeatCellClick(pname, v)}
                      role="button"
                      tabindex="0"
                    >
                      {v != null ? fmtVar(v) : '—'}
                    </td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
    {/if}
  {:else}
    <div class="ops-empty">No portco selected.</div>
  {/if}

  <!-- new initiative modal -->
  {#if showNewInitiative}
    <div class="ops-modal-bg" onclick={() => showNewInitiative = false} role="presentation"></div>
    <div class="ops-modal">
      <div class="ops-modal-h">New Initiative</div>
      <label>Title <input type="text" bind:value={niTitle} placeholder="e.g. COGS reduction Q2" /></label>
      <label>Play type
        <select bind:value={niPlay}>
          {#each PLAY_TYPES as pt}<option value={pt}>{pt}</option>{/each}
        </select>
      </label>
      <label>Owner <input type="text" bind:value={niOwner} /></label>
      <label>Due date <input type="date" bind:value={niDue} /></label>
      <label>Target value (USD) <input type="number" bind:value={niTarget} /></label>
      <div class="ops-modal-actions">
        <button class="ops-btn-ghost" onclick={() => showNewInitiative = false}>Cancel</button>
        <button class="ops-btn-coral" onclick={createInitiative}>Create</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .ops-root {
    padding: 14px;
    color: #e8e3d6;
    font-size: 13px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: transparent;
  }
  .ops-topbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .ops-select {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    color: #e8e3d6;
    padding: 6px 10px;
    font-size: 13px;
    font-family: inherit;
    min-width: 200px;
  }
  .ops-toggle {
    display: inline-flex;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.02);
  }
  .ops-toggle button {
    background: transparent;
    color: #b8b0a0;
    border: 0;
    padding: 6px 12px;
    font-size: 11.5px;
    cursor: pointer;
    letter-spacing: 0.03em;
  }
  .ops-toggle button.active {
    background: #C96342;
    color: #fff;
    font-weight: 600;
  }
  .ops-spacer { flex: 1; }
  .ops-bp-wrap { position: relative; }
  .ops-bp-pop {
    position: absolute;
    right: 0;
    top: 100%;
    margin-top: 4px;
    background: #1A1614;
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px;
    z-index: 50;
    width: 240px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ops-bp-pop label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: #b8b0a0;
  }
  .ops-bp-pop input {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    color: #e8e3d6;
    padding: 5px 8px;
    font-size: 12px;
    font-family: inherit;
  }
  .ops-bp-actions { display: flex; gap: 6px; justify-content: flex-end; }

  .ops-btn-coral {
    background: #C96342;
    color: #fff;
    border: 0;
    padding: 7px 12px;
    font-size: 11.5px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.04em;
  }
  .ops-btn-coral:hover { background: #b35636; }
  .ops-btn-coral:disabled { background: rgba(201,99,66,0.3); cursor: not-allowed; }
  .ops-btn-ghost {
    background: transparent;
    color: #e8e3d6;
    border: 1px solid rgba(255,255,255,0.12);
    padding: 6px 10px;
    font-size: 11px;
    cursor: pointer;
  }
  .ops-btn-ghost-sm {
    background: transparent;
    color: #b8b0a0;
    border: 1px solid rgba(255,255,255,0.1);
    padding: 2px 6px;
    font-size: 10px;
    cursor: pointer;
    letter-spacing: 0.03em;
  }
  .ops-btn-ghost-sm:hover { color: #C96342; border-color: #C96342; }

  .ops-err {
    color: #ff8a7a;
    background: rgba(192,57,43,0.1);
    border: 1px solid rgba(192,57,43,0.3);
    padding: 6px 10px;
    font-size: 12px;
  }
  .ops-empty {
    color: #6b6557;
    padding: 16px;
    text-align: center;
    font-size: 12px;
    font-style: italic;
  }

  .ops-header {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ops-h-name { font-size: 16px; font-weight: 600; color: #fff; }
  .ops-h-meta { font-size: 11.5px; color: #b8b0a0; display: flex; gap: 8px; flex-wrap: wrap; }
  .ops-h-meta b { color: #e8e3d6; }
  .ops-h-meta .dot { color: #6b6557; }

  .ops-section-h {
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #b8b0a0;
    font-weight: 600;
  }

  .ops-mid {
    display: grid;
    grid-template-columns: 1fr 280px;
    gap: 12px;
  }
  @media (max-width: 900px) {
    .ops-mid { grid-template-columns: 1fr; }
  }

  .ops-kpi-grid {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ops-grid-scroll { overflow-x: auto; }
  .ops-grid {
    width: 100%;
    border-collapse: collapse;
    font-size: 11.5px;
  }
  .ops-grid th, .ops-grid td {
    padding: 6px 8px;
    text-align: right;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    white-space: nowrap;
  }
  .ops-grid th {
    font-size: 10px;
    text-transform: uppercase;
    color: #6b6557;
    letter-spacing: 0.04em;
    text-align: right;
    background: rgba(255,255,255,0.02);
  }
  .ops-grid th.sticky, .ops-grid td.sticky {
    text-align: left;
    position: sticky;
    left: 0;
    background: #1A1614;
    z-index: 1;
  }
  .ops-cell { cursor: pointer; }
  .ops-cell:hover { background: rgba(201,99,66,0.06); }
  .ops-cell-val { font-variant-numeric: tabular-nums; color: #e8e3d6; }
  .ops-cell-na { color: #6b6557; }
  .ops-spark-row td { background: rgba(201,99,66,0.04); padding: 4px 8px; }

  .chip {
    display: inline-block;
    border-radius: 9999px;
    padding: 1px 6px;
    font-size: 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    margin-left: 4px;
  }
  .chip-green  { background: rgba(16,185,129,0.2); color: #6ee7b7; }
  .chip-yellow { background: rgba(245,158,11,0.2); color: #fcd34d; }
  .chip-red    { background: rgba(244,63,94,0.2);  color: #fda4af; }
  .chip-gray   { background: rgba(255,255,255,0.06); color: #b8b0a0; }
  .chip-coral  { background: rgba(201,99,66,0.2);  color: #f0a890; }
  .chip-na     { background: rgba(255,255,255,0.04); color: #6b6557; }

  .ops-anom {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 460px;
    overflow-y: auto;
  }
  .ops-anom-h {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .ops-anom-counts { display: flex; gap: 4px; flex-wrap: wrap; }
  .ops-anom-row {
    padding: 8px;
    border-left: 2px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.02);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ops-anom-row.sev-critical { border-left-color: #f43f5e; }
  .ops-anom-row.sev-warn     { border-left-color: #f59e0b; }
  .ops-anom-row.sev-info     { border-left-color: #6b6557; }
  .ops-anom-top {
    display: flex;
    justify-content: space-between;
    font-size: 11.5px;
  }
  .ops-anom-metric { font-weight: 600; color: #e8e3d6; }
  .ops-anom-period { color: #6b6557; font-family: ui-monospace, monospace; }
  .ops-anom-z { font-size: 10.5px; color: #b8b0a0; font-family: ui-monospace, monospace; }
  .ops-anom-exp { font-size: 11px; color: #b8b0a0; line-height: 1.4; }

  .ops-kanban {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ops-kanban-cols {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
  }
  @media (max-width: 900px) {
    .ops-kanban-cols { grid-template-columns: repeat(2, 1fr); }
  }
  .ops-kanban-col {
    background: rgba(0,0,0,0.15);
    border: 1px solid rgba(255,255,255,0.04);
    padding: 8px;
    min-height: 200px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: border-color 0.1s;
  }
  .ops-kanban-col.drag-over {
    border-color: #C96342;
    background: rgba(201,99,66,0.06);
  }
  .ops-kanban-h {
    display: flex;
    align-items: center;
    gap: 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  .ops-kanban-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #b8b0a0;
    font-weight: 600;
    flex: 1;
  }
  .ops-kanban-count {
    background: rgba(255,255,255,0.06);
    color: #b8b0a0;
    padding: 1px 6px;
    border-radius: 9999px;
    font-size: 10px;
    font-family: ui-monospace, monospace;
  }
  .ops-kanban-body { display: flex; flex-direction: column; gap: 6px; }
  .ops-init-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    padding: 8px;
    cursor: grab;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ops-init-card:active { cursor: grabbing; }
  .ops-init-card:hover { border-color: rgba(201,99,66,0.4); }
  .ops-init-title { font-weight: 600; color: #e8e3d6; font-size: 12px; }
  .ops-init-meta { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; font-size: 10.5px; color: #b8b0a0; }
  .ops-init-owner { color: #b8b0a0; }
  .ops-init-foot {
    display: flex;
    justify-content: space-between;
    font-size: 10.5px;
    color: #6b6557;
    font-family: ui-monospace, monospace;
  }
  .ops-init-target { color: #e8e3d6; font-weight: 600; }

  .ops-heatmap {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ops-heat-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11.5px;
  }
  .ops-heat-table th, .ops-heat-table td {
    padding: 8px;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.04);
  }
  .ops-heat-table th {
    font-size: 10px;
    text-transform: uppercase;
    color: #6b6557;
    letter-spacing: 0.04em;
  }
  .ops-heat-table td:first-child { text-align: left; }
  .ops-heat-cell { cursor: pointer; font-family: ui-monospace, monospace; }
  .ops-heat-cell:hover { outline: 1px solid #C96342; }

  .ops-modal-bg {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 80;
  }
  .ops-modal {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: #1A1614;
    border: 1px solid rgba(255,255,255,0.08);
    padding: 16px;
    width: 360px;
    z-index: 81;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .ops-modal-h {
    font-size: 14px;
    font-weight: 600;
    color: #fff;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  .ops-modal label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: #b8b0a0;
  }
  .ops-modal input, .ops-modal select {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    color: #e8e3d6;
    padding: 6px 8px;
    font-size: 12px;
    font-family: inherit;
  }
  .ops-modal-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 6px; }
</style>
