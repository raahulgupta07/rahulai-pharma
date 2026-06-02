<script lang="ts">
  import { onMount } from 'svelte';

  type Action = {
    id: string | number;
    name: string;
    description?: string;
    method: string;
    url_template: string;
    header_template?: any;
    body_template?: any;
    requires_approval?: boolean;
    min_approvals?: number;
    enabled?: boolean;
  };

  let projectId = $state<string>('');
  let actions = $state<Action[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let unavailable = $state(false);

  // Modal state
  let modalOpen = $state(false);
  let modalMode = $state<'create' | 'edit'>('create');
  let editingId = $state<string | number | null>(null);
  let f_name = $state('');
  let f_description = $state('');
  let f_method = $state<'POST' | 'PUT' | 'PATCH' | 'DELETE'>('POST');
  let f_url_template = $state('');
  let f_header_template = $state('{}');
  let f_body_template = $state('{}');
  let f_requires_approval = $state(true);
  let f_min_approvals = $state(1);
  let modalErr = $state<string | null>(null);
  let saving = $state(false);

  // Delete confirm
  let deleteId = $state<string | number | null>(null);
  let deleteHard = $state(false);
  let deleteBusy = $state(false);

  function authHeaders(): Record<string, string> {
    const tok = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    const h: Record<string, string> = { 'Accept': 'application/json' };
    if (tok) h['Authorization'] = `Bearer ${tok}`;
    return h;
  }

  function qs(): string {
    return projectId.trim() ? `?project_id=${encodeURIComponent(projectId.trim())}` : '';
  }

  async function load() {
    loading = true;
    error = null;
    unavailable = false;
    try {
      const r = await fetch(`/api/actions/registry${qs()}`, { headers: authHeaders() });
      if (r.status === 503) { unavailable = true; actions = []; return; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      actions = Array.isArray(j) ? j : (j.items || j.actions || []);
    } catch (e: any) {
      error = e?.message || String(e);
      actions = [];
    } finally {
      loading = false;
    }
  }

  function openCreate() {
    modalMode = 'create';
    editingId = null;
    f_name = '';
    f_description = '';
    f_method = 'POST';
    f_url_template = '';
    f_header_template = '{}';
    f_body_template = '{\n  "example": "{{var}}"\n}';
    f_requires_approval = true;
    f_min_approvals = 1;
    modalErr = null;
    modalOpen = true;
  }

  function openEdit(a: Action) {
    modalMode = 'edit';
    editingId = a.id;
    f_name = a.name || '';
    f_description = a.description || '';
    const m = (a.method || 'POST').toUpperCase();
    f_method = (['POST', 'PUT', 'PATCH', 'DELETE'].includes(m) ? m : 'POST') as any;
    f_url_template = a.url_template || '';
    f_header_template = formatJson(a.header_template) || '{}';
    f_body_template = formatJson(a.body_template) || '{}';
    f_requires_approval = a.requires_approval !== false;
    f_min_approvals = typeof a.min_approvals === 'number' ? a.min_approvals : 1;
    modalErr = null;
    modalOpen = true;
  }

  function closeModal() {
    if (saving) return;
    modalOpen = false;
    modalErr = null;
  }

  function formatJson(v: any): string {
    if (v == null) return '';
    if (typeof v === 'string') {
      try { return JSON.stringify(JSON.parse(v), null, 2); } catch { return v; }
    }
    try { return JSON.stringify(v, null, 2); } catch { return ''; }
  }

  function parseJson(s: string, label: string): any {
    const trimmed = (s || '').trim();
    if (!trimmed) return null;
    try { return JSON.parse(trimmed); }
    catch (e: any) { throw new Error(`${label}: ${e?.message || 'invalid JSON'}`); }
  }

  async function save() {
    modalErr = null;
    if (!f_name.trim()) { modalErr = 'Name is required'; return; }
    if (!f_url_template.trim()) { modalErr = 'URL template is required'; return; }
    let header_template: any, body_template: any;
    try {
      header_template = parseJson(f_header_template, 'Headers');
      body_template = parseJson(f_body_template, 'Body');
    } catch (e: any) { modalErr = e.message; return; }

    const payload: any = {
      name: f_name.trim(),
      description: f_description.trim() || null,
      method: f_method,
      url_template: f_url_template.trim(),
      header_template,
      body_template,
      requires_approval: f_requires_approval,
      min_approvals: Number(f_min_approvals) || 1
    };
    if (projectId.trim()) payload.project_id = projectId.trim();

    saving = true;
    try {
      let url: string, method: string;
      if (modalMode === 'create') {
        url = '/api/actions/registry';
        method = 'POST';
      } else {
        url = `/api/actions/registry/${encodeURIComponent(String(editingId))}`;
        method = 'PATCH';
      }
      const r = await fetch(url, {
        method,
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!r.ok) {
        const txt = await r.text().catch(() => '');
        throw new Error(`HTTP ${r.status}${txt ? ': ' + txt.slice(0, 200) : ''}`);
      }
      modalOpen = false;
      await load();
    } catch (e: any) {
      modalErr = e?.message || String(e);
    } finally {
      saving = false;
    }
  }

  async function toggleEnabled(a: Action) {
    const next = !(a.enabled !== false);
    try {
      const r = await fetch(`/api/actions/registry/${encodeURIComponent(String(a.id))}`, {
        method: 'PATCH',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  function openDelete(id: string | number, hard = false) {
    deleteId = id;
    deleteHard = hard;
  }

  function cancelDelete() {
    deleteId = null;
    deleteHard = false;
  }

  async function confirmDelete() {
    if (deleteId == null) return;
    deleteBusy = true;
    try {
      if (deleteHard) {
        const r = await fetch(`/api/actions/registry/${encodeURIComponent(String(deleteId))}`, {
          method: 'DELETE',
          headers: authHeaders()
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      } else {
        // Soft-delete = toggle enabled=false
        const r = await fetch(`/api/actions/registry/${encodeURIComponent(String(deleteId))}`, {
          method: 'PATCH',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: false })
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      }
      deleteId = null;
      deleteHard = false;
      await load();
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      deleteBusy = false;
    }
  }

  function fmtUrl(u: string): string {
    if (!u) return '—';
    return u.length > 60 ? u.slice(0, 60) + '…' : u;
  }

  onMount(load);
</script>

<div class="ar-shell">
  <header class="ar-head">
    <div>
      <h1>Actions Registry</h1>
      <p class="muted">Define agent actions and approval rules</p>
    </div>
    <div class="ctrls">
      <input
        type="text"
        placeholder="Project ID (optional)"
        bind:value={projectId}
        onkeydown={(e) => { if (e.key === 'Enter') load(); }}
      />
      <button class="refresh" onclick={load} disabled={loading}>{loading ? '…' : 'Refresh'}</button>
      <button class="add" onclick={openCreate} disabled={unavailable}>+ Add action</button>
    </div>
  </header>

  {#if unavailable}
    <div class="warn">Action registry not yet migrated — apply migration first.</div>
  {:else if error}
    <p class="err">Failed: {error}</p>
  {/if}

  {#if loading && !actions.length}
    <p class="muted">Loading…</p>
  {:else if !loading && actions.length === 0 && !unavailable}
    <div class="empty">
      No actions defined yet.
      <button class="link" onclick={openCreate}>Add your first action</button>
    </div>
  {:else if actions.length}
    <section class="card">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Method</th>
            <th>URL template</th>
            <th>Requires approval</th>
            <th>Enabled</th>
            <th class="ta-r">Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each actions as a (a.id)}
            <tr>
              <td>
                <strong>{a.name}</strong>
                {#if a.description}<div class="desc">{a.description}</div>{/if}
              </td>
              <td><span class="method m-{(a.method || '').toLowerCase()}">{a.method || '—'}</span></td>
              <td><code title={a.url_template}>{fmtUrl(a.url_template)}</code></td>
              <td>
                {#if a.requires_approval}
                  <span class="pill p-yes">Yes{a.min_approvals && a.min_approvals > 1 ? ` (${a.min_approvals})` : ''}</span>
                {:else}
                  <span class="pill p-no">No</span>
                {/if}
              </td>
              <td>
                <label class="switch">
                  <input type="checkbox" checked={a.enabled !== false} onchange={() => toggleEnabled(a)} />
                  <span class="slider"></span>
                </label>
              </td>
              <td class="ta-r">
                <button class="btn" onclick={() => openEdit(a)}>Edit</button>
                <button class="btn btn-danger" onclick={() => openDelete(a.id, false)}>Delete</button>
                <button class="link link-danger" onclick={() => openDelete(a.id, true)}>Permanently delete</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>
  {/if}
</div>

{#if modalOpen}
  <div
    class="modal-backdrop"
    onclick={closeModal}
    onkeydown={(e) => { if (e.key === 'Escape') closeModal(); }}
    role="button"
    tabindex="-1"
  >
    <div
      class="modal"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}
      role="dialog"
      tabindex="-1"
    >
      <h2>{modalMode === 'create' ? 'Add action' : 'Edit action'}</h2>

      {#if modalErr}
        <div class="modal-err">{modalErr}</div>
      {/if}

      <div class="form-row">
        <label>Name</label>
        <input type="text" bind:value={f_name} placeholder="send_slack_alert" disabled={saving} />
      </div>

      <div class="form-row">
        <label>Description</label>
        <input type="text" bind:value={f_description} placeholder="Send alert to Slack channel" disabled={saving} />
      </div>

      <div class="form-row two">
        <div>
          <label>Method</label>
          <select bind:value={f_method} disabled={saving}>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
        </div>
        <div>
          <label>URL template</label>
          <input type="text" bind:value={f_url_template} placeholder="https://api.example.com/{{path}}" disabled={saving} />
        </div>
      </div>

      <div class="form-row">
        <label>Header template (JSON)</label>
        <textarea bind:value={f_header_template} rows="3" spellcheck="false" disabled={saving}></textarea>
      </div>

      <div class="form-row">
        <label>Body template (JSON) <span class="hint">— use <code>{'{{var}}'}</code> placeholders</span></label>
        <textarea bind:value={f_body_template} rows="6" spellcheck="false" disabled={saving}></textarea>
      </div>

      <div class="form-row two">
        <div>
          <label class="inline">
            <input type="checkbox" bind:checked={f_requires_approval} disabled={saving} />
            Requires approval
          </label>
        </div>
        <div>
          <label>Min approvals</label>
          <input type="number" bind:value={f_min_approvals} min="1" max="10" disabled={saving || !f_requires_approval} />
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn" onclick={closeModal} disabled={saving}>Cancel</button>
        <button class="btn btn-primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
    </div>
  </div>
{/if}

{#if deleteId != null}
  <div
    class="modal-backdrop"
    onclick={cancelDelete}
    onkeydown={(e) => { if (e.key === 'Escape') cancelDelete(); }}
    role="button"
    tabindex="-1"
  >
    <div
      class="modal modal-sm"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}
      role="dialog"
      tabindex="-1"
    >
      <h2>{deleteHard ? 'Permanently delete?' : 'Disable action?'}</h2>
      <p class="muted">
        {#if deleteHard}
          This will permanently remove the action from the registry. This cannot be undone.
        {:else}
          The action will be disabled but kept in the registry. You can re-enable it later.
        {/if}
      </p>
      <div class="modal-actions">
        <button class="btn" onclick={cancelDelete} disabled={deleteBusy}>Cancel</button>
        <button
          class="btn"
          class:btn-danger={deleteHard}
          class:btn-primary={!deleteHard}
          onclick={confirmDelete}
          disabled={deleteBusy}
        >{deleteBusy ? '…' : (deleteHard ? 'Permanently delete' : 'Disable')}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .ar-shell {
    padding: 24px 32px;
    max-width: 1200px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .ar-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .ar-head h1 {
    font-size: 22px;
    margin: 0 0 4px 0;
    font-weight: 600;
    font-family: 'Source Serif Pro', Georgia, serif;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .err { color: #b3261e; font-size: 13px; }
  .warn {
    background: #fff7e6;
    border: 1px solid #f0c674;
    color: #8a5a00;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 16px;
  }
  .ctrls { display: flex; gap: 8px; align-items: center; }
  .ctrls input[type="text"] {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    min-width: 180px;
  }
  .ctrls button {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  .ctrls button:hover { background: #f7f3e9; }
  .ctrls button:disabled { opacity: 0.5; cursor: default; }
  .ctrls button.add {
    background: #c96342;
    color: #fff;
    border-color: #c96342;
  }
  .ctrls button.add:hover { background: #b85638; }

  .card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    overflow: hidden;
  }
  .empty {
    background: #fff;
    border: 1px dashed #d6d1c2;
    border-radius: 6px;
    padding: 32px;
    text-align: center;
    color: #777;
    font-size: 14px;
  }
  .empty .link { margin-left: 6px; }

  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
    vertical-align: middle;
  }
  th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
    background: #faf7f0;
  }
  td .desc { font-size: 12px; color: #888; margin-top: 2px; }
  td code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
    font-family: ui-monospace, Menlo, monospace;
  }
  td.ta-r, th.ta-r { text-align: right; }

  .method {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 600;
    font-family: ui-monospace, Menlo, monospace;
    background: #eee;
    color: #555;
  }
  .method.m-post { background: #e6f4ea; color: #1e7e34; }
  .method.m-put { background: #fef7e0; color: #8a5a00; }
  .method.m-patch { background: #e8f0fe; color: #1967d2; }
  .method.m-delete { background: #fce8e6; color: #b3261e; }

  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
    background: #eee;
    color: #555;
  }
  .pill.p-yes { background: #fef0e8; color: #c96342; }
  .pill.p-no { background: #eef2f5; color: #555; }

  .switch {
    position: relative;
    display: inline-block;
    width: 36px;
    height: 20px;
    cursor: pointer;
  }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute;
    inset: 0;
    background: #d6d1c2;
    border-radius: 20px;
    transition: 0.2s;
  }
  .slider:before {
    content: "";
    position: absolute;
    width: 14px;
    height: 14px;
    left: 3px;
    top: 3px;
    background: #fff;
    border-radius: 50%;
    transition: 0.2s;
  }
  .switch input:checked + .slider { background: #c96342; }
  .switch input:checked + .slider:before { transform: translateX(16px); }

  .btn {
    padding: 5px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
    margin-left: 4px;
    color: #1f1c17;
  }
  .btn:hover { background: #f7f3e9; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn-primary { background: #c96342; color: #fff; border-color: #c96342; }
  .btn-primary:hover { background: #b85638; }
  .btn-danger { color: #b3261e; }
  .btn-danger:hover { background: #fce8e6; }

  .link {
    background: none;
    border: none;
    color: #c96342;
    text-decoration: underline;
    cursor: pointer;
    font-size: 12px;
    padding: 4px 0;
    margin-left: 6px;
  }
  .link:hover { color: #b85638; }
  .link-danger { color: #b3261e; }
  .link-danger:hover { color: #8a1e16; }

  /* Modal */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(31, 28, 23, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    border: none;
    padding: 16px;
  }
  .modal {
    background: #fff;
    border-radius: 8px;
    padding: 24px;
    max-width: 640px;
    width: 100%;
    max-height: 90vh;
    overflow-y: auto;
    box-shadow: 0 12px 32px rgba(0,0,0,0.15);
  }
  .modal-sm { max-width: 440px; }
  .modal h2 {
    margin: 0 0 16px 0;
    font-size: 18px;
    font-weight: 600;
    font-family: 'Source Serif Pro', Georgia, serif;
  }
  .modal-err {
    background: #fce8e6;
    border: 1px solid #b3261e;
    color: #b3261e;
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
    margin-bottom: 12px;
  }
  .form-row { margin-bottom: 12px; }
  .form-row.two {
    display: grid;
    grid-template-columns: 160px 1fr;
    gap: 12px;
  }
  .form-row label {
    display: block;
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
    font-weight: 500;
  }
  .form-row label.inline {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-transform: none;
    letter-spacing: 0;
    font-size: 13px;
    color: #1f1c17;
    margin-top: 22px;
  }
  .form-row label .hint { color: #888; font-weight: 400; text-transform: none; letter-spacing: 0; }
  .form-row label .hint code { background: #f7f3e9; padding: 1px 4px; border-radius: 3px; font-size: 11px; }
  .form-row input[type="text"],
  .form-row input[type="number"],
  .form-row select,
  .form-row textarea {
    width: 100%;
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    box-sizing: border-box;
    font-family: inherit;
  }
  .form-row textarea {
    font-family: ui-monospace, Menlo, monospace;
    font-size: 12px;
    resize: vertical;
  }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid #f0ebde;
  }
</style>
