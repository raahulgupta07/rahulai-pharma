<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 let skills = $state<any[]>([]);
 let category = $state<string>('all');
 let selected = $state<any>(null);
 let detail = $state<any>(null);
 const token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null);

 async function load() {
 const url = category === 'all' ? '/api/skills' : `/api/skills?category=${category}`;
 const r = await fetch(url, { headers: { Authorization: `Bearer ${token() || ''}` } });
 const j = await r.json();
 skills = j?.skills || [];
 }

 async function open(s: any) {
 selected = s;
 const r = await fetch(`/api/skills/${s.id}`, { headers: { Authorization: `Bearer ${token() || ''}` } });
 detail = await r.json();
 }

 $effect(() => { load(); });
 onMount(load);
</script>

<div class="page">
  <header>
    <div>
      <h1>Skills</h1>
      <p class="muted">10 builtin domain experts · lazy-loaded into agent context.</p>
    </div>
    <select bind:value={category}>
      <option value="all">all categories</option>
      <option value="engineering">engineering</option>
      <option value="analytics">analytics</option>
      <option value="ops">ops</option>
      <option value="vertical">vertical</option>
      <option value="meta">meta</option>
    </select>
  </header>

  <div class="grid">
    {#each skills as s}
      <button class="card" class:active={selected?.id === s.id} onclick={() => open(s)}>
        <div class="hdr">
          <strong>{s.name}</strong>
          <span class="chip">{s.category}</span>
        </div>
        <p class="muted">{s.description}</p>
        <div class="kw">{(s.trigger_keywords || []).slice(0, 3).join(' · ')}</div>
        <div class="stat"><Icon name="bar-chart" size={14} /> {s.invocations_30d || 0} invocations (30d)</div>
      </button>
    {/each}
    {#if !skills.length}<div class="empty">No skills.</div>{/if}
  </div>

  {#if detail}
    <aside class="drawer">
      <button class="x" onclick={() => { detail = null; selected = null; }}><Icon name="x" size={14} /></button>
      <h2>{detail.name}</h2>
      <p class="muted">{detail.description}</p>
      <h3>Trigger Keywords</h3>
      <div class="kws">{(detail.trigger_keywords || []).map((k: string) => `· ${k}`).join('\n')}</div>
      <h3>Instructions</h3>
      <pre class="inst">{detail.instructions}</pre>
    </aside>
  {/if}
</div>

<style>
 .page { padding: 24px 32px 60px; max-width: 1280px; margin: 0 auto; font: 14px Inter; color: var(--pw-ink, #2c2a26); }
 header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; }
 h1 { font: 600 28px 'Source Serif 4', Georgia, serif; margin: 0; color: var(--pw-accent, #c96342); }
 h2 { font: 600 20px 'Source Serif 4', Georgia, serif; margin: 8px 0; }
 h3 { font: 600 12px Inter; text-transform: uppercase; letter-spacing: 0.04em; margin: 16px 0 6px; }
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin: 4px 0 0; }
 select { padding: 6px 10px; border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; background: var(--pw-surface, #faf9f5); font: 12px Inter; }
 .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
 .card { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 16px; text-align: left; cursor: pointer; }
 .card:hover { border-color: var(--pw-accent, #c96342); }
 .card.active { border-color: var(--pw-accent, #c96342); background: rgba(201, 99, 66, 0.04); }
 .hdr { display: flex; justify-content: space-between; align-items: center; }
 .chip { display: inline-block; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; padding: 2px 8px; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
 .kw { color: var(--pw-ink-soft, #87837a); font-size: 11px; margin-top: 6px; font-family: 'JetBrains Mono', monospace; }
 .stat { font-size: 11px; margin-top: 8px; color: var(--pw-ink-soft, #87837a); }
 .empty { text-align: center; padding: 60px; color: var(--pw-ink-soft, #87837a); grid-column: 1/-1; }
 .drawer { position: fixed; top: 56px; right: 0; width: 480px; height: calc(100vh - 56px); background: var(--pw-surface, #faf9f5); border-left: 1px solid var(--pw-border, #e7e3da); padding: 24px; overflow-y: auto; box-shadow: -4px 0 12px rgba(0,0,0,0.06); }
 .x { position: absolute; top: 12px; right: 16px; background: none; border: none; font-size: 13px; cursor: pointer; color: var(--pw-ink-soft, #87837a); }
 .kws { white-space: pre-wrap; font: 11px 'JetBrains Mono', monospace; color: var(--pw-ink-soft, #87837a); }
 pre.inst { background: #1a1614; color: #e7e3da; padding: 16px; border-radius: 0; overflow: auto; font: 11px/1.6 'JetBrains Mono', monospace; white-space: pre-wrap; }
</style>
