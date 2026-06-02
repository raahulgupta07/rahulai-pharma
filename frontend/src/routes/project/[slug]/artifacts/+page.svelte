<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';
  import { confirmDelete } from '$lib/confirmDelete';

  const slug = $derived($page.params.slug);

  let token = $state('');
  let loading = $state(false);
  let err = $state('');

  // Filters
  let runIdFilter = $state('');
  let kindFilter = $state<'' | 'csv' | 'png' | 'json' | 'pdf' | 'md' | 'pptx' | 'xlsx' | 'docx' | 'svg' | 'html' | 'other'>('');
  let limit = $state(50);
  let offset = $state(0);
  let total = $state(0);

  let items = $state<any[]>([]);

  function _h(): Record<string, string> {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function load() {
    if (!slug) return;
    loading = true; err = '';
    try {
      const params = new URLSearchParams({
        project: slug,
        limit: String(limit),
        offset: String(offset),
      });
      if (runIdFilter.trim()) params.set('run_id', runIdFilter.trim());
      if (kindFilter) params.set('kind', kindFilter);
      const r = await fetch(`/api/artifacts/?${params}`, { headers: _h() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || 'load failed');
      items = d.artifacts || [];
      total = d.total ?? items.length;
    } catch (e: any) {
      err = e.message;
      items = [];
    } finally {
      loading = false;
    }
  }

  function applyFilters() {
    offset = 0;
    load();
  }

  function nextPage() {
    if (offset + limit < total) { offset += limit; load(); }
  }
  function prevPage() {
    if (offset >= limit) { offset -= limit; load(); }
  }

  function download(a: any) {
    const url = `/api/artifacts/${a.id}/download`;
    const tk = token ? `Bearer ${token}` : '';
    fetch(url, { headers: tk ? { Authorization: tk } : {} })
      .then(r => r.blob().then(b => ({ b, name: a.filename })))
      .then(({ b, name }) => {
        const u = URL.createObjectURL(b);
        const link = document.createElement('a');
        link.href = u;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(u);
      })
      .catch(e => { err = `download failed: ${e.message}`; });
  }

  async function remove(a: any) {
    if (!(await confirmDelete({ itemName: a.filename, itemType: 'artifact' }))) return;
    try {
      const r = await fetch(`/api/artifacts/${a.id}`, { method: 'DELETE', headers: _h() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'delete failed');
      load();
    } catch (e: any) {
      err = e.message;
    }
  }

  function fmtBytes(n: number | null | undefined): string {
    if (!n && n !== 0) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }

  function fmtDate(s: string | null | undefined): string {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return String(s); }
  }

  // Lucide-style icon per kind (inline SVG, stroke-width 1.8)
  function kindIcon(kind: string): string {
    const k = (kind || '').toLowerCase();
    if (['png', 'jpg', 'jpeg', 'svg'].includes(k)) {
      return `<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>`;
    }
    if (k === 'csv' || k === 'xlsx') {
      return `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="8" y1="9" x2="10" y2="9"/>`;
    }
    if (k === 'json') {
      return `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M10 13a2 2 0 0 1-2 2"/><path d="M14 13a2 2 0 0 0 2 2"/>`;
    }
    if (k === 'pdf') {
      return `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><text x="7" y="18" font-size="6" font-weight="700" stroke="none" fill="currentColor">PDF</text>`;
    }
    if (k === 'pptx') {
      return `<rect x="3" y="4" width="18" height="13" rx="1"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>`;
    }
    if (k === 'docx' || k === 'md' || k === 'txt' || k === 'html') {
      return `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>`;
    }
    return `<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>`;
  }

  const kindOptions = [
    { v: '', label: 'All kinds' },
    { v: 'csv', label: 'CSV' },
    { v: 'png', label: 'Image' },
    { v: 'json', label: 'JSON' },
    { v: 'pdf', label: 'PDF' },
    { v: 'md', label: 'Markdown' },
    { v: 'pptx', label: 'PPTX' },
    { v: 'xlsx', label: 'XLSX' },
    { v: 'docx', label: 'DOCX' },
    { v: 'svg', label: 'SVG' },
    { v: 'html', label: 'HTML' },
    { v: 'other', label: 'Other' },
  ];

  onMount(async () => {
    if (typeof localStorage !== 'undefined') token = localStorage.getItem('dash_token') || '';
    if (!token) { goto(`${base}/login`); return; }
    await load();
  });
</script>

<svelte:head><title>Artifacts · {slug}</title></svelte:head>

<div class="art-page">
  <header class="art-head">
    <a href={`${base}/project/${slug}/settings`} class="art-back" aria-label="Back to settings">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
    </a>
    <h1>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -3px; margin-right: 6px;"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>
      Artifacts
    </h1>
    <span class="art-sub">{slug} · {total} total</span>
    <div class="art-actions">
      <button class="art-btn" onclick={load} disabled={loading}>
        {loading ? 'Loading…' : 'Refresh'}
      </button>
    </div>
  </header>

  <section class="art-filters">
    <input
      type="text"
      placeholder="Filter by run_id…"
      bind:value={runIdFilter}
      onkeydown={(e) => { if (e.key === 'Enter') applyFilters(); }}
    />
    <select bind:value={kindFilter}>
      {#each kindOptions as o}
        <option value={o.v}>{o.label}</option>
      {/each}
    </select>
    <button class="art-btn art-btn-primary" onclick={applyFilters}>Apply</button>
    {#if runIdFilter || kindFilter}
      <button class="art-btn art-btn-ghost" onclick={() => { runIdFilter=''; kindFilter=''; applyFilters(); }}>Clear</button>
    {/if}
  </section>

  {#if err}
    <div class="art-err">{err}</div>
  {/if}

  {#if loading && items.length === 0}
    <div class="art-empty">Loading artifacts…</div>
  {:else if items.length === 0}
    <div class="art-empty">
      <div style="font-size: 30px; opacity: 0.4;">∅</div>
      <p>No artifacts yet for this project{runIdFilter ? ` and run_id "${runIdFilter}"` : ''}.</p>
      <p style="font-size: 11px; opacity: 0.7;">Runs (AutoML, reports, workflows) automatically register files here.</p>
    </div>
  {:else}
    <div class="art-grid">
      {#each items as a}
        <article class="art-tile">
          <div class="art-thumb">
            {#if a.thumbnail_b64}
              <img src={`data:image/png;base64,${a.thumbnail_b64}`} alt={a.filename} />
            {:else}
              <div class="art-thumb-icon">
                <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  {@html kindIcon(a.kind)}
                </svg>
                <span class="art-kind">{(a.kind || 'other').toUpperCase()}</span>
              </div>
            {/if}
          </div>
          <div class="art-meta">
            <div class="art-name" title={a.filename}>{a.filename}</div>
            <div class="art-meta-row">
              <span class="art-chip">{fmtBytes(a.size_bytes)}</span>
              {#if a.run_id}
                <span class="art-chip art-chip-run" title="run_id">#{a.run_id}</span>
              {/if}
            </div>
            <div class="art-date">{fmtDate(a.created_at)}</div>
          </div>
          <div class="art-tile-actions">
            <button class="art-btn art-btn-sm art-btn-primary" onclick={() => download(a)}>Download</button>
            <button class="art-btn art-btn-sm art-btn-ghost" style="color: #d33;" onclick={() => remove(a)} title="Delete">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"/></svg>
            </button>
          </div>
        </article>
      {/each}
    </div>

    <footer class="art-pager">
      <button class="art-btn art-btn-ghost" onclick={prevPage} disabled={offset === 0 || loading}>← Prev</button>
      <span class="art-pager-info">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <button class="art-btn art-btn-ghost" onclick={nextPage} disabled={offset + limit >= total || loading}>Next →</button>
    </footer>
  {/if}
</div>

<style>
  .art-page {
    min-height: 100vh;
    background: var(--pw-bg-alt, #f1ede4);
    color: var(--pw-ink, #2c2a26);
    padding: 28px 32px 80px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    max-width: 1400px;
    margin: 0 auto;
  }

  .art-head {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 22px;
  }
  .art-head h1 {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 18px;
    font-weight: 600;
    margin: 0;
    color: var(--pw-ink, #2c2a26);
  }
  .art-sub {
    color: rgba(44, 42, 38, 0.55);
    font-size: 11px;
    margin-left: 4px;
  }
  .art-actions { margin-left: auto; }
  .art-back {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px; height: 32px;
    border-radius: 0;
    border: 1px solid rgba(44, 42, 38, 0.15);
    background: #fff;
    color: var(--pw-ink, #2c2a26);
    text-decoration: none;
  }
  .art-back:hover { background: #fff; border-color: var(--pw-accent, #c96342); color: var(--pw-accent, #c96342); }

  .art-filters {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-bottom: 18px;
    flex-wrap: wrap;
  }
  .art-filters input, .art-filters select {
    padding: 8px 12px;
    border: 1px solid rgba(44, 42, 38, 0.15);
    background: #fff;
    color: var(--pw-ink, #2c2a26);
    border-radius: 0;
    font-size: 13px;
    font-family: inherit;
  }
  .art-filters input { min-width: 240px; }
  .art-filters input:focus, .art-filters select:focus {
    outline: none;
    border-color: var(--pw-accent, #c96342);
  }

  .art-btn {
    padding: 8px 14px;
    border-radius: 0;
    border: 1px solid rgba(44, 42, 38, 0.15);
    background: #fff;
    color: var(--pw-ink, #2c2a26);
    cursor: pointer;
    font-size: 11px;
    font-family: inherit;
    font-weight: 500;
    transition: all 0.15s;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .art-btn:hover:not(:disabled) {
    border-color: var(--pw-accent, #c96342);
    color: var(--pw-accent, #c96342);
  }
  .art-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .art-btn-primary {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }
  .art-btn-primary:hover:not(:disabled) {
    background: #b85638;
    color: #fff;
    border-color: #b85638;
  }
  .art-btn-ghost {
    background: transparent;
  }
  .art-btn-sm { padding: 5px 10px; font-size: 11px; }

  .art-err {
    background: #fff;
    border: 1px solid #d9534f;
    color: #d9534f;
    padding: 10px 14px;
    border-radius: 0;
    font-size: 11px;
    margin-bottom: 14px;
  }

  .art-empty {
    background: #fff;
    border: 1px dashed rgba(44, 42, 38, 0.18);
    padding: 60px 20px;
    text-align: center;
    border-radius: 0;
    color: rgba(44, 42, 38, 0.65);
  }
  .art-empty p { margin: 8px 0; }

  .art-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 16px;
  }

  .art-tile {
    background: #fff;
    border: 1px solid rgba(44, 42, 38, 0.1);
    border-radius: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    transition: all 0.15s;
  }
  .art-tile:hover {
    border-color: var(--pw-accent, #c96342);
    transform: translateY(-2px);
    box-shadow: 0 6px 18px rgba(44, 42, 38, 0.08);
  }

  .art-thumb {
    aspect-ratio: 4 / 3;
    background: var(--pw-bg-alt, #f1ede4);
    display: flex; align-items: center; justify-content: center;
    overflow: hidden;
    border-bottom: 1px solid rgba(44, 42, 38, 0.06);
  }
  .art-thumb img {
    width: 100%; height: 100%; object-fit: cover;
  }
  .art-thumb-icon {
    display: flex; flex-direction: column; align-items: center; gap: 8px;
    color: rgba(44, 42, 38, 0.4);
  }
  .art-kind {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--pw-accent, #c96342);
    background: rgba(201, 99, 66, 0.08);
    padding: 2px 8px;
    border-radius: 0;
  }

  .art-meta {
    padding: 12px 14px 8px;
    flex: 1;
  }
  .art-name {
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 6px;
  }
  .art-meta-row {
    display: flex; gap: 6px; flex-wrap: wrap;
    margin-bottom: 4px;
  }
  .art-chip {
    font-size: 10px;
    padding: 2px 7px;
    background: var(--pw-bg-alt, #f1ede4);
    border-radius: 0;
    color: rgba(44, 42, 38, 0.7);
  }
  .art-chip-run {
    background: rgba(201, 99, 66, 0.08);
    color: var(--pw-accent, #c96342);
    font-weight: 600;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .art-date {
    font-size: 11px;
    color: rgba(44, 42, 38, 0.5);
  }

  .art-tile-actions {
    padding: 8px 14px 12px;
    display: flex; gap: 6px;
    border-top: 1px solid rgba(44, 42, 38, 0.05);
  }
  .art-tile-actions .art-btn:first-child { flex: 1; justify-content: center; }

  .art-pager {
    display: flex; align-items: center; justify-content: center;
    gap: 14px; margin-top: 24px;
  }
  .art-pager-info {
    font-size: 11px;
    color: rgba(44, 42, 38, 0.6);
  }
</style>
