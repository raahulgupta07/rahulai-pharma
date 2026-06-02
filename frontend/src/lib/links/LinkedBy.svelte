<!--
  LinkedBy.svelte — Obsidian-style bidirectional links chip row.

  Props:
    type: string  — artifact type (e.g., "chat", "chart", "skill", "table")
    id:   string  — artifact id

  Renders:
    A chip row showing per-type counts: "📊 3 charts · 💬 12 chats · 📈 2 metrics".
    Click a chip → expands inline list of matching artifacts (fetched on demand).

  Styling: teal/violet pills, warm dark theme, mirrors AnswerCard aesthetic.
-->
<script lang="ts">
  type Props = { type: string; id: string };
  let { type, id }: Props = $props();

  type Total = { type: string; count: number };
  type Link = {
    src_type: string; src_id: string;
    dst_type: string; dst_id: string;
    rel: string; project_slug?: string | null;
    created_at?: string;
  };

  let summary: { totals: Total[] } | null = $state(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  // expanded[otherType] -> Link[]
  let expanded: Record<string, Link[] | null> = $state({});
  let expandedLoading: Record<string, boolean> = $state({});

  // type → glyph mapping
  const GLYPH: Record<string, string> = {
    chart: '📊', chat: '💬', metric: '📈', table: '🗂',
    skill: '🧩', dashboard: '📋', deck: '🖼', rule: '📐',
    decision: '⚖', file: '📄', memory: '🧠', column: '🔠',
  };
  const glyph = (t: string) => GLYPH[t] ?? '🔗';
  const label = (t: string, n: number) => {
    const base = t.endsWith('s') ? t : t + 's';
    return `${n} ${n === 1 ? t : base}`;
  };

  async function loadSummary() {
    if (!type || !id) return;
    loading = true;
    error = null;
    try {
      const r = await fetch(
        `/api/links/summary?type=${encodeURIComponent(type)}&id=${encodeURIComponent(id)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      summary = await r.json();
    } catch (e: any) {
      error = e?.message ?? String(e);
      summary = null;
    } finally {
      loading = false;
    }
  }

  async function toggleExpand(otherType: string) {
    if (expanded[otherType]) {
      expanded = { ...expanded, [otherType]: null };
      return;
    }
    expandedLoading = { ...expandedLoading, [otherType]: true };
    try {
      // Fetch both directions; this artifact may be dst (incoming) or src (outgoing)
      const [inc, out] = await Promise.all([
        fetch(
          `/api/links?dst_type=${encodeURIComponent(type)}&dst_id=${encodeURIComponent(id)}` +
            `&src_type=${encodeURIComponent(otherType)}`,
        ).then((r) => (r.ok ? r.json() : { links: [] })),
        fetch(
          `/api/links?src_type=${encodeURIComponent(type)}&src_id=${encodeURIComponent(id)}` +
            `&dst_type=${encodeURIComponent(otherType)}`,
        ).then((r) => (r.ok ? r.json() : { links: [] })),
      ]);
      const merged: Link[] = [...(inc.links ?? []), ...(out.links ?? [])];
      // dedupe by composite key
      const seen = new Set<string>();
      const dedup = merged.filter((l) => {
        const k = `${l.src_type}|${l.src_id}|${l.dst_type}|${l.dst_id}|${l.rel}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      expanded = { ...expanded, [otherType]: dedup };
    } catch {
      expanded = { ...expanded, [otherType]: [] };
    } finally {
      expandedLoading = { ...expandedLoading, [otherType]: false };
    }
  }

  // artifact href guesser — caller can replace as routes evolve
  function hrefFor(l: Link, viewerType: string): string {
    const isSrc = l.src_type === viewerType && l.src_id === id;
    const t = isSrc ? l.dst_type : l.src_type;
    const i = isSrc ? l.dst_id : l.src_id;
    const slug = l.project_slug ?? '';
    const proj = slug ? `/ui/project/${encodeURIComponent(slug)}` : '/ui';
    switch (t) {
      case 'chat':       return `${proj}#chat=${encodeURIComponent(i)}`;
      case 'chart':      return `${proj}#chart=${encodeURIComponent(i)}`;
      case 'dashboard':  return `${proj}/studio/${encodeURIComponent(i)}`;
      case 'skill':      return `${proj}/settings#skill=${encodeURIComponent(i)}`;
      case 'metric':     return `${proj}/settings#metric=${encodeURIComponent(i)}`;
      case 'table':      return `${proj}/settings#table=${encodeURIComponent(i)}`;
      default:           return `${proj}#${encodeURIComponent(t)}=${encodeURIComponent(i)}`;
    }
  }

  function linkLabel(l: Link, viewerType: string): string {
    const isSrc = l.src_type === viewerType && l.src_id === id;
    const otherType = isSrc ? l.dst_type : l.src_type;
    const otherId = isSrc ? l.dst_id : l.src_id;
    return `${glyph(otherType)} ${otherType}:${otherId} · ${l.rel}`;
  }

  $effect(() => {
    // re-fetch when props change
    type; id;
    void loadSummary();
  });
</script>

{#if loading}
  <div class="lb-row lb-muted">linked-by · loading…</div>
{:else if error}
  <div class="lb-row lb-error">links unavailable</div>
{:else if summary && summary.totals && summary.totals.length}
  <div class="lb-wrap">
    <div class="lb-row">
      <span class="lb-label">linked by</span>
      {#each summary.totals as t (t.type)}
        <button
          class="lb-chip"
          class:is-open={!!expanded[t.type]}
          onclick={() => toggleExpand(t.type)}
          title={`Click to expand ${t.count} ${t.type}`}
        >
          <span class="lb-glyph">{glyph(t.type)}</span>
          <span class="lb-count">{t.count}</span>
          <span class="lb-type">{t.type}{t.count === 1 ? '' : 's'}</span>
        </button>
      {/each}
    </div>

    {#each summary.totals as t (t.type)}
      {#if expanded[t.type] !== undefined && expanded[t.type] !== null}
        <div class="lb-expand">
          <div class="lb-expand-head">{glyph(t.type)} {label(t.type, (expanded[t.type] ?? []).length)}</div>
          {#if expandedLoading[t.type]}
            <div class="lb-muted">loading…</div>
          {:else if (expanded[t.type] ?? []).length === 0}
            <div class="lb-muted">no links</div>
          {:else}
            <ul class="lb-list">
              {#each expanded[t.type] ?? [] as l (`${l.src_type}|${l.src_id}|${l.dst_type}|${l.dst_id}|${l.rel}`)}
                <li>
                  <a class="lb-item" href={hrefFor(l, type)}>{linkLabel(l, type)}</a>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
    {/each}
  </div>
{/if}

<style>
  .lb-wrap {
    margin: 8px 0;
    font-family: inherit;
  }
  .lb-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--pw-ink-muted, #7a6f60);
  }
  .lb-label {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 10.5px;
    font-weight: 600;
    opacity: 0.75;
    margin-right: 4px;
  }
  .lb-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid rgba(14, 124, 134, 0.25);
    background: rgba(14, 124, 134, 0.08);
    color: var(--pw-brand-teal, #0e7c86);
    font-size: 11.5px;
    line-height: 1;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s, transform 0.12s;
  }
  .lb-chip:hover {
    background: rgba(14, 124, 134, 0.16);
    border-color: rgba(14, 124, 134, 0.55);
    transform: translateY(-1px);
  }
  .lb-chip.is-open {
    background: rgba(124, 92, 220, 0.14);
    border-color: rgba(124, 92, 220, 0.55);
    color: #7c5cdc;
  }
  .lb-glyph { font-size: 12px; }
  .lb-count { font-weight: 700; }
  .lb-type {
    opacity: 0.85;
    text-transform: lowercase;
  }
  .lb-expand {
    margin-top: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    background: var(--pw-bg-alt, #f6ecda);
    border: 1px solid rgba(0, 0, 0, 0.06);
  }
  .lb-expand-head {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    color: var(--pw-ink-muted, #7a6f60);
    margin-bottom: 6px;
  }
  .lb-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .lb-item {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 6px;
    color: var(--pw-ink, #1f1a14);
    font-size: 12px;
    text-decoration: none;
    background: rgba(255, 255, 255, 0.55);
    border: 1px solid rgba(0, 0, 0, 0.05);
    transition: background 0.12s;
  }
  .lb-item:hover {
    background: rgba(124, 92, 220, 0.10);
    color: #7c5cdc;
  }
  .lb-muted {
    font-size: 11.5px;
    color: var(--pw-ink-muted, #7a6f60);
  }
  .lb-error {
    color: #b04a3a;
  }
</style>
