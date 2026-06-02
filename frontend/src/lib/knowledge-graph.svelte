<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  let { tables = [], relationships = [], memories = [], rules = [], triples = [], onNodeClick = (node: any) => {} }: {
    tables: any[];
    relationships: any[];
    memories: any[];
    rules: any[];
    triples: any[];
    onNodeClick?: (node: any) => void;
  } = $props();

  let chartEl: HTMLDivElement;
  let chart: any;
  let showLabels = $state(true);
  let showInferred = $state(true);
  let filterType = $state('all'); // all, table, document, entity, metric

  // Community colors (like PyVis)
  const COMMUNITY_COLORS = [
    '#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6',
    '#1abc9c', '#e67e22', '#34495e', '#e91e63', '#00bcd4',
    '#8bc34a', '#ff5722', '#607d8b', '#795548', '#cddc39',
  ];

  const NODE_COLORS: Record<string, string> = {
    table: '#00fc40',
    document: '#0078d4',
    entity: '#ff9d00',
    metric: '#e74c3c',
    fact: '#9b59b6',
    inferred: '#888',
    memory: '#ff9d00',
    rule: '#00b4d8',
    column: '#555',
  };

  function buildGraph() {
    const nodes: any[] = [];
    const links: any[] = [];
    const nodeSet = new Set<string>();

    // ── Categories for legend ──
    const categories = [
      { name: 'Table', itemStyle: { color: '#00fc40' } },
      { name: 'Document', itemStyle: { color: '#0078d4' } },
      { name: 'Entity', itemStyle: { color: '#ff9d00' } },
      { name: 'Metric', itemStyle: { color: '#e74c3c' } },
      { name: 'Memory', itemStyle: { color: '#ff9d00' } },
      { name: 'Rule', itemStyle: { color: '#00b4d8' } },
      { name: 'Column', itemStyle: { color: '#555' } },
      { name: 'Inferred', itemStyle: { color: '#888' } },
    ];

    const catIndex: Record<string, number> = { table: 0, document: 1, entity: 2, metric: 3, memory: 4, rule: 5, column: 6, inferred: 7 };

    // ── Count connections per node for centrality-based sizing ──
    const connectionCount: Record<string, number> = {};
    function addConnection(id: string) {
      connectionCount[id] = (connectionCount[id] || 0) + 1;
    }

    // ── Table nodes ──
    tables.forEach((t: any) => {
      const id = `table::${t.name}`;
      if (nodeSet.has(id)) return;
      nodeSet.add(id);
      nodes.push({
        id, name: t.name, category: 0,
        symbolSize: Math.max(25, Math.min(55, 18 + Math.sqrt(t.rows || 0) * 4)),
        label: { show: showLabels, fontSize: 10, fontWeight: 'bold', color: '#e0e0e0', formatter: `${t.name}` },
        itemStyle: { color: '#1a1a1a', borderColor: '#00fc40', borderWidth: 2.5 },
        data: { type: 'table', ...t },
      });
    });

    // ── Column nodes (top 5 per table) ──
    tables.forEach((t: any) => {
      const cols = t.inspectData?.columns || [];
      cols.slice(0, 5).forEach((col: any) => {
        const id = `col::${t.name}.${col.name}`;
        if (nodeSet.has(id)) return;
        nodeSet.add(id);
        nodes.push({
          id, name: col.name, category: 6,
          symbolSize: 8,
          label: { show: showLabels, fontSize: 7, color: '#666', position: 'right' },
          itemStyle: { color: '#222', borderColor: '#555', borderWidth: 1 },
          data: { type: 'column', table: t.name, ...col },
        });
        links.push({ source: `table::${t.name}`, target: id, lineStyle: { color: '#333', width: 0.8 } });
      });
    });

    // ── Table relationship links ──
    relationships.forEach((rel: any) => {
      const src = `table::${rel.from_table || rel.table1}`;
      const tgt = `table::${rel.to_table || rel.table2}`;
      if (nodeSet.has(src) && nodeSet.has(tgt)) {
        links.push({
          source: src, target: tgt,
          lineStyle: { color: '#00fc40', width: 2, type: 'dashed' },
          label: { show: showLabels, formatter: rel.from_column ? `${rel.from_column}→${rel.to_column}` : (rel.join_type || ''), fontSize: 7, color: '#666' },
        });
        addConnection(src); addConnection(tgt);
      }
    });

    // ── Knowledge Graph Triples → nodes + links ──
    const filteredTriples = triples.filter(t => showInferred || !t.inferred);
    filteredTriples.forEach((triple: any) => {
      const subj = triple.subject || '';
      const obj = triple.object || '';
      const pred = triple.predicate || '';
      const srcType = triple.source_type || 'entity';
      const community = triple.community ?? 0;
      const isInferred = triple.inferred || false;

      // Determine node types
      const subjType = _guessNodeType(subj, srcType, 'subject');
      const objType = _guessNodeType(obj, srcType, 'object');

      // Filter by type
      if (filterType !== 'all') {
        if (subjType !== filterType && objType !== filterType) return;
      }

      const subjId = `${subjType}::${subj}`;
      const objId = `${objType}::${obj}`;
      const communityColor = COMMUNITY_COLORS[community % COMMUNITY_COLORS.length];

      // Add subject node
      if (!nodeSet.has(subjId)) {
        nodeSet.add(subjId);
        nodes.push({
          id: subjId, name: subj,
          category: catIndex[subjType] ?? 2,
          symbolSize: 20,
          symbol: subjType === 'metric' ? 'diamond' : subjType === 'document' ? 'rect' : 'circle',
          label: { show: showLabels, fontSize: 9, color: '#ddd', position: 'bottom' },
          itemStyle: { color: communityColor, borderColor: NODE_COLORS[subjType] || '#888', borderWidth: 2, opacity: 0.9 },
          data: { type: subjType, name: subj, source_type: srcType, community },
        });
      }
      addConnection(subjId);

      // Add object node
      if (!nodeSet.has(objId)) {
        nodeSet.add(objId);
        nodes.push({
          id: objId, name: obj,
          category: catIndex[objType] ?? 2,
          symbolSize: 16,
          symbol: objType === 'metric' ? 'diamond' : objType === 'document' ? 'rect' : 'circle',
          label: { show: showLabels, fontSize: 8, color: '#bbb', position: 'bottom' },
          itemStyle: { color: communityColor, borderColor: NODE_COLORS[objType] || '#888', borderWidth: 1.5, opacity: 0.85 },
          data: { type: objType, name: obj, source_type: srcType, community },
        });
      }
      addConnection(objId);

      // Add edge
      links.push({
        source: subjId, target: objId,
        lineStyle: {
          color: isInferred ? '#666' : communityColor,
          width: isInferred ? 1 : 1.5,
          type: isInferred ? 'dashed' : 'solid',
          opacity: isInferred ? 0.5 : 0.7,
        },
        label: { show: showLabels && !isInferred, formatter: pred, fontSize: 7, color: '#777' },
      });
    });

    // ── Memory nodes ──
    memories.slice(0, 5).forEach((m: any, i: number) => {
      const id = `mem::${i}`;
      if (nodeSet.has(id)) return;
      nodeSet.add(id);
      nodes.push({
        id, name: (m.fact || '').slice(0, 25) + '...',
        category: 4, symbolSize: 14, symbol: 'rect',
        label: { show: showLabels, fontSize: 7, color: '#ff9d00', position: 'bottom', width: 80, overflow: 'truncate' },
        itemStyle: { color: '#1a1a1a', borderColor: '#ff9d00', borderWidth: 1.5 },
        data: { type: 'memory', ...m },
      });
      if (tables.length > 0) {
        links.push({ source: id, target: `table::${tables[i % tables.length].name}`, lineStyle: { color: '#ff9d00', width: 0.5, type: 'dotted', opacity: 0.3 } });
      }
    });

    // ── Rule nodes ──
    rules.slice(0, 4).forEach((r: any, i: number) => {
      const id = `rule::${i}`;
      if (nodeSet.has(id)) return;
      nodeSet.add(id);
      nodes.push({
        id, name: (r.name || '').slice(0, 22) + '...',
        category: 5, symbolSize: 13, symbol: 'diamond',
        label: { show: showLabels, fontSize: 7, color: '#00b4d8', position: 'bottom', width: 80, overflow: 'truncate' },
        itemStyle: { color: '#1a1a1a', borderColor: '#00b4d8', borderWidth: 1.5 },
        data: { type: 'rule', ...r },
      });
      if (tables.length > 0) {
        links.push({ source: id, target: `table::${tables[i % tables.length].name}`, lineStyle: { color: '#00b4d8', width: 0.5, type: 'dotted', opacity: 0.2 } });
      }
    });

    // ── Apply centrality-based sizing ──
    nodes.forEach(n => {
      const conns = connectionCount[n.id] || 0;
      if (conns > 2) {
        n.symbolSize = Math.min(65, (n.symbolSize || 16) + conns * 3);
      }
    });

    return { nodes, links, categories };
  }

  function _guessNodeType(name: string, sourceType: string, role: string): string {
    const lower = (name || '').toLowerCase();
    // Check if it matches a known table
    if (tables.some((t: any) => t.name === name || t.name === lower)) return 'table';
    // Numbers/percentages → metric
    if (/^\d+[\d,.%MBK]*$/.test(name.trim()) || /\d+%/.test(name)) return 'metric';
    // Document references
    if (/slide|page|pdf|pptx|docx|\.xlsx|\.csv/.test(lower)) return 'document';
    if (sourceType === 'document') return role === 'subject' ? 'entity' : 'entity';
    if (sourceType === 'table') return role === 'object' ? 'entity' : 'table';
    if (sourceType === 'fact') return 'metric';
    return 'entity';
  }

  function renderChart() {
    if (!chartEl || !chart) return;
    const { nodes, links, categories } = buildGraph();

    chart.setOption({
      backgroundColor: '#1a1a1a',
      tooltip: {
        trigger: 'item',
        backgroundColor: '#2a2a2a',
        borderColor: '#444',
        borderWidth: 1,
        textStyle: { color: '#e0e0e0', fontFamily: 'Space Grotesk, monospace', fontSize: 10 },
        formatter: (params: any) => {
          const d = params.data?.data;
          if (!d) return params.name;
          if (d.type === 'table') return `<b style="color:#00fc40">TABLE</b><br/><b>${d.name}</b><br/>${d.rows || '?'} rows · ${d.columns || '?'} cols`;
          if (d.type === 'column') return `<b style="color:#555">COLUMN</b><br/>${d.table}.${d.name}<br/>Type: ${d.type}`;
          if (d.type === 'memory') return `<b style="color:#ff9d00">MEMORY</b><br/>${d.fact}`;
          if (d.type === 'rule') return `<b style="color:#00b4d8">RULE</b><br/>${d.name}<br/>${(d.definition || '').slice(0, 80)}`;
          if (d.type === 'entity') return `<b style="color:#ff9d00">ENTITY</b><br/>${d.name}<br/>Source: ${d.source_type || '?'}<br/>Community: ${d.community ?? '?'}`;
          if (d.type === 'metric') return `<b style="color:#e74c3c">METRIC</b><br/>${d.name}<br/>Source: ${d.source_type || '?'}`;
          if (d.type === 'document') return `<b style="color:#0078d4">DOCUMENT</b><br/>${d.name}`;
          return `<b>${d.name || params.name}</b>`;
        }
      },
      legend: {
        data: categories.map(c => c.name),
        right: 10, top: 6,
        textStyle: { color: '#8a8a9a', fontFamily: 'Space Grotesk, monospace', fontSize: 9 },
        itemWidth: 10, itemHeight: 10,
      },
      series: [{
        type: 'graph',
        layout: 'force',
        data: nodes,
        links: links,
        categories: categories,
        roam: true,
        draggable: true,
        force: {
          repulsion: triples.length > 50 ? 200 : 300,
          gravity: triples.length > 50 ? 0.15 : 0.1,
          edgeLength: [60, 180],
          friction: 0.6,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3 },
          itemStyle: { borderWidth: 4 },
        },
        lineStyle: { curveness: 0.15 },
      }],
    }, true);

    chart.on('click', (params: any) => {
      if (params.data?.data) onNodeClick(params.data.data);
    });
  }

  let _onResize: (() => void) | null = null;
  onMount(async () => {
    const echarts = await import('echarts');
    chart = echarts.init(chartEl, undefined, { renderer: 'canvas' });
    renderChart();
    _onResize = () => chart?.resize();
    window.addEventListener('resize', _onResize);
  });

  onDestroy(() => {
    if (_onResize) { try { window.removeEventListener('resize', _onResize); } catch {} _onResize = null; }
    chart?.dispose();
  });

  $effect(() => {
    if ((tables || triples || relationships) && chart) renderChart();
  });
