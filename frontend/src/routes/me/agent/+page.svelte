<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { base } from '$app/paths';
 import { goto } from '$app/navigation';
 import AgentStatus from '$lib/components/AgentStatus.svelte';
 import AgentMemoryFeed from '$lib/components/AgentMemoryFeed.svelte';
 import AgentRecommendations from '$lib/components/AgentRecommendations.svelte';
 import {
 agentGet,
 agentBootstrap,
 simList,
 type UserAgent,
 type SimRun,
 } from '$lib/api';

 let agent = $state<UserAgent | null>(null);
 let loading = $state(true);
 let bootstrapping = $state(false);
 let error = $state<string | null>(null);
 let sims = $state<SimRun[]>([]);
 let simsLoading = $state(false);
 let simsError = $state<string | null>(null);

 async function loadAgent() {
 loading = true;
 error = null;
 try {
 agent = await agentGet();
 } catch (e) {
 error = e instanceof Error ? e.message : 'Failed to load agent';
 agent = null;
 }
 loading = false;
 }

 async function loadSims() {
 simsLoading = true;
 simsError = null;
 try {
 sims = await simList();
 } catch (e) {
 simsError = e instanceof Error ? e.message : 'Failed to load sims';
 sims = [];
 }
 simsLoading = false;
 }

 async function bootstrap() {
 bootstrapping = true;
 error = null;
 try {
 agent = await agentBootstrap();
 } catch (e) {
 error = e instanceof Error ? e.message : 'Bootstrap failed';
 }
 bootstrapping = false;
 }

 function fmtRel(iso?: string): string {
 if (!iso) return '—';
 const t = new Date(iso).getTime();
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
 }

 function simStatusColor(s: string): string {
 if (s === 'done') return 'var(--pw-success, #2f9a5a)';
 if (s === 'running') return '#d59b3a';
 if (s === 'queued') return '#888';
 if (s === 'failed') return 'var(--pw-error, #c0392b)';
 return '#888';
 }

 onMount(() => {
 loadAgent();
 loadSims();
 });
</script>

<svelte:head>
  <title>My Agent · CityAgent Insights</title>
</svelte:head>

