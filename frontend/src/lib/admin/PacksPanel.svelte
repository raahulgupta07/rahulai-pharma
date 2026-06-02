<script lang="ts">
  import { onMount } from 'svelte';

  type Pack = {
    id: string;
    name: string;
    version: string | null;
    author: string | null;
    source_path: string | null;
    created_at: string | null;
    description?: string;
    vertical?: string | null;
    skills?: string[];
    golden_qa?: any[];
    mdl_fragments?: string[];
    workflow_count?: number;
    model_count?: number;
    format?: string;
    enabled?: boolean;
    installed_at?: string | null;
    manifest?: Record<string, any>;
  };

  type Tab = 'available' | 'installed';

  let tab = $state<Tab>('available');
  let packs = $state<Pack[]>([]);
  let installed = $state<Pack[]>([]);
  let projectSlug = $state('');
  let projectInputs = $state<Record<string, string>>({});
  let loading = $state(false);
  let syncing = $state(false);
  let error = $state<string | null>(null);
  let toast = $state<string | null>(null);

  // manifest modal
  let manifestPack = $state<Pack | null>(null);
  let manifestLoading = $state(false);

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    const h: Record<string, string> = { Accept: 'application/json' };
    if (t) h['Authorization'] = `Bearer ${t}`;
    return h;
  }

  async function api(method: string, path: string, body?: any): Promise<any> {
    const opts: RequestInit = {
      method,
      headers: authHeaders()
    };
    if (body !== undefined) {
      opts.headers = { ...opts.headers, 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(path, opts);
    if (!r.ok) {
      const txt = await r.text().catch(() => '');
      throw new Error(`HTTP ${r.status}: ${txt || r.statusText}`);
    }
    return r.json();
  }

  function flash(msg: string) {
    toast = msg;
    setTimeout(() => { if (toast === msg) toast = null; }, 2500);
  }

  async function loadAvailable() {
    loading = true;
    error = null;
    try {
      const res = await api('GET', '/api/packs');
      packs = res.packs || [];
    } catch (e: any) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function loadInstalled() {
    if (!projectSlug.trim()) {
      installed = [];
      return;
    }
    loading = true;
    error = null;
    try {
      const slug = encodeURIComponent(projectSlug.trim());
      const res = await api('GET', `/api/packs/installed?project_slug=${slug}`);
      installed = res.installed || [];
    } catch (e: any) {
      error = e.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function syncFromDisk() {
    syncing = true;
    error = null;
    try {
      const res = await api('POST', '/api/packs/sync');
      flash(`Synced ${res.synced} pack(s)`);
      await loadAvailable();
    } catch (e: any) {
      error = e.message || String(e);
    } finally {
      syncing = false;
    }
  }

  async function installPack(pack: Pack) {
    const slug = (projectInputs[pack.id] || projectSlug || '').trim();
    if (!slug) {
      error = 'Project slug required (set the top input or per-row override)';
      return;
    }
    try {
      const res = await api('POST', `/api/packs/${pack.id}/install`,
                            { project_slug: slug });
      flash(`Installed "${pack.name}" → ${slug}`
            + (res.skills_registered ? ` (+${res.skills_registered} skill(s))` : ''));
      if (tab === 'installed' && projectSlug.trim() === slug) await loadInstalled();
    } catch (e: any) {
      error = e.message || String(e);
    }
  }

  async function uninstallPack(pack: Pack) {
    const slug = projectSlug.trim();
    if (!slug) {
      error = 'Project slug required';
      return;
    }
    if (!confirm(`Disable "${pack.name}" for ${slug}?`)) return;
    try {
      await api('POST', `/api/packs/${pack.id}/uninstall`,
                { project_slug: slug });
      flash(`Disabled "${pack.name}" for ${slug}`);
      await loadInstalled();
    } catch (e: any) {
      error = e.message || String(e);
    }
  }

  async function viewManifest(pack: Pack) {
    manifestLoading = true;
    manifestPack = pack;
    try {
      const detail = await api('GET', `/api/packs/${pack.id}`);
      manifestPack = { ...pack, ...detail };
    } catch (e: any) {
      error = e.message || String(e);
      manifestPack = null;
    } finally {
      manifestLoading = false;
    }
  }

  function closeManifest() { manifestPack = null; }

  function fmtJson(o: any): string {
    try { return JSON.stringify(o ?? {}, null, 2); }
    catch { return String(o); }
  }

  function switchTab(t: Tab) {
    tab = t;
    if (t === 'installed') loadInstalled();
  }

  onMount(() => { loadAvailable(); });
</script>

<div class="pk-shell">
  <header class="pk-head">
    <div>
      <h1>Pack Registry</h1>
      <p class="muted">Internal vertical packs & extensions. Browse manifests, install per-project.</p>
    </div>
    <div class="ctrls">
      <input
        class="slug-input"
        type="text"
        placeholder="project_slug (for install / installed view)"
        bind:value={projectSlug}
        onchange={() => { if (tab === 'installed') loadInstalled(); }}
      />
      <button class="btn-secondary" onclick={syncFromDisk} disabled={syncing}>
        {syncing ? 'Syncing...' : 'Sync from disk'}
      </button>
      <button class="btn-ghost" onclick={loadAvailable} disabled={loading}>
        Refresh
      </button>
    </div>
  </header>

  {#if error}
    <p class="err">{error}</p>
  {/if}
  {#if toast}
    <p class="toast">{toast}</p>
  {/if}

  <nav class="tabs">
    <button class="tab" class:active={tab === 'available'} onclick={() => switchTab('available')}>
      Available <span class="badge">{packs.length}</span>
    </button>
    <button class="tab" class:active={tab === 'installed'} onclick={() => switchTab('installed')}>
      Installed <span class="badge">{installed.length}</span>
    </button>
  </nav>

  {#if tab === 'available'}
    {#if loading}
      <p class="muted">Loading...</p>
    {:else if packs.length === 0}
      <div class="empty">
        <p>No packs registered yet.</p>
        <p class="small muted">Click <strong>Sync from disk</strong> to scan
          <code>dash/workflows/verticals/</code> and register the built-in packs.</p>
      </div>
    {:else}
      <table class="pk-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Version</th>
            <th>Format</th>
            <th class="num">Workflows</th>
            <th class="num">Models</th>
            <th class="num">Skills</th>
            <th>Author</th>
            <th>Install for</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each packs as p (p.id)}
            <tr>
              <td>
                <button class="link" onclick={() => viewManifest(p)} title="View manifest">
                  {p.name}
                </button>
                {#if p.description}
                  <div class="muted small">{p.description}</div>
                {/if}
              </td>
              <td><code>{p.version || '—'}</code></td>
              <td>
                <span class="fmt fmt-{p.format || 'legacy'}">{p.format || 'legacy'}</span>
              </td>
              <td class="num">{p.workflow_count ?? 0}</td>
              <td class="num">{p.model_count ?? 0}</td>
              <td class="num">{(p.skills || []).length}</td>
              <td class="small">{p.author || '—'}</td>
              <td>
                <input
                  class="slug-input small"
                  type="text"
                  placeholder={projectSlug || 'project_slug'}
                  value={projectInputs[p.id] || ''}
                  oninput={(e) => projectInputs[p.id] = (e.target as HTMLInputElement).value}
                />
              </td>
              <td class="actions">
                <button class="btn-mini" onclick={() => installPack(p)}>Install</button>
                <button class="btn-mini btn-ghost" onclick={() => viewManifest(p)}>Manifest</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {:else}
    {#if !projectSlug.trim()}
      <div class="empty">
        <p>Enter a <code>project_slug</code> above to view installed packs.</p>
      </div>
    {:else if loading}
      <p class="muted">Loading...</p>
    {:else if installed.length === 0}
      <div class="empty">
        <p>No packs installed for <code>{projectSlug}</code>.</p>
      </div>
    {:else}
      <table class="pk-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Version</th>
            <th>Format</th>
            <th>Status</th>
            <th>Installed</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each installed as p (p.id)}
            <tr>
              <td>
                <button class="link" onclick={() => viewManifest(p)}>{p.name}</button>
                {#if p.description}
                  <div class="muted small">{p.description}</div>
                {/if}
              </td>
              <td><code>{p.version || '—'}</code></td>
              <td><span class="fmt fmt-{p.format || 'legacy'}">{p.format || 'legacy'}</span></td>
              <td>
                {#if p.enabled}
                  <span class="pill pill-on">enabled</span>
                {:else}
                  <span class="pill pill-off">disabled</span>
                {/if}
              </td>
              <td class="small">{p.installed_at ? new Date(p.installed_at).toLocaleString() : '—'}</td>
              <td class="actions">
                <button class="btn-mini btn-ghost" onclick={() => viewManifest(p)}>Manifest</button>
                {#if p.enabled}
                  <button class="btn-mini btn-danger" onclick={() => uninstallPack(p)}>Disable</button>
                {:else}
                  <button class="btn-mini" onclick={() => installPack(p)}>Re-enable</button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</div>

{#if manifestPack}
  <div class="modal-backdrop" onclick={closeManifest} role="presentation">
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <header class="modal-head">
        <div>
          <h2>{manifestPack.name} <span class="muted small">v{manifestPack.version || '—'}</span></h2>
          <p class="muted small">{manifestPack.source_path || ''}</p>
        </div>
        <button class="btn-ghost" onclick={closeManifest} aria-label="Close">✕</button>
      </header>
      <div class="modal-body">
        {#if manifestLoading}
          <p class="muted">Loading manifest...</p>
        {:else}
          <pre class="manifest">{fmtJson(manifestPack.manifest || manifestPack)}</pre>
        {/if}
      </div>
      <footer class="modal-foot">
        <button class="btn-ghost" onclick={closeManifest}>Close</button>
      </footer>
    </div>
  </div>
{/if}

<style>
  .pk-shell {
    padding: 24px 32px;
    max-width: 1300px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .pk-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
    flex-wrap: wrap;
  }
  .pk-head h1 {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 24px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err {
    color: #b3261e;
    background: #fdecea;
    border: 1px solid #f5c2c0;
    border-radius: 4px;
    padding: 8px 12px;
    margin: 0 0 16px 0;
    font-size: 13px;
  }
  .toast {
    color: #1f5c3a;
    background: #e6f4ec;
    border: 1px solid #b8dec8;
    border-radius: 4px;
    padding: 8px 12px;
    margin: 0 0 16px 0;
    font-size: 13px;
  }
  .ctrls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .slug-input {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    min-width: 220px;
    font-family: monospace;
  }
  .slug-input.small { min-width: 140px; font-size: 12px; padding: 4px 8px; }
  .btn-ghost, .btn-primary, .btn-secondary, .btn-mini {
    padding: 6px 12px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
    color: #1f1c17;
  }
  .btn-ghost:hover, .btn-secondary:hover { background: #f7f3e9; }
  .btn-primary { background: #c96342; color: #fff; border-color: #c96342; }
  .btn-primary:hover { background: #b35636; }
  .btn-secondary { background: #fff; border-color: #c96342; color: #c96342; }
  .btn-mini { padding: 3px 8px; font-size: 11px; margin-right: 4px; }
  .btn-mini.btn-danger { color: #b3261e; border-color: #f5c2c0; }
  .btn-mini.btn-danger:hover { background: #fdecea; }
  .btn-mini.btn-ghost { color: #777; }
  .btn-ghost:disabled, .btn-secondary:disabled, .btn-primary:disabled { opacity: 0.5; cursor: default; }

  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid #e8e3d6;
    margin-bottom: 16px;
  }
  .tab {
    background: transparent;
    border: none;
    padding: 10px 16px;
    font-size: 13px;
    color: #777;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
  }
  .tab:hover { color: #1f1c17; }
  .tab.active {
    color: #c96342;
    border-bottom-color: #c96342;
    font-weight: 600;
  }
  .badge {
    background: #f0ebe0;
    color: #555;
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 11px;
    margin-left: 4px;
  }
  .tab.active .badge { background: #fdecea; color: #c96342; }

  .pk-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .pk-table thead { background: #f7f3e9; }
  .pk-table th, .pk-table td {
    padding: 8px 10px;
    text-align: left;
    border-bottom: 1px solid #e8e3d6;
    vertical-align: top;
  }
  .pk-table th {
    font-weight: 600;
    color: #555;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .pk-table td.num, .pk-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pk-table tr:hover { background: #fafaf6; }

  .link {
    background: none;
    border: none;
    color: #c96342;
    cursor: pointer;
    padding: 0;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
  }
  .link:hover { text-decoration: underline; }

  code {
    font-family: monospace;
    font-size: 12px;
    background: #f0ebe0;
    padding: 1px 5px;
    border-radius: 3px;
  }

  .fmt {
    display: inline-block;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 8px;
    font-family: monospace;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .fmt-mdl { background: #e6f0fd; color: #1f5cb3; }
  .fmt-legacy { background: #f0ebe0; color: #555; }

  .pill {
    display: inline-block;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .pill-on { background: #e6f4ec; color: #1f5c3a; }
  .pill-off { background: #f0ebe0; color: #777; }

  .actions { white-space: nowrap; }

  .empty {
    padding: 32px 16px;
    text-align: center;
    color: #777;
    background: #fafaf6;
    border: 1px dashed #e8e3d6;
    border-radius: 6px;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(31, 28, 23, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    padding: 24px;
  }
  .modal {
    background: #fff;
    border-radius: 6px;
    width: min(800px, 100%);
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
  }
  .modal-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    padding: 16px 20px;
    border-bottom: 1px solid #e8e3d6;
  }
  .modal-head h2 {
    margin: 0;
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 18px;
    font-weight: 600;
  }
  .modal-body { padding: 16px 20px; overflow: auto; flex: 1; }
  .modal-foot {
    padding: 12px 20px;
    border-top: 1px solid #e8e3d6;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .manifest {
    margin: 0;
    padding: 12px;
    background: #1f1c17;
    color: #e8e3d6;
    border-radius: 4px;
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 12px;
    line-height: 1.5;
    overflow: auto;
    max-height: 60vh;
  }
</style>
