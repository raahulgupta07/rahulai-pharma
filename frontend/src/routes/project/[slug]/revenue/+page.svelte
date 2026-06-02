<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/stores';
 import { base } from '$app/paths';

 const slug = $derived($page.params.slug);

 let token = $state('');
 let loading = $state(true);
 let err = $state('');
 let schemaInfo = $state<any>(null);
 let current = $state<any>(null);
 let breakdown = $state<any>(null);
 let trend = $state<any[]>([]);
 let retention = $state<any>(null);
 let cohort = $state<any>(null);
 let lastFetched = $state<Date | null>(null);
 let snapshotting = $state(false);
 let snapshotMsg = $state('');

 // Period selector
 type Period = 'this_month' | 'last_month' | 'custom';
 let period = $state<Period>('last_month');
 let customStart = $state('');
 let customEnd = $state('');

 let waterfallEl: HTMLDivElement | null = null;
 let trendEl: HTMLDivElement | null = null;
 let cohortEl: HTMLDivElement | null = null;
 let waterfallChart: any = null;
 let trendChart: any = null;
 let cohortChart: any = null;

 function _h(): Record<string, string> {
 return token ? { Authorization: `Bearer ${token}` } : {};
 }

 function fmtMoney(v: any): string {
 if (v === null || v === undefined || v === '') return '—';
 const n = Number(v);
 if (!Number.isFinite(n)) return '—';
 if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
 if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
 return `$${n.toFixed(0)}`;
 }

 function fmtPct(v: any): string {
 if (v === null || v === undefined) return '—';
 const n = Number(v);
 if (!Number.isFinite(n)) return '—';
 return `${n.toFixed(1)}%`;
 }

 function lastCompletedMonthBounds(): { start: string; end: string } {
 const today = new Date();
 const firstThis = new Date(today.getFullYear(), today.getMonth(), 1);
 const lastPrev = new Date(firstThis.getTime() - 24 * 3600 * 1000);
 const firstPrev = new Date(lastPrev.getFullYear(), lastPrev.getMonth(), 1);
 const fmt = (d: Date) => d.toISOString().slice(0, 10);
 return { start: fmt(firstPrev), end: fmt(lastPrev) };
 }

 function thisMonthBounds(): { start: string; end: string } {
 const today = new Date();
 const firstThis = new Date(today.getFullYear(), today.getMonth(), 1);
 const fmt = (d: Date) => d.toISOString().slice(0, 10);
 return { start: fmt(firstThis), end: fmt(today) };
 }

 function activeBounds(): { start: string; end: string } {
 if (period === 'custom' && customStart && customEnd) {
 return { start: customStart, end: customEnd };
 }
 if (period === 'this_month') return thisMonthBounds();
 return lastCompletedMonthBounds();
 }

 async function loadSchema() {
 try {
 const r = await fetch(`/api/projects/${slug}/mrr/schema-detection`, { headers: _h() });
 schemaInfo = await r.json();
 } catch (e: any) {
 schemaInfo = { found: false, error: e.message };
 }
 }

 async function loadAll() {
 loading = true; err = '';
 try {
 await loadSchema();
 if (!schemaInfo?.found) {
 loading = false;
 return;
 }
 const { start, end } = activeBounds();
 const qs = `period_start=${start}&period_end=${end}`;
 const [c, b, t, ret, coh] = await Promise.all([
 fetch(`/api/projects/${slug}/mrr/current`, { headers: _h() }).then(r => r.json()),
 fetch(`/api/projects/${slug}/mrr/breakdown?${qs}`, { headers: _h() }).then(r => r.json()),
 fetch(`/api/projects/${slug}/mrr/trend?months=12`, { headers: _h() }).then(r => r.json()),
 fetch(`/api/projects/${slug}/mrr/retention?${qs}`, { headers: _h() }).then(r => r.json()),
 fetch(`/api/projects/${slug}/mrr/cohort-survival?max_periods=12`, { headers: _h() }).then(r => r.json()),
 ]);
 current = c; breakdown = b; trend = (t.series || []); retention = ret; cohort = coh;
 lastFetched = new Date();
 // wait next tick for DOM
 setTimeout(() => { renderCharts(); }, 50);
 } catch (e: any) { err = e.message; }
 loading = false;
 }

 async function snapshotNow() {
 snapshotting = true; snapshotMsg = '';
 try {
 const r = await fetch(`/api/projects/${slug}/mrr/snapshot-now`, {
 method: 'POST', headers: _h()
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || 'snapshot failed');
 snapshotMsg = `Snapshot saved · ${d.period_start} → ${d.period_end}`;
 await loadAll();
 } catch (e: any) {
 snapshotMsg = `ERROR: ${e.message}`;
 }
 snapshotting = false;
 }

 async function renderCharts() {
 if (!breakdown?.ok || !waterfallEl || !trendEl || !cohortEl) return;
 const echarts: any = await import('echarts');

 // ── Waterfall ───────────────────────────────────────
 const startMrr = Number(breakdown.start_mrr || 0);
 const endMrr = Number(breakdown.end_mrr || 0);
 const newM = Number(breakdown.new_mrr || 0);
 const expM = Number(breakdown.expansion_mrr || 0);
 const reactM = Number(breakdown.reactivation_mrr || 0);
 const contM = Number(breakdown.contraction_mrr || 0);
 const churnM = Number(breakdown.churn_mrr || 0);

 const labels = ['Start MRR', 'New', 'Expansion', 'Reactivation',
 'Contraction', 'Churn', 'End MRR'];
 // Waterfall: invisible base + visible delta
 const running: number[] = [startMrr];
 running.push(running[0]); // before New
 running.push(running[1] + newM); // after New
 running.push(running[2] + expM); // after Expansion
 running.push(running[3] + reactM); // after Reactivation
 running.push(running[4] - contM); // after Contraction
 running.push(running[5] - churnM); // after Churn

 const baseSeries: number[] = [];
 const deltaSeries: number[] = [];
 const colors: string[] = [];

 // Start
 baseSeries.push(0); deltaSeries.push(startMrr); colors.push('#1a1a1a');
 // New
 baseSeries.push(running[1]); deltaSeries.push(newM); colors.push('#2da44e');
 // Expansion
 baseSeries.push(running[2]); deltaSeries.push(expM); colors.push('#46b870');
 // Reactivation
 baseSeries.push(running[3]); deltaSeries.push(reactM); colors.push('#1f6feb');
 // Contraction (drop)
 baseSeries.push(running[5]); deltaSeries.push(contM); colors.push('#fb8500');
 // Churn (drop)
 baseSeries.push(running[6]); deltaSeries.push(churnM); colors.push('#cf222e');
 // End
 baseSeries.push(0); deltaSeries.push(endMrr); colors.push('#1a1a1a');

 if (waterfallChart) waterfallChart.dispose();
 waterfallChart = echarts.init(waterfallEl);
 waterfallChart.setOption({
 grid: { left: 60, right: 20, top: 30, bottom: 50 },
 tooltip: {
 trigger: 'axis', axisPointer: { type: 'shadow' },
 formatter: (params: any[]) => {
 const i = params[0].dataIndex;
 return `<b>${labels[i]}</b><br/>${fmtMoney(deltaSeries[i])}`;
 }
 },
 xAxis: { type: 'category', data: labels, axisLabel: { rotate: 0, fontSize: 10 } },
 yAxis: { type: 'value', axisLabel: { formatter: (v: number) => fmtMoney(v) } },
 series: [
 {
 name: 'base', type: 'bar', stack: 'total',
 itemStyle: { color: 'transparent' },
 data: baseSeries
 },
 {
 name: 'delta', type: 'bar', stack: 'total',
 data: deltaSeries.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
 label: { show: true, position: 'top',
 formatter: (p: any) => fmtMoney(p.value), fontSize: 10 }
 }
 ]
 });

 // ── Trend ───────────────────────────────────────
 if (trendChart) trendChart.dispose();
 trendChart = echarts.init(trendEl);
 const okTrend = (trend || []).filter((p: any) => p.mrr !== null && p.mrr !== undefined);
 const periods = okTrend.map((p: any) => p.period);
 const mrrSeries = okTrend.map((p: any) => p.mrr);
 const arrSeries = okTrend.map((p: any) => p.arr);
 trendChart.setOption({
 grid: { left: 60, right: 60, top: 40, bottom: 40 },
 legend: { data: ['MRR', 'ARR'], top: 0 },
 tooltip: { trigger: 'axis' },
 xAxis: { type: 'category', data: periods,
 axisLabel: { fontSize: 10, formatter: (v: string) => v.slice(0, 7) } },
 yAxis: [
 { type: 'value', name: 'MRR', axisLabel: { formatter: (v: number) => fmtMoney(v) } },
 { type: 'value', name: 'ARR', axisLabel: { formatter: (v: number) => fmtMoney(v) } }
 ],
 series: [
 { name: 'MRR', type: 'line', data: mrrSeries, smooth: true,
 itemStyle: { color: '#1f6feb' }, yAxisIndex: 0, areaStyle: { opacity: 0.15 } },
 { name: 'ARR', type: 'line', data: arrSeries, smooth: true,
 itemStyle: { color: '#2da44e' }, yAxisIndex: 1 }
 ]
 });

 // ── Cohort heatmap ──────────────────────────────
 if (cohortChart) cohortChart.dispose();
 cohortChart = echarts.init(cohortEl);
 const cohorts = (cohort?.cohorts || []) as any[];
 const matrix = (cohort?.survival_matrix || []) as (number | null)[][];
 const periodsCount = matrix.length > 0 ? matrix[0].length : 0;
 const data: any[] = [];
 cohorts.forEach((c: any, ri: number) => {
 const row = matrix[ri] || [];
 row.forEach((v: number | null, ci: number) => {
 if (v !== null && v !== undefined) data.push([ci, ri, v]);
 });
 });
 cohortChart.setOption({
 grid: { left: 80, right: 20, top: 30, bottom: 50 },
 tooltip: { position: 'top',
 formatter: (p: any) => {
 const c = cohorts[p.value[1]] || {};
 return `${c.cohort || ''} · M+${p.value[0]} · ${p.value[2]}%`;
 } },
 xAxis: { type: 'category',
 data: Array.from({ length: periodsCount }, (_, i) => `M+${i}`),
 splitArea: { show: true } },
 yAxis: { type: 'category', data: cohorts.map((c: any) => c.cohort), splitArea: { show: true } },
 visualMap: { min: 0, max: 100, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
 inRange: { color: ['#fee0d2', '#fdae6b', '#74c476', '#1a8a3a'] } },
 series: [{ type: 'heatmap', data, label: { show: true, formatter: (p: any) => `${p.value[2]}` } }]
 });
 }

 function handleResize() {
 waterfallChart?.resize();
 trendChart?.resize();
 cohortChart?.resize();
 }

 onMount(() => {
 token = (typeof localStorage !== 'undefined' ? localStorage.getItem('token') || '' : '');
 loadAll();
 if (typeof window !== 'undefined') {
 window.addEventListener('resize', handleResize);
 }
 });

 onDestroy(() => {
 if (typeof window !== 'undefined') {
 window.removeEventListener('resize', handleResize);
 }
 waterfallChart?.dispose(); trendChart?.dispose(); cohortChart?.dispose();
 });

 $effect(() => {
 // Re-render charts when period changes
 if (period && breakdown?.ok) {
 // intentional
 }
 });
</script>

<svelte:head><title>Revenue · {slug}</title></svelte:head>

<div class="rev-wrap">
  <header class="rev-head">
    <div>
      <h1><Icon name="dollar-sign" size={14} /> REVENUE</h1>
      <div class="sub">MRR · ARR · Retention · Cohorts · {slug}</div>
    </div>
    <div class="actions">
      <a class="btn-ghost" href={`${base}/project/${slug}/settings`}>← back to settings</a>
      <button class="btn-ghost" onclick={() => loadAll()} disabled={loading}>↻ REFRESH</button>
      <button class="btn-primary" onclick={snapshotNow} disabled={snapshotting || !schemaInfo?.found}>
        {snapshotting ? '...' : ' SNAPSHOT NOW'}
      </button>
    </div>
  </header>

  {#if loading}
    <div class="info">Loading…</div>
  {:else if err}
    <div class="error"><Icon name="alert-triangle" size={14} /> {err}</div>
  {:else if !schemaInfo?.found}
    <div class="error-state">
      <div class="error-emoji"><Icon name="ban" size={14} /></div>
      <h2>Subscription tables not detected</h2>
      <p>
        Apply the SaaS template, or set up tables matching the naming convention
        (<code>subscriptions</code>, <code>billing_cycles</code>, etc.) with columns
        <code>customer_id</code>, <code>mrr</code>, <code>started_at</code>, <code>canceled_at</code>.
      </p>
      {#if schemaInfo?.suggestions}
        <ul class="suggestions">
          {#each schemaInfo.suggestions as s}<li>{s}</li>{/each}
        </ul>
      {/if}
      <a class="btn-primary" href={`${base}/project/${slug}/settings`}>→ APPLY SAAS TEMPLATE</a>
    </div>
  {:else}
    {#if snapshotMsg}
      <div class="banner">{snapshotMsg}</div>
    {/if}

    <!-- Period selector -->
    <section class="period-bar">
      <span class="lbl">PERIOD</span>
      <button class="period-pill" class:active={period === 'this_month'}
        onclick={() => { period = 'this_month'; loadAll(); }}>THIS MONTH</button>
      <button class="period-pill" class:active={period === 'last_month'}
        onclick={() => { period = 'last_month'; loadAll(); }}>LAST MONTH</button>
      <button class="period-pill" class:active={period === 'custom'}
        onclick={() => { period = 'custom'; }}>CUSTOM</button>
      {#if period === 'custom'}
        <input type="date" bind:value={customStart} />
        <input type="date" bind:value={customEnd} />
        <button class="btn-ghost" onclick={() => loadAll()} disabled={!customStart || !customEnd}>APPLY</button>
      {/if}
      {#if breakdown?.ok}
        <span class="period-meta">{breakdown.period_start} → {breakdown.period_end}</span>
      {/if}
      {#if lastFetched}
        <span class="period-meta">· refreshed {lastFetched.toLocaleTimeString()}</span>
      {/if}
    </section>

    <!-- 6 KPI Cards -->
    <section class="kpi-grid">
      <div class="kpi">
        <div class="kpi-lbl">MRR</div>
        <div class="kpi-val">{fmtMoney(current?.mrr || 0)}</div>
        <div class="kpi-sub">current</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">ARR</div>
        <div class="kpi-val">{fmtMoney(current?.arr || 0)}</div>
        <div class="kpi-sub">annualized</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">NET NEW MRR</div>
        <div class="kpi-val" class:pos={(breakdown?.net_new_mrr ?? 0) >= 0}
                           class:neg={(breakdown?.net_new_mrr ?? 0) < 0}>
          {fmtMoney(breakdown?.net_new_mrr || 0)}
        </div>
        <div class="kpi-sub">period</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">GROSS RETENTION</div>
        <div class="kpi-val">{fmtPct(retention?.gross_retention_pct)}</div>
        <div class="kpi-sub">GRR</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">NET RETENTION</div>
        <div class="kpi-val" class:pos={(retention?.net_retention_pct ?? 0) > 100}>
          {fmtPct(retention?.net_retention_pct)}
        </div>
        <div class="kpi-sub">NRR</div>
      </div>
      <div class="kpi">
        <div class="kpi-lbl">ACTIVE SUBS</div>
        <div class="kpi-val">{current?.active_subscribers ?? 0}</div>
        <div class="kpi-sub">customers</div>
      </div>
    </section>

    <!-- Waterfall -->
    <section class="card">
      <h2>MRR MOVEMENT — {breakdown?.period_start || ''} → {breakdown?.period_end || ''}</h2>
      <div class="chart" bind:this={waterfallEl}></div>
      {#if breakdown?.ok}
        <div class="legend">
          <span class="legend-item"><i style="background:#2da44e"></i>NEW {fmtMoney(breakdown.new_mrr)}</span>
          <span class="legend-item"><i style="background:#46b870"></i>EXPANSION {fmtMoney(breakdown.expansion_mrr)}</span>
          <span class="legend-item"><i style="background:#1f6feb"></i>REACTIVATION {fmtMoney(breakdown.reactivation_mrr)}</span>
          <span class="legend-item"><i style="background:#fb8500"></i>CONTRACTION {fmtMoney(breakdown.contraction_mrr)}</span>
          <span class="legend-item"><i style="background:#cf222e"></i>CHURN {fmtMoney(breakdown.churn_mrr)}</span>
        </div>
      {/if}
    </section>

    <!-- Trend -->
    <section class="card">
      <h2>12-MONTH TREND — MRR + ARR</h2>
      <div class="chart" bind:this={trendEl}></div>
    </section>

    <!-- Cohort heatmap -->
    <section class="card">
      <h2>COHORT RETENTION — % OF SIGNUP COHORT STILL ACTIVE</h2>
      {#if (cohort?.cohorts || []).length === 0}
        <div class="info">No cohorts yet — needs at least one customer with subscription start date.</div>
      {:else}
        <div class="chart cohort-chart" bind:this={cohortEl}></div>
      {/if}
    </section>
  {/if}
</div>

<style>
 .rev-wrap { padding: 24px; max-width: 1400px; margin: 0 auto; font-family: -apple-system, system-ui, sans-serif; }
 .rev-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; gap: 16px; flex-wrap: wrap; }
 .rev-head h1 { font-size: 18px; font-weight: 900; margin: 0; letter-spacing: 0.02em; }
 .sub { font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 4px; }
 .actions { display: flex; gap: 8px; flex-wrap: wrap; }
 .btn-ghost { padding: 8px 14px; background: #fafaf5; border: 2px solid #1a1a1a; color: #1a1a1a; font-family: monospace; font-size: 11px; font-weight: 900; text-decoration: none; cursor: pointer; }
 .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn-primary { padding: 8px 14px; background: #1a1a1a; border: 2px solid #1a1a1a; color: #fff; font-family: monospace; font-size: 11px; font-weight: 900; text-decoration: none; cursor: pointer; }
 .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
 .info { padding: 30px; text-align: center; color: #666; font-size: 11px; }
 .error { padding: 16px; background: #fff0f0; border: 2px solid #cf222e; color: #cf222e; margin-bottom: 16px; font-size: 11px; }
 .banner { padding: 10px 14px; background: #f0f9ff; border: 2px solid #1f6feb; color: #1f6feb; margin-bottom: 16px; font-size: 11px; font-family: monospace; }
 .error-state { padding: 60px; text-align: center; background: #fafaf5; border: 2px dashed #cf222e; }
 .error-state h2 { font-size: 14px; font-weight: 900; margin: 12px 0 8px; }
 .error-state p { color: #444; max-width: 700px; margin: 0 auto 20px; line-height: 1.6; }
 .error-state code { background: #1a1a1a; color: #fff; padding: 2px 6px; font-size: 11px; }
 .error-emoji { font-size: 48px; }
 .suggestions { text-align: left; max-width: 700px; margin: 0 auto 20px; color: #666; font-size: 11px; }
 .period-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; padding: 10px 14px; background: #fafaf5; border: 2px solid #1a1a1a; }
 .period-bar .lbl { font-size: 10px; font-weight: 900; letter-spacing: 0.08em; color: #666; }
 .period-pill { padding: 6px 12px; background: #fff; border: 2px solid #1a1a1a; cursor: pointer; font-family: monospace; font-size: 10px; font-weight: 900; }
 .period-pill.active { background: #1a1a1a; color: #fff; }
 .period-meta { font-size: 11px; color: #666; font-family: monospace; }
 .period-bar input[type="date"] { padding: 6px 8px; border: 2px solid #1a1a1a; font-family: monospace; font-size: 11px; }
 .kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 20px; }
 @media (max-width: 1200px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
 @media (max-width: 700px) { .kpi-grid { grid-template-columns: repeat(2, 1fr); } }
 .kpi { padding: 14px; background: #fafaf5; border: 2px solid #1a1a1a; }
 .kpi-lbl { font-size: 10px; font-weight: 900; letter-spacing: 0.08em; color: #666; text-transform: uppercase; }
 .kpi-val { font-size: 16px; font-weight: 900; margin: 6px 0 4px; color: #1a1a1a; }
 .kpi-val.pos { color: #2da44e; }
 .kpi-val.neg { color: #cf222e; }
 .kpi-sub { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.05em; }
 .card { padding: 16px; background: #fff; border: 2px solid #1a1a1a; margin-bottom: 16px; }
 .card h2 { font-size: 11px; font-weight: 900; letter-spacing: 0.08em; margin: 0 0 12px; text-transform: uppercase; color: #1a1a1a; }
 .chart { width: 100%; height: 360px; }
 .cohort-chart { height: 460px; }
 .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 8px; font-size: 10px; font-family: monospace; }
 .legend-item { display: flex; align-items: center; gap: 4px; }
 .legend-item i { width: 12px; height: 12px; display: inline-block; }
</style>
