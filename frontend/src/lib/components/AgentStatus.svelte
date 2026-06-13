<script lang="ts">
  import { onMount } from 'svelte';
  import {
    agentGet,
    agentBootstrap,
    agentEnable,
    agentTrain,
    agentDelete,
    type UserAgent
  } from '$lib/api';

  let agent = $state<UserAgent | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state<string | null>(null);

  const dotColor = $derived.by(() => {
    if (!agent) return '#999';
    if (agent.state === 'ready') return 'var(--pw-success, #2f9a5a)';
    if (agent.state === 'building' || agent.state === 'training') return '#d59b3a';
    if (agent.state === 'error') return 'var(--pw-error, #c0392b)';
    return '#999';
  });

  const stateLabel = $derived(agent ? agent.state.toUpperCase() : '');

  const lastSyncRel = $derived.by(() => {
    if (!agent?.last_sync) return '—';
    const t = new Date(agent.last_sync).getTime();
    if (Number.isNaN(t)) return '—';
    const diff = Math.max(0, Date.now() - t);
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  });

  const personaRows = $derived.by(() => {
    const p = agent?.persona || {};
    const pick = (keys: string[]): string => {
      for (const k of keys) {
        const v = (p as Record<string, unknown>)[k];
        if (v != null && String(v).trim()) {
          return Array.isArray(v) ? (v as unknown[]).join(', ') : String(v);
        }
      }
      return '';
    };
    return [
      { label: 'Role', value: pick(['role', 'title']) },
      { label: 'Expertise', value: pick(['expertise', 'skills', 'expertise_areas']) },
      { label: 'Decision style', value: pick(['decision_style', 'decisionStyle', 'style']) },
      { label: 'Risk tolerance', value: pick(['risk_tolerance', 'riskTolerance', 'risk']) },
      { label: 'Vocab', value: pick(['vocab', 'vocabulary', 'tone']) }
    ];
  });

  const personaHasContent = $derived(personaRows.some((r) => r.value));

  export async function refresh() {
    try {
      agent = await agentGet();
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load agent';
    } finally {
      loading = false;
    }
  }

  onMount(refresh);

  async function handleBootstrap() {
    busy = true;
    error = null;
    try {
      agent = await agentBootstrap();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Bootstrap failed';
    } finally {
      busy = false;
    }
  }

  async function handleTrain() {
    if (!agent) return;
    busy = true;
    error = null;
    try {
      await agentTrain();
      await refresh();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Train failed';
    } finally {
      busy = false;
    }
  }

  async function handleToggle() {
    if (!agent) return;
    busy = true;
    error = null;
    try {
      agent = await agentEnable(!agent.enabled);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Toggle failed';
    } finally {
      busy = false;
    }
  }

  async function handleReset() {
    if (!agent) return;
    if (!window.confirm('Reset agent? This deletes all memory and persona.')) return;
    busy = true;
    error = null;
    try {
      await agentDelete();
      agent = null;
    } catch (e) {
      error = e instanceof Error ? e.message : 'Reset failed';
    } finally {
      busy = false;
    }
  }
</script>

