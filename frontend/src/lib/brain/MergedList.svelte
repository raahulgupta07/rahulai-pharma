<script lang="ts">
  type ItemStatus = 'synced' | 'conflict' | 'agent_only' | 'company_only';

  interface BrainItem {
    category: string;
    name: string;
    key: string;
    agent_value: string | null;
    company_value: string | null;
    agent_id: string | number | null;
    company_id: string | number | null;
    status: ItemStatus;
    meta: Record<string, unknown>;
  }

  type StatusFilter = 'all' | ItemStatus;

  let {
    items = [] as BrainItem[],
    loading = false,
    statusFilter = 'all' as StatusFilter,
    query = '',
    onAction = (_action: string, _item: BrainItem) => {}
  } = $props();

  let expandedKey = $state<string | null>(null);

  // single-tenant: merged value = whichever side has content
  function mergedValue(i: BrainItem): string | null {
    return i.company_value ?? i.agent_value ?? null;
  }

  // Burmese twin (folded into the row by the backend) — read from meta.
  function burmeseValue(i: BrainItem): string | null {
    const m = (i.meta || {}) as Record<string, unknown>;
    const v = m.definition_my ?? m.question_my ?? m.fact_my ?? null;
    const s = v == null ? '' : String(v).trim();
    return s ? s : null;
  }

  // one-line preview, whitespace-collapsed, truncated
  function preview(i: BrainItem): string {
    const v = mergedValue(i);
    if (!v) return '';
    return v.replace(/\s+/g, ' ').trim().slice(0, 120);
  }

  // type-colour dot keyed by brain category
  function dotColor(cat: string): string {
    switch ((cat || '').toLowerCase()) {
      case 'definitions': case 'formula': return '#c96342'; // coral — formulas/concepts
      case 'glossary':                     return '#3a7563'; // green — terms
      case 'patterns':                     return '#b8860b'; // gold — proven Q&A
      case 'rules':                        return '#5b6fb5'; // blue — rules
      case 'graph':                        return '#8a6db5'; // purple — triples
      case 'schema':                       return '#6b6557'; // slate — tables
      default:                             return '#6b6557';
    }
  }

  const filtered = $derived(
    items.filter((i) => {
      if (statusFilter !== 'all' && i.status !== statusFilter) return false;
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return (
        i.name.toLowerCase().includes(q) ||
        (mergedValue(i) || '').toLowerCase().includes(q)
      );
    })
  );

  function toggleExpand(key: string) {
    expandedKey = expandedKey === key ? null : key;
  }
</script>

