<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import CostTab from '$lib/admin/telemetry/CostTab.svelte';
  import RunTimelineTab from '$lib/admin/telemetry/RunTimelineTab.svelte';
  import SkillHeatmapTab from '$lib/admin/telemetry/SkillHeatmapTab.svelte';
  import ToolHealthTab from '$lib/admin/telemetry/ToolHealthTab.svelte';
  import RunAuditTab from '$lib/admin/telemetry/RunAuditTab.svelte';

  type TabKey = 'cost' | 'run-timeline' | 'skill-heatmap' | 'tool-health' | 'run-audit';
  let active = $state<TabKey>('cost');

  onMount(() => {
    const t = page.url.searchParams.get('tab') as TabKey;
    if (t && ['cost', 'run-timeline', 'skill-heatmap', 'tool-health', 'run-audit'].includes(t)) {
      active = t;
    }
  });

  $effect(() => {
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', active);
      window.history.replaceState({}, '', url);
    }
  });

  const railItems: { key: TabKey; label: string }[] = [
    { key: 'cost', label: 'Cost Analytics' },
    { key: 'run-timeline', label: 'Run Timeline' },
    { key: 'skill-heatmap', label: 'Skill Heatmap' },
    { key: 'tool-health', label: 'Tool Health' },
    { key: 'run-audit', label: 'Run Context Audit' }
  ];

  function tabLabel(t: TabKey): string {
    return {
      cost: 'Cost Analytics',
      'run-timeline': 'Run Timeline Browser',
      'skill-heatmap': 'Skill Heatmap',
      'tool-health': 'Tool Health',
      'run-audit': 'Run Context Audit'
    }[t];
  }

  function tabSubtitle(t: TabKey): string {
    return {
      cost: 'LLM spend per project, model, day',
      'run-timeline': 'Drill any run_id across workflows / evals / schedules',
      'skill-heatmap': 'Most-invoked skills last 30 days',
      'tool-health': 'SkillRefinery utility scores per tool per project',
      'run-audit': 'RunContext snapshot for every chat / workflow'
    }[t];
  }
</script>

<div class="sec-shell">
  <aside class="cc-rail">
    <div class="cc-rail-group">
      <div class="cc-rail-grouplabel">TELEMETRY</div>
      {#each railItems as item}
        <button class="cc-rail-btn" class:active={active === item.key} onclick={() => (active = item.key)}>
          {#if item.key === 'cost'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
          {:else if item.key === 'run-timeline'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>
          {:else if item.key === 'skill-heatmap'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          {:else if item.key === 'tool-health'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          {:else if item.key === 'run-audit'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><line x1="12" y1="11" x2="16" y2="11"/><line x1="12" y1="16" x2="16" y2="16"/><line x1="8" y1="11" x2="8.01" y2="11"/><line x1="8" y1="16" x2="8.01" y2="16"/></svg>
          {/if}
          <span>{item.label}</span>
        </button>
      {/each}
    </div>
  </aside>

  <main class="sec-main">
    <header class="sec-header">
      <h1>{tabLabel(active)}</h1>
      <p class="sec-sub">{tabSubtitle(active)}</p>
    </header>

    <div class="sec-content">
      {#if active === 'cost'}
        <CostTab />
      {:else if active === 'run-timeline'}
        <RunTimelineTab />
      {:else if active === 'skill-heatmap'}
        <SkillHeatmapTab />
      {:else if active === 'tool-health'}
        <ToolHealthTab />
      {:else if active === 'run-audit'}
        <RunAuditTab />
      {/if}
    </div>
  </main>
</div>

