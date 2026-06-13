<script>
  import Icon from '$lib/Icon.svelte';
 import { onDestroy } from 'svelte';
 let { cell, data, ondrill, isNew = false } = $props();
 let chartEl = $state();
 let chart = null;
 let showInsight = $state(false);

 function fmtNum(v) {
 if (v == null) return '—';
 if (typeof v !== 'number') return String(v);
 if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(1)+'M';
 if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1)+'K';
 return v.toFixed(v % 1 === 0 ? 0 : 1);
 }

 // Render-time decimal cleanup — strip "182.333333334" → "182"
 // Applied to OLD specs that were persisted before backend narrator regex shipped.
 function cleanText(t) {
   if (!t || typeof t !== 'string') return t;
   return t.replace(/\b\d+\.\d{3,}\b/g, (m) => {
     const n = parseFloat(m);
     if (isNaN(n)) return m;
     if (n === Math.floor(n)) return String(Math.floor(n));
     if (Math.abs(n) >= 100) return String(Math.round(n));
     if (Math.abs(n) >= 10) return n.toFixed(1);
     return n.toFixed(2);
   });
 }

 function renderPlaceholder() {
   if (!chartEl) return;
   chartEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#888;font-size:11px;font-style:italic;">no data</div>';
 }

 async function renderChart() {
 if (!chartEl || cell.type !== 'chart') return;
 const echarts = await import('echarts');
 if (chart) chart.dispose();
 const cfg = cell.config || {};

 // NEW (Deep Dash 9-stage): if pre-built ECharts options envelope provided
 // AND non-empty, use it directly. Skip x_col/y_col rebuild path.
 const eo = cfg.echarts_options;
 const hasEo = eo && typeof eo === 'object' && Object.keys(eo).length > 0;
 const eoSeries = hasEo && Array.isArray(eo.series) ? eo.series : null;
 const eoHasData = eoSeries && eoSeries.some((s) => Array.isArray(s?.data) && s.data.length > 0);

 // Detect gauges with single-value baked in (eoHasData=false may still be valid gauge)
 const seriesArr0 = hasEo && Array.isArray(eo.series) ? eo.series : [];
 const hasGaugeValue = seriesArr0.some((s) =>
   s?.type === 'gauge' && (
     (Array.isArray(s.data) && s.data.length > 0) ||
     s.value != null ||
     (s.detail && s.detail.formatter)
   )
 );
 if (hasEo && (eoHasData || hasGaugeValue)) {
   chart = echarts.init(chartEl);
   // Beautify: detect chart type from series; apply clean overrides
   const cleanEo = JSON.parse(JSON.stringify(eo)); // deep clone
   const seriesArr = Array.isArray(cleanEo.series) ? cleanEo.series : [];
   const isGauge = seriesArr.some((s) => s?.type === 'gauge');
   const isPie = seriesArr.some((s) => s?.type === 'pie');
   if (isGauge) {
     // Force-rebuild gauge series w/ clean defaults — discard LLM's messy
     // axisLabel/splitLine/pointer/title that overlap value
     seriesArr.forEach((s) => {
       if (s.type !== 'gauge') return;
       // Extract value from any of the LLM's possible shapes
       let val = null;
       let label = cell.title || '';
       if (Array.isArray(s.data) && s.data.length > 0) {
         const d0 = s.data[0];
         if (typeof d0 === 'object' && d0 !== null) {
           val = d0.value ?? null;
           if (d0.name) label = d0.name;
         } else {
           val = d0;
         }
       } else if (s.value != null) {
         val = s.value;
       }
       // Discard ALL keys that cause label/value overlap
       const cleanSeries = {
         type: 'gauge',
         radius: '85%',
         center: ['50%', '58%'],
         startAngle: 200,
         endAngle: -20,
         min: s.min ?? 0,
         max: s.max ?? (typeof val === 'number' ? Math.max(100, val * 1.2) : 100),
         axisLine: { lineStyle: { width: 14, color: [[1, '#e2ddd2']] } },
         progress: { show: true, width: 14, itemStyle: { color: '#c96342' } },
         pointer: { show: false },
         anchor: { show: false },
         axisTick: { show: false },
         splitLine: { show: false },
         axisLabel: { show: false },
         title: { show: false },
         detail: {
           valueAnimation: true,
           offsetCenter: [0, '5%'],
           fontSize: 30,
           fontWeight: 700,
           color: '#2c2a26',
           fontFamily: 'Source Serif Pro, Georgia, serif',
           formatter: (v) => {
             if (typeof v !== 'number' || isNaN(v)) return String(v ?? '—');
             if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
             if (v === Math.floor(v)) return String(v);
             return v.toFixed(1);
           },
         },
         data: val != null ? [{ value: val }] : [],
       };
       // Replace the entire series object
       Object.keys(s).forEach((k) => delete s[k]);
       Object.assign(s, cleanSeries);
     });
     // gauges typically don't need x/y axis or grid
     delete cleanEo.xAxis;
     delete cleanEo.yAxis;
     delete cleanEo.grid;
     delete cleanEo.legend;
     delete cleanEo.title;  // suppress top-of-chart title — cell already has it
   } else if (isPie) {
     seriesArr.forEach((s) => {
       if (s.type !== 'pie') return;
       s.radius = s.radius || ['45%', '72%'];
       s.center = s.center || ['50%', '52%'];
       s.itemStyle = { ...(s.itemStyle || {}), borderColor: '#fdfaf5', borderWidth: 2 };
       s.label = { ...(s.label || {}), fontSize: 11, color: '#6b6557' };
       s.labelLine = { length: 8, length2: 6 };
     });
     cleanEo.legend = { ...(cleanEo.legend || {}), bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } };
     delete cleanEo.xAxis;
     delete cleanEo.yAxis;
     delete cleanEo.grid;
   } else {
     // default polish for bar/line/scatter
     if (cleanEo.xAxis) {
       const ax = Array.isArray(cleanEo.xAxis) ? cleanEo.xAxis[0] : cleanEo.xAxis;
       ax.axisLabel = { ...(ax.axisLabel || {}), fontSize: 10, color: '#6b6557' };
       ax.axisLine = { ...(ax.axisLine || {}), lineStyle: { color: '#c8c3b8' } };
       ax.axisTick = { ...(ax.axisTick || {}), lineStyle: { color: '#c8c3b8' } };
     }
     if (cleanEo.yAxis) {
       const ay = Array.isArray(cleanEo.yAxis) ? cleanEo.yAxis[0] : cleanEo.yAxis;
       ay.axisLabel = { ...(ay.axisLabel || {}), fontSize: 10, color: '#6b6557' };
       ay.splitLine = { ...(ay.splitLine || {}), lineStyle: { color: '#ece8de', type: 'dashed' } };
     }
     cleanEo.legend = cleanEo.legend ? { ...cleanEo.legend, top: 4, textStyle: { fontSize: 11 } } : undefined;
   }
   const opt = { grid: { left: 48, right: 16, top: 32, bottom: 32, containLabel: true }, ...cleanEo };
   chart.setOption(opt);
   return;
 }

 // Legacy path: rebuild options from rows + x_col/y_col + chart_type.
 // Allow rows to be sourced from echarts_options.series[0].data if data prop missing.
 let rows = data?.rows;
 if ((!rows || !rows.length) && eoSeries && Array.isArray(eoSeries[0]?.data) && eoSeries[0].data.length) {
   const sd = eoSeries[0].data;
   const xAxisData = (eo.xAxis && (Array.isArray(eo.xAxis) ? eo.xAxis[0]?.data : eo.xAxis.data)) || null;
   rows = sd.map((v, i) => {
     if (v && typeof v === 'object' && !Array.isArray(v)) {
       return { name: v.name ?? i, value: v.value ?? v };
     }
     if (Array.isArray(v)) return { name: v[0], value: v[1] };
     return { name: xAxisData ? xAxisData[i] : i, value: v };
   });
 }
 if (!rows || !rows.length) { renderPlaceholder(); return; }
 chart = echarts.init(chartEl);
 const ct = cfg.chart_type || 'line';
 const xCol = cfg.x_col || Object.keys(rows[0])[0];
 const yCol = cfg.y_col || Object.keys(rows[0])[1] || Object.keys(rows[0])[0];
 const xData = rows.map((r) => r[xCol]);
 const yData = rows.map((r) => r[yCol]);

 let option;
 if (ct === 'pie') {
 option = {
 tooltip: {trigger:'item'},
 series: [{type:'pie', radius:['40%','70%'], data: rows.map((r) => ({name:r[xCol], value:r[yCol]}))}]
 };
 } else if (ct === 'bar') {
 option = {
 tooltip:{}, xAxis:{type:'category', data:xData, axisLabel:{rotate:30, fontSize:10}},
 yAxis:{type:'value'}, series:[{type:'bar', data:yData, itemStyle:{color:'#2e7d32'}}]
 };
 } else if (ct === 'scatter') {
 option = {
 tooltip:{}, xAxis:{}, yAxis:{},
 series:[{type:'scatter', data: rows.map((r) =>[r[xCol], r[yCol]]), itemStyle:{color:'#1976d2'}}]
 };
 } else {
 option = {
 tooltip:{trigger:'axis'}, xAxis:{type:'category', data:xData, axisLabel:{fontSize:10}},
 yAxis:{type:'value'},
 series:[{type:'line', data:yData, smooth:true, areaStyle: ct==='area'?{opacity:0.2}:undefined, itemStyle:{color:'#2e7d32'}}]
 };
 }
 option.grid = {left:40, right:10, top:20, bottom:30};
 chart.setOption(option);
 }

 $effect(() => { if (cell.type === 'chart') renderChart(); });
 $effect(() => {
 const handler = () => chart?.resize();
 window.addEventListener('resize', handler);
 return () => window.removeEventListener('resize', handler);
 });
 onDestroy(() => chart?.dispose());

 const hasInsight = $derived(!!(cell.config?.cause || cell.config?.action || cell.config?.headline));

 function uniqueAxis(rows, key) { return [...new Set(rows.map(r => r[key]))].sort(); }
 function findCellValue(rows, x_axis, x, y_axis, y, value_col) {
 const r = rows.find(row => row[x_axis] === x && row[y_axis] === y);
 return r ? r[value_col] : null;
 }
 function bandColor(value, legend) {
 if (value == null) return null;
 const match = legend?.find(b => b.name === String(value).toLowerCase());
 return match?.color || '#9e9e9e';
 }
