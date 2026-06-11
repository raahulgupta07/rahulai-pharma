<script lang="ts">
  // Full "Build & Release" card — version, commit, build age, data freshness,
  // and the What's-new feed inline. Used on Admin Overview + Profile.
  import { onMount } from 'svelte';
  import { versionInfo, loadVersion, shortCommit, builtLabel, ageLabel } from '$lib/stores/version';

  let { compact = false }: { compact?: boolean } = $props();

  let v = $derived($versionInfo);
  let sc = $derived(shortCommit(v));
  let bl = $derived(builtLabel(v));
  let al = $derived(ageLabel(v));
  let releases = $derived(v?.changelog ?? []);
  let showAll = $state(false);
  let shown = $derived(showAll ? releases : releases.slice(0, 1));

  onMount(() => { loadVersion(); });
</script>

{#if v}
  <div class="vc" class:vc--compact={compact}>
    <div class="vc-top">
      <div class="vc-id">
        <span class="vc-v">v{v.version}</span>
        {#if sc}<span class="vc-sep">·</span><span class="vc-mono">{sc}</span>{/if}
        {#if bl}<span class="vc-sep">·</span><span class="vc-dim">built {bl}{#if al} ({al}){/if}</span>{/if}
      </div>
      <span class="vc-badge" class:vc-badge--stale={v.stale}>
        <span class="vc-dot" class:vc-dot--stale={v.stale}></span>
        {v.stale ? 'Stale build' : 'Up to date'}
      </span>
    </div>

    {#if v.data}
      <div class="vc-data">
        <span class="vc-data-k">Data</span>
        {#if v.data.last_upload}<span>last upload {v.data.last_upload}</span>{/if}
        {#if v.data.catalog_rows != null}<span class="vc-sep">·</span><span>{v.data.catalog_rows.toLocaleString()} catalog</span>{/if}
        {#if v.data.stock_rows != null}<span class="vc-sep">·</span><span>{v.data.stock_rows.toLocaleString()} stock</span>{/if}
        {#if v.data.shop_flat && (v.data.shop_flat.catalog_only || v.data.shop_flat.stock_only)}
          <span class="vc-sep">·</span>
          <span class="vc-gap">{v.data.shop_flat.catalog_only} cat-only · {v.data.shop_flat.stock_only} stock-only</span>
        {/if}
      </div>
    {/if}

    <div class="vc-wn">
      <div class="vc-wn-head">
        <span class="vc-wn-spark">✦</span><span>What's new</span>
        {#if releases.length > 1}
          <button class="vc-wn-toggle" type="button" onclick={() => (showAll = !showAll)}>
            {showAll ? 'Show less' : `See all (${releases.length})`}
          </button>
        {/if}
      </div>
      {#each shown as rel}
        <div class="vc-rel">
          <div class="vc-relhead">
            <span class="vc-relver">v{rel.version}</span>
            {#if rel.title}<span class="vc-reltitle">{rel.title}</span>{/if}
            {#if rel.date}<span class="vc-reldate">{rel.date}</span>{/if}
          </div>
          <ul class="vc-list">
            {#each rel.items as it}<li>{it}</li>{/each}
          </ul>
        </div>
      {/each}
      {#if !releases.length}<div class="vc-empty">No release notes yet.</div>{/if}
    </div>
  </div>
{/if}

<style>
  .vc {
    background: var(--pw-bg-alt, #faf6f0);
    border: 1px solid var(--pw-line, rgba(0,0,0,.08));
    border-radius: 14px; padding: 16px 18px;
    color: var(--pw-ink, #2a2622);
  }
  .vc--compact { padding: 12px 14px; }
  .vc-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .vc-id { display: flex; align-items: baseline; gap: 7px; flex-wrap: wrap; }
  .vc-v { font-size: 18px; font-weight: 800; color: var(--pw-accent, #9a4a2f); }
  .vc-mono { font-family: ui-monospace, monospace; font-size: 12.5px; color: var(--pw-ink-soft, #5a5550); }
  .vc-sep { opacity: .4; }
  .vc-dim { font-size: 12.5px; color: var(--pw-dim, #9a948c); }
  .vc-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11.5px; font-weight: 650; padding: 4px 10px; border-radius: 999px;
    background: rgba(47,163,107,.12); color: #1f7a4d;
  }
  .vc-badge--stale { background: #fbf0d6; color: #8a5a00; }
  .vc-dot { width: 7px; height: 7px; border-radius: 50%; background: #2fa36b; }
  .vc-dot--stale { background: #d4930e; }
  .vc-data {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
    margin-top: 10px; font-size: 12.5px; color: var(--pw-ink-soft, #5a5550);
  }
  .vc-data-k { font-weight: 700; color: var(--pw-muted, #8a847c); text-transform: uppercase; font-size: 10.5px; letter-spacing: .5px; }
  .vc-gap { color: var(--pw-accent, #9a4a2f); }
  .vc-wn { margin-top: 14px; border-top: 1px solid var(--pw-line, rgba(0,0,0,.08)); padding-top: 12px; }
  .vc-wn-head { display: flex; align-items: center; gap: 8px; font-weight: 650; font-size: 13px; }
  .vc-wn-spark { color: var(--pw-accent, #9a4a2f); }
  .vc-wn-toggle { margin-left: auto; background: none; border: none; cursor: pointer; font: inherit; font-size: 12px; color: var(--pw-accent, #9a4a2f); font-weight: 600; }
  .vc-rel { margin-top: 10px; }
  .vc-relhead { display: flex; align-items: baseline; gap: 8px; }
  .vc-relver { font-weight: 700; color: var(--pw-accent, #9a4a2f); font-size: 12.5px; }
  .vc-reltitle { font-weight: 600; font-size: 12.5px; }
  .vc-reldate { margin-left: auto; font-size: 11px; color: var(--pw-dim, #9a948c); }
  .vc-list { margin: 6px 0 0; padding-left: 20px; color: var(--pw-ink-soft, #5a5550); font-size: 12.5px; line-height: 1.55; }
  .vc-list li { margin: 2px 0; }
  .vc-empty { color: var(--pw-muted, #8a847c); font-size: 12.5px; margin-top: 8px; }
</style>
