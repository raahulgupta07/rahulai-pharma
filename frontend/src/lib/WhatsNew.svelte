<script lang="ts">
  // Shared "What's new" modal — build info + data freshness + every release.
  // Opened by the nav/footer version chips and the Build & Release card.
  import { versionInfo, shortCommit, builtLabel, ageLabel } from '$lib/stores/version';

  let { open = $bindable(false) }: { open?: boolean } = $props();

  let v = $derived($versionInfo);
  let sc = $derived(shortCommit(v));
  let bl = $derived(builtLabel(v));
  let al = $derived(ageLabel(v));

  function close() { open = false; }
  function onKey(e: KeyboardEvent) { if (e.key === 'Escape') close(); }
</script>

<svelte:window onkeydown={open ? onKey : undefined} />

{#if open && v}
  <div class="wn-backdrop" onclick={close} role="presentation">
    <div class="wn-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="What's new">
      <div class="wn-head">
        <span class="wn-spark">✦</span>
        <span class="wn-htitle">What's new</span>
        <span class="wn-hver" class:wn-hver--stale={v.stale}>v{v.version}</span>
        <button class="wn-x" onclick={close} aria-label="Close">✕</button>
      </div>

      <div class="wn-meta">
        <span class="wn-dot" class:wn-dot--stale={v.stale}></span>
        {#if v.stale}
          <span class="wn-meta-warn">Stale / dev build — rebuild to deploy latest</span>
        {:else}
          <span>Up to date</span>
        {/if}
        {#if bl}<span class="wn-sep">·</span><span>built {bl}{#if al} ({al}){/if}</span>{/if}
        {#if sc}<span class="wn-sep">·</span><span class="wn-mono">{sc}</span>{/if}
      </div>

      {#if v.data}
        <div class="wn-data">
          <span class="wn-data-k">Data</span>
          {#if v.data.last_upload}<span>last upload {v.data.last_upload}</span>{/if}
          {#if v.data.catalog_rows != null}<span class="wn-sep">·</span><span>{v.data.catalog_rows.toLocaleString()} catalog</span>{/if}
          {#if v.data.stock_rows != null}<span class="wn-sep">·</span><span>{v.data.stock_rows.toLocaleString()} stock</span>{/if}
          {#if v.data.shop_flat && (v.data.shop_flat.catalog_only || v.data.shop_flat.stock_only)}
            <span class="wn-sep">·</span>
            <span class="wn-gap" title="catalog-only / orphan-stock mismatches">
              {v.data.shop_flat.catalog_only} cat-only · {v.data.shop_flat.stock_only} stock-only
            </span>
          {/if}
        </div>
      {/if}

      <div class="wn-body">
        {#each (v.changelog ?? []) as rel}
          <div class="wn-rel">
            <div class="wn-relhead">
              <span class="wn-relver">v{rel.version}</span>
              {#if rel.title}<span class="wn-reltitle">{rel.title}</span>{/if}
              {#if rel.date}<span class="wn-reldate">{rel.date}</span>{/if}
            </div>
            <ul class="wn-list">
              {#each rel.items as it}<li>{it}</li>{/each}
            </ul>
          </div>
        {/each}
        {#if !(v.changelog && v.changelog.length)}
          <div class="wn-empty">No release notes yet.</div>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .wn-backdrop {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(20, 16, 12, .42);
    backdrop-filter: blur(2px);
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }
  .wn-modal {
    width: 100%; max-width: 560px; max-height: 80vh;
    display: flex; flex-direction: column;
    background: var(--pw-bg, #fff);
    color: var(--pw-ink, #2a2622);
    border: 1px solid var(--pw-line, rgba(0,0,0,.1));
    border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0,0,0,.28);
    overflow: hidden;
  }
  .wn-head {
    display: flex; align-items: center; gap: 9px;
    padding: 16px 18px; border-bottom: 1px solid var(--pw-line, rgba(0,0,0,.08));
  }
  .wn-spark { color: var(--pw-accent, #9a4a2f); }
  .wn-htitle { font-weight: 700; font-size: 15px; flex: 1; }
  .wn-hver {
    font-weight: 700; font-size: 12px;
    padding: 3px 9px; border-radius: 999px;
    background: var(--pw-accent-bg, #f3ece1); color: var(--pw-accent, #9a4a2f);
  }
  .wn-hver--stale { background: #fbf0d6; color: #8a5a00; }
  .wn-x {
    background: none; border: none; cursor: pointer;
    font-size: 15px; color: var(--pw-muted, #8a847c); padding: 2px 6px; border-radius: 6px;
  }
  .wn-x:hover { background: var(--pw-bg-alt, #f5f1ea); color: var(--pw-ink); }
  .wn-meta, .wn-data {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
    padding: 10px 18px; font-size: 12.5px; color: var(--pw-ink-soft, #5a5550);
  }
  .wn-data { padding-top: 0; }
  .wn-data-k { font-weight: 700; color: var(--pw-muted, #8a847c); text-transform: uppercase; font-size: 10.5px; letter-spacing: .5px; }
  .wn-dot { width: 8px; height: 8px; border-radius: 50%; background: #2fa36b; box-shadow: 0 0 0 3px rgba(47,163,107,.16); }
  .wn-dot--stale { background: #d4930e; box-shadow: 0 0 0 3px rgba(212,147,14,.16); }
  .wn-meta-warn { color: #8a5a00; font-weight: 600; }
  .wn-gap { color: var(--pw-accent, #9a4a2f); }
  .wn-sep { opacity: .4; }
  .wn-mono { font-family: ui-monospace, monospace; }
  .wn-body { padding: 6px 18px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
  .wn-rel { border-top: 1px dashed var(--pw-line, rgba(0,0,0,.08)); padding-top: 12px; }
  .wn-rel:first-child { border-top: none; padding-top: 4px; }
  .wn-relhead { display: flex; align-items: baseline; gap: 8px; }
  .wn-relver { font-weight: 700; color: var(--pw-accent, #9a4a2f); }
  .wn-reltitle { font-weight: 600; }
  .wn-reldate { margin-left: auto; font-size: 11px; color: var(--pw-dim, #9a948c); }
  .wn-list { margin: 8px 0 0; padding-left: 20px; color: var(--pw-ink-soft, #5a5550); font-size: 13px; line-height: 1.6; }
  .wn-list li { margin: 3px 0; }
  .wn-empty { color: var(--pw-muted, #8a847c); font-size: 13px; padding: 8px 0; }
</style>
