<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import { markdownToHtml } from '$lib';

  const slug = $derived(page.params.slug || '');

  let pages = $state<any[]>([]);
  let byCat = $state<Record<string, number>>({});
  let total = $state(0);
  let q = $state('');
  let activeCat = $state<string>('');
  let cur = $state<any>(null);
  let loadingPage = $state(false);

  const CAT_COLOR: Record<string, string> = {
    glossary: '#3ec9a7', formula: '#c96342', alias: '#e0a458',
    kpi: '#7c9cff', pattern: '#b06dff', org: '#5fb0d6', entity: '#9aa0b5',
  };
  function catColor(c: string) { return CAT_COLOR[c] || '#9aa0b5'; }

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  async function loadIndex() {
    try {
      const r = await fetch(`/api/projects/${slug}/wiki`, { headers: _h() });
      if (!r.ok) return;
      const d = await r.json();
      pages = d.pages || [];
      byCat = d.by_category || {};
      total = d.total || pages.length;
      if (!cur && pages.length) openPage(pages[0].name);
    } catch {}
  }

  async function openPage(name: string) {
    loadingPage = true;
    try {
      const r = await fetch(`/api/projects/${slug}/wiki/page?name=${encodeURIComponent(name)}`, { headers: _h() });
      if (r.ok) cur = await r.json();
    } catch {}
    loadingPage = false;
  }

  const filtered = $derived(
    pages.filter((p) =>
      (!activeCat || p.category === activeCat) &&
      (!q || p.name.toLowerCase().includes(q.toLowerCase()) || (p.snippet || '').toLowerCase().includes(q.toLowerCase()))
    )
  );

  onMount(loadIndex);
</script>

