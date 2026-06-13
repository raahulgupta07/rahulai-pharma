<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 // Drop-in card showing one agent run summary. Used in chat message footers.
 interface Props {
 agent?: string;
 mode?: string;
 durationMs?: number;
 cost?: number;
 toolCalls?: string[];
 skillsLoaded?: string[];
 hooksFired?: string[];
 expandable?: boolean;
 }
 let {
 agent = 'Leader', mode = 'AUTO', durationMs = 0, cost = 0,
 toolCalls = [], skillsLoaded = [], hooksFired = [],
 expandable = true,
 }: Props = $props();
 let expanded = $state(false);
</script>

<div class="arc">
  <button class="hdr" onclick={() => expandable && (expanded = !expanded)}>
    <span class="agent"><Icon name="bot" size={14} /> {agent}</span>
    <span class="chip">{mode}</span>
    {#if toolCalls.length}<span class="chip">{toolCalls.length} tools</span>{/if}
    {#if skillsLoaded.length}<span class="chip">{skillsLoaded.length} skills</span>{/if}
    <span class="muted">{durationMs}ms · ${cost.toFixed(3)}</span>
    {#if expandable}<span class="caret">{expanded ? '▾' : '▸'}</span>{/if}
  </button>
  {#if expanded}
    <div class="body">
      {#if toolCalls.length}
        <div class="row"><strong>Tool calls:</strong> {toolCalls.join(' · ')}</div>
      {/if}
      {#if skillsLoaded.length}
        <div class="row"><strong>Skills loaded:</strong> {skillsLoaded.join(' · ')}</div>
      {/if}
      {#if hooksFired.length}
        <div class="row"><strong>Hooks fired:</strong> {hooksFired.join(' · ')}</div>
      {/if}
    </div>
  {/if}
</div>

<style>
 .arc { background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); font: 12px Inter; color: var(--pw-ink, #2c2a26); margin-top: 8px; }
 .hdr { display: flex; gap: 8px; align-items: center; width: 100%; background: none; border: none; padding: 6px 10px; cursor: pointer; text-align: left; }
 .agent { font-weight: 600; }
 .chip { display: inline-block; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); padding: 1px 6px; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin-left: auto; }
 .caret { color: var(--pw-ink-soft, #87837a); margin-left: 6px; }
 .body { padding: 8px 12px; border-top: 1px solid var(--pw-border, #e7e3da); }
 .row { margin: 4px 0; font-size: 11px; }
</style>