<div class="mgl-root">
  {#if loading}
    <div class="mgl-skeletons">
      {#each [1, 2, 3] as _}
        <div class="mgl-skeleton-row">
          <div class="mgl-skeleton mgl-sk-badge"></div>
          <div class="mgl-skeleton mgl-sk-text"></div>
          <div class="mgl-skeleton mgl-sk-chevron"></div>
        </div>
      {/each}
    </div>
  {:else if filtered.length === 0}
    <div class="mgl-empty">No entries.</div>
  {:else}
    <div class="mgl-list">
      {#each filtered as item (item.key)}
        {@const isExpanded = expandedKey === item.key}
        {@const prev = preview(item)}
        <div class="mgl-item">
          <button
            class="mgl-row"
            onclick={() => toggleExpand(item.key)}
            type="button"
            aria-expanded={isExpanded}
          >
            <span class="mgl-dot" style={`background:${dotColor(item.category)}`}></span>
            <span class="mgl-body">
              <span class="mgl-name">{item.name}</span>
              {#if prev}<span class="mgl-prev">{prev}</span>{/if}
            </span>
            <span class="mgl-chevron">{isExpanded ? '▾' : '▸'}</span>
          </button>

          {#if isExpanded}
            <div class="mgl-panel">
              {#if mergedValue(item) != null}
                {@const my = burmeseValue(item)}
                {#if my}
                  <div class="mgl-bi-line"><span class="mgl-bi-badge">1</span><pre class="mgl-pre">{mergedValue(item)}</pre></div>
                  <div class="mgl-bi-line" style="margin-top:8px;"><span class="mgl-bi-badge">2</span><pre class="mgl-pre mgl-pre-my" lang="my">{my}</pre></div>
                {:else}
                  <pre class="mgl-pre">{mergedValue(item)}</pre>
                {/if}
              {:else}
                <span class="mgl-null">— no value —</span>
              {/if}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .mgl-root {
    font-family: inherit;
    color: #2c2a26;
    background: #f7f4ec;
    border-radius: 0;
  }

  /* ── Skeletons ─────────────────────────────────────────────── */
  .mgl-skeletons {
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  .mgl-skeleton-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid #e3ddd0;
  }

  .mgl-skeleton {
    background: #efeadd;
    border-radius: 0;
    animation: mgl-pulse 1.4s ease-in-out infinite;
  }

  @keyframes mgl-pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.45; }
  }

  .mgl-sk-badge   { width: 72px; height: 18px; flex-shrink: 0; }
  .mgl-sk-text    { flex: 1; height: 14px; }
  .mgl-sk-chevron { width: 12px; height: 14px; flex-shrink: 0; }

  /* ── Empty state ───────────────────────────────────────────── */
  .mgl-empty {
    padding: 24px 12px;
    color: #6b6557;
    font-size: 13px;
    text-align: center;
  }

  /* ── List & Item ───────────────────────────────────────────── */
  .mgl-list {
    display: flex;
    flex-direction: column;
  }

  .mgl-item {
    border-bottom: 1px solid #e3ddd0;
  }

  .mgl-item:last-child {
    border-bottom: none;
  }

  /* ── Row (collapsed header) ────────────────────────────────── */
  .mgl-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
    padding: 9px 12px;
    background: transparent;
    border: none;
    border-radius: 0;
    cursor: pointer;
    text-align: left;
    color: #2c2a26;
    transition: background 0.1s;
  }

  .mgl-row:hover {
    background: #efeadd;
  }

  /* ── Type dot ──────────────────────────────────────────────── */
  .mgl-dot {
    flex-shrink: 0;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-top: 5px;
    align-self: flex-start;
  }

  /* ── Body (name + inline preview) ──────────────────────────── */
  .mgl-body {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .mgl-name {
    font-size: 13px;
    font-weight: 500;
    color: #2c2a26;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mgl-prev {
    font-size: 11px;
    color: #8a8478;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: 'Menlo', 'Consolas', 'Monaco', monospace;
  }

  /* ── Chevron ───────────────────────────────────────────────── */
  .mgl-chevron {
    flex-shrink: 0;
    font-size: 11px;
    color: #6b6557;
    line-height: 1;
  }

  /* ── Expanded Panel ────────────────────────────────────────── */
  .mgl-panel {
    background: #faf8f1;
    padding: 12px;
    border-top: 1px solid #e3ddd0;
  }

  .mgl-conflict-note {
    font-size: 11px;
    color: #c96342;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    margin-bottom: 10px;
  }

  .mgl-values {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  @media (min-width: 640px) {
    .mgl-values {
      flex-direction: row;
    }

    .mgl-value-block {
      flex: 1;
      min-width: 0;
    }
  }

  .mgl-value-block {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .mgl-value-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #6b6557;
    font-weight: 700;
  }

  .mgl-pre {
    margin: 0;
    background: #ffffff;
    border: 1px solid #e3ddd0;
    border-radius: 0;
    padding: 6px 8px;
    font-size: 11px;
    font-family: 'Menlo', 'Consolas', 'Monaco', monospace;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 220px;
    overflow: auto;
    color: #2c2a26;
    line-height: 1.5;
  }

  .mgl-null {
    font-size: 13px;
    color: #6b6557;
    padding: 4px 0;
  }

  /* ── Bilingual 1/2 block ── */
  .mgl-bi-line {
    display: flex;
    align-items: flex-start;
    gap: 6px;
  }
  .mgl-bi-badge {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    margin-top: 4px;
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
    color: #2c2a26;
    background: #efeadd;
    border: 1px solid #e3ddd0;
    border-radius: 50%;
  }
  .mgl-bi-line .mgl-pre {
    flex: 1;
    min-width: 0;
  }
  .mgl-pre-my {
    color: #6b6557;
  }

  /* ── Action Buttons ────────────────────────────────────────── */
  .mgl-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
    align-items: center;
  }

  .mgl-btn {
    display: inline-block;
    padding: 4px 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    border-radius: 0;
    cursor: pointer;
    border: 1px solid transparent;
    transition: opacity 0.1s, background 0.1s;
    white-space: nowrap;
  }

  .mgl-btn:hover {
    opacity: 0.82;
  }

  .mgl-btn--coral {
    background: #c96342;
    color: #ffffff;
    border-color: #c96342;
  }

  .mgl-btn--ink-outline {
    background: transparent;
    color: #2c2a26;
    border-color: #2c2a26;
  }

  .mgl-insync {
    font-size: 12px;
    color: #6b6557;
    font-style: italic;
  }
</style>
