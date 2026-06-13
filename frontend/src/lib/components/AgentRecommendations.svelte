<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { page } from '$app/state';
 import { agentRecommendations } from '$lib/api';

 let recs = $state<string[]>([]);
 let loading = $state(true);
 let notEnabled = $state(false);
 let error = $state(false);

 const slug = $derived(page.params.slug || '');
 const settingsUrl = $derived(slug ? `/project/${slug}/settings?tab=myagent` : '/settings');

 async function load() {
 loading = true;
 notEnabled = false;
 error = false;
 try {
 const data = await agentRecommendations();
 recs = Array.isArray(data) ? data : [];
 } catch (e: any) {
 const msg = String(e?.message || e);
 if (msg.includes('404')) notEnabled = true;
 else error = true;
 recs = [];
 } finally {
 loading = false;
 }
 }

 onMount(load);
</script>

<div class="ar-card">
  <div class="ar-head">
    <span class="ar-title"><Icon name="dna" size={14} /> My Agent suggests</span>
    <button class="ar-refresh" onclick={load} title="Refresh" aria-label="Refresh recommendations" disabled={loading}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
    </button>
  </div>
  <div class="ar-body">
    {#if loading}
      <div class="ar-muted">Loading…</div>
    {:else if notEnabled}
      <div class="ar-muted">My Agent is not enabled for this project.</div>
      <a class="ar-link" href={settingsUrl}>Enable My Agent →</a>
    {:else if error}
      <div class="ar-muted">Could not load suggestions.</div>
    {:else if recs.length === 0}
      <div class="ar-muted">Use Dash for a few days — your agent will surface suggestions here.</div>
    {:else}
      {#each recs as r, i}
        <div class="ar-row">
          <span class="ar-num">{i + 1}</span>
          <span class="ar-text">{r}</span>
          <span class="ar-arrow">→</span>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
 .ar-card {
 background: var(--pw-bg-alt, #faf6f1);
 border: 1px solid var(--pw-border, #e5ddd2);
 border-radius: var(--pw-radius-sm);
 box-shadow: 0 1px 3px rgba(0,0,0,0.06);
 width: 100%;
 min-height: 120px;
 display: flex;
 flex-direction: column;
 font-family: var(--pw-font-body, system-ui, sans-serif);
 }
 .ar-head {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 8px 12px;
 border-bottom: 1px solid var(--pw-border, #e5ddd2);
 }
 .ar-title {
 font-size: 11px;
 font-weight: 700;
 color: var(--pw-ink, #2c2a26);
 }
 .ar-refresh {
 background: none;
 border: none;
 cursor: pointer;
 color: var(--pw-muted, #888);
 padding: 2px;
 display: flex;
 align-items: center;
 }
 .ar-refresh:hover { color: var(--pw-accent, #c96342); }
 .ar-refresh:disabled { opacity: 0.5; cursor: default; }
 .ar-body {
 padding: 8px 12px;
 display: flex;
 flex-direction: column;
 gap: 4px;
 flex: 1;
 }
 .ar-row {
 display: flex;
 align-items: center;
 gap: 8px;
 font-size: 11px;
 color: var(--pw-ink, #2c2a26);
 padding: 4px 2px;
 border-radius: var(--pw-radius-sm);
 }
 .ar-row:hover { background: rgba(201,99,66,0.05); }
 .ar-row:hover .ar-arrow { opacity: 1; }
 .ar-num {
 flex-shrink: 0;
 width: 18px;
 height: 18px;
 border-radius: 50%;
 background: var(--pw-accent, #c96342);
 color: #fff;
 font-size: 10px;
 font-weight: 700;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 }
 .ar-text { flex: 1; line-height: 1.4; }
 .ar-arrow {
 color: var(--pw-accent, #c96342);
 font-weight: 700;
 opacity: 0;
 transition: opacity 0.15s;
 }
 .ar-muted {
 font-size: 11.5px;
 color: var(--pw-muted, #888);
 line-height: 1.4;
 }
 .ar-link {
 margin-top: 6px;
 font-size: 11.5px;
 color: var(--pw-accent, #c96342);
 font-weight: 600;
 text-decoration: none;
 }
 .ar-link:hover { text-decoration: underline; }
</style>
