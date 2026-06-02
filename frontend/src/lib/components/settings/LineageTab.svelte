<script lang="ts">
  import Icon from '$lib/Icon.svelte';
  import KnowledgeGraph from '$lib/knowledge-graph.svelte';

  let {
    detail = null,
    lineage = null,
    relationships = [],
    kgTriples = [],
    brainMemories = [],
    tableInspectCache = {},
    tableMetaCache = {},
    rules = []
  }: {
    detail?: any;
    lineage?: any;
    relationships?: any[];
    kgTriples?: any[];
    brainMemories?: any[];
    tableInspectCache?: Record<string, any>;
    tableMetaCache?: Record<string, any>;
    rules?: any[];
  } = $props();

  // Tab-local UI state — graphSelectedNode is only used within this tab.
  let graphSelectedNode = $state<any>(null);

  // Derived relationship sets (were {@const} in parent's {:else if} branch;
  // {@const} can't sit at a component root, so promote to $derived).
  const fkRels = $derived(lineage?.relationships || []);
  const aiRels = $derived(relationships);
  const allRelsLineage = $derived([...fkRels, ...aiRels]);
</script>

<div class="flex items-center justify-between mb-4">
  <div>
    <div style="font-size: 16px; font-weight: 900; text-transform: uppercase;">Knowledge Graph</div>
    <div style="font-size: 10px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px;">
      {detail?.tables?.length || 0} tables · {[...(lineage?.relationships || []), ...relationships].length} relationships · {kgTriples.length} triples · {brainMemories.length} memories
    </div>
  </div>
</div>

