<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import EChartView from '$lib/echart.svelte';
 import { markdownToHtml } from '$lib';

 let dashboards = $state<any[]>([]);
 let v2Dashboards = $state<any[]>([]);
 let activeDashboard = $state<any>(null);
 let editMode = $state(false);
 let loading = $state(true);
 let newDashName = $state('');
 let newDashProject = $state('');
 let showCreate = $state(false);
 let view = $state<'list' | 'detail'>('list');
 let activeListTab = $state<'all' | 'my' | 'shared' | 'favorites'>('all');
 let projects = $state<any[]>([]);
 let deleteTarget = $state<any>(null);
 let deleteTypedName = $state('');
 let searchQuery = $state('');
 let searchInput: HTMLInputElement | null = $state(null);
 let favorites = $state<Set<string>>(new Set());

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(async () => {
 await Promise.all([loadDashboards(), loadV2Dashboards(), loadProjects()]);
 loading = false;
 try {
 const fav = localStorage.getItem('dash_dashboard_favs');
 if (fav) favorites = new Set(JSON.parse(fav));
 } catch {}
 if (typeof window !== 'undefined') {
 window.addEventListener('keydown', handleKeydown);
 }
 });

 onDestroy(() => {
 if (typeof window !== 'undefined') {
 try { window.removeEventListener('keydown', handleKeydown); } catch {}
 }
 });

 function handleKeydown(e: KeyboardEvent) {
 if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
 e.preventDefault();
 searchInput?.focus();
 }
 }

 function toggleFav(key: string) {
 if (favorites.has(key)) favorites.delete(key);
 else favorites.add(key);
 favorites = new Set(favorites);
 try { localStorage.setItem('dash_dashboard_favs', JSON.stringify([...favorites])); } catch {}
 }

 async function loadDashboards() {
 try {
 const res = await fetch('/api/all-dashboards', { headers: _h() });
 if (res.ok) { const d = await res.json(); dashboards = d.dashboards || []; }
 } catch {}
 }

 async function loadV2Dashboards() {
 try {
 const res = await fetch('/api/dashboards/list-all', { headers: _h() });
 if (res.ok) v2Dashboards = await res.json();
 } catch {}
 }

 async function loadProjects() {
 try {
 const res = await fetch('/api/user-projects-brief', { headers: _h() });
 if (res.ok) { const d = await res.json(); projects = d.projects || []; }
 } catch {}
 }

 async function loadDashboard(id: number, projectSlug: string) {
 try {
 const res = await fetch(`/api/projects/${projectSlug}/dashboards/${id}`, { headers: _h() });
 if (res.ok) {
 activeDashboard = { ...(await res.json()), project_slug: projectSlug };
 view = 'detail'; editMode = false;
 }
 } catch {}
 }

 async function createDashboard() {
 const name = newDashName.trim() || 'Dashboard';
 const slug = newDashProject || projects[0]?.slug;
 if (!slug) return;
 try {
 const res = await fetch(`/api/projects/${slug}/dashboards?name=${encodeURIComponent(name)}`, { method: 'POST', headers: _h() });
 if (res.ok) { const d = await res.json(); newDashName = ''; showCreate = false; await loadDashboards(); await loadDashboard(d.id, slug); }
 } catch {}
 }

 async function removeWidget(widgetId: string) {
 if (!activeDashboard) return;
 try { await fetch(`/api/projects/${activeDashboard.project_slug}/dashboards/${activeDashboard.id}/widgets/${widgetId}`, { method: 'DELETE', headers: _h() }); await loadDashboard(activeDashboard.id, activeDashboard.project_slug); } catch {}
 }

 async function deleteDashboard() {
 if (!activeDashboard) return;
 try { await fetch(`/api/projects/${activeDashboard.project_slug}/dashboards/${activeDashboard.id}`, { method: 'DELETE', headers: _h() }); activeDashboard = null; view = 'list'; await loadDashboards(); } catch {}
 }

 async function duplicateDashboard(d: any) {
 try {
 const res = await fetch(`/api/projects/${d.project_slug}/dashboards/${d.id}`, { headers: _h() });
 if (!res.ok) return;
 const full = await res.json();
 const newName = `${d.name} (copy)`;
 const cr = await fetch(`/api/projects/${d.project_slug}/dashboards?name=${encodeURIComponent(newName)}`, { method: 'POST', headers: _h() });
 if (!cr.ok) return;
 const nd = await cr.json();
 for (const w of (full.widgets || [])) {
 try {
 await fetch(`/api/projects/${d.project_slug}/dashboards/${nd.id}/widgets`, {
 method: 'POST',
 headers: { ..._h(), 'Content-Type': 'application/json' },
 body: JSON.stringify(w),
 });
 } catch {}
 }
 await loadDashboards();
 } catch {}
 }

 async function exportDashboard(d: any) {
 try {
 const res = await fetch(`/api/projects/${d.project_slug}/dashboards/${d.id}`, { headers: _h() });
 if (res.ok) {
 const full = await res.json();
 const s = (full.widgets || []).map((w: any) => ({ title: w.title || '', content: w.content || '', headers: w.headers || [], rows: w.rows || [] }));
 const pptxRes = await fetch('/api/export/pptx', { method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: d.name, slides: s }) });
 if (pptxRes.ok) { const blob = await pptxRes.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `${d.name}.pptx`; a.click(); URL.revokeObjectURL(url); }
 }
 } catch {}
 }

 function goBack() { activeDashboard = null; view = 'list'; editMode = false; loadDashboards(); }

 function timeAgo(ts: string | null): string {
 if (!ts) return '';
 const diff = Date.now() - new Date(ts).getTime();
 if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
 if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
 return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 }

 function formatCreated(ts: string | null): string {
 if (!ts) return 'recently';
 try {
 return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 } catch { return 'recently'; }
 }

 function dashKey(d: any, kind: 'v1' | 'v2' = 'v1'): string {
 return `${kind}:${d.project_slug}:${d.id}`;
 }

 // Combined dashboards stream for filtering (v1 + v2 normalized)
 const allCombined = $derived([
 ...v2Dashboards.map((v) => ({
 _kind: 'v2' as const,
 id: v.id,
 name: v.title || 'Untitled',
 project_slug: v.project_slug,
 project_name: v.project_slug,
 created_at: v.created_at,
 updated_at: v.created_at,
 widget_count: v.widget_count || 0,
 creator: v.creator || 'agent',
 is_owner: true,
 description: v.description || '',
 })),
 ...dashboards.map((d) => ({
 _kind: 'v1' as const,
 id: d.id,
 name: d.name,
 project_slug: d.project_slug,
 project_name: d.project_name || d.project_slug,
 created_at: d.created_at,
 updated_at: d.updated_at,
 widget_count: d.widget_count || 0,
 creator: d.creator || 'unknown',
 is_owner: !!d.is_owner,
 description: d.description || '',
 })),
 ]);

 const filteredDashboards = $derived(
 allCombined.filter((d) => {
 // Tab filter
 if (activeListTab === 'my' && !d.is_owner) return false;
 if (activeListTab === 'shared' && d.is_owner) return false;
 if (activeListTab === 'favorites' && !favorites.has(dashKey(d, d._kind))) return false;

 // Search filter
 const q = searchQuery.trim().toLowerCase();
 if (!q) return true;
 const hay = `${d.name} ${d.project_name} ${d.project_slug} ${d.creator}`.toLowerCase();
 return hay.includes(q);
 })
 );

 const totalCount = $derived(allCombined.length);
