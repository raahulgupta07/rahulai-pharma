<script lang="ts">
  import { onMount } from 'svelte';

  let activeTab = $state<'types' | 'connections' | 'grants' | 'audit'>('types');

  let toast = $state<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  let toastTimer: any = null;
  function flash(kind: 'ok' | 'err', msg: string) {
    toast = { kind, msg };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 3500);
  }

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  function _hj(): Record<string, string> {
    return { ..._h(), 'Content-Type': 'application/json' };
  }

  /* ─── data ─── */
  type Connector = { type: string; title: string; kind: string; description: string };
  type Connection = {
    id: string;
    name: string;
    connector_type: string;
    enabled: boolean;
    allow_all_users: boolean;
    users_allowed: any[];
    ldap_groups_allowed: any[];
    created_at?: string;
    updated_at?: string;
  };
  type FieldSchema = { config: any; credentials: any };
  type AuditRow = {
    id: number;
    user_id: number | null;
    action: string;
    sql_text: string | null;
    row_count: number | null;
    duration_ms: number | null;
    error: string | null;
    created_at: string;
  };

  let connectors = $state<Connector[]>([]);
  let connections = $state<Connection[]>([]);
  let loadingConnectors = $state(false);
  let loadingConnections = $state(false);
  let connectorsError = $state<string>('');

  /* ─── create/edit modal ─── */
  let showModal = $state(false);
  let modalMode = $state<'create' | 'edit'>('create');
  let editingId = $state<string | null>(null);
  let form = $state<{ name: string; connector_type: string; config: Record<string, any>; credentials: Record<string, any> }>({
    name: '',
    connector_type: '',
    config: {},
    credentials: {}
  });
  let fields = $state<FieldSchema | null>(null);
  let loadingFields = $state(false);
  let testing = $state(false);

  /* ─── connector brand logos (simpleicons.org CDN, brand-colored) ─── */
  const LOGO_MAP: Record<string, string> = {
    postgresql: 'https://cdn.simpleicons.org/postgresql/336791',
    bigquery:   'https://cdn.simpleicons.org/googlebigquery/669DF6',
    powerbi:    'https://cdn.simpleicons.org/powerbi/F2C811',
  };
  function logoUrl(type: string): string {
    return LOGO_MAP[type] || 'https://cdn.simpleicons.org/databricks/FF3621';
  }
  function onLogoError(e: Event) {
    void e;
  }

  let typePickerOpen = $state(false);
  let saving = $state(false);

  /* ─── grants tab ─── */
  let grantsConnId = $state<string | null>(null);
  let grantsForm = $state<{ allow_all_users: boolean; users_allowed_text: string; ldap_groups_allowed_text: string }>({
    allow_all_users: false,
    users_allowed_text: '',
    ldap_groups_allowed_text: ''
  });
  let grantsSaving = $state(false);

  /* ─── audit tab ─── */
  let auditConnId = $state<string | null>(null);
  let auditDateFrom = $state('');
  let auditDateTo = $state('');
  let auditSuccessOnly = $state(false);
  let auditRows = $state<AuditRow[]>([]);
  let auditLoading = $state(false);
  let auditExpandedId = $state<number | null>(null);

  /* ─── lifecycle ─── */
  onMount(async () => {
    try {
      await Promise.all([loadConnectors(), loadConnections()]);
    } catch {}
  });

  async function loadConnectors() {
    loadingConnectors = true;
    connectorsError = '';
    try {
      const r = await fetch('/api/admin/connectors', { headers: _h() });
      if (r.ok) {
        const data = await r.json();
        // Backend returns {connectors: [...]} — extract array.
        // Tolerate raw array fallback for forward-compat.
        const list = Array.isArray(data) ? data : (data?.connectors ?? data?.types ?? data?.connector_types ?? []);
        connectors = Array.isArray(list) ? list : [];
        if (!connectors.length) {
          connectorsError = 'Endpoint returned 200 but no connector types in payload.';
          console.warn('[ConnectorsPanel] empty connector list', data);
        }
      } else {
        connectorsError = `Failed to load connector types — HTTP ${r.status}`;
        console.error('[ConnectorsPanel] /api/admin/connectors HTTP', r.status);
      }
    } catch (e: any) {
      connectorsError = `Failed to load connector types — ${e?.message || 'network error'}`;
      console.error('[ConnectorsPanel] loadConnectors failed', e);
    }
    loadingConnectors = false;
  }

  async function loadConnections() {
    loadingConnections = true;
    try {
      const r = await fetch('/api/admin/connections', { headers: _h() });
      if (r.ok) connections = (await r.json()) || [];
    } catch {}
    loadingConnections = false;
  }

  async function loadFields(type: string) {
    loadingFields = true;
    fields = null;
    try {
      const r = await fetch(`/api/admin/connectors/${encodeURIComponent(type)}/fields`, { headers: _h() });
      if (r.ok) fields = await r.json();
    } catch {}
    loadingFields = false;
  }

  /* ─── helpers ─── */
  function prettyLabel(key: string): string {
    return key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function widgetType(prop: any): string {
    const ui = prop?.json_schema_extra?.['ui:type'] || prop?.['ui:type'];
    if (ui) return String(ui);
    const t = prop?.type;
    if (t === 'integer' || t === 'number') return 'number';
    if (t === 'boolean') return 'boolean';
    return 'string';
  }

  function schemaProps(schema: any): [string, any][] {
    if (!schema || !schema.properties) return [];
    return Object.entries(schema.properties);
  }

  function seedDefaults(schema: any): Record<string, any> {
    const out: Record<string, any> = {};
    for (const [k, p] of schemaProps(schema)) {
      const pp = p as any;
      if (pp.default !== undefined) out[k] = pp.default;
      else if (widgetType(pp) === 'boolean') out[k] = false;
      else if (widgetType(pp) === 'number') out[k] = pp.type === 'integer' ? 0 : 0;
      else out[k] = '';
    }
    return out;
  }

  /* ─── modal open/close ─── */
  async function openCreate(prefillType = '') {
    modalMode = 'create';
    editingId = null;
    form = { name: '', connector_type: prefillType, config: {}, credentials: {} };
    fields = null;
    showModal = true;
    if (prefillType) {
      await loadFields(prefillType);
      if (fields) {
        form.config = seedDefaults(fields.config);
        form.credentials = seedDefaults(fields.credentials);
      }
    }
  }

  async function openEdit(c: Connection) {
    modalMode = 'edit';
    editingId = c.id;
    form = { name: c.name, connector_type: c.connector_type, config: {}, credentials: {} };
    fields = null;
    showModal = true;
    await loadFields(c.connector_type);
    if (fields) {
      // Best-effort: seed defaults then layer in existing config (creds never returned)
      form.config = { ...seedDefaults(fields.config) };
      form.credentials = seedDefaults(fields.credentials);
    }
  }

  function closeModal() {
    showModal = false;
    editingId = null;
    fields = null;
  }

  async function onTypeChange() {
    if (!form.connector_type) {
      fields = null;
      return;
    }
    await loadFields(form.connector_type);
    if (fields) {
      form.config = seedDefaults(fields.config);
      form.credentials = seedDefaults(fields.credentials);
    }
  }

  /* ─── test/save ─── */
  async function testConnection() {
    if (!form.connector_type) return;
    testing = true;
    try {
      const body =
        modalMode === 'edit' && editingId
          ? null
          : { connector_type: form.connector_type, config: form.config, credentials: form.credentials };
      const url =
        modalMode === 'edit' && editingId
          ? `/api/admin/connections/${editingId}/test`
          : '/api/admin/connections/test';
      const r = await fetch(url, {
        method: 'POST',
        headers: _hj(),
        body: body ? JSON.stringify(body) : '{}'
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && (d.success || d.ok)) {
        flash('ok', d.message || 'Connection OK' + (d.tables_visible != null ? ` · ${d.tables_visible} tables` : ''));
      } else {
        flash('err', d.message || d.detail || 'Test failed');
      }
    } catch (e: any) {
      flash('err', e?.message || 'Test failed');
    }
    testing = false;
  }

  async function saveConnection() {
    if (!form.name || !form.connector_type) {
      flash('err', 'Name and type required');
      return;
    }
    saving = true;
    try {
      const isEdit = modalMode === 'edit' && editingId;
      const url = isEdit ? `/api/admin/connections/${editingId}` : '/api/admin/connections';
      const method = isEdit ? 'PATCH' : 'POST';
      const body: any = { name: form.name, connector_type: form.connector_type, config: form.config };
      // For edit, only send credentials if user re-entered any non-empty
      const credsHaveValue = Object.values(form.credentials).some((v) => v !== '' && v !== null && v !== undefined);
      if (!isEdit || credsHaveValue) body.credentials = form.credentials;

      const r = await fetch(url, { method, headers: _hj(), body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (r.ok) {
        flash('ok', isEdit ? 'Connection updated' : 'Connection created');
        await loadConnections();
        closeModal();
      } else {
        flash('err', d.detail || d.message || `Save failed (${r.status})`);
      }
    } catch (e: any) {
      flash('err', e?.message || 'Save failed');
    }
    saving = false;
  }

  async function testSaved(id: string) {
    try {
      const r = await fetch(`/api/admin/connections/${id}/test`, { method: 'POST', headers: _hj() });
      const d = await r.json().catch(() => ({}));
      if (r.ok && (d.success || d.ok)) flash('ok', d.message || 'Connection OK');
      else flash('err', d.message || d.detail || 'Test failed');
    } catch (e: any) {
      flash('err', e?.message || 'Test failed');
    }
  }

  async function deleteConn(c: Connection) {
    if (!confirm(`Delete connection "${c.name}"? This cannot be undone.`)) return;
    try {
      const r = await fetch(`/api/admin/connections/${c.id}`, { method: 'DELETE', headers: _h() });
      if (r.ok) {
        flash('ok', 'Deleted');
        await loadConnections();
      } else {
        const d = await r.json().catch(() => ({}));
        flash('err', d.detail || `Delete failed (${r.status})`);
      }
    } catch (e: any) {
      flash('err', e?.message || 'Delete failed');
    }
  }

  /* ─── grants ─── */
  function openGrantsFor(c: Connection) {
    grantsConnId = c.id;
    grantsForm = {
      allow_all_users: !!c.allow_all_users,
      users_allowed_text: (c.users_allowed || []).join(', '),
      ldap_groups_allowed_text: (c.ldap_groups_allowed || []).join('\n')
    };
    activeTab = 'grants';
  }

  async function saveGrants() {
    if (!grantsConnId) return;
    grantsSaving = true;
    try {
      const users_allowed = grantsForm.users_allowed_text
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const ldap_groups_allowed = grantsForm.ldap_groups_allowed_text
        .split(/\n+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const body = {
        allow_all_users: grantsForm.allow_all_users,
        users_allowed,
        ldap_groups_allowed
      };
      const r = await fetch(`/api/admin/connections/${grantsConnId}/grant`, {
        method: 'POST',
        headers: _hj(),
        body: JSON.stringify(body)
      });
      if (r.ok) {
        flash('ok', 'Grants saved');
        await loadConnections();
      } else {
        const d = await r.json().catch(() => ({}));
        flash('err', d.detail || `Save failed (${r.status})`);
      }
    } catch (e: any) {
      flash('err', e?.message || 'Save failed');
    }
    grantsSaving = false;
  }

  /* ─── audit ─── */
  async function loadAudit() {
    if (!auditConnId) {
      auditRows = [];
      return;
    }
    auditLoading = true;
    try {
      const params = new URLSearchParams({ limit: '100' });
      const r = await fetch(`/api/admin/connections/${auditConnId}/audit?${params}`, { headers: _h() });
      if (r.ok) {
        let rows: AuditRow[] = (await r.json()) || [];
        // client-side filter
        if (auditDateFrom) {
          const from = new Date(auditDateFrom).getTime();
          rows = rows.filter((x) => new Date(x.created_at).getTime() >= from);
        }
        if (auditDateTo) {
          const to = new Date(auditDateTo).getTime() + 86400000;
          rows = rows.filter((x) => new Date(x.created_at).getTime() <= to);
        }
        if (auditSuccessOnly) rows = rows.filter((x) => !x.error);
        auditRows = rows;
      }
    } catch {}
    auditLoading = false;
  }

  function openAuditFor(c: Connection) {
    auditConnId = c.id;
    activeTab = 'audit';
    loadAudit();
  }

  function fmtDate(iso?: string): string {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch {
      return iso || '—';
    }
  }
  function fmtDur(ms?: number | null): string {
    if (ms == null) return '—';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  /* ─── derived ─── */
  const tabs: { id: 'types' | 'connections' | 'grants' | 'audit'; label: string }[] = [
    { id: 'types', label: 'TYPES' },
    { id: 'connections', label: 'CONNECTIONS' },
    { id: 'grants', label: 'GRANTS' },
    { id: 'audit', label: 'AUDIT' }
  ];

  const currentGrantsConn = $derived(connections.find((c) => c.id === grantsConnId) || null);
  const currentAuditConn = $derived(connections.find((c) => c.id === auditConnId) || null);
</script>

<div class="conn-panel">
  <div class="conn-subnav">
    {#each tabs as t}
      <button class="cc-rail-btn conn-tab" class:active={activeTab === t.id} onclick={() => (activeTab = t.id)}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          {#if t.id === 'types'}<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
          {:else if t.id === 'connections'}<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
          {:else if t.id === 'grants'}<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M20 8v6M23 11h-6"/>
          {:else if t.id === 'audit'}<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
          {/if}
        </svg>
        <span>{t.label}</span>
      </button>
    {/each}
  </div>

  <div class="conn-content">
    <div class="conn-head">
      <h2 class="conn-title">
        {#if activeTab === 'types'}Connector types
        {:else if activeTab === 'connections'}Connections
        {:else if activeTab === 'grants'}Grants
        {:else}Query audit
        {/if}
      </h2>
      <p class="conn-subtitle">
        {#if activeTab === 'types'}Browse available data connector types. Click a card to start a new connection.
        {:else if activeTab === 'connections'}Manage all configured upstream data sources.
        {:else if activeTab === 'grants'}Control which users and AAD groups may use each connection.
        {:else}Inspect query history per connection.
        {/if}
      </p>
    </div>


    <main class="cc-main">
      <div class="cc-head">
        <h1 class="cc-title">
          {#if activeTab === 'types'}Connector types
          {:else if activeTab === 'connections'}Connections
          {:else if activeTab === 'grants'}Grants
          {:else}Query audit
          {/if}
        </h1>
        <p class="cc-subtitle">
          {#if activeTab === 'types'}Browse available data connector types. Click a card to start a new connection.
          {:else if activeTab === 'connections'}Manage all configured upstream data sources.
          {:else if activeTab === 'grants'}Control which users and AAD groups may use each connection.
          {:else}Inspect query history per connection.
          {/if}
        </p>
      </div>

      <!-- ════════════════ TYPES ════════════════ -->
      {#if activeTab === 'types'}
        {#if loadingConnectors}
          <div style="color: var(--pw-muted); font-size: 13px;">Loading…</div>
        {:else if connectors.length === 0}
          <div class="empty-cli">
            $ {connectorsError || 'no connector types registered.'}
          </div>
        {:else}
          <div class="type-grid">
            {#each connectors as c}
              <button class="type-card" onclick={() => openCreate(c.type)}>
                <div class="type-logo-wrap">
                  <img src={logoUrl(c.type)} alt={c.title || c.type} class="type-logo" onerror={onLogoError} />
                </div>
                <div class="type-card-head">
                  <span class="type-title">{c.title || c.type}</span>
                  <span class="type-kind">{c.kind}</span>
                </div>
                <div class="type-desc">{c.description || ''}</div>
                <div class="type-foot">+ new connection</div>
              </button>
            {/each}
          </div>
        {/if}

      <!-- ════════════════ CONNECTIONS ════════════════ -->
      {:else if activeTab === 'connections'}
        <div style="display: flex; justify-content: flex-end; margin-bottom: 14px;">
          <button class="btn-primary" onclick={() => openCreate('')}>+ NEW CONNECTION</button>
        </div>

        {#if loadingConnections}
          <div style="color: var(--pw-muted); font-size: 13px;">Loading…</div>
        {:else if connections.length === 0}
          <div class="empty-cli">$ no connections configured. Click + NEW CONNECTION to start.</div>
        {:else}
          <table class="ds-table">
            <thead>
              <tr>
                <th>NAME</th>
                <th>TYPE</th>
                <th>STATUS</th>
                <th>GRANTS</th>
                <th>CREATED</th>
                <th style="text-align: right;">ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {#each connections as c}
                <tr>
                  <td style="font-weight: 600;">{c.name}</td>
                  <td>
                    <div class="conn-type-cell">
                      <img src={logoUrl(c.connector_type)} alt="" class="type-logo-sm" onerror={onLogoError} />
                      <code class="mono">{c.connector_type}</code>
                    </div>
                  </td>
                  <td>
                    {#if c.enabled}
                      <span class="pill pill-ok">enabled</span>
                    {:else}
                      <span class="pill pill-off">disabled</span>
                    {/if}
                  </td>
                  <td style="font-size: 11.5px; color: var(--pw-ink);">
                    {#if c.allow_all_users}all users
                    {:else}
                      {(c.users_allowed?.length || 0)} users · {(c.ldap_groups_allowed?.length || 0)} groups
                    {/if}
                  </td>
                  <td style="font-size: 11.5px; color: var(--pw-muted);">{fmtDate(c.created_at)}</td>
                  <td style="text-align: right;">
                    <span class="row-actions">
                      <button class="link" onclick={() => openEdit(c)}>edit</button>
                      <span class="sep">·</span>
                      <button class="link" onclick={() => testSaved(c.id)}>test</button>
                      <span class="sep">·</span>
                      <button class="link" onclick={() => openGrantsFor(c)}>grant</button>
                      <span class="sep">·</span>
                      <button class="link" onclick={() => openAuditFor(c)}>audit</button>
                      <span class="sep">·</span>
                      <button class="link link-danger" onclick={() => deleteConn(c)}>delete</button>
                    </span>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

      <!-- ════════════════ GRANTS ════════════════ -->
      {:else if activeTab === 'grants'}
        <div style="margin-bottom: 14px;">
          <label style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted); font-weight: 600;">
            Connection
          </label>
          <select bind:value={grantsConnId} onchange={() => {
            const c = connections.find((x) => x.id === grantsConnId);
            if (c) openGrantsFor(c);
          }} class="input" style="width: 100%; max-width: 480px; display: block; margin-top: 4px;">
            <option value={null}>— pick a connection —</option>
            {#each connections as c}
              <option value={c.id}>{c.name} ({c.connector_type})</option>
            {/each}
          </select>
        </div>

        {#if currentGrantsConn}
          <div class="form-card">
            <div class="field">
              <label class="lbl">
                <input type="checkbox" bind:checked={grantsForm.allow_all_users} />
                Allow all authenticated users
              </label>
              <p class="hint">When on, the lists below are ignored — every signed-in user may query this connection.</p>
            </div>

            <div class="field">
              <label class="lbl">Users allowed</label>
              <textarea rows="4" bind:value={grantsForm.users_allowed_text} class="input" placeholder="user_id_1, user_id_2, ..."></textarea>
              <p class="hint">Comma- or whitespace-separated user IDs.</p>
            </div>

            <div class="field">
              <label class="lbl">LDAP / AAD groups allowed</label>
              <textarea rows="6" bind:value={grantsForm.ldap_groups_allowed_text} class="input" placeholder="11111111-2222-3333-4444-555555555555&#10;...one GUID per line..."></textarea>
              <p class="hint">One AAD group GUID per line.</p>
            </div>

            <div style="display: flex; gap: 8px; margin-top: 12px;">
              <button class="btn-primary" disabled={grantsSaving} onclick={saveGrants}>
                {grantsSaving ? 'SAVING…' : 'SAVE GRANTS'}
              </button>
            </div>
          </div>
        {:else}
          <div class="empty-cli">$ select a connection above to configure grants.</div>
        {/if}

      <!-- ════════════════ AUDIT ════════════════ -->
      {:else if activeTab === 'audit'}
        <div class="audit-filters">
          <div class="field" style="flex: 1; min-width: 220px;">
            <label class="lbl">Connection</label>
            <select bind:value={auditConnId} onchange={loadAudit} class="input">
              <option value={null}>— pick a connection —</option>
              {#each connections as c}
                <option value={c.id}>{c.name} ({c.connector_type})</option>
              {/each}
            </select>
          </div>
          <div class="field">
            <label class="lbl">From</label>
            <input type="date" bind:value={auditDateFrom} onchange={loadAudit} class="input" />
          </div>
          <div class="field">
            <label class="lbl">To</label>
            <input type="date" bind:value={auditDateTo} onchange={loadAudit} class="input" />
          </div>
          <div class="field" style="align-self: end;">
            <label class="lbl">
              <input type="checkbox" bind:checked={auditSuccessOnly} onchange={loadAudit} />
              Success only
            </label>
          </div>
          <div class="field" style="align-self: end;">
            <button class="btn-ghost" onclick={loadAudit}>↻ REFRESH</button>
          </div>
        </div>

        {#if !auditConnId}
          <div class="empty-cli">$ pick a connection to inspect its query log.</div>
        {:else if auditLoading}
          <div style="color: var(--pw-muted); font-size: 13px;">Loading…</div>
        {:else if auditRows.length === 0}
          <div class="empty-cli">$ no audit rows for the current filter.</div>
        {:else}
          <table class="ds-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>USER</th>
                <th>ACTION</th>
                <th>SQL</th>
                <th style="text-align: right;">ROWS</th>
                <th style="text-align: right;">DURATION</th>
                <th>ERROR</th>
              </tr>
            </thead>
            <tbody>
              {#each auditRows as r}
                <tr>
                  <td style="font-size: 11.5px; color: var(--pw-muted); white-space: nowrap;">{fmtDate(r.created_at)}</td>
                  <td style="font-size: 11.5px;">{r.user_id ?? '—'}</td>
                  <td style="font-size: 11.5px;"><code class="mono">{r.action}</code></td>
                  <td style="max-width: 420px;">
                    {#if r.sql_text}
                      <button class="sql-toggle" onclick={() => (auditExpandedId = auditExpandedId === r.id ? null : r.id)}>
                        {auditExpandedId === r.id ? '▾' : '▸'}
                        <span class="sql-one">{r.sql_text.replace(/\s+/g, ' ').slice(0, 80)}{r.sql_text.length > 80 ? '…' : ''}</span>
                      </button>
                      {#if auditExpandedId === r.id}
                        <pre class="sql-block">{r.sql_text}</pre>
                      {/if}
                    {:else}
                      <span style="color: var(--pw-muted);">—</span>
                    {/if}
                  </td>
                  <td style="text-align: right; font-variant-numeric: tabular-nums;">{r.row_count ?? '—'}</td>
                  <td style="text-align: right; font-variant-numeric: tabular-nums;">{fmtDur(r.duration_ms)}</td>
                  <td style="color: var(--pw-error, #c0392b); font-size: 11.5px;">{r.error || ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      {/if}
  </div>
</div>

  <!-- ════════════════ CREATE / EDIT MODAL ════════════════ -->
  {#if showModal}
    <div class="modal-backdrop" onclick={(e) => { if (e.target === e.currentTarget) closeModal(); }} role="presentation">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-head">
          <div>
            <div class="modal-title">{modalMode === 'edit' ? 'Edit connection' : 'New connection'}</div>
            <div class="modal-sub">
              {modalMode === 'edit'
                ? 'Update name, config, or rotate credentials.'
                : 'Configure a new upstream data source.'}
            </div>
          </div>
          <button class="modal-close" onclick={closeModal} aria-label="close">×</button>
        </div>

        <div class="modal-body">
          <div class="field">
            <label class="lbl">Name</label>
            <input type="text" bind:value={form.name} class="input" placeholder="e.g. prod_warehouse" />
          </div>

          <div class="field">
            <label class="lbl">Connector type</label>
            <div class="type-picker">
              <button
                type="button"
                class="type-picker-btn"
                disabled={modalMode === 'edit'}
                onclick={() => { if (modalMode !== 'edit') typePickerOpen = !typePickerOpen; }}
              >
                {#if form.connector_type}
                  <img src={logoUrl(form.connector_type)} alt="" class="type-logo-sm" onerror={onLogoError} />
                  <span class="type-picker-label">
                    {(connectors.find(x => x.type === form.connector_type)?.title) || form.connector_type}
                  </span>
                  <span class="type-picker-kind">
                    {(connectors.find(x => x.type === form.connector_type)?.kind) || ''}
                  </span>
                {:else}
                  <span class="type-picker-placeholder">— pick a connector —</span>
                {/if}
                <span class="type-picker-caret">▾</span>
              </button>
              {#if typePickerOpen}
                <div class="type-picker-popup">
                  {#each connectors as c}
                    <button
                      type="button"
                      class="type-picker-item"
                      class:active={form.connector_type === c.type}
                      onclick={() => {
                        form.connector_type = c.type;
                        typePickerOpen = false;
                        onTypeChange();
                      }}
                    >
                      <img src={logoUrl(c.type)} alt="" class="type-logo-md" onerror={onLogoError} />
                      <div class="type-picker-item-body">
                        <div class="type-picker-item-title">{c.title || c.type}</div>
                        <div class="type-picker-item-kind">{c.kind} · {c.description || ''}</div>
                      </div>
                    </button>
                  {/each}
                </div>
              {/if}
            </div>
          </div>

          {#if loadingFields}
            <div style="color: var(--pw-muted); font-size: 13px; padding: 12px 0;">Loading fields…</div>
          {:else if fields}
            <div class="form-section">
              <div class="form-section-title">Configuration</div>
              {#each schemaProps(fields.config) as [key, prop]}
                {@const w = widgetType(prop)}
                <div class="field">
                  <label class="lbl">
                    {prettyLabel(key)}
                    {#if (fields.config.required || []).includes(key)}<span class="req">*</span>{/if}
                  </label>
                  {#if w === 'textarea'}
                    <textarea rows="6" bind:value={form.config[key]} class="input"></textarea>
                  {:else if w === 'password'}
                    <input type="password" bind:value={form.config[key]} class="input" />
                  {:else if w === 'number'}
                    <input type="number" bind:value={form.config[key]} class="input" />
                  {:else if w === 'boolean'}
                    <label class="lbl" style="display: flex; gap: 8px; font-weight: 400;">
                      <input type="checkbox" bind:checked={form.config[key]} />
                      {prop?.description || prettyLabel(key)}
                    </label>
                  {:else}
                    <input type="text" bind:value={form.config[key]} class="input" />
                  {/if}
                  {#if prop?.description && w !== 'boolean'}
                    <p class="hint">{prop.description}</p>
                  {/if}
                </div>
              {/each}
            </div>

            <div class="form-section">
              <div class="form-section-title">Credentials</div>
              {#if modalMode === 'edit'}
                <p class="hint" style="margin: 0 0 12px;">Leave blank to keep existing credentials. Fill any field to rotate.</p>
              {/if}
              {#each schemaProps(fields.credentials) as [key, prop]}
                {@const w = widgetType(prop)}
                <div class="field">
                  <label class="lbl">
                    {prettyLabel(key)}
                    {#if modalMode === 'create' && (fields.credentials.required || []).includes(key)}<span class="req">*</span>{/if}
                  </label>
                  {#if w === 'textarea'}
                    <textarea rows="6" bind:value={form.credentials[key]} class="input" placeholder={modalMode === 'edit' ? '(unchanged)' : ''}></textarea>
                  {:else if w === 'password'}
                    <input type="password" bind:value={form.credentials[key]} class="input" placeholder={modalMode === 'edit' ? '(unchanged)' : ''} />
                  {:else if w === 'number'}
                    <input type="number" bind:value={form.credentials[key]} class="input" />
                  {:else if w === 'boolean'}
                    <label class="lbl" style="display: flex; gap: 8px; font-weight: 400;">
                      <input type="checkbox" bind:checked={form.credentials[key]} />
                      {prop?.description || prettyLabel(key)}
                    </label>
                  {:else}
                    <input type="text" bind:value={form.credentials[key]} class="input" placeholder={modalMode === 'edit' ? '(unchanged)' : ''} />
                  {/if}
                  {#if prop?.description && w !== 'boolean'}
                    <p class="hint">{prop.description}</p>
                  {/if}
                </div>
              {/each}
            </div>
          {:else if form.connector_type}
            <div style="color: var(--pw-muted); font-size: 13px;">No field schema returned.</div>
          {/if}
        </div>

        <div class="modal-foot">
          <button class="btn-ghost" onclick={closeModal}>CANCEL</button>
          <span style="flex: 1;"></span>
          <button class="btn-ghost" disabled={testing || !form.connector_type} onclick={testConnection}>
            {testing ? 'TESTING…' : 'TEST'}
          </button>
          <button class="btn-primary" disabled={saving || !form.name || !form.connector_type} onclick={saveConnection}>
            {saving ? 'SAVING…' : modalMode === 'edit' ? 'SAVE' : 'CREATE'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  {#if toast}
    <div class="toast" class:toast-err={toast.kind === 'err'}>{toast.msg}</div>
  {/if}

<style>
  /* ─── shell + rail (mirrors command-center) ─── */
  :global(.cc-shell) {
    display: grid;
    grid-template-columns: 220px 1fr;
    background: var(--pw-bg);
    min-height: calc(100vh - 56px);
    font-family: var(--pw-font-body, 'Inter', system-ui, sans-serif);
    color: var(--pw-ink);
  }
  :global(.cc-rail) {
    position: sticky;
    top: 0;
    align-self: start;
    height: calc(100vh - 56px);
    overflow-y: auto;
    overscroll-behavior: contain;
    background: var(--pw-bg-alt);
    border-right: 1px solid var(--pw-border);
    padding: 0 8px 24px;
  }
  :global(.cc-rail-group) { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
  :global(.cc-rail-grouplabel) {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--pw-muted);
    padding: 12px 14px 6px;
    font-weight: 600;
  }
  :global(.cc-rail-btn) {
    display: flex; align-items: center; gap: 10px;
    width: 100%; text-align: left;
    background: transparent; border: none;
    padding: 8px 12px; border-radius: var(--pw-radius-sm);
    font-size: 12px; color: var(--pw-ink);
    font-family: inherit; cursor: pointer;
    border-left: 2px solid transparent;
    line-height: 1.3;
  }
  :global(.cc-rail-btn svg) { width: 14px; height: 14px; flex: 0 0 auto; color: var(--pw-muted); }
  :global(.cc-rail-btn:hover) { background: rgba(201, 99, 66, 0.04); }
  :global(.cc-rail-btn.active) {
    background: rgba(201, 99, 66, 0.08);
    color: var(--pw-accent);
    font-weight: 600;
  }
  :global(.cc-rail-btn.active svg) { color: var(--pw-accent); }

  :global(.cc-main) {
    padding: 32px 48px 80px 48px;
    max-width: 1280px;
    margin: 0 auto;
    width: 100%;
    box-sizing: border-box;
  }
  @media (max-width: 1024px) { :global(.cc-main) { padding: 24px; } }
  @media (max-width: 640px) {
    :global(.cc-shell) { grid-template-columns: 1fr; }
    :global(.cc-rail) { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--pw-border); }
    :global(.cc-main) { padding: 16px; }
  }

  .cc-head { margin-bottom: 24px; }
  .cc-title {
    font-family: var(--pw-font-serif, 'Source Serif 4', Georgia, serif);
    font-size: 24px;
    font-weight: 600;
    margin: 0 0 4px 0;
    color: var(--pw-ink);
    letter-spacing: -0.01em;
  }
  .cc-subtitle { font-size: 12px; color: var(--pw-muted); margin: 0; }

  /* ─── types grid ─── */
  .type-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
  }
  .type-card {
    text-align: left;
    background: var(--pw-surface, #faf9f5);
    border: 1px solid var(--pw-border);
    padding: 16px;
    cursor: pointer;
    font-family: inherit;
    color: var(--pw-ink);
    transition: border-color 0.12s, background 0.12s;
    display: flex; flex-direction: column; gap: 10px;
    min-height: 140px;
  }
  .type-card:hover { border-color: var(--pw-accent); background: var(--pw-bg); }
  .type-logo-wrap {
    display: flex; align-items: center; justify-content: center;
    width: 48px; height: 48px; margin-bottom: 6px;
    background: var(--pw-bg); border: 1px solid var(--pw-border); border-radius: 8px;
  }
  .type-logo { width: 32px; height: 32px; object-fit: contain; }
  .type-logo-sm { width: 18px; height: 18px; object-fit: contain; flex: 0 0 auto; vertical-align: middle; }
  .type-logo-md { width: 28px; height: 28px; object-fit: contain; flex: 0 0 auto; }
  .conn-type-cell { display: flex; align-items: center; gap: 8px; }
  /* custom type picker (modal) */
  .type-picker { position: relative; }
  .type-picker-btn {
    display: flex; align-items: center; gap: 10px;
    width: 100%; padding: 10px 12px;
    background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: 6px;
    font: inherit; color: var(--pw-ink); cursor: pointer; text-align: left;
  }
  .type-picker-btn:hover:not(:disabled) { border-color: var(--pw-accent); }
  .type-picker-btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .type-picker-label { flex: 1; font-weight: 500; }
  .type-picker-kind { font-size: 11px; color: var(--pw-muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .type-picker-placeholder { flex: 1; color: var(--pw-muted); }
  .type-picker-caret { color: var(--pw-muted); font-size: 12px; }
  .type-picker-popup {
    position: absolute; top: calc(100% + 4px); left: 0; right: 0;
    background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.08); z-index: 50;
    max-height: 360px; overflow-y: auto;
  }
  .type-picker-item {
    display: flex; align-items: center; gap: 12px; width: 100%;
    padding: 10px 12px; background: transparent; border: none; border-bottom: 1px solid var(--pw-border);
    text-align: left; cursor: pointer; color: var(--pw-ink);
  }
  .type-picker-item:last-child { border-bottom: none; }
  .type-picker-item:hover { background: var(--pw-bg-alt); }
  .type-picker-item.active { background: rgba(201,99,66,0.08); }
  .type-picker-item-body { flex: 1; min-width: 0; }
  .type-picker-item-title { font-weight: 600; font-size: 13px; color: var(--pw-ink); }
  .type-picker-item-kind { font-size: 11px; color: var(--pw-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .type-title {
    font-family: var(--pw-font-serif, Georgia, serif);
    font-size: 17px; font-weight: 600;
  }
  .type-kind {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--pw-muted); border: 1px solid var(--pw-border); padding: 2px 6px;
  }
  .type-desc { font-size: 12.5px; color: var(--pw-ink); line-height: 1.5; flex: 1; }
  .type-foot { font-size: 11px; color: var(--pw-accent); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }

  /* ─── table ─── */
  .ds-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 1px solid var(--pw-border);
    font-size: 12.5px;
  }
  .ds-table thead th {
    background: var(--pw-bg-alt);
    color: var(--pw-muted);
    font-size: 11.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--pw-border);
  }
  .ds-table tbody td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--pw-border);
    color: var(--pw-ink);
    vertical-align: top;
  }
  .ds-table tbody tr:last-child td { border-bottom: none; }
  .ds-table tbody tr:hover { background: var(--pw-bg-alt); }

  .pill {
    display: inline-block;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
    border-radius: var(--pw-radius-sm);
  }
  .pill-ok { background: rgba(46, 125, 50, 0.12); color: var(--pw-success, #2e7d32); }
  .pill-off { background: rgba(135, 131, 122, 0.18); color: var(--pw-muted); }

  .row-actions { display: inline-flex; align-items: center; gap: 6px; }
  .link {
    background: none; border: none; padding: 0;
    color: var(--pw-accent);
    font-size: 12px; font-family: inherit; cursor: pointer;
  }
  .link:hover { text-decoration: underline; }
  .link-danger { color: var(--pw-error, #c0392b); }
  .sep { color: var(--pw-muted); font-size: 12px; }

  /* ─── buttons ─── */
  .btn-primary {
    background: var(--pw-accent);
    color: #fff;
    border: 1px solid var(--pw-accent);
    padding: 8px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-family: inherit;
    cursor: pointer;
    transition: filter 0.12s;
  }
  .btn-primary:hover:not(:disabled) { filter: brightness(0.95); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-ghost {
    background: var(--pw-bg-alt);
    border: 1px solid var(--pw-border);
    color: var(--pw-ink);
    padding: 8px 14px;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-family: inherit;
    cursor: pointer;
    transition: background 0.12s;
  }
  .btn-ghost:hover:not(:disabled) { background: var(--pw-surface); }
  .btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

  /* ─── forms ─── */
  .field { margin-bottom: 14px; }
  .lbl {
    display: block;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--pw-muted);
    font-weight: 600;
    margin-bottom: 4px;
  }
  .req { color: var(--pw-error, #c0392b); margin-left: 4px; }
  .hint { font-size: 11.5px; color: var(--pw-muted); margin: 4px 0 0 0; line-height: 1.4; }
  .input {
    width: 100%;
    border: 1px solid var(--pw-border);
    background: var(--pw-bg);
    padding: 7px 10px;
    font-family: inherit;
    font-size: 12.5px;
    color: var(--pw-ink);
    box-sizing: border-box;
    border-radius: var(--pw-radius-sm);
  }
  .input:focus { outline: none; border-color: var(--pw-accent); }
  textarea.input { font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: 12px; resize: vertical; }
  select.input { padding-right: 28px; }

  .form-card {
    background: var(--pw-surface, #faf9f5);
    border: 1px solid var(--pw-border);
    padding: 20px;
    max-width: 720px;
  }
  .form-section { margin-top: 22px; }
  .form-section-title {
    font-family: var(--pw-font-serif, Georgia, serif);
    font-size: 15px;
    font-weight: 600;
    color: var(--pw-ink);
    padding-bottom: 6px;
    margin-bottom: 14px;
    border-bottom: 1px solid var(--pw-border);
  }

  .audit-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 16px;
    align-items: flex-end;
  }

  .empty-cli {
    font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 12.5px;
    color: var(--pw-muted);
    background: var(--pw-bg-alt);
    border: 1px dashed var(--pw-border);
    padding: 24px 20px;
  }

  .mono { font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: 11.5px; color: var(--pw-ink); }

  .sql-toggle {
    background: none; border: none; padding: 0;
    color: var(--pw-ink); font-family: inherit; cursor: pointer;
    text-align: left;
    display: inline-flex; align-items: baseline; gap: 6px;
    width: 100%;
  }
  .sql-one {
    font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 11.5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    flex: 1;
  }
  .sql-block {
    background: #1a1614;
    color: #e8e3d6;
    font-family: var(--pw-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 12px;
    padding: 12px 14px;
    margin: 8px 0 4px;
    white-space: pre-wrap;
    word-break: break-word;
    border-radius: var(--pw-radius-sm);
    line-height: 1.5;
  }

  /* ─── modal ─── */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0, 0, 0, 0.45);
    z-index: 300;
    display: flex; align-items: flex-start; justify-content: center;
    padding: 48px 16px;
    overflow-y: auto;
  }
  .modal {
    background: var(--pw-bg);
    border: 1px solid var(--pw-border);
    width: 100%;
    max-width: 720px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.15);
    display: flex; flex-direction: column;
  }
  .modal-head {
    display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;
    padding: 18px 22px;
    border-bottom: 1px solid var(--pw-border);
  }
  .modal-title {
    font-family: var(--pw-font-serif, Georgia, serif);
    font-size: 20px;
    font-weight: 600;
    color: var(--pw-ink);
  }
  .modal-sub { font-size: 12px; color: var(--pw-muted); margin-top: 2px; }
  .modal-close {
    background: none; border: none; padding: 0 6px;
    font-size: 24px; line-height: 1;
    cursor: pointer; color: var(--pw-muted);
  }
  .modal-close:hover { color: var(--pw-ink); }
  .modal-body { padding: 20px 22px; }
  .modal-foot {
    display: flex; gap: 8px; align-items: center;
    padding: 14px 22px;
    border-top: 1px solid var(--pw-border);
    background: var(--pw-bg-alt);
  }

  /* ─── toast ─── */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--pw-ink);
    color: var(--pw-bg);
    padding: 12px 18px;
    font-size: 12.5px;
    z-index: 400;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
    max-width: 420px;
  }
  .toast-err { background: var(--pw-error, #c0392b); color: #fff; }

  /* ─── connectors panel scoped wrappers ─── */
  :global(.conn-panel) {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  :global(.conn-subnav) {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--pw-border);
    padding-bottom: 6px;
    overflow-x: auto;
  }
  :global(.conn-tab) {
    flex: 0 0 auto;
    padding: 6px 12px !important;
  }
  :global(.conn-content) {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  :global(.conn-head) {
    margin-bottom: 4px;
  }
  :global(.conn-title) {
    font-family: var(--pw-font-serif, Georgia, serif);
    font-size: 18px;
    margin: 0 0 4px 0;
    color: var(--pw-ink);
  }
  :global(.conn-subtitle) {
    margin: 0;
    color: var(--pw-muted);
    font-size: 12.5px;
  }

</style>
