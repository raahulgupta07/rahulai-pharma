<script lang="ts">
  import { onMount } from 'svelte';

  // ── Types ─────────────────────────────────────────────────────────────
  type VirtualColumn = { name: string; expression: string; type?: string; bounds?: any };
  type Relationship = { model: string; on: string; type?: string; optional?: boolean };

  type ModelRow = {
    id: number;
    name: string;
    model_name: string;
    raw_table_ref: string;
    description: string;
    pack_name: string;
    vcol_count: number;
    rel_count: number;
    status: string;
    version: number;
    updated_at: string | null;
  };

  type ModelDetail = {
    id: number;
    project_slug: string;
    name: string;
    description: string;
    kind: string;
    model_name: string;
    raw_table_ref: string;
    virtual_columns: VirtualColumn[];
    relationships: Relationship[];
    raw_columns: { name: string; type: string }[];
    status: string;
    version: number;
  };

  type MetricRow = {
    id: number;
    name: string;
    description?: string;
    kind: string;
    status: string;
    version: number;
    measure_col?: string | null;
    source_tables?: string[];
    group_dims?: string[];
    filters?: any[];
    denom_filters?: any[];
    synonyms?: string[];
    model_name?: string | null;
    updated_at?: string | null;
  };

  type AvailablePack = {
    name: string;
    vertical: string;
    description: string;
    workflow_count: number;
    model_count: number;
    format: string;
    installed: boolean;
  };

  // ── State ─────────────────────────────────────────────────────────────
  let projectSlug = $state('');
  let activeTab = $state<'models' | 'metrics' | 'packs'>('models');

  let installed = $state<{ packs: any[]; models: ModelRow[]; total_models: number } | null>(null);
  let metrics = $state<MetricRow[]>([]);
  let packs = $state<AvailablePack[]>([]);

  let expandedModelId = $state<number | null>(null);
  let modelDetail = $state<ModelDetail | null>(null);

  let editMetricId = $state<number | null>(null);
  let editMetric = $state<MetricRow | null>(null);

  let showNewMetricModal = $state(false);
  let newMetric = $state<any>({
    name: '',
    description: '',
    kind: 'count',
    measure_col: '',
    source_tables_str: '',
    group_dims_str: '',
    synonyms_str: '',
    status: 'draft',
  });

  let loading = $state(false);
  let error = $state<string | null>(null);
  let toast = $state<string | null>(null);

  // ── Helpers ───────────────────────────────────────────────────────────
  function getToken(): string {
    if (typeof window === 'undefined') return '';
    return localStorage.getItem('dash_token') || '';
  }

  async function api(path: string, opts: RequestInit = {}): Promise<any> {
    const token = getToken();
    const headers: Record<string, string> = {
      'Accept': 'application/json',
      ...((opts.headers as Record<string, string>) || {}),
    };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error(`HTTP ${r.status}: ${txt}`);
    }
    return r.json();
  }

  function flash(msg: string) {
    toast = msg;
    setTimeout(() => (toast = null), 2500);
  }

  // ── Loaders ───────────────────────────────────────────────────────────
  async function loadInstalled() {
    if (!projectSlug) return;
    loading = true;
    error = null;
    try {
      installed = await api(`/api/mdl/installed?project_slug=${encodeURIComponent(projectSlug)}`);
    } catch (e: any) {
      error = e?.message || String(e);
      installed = null;
    } finally {
      loading = false;
    }
  }

  async function loadMetrics() {
    if (!projectSlug) return;
    loading = true;
    error = null;
    try {
      // No dedicated list endpoint — reuse installed for now, plus query by project
      // via a list_definitions equivalent endpoint that DOES exist in metrics_api
      // Fallback: read all from installed/metric and filter
      const r = await fetch(`/api/projects/${encodeURIComponent(projectSlug)}/metrics`, {
        headers: { Authorization: `Bearer ${getToken()}`, Accept: 'application/json' },
      });
      if (r.ok) {
        const data = await r.json();
        metrics = (data.metrics || data || []) as MetricRow[];
      } else {
        // Fallback to installed listing (model_name NOT NULL filter)
        const inst = await api(`/api/mdl/installed?project_slug=${encodeURIComponent(projectSlug)}`);
        metrics = (inst.models || []).map((m: any) => ({
          id: m.id,
          name: m.name,
          description: m.description,
          kind: 'count',
          status: m.status,
          version: m.version,
          model_name: m.model_name,
          updated_at: m.updated_at,
        }));
      }
    } catch (e: any) {
      error = e?.message || String(e);
      metrics = [];
    } finally {
      loading = false;
    }
  }

  async function loadPacks() {
    loading = true;
    error = null;
    try {
      const q = projectSlug ? `?project_slug=${encodeURIComponent(projectSlug)}` : '';
      const data = await api(`/api/mdl/packs/available${q}`);
      packs = data.packs || [];
    } catch (e: any) {
      error = e?.message || String(e);
      packs = [];
    } finally {
      loading = false;
    }
  }

  async function loadAll() {
    if (!projectSlug) return;
    await Promise.all([loadInstalled(), loadMetrics(), loadPacks()]);
  }

  // ── Model expand + edit ───────────────────────────────────────────────
  async function toggleModel(id: number) {
    if (expandedModelId === id) {
      expandedModelId = null;
      modelDetail = null;
      return;
    }
    expandedModelId = id;
    modelDetail = null;
    try {
      modelDetail = await api(`/api/mdl/model/${id}`);
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function saveVcol(idx: number) {
    if (!modelDetail) return;
    try {
      const r = await api(`/api/mdl/model/${modelDetail.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ virtual_columns: modelDetail.virtual_columns }),
      });
      if (r.ok === false) {
        const bad = (r.validation || []).filter((v: any) => !v.valid);
        error = `SQL validation failed: ${bad.map((b: any) => `${b.name}: ${b.error}`).join('; ')}`;
        return;
      }
      flash(`✓ saved (v${r.version})`);
      await loadInstalled();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function saveRel(idx: number) {
    if (!modelDetail) return;
    try {
      const r = await api(`/api/mdl/model/${modelDetail.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ relationships: modelDetail.relationships }),
      });
      flash(`✓ saved (v${r.version})`);
      await loadInstalled();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function saveDescription() {
    if (!modelDetail) return;
    try {
      const r = await api(`/api/mdl/model/${modelDetail.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ description: modelDetail.description }),
      });
      flash(`✓ saved (v${r.version})`);
      await loadInstalled();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  function addVcol() {
    if (!modelDetail) return;
    modelDetail.virtual_columns = [...modelDetail.virtual_columns, { name: '', expression: '', type: 'string' }];
  }

  function removeVcol(idx: number) {
    if (!modelDetail) return;
    modelDetail.virtual_columns = modelDetail.virtual_columns.filter((_, i) => i !== idx);
  }

  function addRel() {
    if (!modelDetail) return;
    modelDetail.relationships = [...modelDetail.relationships, { model: '', on: '', type: 'many_to_one' }];
  }

  function removeRel(idx: number) {
    if (!modelDetail) return;
    modelDetail.relationships = modelDetail.relationships.filter((_, i) => i !== idx);
  }

  // ── Metric editing ────────────────────────────────────────────────────
  function startEditMetric(m: MetricRow) {
    editMetricId = m.id;
    editMetric = JSON.parse(JSON.stringify(m));
  }

  async function saveMetric() {
    if (!editMetric || editMetricId == null) return;
    try {
      const body: any = {
        description: editMetric.description,
        kind: editMetric.kind,
        measure_col: editMetric.measure_col,
        status: editMetric.status,
        source_tables: editMetric.source_tables,
        group_dims: editMetric.group_dims,
        synonyms: editMetric.synonyms,
        filters: editMetric.filters,
        denom_filters: editMetric.denom_filters,
      };
      const r = await api(`/api/mdl/metric/${editMetricId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
      flash(`✓ saved (v${r.version})`);
      editMetricId = null;
      editMetric = null;
      await loadMetrics();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function deleteMetric(id: number, name: string) {
    if (!confirm(`Delete metric "${name}"? (soft delete — sets status=deprecated)`)) return;
    try {
      await api(`/api/mdl/metric/${id}`, { method: 'DELETE' });
      flash(`✓ deleted`);
      await loadMetrics();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function createMetric() {
    if (!projectSlug || !newMetric.name) {
      error = 'project_slug and name required';
      return;
    }
    try {
      const body = {
        project_slug: projectSlug,
        name: newMetric.name,
        description: newMetric.description,
        kind: newMetric.kind,
        measure_col: newMetric.measure_col || null,
        source_tables: newMetric.source_tables_str
          .split(',')
          .map((s: string) => s.trim())
          .filter(Boolean),
        group_dims: newMetric.group_dims_str
          .split(',')
          .map((s: string) => s.trim())
          .filter(Boolean),
        synonyms: newMetric.synonyms_str
          .split(',')
          .map((s: string) => s.trim())
          .filter(Boolean),
        status: newMetric.status,
      };
      await api(`/api/mdl/metric`, { method: 'POST', body: JSON.stringify(body) });
      flash(`✓ created`);
      showNewMetricModal = false;
      newMetric = {
        name: '', description: '', kind: 'count', measure_col: '',
        source_tables_str: '', group_dims_str: '', synonyms_str: '', status: 'draft',
      };
      await loadMetrics();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  // ── Pack install ──────────────────────────────────────────────────────
  async function installPack(pack: AvailablePack) {
    if (!projectSlug) {
      error = 'project_slug required';
      return;
    }
    if (!confirm(`Install pack "${pack.name}" into project "${projectSlug}"?`)) return;
    try {
      const r = await api(`/api/mdl/install`, {
        method: 'POST',
        body: JSON.stringify({ project_slug: projectSlug, pack_name: pack.name }),
      });
      if (r.ok) {
        flash(`✓ installed: ${r.models_installed} models, ${r.workflows_installed} workflows`);
        await loadAll();
      } else {
        error = `install failed: ${r.error || JSON.stringify(r)}`;
      }
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  // Auto-load when slug changes & tab switch
  function onSlugSubmit(e: SubmitEvent) {
    e.preventDefault();
    if (projectSlug) loadAll();
  }

  function switchTab(t: 'models' | 'metrics' | 'packs') {
    activeTab = t;
    if (!projectSlug) return;
    if (t === 'models') loadInstalled();
    else if (t === 'metrics') loadMetrics();
    else loadPacks();
  }

  onMount(() => {
    // Read project from URL query
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      const slug = url.searchParams.get('project_slug') || url.searchParams.get('slug');
      if (slug) {
        projectSlug = slug;
        loadAll();
      }
    }
  });
</script>

<div class="mdl-shell">
  <header class="mdl-head">
    <div>
      <h1>MDL Editor</h1>
      <p class="muted">Models · virtual columns · metric definitions · relationships</p>
    </div>
    <form class="ctrls" onsubmit={onSlugSubmit}>
      <input
        type="text"
        placeholder="project_slug"
        bind:value={projectSlug}
        class="slug-input"
      />
      <button type="submit" class="refresh" disabled={loading}>
        {loading ? '...' : 'Load'}
      </button>
    </form>
  </header>

  {#if toast}
    <div class="toast">{toast}</div>
  {/if}
  {#if error}
    <div class="err">⚠ {error} <button class="x" onclick={() => (error = null)}>✕</button></div>
  {/if}

  <nav class="tabs">
    <button class="tab" class:active={activeTab === 'models'} onclick={() => switchTab('models')}>
      Models{installed ? ` (${installed.total_models})` : ''}
    </button>
    <button class="tab" class:active={activeTab === 'metrics'} onclick={() => switchTab('metrics')}>
      Metrics ({metrics.length})
    </button>
    <button class="tab" class:active={activeTab === 'packs'} onclick={() => switchTab('packs')}>
      Available Packs ({packs.length})
    </button>
  </nav>

  <!-- ── MODELS TAB ────────────────────────────────────────────────── -->
  {#if activeTab === 'models'}
    {#if !projectSlug}
      <p class="muted">Enter a project_slug above to load installed models.</p>
    {:else if installed && installed.models.length === 0}
      <p class="muted">No MDL models installed in this project. See Available Packs tab.</p>
    {:else if installed}
      <ul class="model-list">
        {#each installed.models as m (m.id)}
          <li class="model-row">
            <button class="model-header" onclick={() => toggleModel(m.id)}>
              <span class="chevron">{expandedModelId === m.id ? '▼' : '▶'}</span>
              <span class="m-name">{m.model_name}</span>
              <span class="m-table">→ <code>{m.raw_table_ref}</code></span>
              <span class="badge">{m.vcol_count} vcols</span>
              <span class="badge">{m.rel_count} rels</span>
              <span class="pack">{m.pack_name}</span>
              <span class="status" data-status={m.status}>{m.status}</span>
              <span class="version">v{m.version}</span>
            </button>

            {#if expandedModelId === m.id}
              <div class="model-body">
                {#if !modelDetail}
                  <p class="muted small">loading…</p>
                {:else}
                  <div class="field-row">
                    <label>Description</label>
                    <input
                      type="text"
                      bind:value={modelDetail.description}
                      onblur={saveDescription}
                    />
                  </div>

                  <div class="section">
                    <div class="section-head">
                      <h3>Virtual Columns</h3>
                      <button class="btn-sm" onclick={addVcol}>+ Add</button>
                    </div>
                    {#if modelDetail.virtual_columns.length === 0}
                      <p class="muted small">No virtual columns. Add one to expose derived fields.</p>
                    {:else}
                      <table class="inner-table">
                        <thead>
                          <tr>
                            <th>Name</th>
                            <th>Expression</th>
                            <th>Type</th>
                            <th></th>
                          </tr>
                        </thead>
                        <tbody>
                          {#each modelDetail.virtual_columns as vc, i}
                            <tr>
                              <td><input type="text" bind:value={vc.name} /></td>
                              <td>
                                <textarea
                                  bind:value={vc.expression}
                                  rows="2"
                                  placeholder="qty * unit_cost"
                                ></textarea>
                              </td>
                              <td>
                                <select bind:value={vc.type}>
                                  <option value="string">string</option>
                                  <option value="numeric">numeric</option>
                                  <option value="boolean">boolean</option>
                                  <option value="date">date</option>
                                </select>
                              </td>
                              <td class="actions">
                                <button class="btn-sm" onclick={() => saveVcol(i)}>Save</button>
                                <button class="btn-sm danger" onclick={() => removeVcol(i)}>×</button>
                              </td>
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                    {/if}
                  </div>

                  <div class="section">
                    <div class="section-head">
                      <h3>Relationships</h3>
                      <button class="btn-sm" onclick={addRel}>+ Add</button>
                    </div>
                    {#if modelDetail.relationships.length === 0}
                      <p class="muted small">No relationships defined.</p>
                    {:else}
                      <table class="inner-table">
                        <thead>
                          <tr>
                            <th>Target Model</th>
                            <th>Join Condition</th>
                            <th>Type</th>
                            <th>Optional</th>
                            <th></th>
                          </tr>
                        </thead>
                        <tbody>
                          {#each modelDetail.relationships as rel, i}
                            <tr>
                              <td><input type="text" bind:value={rel.model} /></td>
                              <td><input type="text" bind:value={rel.on} placeholder="sku = brands.code" /></td>
                              <td>
                                <select bind:value={rel.type}>
                                  <option value="many_to_one">many_to_one</option>
                                  <option value="one_to_many">one_to_many</option>
                                  <option value="many_to_many">many_to_many</option>
                                </select>
                              </td>
                              <td><input type="checkbox" bind:checked={rel.optional} /></td>
                              <td class="actions">
                                <button class="btn-sm" onclick={() => saveRel(i)}>Save</button>
                                <button class="btn-sm danger" onclick={() => removeRel(i)}>×</button>
                              </td>
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                    {/if}
                  </div>

                  {#if modelDetail.raw_columns.length > 0}
                    <details class="raw-cols">
                      <summary>Raw columns from <code>{modelDetail.raw_table_ref}</code> ({modelDetail.raw_columns.length})</summary>
                      <div class="cols-grid">
                        {#each modelDetail.raw_columns as rc}
                          <div class="col-chip"><code>{rc.name}</code> <span class="type">{rc.type}</span></div>
                        {/each}
                      </div>
                    </details>
                  {/if}
                {/if}
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  {/if}

  <!-- ── METRICS TAB ───────────────────────────────────────────────── -->
  {#if activeTab === 'metrics'}
    {#if !projectSlug}
      <p class="muted">Enter a project_slug above to load metrics.</p>
    {:else}
      <div class="metrics-toolbar">
        <button class="btn-primary" onclick={() => (showNewMetricModal = true)}>+ New Metric</button>
      </div>

      {#if metrics.length === 0}
        <p class="muted">No metric definitions for this project.</p>
      {:else}
        <table class="metric-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>Measure</th>
              <th>Tables</th>
              <th>Status</th>
              <th>Ver</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each metrics as m}
              {#if editMetricId === m.id && editMetric}
                <tr class="edit-row">
                  <td colspan="7">
                    <div class="edit-pane">
                      <div class="field-row">
                        <label>Description</label>
                        <input type="text" bind:value={editMetric.description} />
                      </div>
                      <div class="field-row">
                        <label>Kind</label>
                        <select bind:value={editMetric.kind}>
                          <option value="count">count</option>
                          <option value="rate">rate</option>
                          <option value="ratio">ratio</option>
                          <option value="contribution">contribution</option>
                          <option value="sum">sum</option>
                          <option value="avg">avg</option>
                        </select>
                      </div>
                      <div class="field-row">
                        <label>Measure column</label>
                        <input type="text" bind:value={editMetric.measure_col} />
                      </div>
                      <div class="field-row">
                        <label>Status</label>
                        <select bind:value={editMetric.status}>
                          <option value="draft">draft</option>
                          <option value="verified">verified</option>
                          <option value="deprecated">deprecated</option>
                        </select>
                      </div>
                      <div class="field-row">
                        <label>Filters (JSON)</label>
                        <textarea
                          rows="3"
                          value={JSON.stringify(editMetric.filters || [], null, 2)}
                          onchange={(e) => {
                            try {
                              editMetric!.filters = JSON.parse((e.target as HTMLTextAreaElement).value);
                            } catch {
                              error = 'Invalid filters JSON';
                            }
                          }}
                        ></textarea>
                      </div>
                      <div class="actions">
                        <button class="btn-primary" onclick={saveMetric}>Save</button>
                        <button class="btn-sm" onclick={() => { editMetricId = null; editMetric = null; }}>Cancel</button>
                      </div>
                    </div>
                  </td>
                </tr>
              {:else}
                <tr>
                  <td><strong>{m.name}</strong>{#if m.description}<div class="muted small">{m.description}</div>{/if}</td>
                  <td><code>{m.kind}</code></td>
                  <td>{m.measure_col || '—'}</td>
                  <td class="small">{(m.source_tables || []).join(', ') || '—'}</td>
                  <td><span class="status" data-status={m.status}>{m.status}</span></td>
                  <td>v{m.version}</td>
                  <td class="actions">
                    <button class="btn-sm" onclick={() => startEditMetric(m)}>Edit</button>
                    <button class="btn-sm danger" onclick={() => deleteMetric(m.id, m.name)}>Delete</button>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
  {/if}

  <!-- ── PACKS TAB ─────────────────────────────────────────────────── -->
  {#if activeTab === 'packs'}
    {#if packs.length === 0}
      <p class="muted">No MDL packs registered, or none available to install.</p>
    {:else}
      <ul class="pack-list">
        {#each packs as p}
          <li class="pack-row">
            <div class="pack-main">
              <div class="pack-title">
                <strong>{p.name}</strong>
                <span class="pack-vertical">{p.vertical}</span>
                {#if p.installed}<span class="pill installed">installed</span>{/if}
              </div>
              <p class="muted small">{p.description || 'No description.'}</p>
              <p class="small">{p.model_count} model{p.model_count === 1 ? '' : 's'} · {p.workflow_count} workflow{p.workflow_count === 1 ? '' : 's'}</p>
            </div>
            <div class="pack-actions">
              {#if !p.installed}
                <button class="btn-primary" onclick={() => installPack(p)} disabled={!projectSlug}>
                  Install
                </button>
              {:else}
                <span class="muted small">already installed</span>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}

  <!-- ── NEW METRIC MODAL ──────────────────────────────────────────── -->
  {#if showNewMetricModal}
    <div
      class="modal-bg"
      onclick={() => (showNewMetricModal = false)}
      role="presentation"
      onkeydown={(e) => { if (e.key === 'Escape') showNewMetricModal = false; }}
    >
      <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-label="New metric">
        <h2>New Metric Definition</h2>
        <div class="field-row">
          <label>Name <span class="req">*</span></label>
          <input type="text" bind:value={newMetric.name} placeholder="active_users" />
        </div>
        <div class="field-row">
          <label>Description</label>
          <input type="text" bind:value={newMetric.description} />
        </div>
        <div class="field-row">
          <label>Kind</label>
          <select bind:value={newMetric.kind}>
            <option value="count">count</option>
            <option value="rate">rate</option>
            <option value="ratio">ratio</option>
            <option value="contribution">contribution</option>
            <option value="sum">sum</option>
            <option value="avg">avg</option>
          </select>
        </div>
        <div class="field-row">
          <label>Measure column</label>
          <input type="text" bind:value={newMetric.measure_col} placeholder="amount (optional)" />
        </div>
        <div class="field-row">
          <label>Source tables</label>
          <input type="text" bind:value={newMetric.source_tables_str} placeholder="orders, customers" />
        </div>
        <div class="field-row">
          <label>Group dims</label>
          <input type="text" bind:value={newMetric.group_dims_str} placeholder="region, channel" />
        </div>
        <div class="field-row">
          <label>Synonyms</label>
          <input type="text" bind:value={newMetric.synonyms_str} placeholder="dau, daily_users" />
        </div>
        <div class="field-row">
          <label>Status</label>
          <select bind:value={newMetric.status}>
            <option value="draft">draft</option>
            <option value="verified">verified</option>
          </select>
        </div>
        <div class="actions">
          <button class="btn-primary" onclick={createMetric}>Create</button>
          <button class="btn-sm" onclick={() => (showNewMetricModal = false)}>Cancel</button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .mdl-shell {
    padding: 24px 32px;
    max-width: 1300px;
    margin: 0 auto;
    font-family: 'Source Serif Pro', Georgia, serif;
    color: #1f1c17;
    background: #faf7f1;
    min-height: calc(100vh - 56px);
  }
  .mdl-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .mdl-head h1 {
    font-size: 24px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .ctrls { display: flex; gap: 8px; }
  .slug-input {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    width: 240px;
    font-family: inherit;
  }
  .ctrls .refresh {
    padding: 6px 14px;
    border: 1px solid #c96342;
    background: #c96342;
    color: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }
  .ctrls .refresh:disabled { opacity: 0.5; cursor: default; }

  .toast {
    position: fixed;
    top: 80px;
    right: 32px;
    background: #2c2a26;
    color: #f7f3e9;
    padding: 8px 16px;
    border-radius: 4px;
    font-size: 13px;
    z-index: 100;
  }
  .err {
    background: #fcebe6;
    border: 1px solid #c96342;
    color: #7d2814;
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 16px;
    font-size: 13px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .err .x {
    background: none;
    border: none;
    color: #7d2814;
    cursor: pointer;
    font-size: 14px;
  }

  .tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 16px;
    border-bottom: 1px solid #e8e3d6;
  }
  .tab {
    padding: 8px 16px;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: #777;
    cursor: pointer;
    font-size: 14px;
    font-family: inherit;
  }
  .tab.active {
    color: #c96342;
    border-bottom-color: #c96342;
    font-weight: 600;
  }

  /* models */
  .model-list { list-style: none; padding: 0; margin: 0; }
  .model-row {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    margin-bottom: 10px;
    overflow: hidden;
  }
  .model-header {
    width: 100%;
    background: none;
    border: none;
    text-align: left;
    padding: 12px 16px;
    cursor: pointer;
    display: grid;
    grid-template-columns: 20px 1fr auto auto auto auto auto auto;
    gap: 12px;
    align-items: center;
    font-family: inherit;
    font-size: 14px;
  }
  .model-header:hover { background: #f7f3e9; }
  .chevron { color: #777; font-size: 11px; }
  .m-name { font-weight: 600; color: #1f1c17; }
  .m-table code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }
  .badge {
    background: #ebe6d8;
    color: #555;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
  }
  .pack {
    color: #c96342;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .status {
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    background: #ebe6d8;
    color: #555;
  }
  .status[data-status='verified'] { background: #d4ebd6; color: #1f6b2d; }
  .status[data-status='deprecated'] { background: #f5d6d6; color: #8a2727; }
  .version { color: #999; font-size: 11px; }

  .model-body {
    background: #faf7f1;
    padding: 16px;
    border-top: 1px solid #e8e3d6;
  }

  .field-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }
  .field-row label {
    min-width: 130px;
    font-size: 12px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .field-row input[type='text'],
  .field-row select,
  .field-row textarea {
    flex: 1;
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    font-family: inherit;
  }
  .field-row .req { color: #c96342; }

  .section { margin-top: 16px; }
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .section-head h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin: 0;
    color: #555;
    font-weight: 600;
  }

  .inner-table {
    width: 100%;
    border-collapse: collapse;
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 4px;
  }
  .inner-table th, .inner-table td {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
  }
  .inner-table th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
  }
  .inner-table input[type='text'],
  .inner-table textarea,
  .inner-table select {
    width: 100%;
    padding: 4px 8px;
    border: 1px solid #d6d1c2;
    background: #fafafa;
    border-radius: 3px;
    font-size: 12px;
    font-family: 'SF Mono', Menlo, monospace;
  }
  .inner-table textarea {
    resize: vertical;
    min-height: 32px;
  }
  .actions { display: flex; gap: 6px; white-space: nowrap; }
  .btn-sm {
    padding: 4px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 3px;
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
  }
  .btn-sm:hover { background: #f7f3e9; }
  .btn-sm.danger { color: #b3261e; border-color: #e5a09a; }
  .btn-primary {
    padding: 6px 14px;
    background: #c96342;
    color: #fff;
    border: 1px solid #c96342;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }

  .raw-cols { margin-top: 14px; }
  .raw-cols summary {
    cursor: pointer;
    font-size: 12px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .cols-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
  }
  .col-chip {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 3px;
    padding: 3px 8px;
    font-size: 11px;
  }
  .col-chip code {
    background: none;
    padding: 0;
    font-family: 'SF Mono', monospace;
  }
  .col-chip .type {
    color: #999;
    margin-left: 4px;
    font-size: 10px;
  }

  /* metrics */
  .metrics-toolbar { margin-bottom: 12px; }
  .metric-table {
    width: 100%;
    border-collapse: collapse;
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    overflow: hidden;
  }
  .metric-table th, .metric-table td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
  }
  .metric-table th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
  }
  .metric-table code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 11px;
  }

  .edit-pane {
    background: #faf7f1;
    padding: 16px;
    border-left: 3px solid #c96342;
  }

  /* packs */
  .pack-list { list-style: none; padding: 0; margin: 0; }
  .pack-row {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 14px 16px;
    margin-bottom: 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .pack-main { flex: 1; }
  .pack-title {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-bottom: 4px;
  }
  .pack-title strong { font-size: 15px; }
  .pack-vertical {
    color: #777;
    font-size: 12px;
  }
  .pill.installed {
    background: #d4ebd6;
    color: #1f6b2d;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  /* modal */
  .modal-bg {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(20, 18, 14, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
  }
  .modal {
    background: #faf7f1;
    border: 1px solid #d6d1c2;
    border-radius: 8px;
    padding: 24px 28px;
    width: 480px;
    max-width: 90vw;
    max-height: 90vh;
    overflow-y: auto;
  }
  .modal h2 {
    font-size: 18px;
    margin: 0 0 16px 0;
    font-weight: 600;
  }
  .modal .actions {
    margin-top: 16px;
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
</style>
