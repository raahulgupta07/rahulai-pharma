<script lang="ts">
  // MetricFixModal.svelte — A3+A6: fix/define a metric from chat SQL
  // Props
  let {
    slug,
    question = '',
    sql = '',
    prefillSpec = null as any,
    onclose
  }: {
    slug: string;
    question?: string;
    sql?: string;
    prefillSpec?: any;
    onclose: () => void;
  } = $props();

  // ─── auth helper ────────────────────────────────────────────────
  function _headers(): Record<string, string> {
    const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    const scopeId = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_scope_id') : null;
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h['Authorization'] = `Bearer ${token}`;
    if (scopeId) h['X-Scope-Id'] = scopeId;
    return h;
  }

  // ─── spec state ─────────────────────────────────────────────────
  type Filter = { col: string; op: string; value: string; trim: boolean };
  const OPS = ['=', '!=', '<', '<=', '>', '>=', 'LIKE', 'IN', 'NOT IN', 'IS NULL', 'IS NOT NULL'];
  const REASONS = [
    { id: 'missing_filter', label: 'Missing filter' },
    { id: 'extra_filter',   label: 'Extra filter' },
    { id: 'wrong_column',   label: 'Wrong column' },
    { id: 'wrong_value',    label: 'Wrong value' },
    { id: 'wrong_table',    label: 'Wrong table' },
  ];
  const KINDS = ['count', 'sum', 'average', 'ratio', 'custom'];

  let specName        = $state('');
  let specKind        = $state('count');
  let specDesc        = $state('');
  let specMeasureCol  = $state('');
  let specGroupDims   = $state<string[]>([]);
  let specGroupInput  = $state('');
  let specFilters     = $state<Filter[]>([{ col: '', op: '=', value: '', trim: false }]);
  let specDenomFilters = $state<Filter[]>([]);
  let specVerifiedAns = $state('');
  let specSourceGlob  = $state('');
  let specStatus      = $state('draft');

  // correction-UX
  let rightNumber     = $state('');
  let reasonId        = $state('');

  // async state
  let busy            = $state(false);
  let prefillBusy     = $state(false);
  let testResult      = $state<{ ok: boolean; total: any; table_md?: string; sql?: string } | null>(null);
  let testError       = $state('');
  let saveError       = $state('');
  let toast           = $state('');

  // ─── on open: prefill from sql ──────────────────────────────────
  import { onMount } from 'svelte';

  function applySpec(spec: any) {
    if (!spec) return;
    specName        = spec.name || '';
    specKind        = spec.kind || 'count';
    specDesc        = spec.description || '';
    specMeasureCol  = spec.measure_col || '';
    specSourceGlob  = spec.source_glob || '';
    specVerifiedAns = spec.verified_answer != null ? String(spec.verified_answer) : '';
    specStatus      = spec.status || 'draft';
    if (Array.isArray(spec.filters) && spec.filters.length) {
      specFilters = spec.filters.map((f: any) => ({
        col: f.col || '', op: f.op || '=', value: f.value || '', trim: !!f.trim
      }));
    }
    if (Array.isArray(spec.denom_filters) && spec.denom_filters.length) {
      specDenomFilters = spec.denom_filters.map((f: any) => ({
        col: f.col || '', op: f.op || '=', value: f.value || '', trim: !!f.trim
      }));
    }
    if (Array.isArray(spec.group_dims)) {
      specGroupDims = [...spec.group_dims];
    }
    if (Array.isArray(spec.synonyms) && spec.synonyms.length) {
      // ignore for now — display-only
    }
  }

  onMount(async () => {
    if (prefillSpec) {
      applySpec(prefillSpec);
      return;
    }
    if (sql && slug) {
      prefillBusy = true;
      try {
        const res = await fetch(`/api/projects/${slug}/metrics/from-chat`, {
          method: 'POST',
          headers: _headers(),
          body: JSON.stringify({ question, sql })
        });
        if (res.ok) {
          const data = await res.json().catch(() => null);
          if (data?.spec) applySpec(data.spec);
        }
      } catch (_) { /* fail-soft */ }
      prefillBusy = false;
    }
  });

  // ─── filter helpers ─────────────────────────────────────────────
  function addFilter() {
    specFilters = [...specFilters, { col: '', op: '=', value: '', trim: false }];
  }
  function removeFilter(idx: number) {
    specFilters = specFilters.filter((_, i) => i !== idx);
  }
  function addDenomFilter() {
    specDenomFilters = [...specDenomFilters, { col: '', op: '=', value: '', trim: false }];
  }
  function removeDenomFilter(idx: number) {
    specDenomFilters = specDenomFilters.filter((_, i) => i !== idx);
  }

  // ─── group dim chips ────────────────────────────────────────────
  function addGroupDim() {
    const v = specGroupInput.trim();
    if (v && !specGroupDims.includes(v)) specGroupDims = [...specGroupDims, v];
    specGroupInput = '';
  }
  function removeGroupDim(d: string) {
    specGroupDims = specGroupDims.filter((x) => x !== d);
  }

  // ─── build spec object ──────────────────────────────────────────
  function buildSpec() {
    return {
      name: specName.trim(),
      description: specDesc.trim(),
      kind: specKind,
      source_glob: specSourceGlob.trim(),
      measure_col: specMeasureCol.trim(),
      filters: specFilters.filter((f) => f.col.trim()),
      denom_filters: specDenomFilters.filter((f) => f.col.trim()),
      group_dims: specGroupDims,
      verified_answer: specVerifiedAns !== '' ? Number(specVerifiedAns) || specVerifiedAns : null,
      status: specStatus,
      synonyms: [],
    };
  }

  // ─── TEST LIVE ──────────────────────────────────────────────────
  async function testLive() {
    busy = true;
    testError = '';
    testResult = null;
    try {
      const res = await fetch(`/api/projects/${slug}/metrics/test`, {
        method: 'POST',
        headers: _headers(),
        body: JSON.stringify({ spec: buildSpec() })
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data) {
        testResult = data;
      } else {
        testError = data?.detail || data?.error || `HTTP ${res.status}`;
      }
    } catch (e: any) {
      testError = e?.message || 'Network error';
    }
    busy = false;
  }

  // ─── SAVE ───────────────────────────────────────────────────────
  async function saveLock() {
    saveError = '';
    busy = true;
    try {
      const res = await fetch(`/api/projects/${slug}/metrics`, {
        method: 'POST',
        headers: _headers(),
        body: JSON.stringify(buildSpec())
      });
      const data = await res.json().catch(() => null);
      if (res.ok) {
        toast = '✓ Metric saved';
        setTimeout(() => { toast = ''; onclose(); }, 1200);
      } else {
        saveError = data?.detail || data?.error || `HTTP ${res.status}`;
      }
    } catch (e: any) {
      saveError = e?.message || 'Network error';
    }
    busy = false;
  }

  // ─── ESC close ──────────────────────────────────────────────────
  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

<!-- Backdrop -->
<div style="
  position: fixed; inset: 0; z-index: 9998;
  background: rgba(0,0,0,0.45);
  display: flex; align-items: flex-start; justify-content: flex-end;
" onclick={() => onclose()}>
  <!-- Drawer panel (stop propagation so clicks inside don't close) -->
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div style="
    position: relative; z-index: 9999;
    width: min(540px, 100vw);
    height: 100vh;
    background: var(--pw-bg, #f5f0e8);
    border-left: 2px solid var(--pw-ink, #1a1614);
    overflow-y: auto;
    font-family: monospace;
  " onclick={(e) => e.stopPropagation()}>

    <!-- Header -->
    <div style="
      background: var(--pw-ink, #1a1614);
      color: var(--pw-bg, #f5f0e8);
      padding: 10px 16px;
      display: flex; align-items: center; gap: 10px;
      font-size: 11px; font-weight: 900; letter-spacing: 0.08em;
    ">
      <span style="flex:1;">📌 DEFINE / FIX METRIC</span>
      <button onclick={() => onclose()} style="
        background: none; border: 1px solid rgba(255,255,255,0.3);
        color: inherit; cursor: pointer; padding: 2px 8px; font-size: 11px;
      ">✕ CLOSE</button>
    </div>

    {#if prefillBusy}
      <div style="padding:20px; font-size:11px; color:var(--pw-muted,#888);">
        ⟳ Analysing SQL to prefill editor…
      </div>
    {/if}

    <div style="padding: 16px; display: flex; flex-direction: column; gap: 14px;">

      <!-- Context: question + SQL -->
      {#if question}
        <div>
          <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; color:var(--pw-muted,#888); margin-bottom:3px;">QUESTION</div>
          <div style="font-size:11px; color:var(--pw-ink,#1a1614); background:var(--pw-bg-alt,#ede8de); padding:6px 8px; border:1px solid var(--pw-border,#ccc);">{question}</div>
        </div>
      {/if}

      <!-- Right number + reason -->
      <div style="background:var(--pw-bg-alt,#ede8de); border:1px solid var(--pw-border,#ccc); padding:10px;">
        <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:6px;">CORRECTION</div>
        <label style="display:block; font-size:10px; margin-bottom:3px;">RIGHT NUMBER (leave blank if unknown)</label>
        <input type="number" bind:value={rightNumber} placeholder="e.g. 1544" style="
          width: 100%; box-sizing: border-box; padding: 5px 8px;
          border: 1px solid var(--pw-border,#ccc); font-family: monospace; font-size: 11px;
          background: var(--pw-bg,#f5f0e8);
        " />
        <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; margin-top:8px; margin-bottom:4px;">REASON</div>
        <div style="display:flex; flex-wrap:wrap; gap:5px;">
          {#each REASONS as r}
            <label style="display:flex; align-items:center; gap:4px; cursor:pointer;">
              <input type="radio" bind:group={reasonId} value={r.id} />
              <span style="font-size:10px; font-weight:700;">{r.label}</span>
            </label>
          {/each}
        </div>
      </div>

      <!-- Name + Kind -->
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">METRIC NAME</label>
          <input bind:value={specName} placeholder="e.g. total_leads" style="
            width:100%; box-sizing:border-box; padding:5px 8px;
            border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:11px;
            background:var(--pw-bg,#f5f0e8);
          " />
        </div>
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">KIND</label>
          <select bind:value={specKind} style="
            width:100%; padding:5px 8px; font-family:monospace; font-size:11px;
            border:1px solid var(--pw-border,#ccc); background:var(--pw-bg,#f5f0e8);
          ">
            {#each KINDS as k}
              <option value={k}>{k}</option>
            {/each}
          </select>
        </div>
      </div>

      <!-- Description -->
      <div>
        <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">DESCRIPTION</label>
        <textarea bind:value={specDesc} rows="2" style="
          width:100%; box-sizing:border-box; padding:5px 8px;
          border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:11px;
          background:var(--pw-bg,#f5f0e8); resize:vertical;
        "></textarea>
      </div>

      <!-- Source glob + Measure col -->
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">SOURCE GLOB</label>
          <input bind:value={specSourceGlob} placeholder="e.g. sales_*" style="
            width:100%; box-sizing:border-box; padding:5px 8px;
            border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:11px;
            background:var(--pw-bg,#f5f0e8);
          " />
        </div>
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">MEASURE COL</label>
          <input bind:value={specMeasureCol} placeholder="e.g. revenue" style="
            width:100%; box-sizing:border-box; padding:5px 8px;
            border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:11px;
            background:var(--pw-bg,#f5f0e8);
          " />
        </div>
      </div>

      <!-- Filters -->
      <div>
        <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:5px;">FILTERS</div>
        <div style="display:flex; flex-direction:column; gap:5px;">
          {#each specFilters as f, idx}
            {#if idx >= 0}
              <div style="display:grid; grid-template-columns:3fr 2fr 3fr auto auto; gap:4px; align-items:center;">
                <input bind:value={f.col} placeholder="col" style="padding:4px 6px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);" />
                <select bind:value={f.op} style="padding:4px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);">
                  {#each OPS as op}
                    <option value={op}>{op}</option>
                  {/each}
                </select>
                <input bind:value={f.value} placeholder="value" style="padding:4px 6px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);" />
                <label style="display:flex; align-items:center; gap:2px; font-size:9px; cursor:pointer;">
                  <input type="checkbox" bind:checked={f.trim} />TRIM
                </label>
                <button onclick={() => removeFilter(idx)} style="
                  padding:2px 7px; border:1px solid var(--pw-border,#ccc);
                  background:none; cursor:pointer; font-size:10px; font-family:monospace;
                ">✕</button>
              </div>
            {/if}
          {/each}
        </div>
        <button onclick={addFilter} style="
          margin-top:5px; padding:3px 10px; border:1px solid var(--pw-ink,#1a1614);
          background:none; cursor:pointer; font-family:monospace; font-size:10px; font-weight:700;
        ">+ ADD FILTER</button>
      </div>

      <!-- Denom Filters -->
      <div>
        <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:5px;">DENOMINATOR FILTERS</div>
        <div style="display:flex; flex-direction:column; gap:5px;">
          {#each specDenomFilters as f, idx}
            {#if idx >= 0}
              <div style="display:grid; grid-template-columns:3fr 2fr 3fr auto auto; gap:4px; align-items:center;">
                <input bind:value={f.col} placeholder="col" style="padding:4px 6px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);" />
                <select bind:value={f.op} style="padding:4px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);">
                  {#each OPS as op}
                    <option value={op}>{op}</option>
                  {/each}
                </select>
                <input bind:value={f.value} placeholder="value" style="padding:4px 6px; border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);" />
                <label style="display:flex; align-items:center; gap:2px; font-size:9px; cursor:pointer;">
                  <input type="checkbox" bind:checked={f.trim} />TRIM
                </label>
                <button onclick={() => removeDenomFilter(idx)} style="
                  padding:2px 7px; border:1px solid var(--pw-border,#ccc);
                  background:none; cursor:pointer; font-size:10px; font-family:monospace;
                ">✕</button>
              </div>
            {/if}
          {/each}
        </div>
        <button onclick={addDenomFilter} style="
          margin-top:5px; padding:3px 10px; border:1px solid var(--pw-ink,#1a1614);
          background:none; cursor:pointer; font-family:monospace; font-size:10px; font-weight:700;
        ">+ ADD DENOM FILTER</button>
      </div>

      <!-- Group-by chips -->
      <div>
        <div style="font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:5px;">GROUP DIMS</div>
        <div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom:5px;">
          {#each specGroupDims as d}
            <span style="
              background:var(--pw-ink,#1a1614); color:var(--pw-bg,#f5f0e8);
              padding:2px 8px; font-size:10px; display:flex; align-items:center; gap:4px;
            ">{d}
              <button onclick={() => removeGroupDim(d)} style="
                background:none; border:none; color:inherit; cursor:pointer; font-size:10px; padding:0;
              ">✕</button>
            </span>
          {/each}
        </div>
        <div style="display:flex; gap:6px;">
          <input bind:value={specGroupInput} onkeydown={(e) => e.key === 'Enter' && addGroupDim()} placeholder="dim col…" style="
            flex:1; padding:4px 8px; border:1px solid var(--pw-border,#ccc);
            font-family:monospace; font-size:10px; background:var(--pw-bg,#f5f0e8);
          " />
          <button onclick={addGroupDim} style="
            padding:4px 10px; border:1px solid var(--pw-ink,#1a1614);
            background:none; cursor:pointer; font-family:monospace; font-size:10px; font-weight:700;
          ">ADD</button>
        </div>
      </div>

      <!-- Verified answer + status -->
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">VERIFIED ANSWER</label>
          <input type="number" bind:value={specVerifiedAns} placeholder="known correct total" style="
            width:100%; box-sizing:border-box; padding:5px 8px;
            border:1px solid var(--pw-border,#ccc); font-family:monospace; font-size:11px;
            background:var(--pw-bg,#f5f0e8);
          " />
        </div>
        <div>
          <label style="display:block; font-size:10px; font-weight:900; letter-spacing:0.06em; margin-bottom:3px;">STATUS</label>
          <select bind:value={specStatus} style="
            width:100%; padding:5px 8px; font-family:monospace; font-size:11px;
            border:1px solid var(--pw-border,#ccc); background:var(--pw-bg,#f5f0e8);
          ">
            <option value="draft">draft</option>
            <option value="verified">verified</option>
            <option value="deprecated">deprecated</option>
          </select>
        </div>
      </div>

      <!-- TEST LIVE result -->
      {#if testResult}
        <div style="
          border:2px solid {testResult.ok ? '#2e7d32' : '#c62828'};
          background: {testResult.ok ? '#f1f8e9' : '#ffebee'};
          padding: 10px; font-family:monospace; font-size:11px;
        ">
          <div style="font-weight:900; margin-bottom:4px;">
            {testResult.ok ? '✓ PASS' : '✗ FAIL'} — total: {testResult.total ?? '—'}
          </div>
          {#if specVerifiedAns && testResult.total != null}
            <div style="font-size:10px;">
              Expected: {specVerifiedAns} · Got: {testResult.total}
              {#if Number(testResult.total) === Number(specVerifiedAns)}
                <strong style="color:#2e7d32;"> ✓ MATCH</strong>
              {:else}
                <strong style="color:#c62828;"> ✗ MISMATCH</strong>
              {/if}
            </div>
          {/if}
          {#if testResult.table_md}
            <pre style="
              background:var(--pw-bg-alt,#ede8de); padding:6px; margin-top:6px;
              font-size:10px; overflow-x:auto; white-space:pre-wrap;
            ">{testResult.table_md}</pre>
          {/if}
        </div>
      {/if}
      {#if testError}
        <div style="color:#c62828; font-size:11px; font-family:monospace;">{testError}</div>
      {/if}
      {#if saveError}
        <div style="color:#c62828; font-size:11px; font-family:monospace;">{saveError}</div>
      {/if}
      {#if toast}
        <div style="
          background:var(--pw-ink,#1a1614); color:var(--pw-bg,#f5f0e8);
          padding:8px 12px; font-size:11px; font-family:monospace; font-weight:700;
        ">{toast}</div>
      {/if}

      <!-- Actions -->
      <div style="display:flex; gap:8px; padding-bottom:20px;">
        <button onclick={testLive} disabled={busy} style="
          flex:1; padding:8px; border:2px solid var(--pw-ink,#1a1614);
          background:none; cursor:pointer; font-family:monospace; font-size:11px; font-weight:900;
          letter-spacing:0.05em;
          opacity: {busy ? 0.6 : 1};
        ">⚡ TEST LIVE</button>
        <button onclick={saveLock} disabled={busy || !specName.trim()} style="
          flex:1; padding:8px; border:2px solid var(--pw-ink,#1a1614);
          background:var(--pw-ink,#1a1614); color:var(--pw-bg,#f5f0e8);
          cursor:pointer; font-family:monospace; font-size:11px; font-weight:900;
          letter-spacing:0.05em;
          opacity: {busy || !specName.trim() ? 0.5 : 1};
        ">🔒 SAVE &amp; LOCK</button>
      </div>

    </div>
  </div>
</div>
