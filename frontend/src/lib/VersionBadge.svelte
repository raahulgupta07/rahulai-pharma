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
    <button class="vb-chip" class:vb-chip--stale={v.stale} type="button"
            onclick={() => (open = true)}
            title={v.stale ? 'Stale / dev build — rebuild to deploy latest' : "What's new"}>
      <span class="vb-dot"></span>
      <span class="vb-v">v{v.version}</span>
      {#if v.stale}<span class="vb-warn">⚠</span>{/if}
    </button>
  {/if}
{/if}

<WhatsNew bind:open />

<style>
  /* nav pill */
  .vb-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 10px; border-radius: 999px;
    background: var(--pw-accent-bg, #f3ece1);
    color: var(--pw-accent, #9a4a2f);
    border: 1px solid var(--pw-accent-soft, rgba(154,74,47,.18));
    font-size: 12px; font-weight: 650; letter-spacing: .2px;
    cursor: pointer; white-space: nowrap;
    transition: box-shadow .15s, transform .15s;
  }
  .vb-chip:hover { box-shadow: 0 0 0 3px var(--pw-accent-bg, #f3ece1); transform: translateY(-1px); }
  .vb-dot { width: 7px; height: 7px; border-radius: 50%; background: #2fa36b; box-shadow: 0 0 0 3px rgba(47,163,107,.18); }
  .vb-chip--stale { background: #fbf0d6; color: #8a5a00; border-color: rgba(180,120,0,.28); }
  .vb-chip--stale .vb-dot { background: #d4930e; box-shadow: 0 0 0 3px rgba(212,147,14,.18); }
  .vb-warn { font-weight: 800; }

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