</script>

<!-- Controls bar (like PyVis) -->
<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap;">
  <button style="padding: 3px 10px; font-size: 11px; font-weight: 700; font-family: var(--font-family-display); border: 1px solid {showLabels ? '#00fc40' : '#555'}; background: {showLabels ? '#00fc40' : 'transparent'}; color: {showLabels ? '#1a1a1a' : '#888'}; cursor: pointer;"
    onclick={() => { showLabels = !showLabels; renderChart(); }}>
    {showLabels ? 'HIDE LABELS' : 'SHOW LABELS'}
  </button>
  <button style="padding: 3px 10px; font-size: 11px; font-weight: 700; font-family: var(--font-family-display); border: 1px solid {showInferred ? '#888' : '#555'}; background: {showInferred ? '#444' : 'transparent'}; color: {showInferred ? '#ddd' : '#666'}; cursor: pointer;"
    onclick={() => { showInferred = !showInferred; renderChart(); }}>
    {showInferred ? 'HIDE INFERRED' : 'SHOW INFERRED'}
  </button>
  <!-- Type filter -->
  {#each [['all','ALL'],['table','TABLES'],['entity','ENTITIES'],['document','DOCS'],['metric','METRICS']] as [val, label]}
    <button style="padding: 3px 8px; font-size: 11px; font-weight: 700; font-family: var(--font-family-display); border: 1px solid {filterType === val ? '#fff' : '#444'}; background: {filterType === val ? '#333' : 'transparent'}; color: {filterType === val ? '#fff' : '#666'}; cursor: pointer;"
      onclick={() => { filterType = val; renderChart(); }}>
      {label}
    </button>
  {/each}
  <!-- Edge legend -->
  <span style="margin-left: auto; font-size: 11px; color: #666; display: flex; align-items: center; gap: 8px;">
    <span style="display: flex; align-items: center; gap: 3px;"><span style="width: 16px; height: 2px; background: #aaa; display: inline-block;"></span> Extracted</span>
    <span style="display: flex; align-items: center; gap: 3px;"><span style="width: 16px; height: 2px; background: #666; display: inline-block; border-top: 1px dashed #666;"></span> Inferred</span>
  </span>
</div>

<div bind:this={chartEl} style="width: 100%; height: 550px; border: 2px solid #383832; border-bottom-width: 4px; background: #1a1a1a;"></div>

<!-- Stats bar -->
<div style="display: flex; gap: 16px; padding: 6px 12px; background: #222; border: 1px solid #333; border-top: none; font-size: 11px; color: #888; font-family: var(--font-family-display);">
  <span><b style="color: #00fc40;">{tables.length}</b> tables</span>
  <span><b style="color: #ff9d00;">{new Set(triples.map(t => t.subject)).size + new Set(triples.map(t => t.object)).size}</b> entities</span>
  <span><b style="color: #e74c3c;">{triples.filter(t => !t.inferred).length}</b> extracted</span>
  <span><b style="color: #888;">{triples.filter(t => t.inferred).length}</b> inferred</span>
  <span><b>{new Set(triples.map(t => t.community)).size}</b> communities</span>
  <span><b>{relationships.length}</b> joins</span>
</div>
