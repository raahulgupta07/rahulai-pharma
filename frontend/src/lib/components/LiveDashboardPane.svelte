<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import DashRenderer from '$lib/dashboards/DashRenderer.svelte';
  import { dashFetch } from '$lib/api';

  let {
    run_id = '',
    project_slug = '',
    dashboard_id = null,
    events = [],
    status = 'idle',
    workflow_name = '',
  }: {
    run_id?: string;
    project_slug?: string;
    dashboard_id?: string | null;
    events?: any[];
    status?: string;
    workflow_name?: string;
  } = $props();

  let spec = $state<any>({ panels: [], source: 'workflow' });
  let prevPanelIds = $state(new Set<string>());
  let newPanelIds = $state(new Set<string>());
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let lastFetchOk = $state(false);
  let lastEventLen = 0;

  const expected_dashboard_id = $derived(`wfd_${run_id}`);

  function _panelIds(s: any): string[] {
    const panels = s?.panels ?? s?.cells ?? [];
    return panels.map((p: any, i: number) => p.id || p.cell_id || `p_${i}`);
  }

  async function fetchSpec() {
    if (!run_id) return;
    try {
      const did = dashboard_id || expected_dashboard_id;
      const res = await dashFetch(`/api/dashboards/${did}`);
      if (!res.ok) {
        lastFetchOk = false;
        return;
      }
      const data = await res.json();
      const newSpec = data?.spec || data;
      if (!newSpec || typeof newSpec !== 'object') return;

      const ids = _panelIds(newSpec);
      const fresh: string[] = [];
      for (const id of ids) {
        if (!prevPanelIds.has(id)) fresh.push(id);
      }
      if (fresh.length > 0) {
        const next = new Set(newPanelIds);
        for (const id of fresh) next.add(id);
        newPanelIds = next;
        for (const id of fresh) {
          setTimeout(() => {
            const cur = new Set(newPanelIds);
            cur.delete(id);
            newPanelIds = cur;
          }, 1500);
        }
      }
      prevPanelIds = new Set(ids);
      spec = newSpec;
      lastFetchOk = true;
    } catch (e) {
      lastFetchOk = false;
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(fetchSpec, 1500);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  $effect(() => {
    if (status === 'running') {
      startPolling();
    } else {
      if (pollTimer) {
        stopPolling();
        fetchSpec();
      }
    }
  });

  $effect(() => {
    const len = events?.length || 0;
    if (len > lastEventLen) {
      const newEvts = events.slice(lastEventLen);
      lastEventLen = len;
      for (const ev of newEvts) {
        const t = ev?.type || ev?.event;
        if (t === 'panel_ready') {
          fetchSpec();
        } else if (t === 'done') {
          fetchSpec();
          stopPolling();
        }
      }
    }
  });

  onMount(() => {
    fetchSpec();
  });

  onDestroy(() => {
    stopPolling();
  });

  const panelCount = $derived((spec?.panels ?? spec?.cells ?? []).length);
  const specVersion = $derived(spec?.spec_version || spec?.version || 1);
  const isFailed = $derived(status === 'failed' || status === 'error');
  const isRunning = $derived(status === 'running');
  const dotColor = $derived(
    isFailed ? '#c0392b' : isRunning ? '#c96342' : status === 'done' ? '#2e7d32' : '#888'
  );
</script>

<div class="lpane">
  <div class="lpane-header">
    <div class="lpane-title">
      <span class="lpane-emoji">📊</span>
      <span class="lpane-name">{workflow_name || 'Workflow'} · LIVE</span>
    </div>
    <div class="lpane-meta">
      <span class="lpane-pill">v{specVersion}</span>
      <span class="lpane-count">({panelCount} panel{panelCount === 1 ? '' : 's'})</span>
      <span class="lpane-dot" style="background:{dotColor}"></span>
    </div>
  </div>

  <div class="lpane-body">
    {#if isFailed}
      <div class="lpane-fail">workflow failed — partial dashboard saved</div>
      <DashRenderer {spec} {newPanelIds} />
    {:else if panelCount === 0 && isRunning}
      <div class="lpane-skel-wrap">
        <div class="lpane-skel"></div>
        <div class="lpane-skel"></div>
        <div class="lpane-skel"></div>
      </div>
    {:else if panelCount === 0}
      <div class="lpane-empty">waiting for panels…</div>
    {:else}
      <DashRenderer {spec} {newPanelIds} />
    {/if}
  </div>
</div>

<style>
  .lpane {
    background: var(--pw-bg, #fdfaf5);
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .lpane-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 16px;
    border-bottom: 1px solid var(--pw-border, #e2ddd2);
    background: var(--pw-bg-alt, #f5efe2);
    flex-shrink: 0;
  }
  .lpane-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 15px;
    font-weight: 600;
    color: var(--pw-ink, #2c2a26);
  }
  .lpane-emoji {
    font-size: 16px;
  }
  .lpane-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 11px;
    color: var(--pw-muted, #888);
  }
  .lpane-pill {
    padding: 2px 8px;
    background: var(--pw-bg, #fdfaf5);
    border: 1px solid var(--pw-border, #e2ddd2);
    border-radius: var(--pw-radius-sm);
    font-family: ui-monospace, Menlo, monospace;
    font-size: 10px;
    color: var(--pw-ink, #2c2a26);
  }
  .lpane-count {
    font-family: ui-monospace, Menlo, monospace;
  }
  .lpane-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .lpane-body {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }
  .lpane-skel-wrap {
    padding: 16px;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
  }
  .lpane-skel {
    width: 100%;
    min-width: 250px;
    height: 140px;
    background: linear-gradient(90deg, #ece8de 0%, #f5efe2 50%, #ece8de 100%);
    background-size: 200% 100%;
    animation: lpane-shimmer 1.4s ease-in-out infinite;
    border-radius: 4px;
  }
  @keyframes lpane-shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }
  .lpane-fail {
    margin: 12px 16px;
    padding: 10px 14px;
    background: rgba(192, 57, 43, 0.08);
    border-left: 3px solid #c0392b;
    color: #c0392b;
    font-size: 12px;
    font-weight: 600;
  }
  .lpane-empty {
    padding: 24px;
    text-align: center;
    font-size: 11px;
    color: var(--pw-muted, #888);
    font-style: italic;
  }
</style>
