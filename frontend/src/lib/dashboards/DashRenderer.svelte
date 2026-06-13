<script>
  import Icon from '$lib/Icon.svelte';
 import Cell from './cells/Cell.svelte';
 let { spec, data = {}, editChangedIds = [], newPanelIds = new Set() } = $props();
 const cells = $derived(spec?.cells ?? []);
 function cleanTitle(t) {
 if (!t) return '';
 return String(t)
 .replace(/critical\s+style\s+rule.*/i, '')
 .replace(/fast\s+mode\b.*/i, '')
 .replace(/^build\s+dashboard\s+covering[:\s]*/i, '')
 .replace(/^\d+\)\s*/, '')
 .replace(/^[\s\-—:>•*#]+/, '')
 .trim()
 .slice(0, 80) || 'Dashboard';
 }
 const safeTitle = $derived(cleanTitle(spec?.title));
 const insights = $derived(spec?.insights ?? []);
 const filters = $derived(spec?.filters ?? []);
 const sevBg = (s) => s === 'high' ? '#ffebee' : s === 'medium' ? '#fff8e1' : '#e8f5e9';
 const sevFg = (s) => s === 'high' ? '#c62828' : s === 'medium' ? '#e65100' : '#2e7d32';

 let expandedId = $state(null);
 let drillData = $state({});
 let drilling = $state(false);

 async function handleDrill(cell) {
 if (expandedId === cell.id) { expandedId = null; return; }
 expandedId = cell.id;
 if (drillData[cell.id]) return;
 drilling = true;
 try {
 const r = await fetch('/api/dashboards/drill-cell', {
 method: 'POST',
 headers: { 'content-type': 'application/json' },
 body: JSON.stringify({
 cell,
 project_slug: spec?.project_slug || '',
 persona: spec?.persona || ''
 })
 });
 const d = await r.json();
 if (d.ok) drillData = { ...drillData, [cell.id]: d.cells || [] };
 } catch (e) {
 console.error('drill failed', e);
 } finally {
 drilling = false;
 }
 }
</script>

<div class="dash">
  {#if spec?.template}
    <div class="tpl-label">Template: {spec.template}</div>
  {/if}
  {#if safeTitle}<h1>{safeTitle}</h1>{/if}

  {#if filters.length}
    <div class="filters">
      {#each filters as f}
        <span class="chip">{f.label ?? f.col ?? f.key ?? 'filter'}: <b>{f.value ?? f.default ?? 'all'}</b></span>
      {/each}
    </div>
  {/if}

  {#if insights.length}
    <div class="insights">
      {#each insights as ins}
        <div class="banner" style:background={sevBg(ins.severity)} style:color={sevFg(ins.severity)}>
          <strong>{ins.title ?? 'Insight'}</strong> — {ins.finding ?? ins.text ?? ''}
        </div>
      {/each}
    </div>
  {/if}

  <div class="grid">
    {#each cells as cell, idx (cell.id)}
      {@const rawG = cell.grid ?? [0, idx*2, 6, 2]}
      {@const gx = Math.max(0, Math.min(11, rawG[0] || 0))}
      {@const gy = Math.max(0, rawG[1] || 0)}
      {@const gw = Math.max(1, Math.min(12 - gx, rawG[2] || 6))}
      {@const gh = Math.max(1, rawG[3] || 2)}
      {@const isNew = newPanelIds.has(cell.id) || newPanelIds.has(`p_${idx}`)}
      <div class="card cell fade-in-panel" class:flash-cell={editChangedIds.includes(cell.id)} class:cell-panel-new={isNew}
        data-palette={cell.config?.palette_role || 'neutral'}
        data-panel-id={cell.id}
        style="grid-column-start:{gx+1}; grid-row-start:{gy+1}; grid-column-end: span {gw}; grid-row-end: span {gh};">
        <Cell {cell} data={data[cell.id]} ondrill={handleDrill} isNew={isNew} />
      </div>
      {#if expandedId === cell.id}
        <div class="drill-row" style="grid-column: 1 / -1;">
          <div class="drill-header">
            <span>↳ Drilled from "{cell.title}"{drilling && !drillData[cell.id] ? ' — loading...' : ''}</span>
            <button class="drill-close" onclick={() => expandedId = null} aria-label="Close"><Icon name="x" size={14} /></button>
          </div>
          {#if drillData[cell.id]?.length}
            <div class="drill-grid">
              {#each drillData[cell.id] as dcell (dcell.id)}
                <div class="drill-cell">
                  <Cell cell={dcell} data={dcell.data} />
                </div>
              {/each}
            </div>
          {:else if !drilling}
            <div class="drill-empty">No deeper insights found.</div>
          {/if}
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
 .dash { background: #fafaf7; padding: 16px; color: #1a1a1a; font-family: -apple-system, system-ui, sans-serif; }
 h1 { font-size: 14px; font-weight: 700; margin: 0 0 12px; }
 .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
 .chip { background: #fff; border: 1px solid #e0e0d8; border-radius: var(--pw-radius-sm); padding: 4px 10px; font-size: 11px; color: #666; }
 .chip b { color: #1a1a1a; }
 .insights { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
 .banner { padding: 8px 12px; border-radius: var(--pw-radius-sm); font-size: 11px; }
 .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; grid-auto-rows: minmax(120px, auto); }
 .card { background: transparent; border: none; border-radius: var(--pw-radius-sm); padding: 0; min-width: 0; min-height: 140px; overflow: visible; transition: box-shadow 0.5s, border-color 0.5s; }
 .cell { display: flex; }
 .cell[data-palette='danger'] { border-top: 3px solid #c62828; }
 .cell[data-palette='warning'] { border-top: 3px solid #e65100; }
 .cell[data-palette='good'] { border-top: 3px solid #2e7d32; }
 .cell[data-palette='info'] { border-top: 3px solid #1976d2; }
 .cell[data-palette='neutral'] { border-top: 1px solid #e0e0d8; }
 .cell > :global(*) { width: 100%; }
 .flash-cell { animation: cellflash 0.6s ease-out; }
 .fade-in-panel { animation: panelfadein 400ms ease-out; }
 @keyframes panelfadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
 @keyframes cellflash { 0% { border-color: #2e7d32; box-shadow: 0 0 0 3px rgba(46,125,50,0.35); background: #f1f8e9; } 100% { border-color: #e0e0d8; box-shadow: none; background: #fff; } }
 .tpl-label { float: right; font-size: 11px; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
 .drill-row { background: #f8f8f0; border: 1px dashed #2e7d32; border-radius: var(--pw-radius-sm); padding: 12px; margin: 4px 0; }
 .drill-header { font-size: 11px; color: #666; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
 .drill-close { background: none; border: none; color: #666; cursor: pointer; font-size: 11px; padding: 0 4px; }
 .drill-close:hover { color: #c62828; }
 .drill-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
 .drill-cell { background: #fff; border: 1px solid #e0e0d8; border-radius: var(--pw-radius-sm); min-height: 100px; padding: 6px; display: flex; }
 .drill-cell > :global(*) { width: 100%; }
 .drill-empty { font-size: 11px; color: #999; padding: 8px; text-align: center; }
 @media (max-width: 767px) {
 .cell { grid-column: 1 / -1 !important; grid-row: auto !important; }
 }
 @keyframes panelfadein-new {
   from { opacity: 0; transform: translateY(8px); }
   to { opacity: 1; transform: translateY(0); }
 }
 :global(.cell-panel-new) {
   animation: panelfadein-new 400ms ease-out;
 }
</style>