</script>

{#snippet badges()}
  {#if cell.config?.domain_tags?.includes('gap')}
    <span class="gap-badge">GAP CLOSED</span>
  {/if}
  {#if cell.config?.drill_into?.length > 0 && cell.type !== 'insight'}
    <button class="drill-btn" onclick={() => ondrill?.(cell)} title="Drill deeper" aria-label="Drill"><Icon name="search" size={14} /></button>
  {/if}
{/snippet}

{#snippet insightOverlay()}
  {#if hasInsight && cell.type !== 'insight'}
    <button class="info-btn"
            onmouseenter={() => showInsight = true}
            onmouseleave={() => showInsight = false}
            onclick={() => showInsight = !showInsight}
            title="Insight"
            aria-label="Insight">ℹ</button>
    {#if showInsight}
      <div class="insight-overlay" class:high={cell.config?.severity === 'high'}>
        {#if cell.config?.headline}
          <div class="ov-section">
            <span class="ov-label">FINDING</span>
            <div>{cell.config.headline}</div>
          </div>
        {/if}
        {#if cell.config?.cause}
          <div class="ov-section">
            <span class="ov-label">WHY</span>
            <div>{cell.config.cause}</div>
          </div>
        {/if}
        {#if cell.config?.action}
          <div class="ov-section">
            <span class="ov-label">ACTION</span>
            <div>→ {cell.config.action}</div>
          </div>
        {/if}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet narrativeBlock()}
  {#if cell.config?.narrative}
    <div class="narrative">{cleanText(cell.config.narrative)}</div>
  {/if}
{/snippet}

{#snippet confidenceBadge()}
  {#if cell.config?.confidence}
    {@const _cl = String(cell.config.confidence).toLowerCase()}
    {#if _cl === 'medium' || _cl === 'low'}
      <span class="confidence-badge conf-{_cl}">{_cl}</span>
    {/if}
  {/if}
{/snippet}

{#snippet sourcesFooter()}
  {#if Array.isArray(cell.config?.sources) && cell.config.sources.length}
    <div class="sources-footer">Sources: {cell.config.sources.join(', ')}</div>
  {/if}
{/snippet}

{#snippet verifiedBadge()}
  {#if cell.verified === true}
    <span class="verified-badge" title="verified vs pinned metric">✓ verified vs pinned metric</span>
  {/if}
{/snippet}

{#if !cell.sql && cell.narrative && cell.type !== 'insight' && cell.type !== 'kpi' && cell.type !== 'chart' && cell.type !== 'table' && cell.type !== 'network_grid'}
  <!-- narrative-only panel: render clean text, no warning -->
  <div class="cell-inner" class:cell-highlight={isNew} style="padding: 12px 14px; font-family: 'Source Serif Pro', Georgia, serif; font-size: 13px; line-height: 1.55; color: var(--pw-ink, #2c2a26);">
    {#if cell.title}<div style="font-weight: 600; margin-bottom: 6px;">{cleanText(cell.title)}</div>{/if}
    <div>{cleanText(cell.narrative)}</div>
    {@render sourcesFooter()}
  </div>
{:else if cell.type === 'kpi'}
  {@const _eo = cell.config?.echarts_options}
  {@const _kpiVal = data?.value ?? (_eo && typeof _eo === 'object' ? _eo.value : undefined)}
  <div class="kpi cell-inner" class:cell-highlight={isNew}>
    {@render badges()}
    {@render confidenceBadge()}
    <div class="lbl" title={cell.title}>{cleanText(cell.title)}</div>
    <div class="val">{fmtNum(_kpiVal)}</div>
    {#if data?.delta != null}
      <div class="delta" class:bad={data.delta < 0}>{data.delta > 0 ? '↑' : '↓'} {fmtNum(Math.abs(data.delta))}</div>
    {/if}
    {@render narrativeBlock()}
    {@render sourcesFooter()}
    {@render verifiedBadge()}
    {@render insightOverlay()}
  </div>
{:else if cell.type === 'chart'}
  {@const _eoChart = cell.config?.echarts_options}
  {@const _hasEoData = _eoChart && typeof _eoChart === 'object' && Array.isArray(_eoChart.series) && _eoChart.series.some((s) => Array.isArray(s?.data) && s.data.length > 0)}
  <div class="chart-wrap cell-inner" class:cell-highlight={isNew}>
    {@render badges()}
    {@render confidenceBadge()}
    <div class="title">{cleanText(cell.title)}</div>
    {#if data?.error && data.error !== 'no sql'}
      <div class="err"><Icon name="alert-triangle" size={14} /> {data.error}</div>
    {:else if !data?.rows?.length && !_hasEoData}
      {#if cell.narrative}
        <!-- chart with no data but has narrative: skip empty, render narrative cleanly -->
      {:else}
        <div class="empty">no data</div>
      {/if}
    {:else}
      <div bind:this={chartEl} class="chart"></div>
    {/if}
    {@render narrativeBlock()}
    {@render sourcesFooter()}
    {@render verifiedBadge()}
    {@render insightOverlay()}
  </div>
{:else if cell.type === 'table'}
  <div class="tbl-wrap cell-inner" class:cell-highlight={isNew}>
    {@render badges()}
    {@render confidenceBadge()}
    <div class="title">{cleanText(cell.title)}</div>
    {#if !data?.rows?.length}
      {#if cell.narrative}<!-- skip empty, narrative renders below -->{:else}<div class="empty">no rows</div>{/if}
    {:else}
      <table>
        <thead><tr>{#each (data.cols || Object.keys(data.rows[0])) as c}<th>{c}</th>{/each}</tr></thead>
        <tbody>
          {#each data.rows.slice(0,15) as r}<tr>{#each (data.cols || Object.keys(r)) as c}<td>{r[c] ?? ''}</td>{/each}</tr>{/each}
        </tbody>
      </table>
    {/if}
    {@render narrativeBlock()}
    {@render sourcesFooter()}
    {@render verifiedBadge()}
    {@render insightOverlay()}
  </div>
{:else if cell.type === 'network_grid'}
  <div class="ng-wrap cell-inner" class:cell-highlight={isNew}>
    {@render badges()}
    <div class="title">{cleanText(cell.title)}</div>
    {#if data?.error}
      <div class="err"><Icon name="alert-triangle" size={14} /> {data.error}</div>
    {:else if !cell.config?.x_axis || !cell.config?.y_axis}
      <div class="empty">grid config missing</div>
    {:else if !data?.rows?.length}
      <div class="empty">no network data</div>
    {:else}
      {@const xAxis = cell.config.x_axis}
      {@const yAxis = cell.config.y_axis}
      {@const valCol = cell.config.value_col || 'value'}
      {@const legend = cell.config.band_legend || []}
      {@const xs = uniqueAxis(data.rows, xAxis)}
      {@const ys = uniqueAxis(data.rows, yAxis)}
      <div class="network-grid">
        <div class="ng-table" style="grid-template-columns: minmax(80px, max-content) repeat({xs.length}, 28px);">
          <div class="ng-corner"></div>
          {#each xs as x}
            <div class="ng-head" class:rotated={xs.length > 8}><span>{x}</span></div>
          {/each}
          {#each ys as y}
            <div class="ng-ylabel">{y}</div>
            {#each xs as x}
              {@const v = findCellValue(data.rows, xAxis, x, yAxis, y, valCol)}
              {@const c = bandColor(v, legend)}
              {#if c}
                <button
                  class="ng-cell"
                  style="background:{c}"
                  title="{y}: {x} → {v}"
                  aria-label="{y} {x} {v}"
                  onclick={() => ondrill?.(cell, {x, y})}
                ></button>
              {:else}
                <div class="ng-cell empty-cell" title="{y}: {x} → no data"></div>
              {/if}
            {/each}
          {/each}
        </div>
      </div>
      {#if legend.length}
        <div class="ng-legend">
          {#each legend as b}
            <span class="ng-leg-item"><span class="ng-swatch" style="background:{b.color}"></span>{b.name}</span>
          {/each}
        </div>
      {/if}
    {/if}
    {@render insightOverlay()}
  </div>
{:else if cell.type === 'insight'}
  <div class="insight" class:high={cell.config?.severity === 'high'} class:cell-highlight={isNew}>
    <div class="ico">{cell.config?.severity === 'high' ? '' : cell.config?.severity === 'medium' ? '' : ''}</div>
    <div class="insight-body">
      <div class="finding">{cell.title || cell.config?.headline}</div>
      {#if cell.config?.cause}<div class="cause"><strong>WHY:</strong> {cell.config.cause}</div>{/if}
      {#if cell.config?.action}<div class="action"><strong>ACTION:</strong> {cell.config.action}</div>{/if}
    </div>
  </div>
{/if}

<style>
 :global(.cell) {
   display: flex;
   background: var(--pw-bg, #fdfaf5);
   border: 1px solid var(--pw-border, #e2ddd2);
   border-radius: 4px;
   overflow: hidden;
   transition: box-shadow 0.18s ease, transform 0.18s ease;
 }
 :global(.cell:hover) {
   box-shadow: 0 4px 14px rgba(44, 42, 38, 0.06);
   transform: translateY(-1px);
 }
 .cell-inner { width: 100%; height: 100%; box-sizing: border-box; overflow: hidden; position: relative; }
 .kpi { padding: 18px 16px 14px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
 .lbl { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--pw-muted, #888); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.35; font-weight: 600; }
 .val { font-family: 'Source Serif Pro', Georgia, serif; font-size: 36px; font-weight: 700; color: var(--pw-ink, #2c2a26); line-height: 1.1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .title { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-family: 'Source Serif Pro', Georgia, serif; font-size: 14px; font-weight: 600; color: var(--pw-ink, #2c2a26); line-height: 1.35; margin-bottom: 10px; text-transform: none; letter-spacing: 0; }
 .delta { font-size: 11px; font-weight: 600; color: #16a34a; }
 .delta.bad { color: #c0392b; }
 .chart-wrap, .tbl-wrap { padding: 14px 16px 12px; height: 100%; display: flex; flex-direction: column; gap: 8px; }
 .drill-btn { position: absolute; top: 8px; right: 8px; background: none; border: none; opacity: 0.35; cursor: pointer; font-size: 12px; padding: 2px 6px; z-index: 2; color: var(--pw-muted); }
 .drill-btn:hover { opacity: 0.85; }
 .gap-badge { position: absolute; top: 8px; left: 8px; background: rgba(231, 102, 81, 0.12); color: #c0392b; font-size: 9px; padding: 2px 6px; border-radius: 2px; font-weight: 700; z-index: 2; letter-spacing: 0.06em; text-transform: uppercase; }
 .chart { flex: 1; min-height: 220px; }
 .empty, .err { color: var(--pw-muted, #888); font-size: 11px; padding: 24px; text-align: center; font-style: italic; }
 .err { color: #c0392b; }
 table { width: 100%; font-size: 11.5px; border-collapse: collapse; }
 th { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e2ddd2); color: var(--pw-muted, #6b6557); font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; font-size: 10px; }
 td { padding: 5px 8px; border-bottom: 1px solid rgba(232, 227, 215, 0.5); color: var(--pw-ink, #2c2a26); }
 tr:hover td { background: rgba(201, 99, 66, 0.03); }
 .insight { display: flex; gap: 10px; padding: 12px; background: #fff3e0; border-left: 3px solid #e65100; }
 .insight.high { background: #ffebee; border-left-color: #c62828; }
 .ico { font-size: 13px; }
 .insight-body { flex: 1; }
 .finding { font-weight: 600; }
 .cause { font-size: 11px; color: #555; margin-top: 6px; line-height: 1.4; }
 .action { font-size: 11px; color: #1a1a1a; margin-top: 4px; line-height: 1.4; }
 .info-btn {
 position: absolute; bottom: 4px; right: 4px;
 background: #e8f5e9; color: #2e7d32; border: none;
 border-radius: 50%; width: 18px; height: 18px;
 font-size: 11px; cursor: pointer; opacity: 0.6;
 z-index: 10;
 }
 .info-btn:hover { opacity: 1; }
 .insight-overlay {
 position: absolute; bottom: 26px; right: 4px;
 background: #1a1a1a; color: #fafaf7; padding: 12px;
 border-radius: var(--pw-radius-sm); font-size: 11px; line-height: 1.5;
 width: 280px; z-index: 100;
 box-shadow: 0 4px 12px rgba(0,0,0,0.3);
 }
 .insight-overlay.high { border-left: 3px solid #c62828; }
 .ov-section { margin-bottom: 8px; }
 .ov-label { font-size: 11px; color: #888; letter-spacing: 0.5px; text-transform: uppercase; display: block; margin-bottom: 2px; }
 .ng-wrap { padding: 8px 12px; height: 100%; display: flex; flex-direction: column; }
 .network-grid { overflow: auto; max-height: 100%; flex: 1; font-family: ui-monospace, Menlo, monospace; }
 .ng-table { display: grid; gap: 2px; align-items: end; }
 .ng-corner { position: sticky; top: 0; left: 0; background: white; z-index: 3; }
 .ng-head { font-size: 10px; color: #555; padding: 2px; position: sticky; top: 0; background: white; z-index: 1; text-align: center; height: 28px; display: flex; align-items: end; justify-content: center; }
 .ng-head.rotated span { transform: rotate(-45deg); transform-origin: left bottom; white-space: nowrap; display: inline-block; }
 .ng-ylabel { font-size: 10px; color: #333; padding: 0 6px; position: sticky; left: 0; background: white; z-index: 1; display: flex; align-items: center; height: 24px; white-space: nowrap; }
 .ng-cell { width: 24px; height: 24px; border-radius: var(--pw-radius-sm); border: 1px solid rgba(0,0,0,0.08); cursor: pointer; padding: 0; }
 .ng-cell:hover { outline: 1px solid #1a1a1a; }
 .ng-cell.empty-cell { background: #f5f5f5; background-image: repeating-linear-gradient(45deg, transparent, transparent 4px, #ddd 4px, #ddd 5px); cursor: default; }
 .ng-legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; font-size: 10px; font-family: ui-monospace, Menlo, monospace; color: #555; }
 .ng-leg-item { display: inline-flex; align-items: center; gap: 4px; }
 .ng-swatch { width: 10px; height: 10px; border-radius: var(--pw-radius-sm); border: 1px solid rgba(0,0,0,0.1); display: inline-block; }
 .narrative {
   font-family: 'Source Serif 4', Georgia, serif;
   font-style: italic;
   font-size: 11.5px;
   color: #5a554d;
   line-height: 1.45;
   margin-top: 8px;
   padding: 6px 8px 0;
   border-top: 1px solid rgba(201, 99, 66, 0.12);
 }
 .confidence-badge {
   position: absolute;
   top: 4px;
   right: 4px;
   font-size: 9px;
   font-weight: 600;
   letter-spacing: 0.4px;
   text-transform: uppercase;
   padding: 2px 6px;
   border-radius: var(--pw-radius-sm);
   z-index: 2;
   font-family: 'Inter', system-ui, sans-serif;
 }
 .confidence-badge.conf-low { background: #ececec; color: #6b6b6b; }
 .confidence-badge.conf-medium { background: rgba(46, 109, 192, 0.12); color: #2e6dc0; }
 .confidence-badge.conf-high { background: rgba(46, 125, 50, 0.14); color: #2e7d32; }
 .sources-footer {
   font-size: 10px;
   color: #8a8378;
   margin-top: 4px;
   padding: 0 8px 4px;
   font-family: 'Inter', system-ui, sans-serif;
   letter-spacing: 0.2px;
   white-space: nowrap;
   overflow: hidden;
   text-overflow: ellipsis;
 }
 /* When confidence badge present, nudge drill button left to avoid overlap */
 .cell-inner:has(.confidence-badge) .drill-btn { right: 56px; }
 .verified-badge {
   position: absolute;
   bottom: 4px;
   right: 4px;
   font-size: 9px;
   font-weight: 600;
   letter-spacing: 0.3px;
   padding: 2px 6px;
   border-radius: var(--pw-radius-sm);
   background: rgba(201, 99, 66, 0.12);
   color: #c96342;
   border: 1px solid rgba(201, 99, 66, 0.3);
   font-family: 'Inter', system-ui, sans-serif;
   z-index: 3;
   pointer-events: none;
   white-space: nowrap;
 }
 /* When verified badge present, nudge info-btn up to avoid overlap */
 .cell-inner:has(.verified-badge) .info-btn { bottom: 26px; }
 .cell-highlight {
   border: 2px solid var(--pw-accent, #c96342) !important;
   box-shadow: 0 0 24px rgba(201, 99, 66, 0.25);
   transition: border-color 1.5s ease-out 0.4s, box-shadow 1.5s ease-out 0.4s;
 }
</style>
