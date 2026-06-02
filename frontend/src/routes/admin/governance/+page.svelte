<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import SecretLeaksTab from '$lib/admin/governance/SecretLeaksTab.svelte';
  import HookAuditTab from '$lib/admin/governance/HookAuditTab.svelte';
  import RefusalsTab from '$lib/admin/governance/RefusalsTab.svelte';

  // Human-in-loop (Approvals + HITL Gates) removed 2026-05-20 — over-engineered,
  // no agent produces requests. Governance is now pure audit/guardrail surface.
  type TabKey = 'secret-leaks' | 'hook-audit' | 'refusals';
  let active = $state<TabKey>('secret-leaks');

  onMount(() => {
    const t = page.url.searchParams.get('tab') as TabKey;
    if (t && ['secret-leaks', 'hook-audit', 'refusals'].includes(t)) {
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

  function tabLabel(t: TabKey): string {
    return ({ 'secret-leaks': 'Secret Leak Audit', 'hook-audit': 'Hook Audit', refusals: 'Refusal Log' } as Record<TabKey, string>)[t];
  }
  function tabSubtitle(t: TabKey): string {
    return ({ 'secret-leaks': '14-day pattern + severity audit', 'hook-audit': 'Pre/post hook fires + blocks', refusals: 'Off-topic scope-classifier refusals' } as Record<TabKey, string>)[t];
  }
</script>

<div class="sec-shell">
  <aside class="cc-rail">
    <div class="cc-rail-group">
      <div class="cc-rail-grouplabel">GOVERNANCE</div>
      <button class="cc-rail-btn" class:active={active === 'secret-leaks'} onclick={() => (active = 'secret-leaks')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <span>Secret Leaks</span>
      </button>
      <button class="cc-rail-btn" class:active={active === 'hook-audit'} onclick={() => (active = 'hook-audit')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          <line x1="12" y1="9" x2="12" y2="13"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
        <span>Hook Audit</span>
      </button>
      <button class="cc-rail-btn" class:active={active === 'refusals'} onclick={() => (active = 'refusals')}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
          <circle cx="12" cy="12" r="10"/>
          <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
        </svg>
        <span>Refusal Log</span>
      </button>
    </div>
  </aside>
  <main class="sec-main">
    <header class="sec-head">
      <h1>{tabLabel(active)}</h1>
      <p class="muted">{tabSubtitle(active)}</p>
    </header>
    {#if active === 'secret-leaks'}
      <SecretLeaksTab />
    {:else if active === 'hook-audit'}
      <HookAuditTab />
    {:else if active === 'refusals'}
      <RefusalsTab />
    {/if}
  </main>
</div>