<div class="agent-status">
  {#if loading}
    <div class="loading">Loading agent…</div>
  {:else if !agent}
    <div class="empty">
      <div class="empty-title">My Agent</div>
      <div class="empty-sub">Personal AI agent that learns from your work.</div>
      <button
        class="btn btn-primary"
        onclick={handleBootstrap}
        disabled={busy}
      >
        {busy ? 'Enabling…' : 'Enable My Agent'}
      </button>
      {#if error}<div class="error">{error}</div>{/if}
    </div>
  {:else}
    <div class="panel">
      <div class="head">
        <div class="pill">
          <span class="dot" style="background:{dotColor}"></span>
          <span class="state">{stateLabel}</span>
          <span class="ver">v{agent.version}</span>
        </div>
        <span class="sync">{lastSyncRel}</span>
      </div>

      <div class="persona">
        <div class="persona-title">Persona</div>
        {#if personaHasContent}
          {#each personaRows as row}
            {#if row.value}
              <div class="prow">
                <span class="plabel">{row.label}</span>
                <span class="pval">{row.value}</span>
              </div>
            {/if}
          {/each}
        {:else}
          <div class="persona-empty">No persona yet. Train to build one.</div>
        {/if}
      </div>

      {#if error}<div class="error">{error}</div>{/if}

      <div class="actions">
        <button class="btn" onclick={handleTrain} disabled={busy}>Train</button>
        <button class="btn" onclick={handleToggle} disabled={busy}>
          {agent.enabled ? 'Disable' : 'Enable'}
        </button>
        <button class="btn btn-danger" onclick={handleReset} disabled={busy}>Reset</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .agent-status {
    width: 400px;
    max-width: 100%;
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border, #e8e6dd);
    border-radius: var(--pw-radius-sm);
    padding: 16px;
    color: var(--pw-ink, #2c2c2c);
    font-size: 11px;
  }
  .loading,
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .empty-title {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 13px;
    color: var(--pw-ink, #2c2c2c);
  }
  .empty-sub {
    font-size: 11px;
    color: var(--pw-muted, #807a72);
    margin-bottom: 4px;
  }
  .panel {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--pw-bg-alt, #f7f6f3);
    border: 1px solid var(--pw-border, #e8e6dd);
    border-radius: var(--pw-radius-sm);
    padding: 4px 10px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .state {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    color: var(--pw-ink, #2c2c2c);
  }
  .ver {
    font-size: 11px;
    color: var(--pw-muted, #807a72);
  }
  .sync {
    font-size: 11px;
    color: var(--pw-muted, #807a72);
  }
  .persona {
    background: var(--pw-bg-alt, #f7f6f3);
    border: 1px solid var(--pw-border, #e8e6dd);
    border-radius: var(--pw-radius-sm);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .persona-title {
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--pw-muted, #807a72);
    margin-bottom: 2px;
  }
  .persona-empty {
    font-size: 11px;
    color: var(--pw-muted, #807a72);
    font-style: italic;
  }
  .prow {
    display: flex;
    gap: 10px;
    font-size: 12.5px;
    line-height: 1.4;
  }
  .plabel {
    flex-shrink: 0;
    width: 110px;
    color: var(--pw-muted, #807a72);
  }
  .pval {
    color: var(--pw-ink, #2c2c2c);
    word-break: break-word;
  }
  .actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .btn {
    appearance: none;
    border: 1px solid var(--pw-border, #e8e6dd);
    background: var(--pw-surface, #fff);
    color: var(--pw-ink, #2c2c2c);
    font: inherit;
    font-size: 11px;
    font-weight: 500;
    padding: 8px 14px;
    border-radius: var(--pw-radius-sm, 8px);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .btn:hover:not(:disabled) {
    background: var(--pw-bg-alt, #f7f6f3);
  }
  .btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .btn-primary {
    background: var(--pw-accent, #c96342);
    border-color: var(--pw-accent, #c96342);
    color: #fff;
  }
  .btn-primary:hover:not(:disabled) {
    background: var(--pw-accent, #b8593a);
    filter: brightness(0.95);
  }
  .btn-danger {
    color: var(--pw-error, #c0392b);
    border-color: var(--pw-border, #e8e6dd);
  }
  .btn-danger:hover:not(:disabled) {
    background: rgba(192, 57, 43, 0.06);
    border-color: var(--pw-error, #c0392b);
  }
  .error {
    font-size: 11px;
    color: var(--pw-error, #c0392b);
    background: rgba(192, 57, 43, 0.06);
    border: 1px solid rgba(192, 57, 43, 0.2);
    border-radius: var(--pw-radius-sm);
    padding: 6px 10px;
  }
</style>