<div class="me-page">
  <!-- Page header -->
  <header class="me-hero">
    <div class="me-hero-inner">
      <div class="me-hero-text">
        <h1 class="me-hero-title"><Icon name="dna" size={14} /> My Agent</h1>
        <p class="me-hero-sub">
          Your personal AI agent with persistent memory across all projects. Learns
          your decisions, mirrors your style, and assists with what-if predictions.
        </p>
      </div>
      <div class="me-hero-actions">
        <button class="me-btn-ghost" onclick={() => { loadAgent(); loadSims(); }} disabled={loading}>
          ↻ Refresh
        </button>
      </div>
    </div>
  </header>

  {#if loading}
    <div class="me-loading">Loading your agent…</div>
  {:else if error && !agent}
    <div class="me-empty">
      <div class="me-empty-icon"><Icon name="alert-triangle" size={14} /></div>
      <h2>Couldn't load your agent</h2>
      <p class="me-muted">{error}</p>
      <button class="me-btn-primary" onclick={loadAgent}>Retry</button>
    </div>
  {:else if !agent}
    <!-- Empty state: not bootstrapped -->
    <div class="me-empty">
      <div class="me-empty-icon"><Icon name="dna" size={14} /></div>
      <h2>Enable My Agent</h2>
      <p class="me-muted">
        Your personal agent learns from every chat across all your projects.
        It remembers your preferences, your phrasing, and the patterns of
        decisions you make — then helps you faster next time.
      </p>
      <ul class="me-empty-list">
        <li><Icon name="check" size={14} /> Cross-project persistent memory</li>
        <li><Icon name="check" size={14} /> Persona that mirrors your analysis style</li>
        <li><Icon name="check" size={14} /> Background what-if simulations</li>
        <li><Icon name="check" size={14} /> Personalized recommendations</li>
      </ul>
      <button class="me-btn-primary me-btn-lg" onclick={bootstrap} disabled={bootstrapping}>
        {bootstrapping ? 'Enabling…' : 'Enable My Agent'}
      </button>
    </div>
  {:else}
    <!-- Main 2-col grid -->
    <div class="me-grid">
      <!-- LEFT: status + persona + actions -->
      <section class="me-col">
        <div class="me-section">
          <h2 class="me-h2">Status</h2>
          <AgentStatus />
        </div>

        <div class="me-section">
          <h2 class="me-h2">Recommendations</h2>
          <AgentRecommendations />
        </div>
      </section>

      <!-- RIGHT: memory + sim history -->
      <section class="me-col">
        <div class="me-section">
          <h2 class="me-h2">Memory feed</h2>
          <p class="me-section-sub">Last 50 events from your agent's lifetime memory.</p>
          <AgentMemoryFeed />
        </div>

        <div class="me-section">
          <div class="me-section-head">
            <h2 class="me-h2">Background simulations</h2>
            <button class="me-btn-ghost-sm" onclick={loadSims} disabled={simsLoading}>↻</button>
          </div>
          <p class="me-section-sub">What-if scenarios your agent has run on your behalf.</p>

          {#if simsLoading}
            <div class="me-muted me-pad">Loading…</div>
          {:else if simsError}
            <div class="me-pad" style="color: var(--pw-error, #c0392b);">{simsError}</div>
          {:else if sims.length === 0}
            <div class="me-muted me-pad">
              No simulations yet. When your agent runs background what-ifs,
              they'll appear here.
            </div>
          {:else}
            <ul class="me-sim-list">
              {#each sims as s}
                <li class="me-sim-row">
                  <span class="me-sim-dot" style="background:{simStatusColor(s.status)};"></span>
                  <div class="me-sim-body">
                    <div class="me-sim-scenario">{s.scenario}</div>
                    <div class="me-sim-meta">
                      <span class="me-sim-status">{s.status}</span>
                      {#if s.horizon}<span>· {s.horizon}</span>{/if}
                      <span>· {fmtRel(s.created_at)}</span>
                      {#if s.status === 'running' && typeof s.progress === 'number'}
                        <span>· {Math.round(s.progress * 100)}%</span>
                      {/if}
                    </div>
                    {#if s.error}
                      <div class="me-sim-err">{s.error}</div>
                    {/if}
                  </div>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </section>
    </div>
  {/if}
</div>

<style>
 .me-page {
 max-width: 1240px;
 margin: 0 auto;
 padding: 24px 28px 80px;
 color: var(--pw-ink, #2c2a26);
 font-family: var(--font-family-display, 'Inter', system-ui, sans-serif);
 }

 .me-hero {
 background: var(--pw-bg-alt, #f5efe4);
 border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
 border-radius: 0;
 padding: 22px 26px;
 margin-bottom: 22px;
 }
 .me-hero-inner {
 display: flex;
 align-items: flex-start;
 justify-content: space-between;
 gap: 16px;
 flex-wrap: wrap;
 }
 .me-hero-text { flex: 1 1 480px; min-width: 0; }
 .me-hero-title {
 font-family: var(--pw-serif, 'Source Serif Pro', Georgia, serif);
 font-size: 20px;
 font-weight: 600;
 color: var(--pw-ink, #2c2a26);
 margin: 0 0 6px 0;
 letter-spacing: -0.01em;
 }
 .me-hero-sub {
 font-size: 11px;
 color: var(--pw-muted, #6b6660);
 margin: 0;
 max-width: 640px;
 line-height: 1.55;
 }
 .me-hero-actions { display: flex; gap: 8px; flex-shrink: 0; }

 .me-loading,
 .me-empty {
 background: var(--pw-surface, #fff);
 border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
 border-radius: 0;
 padding: 56px 32px;
 text-align: center;
 }
 .me-empty-icon {
 font-size: 56px;
 line-height: 1;
 margin-bottom: 14px;
 }
 .me-empty h2 {
 font-family: var(--pw-serif, 'Source Serif Pro', Georgia, serif);
 font-size: 16px;
 font-weight: 600;
 margin: 0 0 10px 0;
 color: var(--pw-ink, #2c2a26);
 }
 .me-empty p {
 max-width: 520px;
 margin: 0 auto 16px;
 line-height: 1.55;
 }
 .me-empty-list {
 list-style: none;
 padding: 0;
 margin: 0 auto 22px;
 max-width: 360px;
 text-align: left;
 font-size: 13.5px;
 color: var(--pw-ink, #2c2a26);
 }
 .me-empty-list li {
 padding: 6px 0;
 border-bottom: 1px dashed var(--pw-border, rgba(0,0,0,0.06));
 }
 .me-empty-list li:last-child { border-bottom: none; }

 .me-grid {
 display: grid;
 grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr);
 gap: 22px;
 align-items: start;
 }
 .me-col {
 display: flex;
 flex-direction: column;
 gap: 18px;
 min-width: 0;
 }

 .me-section {
 background: var(--pw-surface, #fff);
 border: 1px solid var(--pw-border, rgba(0,0,0,0.08));
 border-radius: 0;
 padding: 18px 20px;
 }
 .me-section-head {
 display: flex;
 align-items: baseline;
 justify-content: space-between;
 gap: 12px;
 }
 .me-h2 {
 font-family: var(--pw-serif, 'Source Serif Pro', Georgia, serif);
 font-size: 13px;
 font-weight: 600;
 color: var(--pw-ink, #2c2a26);
 margin: 0 0 10px 0;
 letter-spacing: -0.005em;
 }
 .me-section-sub {
 font-size: 12.5px;
 color: var(--pw-muted, #6b6660);
 margin: -4px 0 12px 0;
 }
 .me-muted { color: var(--pw-muted, #6b6660); font-size: 11px; }
 .me-pad { padding: 16px 4px; }

 .me-btn-primary,
 .me-btn-ghost,
 .me-btn-ghost-sm {
 font-family: inherit;
 font-size: 11px;
 font-weight: 600;
 letter-spacing: 0.04em;
 text-transform: uppercase;
 padding: 8px 14px;
 border-radius: 0;
 cursor: pointer;
 transition: background 120ms ease, border-color 120ms ease;
 }
 .me-btn-primary {
 background: var(--pw-accent, #c96342);
 color: #fff;
 border: 1px solid var(--pw-accent, #c96342);
 }
 .me-btn-primary:hover:not(:disabled) { background: #b35535; border-color: #b35535; }
 .me-btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
 .me-btn-lg { font-size: 11px; padding: 11px 22px; }
 .me-btn-ghost {
 background: var(--pw-bg-alt, #f5efe4);
 color: var(--pw-ink, #2c2a26);
 border: 1px solid var(--pw-border, rgba(0,0,0,0.12));
 }
 .me-btn-ghost:hover:not(:disabled) { background: rgba(201,99,66,0.08); }
 .me-btn-ghost-sm {
 background: transparent;
 color: var(--pw-muted, #6b6660);
 border: 1px solid var(--pw-border, rgba(0,0,0,0.1));
 padding: 4px 10px;
 font-size: 11px;
 }
 .me-btn-ghost-sm:hover:not(:disabled) { background: var(--pw-bg-alt, #f5efe4); color: var(--pw-ink, #2c2a26); }

 .me-sim-list {
 list-style: none;
 padding: 0;
 margin: 0;
 display: flex;
 flex-direction: column;
 gap: 2px;
 }
 .me-sim-row {
 display: flex;
 align-items: flex-start;
 gap: 10px;
 padding: 10px 6px;
 border-bottom: 1px solid var(--pw-border, rgba(0,0,0,0.05));
 }
 .me-sim-row:last-child { border-bottom: none; }
 .me-sim-dot {
 width: 8px;
 height: 8px;
 border-radius: 50%;
 margin-top: 6px;
 flex-shrink: 0;
 }
 .me-sim-body { flex: 1; min-width: 0; }
 .me-sim-scenario {
 font-size: 13.5px;
 color: var(--pw-ink, #2c2a26);
 line-height: 1.4;
 margin-bottom: 3px;
 word-break: break-word;
 }
 .me-sim-meta {
 font-size: 11.5px;
 color: var(--pw-muted, #6b6660);
 display: flex;
 gap: 6px;
 flex-wrap: wrap;
 }
 .me-sim-status { text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
 .me-sim-err {
 margin-top: 4px;
 font-size: 11px;
 color: var(--pw-error, #c0392b);
 font-family: ui-monospace, 'SF Mono', Menlo, monospace;
 }

 @media (max-width: 900px) {
 .me-grid { grid-template-columns: 1fr; }
 .me-page { padding: 18px 16px 60px; }
 .me-hero-title { font-size: 26px; }
 }
</style>
