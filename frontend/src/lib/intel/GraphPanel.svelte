<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  import { goto } from '$app/navigation';
  import { dashFetch } from '$lib/api';

  let { slug }: { slug: string } = $props();

  let loading = $state(false);
  let err = $state('');

  let nodes = $state<any[]>([]);
  let edges = $state<any[]>([]);
  let orphans = $state<string[]>([]);

  let showTables = $state(true);
  let showMetrics = $state(true);
  let showCharts = $state(true);
  let showChats = $state(true);

  let view = $state<'graph' | 'orphans'>('graph');

  let cy: any = null;
  let cyContainer: HTMLDivElement | null = $state(null);

  let selected = $state<any | null>(null);

  const TYPE_COLORS: Record<string, string> = {
    table: '#4a9eff',
    metric: '#10b981',
    chart: '#f59e0b',
    chat: '#a78bfa',
    unknown: '#6b7280',
  };

  async function loadGraph() {
    if (!slug) return;
    loading = true;
    err = '';
    try {
      const r = await dashFetch(`/api/graph/${slug}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      nodes = data.nodes || [];
      edges = data.edges || [];

      const o = await dashFetch(`/api/graph/${slug}/orphans`);
      if (o.ok) {
        const od = await o.json();
        orphans = od.orphans || [];
      }
      await renderGraph();
    } catch (e: any) {
      err = e?.message || String(e);
    } finally {
      loading = false;
    }
  }

  function filteredElements() {
    const allowed = new Set<string>();
    if (showTables) allowed.add('table');
    if (showMetrics) allowed.add('metric');
    if (showCharts) allowed.add('chart');
    if (showChats) allowed.add('chat');

    const visibleNodes = nodes.filter((n) => allowed.has(n.type));
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = edges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );

    const cyNodes = visibleNodes.map((n) => ({
      data: { id: n.id, label: n.label, type: n.type, orphan: !!n.orphan },
    }));
    const cyEdges = visibleEdges.map((e, i) => ({
      data: { id: `e${i}`, source: e.source, target: e.target, rel: e.rel },
    }));
    return [...cyNodes, ...cyEdges];
  }

  async function renderGraph() {
    if (!cyContainer) return;
    const cytoscape = (await import('cytoscape')).default;
    if (cy) {
      cy.destroy();
      cy = null;
    }
    cy = cytoscape({
      container: cyContainer,
      elements: filteredElements(),
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (ele: any) =>
              TYPE_COLORS[ele.data('type')] || TYPE_COLORS.unknown,
            label: 'data(label)',
            color: '#2c2a26',
            'font-size': '10px',
            'text-outline-width': 2,
            'text-outline-color': '#f7f4ec',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            width: 28,
            height: 28,
            'border-width': 2,
            'border-color': '#e0d8c5',
          },
        },
        {
          selector: 'node[?orphan]',
          style: {
            'border-color': '#ef4444',
            'border-width': 3,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'line-color': '#c4bda9',
            'target-arrow-color': '#c4bda9',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(rel)',
            'font-size': '8px',
            color: '#6b6557',
            'text-rotation': 'autorotate',
            'text-background-color': '#f7f4ec',
            'text-background-opacity': 0.85,
            'text-background-padding': '2px',
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-color': '#c96342',
            'border-width': 3,
          },
        },
      ],
      layout: { name: 'cose', animate: false, padding: 30, idealEdgeLength: 80 },
      wheelSensitivity: 0.2,
    });

    cy.on('tap', 'node', (evt: any) => {
      const id = evt.target.data('id');
      selected = nodes.find((n) => n.id === id) || { id, label: id, type: 'unknown' };
    });
    cy.on('tap', (evt: any) => {
      if (evt.target === cy) selected = null;
    });
  }

  $effect(() => {
    void showTables; void showMetrics; void showCharts; void showChats;
    if (cy) {
      cy.json({ elements: filteredElements() });
      cy.layout({ name: 'cose', animate: false, padding: 30, idealEdgeLength: 80 }).run();
    }
  });

  function openArtifact(n: any) {
    if (!n) return;
    const t = n.type;
    if (t === 'table') goto(`${base}/project/${slug}/settings#datasets`);
    else if (t === 'metric') goto(`${base}/project/${slug}/settings#metrics`);
    else if (t === 'chart') goto(`${base}/project/${slug}/dashboards`);
    else if (t === 'chat') goto(`${base}/project/${slug}`);
    else goto(`${base}/project/${slug}`);
  }

  onMount(() => { loadGraph(); });
</script>

<div class="gp-wrap">
  <header>
    <h2>Project Graph</h2>
    <div class="meta">
      <span>{nodes.length} nodes</span><span>·</span>
      <span>{edges.length} edges</span><span>·</span>
      <span class="orphan-count">{orphans.length} orphan tables</span>
    </div>
  </header>

  <div class="tabs">
    <button class:active={view === 'graph'} onclick={() => (view = 'graph')}>Graph</button>
    <button class:active={view === 'orphans'} onclick={() => (view = 'orphans')}>
      Orphans <span class="badge">{orphans.length}</span>
    </button>
    <button class="reload" onclick={loadGraph} disabled={loading}>
      {loading ? 'Loading…' : '↻ Reload'}
    </button>
  </div>

  {#if err}
    <div class="err">⚠ {err}</div>
  {/if}

  {#if view === 'graph'}
    <div class="filters">
      <label><input type="checkbox" bind:checked={showTables} /> <span class="dot t-table"></span> Tables</label>
      <label><input type="checkbox" bind:checked={showMetrics} /> <span class="dot t-metric"></span> Metrics</label>
      <label><input type="checkbox" bind:checked={showCharts} /> <span class="dot t-chart"></span> Charts</label>
      <label><input type="checkbox" bind:checked={showChats} /> <span class="dot t-chat"></span> Chats</label>
      <span class="legend"><span class="orphan-ring"></span> red border = orphan</span>
    </div>

    <div class="canvas-row">
      <div class="canvas" bind:this={cyContainer}></div>

      {#if selected}
        <aside class="panel">
          <div class="panel-head">
            <span class="badge type-{selected.type}">{selected.type}</span>
            <strong>{selected.label}</strong>
          </div>
          <div class="panel-body">
            <div class="kv"><span>id</span><code>{selected.id}</code></div>
            {#if selected.orphan}
              <div class="warn">⚠ Orphan — zero incoming edges</div>
            {/if}
            <div class="kv">
              <span>outgoing</span>
              <code>{edges.filter((e) => e.source === selected.id).length}</code>
            </div>
            <div class="kv">
              <span>incoming</span>
              <code>{edges.filter((e) => e.target === selected.id).length}</code>
            </div>
          </div>
          <button class="open-btn" onclick={() => openArtifact(selected)}>Open →</button>
        </aside>
      {/if}
    </div>
  {:else}
    <section class="orphan-list">
      {#if orphans.length === 0}
        <div class="empty">No orphan tables. Every table in metadata appears in dash_links.</div>
      {:else}
        <table>
          <thead><tr><th>Table</th><th></th></tr></thead>
          <tbody>
            {#each orphans as name}
              <tr>
                <td><code>{name}</code></td>
                <td><button class="link" onclick={() => goto(`${base}/project/${slug}/settings#datasets`)}>Open →</button></td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  {/if}
</div>

<style>
  .gp-wrap {
    padding: 12px 4px 40px;
    color: var(--pw-ink, #2c2a26);
    font-family: ui-sans-serif, system-ui, sans-serif;
  }
  header { margin-bottom: 10px; }
  h2 { font-size: 18px; margin: 0 0 4px 0; font-weight: 600; font-family: 'Source Serif Pro', Georgia, serif; }
  .meta { font-size: 12px; color: var(--pw-ink-soft, #6b6557); display: flex; gap: 6px; }
  .orphan-count { color: #c0392b; }

  .tabs { display: flex; gap: 6px; margin-bottom: 10px; align-items: center; }
  .tabs button {
    background: var(--pw-surface, #fff); color: var(--pw-ink, #2c2a26);
    border: 1px solid var(--pw-border, #e0d8c5);
    padding: 5px 12px; font-size: 12px; border-radius: 4px; cursor: pointer;
  }
  .tabs button.active { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .tabs button.reload { margin-left: auto; }
  .badge { background: var(--pw-bg-alt, #efeadc); padding: 1px 6px; border-radius: 8px; font-size: 10px; margin-left: 4px; }

  .err {
    background: #fde8e1; border: 1px solid #c0392b; color: #8a2a10;
    padding: 8px 12px; border-radius: 4px; margin-bottom: 8px; font-size: 12px;
  }

  .filters {
    display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
    background: var(--pw-bg-alt, #efeadc); padding: 8px 12px; border-radius: 4px; margin-bottom: 8px;
    font-size: 12px;
  }
  .filters label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .t-table { background: #4a9eff; }
  .t-metric { background: #10b981; }
  .t-chart { background: #f59e0b; }
  .t-chat { background: #a78bfa; }
  .orphan-ring {
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid #ef4444;
  }
  .legend { color: var(--pw-ink-soft, #6b6557); margin-left: auto; }

  .canvas-row { display: flex; gap: 12px; }
  .canvas {
    flex: 1; height: calc(100vh - 280px); min-height: 440px;
    background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 4px;
  }

  aside.panel {
    width: 300px;
    background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 4px;
    padding: 12px; font-size: 12px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .panel-head { display: flex; align-items: center; gap: 8px; }
  .panel-head strong { word-break: break-all; }
  .type-table { background: #e1efff; color: #1e3a5f; }
  .type-metric { background: #d8f3ea; color: #0a6b4f; }
  .type-chart { background: #fbe9c8; color: #80561b; }
  .type-chat { background: #e8def6; color: #4a3175; }
  .type-unknown { background: var(--pw-bg-alt, #efeadc); color: var(--pw-ink-soft, #6b6557); }

  .panel-body { display: flex; flex-direction: column; gap: 6px; }
  .kv { display: flex; justify-content: space-between; gap: 8px; }
  .kv span { color: var(--pw-ink-soft, #6b6557); }
  .kv code { color: var(--pw-ink, #2c2a26); font-size: 11px; word-break: break-all; }
  .warn { color: #c0392b; padding: 6px 8px; background: #fde8e1; border-radius: 4px; }
  .open-btn {
    background: var(--pw-accent, #c96342); color: #fff; border: none; padding: 8px;
    border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  .open-btn:hover { background: #b35535; }

  .orphan-list { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e0d8c5); border-radius: 4px; padding: 12px; }
  .empty { color: var(--pw-ink-soft, #6b6557); font-size: 12px; padding: 16px; text-align: center; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e0d8c5); }
  th { color: var(--pw-ink-soft, #6b6557); font-weight: 500; font-size: 11px; text-transform: uppercase; }
  .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 12px; }
  .link:hover { text-decoration: underline; }
</style>