</script>

<div class="dash-page-wrapper">

  {#if view === 'list'}
    <div class="ds-page">
      <div class="ds-page-head">
        <div>
          <h1 class="ds-page-title">Dashboards</h1>
          <div class="ds-page-sub">{totalCount} {totalCount === 1 ? 'dashboard' : 'dashboards'} · pinned charts and KPIs from chat</div>
        </div>
        <div class="dash-header-right">
          <div class="dash-search-wrap">
            <svg class="dash-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <input
              bind:this={searchInput}
              bind:value={searchQuery}
              type="text"
              placeholder="Search dashboards…"
              class="ds-input dash-search-input"
            />
            <kbd class="dash-kbd">K</kbd>
          </div>

          <div class="dash-filter-pills">
            {#each [
              { id: 'all', label: 'All' },
              { id: 'my', label: 'My' },
              { id: 'favorites', label: 'Favorites' },
              { id: 'shared', label: 'Shared' },
            ] as tab}
              <button
                class="pill-segment"
                class:active={activeListTab === tab.id}
                onclick={() => activeListTab = tab.id as any}
              >
                {tab.label}
              </button>
            {/each}
          </div>

          <button class="btn-primary" onclick={() => { showCreate = true; newDashProject = projects[0]?.slug || ''; }}>
            + New dashboard
          </button>
        </div>
      </div>

      <!-- Dashboard Cards -->
      {#if loading}
        <div class="ds-empty"><div class="ds-empty-text">Loading…</div></div>
      {:else if filteredDashboards.length === 0}
        <div class="ds-empty">
          <div class="ds-empty-icon">▦</div>
          <div class="ds-empty-title">
            {searchQuery ? 'No matches' : activeListTab === 'shared' ? 'No shared dashboards' : activeListTab === 'favorites' ? 'No favorites yet' : 'No dashboards yet'}
          </div>
          <div class="ds-empty-text">
            {searchQuery ? `Nothing found for "${searchQuery}".` : 'Create a dashboard and pin charts from your chat conversations.'}
          </div>
          {#if activeListTab !== 'shared' && !searchQuery}
            <button class="btn-primary" onclick={() => { showCreate = true; newDashProject = projects[0]?.slug || ''; }}>+ New dashboard</button>
          {/if}
        </div>
      {:else}
        <div class="dash-grid">
          {#each filteredDashboards as d (dashKey(d, d._kind))}
            {@const open = () => d._kind === 'v2' ? goto(`${base}/project/${d.project_slug}/dashboards/${d.id}`) : loadDashboard(d.id, d.project_slug)}
            <div class="dash-card">
              <div class="dash-card-head">
                <div class="dash-card-icon">{(d.name || 'D')[0]?.toUpperCase()}</div>
                <div class="dash-card-title">
                  <h3>
                    {#if favorites.has(dashKey(d, d._kind))}<span class="dash-star-inline" title="Starred">★</span>{/if}
                    {d.name}
                  </h3>
                  <p class="dash-card-cat">{d.project_slug} · {d.widget_count} {d.widget_count === 1 ? 'widget' : 'widgets'}</p>
                </div>
                <button class="dash-fav-btn" onclick={() => toggleFav(dashKey(d, d._kind))} title={favorites.has(dashKey(d, d._kind)) ? 'Unstar' : 'Star'} aria-label="Toggle favorite">
                  {favorites.has(dashKey(d, d._kind)) ? '★' : '☆'}
                </button>
              </div>

              <p class="dash-card-desc">{d.description || 'Pin charts and analysis from chat to build dashboards'}</p>

              <div class="dash-card-status">
                <span class="dash-card-status-dot {d.widget_count > 0 ? 'ready' : 'empty'}"></span>
                <span>Created {formatCreated(d.created_at)}{d.is_owner ? '' : ' · shared'}</span>
              </div>

              <button class="dash-open-cta" onclick={open}>
                <span class="dash-open-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
                </span>
                <span class="dash-open-label">Open dashboard</span>
                <span class="dash-open-arrow">→</span>
              </button>
            </div>
          {/each}
        </div>
      {/if}
    </div>

  {:else}
    <!-- ═══ DASHBOARD DETAIL ═══ -->
    <div class="dash-detail-bar">
      <div class="dash-detail-left">
        <button onclick={goBack} class="dash-back-btn" aria-label="Back">←</button>
        <span class="dash-detail-title">{activeDashboard?.name || 'Dashboard'}</span>
        <span class="dash-detail-meta">{activeDashboard?.widgets?.length || 0} widgets</span>
      </div>
      <div class="dash-detail-actions">
        <button class="btn-secondary btn-sm" onclick={async () => {
          if (!activeDashboard?.widgets?.length) return;
          const slides = activeDashboard.widgets.map((w: any) => ({ title: w.title || '', content: w.content || '', headers: w.headers || [], rows: w.rows || [] }));
          try { const res = await fetch('/api/export/pptx', { method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: activeDashboard.name, slides }) }); if (res.ok) { const blob = await res.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `${activeDashboard.name}.pptx`; a.click(); URL.revokeObjectURL(url); } } catch {}
        }}>↓ Export PPTX</button>
        <button class="btn-primary btn-sm" onclick={() => editMode = !editMode}>{editMode ? 'Done' : 'Edit'}</button>
      </div>
    </div>

    <div class="dash-detail-body">
      {#if activeDashboard?.widgets?.length > 0}
        <div class="dashboard-grid">
          {#each activeDashboard.widgets as widget, wi}
            <div class="dash-widget" class:widget-full={widget.full || widget.type === 'text'}>
              <div class="dash-widget-head">
                <span class="dash-widget-title">{widget.title}</span>
                <div class="dash-widget-actions">
                  <span class="dash-widget-type">{widget.type}</span>
                  {#if editMode}<button onclick={() => removeWidget(widget.id)} class="dash-widget-x" aria-label="Remove widget"><Icon name="x" size={14} /></button>{/if}
                </div>
              </div>
              <div class="dash-widget-body">
                {#if widget.type === 'chart' && widget.headers && widget.rows}
                  <div class="dash-chart-types">
                    {#each ['bar', 'line', 'pie', 'scatter', 'area'] as ct}
                      <button class="dash-chart-pill" class:dash-chart-pill-active={(widget.chartType || 'bar') === ct} onclick={() => { const w = [...activeDashboard.widgets]; w[wi] = { ...w[wi], chartType: ct }; activeDashboard = { ...activeDashboard, widgets: w }; }}>{ct}</button>
                    {/each}
                  </div>
                  <div style="height: 280px;"><EChartView headers={widget.headers} rows={(widget.rows || []).map((r: any[]) => r.map((c: any) => String(c ?? '')))} chartType={widget.chartType || 'bar'} /></div>
                  <details class="dash-data-details">
                    <summary>View data ({widget.rows?.length} rows)</summary>
                    <div class="dash-data-scroll"><table class="dash-data-table"><thead><tr>{#each widget.headers as h}<th>{h}</th>{/each}</tr></thead><tbody>{#each widget.rows as row}<tr>{#each row as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody></table></div>
                  </details>
                {:else if widget.type === 'metric'}
                  <div class="dash-metric"><div class="dash-metric-val">{widget.content || '0'}</div></div>
                {:else if widget.type === 'table' && widget.headers && widget.rows}
                  <div class="dash-data-scroll">
                    <table class="dash-data-table">
                      <thead><tr>{#each widget.headers as h}<th>{h}</th>{/each}</tr></thead>
                      <tbody>{#each widget.rows as row}<tr>{#each row as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody>
                    </table>
                  </div>
                {:else}
                  <div class="prose-chat">{@html markdownToHtml(widget.content || '')}</div>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="dash-empty">
          <div class="dash-empty-title">Empty dashboard</div>
          <div class="dash-empty-sub">Go to chat and click pin on any response to add widgets.</div>
        </div>
      {/if}
      {#if editMode}
        <div style="margin-top: 20px; text-align: center;"><button class="btn-danger" onclick={deleteDashboard}>Delete this dashboard</button></div>
      {/if}
    </div>
  {/if}
</div>

<!-- Delete confirmation modal -->
{#if deleteTarget}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget) deleteTarget = null; }}>
    <div class="ds-modal">
      <div class="ds-modal-head">
        <h3 class="ds-modal-title">Delete dashboard</h3>
        <button class="ds-modal-close" onclick={() => deleteTarget = null} aria-label="Close">×</button>
      </div>
      <div class="ds-modal-body">
        <p class="dash-modal-text">
          This will permanently delete <strong>"{deleteTarget.name}"</strong> and all its widgets. This action cannot be undone.
        </p>
        <div class="ds-field">
          <label class="ds-field-label">Type the dashboard name to confirm</label>
          <input type="text" bind:value={deleteTypedName} placeholder={deleteTarget.name} class="ds-input" />
        </div>
      </div>
      <div class="ds-modal-foot">
        <button onclick={() => deleteTarget = null} class="btn-secondary">Cancel</button>
        <button disabled={deleteTypedName !== deleteTarget.name} onclick={async () => {
          await fetch(`/api/projects/${deleteTarget.project_slug}/dashboards/${deleteTarget.id}`, { method: 'DELETE', headers: _h() });
          deleteTarget = null; deleteTypedName = ''; await loadDashboards();
        }} class="btn-danger">
          Delete permanently
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- Create modal -->
{#if showCreate}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget) showCreate = false; }}>
    <div class="ds-modal">
      <div class="ds-modal-head">
        <h3 class="ds-modal-title">Create dashboard</h3>
        <button class="ds-modal-close" onclick={() => showCreate = false} aria-label="Close">×</button>
      </div>
      <div class="ds-modal-body">
        <div class="ds-field">
          <label class="ds-field-label">Dashboard name</label>
          <input type="text" bind:value={newDashName} placeholder="e.g. Sales overview, Weekly report…" class="ds-input" />
        </div>
        <div class="ds-field" style="margin-top: var(--sp-3);">
          <label class="ds-field-label">Project</label>
          <select bind:value={newDashProject} class="ds-select">
            {#each projects as p}<option value={p.slug}>{p.agent_name || p.name}</option>{/each}
          </select>
        </div>
      </div>
      <div class="ds-modal-foot">
        <button class="btn-secondary" onclick={() => showCreate = false}>Cancel</button>
        <button class="btn-primary" onclick={createDashboard}>Create</button>
      </div>
    </div>
  </div>
{/if}

<style>
 .dash-page-wrapper {
 display: flex;
 flex-direction: column;
 height: 100%;
 overflow-y: auto;
 background: var(--pw-bg);
 font-family: var(--pw-font-body);
 }

 .dash-page {
 max-width: 1280px;
 width: 100%;
 margin: 0 auto;
 padding: 32px;
 }

 /* Header strip */
 .dash-header {
 display: flex;
 align-items: flex-start;
 justify-content: space-between;
 gap: 24px;
 flex-wrap: wrap;
 margin-bottom: 24px;
 }
 .dash-header-left { display: flex; flex-direction: column; gap: 4px; }
 .dash-header-right {
 display: flex;
 align-items: center;
 gap: 10px;
 flex-wrap: wrap;
 }

 .dash-h1 {
 font-family: var(--pw-font-headline);
 font-size: 40px;
 font-weight: 500;
 letter-spacing: -0.01em;
 margin: 0;
 color: var(--pw-ink);
 line-height: 1.1;
 }
 .dash-meta {
 font-size: 12px;
 color: var(--pw-muted);
 margin-top: 4px;
 }

 /* Search */
 .dash-search-wrap {
 position: relative;
 display: flex;
 align-items: center;
 width: 280px;
 }
 .dash-search-icon {
 position: absolute;
 left: 12px;
 color: var(--pw-muted);
 pointer-events: none;
 }
 .dash-search {
 width: 100%;
 height: 36px;
 padding: 0 56px 0 34px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 font-family: var(--pw-font-body);
 font-size: 12px;
 color: var(--pw-ink);
 outline: none;
 transition: border-color 0.12s, box-shadow 0.12s;
 }
 .dash-search:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-soft);
 }
 .dash-kbd {
 position: absolute;
 right: 10px;
 font-family: var(--pw-font-body);
 font-size: 10px;
 color: var(--pw-muted);
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 padding: 1px 5px;
 pointer-events: none;
 }

 /* Filter pills */
 .dash-filter-pills {
 display: inline-flex;
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 padding: 3px;
 gap: 2px;
 }
 .dash-pill {
 background: none;
 border: none;
 padding: 6px 14px;
 font-family: var(--pw-font-body);
 font-size: 11px;
 font-weight: 500;
 color: var(--pw-muted);
 cursor: pointer;
 border-radius: var(--pw-radius-sm);
 transition: background 0.12s, color 0.12s;
 }
 .dash-pill:hover { color: var(--pw-ink); }
 .dash-pill-active {
 background: var(--pw-surface);
 color: var(--pw-ink);
 box-shadow: 0 1px 2px rgba(0,0,0,0.04);
 }

 /* CTA */
 .dash-cta {
 padding: 0 16px;
 height: 36px;
 font-size: 12px;
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 font-family: var(--pw-font-body);
 font-weight: 500;
 transition: filter 0.12s;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 }
 .dash-cta:hover { filter: brightness(0.95); }

 /* Grid */
 .dash-grid {
 display: grid;
 grid-template-columns: repeat(3, minmax(0, 1fr));
 gap: 24px;
 }
 @media (max-width: 1100px) {
 .dash-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
 }
 @media (max-width: 700px) {
 .dash-grid { grid-template-columns: minmax(0, 1fr); }
 }

 /* Card — mirror .proj-card */
 .dash-card {
 position: relative;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius, 16px);
 padding: 24px;
 display: flex;
 flex-direction: column;
 gap: 14px;
 box-shadow: var(--pw-shadow-sm);
 transition: box-shadow .15s, transform .15s, border-color .15s;
 overflow: visible;
 }
 .dash-card:hover {
 box-shadow: var(--pw-shadow-md);
 transform: translateY(-2px);
 border-color: var(--pw-border-strong);
 }

 .dash-card-head { display: flex; align-items: flex-start; gap: 12px; }
 .dash-card-icon {
 width: 36px; height: 36px; border-radius: var(--pw-radius-sm);
 background: var(--pw-accent); color: #fff;
 display: grid; place-items: center;
 font-family: var(--pw-font-headline);
 font-weight: 600; font-size: 14px; flex: 0 0 auto;
 }
 .dash-card-title { flex: 1; min-width: 0; }
 .dash-card-title h3 {
 font-family: var(--pw-font-headline);
 font-size: 15px; font-weight: 500;
 letter-spacing: -0.015em;
 margin: 0 0 2px; color: var(--pw-ink);
 text-transform: none;
 white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
 }
 .dash-star-inline { color: var(--pw-accent); margin-right: 2px; }
 .dash-card-cat { font-size: 12.5px; color: var(--pw-muted); margin: 0; }
 .dash-fav-btn {
 width: 28px; height: 28px; border: none; background: transparent;
 color: var(--pw-muted); cursor: pointer; font-size: 16px;
 display: grid; place-items: center; flex: 0 0 auto;
 transition: background .15s, color .15s;
 }
 .dash-fav-btn:hover { background: var(--pw-bg-alt); color: var(--pw-accent); }
 .dash-card-desc {
 font-size: 13.5px; color: var(--pw-ink-soft);
 line-height: 1.5; margin: 0;
 display: -webkit-box; -webkit-line-clamp: 2;
 -webkit-box-orient: vertical; overflow: hidden;
 }
 .dash-card-status {
 display: flex; align-items: center; gap: 8px;
 font-size: 12.5px; color: var(--pw-muted);
 }
 .dash-card-status-dot { width: 7px; height: 7px; border-radius: 50%; }
 .dash-card-status-dot.ready { background: var(--pw-success); }
 .dash-card-status-dot.empty { background: var(--pw-dim); }

 .dash-open-cta {
 display: flex; align-items: center; gap: 10px;
 width: calc(100% + 40px);
 margin: 16px -20px -20px;
 padding: 12px 20px;
 background: transparent;
 border: none;
 border-top: 1px solid var(--pw-border-soft);
 border-radius: var(--pw-radius-sm);
 font: inherit; font-size: 12px; font-weight: 500;
 color: var(--pw-accent);
 cursor: pointer;
 transition: background .12s;
 margin-top: auto;
 }
 .dash-open-cta:hover { background: var(--pw-accent-wash); }
 .dash-open-cta:hover .dash-open-arrow { transform: translateX(2px); }
 .dash-open-icon { display: inline-flex; align-items: center; }
 .dash-open-label { flex: 1; text-align: left; }
 .dash-open-arrow { transition: transform .15s; opacity: .75; }

 .dash-card-head {
 display: flex;
 align-items: center;
 gap: 10px;
 }
 .dash-icon-wrap {
 display: inline-flex;
 align-items: center;
 justify-content: center;
 width: 32px;
 height: 32px;
 border-radius: var(--pw-radius-sm);
 background: var(--pw-accent-soft);
 color: var(--pw-accent);
 flex-shrink: 0;
 }
 .dash-name-btn {
 flex: 1;
 background: none;
 border: none;
 padding: 0;
 text-align: left;
 font-family: var(--pw-font-body);
 font-size: 14px;
 font-weight: 600;
 color: var(--pw-ink);
 cursor: pointer;
 line-height: 1.3;
 min-width: 0;
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: nowrap;
 }
 .dash-name-btn:hover { color: var(--pw-accent); }

 .dash-fav {
 background: none;
 border: none;
 cursor: pointer;
 font-size: 14px;
 color: var(--pw-muted);
 padding: 0;
 line-height: 1;
 }
 .dash-fav:hover { color: var(--pw-warning, #c5934a); }

 .dash-slug {
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 11px;
 color: var(--pw-muted);
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: nowrap;
 }

 .dash-desc {
 font-size: 12px;
 color: var(--pw-ink-soft, var(--pw-ink));
 line-height: 1.5;
 margin-top: 4px;
 display: -webkit-box;
 -webkit-line-clamp: 2;
 -webkit-box-orient: vertical;
 overflow: hidden;
 }

 .dash-card-meta {
 font-size: 11px;
 color: var(--pw-muted);
 display: flex;
 align-items: center;
 gap: 8px;
 margin-top: 4px;
 }
 .dash-shared-pill {
 font-size: 10px;
 padding: 1px 7px;
 background: var(--pw-bg-alt);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 color: var(--pw-muted);
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }

 .dash-card-actions {
 display: flex;
 align-items: center;
 gap: 6px;
 margin-top: 8px;
 }

 .dash-btn-primary {
 padding: 7px 16px;
 font-size: 11px;
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 font-family: var(--pw-font-body);
 font-weight: 500;
 transition: filter 0.12s;
 }
 .dash-btn-primary:hover { filter: brightness(0.95); }

 .dash-btn-ghost {
 padding: 7px 14px;
 font-size: 11px;
 background: transparent;
 color: var(--pw-ink);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 font-family: var(--pw-font-body);
 font-weight: 500;
 transition: background 0.12s;
 }
 .dash-btn-ghost:hover { background: var(--pw-bg-alt); }
 .dash-btn-ghost:disabled { opacity: 0.4; cursor: not-allowed; }

 .dash-btn-flex { flex: 1; }

 .dash-btn-danger {
 margin-left: auto;
 padding: 6px 10px;
 background: none;
 border: 1px solid transparent;
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 color: var(--pw-error);
 font-size: 11px;
 font-weight: 600;
 }
 .dash-btn-danger:hover { background: var(--pw-bg-alt); border-color: var(--pw-border); }

 .dash-btn-danger-inline {
 padding: 8px 16px;
 font-size: 11px;
 background: transparent;
 color: var(--pw-error);
 border: 1px solid var(--pw-error);
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 font-family: var(--pw-font-body);
 font-weight: 500;
 }

 .dash-btn-confirm-danger {
 flex: 1;
 padding: 9px 14px;
 font-size: 11px;
 font-weight: 600;
 border-radius: var(--pw-radius-sm);
 border: 1px solid var(--pw-error);
 background: var(--pw-error);
 color: #fff;
 cursor: pointer;
 font-family: var(--pw-font-body);
 }
 .dash-btn-confirm-danger:disabled {
 background: var(--pw-bg-alt);
 color: var(--pw-muted);
 cursor: not-allowed;
 }

 /* Empty */
 .dash-empty {
 text-align: center;
 padding: 60px 20px;
 }
 .dash-empty-title {
 font-family: var(--pw-font-headline);
 font-size: 18px;
 font-weight: 500;
 color: var(--pw-ink);
 margin-bottom: 6px;
 }
 .dash-empty-sub {
 font-size: 12px;
 color: var(--pw-muted);
 margin-bottom: 18px;
 }

 /* Detail view */
 .dash-detail-bar {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 14px 24px;
 border-bottom: 1px solid var(--pw-border);
 background: var(--pw-surface);
 }
 .dash-detail-left { display: flex; align-items: center; gap: 12px; }
 .dash-detail-actions { display: flex; align-items: center; gap: 8px; }
 .dash-back-btn {
 background: none;
 border: 1px solid var(--pw-border);
 width: 28px;
 height: 28px;
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 color: var(--pw-ink);
 font-size: 13px;
 }
 .dash-back-btn:hover { background: var(--pw-bg-alt); }
 .dash-detail-title {
 font-family: var(--pw-font-headline);
 font-size: 16px;
 font-weight: 500;
 color: var(--pw-ink);
 }
 .dash-detail-meta {
 font-size: 11px;
 color: var(--pw-muted);
 }
 .dash-detail-body {
 flex: 1;
 padding: 24px;
 overflow-y: auto;
 max-width: 1280px;
 width: 100%;
 margin: 0 auto;
 }

 /* Widgets */
 .dash-widget {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 box-shadow: 0 1px 2px rgba(0,0,0,0.04);
 overflow: hidden;
 }
 .dash-widget-head {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 12px 16px;
 border-bottom: 1px solid var(--pw-border);
 }
 .dash-widget-title {
 font-size: 12px;
 font-weight: 600;
 color: var(--pw-ink);
 }
 .dash-widget-actions { display: flex; align-items: center; gap: 8px; }
 .dash-widget-type {
 font-size: 10px;
 padding: 2px 8px;
 background: var(--pw-accent-soft);
 color: var(--pw-accent);
 border-radius: var(--pw-radius-sm);
 text-transform: uppercase;
 letter-spacing: 0.04em;
 font-weight: 600;
 }
 .dash-widget-x {
 background: none;
 border: none;
 cursor: pointer;
 color: var(--pw-error);
 font-size: 13px;
 font-weight: 600;
 }
 .dash-widget-body { padding: 16px; }

 .dash-chart-types {
 display: flex;
 gap: 4px;
 margin-bottom: 10px;
 }
 .dash-chart-pill {
 padding: 4px 10px;
 background: transparent;
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 cursor: pointer;
 font-family: var(--pw-font-body);
 font-size: 11px;
 color: var(--pw-muted);
 text-transform: capitalize;
 }
 .dash-chart-pill-active {
 background: var(--pw-ink);
 color: var(--pw-surface);
 border-color: var(--pw-ink);
 }

 .dash-data-details { margin-top: 10px; }
 .dash-data-details summary {
 font-size: 11px;
 font-weight: 600;
 cursor: pointer;
 color: var(--pw-muted);
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }
 .dash-data-scroll { overflow-x: auto; margin-top: 8px; }
 .dash-data-table {
 width: 100%;
 font-size: 11px;
 border-collapse: collapse;
 }
 .dash-data-table th {
 text-align: left;
 padding: 6px 10px;
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 border-bottom: 1px solid var(--pw-border);
 font-weight: 600;
 }
 .dash-data-table td {
 padding: 5px 10px;
 border-bottom: 1px solid var(--pw-border);
 color: var(--pw-ink);
 }

 .dash-metric { text-align: center; padding: 24px; }
 .dash-metric-val {
 font-family: var(--pw-font-headline);
 font-size: 40px;
 font-weight: 500;
 color: var(--pw-accent);
 }

 /* Modals */
 .dash-modal-overlay {
 position: fixed;
 inset: 0;
 background: rgba(44,44,44,0.45);
 z-index: 200;
 display: flex;
 align-items: center;
 justify-content: center;
 padding: 24px;
 }
 .dash-modal {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 box-shadow: 0 12px 40px rgba(0,0,0,0.18);
 width: 100%;
 max-width: 420px;
 overflow: hidden;
 }
 .dash-modal-head {
 padding: 14px 18px;
 font-family: var(--pw-font-headline);
 font-size: 14px;
 font-weight: 500;
 color: var(--pw-ink);
 border-bottom: 1px solid var(--pw-border);
 }
 .dash-modal-head-danger {
 background: var(--pw-error);
 color: #fff;
 border-bottom-color: var(--pw-error);
 }
 .dash-modal-body { padding: 18px; }
 .dash-modal-text {
 font-size: 12px;
 color: var(--pw-ink);
 margin-bottom: 14px;
 line-height: 1.5;
 }
 .dash-modal-label {
 font-size: 11px;
 font-weight: 600;
 color: var(--pw-muted);
 margin-bottom: 6px;
 margin-top: 4px;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }
 .dash-modal-input {
 width: 100%;
 padding: 9px 12px;
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-sm);
 font-family: var(--pw-font-body);
 font-size: 12px;
 background: var(--pw-surface);
 color: var(--pw-ink);
 margin-bottom: 12px;
 outline: none;
 }
 .dash-modal-input:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-soft);
 }
 .dash-modal-actions {
 display: flex;
 gap: 8px;
 margin-top: 6px;
 }

 @media (max-width: 768px) {
 .dash-page { padding: 24px; }
 .dash-h1 { font-size: 24px; }
 .dash-header-right { width: 100%; }
 .dash-search-wrap { width: 100%; }
 .dash-grid { grid-template-columns: 1fr; }
 }
</style>
