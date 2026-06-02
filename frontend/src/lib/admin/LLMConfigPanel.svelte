<script lang="ts">
  import { onMount } from 'svelte';
  import ModelPickerModal from './ModelPickerModal.svelte';

  type Key = {
    id: number;
    key_label: string;
    key_suffix: string;
    provider: string;
    enabled: boolean;
    created_at: string;
    last_used_at: string | null;
    notes: string | null;
  };

  type ModelEntry = { value: string; default: string; env: string | null; desc: string; triggers?: string[]; used_by?: string[] };
  type Models = {
    chat_model: ModelEntry;
    mid_model: ModelEntry;
    deep_model: ModelEntry;
    reasoning_model: ModelEntry;
    ultra_model: ModelEntry;
    lite_model: ModelEntry;
    embedding_model: ModelEntry;
    catalog?: { chat: string[]; deep: string[]; lite: string[]; embedding: string[] };
  };
  type ModelRowKey = 'chat_model' | 'mid_model' | 'deep_model' | 'reasoning_model' | 'ultra_model' | 'lite_model' | 'embedding_model';
  type ModelType = 'chat' | 'deep' | 'lite' | 'embedding';
  const MODEL_ROW_KEYS: ModelRowKey[] = ['chat_model','mid_model','deep_model','reasoning_model','ultra_model','lite_model','embedding_model'];

  type PoolStat = {
    key_suffix: string;
    in_flight: number;
    total_ok: number;
    total_429: number;
    cooldown_remaining_s: number;
  };

  let keys = $state<Key[]>([]);
  let models = $state<Models | null>(null);
  let modelEdits = $state<Record<string, string>>({});
  let savingModel = $state<string | null>(null);
  let pool = $state<PoolStat[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // Add form
  let showAdd = $state(false);
  let newLabel = $state('');
  let newKey = $state('');
  let newNotes = $state('');
  let adding = $state(false);

  // Edit key panel state
  let editingKeyId = $state<number | null>(null);
  let editLabel = $state('');
  let editNotes = $state('');
  let editReplaceKey = $state('');
  let editReplaceExpanded = $state(false);
  let editSaving = $state(false);

  // Model picker modal state
  let pickerOpen = $state(false);
  let pickerRowKey = $state<ModelRowKey | null>(null);
  let pickerModelType = $state<ModelType>('chat');
  let pickerCurrent = $state('');

  // Expanded model row (Option C — click row to reveal details)
  let expandedModelRow = $state<ModelRowKey | null>(null);
  function toggleModelRow(k: ModelRowKey) {
    expandedModelRow = expandedModelRow === k ? null : k;
  }

  // Sync state
  let syncStatus = $state<{ count: number; last_synced_at: string | null }>({ count: 0, last_synced_at: null });
  let syncing = $state(false);

  let toast = $state<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  let toastTimer: any = null;
  function flash(kind: 'ok' | 'err', msg: string) {
    toast = { kind, msg };
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 3000);
  }

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  }

  async function loadAll() {
    loading = true;
    error = null;
    try {
      const [kr, mr, pr] = await Promise.all([
        fetch('/api/admin/llm/keys', { headers: _h() }),
        fetch('/api/admin/llm/models', { headers: _h() }),
        fetch('/api/admin/openrouter/pool', { headers: _h() })
      ]);
      if (!kr.ok) throw new Error(`keys ${kr.status}`);
      keys = (await kr.json()).keys || [];
      models = mr.ok ? await mr.json() : null;
      pool = pr.ok ? (await pr.json()).keys || [] : [];
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function addKey() {
    if (!newLabel.trim() || !newKey.trim()) {
      flash('err', 'label + key required');
      return;
    }
    adding = true;
    try {
      const r = await fetch('/api/admin/llm/keys', {
        method: 'POST',
        headers: _h(),
        body: JSON.stringify({ label: newLabel, raw_key: newKey, notes: newNotes || null })
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt);
      }
      flash('ok', 'key added');
      newLabel = ''; newKey = ''; newNotes = '';
      showAdd = false;
      await loadAll();
      await refreshPool();
    } catch (e: any) {
      flash('err', e?.message?.slice(0, 100) || 'add failed');
    } finally {
      adding = false;
    }
  }

  async function toggleKey(k: Key) {
    const r = await fetch(`/api/admin/llm/keys/${k.id}`, {
      method: 'PATCH', headers: _h(),
      body: JSON.stringify({ enabled: !k.enabled })
    });
    if (r.ok) { await loadAll(); await refreshPool(); flash('ok', k.enabled ? 'disabled' : 'enabled'); }
    else flash('err', 'toggle failed');
  }

  async function deleteKey(k: Key) {
    if (!confirm(`Delete key "${k.key_label}" (...${k.key_suffix})?`)) return;
    const r = await fetch(`/api/admin/llm/keys/${k.id}`, { method: 'DELETE', headers: _h() });
    if (r.ok) { await loadAll(); await refreshPool(); flash('ok', 'deleted'); }
    else flash('err', 'delete failed');
  }

  async function testKey(k: Key) {
    flash('ok', `testing ...${k.key_suffix}`);
    const r = await fetch(`/api/admin/llm/keys/${k.id}/test`, { method: 'POST', headers: _h() });
    const d = await r.json();
    if (d.ok) flash('ok', `✓ valid · ${d.model_count} models`);
    else flash('err', `✗ ${d.error || d.status}`);
  }

  async function saveModel(key: string) {
    const value = modelEdits[key];
    if (!value || !value.includes('/')) {
      flash('err', 'invalid model id (need provider/model)');
      return;
    }
    savingModel = key;
    try {
      const r = await fetch('/api/admin/llm/models', {
        method: 'PATCH', headers: _h(),
        body: JSON.stringify({ key, value })
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || 'save failed');
      flash('ok', `${key} → ${value}`);
      await loadAll();
    } catch (e: any) {
      flash('err', e?.message?.slice(0, 120) || 'save failed');
    } finally {
      savingModel = null;
    }
  }

  function resetModelEdit(key: string) {
    if (models) modelEdits[key] = (models as any)[key]?.value || '';
  }

  $effect(() => {
    if (models) {
      for (const k of MODEL_ROW_KEYS) {
        if (modelEdits[k] === undefined) modelEdits[k] = (models as any)[k]?.value || '';
      }
    }
  });

  async function refreshPool() {
    try {
      const r = await fetch('/api/admin/llm/pool/refresh', { method: 'POST', headers: _h() });
      if (r.ok) pool = (await r.json()).keys || [];
    } catch {}
  }

  function poolStatFor(suffix: string): PoolStat | undefined {
    return pool.find(p => p.key_suffix === suffix);
  }

  // ---------- Edit key panel ----------
  function openEditKey(k: Key) {
    editingKeyId = k.id;
    editLabel = k.key_label || '';
    editNotes = k.notes || '';
    editReplaceKey = '';
    editReplaceExpanded = false;
  }

  function cancelEdit() {
    editingKeyId = null;
    editLabel = '';
    editNotes = '';
    editReplaceKey = '';
    editReplaceExpanded = false;
  }

  async function saveEditKey() {
    if (editingKeyId == null) return;
    editSaving = true;
    try {
      const body: Record<string, any> = { label: editLabel, notes: editNotes || null };
      if (editReplaceExpanded && editReplaceKey.trim()) {
        body.raw_key = editReplaceKey.trim();
      }
      const r = await fetch(`/api/admin/llm/keys/${editingKeyId}`, {
        method: 'PATCH', headers: _h(),
        body: JSON.stringify(body)
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt);
      }
      flash('ok', 'key updated');
      cancelEdit();
      await loadAll();
    } catch (e: any) {
      flash('err', e?.message?.slice(0, 120) || 'update failed');
    } finally {
      editSaving = false;
    }
  }

  // ---------- Sync models ----------
  async function loadSyncStatus() {
    try {
      const r = await fetch('/api/admin/llm/models/sync-status', { headers: _h() });
      if (r.ok) syncStatus = await r.json();
    } catch {}
  }

  async function syncModels() {
    syncing = true;
    try {
      const r = await fetch('/api/admin/llm/models/sync', { method: 'POST', headers: _h() });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.detail || d.error || 'sync failed');
      flash('ok', `synced ${d.count} models`);
      await loadSyncStatus();
    } catch (e: any) {
      flash('err', e?.message?.slice(0, 120) || 'sync failed');
    } finally {
      syncing = false;
    }
  }

  function relativeTime(iso: string | null): string {
    if (!iso) return 'never';
    const t = new Date(iso).getTime();
    if (!t) return 'never';
    const diff = Date.now() - t;
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m} min ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  }

  // ---------- Model picker ----------
  const ROW_TO_TYPE: Record<ModelRowKey, ModelType> = {
    chat_model: 'chat',
    mid_model: 'chat',
    deep_model: 'deep',
    reasoning_model: 'deep',
    ultra_model: 'deep',
    lite_model: 'lite',
    embedding_model: 'embedding'
  };

  function openModelPicker(rowKey: ModelRowKey, current: string) {
    pickerRowKey = rowKey;
    pickerModelType = ROW_TO_TYPE[rowKey];
    pickerCurrent = current;
    pickerOpen = true;
  }

  function handleModelSelect(modelId: string) {
    if (pickerRowKey) {
      modelEdits[pickerRowKey] = modelId;
    }
    pickerOpen = false;
  }

  onMount(() => {
    loadAll();
    loadSyncStatus();
  });
</script>

<div class="llm-shell">
  <div class="llm-head">
    <div>
      <div class="llm-title">LLM CONFIG</div>
      <div class="llm-sub">OpenRouter API keys + model selection. Pool refreshes every 60s.</div>
    </div>
    <div class="llm-actions">
      <button class="llm-btn" onclick={syncModels} disabled={syncing}>
        {syncing ? '⟳ Syncing…' : '↻ Sync models'}
      </button>
      <button class="llm-btn" onclick={loadAll}>↻ Refresh</button>
      <button class="llm-btn llm-btn-primary" onclick={() => (showAdd = !showAdd)}>+ Add Key</button>
    </div>
  </div>

  <div class="llm-sync-status">
    Catalog: {syncStatus.count} models · last synced {relativeTime(syncStatus.last_synced_at)}
    {#if !syncing}
      <button class="llm-link-btn" onclick={syncModels}>↻ Sync now</button>
    {/if}
  </div>

  {#if toast}
    <div class="llm-toast llm-toast-{toast.kind}">{toast.msg}</div>
  {/if}

  {#if showAdd}
    <div class="llm-add">
      <div class="llm-add-row">
        <input class="llm-input" placeholder="label (e.g. primary, backup-2)" bind:value={newLabel} />
        <input class="llm-input llm-mono" placeholder="sk-or-v1-..." bind:value={newKey} type="password" />
      </div>
      <input class="llm-input" placeholder="notes (optional)" bind:value={newNotes} />
      <div class="llm-add-actions">
        <button class="llm-btn" onclick={() => (showAdd = false)}>Cancel</button>
        <button class="llm-btn llm-btn-primary" onclick={addKey} disabled={adding}>
          {adding ? 'Adding…' : 'Save Key'}
        </button>
      </div>
    </div>
  {/if}

  {#if error}
    <div class="llm-err">⚠ {error}</div>
  {/if}

  <div class="llm-section">
    <div class="llm-section-h">API KEYS ({keys.length})</div>
    {#if loading}
      <div class="llm-empty">Loading…</div>
    {:else if keys.length === 0}
      <div class="llm-empty">No keys yet. Add one above, or set OPENROUTER_API_KEY in .env (legacy).</div>
    {:else}
      <table class="llm-table">
        <thead>
          <tr>
            <th>LABEL</th><th>KEY</th><th>STATUS</th><th>IN-FLIGHT</th><th>OK / 429</th><th>COOLDOWN</th><th>LAST USED</th><th></th>
          </tr>
        </thead>
        <tbody>
          {#each keys as k (k.id)}
            {@const ps = poolStatFor(k.key_suffix)}
            <tr class:disabled={!k.enabled}>
              <td><strong>{k.key_label}</strong>{#if k.notes}<div class="llm-notes">{k.notes}</div>{/if}</td>
              <td class="llm-mono">…{k.key_suffix}</td>
              <td>
                {#if k.enabled}<span class="llm-pill llm-pill-on">ENABLED</span>
                {:else}<span class="llm-pill llm-pill-off">DISABLED</span>{/if}
              </td>
              <td class="llm-mono">{ps?.in_flight ?? '—'}</td>
              <td class="llm-mono">{ps?.total_ok ?? 0} / {ps?.total_429 ?? 0}</td>
              <td class="llm-mono">
                {#if ps && ps.cooldown_remaining_s > 0}
                  <span class="llm-cool">{ps.cooldown_remaining_s.toFixed(0)}s</span>
                {:else}—{/if}
              </td>
              <td class="llm-mono">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : '—'}</td>
              <td>
                <button class="llm-btn-sm" onclick={() => testKey(k)}>Test</button>
                <button class="llm-btn-sm" onclick={() => openEditKey(k)}>Edit</button>
                <button class="llm-btn-sm" onclick={() => toggleKey(k)}>{k.enabled ? 'Disable' : 'Enable'}</button>
                <button class="llm-btn-sm llm-btn-danger" onclick={() => deleteKey(k)}>Del</button>
              </td>
            </tr>
            {#if editingKeyId === k.id}
              <tr class="llm-edit-row">
                <td colspan="8">
                  <div class="llm-edit-panel">
                    <div class="llm-edit-title">EDIT KEY #{k.id}</div>
                    <div class="llm-edit-grid">
                      <label class="llm-edit-label" for="llm-edit-label-{k.id}">Label</label>
                      <input id="llm-edit-label-{k.id}" class="llm-input" bind:value={editLabel} placeholder="primary-via-ui" />
                      <label class="llm-edit-label" for="llm-edit-notes-{k.id}">Notes</label>
                      <input id="llm-edit-notes-{k.id}" class="llm-input" bind:value={editNotes} placeholder="migrated from .env" />
                    </div>
                    <div class="llm-edit-replace">
                      <button
                        class="llm-edit-toggle"
                        onclick={() => (editReplaceExpanded = !editReplaceExpanded)}
                      >
                        {editReplaceExpanded ? '▾' : '▸'} Replace key
                      </button>
                      {#if editReplaceExpanded}
                        <input
                          class="llm-input llm-mono"
                          type="password"
                          placeholder="paste new sk-or-v1-..."
                          bind:value={editReplaceKey}
                        />
                        <div class="llm-warn">
                          ⚠ Replacing the key will reset usage counters. Old key revoked.
                        </div>
                      {/if}
                    </div>
                    <div class="llm-edit-actions">
                      <button class="llm-btn" onclick={cancelEdit} disabled={editSaving}>Cancel</button>
                      <button class="llm-btn llm-btn-primary" onclick={saveEditKey} disabled={editSaving}>
                        {editSaving ? 'Saving…' : 'Save Changes'}
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {/if}
  </div>

  <div class="llm-section">
    <div class="llm-section-h">MODELS</div>
    {#if models}
      <table class="llm-mtable">
        <thead>
          <tr>
            <th class="llm-mt-role">ROLE</th>
            <th class="llm-mt-model">MODEL</th>
            <th class="llm-mt-tier">TIER</th>
            <th class="llm-mt-summary">USED BY</th>
            <th class="llm-mt-act"></th>
          </tr>
        </thead>
        <tbody>
          {#each [
            { k: 'chat_model' as ModelRowKey,      label: 'CHAT',      tierShort: 'baseline'  },
            { k: 'mid_model' as ModelRowKey,       label: 'MID',       tierShort: 'ANALYSIS'  },
            { k: 'deep_model' as ModelRowKey,      label: 'DEEP',      tierShort: 'AGENTIC'   },
            { k: 'reasoning_model' as ModelRowKey, label: 'REASONING', tierShort: 'REASONING' },
            { k: 'ultra_model' as ModelRowKey,     label: 'ULTRA',     tierShort: 'ULTRA'     },
            { k: 'lite_model' as ModelRowKey,      label: 'LITE',      tierShort: 'LOOKUP'    },
            { k: 'embedding_model' as ModelRowKey, label: 'EMBED',     tierShort: 'embed'     }
          ] as row (row.k)}
            {@const m = (models as any)[row.k] as ModelEntry}
            {@const current = modelEdits[row.k] ?? m.value}
            {@const dirty = current !== m.value}
            {@const expanded = expandedModelRow === row.k}
            {@const summary = (m.used_by && m.used_by[0]) ? m.used_by[0] : m.desc}
            <tr class="llm-mt-row" class:dirty onclick={() => toggleModelRow(row.k)}>
              <td class="llm-mt-role"><strong>{row.label}</strong></td>
              <td class="llm-mt-model llm-mono">{current}{#if dirty}<span class="llm-dirty-dot">●</span>{/if}</td>
              <td class="llm-mt-tier"><span class="llm-tier-pill">{row.tierShort}</span></td>
              <td class="llm-mt-summary">{summary}</td>
              <td class="llm-mt-act"><span class="llm-chev">{expanded ? '▾' : '▸'}</span></td>
            </tr>
            {#if expanded}
              <tr class="llm-mt-expand">
                <td colspan="5">
                  <div class="llm-exp-body">
                    {#if m.desc}<div class="llm-exp-desc">{m.desc}</div>{/if}
                    {#if m.triggers && m.triggers.length}
                      <div class="llm-exp-block">
                        <div class="llm-exp-h">FIRES WHEN</div>
                        {#each m.triggers as t}
                          <div class="llm-exp-line">▸ {t}</div>
                        {/each}
                      </div>
                    {/if}
                    {#if m.used_by && m.used_by.length}
                      <div class="llm-exp-block">
                        <div class="llm-exp-h">USED BY</div>
                        {#each m.used_by as u}
                          <div class="llm-exp-line">• {u}</div>
                        {/each}
                      </div>
                    {/if}
                    <div class="llm-exp-meta">
                      <span><strong>Default:</strong> <code>{m.default}</code></span>
                      {#if m.env}<span><strong>Env var:</strong> <code>{m.env}</code></span>{/if}
                    </div>
                    <div class="llm-exp-actions" onclick={(e) => e.stopPropagation()} role="group">
                      <button class="llm-btn-sm" onclick={(e) => { e.stopPropagation(); openModelPicker(row.k, current); }}>Change model ▾</button>
                      <button class="llm-btn-sm" disabled={!dirty} onclick={(e) => { e.stopPropagation(); resetModelEdit(row.k); }}>Reset edit</button>
                      {#if m.default && current !== m.default}
                        <button class="llm-btn-sm" onclick={(e) => { e.stopPropagation(); modelEdits[row.k] = m.default; }}>Reset to default</button>
                      {/if}
                      <button class="llm-btn-sm llm-btn-primary-sm" disabled={!dirty || savingModel === row.k} onclick={(e) => { e.stopPropagation(); saveModel(row.k); }}>
                        {savingModel === row.k ? 'Saving…' : 'Save'}
                      </button>
                    </div>
                  </div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
      <div class="llm-note">Click any row to expand. Edits are live for next LLM call (no restart). DB row wins over env.</div>
    {/if}
  </div>
</div>

<ModelPickerModal
  bind:open={pickerOpen}
  current={pickerCurrent}
  modelType={pickerModelType}
  onSelect={handleModelSelect}
/>

<style>
  .llm-shell { padding: 20px 24px; max-width: 1200px; }
  .llm-head { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 16px; }
  .llm-title { font-size: 22px; font-weight: 600; color: var(--pw-ink, #2c2a26); }
  .llm-sub { font-size: 12px; color: var(--pw-ink-soft, #6b6557); margin-top: 4px; }
  .llm-actions { display: flex; gap: 8px; }
  .llm-btn {
    padding: 6px 14px; font-size: 12px; font-weight: 500;
    border: 1px solid var(--pw-border, #d8d3c5); background: var(--pw-bg-alt, #f5f1e6);
    color: var(--pw-ink, #2c2a26); cursor: pointer; border-radius: 4px;
  }
  .llm-btn:hover { background: var(--pw-bg, #fff); }
  .llm-btn-primary { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .llm-btn-primary:hover { background: #b3543a; }
  .llm-btn-sm {
    padding: 3px 8px; font-size: 11px; border: 1px solid var(--pw-border, #d8d3c5);
    background: transparent; cursor: pointer; margin-right: 4px; border-radius: 3px;
  }
  .llm-btn-sm:hover { background: var(--pw-bg-alt, #f5f1e6); }
  .llm-btn-danger { color: #c0392b; border-color: #c0392b; }
  .llm-add {
    background: var(--pw-bg-alt, #f5f1e6); border: 1px solid var(--pw-border, #d8d3c5);
    border-radius: 6px; padding: 14px; margin-bottom: 16px;
  }
  .llm-add-row { display: flex; gap: 8px; margin-bottom: 8px; }
  .llm-add-row .llm-input:first-child { flex: 0 0 180px; }
  .llm-add-row .llm-input:last-child { flex: 1; }
  .llm-add-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
  .llm-input {
    padding: 7px 10px; font-size: 13px; border: 1px solid var(--pw-border, #d8d3c5);
    background: #fff; border-radius: 4px; width: 100%;
  }
  .llm-mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; }
  .llm-section { margin-top: 20px; }
  .llm-section-h {
    font-size: 11px; font-weight: 600; color: var(--pw-ink-soft, #6b6557);
    text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;
    border-bottom: 1px solid var(--pw-border, #d8d3c5); padding-bottom: 6px;
  }
  .llm-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .llm-table thead { background: var(--pw-bg-alt, #f5f1e6); }
  .llm-table th {
    text-align: left; padding: 8px 10px; font-size: 10.5px; font-weight: 600;
    color: var(--pw-ink-soft, #6b6557); text-transform: uppercase; letter-spacing: 0.04em;
    border-bottom: 1px solid var(--pw-border, #d8d3c5);
  }
  .llm-table td { padding: 9px 10px; border-bottom: 1px solid var(--pw-border-soft, #ece8de); vertical-align: top; }
  .llm-table tr.disabled { opacity: 0.5; }
  .llm-table tr:hover { background: rgba(201,99,66,0.03); }
  .llm-notes { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 2px; }
  .llm-pill { padding: 2px 8px; font-size: 10.5px; border-radius: 10px; font-weight: 600; }
  .llm-pill-on { background: #d4edda; color: #155724; }
  .llm-pill-off { background: #f8d7da; color: #721c24; }
  .llm-cool { color: #a06000; font-weight: 600; }
  .llm-empty { padding: 20px; text-align: center; color: var(--pw-ink-soft, #6b6557); font-size: 13px; }
  .llm-err { background: #f8d7da; color: #721c24; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13px; }
  .llm-models { background: var(--pw-bg-alt, #f5f1e6); border: 1px solid var(--pw-border, #d8d3c5); border-radius: 6px; padding: 12px 14px; }
  .llm-model-row { display: flex; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--pw-border-soft, #ece8de); }
  .llm-model-row:last-child { border-bottom: none; }
  .llm-model-label { font-size: 11px; font-weight: 600; color: var(--pw-ink-soft, #6b6557); text-transform: uppercase; letter-spacing: 0.05em; width: 80px; }
  .llm-note { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 8px; font-style: italic; }
  .llm-model-edit-row {
    display: grid; grid-template-columns: 140px 1fr;
    gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--pw-border-soft, #ece8de);
  }
  .llm-model-edit-row:last-child { border-bottom: none; }
  .llm-model-label-lg { font-size: 13px; font-weight: 600; color: var(--pw-ink, #2c2a26); letter-spacing: 0.04em; }
  .llm-model-desc { font-size: 11px; color: var(--pw-ink-soft, #6b6557); margin-top: 3px; }
  .llm-usage-block { margin-top: 8px; padding-top: 6px; border-top: 1px dashed var(--pw-border-soft, #ece8de); }
  .llm-usage-h { font-size: 9.5px; font-weight: 700; color: var(--pw-ink-soft, #6b6557); letter-spacing: 0.08em; margin-bottom: 3px; text-transform: uppercase; }
  .llm-usage-line { font-size: 10.5px; color: var(--pw-ink, #2c2a26); line-height: 1.5; padding-left: 4px; }

  /* Option C — dense 3-col model table + expand-on-click */
  .llm-mtable { width: 100%; border-collapse: collapse; font-size: 13px; border: 1px solid var(--pw-border, #d8d3c5); border-radius: 4px; overflow: hidden; }
  .llm-mtable thead { background: var(--pw-bg-alt, #f5f1e6); }
  .llm-mtable th {
    text-align: left; padding: 7px 10px; font-size: 10.5px; font-weight: 600;
    color: var(--pw-ink-soft, #6b6557); text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid var(--pw-border, #d8d3c5);
  }
  .llm-mt-role  { width: 110px; }
  .llm-mt-model { min-width: 280px; }
  .llm-mt-tier  { width: 110px; }
  .llm-mt-act   { width: 28px; text-align: right; }
  .llm-mt-row { cursor: pointer; transition: background 0.1s; }
  .llm-mt-row td { padding: 9px 10px; border-bottom: 1px solid var(--pw-border-soft, #ece8de); vertical-align: middle; }
  .llm-mt-row:hover { background: rgba(201,99,66,0.04); }
  .llm-mt-row.dirty td { background: rgba(255,200,80,0.08); }
  .llm-mt-row .llm-dirty-dot { color: #c96342; margin-left: 6px; }
  .llm-tier-pill {
    display: inline-block; padding: 2px 8px; font-size: 10px; font-weight: 600;
    background: var(--pw-bg-alt, #f5f1e6); color: var(--pw-ink-soft, #6b6557);
    border-radius: 10px; text-transform: uppercase; letter-spacing: 0.05em;
  }
  .llm-mt-summary { color: var(--pw-ink-soft, #6b6557); font-size: 12px; max-width: 360px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .llm-chev { color: var(--pw-ink-soft, #6b6557); font-size: 12px; }
  .llm-mt-expand td { padding: 0 !important; border-bottom: 1px solid var(--pw-border, #d8d3c5); background: var(--pw-bg-alt, #f5f1e6); }
  .llm-exp-body { padding: 14px 18px; }
  .llm-exp-desc { font-size: 12px; color: var(--pw-ink, #2c2a26); margin-bottom: 10px; font-style: italic; }
  .llm-exp-block { margin-top: 10px; }
  .llm-exp-h { font-size: 9.5px; font-weight: 700; color: var(--pw-ink-soft, #6b6557); letter-spacing: 0.08em; margin-bottom: 4px; text-transform: uppercase; }
  .llm-exp-line { font-size: 11px; color: var(--pw-ink, #2c2a26); line-height: 1.6; padding-left: 6px; }
  .llm-exp-meta { margin-top: 12px; padding-top: 8px; border-top: 1px dashed var(--pw-border, #d8d3c5);
    display: flex; gap: 18px; font-size: 11px; color: var(--pw-ink-soft, #6b6557); }
  .llm-exp-meta code { font-family: ui-monospace, monospace; background: var(--pw-bg, #fff); padding: 1px 5px; border-radius: 3px; color: var(--pw-ink, #2c2a26); }
  .llm-exp-actions { margin-top: 12px; display: flex; gap: 6px; flex-wrap: wrap; padding-top: 10px;
    border-top: 1px solid var(--pw-border-soft, #ece8de); }
  .llm-model-edit-r { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  /* select.llm-input + llm-model-custom removed in favor of [Change ▾] button + ModelPickerModal */
  .llm-btn-primary-sm { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .llm-btn-primary-sm:hover { background: #b3543a; }
  .llm-btn-primary-sm:disabled { opacity: 0.4; cursor: not-allowed; }
  .llm-toast {
    position: fixed; bottom: 24px; right: 24px; padding: 10px 16px;
    border-radius: 4px; font-size: 13px; z-index: 9999;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  }
  .llm-toast-ok { background: #155724; color: #fff; }
  .llm-toast-err { background: #721c24; color: #fff; }

  .llm-sync-status {
    font-size: 12px; color: var(--pw-ink-soft, #6b6557);
    padding: 6px 0 12px; display: flex; align-items: center; gap: 8px;
  }
  .llm-link-btn {
    background: none; border: none; color: var(--pw-accent, #c96342);
    cursor: pointer; font-size: 12px; padding: 0; text-decoration: underline;
  }
  .llm-link-btn:hover { color: #b3543a; }

  .llm-edit-row td { padding: 0 !important; background: var(--pw-bg-alt, #f5f1e6); }
  .llm-edit-panel {
    padding: 14px 16px; border-left: 3px solid var(--pw-accent, #c96342);
    margin: 4px 0;
  }
  .llm-edit-title {
    font-size: 11px; font-weight: 600; color: var(--pw-ink-soft, #6b6557);
    text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px;
  }
  .llm-edit-grid {
    display: grid; grid-template-columns: 70px 1fr; gap: 8px 12px;
    align-items: center; margin-bottom: 12px;
  }
  .llm-edit-label { font-size: 12px; color: var(--pw-ink, #2c2a26); font-weight: 500; }
  .llm-edit-replace { margin-bottom: 12px; }
  .llm-edit-toggle {
    background: none; border: none; cursor: pointer; padding: 4px 0;
    font-size: 12px; color: var(--pw-ink, #2c2a26); font-weight: 500;
    display: block; margin-bottom: 6px;
  }
  .llm-edit-toggle:hover { color: var(--pw-accent, #c96342); }
  .llm-warn {
    margin-top: 6px; font-size: 11px; color: #a06000;
    background: #fff3cd; padding: 6px 10px; border-radius: 3px;
  }
  .llm-edit-actions {
    display: flex; justify-content: flex-end; gap: 8px;
    padding-top: 8px; border-top: 1px solid var(--pw-border-soft, #ece8de);
  }

  .llm-model-current {
    flex: 1 1 280px; padding: 7px 10px; min-width: 200px;
    background: #fff; border: 1px solid var(--pw-border-soft, #ece8de);
    border-radius: 4px; font-size: 12px; color: var(--pw-ink, #2c2a26);
    overflow-x: auto; white-space: nowrap;
  }
</style>