<!-- Knowledge Graph Visualization (Interactive ECharts) -->
{#if detail?.tables?.length}
  {@const allRels = [...(lineage?.relationships || []), ...relationships]}
  {@const graphTables = (detail.tables || []).map((t: any) => ({ ...t, inspectData: tableInspectCache[t.name] }))}
  <div style="margin-bottom: 20px;">
    <KnowledgeGraph
      tables={graphTables}
      relationships={allRels}
      memories={brainMemories}
      rules={rules}
      triples={kgTriples}
      onNodeClick={(node) => { graphSelectedNode = node; }}
    />
  </div>

  <!-- Node Detail Panel (shows when clicking a node) -->
  {#if graphSelectedNode}
    <div class="ink-border" style="background: var(--pw-surface); padding: 14px; margin-bottom: 16px; position: relative;">
      <button onclick={() => graphSelectedNode = null} style="position: absolute; top: 8px; right: 12px; background: none; border: none; cursor: pointer; font-size: 13px; font-weight: 900; color: var(--pw-ink);"><Icon name="x" size={14} /></button>
      {#if graphSelectedNode.type === 'table'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-accent); margin-bottom: 4px;">TABLE</div>
        <div style="font-size: 14px; font-weight: 900; text-transform: uppercase;">{graphSelectedNode.name}</div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">{graphSelectedNode.rows} rows · {graphSelectedNode.columns} columns</div>
        {#if tableMetaCache[graphSelectedNode.name]?.table_description}
          <div style="font-size: 11px; margin-top: 8px; line-height: 1.5;">{tableMetaCache[graphSelectedNode.name].table_description}</div>
        {/if}
        {#if tableInspectCache[graphSelectedNode.name]?.columns}
          <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-top: 10px; margin-bottom: 4px;">COLUMNS</div>
          <div style="display: flex; flex-wrap: wrap; gap: 4px;">
            {#each tableInspectCache[graphSelectedNode.name].columns as col}
              <span style="font-size: 10px; padding: 2px 8px; background: var(--pw-bg-alt); border: 1px solid var(--pw-ink); font-family: var(--pw-font-body);">{col.name} <span style="color: var(--pw-muted);">({col.type})</span></span>
            {/each}
          </div>
        {/if}
      {:else if graphSelectedNode.type === 'memory'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: #cc7a00; margin-bottom: 4px;">MEMORY</div>
        <div style="font-size: 12px; font-weight: 700;">{graphSelectedNode.fact}</div>
        <div style="font-size: 10px; color: var(--pw-muted); margin-top: 4px;">Scope: {graphSelectedNode.scope || 'project'} · Source: {graphSelectedNode.source || 'user'}</div>
      {:else if graphSelectedNode.type === 'rule'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: #00b4d8; margin-bottom: 4px;">RULE</div>
        <div style="font-size: 12px; font-weight: 700;">{graphSelectedNode.name}</div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">{graphSelectedNode.definition}</div>
      {:else if graphSelectedNode.type === 'column'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-muted); margin-bottom: 4px;">COLUMN</div>
        <div style="font-size: 12px; font-weight: 700;">{graphSelectedNode.table}.{graphSelectedNode.name}</div>
        <div style="font-size: 11px; color: var(--pw-muted); margin-top: 4px;">Type: {graphSelectedNode.type} · Nullable: {graphSelectedNode.nullable ? 'Yes' : 'No'}</div>
      {:else if graphSelectedNode.type === 'entity'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: #cc7a00; margin-bottom: 4px;">ENTITY</div>
        <div style="font-size: 14px; font-weight: 900;">{graphSelectedNode.name}</div>
        <div style="font-size: 10px; color: var(--pw-muted); margin-top: 4px;">Source: {graphSelectedNode.source_type} · Community: {graphSelectedNode.community ?? '?'}</div>
        {#if kgTriples.length > 0}
          <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; margin-top: 8px; margin-bottom: 4px;">RELATIONSHIPS</div>
          {#each kgTriples.filter(t => t.subject === graphSelectedNode.name || t.object === graphSelectedNode.name).slice(0, 10) as t}
            <div style="font-size: 10px; margin-bottom: 2px;">
              <span style="font-weight: 700;">{t.subject}</span>
              <span style="color: var(--pw-muted); font-style: italic;"> {t.predicate} </span>
              <span style="font-weight: 700;">{t.object}</span>
              <span style="font-size: 11px; color: var(--pw-muted); margin-left: 4px;">[{t.source_type}]</span>
            </div>
          {/each}
        {/if}
      {:else if graphSelectedNode.type === 'metric'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: var(--pw-error); margin-bottom: 4px;">METRIC</div>
        <div style="font-size: 14px; font-weight: 900;">{graphSelectedNode.name}</div>
        <div style="font-size: 10px; color: var(--pw-muted); margin-top: 4px;">Source: {graphSelectedNode.source_type}</div>
      {:else if graphSelectedNode.type === 'document'}
        <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.1em; color: #0078d4; margin-bottom: 4px;">DOCUMENT</div>
        <div style="font-size: 14px; font-weight: 900;">{graphSelectedNode.name}</div>
        <div style="font-size: 10px; color: var(--pw-muted); margin-top: 4px;">Source: {graphSelectedNode.source_type}</div>
      {/if}
    </div>
  {/if}
{/if}

<!-- CLI Terminal Header -->
<div style="font-size: 12px; font-weight: 900; text-transform: uppercase; margin-bottom: 8px;">Relationships</div>
<div class="cli-terminal" style="margin-bottom: 16px; padding: 10px 14px;">
  <div class="cli-line">
    <span class="cli-prompt">$</span>
    <span class="cli-command">dash lineage</span>
    <span class="cli-output">--discover</span>
  </div>
  <div class="cli-line">
    <span style="color: var(--pw-muted);">&gt;</span>
    <span class="cli-dim">{fkRels.length} FK relationships (SQL introspection)</span>
  </div>
  <div class="cli-line">
    <span style="color: var(--pw-muted);">&gt;</span>
    <span class="cli-dim">{aiRels.length} AI-discovered relationships</span>
  </div>
  <div class="cli-line">
    <span style="color: var(--pw-muted);">&gt;</span>
    <span class="cli-check">&#10003;</span>
    <span class="cli-command">{allRelsLineage.length} total connections</span>
  </div>
</div>

<!-- Discovered Relationships Table -->
{#if allRelsLineage.length > 0}
  <div style="font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px;">DISCOVERED RELATIONSHIPS ({allRelsLineage.length})</div>
  <div class="ink-border" style="overflow-x: auto; margin-bottom: 16px;">
    <table style="width: 100%; border-collapse: collapse; font-size: 11px; font-family: var(--pw-font-body);">
      <thead>
        <tr style="border-bottom: 2px solid var(--pw-ink); text-transform: uppercase; font-size: 11px; font-weight: 900; letter-spacing: 0.1em;">
          <th style="text-align: left; padding: 8px 12px;">FROM</th>
          <th style="text-align: left; padding: 8px 12px;">TO</th>
          <th style="text-align: left; padding: 8px 12px;">TYPE</th>
          <th style="text-align: left; padding: 8px 12px;">CONFIDENCE</th>
          <th style="text-align: left; padding: 8px 12px;">SOURCE</th>
        </tr>
      </thead>
      <tbody>
        {#each fkRels as rel}
          <tr style="border-bottom: 1px solid var(--pw-bg-alt);">
            <td style="padding: 6px 12px; font-weight: 700;">{rel.from_table}{rel.from_column ? '.' + rel.from_column : ''}</td>
            <td style="padding: 6px 12px; font-weight: 700;">{rel.to_table}{rel.to_column ? '.' + rel.to_column : ''}</td>
            <td style="padding: 6px 12px; color: var(--pw-muted);">FK</td>
            <td style="padding: 6px 12px; color: var(--pw-muted);">{rel.confidence ? Math.round((rel.confidence || 0) * 100) + '%' : '100%'}</td>
            <td style="padding: 6px 12px;"><span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: var(--pw-bg-alt); border: 1px solid var(--pw-ink);">SQL</span></td>
          </tr>
        {/each}
        {#each aiRels as rel}
          <tr style="border-bottom: 1px solid var(--pw-bg-alt);">
            <td style="padding: 6px 12px; font-weight: 700;">{rel.table1 || rel.from_table}</td>
            <td style="padding: 6px 12px; font-weight: 700;">{rel.table2 || rel.to_table}</td>
            <td style="padding: 6px 12px; color: var(--pw-muted);">{rel.join_type || rel.relationship || 'topic'}</td>
            <td style="padding: 6px 12px; color: var(--pw-muted);">{rel.confidence ? rel.confidence + '%' : '—'}</td>
            <td style="padding: 6px 12px;"><span style="font-size: 11px; font-weight: 900; padding: 1px 6px; background: var(--pw-accent); color: var(--pw-ink); border: 1px solid var(--pw-ink);">AI</span></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<!-- Knowledge Graph Triples (Cross-Source) -->
{#if kgTriples.length > 0}
  <div style="font-size: 12px; font-weight: 900; text-transform: uppercase; margin-top: 20px; margin-bottom: 8px;">Cross-Source Knowledge Graph</div>
  <div class="cli-terminal" style="margin-bottom: 12px; padding: 10px 14px;">
    <div class="cli-line">
      <span class="cli-prompt">$</span>
      <span class="cli-command">dash knowledge-graph</span>
      <span class="cli-output">--triples</span>
    </div>
    <div class="cli-line">
      <span style="color: var(--pw-muted);">&gt;</span>
      <span class="cli-dim">{kgTriples.filter(t => !t.inferred).length} extracted triples</span>
    </div>
    <div class="cli-line">
      <span style="color: var(--pw-muted);">&gt;</span>
      <span class="cli-dim">{kgTriples.filter(t => t.inferred).length} inferred relationships</span>
    </div>
    <div class="cli-line">
      <span style="color: var(--pw-muted);">&gt;</span>
      <span class="cli-dim">{new Set(kgTriples.map(t => t.community)).size} communities detected</span>
    </div>
    <div class="cli-line">
      <span class="cli-check">&#10003;</span>
      <span class="cli-command">{kgTriples.length} total triples across {new Set([...kgTriples.map(t => t.source_type)]).size} source types</span>
    </div>
  </div>

  <!-- Source type filter -->
  {@const sourceTypes = [...new Set(kgTriples.map(t => t.source_type))]}
  <div style="display: flex; gap: 6px; margin-bottom: 10px; flex-wrap: wrap;">
    {#each sourceTypes as st}
      <span style="font-size: 11px; font-weight: 700; padding: 2px 8px; background: {st === 'table' ? '#336791' : st === 'document' ? '#0078d4' : st === 'fact' ? '#cc7a00' : 'var(--pw-dim)'}; color: white; text-transform: uppercase;">
        {st} ({kgTriples.filter(t => t.source_type === st).length})
      </span>
    {/each}
  </div>

  <!-- Triples table -->
  <div class="ink-border" style="overflow-x: auto; margin-bottom: 16px;">
    <table style="width: 100%; border-collapse: collapse; font-size: 11px; font-family: var(--pw-font-body);">
      <thead>
        <tr style="border-bottom: 2px solid var(--pw-ink); text-transform: uppercase; font-size: 11px; font-weight: 900; letter-spacing: 0.1em;">
          <th style="text-align: left; padding: 8px 12px;">SUBJECT</th>
          <th style="text-align: left; padding: 8px 12px;">PREDICATE</th>
          <th style="text-align: left; padding: 8px 12px;">OBJECT</th>
          <th style="text-align: left; padding: 8px 12px;">SOURCE</th>
          <th style="text-align: left; padding: 8px 12px;">CONF</th>
        </tr>
      </thead>
      <tbody>
        {#each kgTriples.slice(0, 50) as triple}
          <tr style="border-bottom: 1px solid var(--pw-bg-alt); opacity: {triple.inferred ? 0.7 : 1};">
            <td style="padding: 6px 12px; font-weight: 700;">{triple.subject}</td>
            <td style="padding: 6px 12px; color: var(--pw-muted); font-style: italic;">{triple.predicate}</td>
            <td style="padding: 6px 12px;">{triple.object}</td>
            <td style="padding: 6px 12px;">
              <span style="font-size: 11px; font-weight: 700; padding: 1px 6px; background: {triple.source_type === 'table' ? '#336791' : triple.source_type === 'document' ? '#0078d4' : triple.source_type === 'fact' ? '#cc7a00' : 'var(--pw-dim)'}; color: white;">{triple.source_type?.toUpperCase()}</span>
              {#if triple.inferred}<span style="font-size: 11px; margin-left: 4px; color: var(--pw-muted);">inferred</span>{/if}
            </td>
            <td style="padding: 6px 12px; font-size: 10px; color: var(--pw-muted);">{triple.confidence ? Math.round(triple.confidence * 100) + '%' : ''}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if kgTriples.length > 50}
      <div style="padding: 8px 12px; font-size: 10px; color: var(--pw-muted);">Showing 50 of {kgTriples.length} triples</div>
    {/if}
  </div>
{/if}
