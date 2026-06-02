<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import TemplateGalleryModal from '$lib/studio/TemplateGalleryModal.svelte';
 import NewAgentForm from '$lib/studio/NewAgentForm.svelte';

 interface Project {
 id: number; slug: string; name: string; agent_name: string; agent_role: string;
 agent_personality: string; tables: number; rows: number; created_at: string; updated_at: string;
 is_favorite?: boolean; shared_by?: string; last_trained?: string;
 }

 let projects = $state<Project[]>([]);
 let sharedProjects = $state<Project[]>([]);
 let loading = $state(true);
 let tab = $state<'all'|'mine'|'fav'|'shared'>('all');
 let query = $state('');
 let searchInput: HTMLInputElement | null = null;
 let deleteTarget = $state<any>(null);
 let deleteTypedName = $state('');
 let dupBusy = $state<Record<string, boolean>>({});
 let menuOpen = $state<string | null>(null);
 let renameTarget = $state<any>(null);
 let renameNew = $state('');
 let renameBusy = $state(false);

 let templateGalleryOpen = $state(false);
 let newAgentFormOpen = $state(false);
 let selectedTemplate = $state<any>(null);

 async function archiveProject(p: any) {
 try {
 const r = await fetch(`/api/projects/${p.slug}/archive`, { method: 'POST', headers: _h() });
 if (r.ok) await loadProjects();
 else alert((await r.json().catch(() => ({}))).detail || 'Archive failed');
 } catch (e: any) { alert(e?.message || 'Archive failed'); }
 }

 async function submitRename() {
 if (!renameTarget || !renameNew || renameNew.length < 2 || renameBusy) return;
 renameBusy = true;
 try {
 const u = new URLSearchParams({ name: renameNew });
 const r = await fetch(`/api/projects/${renameTarget.slug}?${u}`, { method: 'PUT', headers: _h() });
 if (r.ok) {
 renameTarget = null; renameNew = '';
 await loadProjects();
 } else {
 alert((await r.json().catch(() => ({}))).detail || 'Rename failed');
 }
 } catch (e: any) { alert(e?.message || 'Rename failed'); }
 finally { renameBusy = false; }
 }

 async function duplicateProject(p: any) {
 if (dupBusy[p.slug]) return;
 dupBusy = { ...dupBusy, [p.slug]: true };
 try {
 const r = await fetch(`/api/projects/${p.slug}/duplicate`, { method: 'POST', headers: _h() });
 if (r.ok) {
 await loadProjects();
 } else {
 const err = await r.json().catch(() => ({}));
 alert(err.detail || 'Duplicate failed');
 }
 } catch (e: any) {
 alert(e?.message || 'Duplicate failed');
 } finally {
 dupBusy = { ...dupBusy, [p.slug]: false };
 }
 }

 function exportProject(p: any) {
 // GET /api/projects/{slug}/export returns a ZIP — trigger browser download
 const tok = localStorage.getItem('dash_token') || '';
 const url = `/api/projects/${p.slug}/export`;
 // Use fetch + blob to honor Authorization header
 fetch(url, { headers: { Authorization: `Bearer ${tok}` } })
 .then((r) => { if (!r.ok) throw new Error('Export failed'); return r.blob(); })
 .then((blob) => {
 const a = document.createElement('a');
 a.href = URL.createObjectURL(blob);
 a.download = `${p.slug}.zip`;
 document.body.appendChild(a); a.click();
 document.body.removeChild(a);
 URL.revokeObjectURL(a.href);
 })
 .catch((e) => alert(e?.message || 'Export failed'));
 }

 // Create modal
 let showCreate = $state(false);
 let cName = $state('');
 let cAgent = $state('');
 let cRole = $state('');
 let cPersonality = $state('friendly');
 let cIcon = $state('');
 let creating = $state(false);
 let createError = $state('');
 let createSteps = $state<{label: string; status: 'pending'|'done'|'error'}[]>([]);
 let createResult = $state<any>(null);
 let fileInput: HTMLInputElement;
 let selectedFile = $state<File | null>(null);

 // Industry template picker UI (library modal + onboarding wizard) removed.
 // Project creation now goes straight to settings page after create.

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(() => {
 loadProjects(); loadShared();
 const onKey = (e: KeyboardEvent) => {
 if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
 e.preventDefault();
 searchInput?.focus();
 } else if (e.key === 'Escape' && document.activeElement === searchInput) {
 query = '';
 }
 };
 window.addEventListener('keydown', onKey);
 return () => window.removeEventListener('keydown', onKey);
 });

 async function loadShared() {
 try { const res = await fetch('/api/projects/shared', { headers: _h() }); if (res.ok) { const d = await res.json(); sharedProjects = d.projects || []; } } catch {}
 }

 async function toggleFavorite(slug: string) {
 try { await fetch(`/api/projects/${slug}/favorite`, { method: 'POST', headers: _h() }); await loadProjects(); } catch {}
 }

 const allProjects = $derived(() => {
 if (tab === 'mine') return projects;
 if (tab === 'fav') return projects.filter(p => p.is_favorite);
 if (tab === 'shared') return sharedProjects;
 return [...projects, ...sharedProjects];
 });

 const filtered = $derived(() => {
 const list = allProjects();
 const q = query.trim().toLowerCase();
 if (!q) return list;
 return list.filter(p => {
 const name = (p.name || '').toLowerCase();
 const agent = (p.agent_name || '').toLowerCase();
 const role = (p.agent_role || '').toLowerCase();
 return name.includes(q) || agent.includes(q) || role.includes(q);
 });
 });

 const totalTables = $derived(projects.reduce((s, p) => s + (p.tables || 0), 0));
 const lastSyncLabel = $derived.by(() => {
 if (!projects.length) return 'just now';
 const ts = projects.map(p => p.updated_at).filter(Boolean).sort().reverse()[0];
 return ts ? relTime(ts) : 'just now';
 });

 async function loadProjects() {
 loading = true;
 try {
 const res = await fetch('/api/projects', { headers: _h() });
 if (res.ok) { const d = await res.json(); projects = d.projects || []; }
 } catch {}
 loading = false;
 }

 function openCreate() {
 showCreate = true; cName = ''; cAgent = ''; cRole = ''; cPersonality = 'friendly';
 createError = ''; createSteps = []; createResult = null; selectedFile = null;
 }

 function exportAll() {
 try {
 const rows = [['name','agent','role','tables','rows','updated_at','last_trained']];
 for (const p of [...projects, ...sharedProjects]) {
 rows.push([p.name, p.agent_name, p.agent_role || '', String(p.tables || 0), String(p.rows || 0), p.updated_at || '', p.last_trained || '']);
 }
 const csv = rows.map(r => r.map(c => `"${(c || '').replace(/"/g, '""')}"`).join(',')).join('\n');
 const blob = new Blob([csv], { type: 'text/csv' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url; a.download = 'projects.csv'; a.click();
 URL.revokeObjectURL(url);
 } catch {}
 }

 async function doCreate() {
 if (!cName || !cAgent) { createError = 'Name and agent name required'; return; }
 creating = true; createError = ''; createResult = null;

 createSteps = [
 { label: 'Creating project schema', status: 'pending' },
 { label: 'Configuring agent persona', status: 'pending' },
 { label: 'Initializing knowledge base', status: 'pending' },
 ];
 if (selectedFile) createSteps.push(
 { label: `Loading ${selectedFile.name}`, status: 'pending' },
 { label: 'Generating metadata', status: 'pending' },
 );
 createSteps.push({ label: 'Agent ready', status: 'pending' });

 for (let i = 0; i < 2; i++) {
 createSteps = createSteps.map((s, idx) => idx === i ? { ...s, status: 'done' as const } : s);
 await new Promise(r => setTimeout(r, 300));
 }

 try {
 const params = new URLSearchParams({ name: cName, agent_name: cAgent, agent_role: cRole, agent_personality: cPersonality });
 const res = await fetch(`/api/projects?${params}`, { method: 'POST', headers: _h() });
 if (!res.ok) { const e = await res.json().catch(() => ({ detail: 'Failed' })); throw new Error(e.detail); }
 const data = await res.json();

 createSteps = createSteps.map((s, idx) => idx === 2 ? { ...s, status: 'done' as const } : s);
 await new Promise(r => setTimeout(r, 200));

 if (selectedFile && data.slug) {
 const ext = selectedFile.name.split('.').pop()?.toLowerCase() || '';
 const isData = ['csv', 'xlsx', 'xls', 'json', 'parquet'].includes(ext);
 const fd = new FormData();
 fd.append('file', selectedFile);
 let upOk = false;
 if (isData) {
 // Staged pipeline: stage → auto-promote (validated, governed, one canonical table).
 const sres = await fetch(`/api/upload/stage?project=${data.slug}`, { method: 'POST', body: fd, headers: _h() });
 if (sres.ok) {
 const sd = await sres.json();
 try { const pr = await fetch(`/api/ingest/${data.slug}/${sd.batch_id}/promote`, { method: 'POST', headers: _h() }); upOk = pr.ok; } catch {}
 }
 } else {
 // Documents keep the document path.
 const upRes = await fetch(`/api/upload?project=${data.slug}`, { method: 'POST', body: fd, headers: _h() });
 upOk = upRes.ok;
 }
 if (upOk) {
 for (let i = 3; i < createSteps.length - 1; i++) {
 createSteps = createSteps.map((s, idx) => idx === i ? { ...s, status: 'done' as const } : s);
 await new Promise(r => setTimeout(r, 200));
 }
 }
 }

 createSteps = createSteps.map(s => ({ ...s, status: 'done' as const }));
 createResult = data;
 await loadProjects();
 if (data?.slug) {
 creating = false;
 // Auto-Config detects vertical post-train. Skip onboarding wizard.
 showCreate = false;
 window.location.href = `/ui/project/${data.slug}/settings`;
 return;
 }
 } catch (e: any) {
 createError = e.message;
 createSteps = createSteps.map(s => s.status === 'pending' ? { ...s, status: 'error' as const } : s);
 }
 creating = false;
 }

 // Share
 let showShare = $state(false);
 let shareSlug = $state('');
 let shareUsername = $state('');
 let shareResult = $state('');

 function openShare(slug: string) { shareSlug = slug; shareUsername = ''; shareResult = ''; showShare = true; }

 async function doShare() {
 if (!shareUsername) return;
 try {
 const res = await fetch(`/api/projects/${shareSlug}/share?username=${encodeURIComponent(shareUsername)}`, { method: 'POST', headers: _h() });
 if (res.ok) { const d = await res.json(); shareResult = d.status === 'already_shared' ? 'Already shared' : 'Shared!'; }
 else { const e = await res.json(); shareResult = e.detail || 'Failed'; }
 } catch { shareResult = 'Error'; }
 }

 async function deleteProject(slug: string, name: string) {
 try {
 await fetch(`/api/projects/${slug}`, { method: 'DELETE', headers: _h() });
 await loadProjects();
 } catch {}
 }

 function relTime(ts: string | null | undefined): string {
 if (!ts) return '';
 try {
 const d = new Date(ts);
 const diff = Date.now() - d.getTime();
 if (diff < 60000) return 'just now';
 if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
 if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
 if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
 return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
 } catch { return ''; }
 }
</script>

<svelte:window onclick={() => { if (menuOpen) menuOpen = null; }} />

<div class="ds-page proj-page">
  <div class="ds-page-head proj-head">
    <div class="proj-title-block">
      <h1 class="ds-page-title">Your projects</h1>
      <p class="ds-page-sub proj-meta">{projects.length} agents · {totalTables} tables · last sync {lastSyncLabel}</p>
    </div>

    <div class="proj-controls">
      <div class="proj-search">
        <span class="proj-search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input
          bind:this={searchInput}
          class="ds-input proj-search-input"
          type="text"
          placeholder="Search projects, tables…"
          bind:value={query}
        />
        <span class="proj-search-kbd">K</span>
      </div>

      <div class="proj-filter-pills">
        <button class="pill-segment" class:active={tab==='all'} onclick={() => tab='all'}>All</button>
        <button class="pill-segment" class:active={tab==='mine'} onclick={() => tab='mine'}>Mine</button>
        <button class="pill-segment" class:active={tab==='fav'} onclick={() => tab='fav'}>Favorites</button>
        <button class="pill-segment" class:active={tab==='shared'} onclick={() => tab='shared'}>Shared</button>
      </div>

      <button class="btn-ghost" onclick={exportAll} title="Export as CSV">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Export
      </button>
      <button class="btn-primary" onclick={openCreate}>+ New agent</button>
      <button class="btn-primary" onclick={() => templateGalleryOpen = true}>+ New Agent from Template</button>
    </div>
  </div>

  {#if loading}
    <div class="ds-empty">
      <div class="ds-empty-text">Loading…</div>
    </div>
  {:else if filtered().length === 0}
    <div class="ds-empty">
      <div class="ds-empty-icon">∅</div>
      {#if query}
        <div class="ds-empty-title">No projects match "{query}"</div>
        <div class="ds-empty-text">Try a shorter query</div>
      {:else if projects.length === 0 && sharedProjects.length === 0}
        <div class="ds-empty-title">No projects yet</div>
        <div class="ds-empty-text">Create your first agent — vertical auto-detected after training</div>
      {:else}
        <div class="ds-empty-title">Nothing here</div>
        <div class="ds-empty-text">Switch tab or clear filters to see more</div>
      {/if}
      <div class="proj-empty-actions">
        <button class="btn-primary" onclick={openCreate}>+ New agent</button>
        <button class="btn-primary" onclick={() => templateGalleryOpen = true}>+ New Agent from Template</button>
      </div>
    </div>
  {:else}
    <div class="proj-grid">
      {#each filtered() as p (p.slug)}
        <div class="ds-card ds-card-hover proj-card">
          {#if !p.shared_by}
            <button class="proj-kebab" aria-label="More actions" aria-expanded={menuOpen === p.slug}
                    onclick={(e) => { e.stopPropagation(); menuOpen = menuOpen === p.slug ? null : p.slug; }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>
            </button>
          {/if}

          <div class="proj-card-head">
            <div class="proj-card-icon">{(p.agent_name || p.name || 'P')[0]?.toUpperCase()}</div>
            <div class="proj-card-title">
              <h3>
                {#if p.is_favorite}<span class="proj-star-inline" title="Starred"><Icon name="star" size={14} /></span>{/if}
                {p.agent_name || p.name}
              </h3>
              <p class="proj-card-cat">{p.shared_by ? `Shared by ${p.shared_by}` : 'General'} · {p.tables || 0} tables</p>
            </div>
          </div>

          {#if p.agent_role}
            <p class="proj-card-desc">{p.agent_role}</p>
          {/if}

          {#if (p.tables || 0) > 0}
            <div class="proj-card-progress">
              <div class="proj-card-progress-row">
                <span class="proj-card-progress-label">Pipeline progress</span>
                <span class="proj-card-progress-val">{p.last_trained ? '100%' : '45%'}</span>
              </div>
              <div class="proj-card-progress-track">
                <div class="proj-card-progress-fill" style="width: {p.last_trained ? 100 : 45}%"></div>
              </div>
            </div>
          {/if}

          <div class="proj-card-status">
            <span class="proj-card-status-dot {p.last_trained ? 'trained' : 'untrained'}"></span>
            <span>{p.last_trained ? `Trained · ${relTime(p.last_trained)}` : 'Not trained'}</span>
          </div>

          <button class="proj-chat-cta" onclick={() => goto(`${base}/project/${p.slug}`)}>
            <span class="proj-chat-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            </span>
            <span class="proj-chat-label">Open chat</span>
            <span class="proj-chat-arrow">→</span>
          </button>

          {#if menuOpen === p.slug}
            <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
            <div class="proj-menu" onclick={(e) => e.stopPropagation()}>
              <button class="proj-menu-item" onclick={() => { toggleFavorite(p.slug); menuOpen = null; }}>
                <span class="proj-menu-icon">{p.is_favorite ? '' : ''}</span>{p.is_favorite ? 'Unstar' : 'Star'}
              </button>
              <button class="proj-menu-item" onclick={() => { goto(`${base}/project/${p.slug}/settings`); menuOpen = null; }}>
                <span class="proj-menu-icon"><Icon name="settings" size={14} /></span>Settings
              </button>
              <button class="proj-menu-item" onclick={() => { renameTarget = p; renameNew = p.name; menuOpen = null; }}>
                <span class="proj-menu-icon"><Icon name="pencil" size={14} /></span>Rename
              </button>
              <button class="proj-menu-item" onclick={() => { duplicateProject(p); menuOpen = null; }} disabled={dupBusy[p.slug]}>
                <span class="proj-menu-icon">⊞</span>{dupBusy[p.slug] ? 'Duplicating…' : 'Duplicate'}
              </button>
              <button class="proj-menu-item" onclick={() => { exportProject(p); menuOpen = null; }}>
                <span class="proj-menu-icon">↓</span>Export
              </button>
              <button class="proj-menu-item" onclick={() => { openShare(p.slug); menuOpen = null; }}>
                <span class="proj-menu-icon">↗</span>Share
              </button>
              <div class="proj-menu-sep"></div>
              <button class="proj-menu-item" onclick={() => { archiveProject(p); menuOpen = null; }}>
                <span class="proj-menu-icon"><Icon name="folder" size={14} /></span>Archive
              </button>
              <button class="proj-menu-item danger" onclick={() => { deleteTarget = p; deleteTypedName = ''; menuOpen = null; }}>
                <span class="proj-menu-icon"><Icon name="trash" size={14} /></span>Delete
              </button>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<!-- ═══ CREATE MODAL ═══ -->
{#if showCreate}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget && !creating) showCreate = false; }}>
  <div class="ds-modal" style="max-width: 520px;">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Create new agent</h3>
      <button class="ds-modal-close" onclick={() => { if (!creating) showCreate = false; }}><Icon name="x" size={14} /></button>
    </div>
    <div class="ds-modal-body">
      <p class="proj-modal-sub">I'll become an expert on your data. Upload files and tell me what to focus on.</p>

      {#if !createResult}
        <div class="ds-field">
          <label class="ds-field-label">Agent name</label>
          <input class="ds-input" type="text" bind:value={cAgent} placeholder="Sales Agent" />
        </div>

        <div class="ds-field">
          <label class="ds-field-label">Project name</label>
          <input class="ds-input" type="text" bind:value={cName} placeholder="Sales analysis" />
        </div>

        <div class="ds-field">
          <label class="ds-field-label">Expertise / focus</label>
          <input class="ds-input" type="text" bind:value={cRole} placeholder="Revenue, pipeline, forecasting…" />
        </div>

        <div class="ds-field">
          <label class="ds-field-label">Personality</label>
          <div class="proj-personality">
            {#each [['friendly', 'Friendly'], ['formal', 'Formal'], ['technical', 'Technical']] as [val, label]}
              <button class="pill-segment" class:active={cPersonality === val} onclick={() => cPersonality = val}>{label}</button>
            {/each}
          </div>
        </div>

        {#if createError}<div class="ds-field-error">{createError}</div>{/if}

        <button class="btn-primary" style="width:100%; margin-top:8px;" onclick={doCreate} disabled={creating}>
          {creating ? 'Creating…' : 'Create agent'}
        </button>
      {/if}

      {#if createSteps.length > 0}
        <div class="proj-steps">
          <div class="proj-steps-h">Onboarding</div>
          {#each createSteps as step}
            <div class="proj-step">
              {#if step.status === 'done'}<span class="proj-step-ok"><Icon name="check" size={14} /></span>
              {:else if step.status === 'error'}<span class="proj-step-err"><Icon name="x" size={14} /></span>
              {:else}<span class="proj-step-pending">○</span>{/if}
              <span>{step.label}</span>
            </div>
          {/each}
          {#if createResult}
            <a href="{base}/project/{createResult.slug}/settings" class="proj-cta proj-cta-block" style="text-decoration: none; text-align: center; margin-top: 10px; display: block;">
              Upload data & configure →
            </a>
          {/if}
        </div>
      {/if}
    </div>
  </div>
</div>
{/if}

<!-- Share modal -->
{#if showShare}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget) showShare = false; }}>
  <div class="ds-modal" style="max-width: 380px;">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Share project</h3>
      <button class="ds-modal-close" onclick={() => showShare = false}><Icon name="x" size={14} /></button>
    </div>
    <div class="ds-modal-body">
      <div class="ds-field">
        <label class="ds-field-label">Username</label>
        <input class="ds-input" type="text" bind:value={shareUsername} placeholder="e.g., john" />
      </div>
      {#if shareResult}
        <div style="font-size: 11px; margin-bottom: 8px; color: {shareResult === 'Shared!' ? 'var(--ds-success)' : 'var(--ds-danger)'};">{shareResult}</div>
      {/if}
      <button class="btn-primary" style="width:100%;" onclick={doShare}>Share</button>
    </div>
  </div>
</div>
{/if}

<!-- Rename modal -->
{#if renameTarget}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget && !renameBusy) renameTarget = null; }}>
  <div class="ds-modal" style="max-width: 440px;">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Rename project</h3>
      <button class="ds-modal-close" onclick={() => { if (!renameBusy) renameTarget = null; }}><Icon name="x" size={14} /></button>
    </div>
    <div class="ds-modal-body">
      <div class="ds-field">
        <label class="ds-field-label">New name</label>
        <input class="ds-input" type="text" bind:value={renameNew} placeholder="Enter new name…" autofocus />
      </div>
    </div>
    <div class="ds-modal-foot">
      <button class="btn-secondary" onclick={() => { if (!renameBusy) renameTarget = null; }} disabled={renameBusy}>Cancel</button>
      <button class="btn-primary" onclick={submitRename} disabled={renameBusy || !renameNew || renameNew.length < 2}>{renameBusy ? 'Saving…' : 'Rename'}</button>
    </div>
  </div>
</div>
{/if}

<!-- Template gallery + new agent form -->
<TemplateGalleryModal
  open={templateGalleryOpen}
  slug=""
  onclose={() => templateGalleryOpen = false}
  onselect={(tpl) => { selectedTemplate = tpl; templateGalleryOpen = false; newAgentFormOpen = true; }}
/>
{#if newAgentFormOpen}
  <NewAgentForm
    slug=""
    template={selectedTemplate}
    onsave={() => { newAgentFormOpen = false; selectedTemplate = null; loadProjects(); }}
    oncancel={() => { newAgentFormOpen = false; selectedTemplate = null; }}
  />
{/if}

<!-- Delete confirmation modal -->
{#if deleteTarget}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ds-modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget) deleteTarget = null; }}>
  <div class="ds-modal" style="max-width: 420px;">
    <div class="ds-modal-head">
      <h3 class="ds-modal-title">Delete project</h3>
      <button class="ds-modal-close" onclick={() => deleteTarget = null}><Icon name="x" size={14} /></button>
    </div>
    <div class="ds-modal-body">
      <p style="font-size: 12px; line-height: 1.5; margin: 0 0 14px;">
        This will permanently delete <strong>"{deleteTarget.name}"</strong> and ALL its data, tables, training, and knowledge. This cannot be undone.
      </p>
      <div class="ds-field">
        <label class="ds-field-label">Type the project name to confirm</label>
        <input class="ds-input" type="text" bind:value={deleteTypedName} placeholder={deleteTarget.name} />
      </div>
    </div>
    <div class="ds-modal-foot">
      <button class="btn-ghost" onclick={() => deleteTarget = null}>Cancel</button>
      <button
        class="btn-danger"
        disabled={deleteTypedName !== deleteTarget.name}
        onclick={async () => {
          await deleteProject(deleteTarget.slug, deleteTarget.name);
          deleteTarget = null; deleteTypedName = '';
        }}
      >Delete permanently</button>
    </div>
  </div>
</div>
{/if}

<style>
 .proj-page {
 max-width: 1280px;
 margin: 0 auto;
 padding: 32px 32px 64px;
 font-family: var(--pw-font-body);
 }

 .proj-head {
 display: flex;
 flex-wrap: wrap;
 align-items: flex-end;
 justify-content: space-between;
 gap: 24px;
 margin-bottom: 32px;
 }

 .proj-title-block {
 flex: 0 0 auto;
 }

 .proj-h1 {
 font-family: var(--pw-font-headline);
 font-size: 40px;
 font-weight: 500;
 letter-spacing: -0.025em;
 line-height: 1.1;
 margin: 0 0 8px;
 color: var(--pw-ink);
 }

 .proj-meta {
 font-size: 12px;
 color: var(--pw-muted);
 margin: 0;
 display: flex;
 align-items: center;
 gap: 6px;
 }

 .proj-meta::before {
 content: '';
 width: 6px;
 height: 6px;
 border-radius: 50%;
 background: var(--pw-success);
 animation: proj-pulse 2s ease-in-out infinite;
 }

 @keyframes proj-pulse {
 0%, 100% { box-shadow: 0 0 0 0 rgba(45,106,79,0.5); }
 50% { box-shadow: 0 0 0 5px rgba(45,106,79,0); }
 }

 .proj-controls {
 display: flex;
 align-items: center;
 gap: 10px;
 flex-wrap: wrap;
 }

 .proj-search {
 position: relative;
 width: 280px;
 }
 .proj-search-input {
 width: 100%;
 padding: 9px 50px 9px 32px;
 border: 1px solid var(--pw-border-strong);
 border-radius: var(--pw-radius-pill);
 background: var(--pw-surface);
 font-family: inherit;
 font-size: 13.5px;
 color: var(--pw-ink);
 outline: none;
 transition: border-color .15s, box-shadow .15s;
 }
 .proj-search-input:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-bg);
 }
 .proj-search-input::placeholder {
 color: var(--pw-dim);
 }
 .proj-search-icon {
 position: absolute;
 left: 12px;
 top: 50%;
 transform: translateY(-50%);
 color: var(--pw-muted);
 pointer-events: none;
 display: flex;
 }
 .proj-search-kbd {
 position: absolute;
 right: 10px;
 top: 50%;
 transform: translateY(-50%);
 font-size: 10.5px;
 color: var(--pw-muted);
 background: var(--pw-bg-alt);
 padding: 2px 6px;
 border-radius: 0;
 font-family: ui-monospace, monospace;
 }

 .proj-filter-pills {
 display: inline-flex;
 background: var(--pw-bg-alt);
 border-radius: var(--pw-radius-pill);
 padding: 3px;
 }
 .proj-filter-pills button {
 padding: 7px 14px;
 border: none;
 background: transparent;
 color: var(--pw-muted);
 font-family: inherit;
 font-size: 12px;
 font-weight: 500;
 border-radius: var(--pw-radius-pill);
 cursor: pointer;
 transition: all .15s;
 }
 .proj-filter-pills button:hover {
 color: var(--pw-ink);
 }
 .proj-filter-pills button.active {
 background: var(--pw-surface);
 color: var(--pw-ink);
 box-shadow: var(--pw-shadow-sm);
 }

 .proj-ghost-btn {
 padding: 8px 14px;
 border: 1px solid var(--pw-border-strong);
 border-radius: var(--pw-radius-pill);
 background: var(--pw-surface);
 color: var(--pw-ink);
 font-family: inherit;
 font-size: 12px;
 font-weight: 500;
 cursor: pointer;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 transition: background .15s, border-color .15s;
 }
 .proj-ghost-btn:hover {
 background: var(--pw-surface-warm, var(--pw-bg-alt));
 border-color: var(--pw-dim);
 }
 .proj-ghost-btn:disabled {
 opacity: 0.5;
 cursor: not-allowed;
 }

 .proj-cta {
 padding: 9px 18px;
 background: var(--pw-accent);
 color: #fff;
 border: 1px solid var(--pw-accent);
 border-radius: var(--pw-radius-pill);
 font-family: inherit;
 font-size: 12px;
 font-weight: 500;
 cursor: pointer;
 transition: background .15s;
 }
 .proj-cta:hover {
 background: var(--pw-accent-ink);
 }
 .proj-cta:disabled {
 opacity: 0.5;
 cursor: not-allowed;
 }
 .proj-cta-block {
 width: 100%;
 padding: 11px 18px;
 border-radius: var(--pw-radius-button, 10px);
 }

 .proj-grid {
 display: grid;
 grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
 gap: 24px;
 }

 .proj-card {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius, 16px);
 padding: 24px;
 display: flex;
 flex-direction: column;
 gap: 14px;
 box-shadow: var(--pw-shadow-sm);
 transition: box-shadow .15s, transform .15s, border-color .15s;
 }
 .proj-card:hover {
 box-shadow: var(--pw-shadow-md);
 transform: translateY(-2px);
 border-color: var(--pw-border-strong);
 }

 .proj-card-head {
 display: flex;
 align-items: flex-start;
 gap: 12px;
 }

 .proj-card-icon {
 width: 36px;
 height: 36px;
 border-radius: 0;
 background: var(--pw-accent);
 color: #fff;
 display: grid;
 place-items: center;
 font-family: var(--pw-font-headline);
 font-weight: 600;
 font-size: 14px;
 flex: 0 0 auto;
 }

 .proj-card-title {
 flex: 1;
 min-width: 0;
 }
 .proj-card-title h3 {
 font-family: var(--pw-font-headline);
 font-size: 15px;
 font-weight: 500;
 letter-spacing: -0.015em;
 margin: 0 0 2px;
 color: var(--pw-ink);
 text-transform: none;
 white-space: nowrap;
 overflow: hidden;
 text-overflow: ellipsis;
 }
 .proj-card-cat {
 font-size: 12.5px;
 color: var(--pw-muted);
 margin: 0;
 }

 .proj-card-actions-top {
 display: flex;
 gap: 4px;
 flex: 0 0 auto;
 }
 .proj-icon-btn {
 width: 28px;
 height: 28px;
 border: none;
 background: transparent;
 color: var(--pw-muted);
 border-radius: 0;
 cursor: pointer;
 display: grid;
 place-items: center;
 transition: background .15s, color .15s;
 }
 .proj-icon-btn:hover {
 background: var(--pw-bg-alt);
 color: var(--pw-ink);
 }

 .proj-card-desc {
 font-size: 13.5px;
 color: var(--pw-ink-soft);
 line-height: 1.5;
 margin: 0;
 display: -webkit-box;
 -webkit-line-clamp: 2;
 -webkit-box-orient: vertical;
 overflow: hidden;
 }

 .proj-card-progress-row {
 display: flex;
 justify-content: space-between;
 font-size: 11px;
 margin-bottom: 4px;
 }
 .proj-card-progress-label { color: var(--pw-muted); }
 .proj-card-progress-val { color: var(--pw-ink); font-weight: 500; }
 .proj-card-progress-track {
 height: 6px;
 background: var(--pw-bg-alt);
 border-radius: 0;
 overflow: hidden;
 }
 .proj-card-progress-fill {
 height: 100%;
 background: var(--pw-accent);
 border-radius: 0;
 transition: width .3s ease;
 }

 .proj-card-status {
 display: flex;
 align-items: center;
 gap: 8px;
 font-size: 12.5px;
 color: var(--pw-muted);
 }
 .proj-card-status-dot {
 width: 7px;
 height: 7px;
 border-radius: 50%;
 }
 .proj-card-status-dot.trained { background: var(--pw-success); }
 .proj-card-status-dot.untrained { background: var(--pw-dim); }

 /* Card relative positioning so kebab + menu anchor correctly */
 .proj-card { position: relative; overflow: visible; }
 .proj-card:has(.proj-menu) { z-index: 100; }

 .proj-kebab {
 position: absolute; top: 12px; right: 12px;
 width: 28px; height: 28px;
 display: inline-flex; align-items: center; justify-content: center;
 background: transparent;
 border: 1px solid transparent;
 border-radius: 0;
 color: var(--pw-muted);
 cursor: pointer;
 transition: background .12s, color .12s, border-color .12s;
 z-index: 1;
 }
 .proj-card:hover .proj-kebab,
 .proj-kebab:hover,
 .proj-kebab[aria-expanded="true"] {
 background: var(--pw-bg-alt);
 border-color: var(--pw-border);
 color: var(--pw-ink);
 }

 /* Chat CTA bottom row */
 .proj-chat-cta {
 display: flex; align-items: center; gap: 10px;
 width: 100%;
 margin: 16px -20px -20px; /* extend to card edges */
 width: calc(100% + 40px);
 padding: 12px 20px;
 background: transparent;
 border: none;
 border-top: 1px solid var(--pw-border-soft);
 border-radius: 0;
 font: inherit;
 font-size: 12px;
 font-weight: 500;
 color: var(--pw-accent);
 cursor: pointer;
 transition: background .12s;
 margin-top: auto;
 }
 .proj-chat-cta:hover { background: var(--pw-accent-wash); }
 .proj-chat-cta:hover .proj-chat-arrow { transform: translateX(2px); }
 .proj-chat-icon { display: inline-flex; align-items: center; }
 .proj-chat-label { flex: 1; text-align: left; }
 .proj-chat-arrow { transition: transform .15s; opacity: .75; }

 /* Kebab menu */
 .proj-menu {
 position: absolute; top: 44px; right: 12px;
 min-width: 200px;
 background: var(--pw-surface, #fff);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 box-shadow: 0 12px 32px rgba(0,0,0,0.12);
 padding: 6px;
 z-index: 50;
 }
 .proj-menu-item {
 display: flex; align-items: center; gap: 10px;
 width: 100%; padding: 8px 12px;
 background: none; border: none;
 border-radius: 0;
 font: inherit; font-size: 12px;
 color: var(--pw-ink); text-align: left;
 cursor: pointer;
 }
 .proj-menu-item:hover:not(:disabled) { background: var(--pw-bg-alt); }
 .proj-menu-item:disabled { opacity: 0.5; cursor: not-allowed; }
 .proj-menu-item.danger { color: #b91c1c; }
 .proj-menu-item.danger:hover { background: #fef2f2; }
 .proj-menu-icon {
 width: 18px; display: inline-flex; justify-content: center;
 font-size: 12px; opacity: 0.85;
 }
 .proj-menu-sep { height: 1px; background: var(--pw-border-soft); margin: 4px 6px; }

 .proj-star-inline {
 display: inline-block;
 color: var(--pw-accent);
 font-size: 12px;
 margin-right: 6px;
 vertical-align: middle;
 }
 .proj-cta-primary {
 flex: 1;
 height: 38px;
 padding: 0 16px;
 background: var(--pw-accent-soft, #d97757);
 color: #fff;
 border: 1px solid var(--pw-accent-soft, #d97757);
 border-radius: 0;
 font-family: inherit;
 font-size: 12px;
 font-weight: 500;
 letter-spacing: 0;
 text-transform: none;
 cursor: pointer;
 transition: background .15s, border-color .15s;
 }
 .proj-cta-primary:hover {
 background: var(--pw-accent, #c96342);
 border-color: var(--pw-accent, #c96342);
 }
 .proj-cta-ghost {
 padding: 10px 16px;
 background: transparent;
 color: var(--pw-ink);
 border: 1px solid var(--pw-border-strong);
 border-radius: var(--pw-radius-button, 10px);
 font-family: inherit;
 font-size: 13.5px;
 font-weight: 500;
 cursor: pointer;
 transition: background .15s;
 }
 .proj-cta-ghost:hover {
 background: var(--pw-bg-alt);
 }

 /* Empty state */
 .proj-empty {
 background: var(--pw-surface);
 border: 1px dashed var(--pw-border-strong);
 border-radius: var(--pw-radius, 16px);
 padding: 64px 32px;
 text-align: center;
 }
 .proj-empty-h {
 font-family: var(--pw-font-headline);
 font-size: 18px;
 font-weight: 500;
 color: var(--pw-ink);
 margin: 0 0 6px;
 }
 .proj-empty-sub {
 font-size: 13.5px;
 color: var(--pw-muted);
 margin: 0 0 18px;
 }
 .proj-empty-actions {
 display: flex;
 gap: 10px;
 justify-content: center;
 }

 /* Modals */
 .proj-modal-backdrop {
 position: fixed;
 inset: 0;
 background: rgba(44, 44, 44, 0.45);
 z-index: 100;
 display: flex;
 align-items: center;
 justify-content: center;
 padding: 24px;
 }
 .proj-modal {
 background: var(--pw-surface);
 width: 100%;
 max-height: 92vh;
 border-radius: var(--pw-radius, 16px);
 box-shadow: var(--pw-shadow-lg, 0 16px 48px rgba(0,0,0,0.18));
 overflow: hidden;
 display: flex;
 flex-direction: column;
 }
 .proj-modal-head {
 display: flex;
 align-items: center;
 justify-content: space-between;
 padding: 14px 18px;
 border-bottom: 1px solid var(--pw-border);
 font-family: var(--pw-font-headline);
 font-size: 14px;
 font-weight: 500;
 color: var(--pw-ink);
 background: var(--pw-surface);
 }
 .proj-modal-close {
 background: none;
 border: none;
 color: var(--pw-muted);
 cursor: pointer;
 font-size: 14px;
 padding: 0;
 width: 24px;
 height: 24px;
 display: grid;
 place-items: center;
 }
 .proj-modal-close:hover { color: var(--pw-ink); }
 .proj-modal-body {
 padding: 24px;
 overflow-y: auto;
 }
 .proj-modal-sub {
 font-size: 12px;
 color: var(--pw-muted);
 margin: 0 0 16px;
 }
 .proj-modal-foot {
 display: flex;
 justify-content: space-between;
 align-items: center;
 margin-top: 18px;
 gap: 8px;
 }
 .proj-modal-hint {
 font-size: 11px;
 color: var(--pw-muted);
 }

 .proj-field {
 margin-bottom: 14px;
 }
 .proj-field label {
 display: block;
 font-size: 11px;
 color: var(--pw-muted);
 margin-bottom: 5px;
 font-weight: 500;
 }
 .proj-field input,
 .proj-input {
 width: 100%;
 padding: 9px 12px;
 border: 1px solid var(--pw-border-strong);
 border-radius: var(--pw-radius-button, 10px);
 background: var(--pw-surface);
 font-family: inherit;
 font-size: 13.5px;
 color: var(--pw-ink);
 outline: none;
 transition: border-color .15s, box-shadow .15s;
 }
 .proj-field input:focus,
 .proj-input:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-bg);
 }

 .proj-personality {
 display: flex;
 gap: 6px;
 }
 .proj-personality button {
 flex: 1;
 padding: 8px;
 font-family: inherit;
 font-size: 12.5px;
 border: 1px solid var(--pw-border-strong);
 background: var(--pw-surface);
 color: var(--pw-ink);
 border-radius: var(--pw-radius-button, 10px);
 cursor: pointer;
 transition: all .15s;
 }
 .proj-personality button.active {
 background: var(--pw-accent);
 color: #fff;
 border-color: var(--pw-accent);
 }

 .proj-steps {
 margin-top: 16px;
 padding: 14px;
 background: var(--pw-bg-alt);
 border-left: 3px solid var(--pw-accent);
 border-radius: 0;
 }
 .proj-steps-h {
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-muted);
 margin-bottom: 8px;
 font-weight: 500;
 }
 .proj-step {
 display: flex;
 align-items: center;
 gap: 8px;
 font-size: 12.5px;
 padding: 3px 0;
 color: var(--pw-ink);
 }
 .proj-step-ok { color: var(--pw-success); }
 .proj-step-err { color: var(--pw-error); }
 .proj-step-pending { color: var(--pw-muted); }

 .proj-summary {
 border: 1px solid var(--pw-border);
 border-radius: var(--pw-radius-button, 10px);
 padding: 12px 14px;
 background: var(--pw-bg-alt);
 margin-bottom: 12px;
 }
 .proj-summary-row {
 display: flex;
 justify-content: space-between;
 font-size: 12px;
 padding: 4px 0;
 color: var(--pw-ink);
 }
 .proj-summary-row span:first-child {
 color: var(--pw-muted);
 }

 @media (max-width: 900px) {
 .proj-page { padding: 24px 18px 48px; }
 .proj-h1 { font-size: 28px; }
 .proj-head {
 flex-direction: column;
 align-items: stretch;
 }
 .proj-controls {
 flex-wrap: wrap;
 }
 .proj-search {
 width: 100%;
 flex: 1 1 100%;
 }
 .proj-grid {
 grid-template-columns: 1fr;
 }
 }
</style>
