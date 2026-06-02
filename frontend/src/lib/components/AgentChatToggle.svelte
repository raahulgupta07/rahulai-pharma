<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 interface Props {
 mode: 'team' | 'agent';
 disabled?: boolean;
 }

 let { mode = $bindable(), disabled = false }: Props = $props();

 function select(next: 'team' | 'agent') {
 if (next === 'agent' && disabled) return;
 mode = next;
 }
</script>

<div class="toggle" role="tablist" aria-label="Chat mode">
  <button
    type="button"
    role="tab"
    aria-selected={mode === 'team'}
    class="seg"
    class:active={mode === 'team'}
    onclick={() => select('team')}
  >
    Team
  </button>
  <button
    type="button"
    role="tab"
    aria-selected={mode === 'agent'}
    class="seg"
    class:active={mode === 'agent'}
    class:disabled
    title={disabled ? 'Enable agent in Settings → My Agent' : undefined}
    onclick={() => select('agent')}
  >
    <span aria-hidden="true"><Icon name="dna" size={14} /></span> My Agent
  </button>
</div>

<style>
 .toggle {
 display: inline-flex;
 align-items: stretch;
 height: 36px;
 width: 200px;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 overflow: hidden;
 background: transparent;
 }
 .seg {
 flex: 1;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 gap: 4px;
 padding: 6px 14px;
 font-size: 11px;
 font-weight: 700;
 font-family: var(--pw-sans);
 color: var(--pw-ink);
 background: transparent;
 border: 0;
 cursor: pointer;
 transition: background 0.15s ease, color 0.15s ease;
 white-space: nowrap;
 }
 .seg:hover:not(.active):not(.disabled) {
 background: var(--pw-accent-wash);
 }
 .seg.active {
 background: var(--pw-accent);
 color: #ffffff;
 }
 .seg.disabled {
 cursor: not-allowed;
 color: var(--pw-dim);
 }
</style>
