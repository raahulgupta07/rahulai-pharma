<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import DraftsTab from '$lib/admin/agent-os/DraftsTab.svelte';
  import FleetTab from '$lib/admin/agent-os/FleetTab.svelte';
  import WorkflowRunsTab from '$lib/admin/agent-os/WorkflowRunsTab.svelte';
  import SchedulesTab from '$lib/admin/agent-os/SchedulesTab.svelte';
  import EvalRunsTab from '$lib/admin/agent-os/EvalRunsTab.svelte';

  type TabKey = 'drafts' | 'fleet' | 'workflow-runs' | 'schedules' | 'eval-runs';
  let active = $state<TabKey>('drafts');
  let counts = $state({ drafts: 0, fleet: 0 });

  const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);
  function authHeaders(): Record<string, string> {
    const t = token();
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  async function loadCounts() {
    try {
      const r = await fetch('/api/skill-drafts?status=pending', { headers: authHeaders() });
      if (r.ok) {
        const j = await r.json();
        const list = Array.isArray(j) ? j : j.drafts || j.items || [];
        counts.drafts = list.length;
      }
    } catch {}
    try {
      const r = await fetch('/api/custom-agents', { headers: authHeaders() });
      if (r.ok) {
        const j = await r.json();
        const list = Array.isArray(j) ? j : j.agents || j.items || [];
        counts.fleet = list.length;
      }
    } catch {}
  }

  onMount(() => {
    const t = page.url.searchParams.get('tab') as TabKey;
    if (t && ['drafts', 'fleet', 'workflow-runs', 'schedules', 'eval-runs'].includes(t)) {
      active = t;
    }
    loadCounts();
    const iv = setInterval(loadCounts, 30000);
    return () => clearInterval(iv);
  });

  $effect(() => {
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', active);
      window.history.replaceState({}, '', url);
    }
  });

  function tabLabel(t: TabKey): string {
    return ({ drafts: 'Skill Drafts', fleet: 'Sub-Agent Fleet', 'workflow-runs': 'Workflow Runs', schedules: 'Agent Schedules', 'eval-runs': 'Eval Runs' } as Record<TabKey, string>)[t];
  }
  function tabSubtitle(t: TabKey): string {
    return ({ drafts: 'LLM-proposed skills awaiting review', fleet: 'All custom sub-agents (project + global)', 'workflow-runs': 'Cross-project DAG run history', schedules: 'Recurring agent-callable tasks', 'eval-runs': '4-layer eval framework results' } as Record<TabKey, string>)[t];
  }

  const railItems: { key: TabKey; label: string; badge?: () => number; badgeStyle?: 'accent' | 'gray' }[] = [
    { key: 'drafts', label: 'Drafts', badge: () => counts.drafts, badgeStyle: 'accent' },
    { key: 'fleet', label: 'Sub-Agent Fleet', badge: () => counts.fleet, badgeStyle: 'gray' },
    { key: 'workflow-runs', label: 'Workflow Runs' },
    { key: 'schedules', label: 'Schedules' },
    { key: 'eval-runs', label: 'Eval Runs' }
  ];
</script>

<div class="sec-shell">
  <aside class="cc-rail">
    <div class="cc-rail-group">
      <div class="cc-rail-grouplabel">AGENT OS</div>
      {#each railItems as item}
        <button class="cc-rail-btn" class:active={active === item.key} onclick={() => (active = item.key)}>
          {#if item.key === 'drafts'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>
          {:else if item.key === 'fleet'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
          {:else if item.key === 'workflow-runs'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          {:else if item.key === 'schedules'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          {:else if item.key === 'eval-runs'}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          {/if}
          <span>{item.label}</span>
          {#if item.badge && item.badge() > 0}
            <span class="sec-badge" class:gray={item.badgeStyle === 'gray'}>{item.badge()}</span>
          {/if}
        </button>
      {/each}
    </div>
  </aside>

  <main class="sec-main">
    <header class="sec-main-head">
      <h1>{tabLabel(active)}</h1>
      <p class="sec-main-sub">{tabSubtitle(active)}</p>
    </header>
    <div class="sec-content">
      {#if active === 'drafts'}
        <DraftsTab />
      {:else if active === 'fleet'}
        <FleetTab />
      {:else if active === 'workflow-runs'}
        <WorkflowRunsTab />
      {:else if active === 'schedules'}
        <SchedulesTab />
      {:else if active === 'eval-runs'}
        <EvalRunsTab />
      {/if}
    </div>
  </main>
</div>

