<script lang="ts">
  // Compact version chip → opens the shared What's-new modal.
  // variant 'nav'  = pill in the top nav.
  // variant 'footer' = subtle inline link in the app footer.
  import { onMount } from 'svelte';
  import { versionInfo, loadVersion, shortCommit } from '$lib/stores/version';
  import WhatsNew from '$lib/WhatsNew.svelte';

  let { variant = 'nav' }: { variant?: 'nav' | 'footer' } = $props();

  let open = $state(false);
  let v = $derived($versionInfo);
  let sc = $derived(shortCommit(v));

  onMount(() => { loadVersion(); });
</script>

{#if v}
  {#if variant === 'footer'}
    <button class="vb-foot" class:vb-foot--stale={v.stale} type="button"
            onclick={() => (open = true)} title="What's new">
      · v{v.version}{#if v.stale} ⚠{/if} <span class="vb-foot-caret">⌄</span>
    </button>
  {:else}
    <button class="vb-bell" class:vb-bell--stale={v.stale} type="button"
            onclick={() => (open = true)}
            aria-label="What's new"
            title={v.stale ? 'Stale / dev build — rebuild to deploy latest' : `What's new — v${v.version}`}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>
        <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
      </svg>
      <span class="vb-bell-dot" class:vb-bell-dot--stale={v.stale}></span>
    </button>
  {/if}
{/if}

<WhatsNew bind:open />

<style>
  /* nav bell icon (no text) */
  .vb-bell {
    position: relative;
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; border-radius: 9px;
    background: none; border: none; cursor: pointer;
    color: var(--pw-ink-soft, #5a5550);
    transition: background .15s, color .15s;
  }
  .vb-bell:hover { background: var(--pw-accent-bg, #f3ece1); color: var(--pw-accent, #9a4a2f); }
  .vb-bell-dot {
    position: absolute; top: 6px; right: 7px;
    width: 7px; height: 7px; border-radius: 50%;
    background: #2fa36b; box-shadow: 0 0 0 2px var(--pw-bg, #fff);
  }
  .vb-bell-dot--stale { background: #d4930e; }
  .vb-bell--stale { color: #8a5a00; }

  /* footer inline link */
  .vb-foot {
    background: none; border: none; cursor: pointer;
    color: var(--pw-muted, #8a847c); font: inherit; font-size: inherit;
    padding: 0 0 0 2px; display: inline-flex; align-items: center; gap: 3px;
  }
  .vb-foot:hover { color: var(--pw-accent, #9a4a2f); }
  .vb-foot--stale { color: #8a5a00; font-weight: 600; }
  .vb-foot-caret { font-size: 11px; opacity: .7; }
</style>
