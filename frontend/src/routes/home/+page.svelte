<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { goto } from '$app/navigation';
 import { brand } from '$lib/stores/branding';

 let projects = $state<any[]>([]);
 let username = $state('');
 let isSuper = $state(false);
 let loading = $state(true);
 let bootStep = $state(0);
 let bootDone = $state(false);

 let now = $state(new Date());
 let _clockTimer: any = null;

 let searchQ = $state('');
 let filterMode = $state<'all' | 'mine' | 'fav'>('all');
 let searchInput = $state<HTMLInputElement | null>(null);

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(async () => {
 username = localStorage.getItem('dash_user') || '';

 for (let i = 1; i <= 6; i++) {
 bootStep = i;
 await new Promise(r => setTimeout(r, 120));
 }

 try {
 const [pRes, aRes] = await Promise.all([
 fetch('/api/projects', { headers: _h() }),
 fetch('/api/auth/check', { headers: _h() }),
 ]);
 if (pRes.ok) { const d = await pRes.json(); projects = d.projects || []; }
 if (aRes.ok) { const d = await aRes.json(); username = d.username || ''; isSuper = d.is_super || false; }
 } catch {}

 bootStep = 7;
 loading = false;
 await new Promise(r => setTimeout(r, 200));
 bootDone = true;

 _clockTimer = setInterval(() => { now = new Date(); }, 60_000);

 window.addEventListener('keydown', handleKey);
 });

 onDestroy(() => {
 if (_clockTimer) clearInterval(_clockTimer);
 if (typeof window !== 'undefined') window.removeEventListener('keydown', handleKey);
 });

 function handleKey(e: KeyboardEvent) {
 if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
 e.preventDefault();
 searchInput?.focus();
 }
 }

 let greeting = $derived.by(() => {
 const h = now.getHours();
 if (h >= 5 && h < 12) return 'Good morning';
 if (h >= 12 && h < 17) return 'Good afternoon';
 if (h >= 17 && h < 23) return 'Good evening';
 return 'Working late';
 });

 const totalTables = $derived(projects.reduce((s, p) => s + (p.tables || 0), 0));
 const onlineCount = $derived(projects.length);

 const visibleProjects = $derived.by(() => {
 let list = projects;
 if (filterMode === 'mine') {
 list = list.filter(p => !p.is_shared);
 } else if (filterMode === 'fav') {
 list = list.filter(p => p.is_favorite);
 }
 const q = searchQ.trim().toLowerCase();
 if (q) {
 list = list.filter(p => {
 const name = (p.agent_name || '').toLowerCase();
 const desc = (p.agent_role || '').toLowerCase();
 const cat = (p.category || p.template || '').toLowerCase();
 return name.includes(q) || desc.includes(q) || cat.includes(q);
 });
 }
 return list;
 });

 function timeAgo(ts: string | null): string {
 if (!ts) return '';
 try {
 const d = new Date(ts);
 const diff = Date.now() - d.getTime();
 if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
 if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
 return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 } catch { return ''; }
 }

 function categoryOf(p: any): string {
 const raw = p.category || p.template || p.vertical || 'Workspace';
 return String(raw).replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
 }

 const bootLines = [
 'Setting up your session',
 'Loading your data',
 'Waking up AI brain',
 'Preparing knowledge',
 'Loading your agents',
 'Almost there',
 ];
</script>

<svelte:head>
  <title>Home — {$brand?.name || 'Dash'}</title>
</svelte:head>