<div class="wk-root">
  <!-- header -->
  <div class="wk-head">
    <button class="wk-back" onclick={() => goto(`${base}/project/${slug}/overview`)}>← Dashboard</button>
    <div class="wk-title">Brain Wiki</div>
    <span class="wk-sub">{total} concepts · auto-built from brain + knowledge graph</span>
    <div class="wk-spacer"></div>
    <button class="wk-graph" onclick={() => goto(`${base}/project/${slug}/graph`)}>⛶ Graph view</button>
  </div>

  <div class="wk-body">
    <!-- left: concept index -->
    <aside class="wk-rail">
      <input class="wk-search" placeholder="search concepts…" bind:value={q} />
      <div class="wk-cats">
        <button class:on={activeCat === ''} onclick={() => (activeCat = '')}>All</button>
        {#each Object.entries(byCat).sort((a, b) => b[1] - a[1]) as [c, n]}
          <button class:on={activeCat === c} onclick={() => (activeCat = c)}>
            <i style="background:{catColor(c)}"></i>{c} <span>{n}</span>
          </button>
        {/each}
      </div>
      <div class="wk-list">
        {#each filtered as p}
          <button class="wk-item" class:active={cur?.name === p.name} onclick={() => openPage(p.name)}>
            <span class="wk-dot" style="background:{catColor(p.category)}"></span>
            <span class="wk-item-name">{p.name}</span>
            {#if p.links}<span class="wk-links">{p.links}</span>{/if}
          </button>
        {/each}
        {#if !filtered.length}<div class="wk-empty">no match</div>{/if}
      </div>
    </aside>

    <!-- right: reader -->
    <main class="wk-reader">
      {#if loadingPage}
        <div class="wk-loading">loading…</div>
      {:else if cur}
        <div class="wk-page">
          <div class="wk-cat-badge" style="color:{catColor(cur.category)};border-color:{catColor(cur.category)}">{cur.category}</div>
          <h1 class="wk-h1">{cur.name}</h1>
          {#if cur.aliases?.length}
            <div class="wk-aliases">also: {cur.aliases.join(' · ')}</div>
          {/if}

          {#if cur.body}
            <div class="wk-prose">{@html markdownToHtml(cur.body)}</div>
          {:else}
            <div class="wk-noborn">No written definition — this concept exists only as a graph node. Links below.</div>
          {/if}

          {#if cur.links_out?.length}
            <div class="wk-sec-h">Links →</div>
            <div class="wk-links-list">
              {#each cur.links_out as l}
                <div class="wk-link-row">
                  <span class="wk-pred">{l.predicate}</span>
                  {#if l.page}
                    <button class="wk-wikilink" onclick={() => openPage(l.target)}>{l.target}</button>
                  {:else}
                    <span class="wk-deadlink">{l.target}</span>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}

          {#if cur.backlinks?.length}
            <div class="wk-sec-h">⟵ Linked from (backlinks)</div>
            <div class="wk-links-list">
              {#each cur.backlinks as l}
                <div class="wk-link-row">
                  {#if l.page}
                    <button class="wk-wikilink" onclick={() => openPage(l.source)}>{l.source}</button>
                  {:else}
                    <span class="wk-deadlink">{l.source}</span>
                  {/if}
                  <span class="wk-pred">{l.predicate} →</span>
                </div>
              {/each}
            </div>
          {/if}

          {#if cur.siblings?.length}
            <div class="wk-sec-h">Related ({cur.category})</div>
            <div class="wk-sibs">
              {#each cur.siblings as s}
                <button class="wk-sib" onclick={() => openPage(s)}>{s}</button>
              {/each}
            </div>
          {/if}

          <div class="wk-ask">
            <button onclick={() => goto(`${base}/project/${slug}?q=${encodeURIComponent('Tell me about ' + cur.name)}`)}>Ask agent about “{cur.name}” →</button>
          </div>
        </div>
      {:else}
        <div class="wk-loading">select a concept</div>
      {/if}
    </main>
  </div>
</div>

<style>
  .wk-root { position: fixed; inset: 76px 0 0 0; display: flex; flex-direction: column; background: var(--color-surface); }
  .wk-head { display: flex; align-items: center; gap: 12px; padding: 10px 18px; border-bottom: 1px solid var(--pw-border, #e5ddcf); flex-wrap: wrap; }
  .wk-back { background: none; border: 1px solid var(--pw-border, #e5ddcf); color: var(--color-on-surface); font-size: 12px; padding: 5px 10px; cursor: pointer; }
  .wk-title { font-size: 16px; font-weight: 900; color: var(--color-on-surface); }
  .wk-sub { font-size: 11px; color: var(--color-on-surface-dim); }
  .wk-spacer { flex: 1; }
  .wk-graph { background: var(--color-on-surface); color: var(--color-surface); border: none; font-size: 12px; padding: 6px 12px; cursor: pointer; font-weight: 700; }

  .wk-body { flex: 1; display: grid; grid-template-columns: 300px 1fr; overflow: hidden; }
  .wk-rail { border-right: 1px solid var(--pw-border, #e5ddcf); display: flex; flex-direction: column; overflow: hidden; background: var(--color-surface-bright, var(--color-surface)); }
  .wk-search { margin: 12px; padding: 8px 11px; border: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface); color: var(--color-on-surface); font-size: 12px; }
  .wk-cats { display: flex; flex-wrap: wrap; gap: 5px; padding: 0 12px 10px; }
  .wk-cats button { display: inline-flex; align-items: center; gap: 4px; font-size: 10.5px; padding: 3px 8px; border: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface); color: var(--color-on-surface); cursor: pointer; text-transform: capitalize; }
  .wk-cats button.on { background: var(--color-on-surface); color: var(--color-surface); }
  .wk-cats i { width: 7px; height: 7px; border-radius: 50%; }
  .wk-cats span { opacity: 0.6; font-weight: 700; }
  .wk-list { flex: 1; overflow-y: auto; padding: 4px 8px 40px; }
  .wk-item { width: 100%; display: flex; align-items: center; gap: 8px; padding: 7px 9px; background: none; border: none; cursor: pointer; text-align: left; font-size: 12.5px; color: var(--color-on-surface); border-radius: 4px; }
  .wk-item:hover { background: var(--color-surface-dim, rgba(0,0,0,0.04)); }
  .wk-item.active { background: rgba(201,99,66,0.1); font-weight: 700; }
  .wk-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .wk-item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wk-links { font-size: 10px; color: var(--color-on-surface-dim); background: var(--color-surface-dim, rgba(0,0,0,0.05)); padding: 1px 6px; border-radius: 10px; }
  .wk-empty { padding: 20px; text-align: center; color: var(--color-on-surface-dim); font-size: 11px; }

  .wk-reader { overflow-y: auto; padding: 28px 40px 80px; }
  .wk-page { max-width: 720px; }
  .wk-cat-badge { display: inline-block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; border: 1px solid; padding: 2px 8px; font-weight: 700; }
  .wk-h1 { font-size: 28px; font-weight: 900; color: var(--color-on-surface); margin: 10px 0 4px; line-height: 1.15; }
  .wk-aliases { font-size: 12px; color: var(--color-on-surface-dim); font-style: italic; margin-bottom: 16px; }
  .wk-prose { font-size: 14px; line-height: 1.7; color: var(--color-on-surface); margin-bottom: 24px; }
  .wk-prose :global(p) { margin: 0 0 12px; }
  .wk-prose :global(code) { background: var(--color-surface-dim, rgba(0,0,0,0.06)); padding: 1px 5px; font-size: 12.5px; }
  .wk-noborn { font-size: 13px; color: var(--color-on-surface-dim); font-style: italic; margin-bottom: 24px; padding: 12px; border-left: 3px solid var(--pw-border, #e5ddcf); }

  .wk-sec-h { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-on-surface-dim); font-weight: 700; margin: 22px 0 8px; border-bottom: 1px solid var(--pw-border, #e5ddcf); padding-bottom: 4px; }
  .wk-links-list { display: flex; flex-direction: column; gap: 5px; }
  .wk-link-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .wk-pred { font-size: 11px; color: var(--color-on-surface-dim); font-family: ui-monospace, monospace; }
  .wk-wikilink { background: none; border: none; color: var(--color-primary); cursor: pointer; font-size: 13px; padding: 0; text-decoration: underline; text-underline-offset: 2px; }
  .wk-wikilink:hover { font-weight: 700; }
  .wk-deadlink { color: var(--color-on-surface); }

  .wk-sibs { display: flex; flex-wrap: wrap; gap: 6px; }
  .wk-sib { font-size: 11.5px; padding: 4px 10px; border: 1px solid var(--pw-border, #e5ddcf); background: var(--color-surface); color: var(--color-on-surface); cursor: pointer; border-radius: 12px; }
  .wk-sib:hover { border-color: var(--color-primary); color: var(--color-primary); }

  .wk-ask { margin-top: 28px; }
  .wk-ask button { background: var(--color-primary); color: #fff; border: none; font-size: 12px; padding: 8px 16px; cursor: pointer; font-weight: 700; }
  .wk-loading { padding: 60px; text-align: center; color: var(--color-on-surface-dim); }

  @media (max-width: 800px) {
    .wk-body { grid-template-columns: 1fr; }
    .wk-rail { display: none; }
  }
</style>