<div class="home-page">
  <div class="home-wrap">
    {#if !bootDone}
      <div class="boot-card">
        <div class="boot-title">Welcome back{username ? `, ${username}` : ''}.</div>
        <div class="boot-lines">
          {#each bootLines as line, i}
            {#if bootStep > i}
              <div class="boot-line"><span class="boot-check"><Icon name="check" size={14} /></span><span>{line}</span></div>
            {/if}
          {/each}
        </div>
        <div class="boot-bar"><div class="boot-bar-fill" style="width: {Math.round((bootStep / 6) * 100)}%"></div></div>
      </div>
    {/if}

    {#if bootDone}
      <!-- Header strip -->
      <header class="home-header">
        <div class="home-header-left">
          <h1 class="home-h1">{greeting}, {username || 'there'}</h1>
          <p class="home-meta">{onlineCount} agents online · {totalTables} tables indexed</p>
        </div>

        <div class="home-header-right">
          <div class="home-search">
            <svg class="home-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input
              bind:this={searchInput}
              bind:value={searchQ}
              type="text"
              placeholder="Search agents…"
              class="home-search-input"
              aria-label="Search agents"
            />
            <kbd class="home-kbd">K</kbd>
          </div>

          <div class="home-segment">
            <button class="home-segment-btn" class:active={filterMode === 'all'} onclick={() => filterMode = 'all'}>All</button>
            <button class="home-segment-btn" class:active={filterMode === 'mine'} onclick={() => filterMode = 'mine'}>Mine</button>
            <button class="home-segment-btn" class:active={filterMode === 'fav'} onclick={() => filterMode = 'fav'}>Favorites</button>
          </div>

          <button class="home-new" onclick={() => goto('/ui/projects')}>+ New agent</button>
        </div>
      </header>

      <!-- Card grid (matches /ui/projects style — pipeline progress + Open chat CTA) -->
      {#if visibleProjects.length > 0}
        <div class="home-grid">
          {#each visibleProjects as p}
            <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
            <div class="home-card" onclick={() => goto(`/ui/project/${p.slug}`)} style="cursor: pointer;">
              <button class="home-card-kebab" aria-label="More"
                      onclick={(e) => { e.stopPropagation(); goto(`/ui/project/${p.slug}/settings`); }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
              </button>

              <div class="home-card-head">
                <div class="home-card-icon">{(p.agent_name || 'A').charAt(0).toUpperCase()}</div>
                <div class="home-card-title">
                  <h3>
                    {#if p.is_favorite}<span class="home-star-inline" title="Starred">★</span>{/if}
                    {p.agent_name}
                  </h3>
                  <p class="home-card-cat">General · {p.tables || 0} tables</p>
                </div>
              </div>

              {#if p.agent_role}
                <p class="home-card-desc">{p.agent_role}</p>
              {/if}

              {#if (p.tables || 0) > 0}
                <div class="home-card-progress">
                  <div class="home-card-progress-row">
                    <span class="home-card-progress-label">Pipeline progress</span>
                    <span class="home-card-progress-val">{p.is_trained ? '100%' : '45%'}</span>
                  </div>
                  <div class="home-card-progress-track">
                    <div class="home-card-progress-fill" style="width: {p.is_trained ? 100 : 45}%"></div>
                  </div>
                </div>
              {/if}

              <div class="home-card-status">
                <span class="home-card-status-dot {p.is_trained ? 'trained' : 'untrained'}"></span>
                <span>{p.is_trained ? `Trained · ${timeAgo(p.updated_at)}` : 'Not trained'}</span>
              </div>

              <button class="home-chat-cta" onclick={(e) => { e.stopPropagation(); goto(`/ui/project/${p.slug}`); }}>
                <span class="home-chat-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                </span>
                <span class="home-chat-label">Open chat</span>
                <span class="home-chat-arrow">→</span>
              </button>
            </div>
          {/each}
        </div>
      {:else}
        <div class="home-empty">
          {#if projects.length === 0}
            <div class="home-empty-msg">No agents yet.</div>
            <button class="home-cta-primary" onclick={() => goto('/ui/projects')}>Create your first agent</button>
          {:else}
            <div class="home-empty-msg">No agents match your filters.</div>
            <button class="home-cta-ghost" onclick={() => { searchQ = ''; filterMode = 'all'; }}>Reset filters</button>
          {/if}
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
 .home-page {
 background: var(--pw-bg);
 min-height: 100%;
 overflow-y: auto;
 font-family: var(--pw-font-body);
 color: var(--pw-ink);
 }

 .home-wrap {
 padding: 40px 32px 64px;
 max-width: 1200px;
 margin: 0 auto;
 }

 /* Boot panel */
 .boot-card {
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius);
 padding: 24px 28px;
 max-width: 520px;
 }
 .boot-title {
 font-family: var(--pw-font-headline);
 font-size: 19px;
 font-weight: 500;
 letter-spacing: -0.02em;
 color: var(--pw-ink);
 }
 .boot-lines {
 margin-top: 14px;
 display: flex;
 flex-direction: column;
 gap: 6px;
 }
 .boot-line {
 display: flex;
 align-items: center;
 gap: 8px;
 font-size: 13.5px;
 color: var(--pw-ink-soft);
 }
 .boot-check { color: var(--pw-success); }
 .boot-bar {
 margin-top: 16px;
 height: 4px;
 background: var(--pw-border);
 border-radius: var(--pw-radius-pill);
 overflow: hidden;
 }
 .boot-bar-fill {
 height: 100%;
 background: var(--pw-accent);
 transition: width 0.2s;
 }

 /* Header strip */
 .home-header {
 display: flex;
 align-items: flex-end;
 justify-content: space-between;
 gap: 24px;
 flex-wrap: wrap;
 margin-bottom: 32px;
 }

 .home-header-left { min-width: 0; flex: 1 1 auto; }

 .home-h1 {
 font-family: var(--pw-font-headline);
 font-size: 34px;
 font-weight: 500;
 letter-spacing: -0.025em;
 margin: 0 0 8px;
 color: var(--pw-ink);
 line-height: 1.1;
 }
 .home-meta {
 margin: 0;
 font-size: 13.5px;
 color: var(--pw-muted);
 }

 .home-header-right {
 display: flex;
 align-items: center;
 gap: 10px;
 flex-wrap: wrap;
 }

 /* Search pill */
 .home-search {
 position: relative;
 display: flex;
 align-items: center;
 width: 280px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-pill);
 padding: 0 12px;
 transition: border-color 0.15s, box-shadow 0.15s;
 }
 .home-search:focus-within {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-bg);
 }
 .home-search-icon {
 width: 14px;
 height: 14px;
 color: var(--pw-muted);
 flex-shrink: 0;
 }
 .home-search-input {
 flex: 1;
 background: transparent;
 border: none;
 outline: none;
 padding: 8px 8px;
 font-family: inherit;
 font-size: 11px;
 color: var(--pw-ink);
 min-width: 0;
 }
 .home-search-input::placeholder { color: var(--pw-dim); }
 .home-kbd {
 font-family: inherit;
 font-size: 10.5px;
 color: var(--pw-muted);
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 2px 6px;
 flex-shrink: 0;
 }

 /* Segmented filter */
 .home-segment {
 display: inline-flex;
 background: var(--pw-bg-alt);
 border-radius: var(--pw-radius-pill);
 padding: 3px;
 gap: 2px;
 }
 .home-segment-btn {
 background: transparent;
 border: none;
 font-family: inherit;
 font-size: 12.5px;
 font-weight: 500;
 color: var(--pw-muted);
 padding: 6px 14px;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 transition: background 0.15s, color 0.15s, box-shadow 0.15s;
 }
 .home-segment-btn:hover { color: var(--pw-ink); }
 .home-segment-btn.active {
 background: var(--pw-surface);
 color: var(--pw-ink);
 box-shadow: var(--pw-shadow-sm);
 }

 /* + New CTA */
 .home-new {
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 border-radius: var(--pw-radius-pill);
 padding: 8px 16px;
 font-family: inherit;
 font-size: 11px;
 font-weight: 500;
 cursor: pointer;
 transition: background 0.15s;
 }
 .home-new:hover { background: var(--pw-accent-ink); }

 /* Card grid */
 .home-grid {
 display: grid;
 grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
 gap: 24px;
 }

 .home-card {
 position: relative;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius);
 padding: 20px 22px 0;
 box-shadow: var(--pw-shadow-sm);
 transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
 display: flex;
 flex-direction: column;
 gap: 14px;
 overflow: hidden;
 }
 .home-card:hover {
 transform: translateY(-2px);
 box-shadow: var(--pw-shadow-md);
 border-color: var(--pw-border-strong);
 }
 .home-card-kebab {
 position: absolute;
 top: 12px;
 right: 12px;
 width: 28px;
 height: 28px;
 background: transparent;
 border: none;
 border-radius: 4px;
 color: var(--pw-muted);
 cursor: pointer;
 display: flex;
 align-items: center;
 justify-content: center;
 transition: background 0.15s ease, color 0.15s ease;
 }
 .home-card-kebab:hover {
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 }
 .home-star-inline {
 color: var(--pw-accent);
 margin-right: 4px;
 }
 .home-card-progress {
 display: flex;
 flex-direction: column;
 gap: 6px;
 }
 .home-card-progress-row {
 display: flex;
 justify-content: space-between;
 font-size: 11.5px;
 color: var(--pw-muted);
 }
 .home-card-progress-label {
 letter-spacing: -0.005em;
 }
 .home-card-progress-val {
 font-weight: 600;
 color: var(--pw-ink);
 }
 .home-card-progress-track {
 height: 4px;
 background: var(--pw-bg-alt);
 border-radius: 2px;
 overflow: hidden;
 }
 .home-card-progress-fill {
 height: 100%;
 background: var(--pw-accent);
 transition: width 0.3s ease;
 }
 .home-chat-cta {
 display: flex;
 align-items: center;
 gap: 8px;
 padding: 14px 0;
 margin: 6px -22px 0;
 padding-left: 22px;
 padding-right: 22px;
 background: transparent;
 border: none;
 border-top: 1px solid var(--pw-border);
 color: var(--pw-accent);
 font-size: 13px;
 font-weight: 500;
 cursor: pointer;
 transition: background 0.15s ease;
 text-align: left;
 width: calc(100% + 44px);
 }
 .home-chat-cta:hover {
 background: var(--pw-bg-alt);
 }
 .home-chat-icon {
 display: inline-flex;
 align-items: center;
 }
 .home-chat-label {
 flex: 1;
 }
 .home-chat-arrow {
 font-size: 14px;
 }

 .home-card-head {
 display: flex;
 align-items: flex-start;
 gap: 12px;
 }
 .home-card-icon {
 width: 40px;
 height: 40px;
 flex-shrink: 0;
 border-radius: 0;
 background: var(--pw-accent);
 color: #fff;
 display: flex;
 align-items: center;
 justify-content: center;
 font-family: var(--pw-font-headline);
 font-size: 13px;
 font-weight: 500;
 }
 .home-card-title {
 flex: 1;
 min-width: 0;
 }
 .home-card-title h3 {
 margin: 0;
 font-family: var(--pw-font-headline);
 font-size: 13px;
 font-weight: 500;
 letter-spacing: -0.01em;
 color: var(--pw-ink);
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: nowrap;
 }
 .home-card-cat {
 margin: 2px 0 0;
 font-size: 12.5px;
 color: var(--pw-muted);
 }
 .home-card-actions-top {
 display: flex;
 align-items: center;
 gap: 4px;
 }
 .home-icon-btn {
 background: transparent;
 border: none;
 color: var(--pw-muted);
 width: 28px;
 height: 28px;
 border-radius: 0;
 display: inline-flex;
 align-items: center;
 justify-content: center;
 cursor: pointer;
 transition: background 0.15s, color 0.15s;
 padding: 0;
 }
 .home-icon-btn:hover {
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 }
 .home-icon-btn svg {
 width: 16px;
 height: 16px;
 }

 .home-card-desc {
 margin: 0;
 font-size: 11px;
 color: var(--pw-ink-soft);
 line-height: 1.5;
 display: -webkit-box;
 -webkit-line-clamp: 2;
 -webkit-box-orient: vertical;
 overflow: hidden;
 }

 .home-card-status {
 display: flex;
 align-items: center;
 gap: 7px;
 font-size: 11px;
 color: var(--pw-muted);
 }
 .home-card-status-dot {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 flex-shrink: 0;
 }
 .home-card-status-dot.trained { background: var(--pw-success); }
 .home-card-status-dot.untrained { background: var(--pw-dim); }

 .home-card-cta {
 display: flex;
 gap: 8px;
 margin-top: auto;
 }
 .home-cta-primary {
 flex: 1;
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 padding: 8px 16px;
 font-family: inherit;
 font-size: 11px;
 font-weight: 500;
 cursor: pointer;
 border-radius: var(--pw-radius-pill);
 transition: background 0.15s;
 }
 .home-cta-primary:hover { background: var(--pw-accent-ink); }
 .home-cta-ghost {
 background: transparent;
 color: var(--pw-ink-soft);
 border: 1px solid var(--pw-border-strong);
 padding: 8px 16px;
 font-family: inherit;
 font-size: 11px;
 font-weight: 500;
 cursor: pointer;
 border-radius: var(--pw-radius-pill);
 transition: background 0.15s, color 0.15s;
 }
 .home-cta-ghost:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 }

 /* Empty state */
 .home-empty {
 background: var(--pw-surface);
 border: 1px dashed var(--pw-border-strong);
 border-radius: var(--pw-radius);
 padding: 40px;
 text-align: center;
 display: flex;
 flex-direction: column;
 align-items: center;
 gap: 14px;
 }
 .home-empty-msg {
 font-size: 11px;
 color: var(--pw-muted);
 }
 .home-empty .home-cta-primary,
 .home-empty .home-cta-ghost {
 flex: 0 0 auto;
 padding: 10px 22px;
 }

 @media (max-width: 720px) {
 .home-wrap { padding: 24px 18px 48px; }
 .home-h1 { font-size: 30px; }
 .home-header { flex-direction: column; align-items: stretch; }
 .home-header-right { width: 100%; }
 .home-search { width: 100%; }
 }
</style>
