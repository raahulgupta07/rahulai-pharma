<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount } from 'svelte';
  import DiffPanel from '$lib/chat/DiffPanel.svelte';

  type EntityType = 'skill' | 'metric' | 'model';

  type Version = {
    version: string;
    ts?: string | null;
    label?: string | null;
    source?: string;
    snapshot: Record<string, unknown>;
    change_type?: string | null;
  };

  type RecentEvent = {
    type: EntityType;
    id: string | number;
    name?: string | null;
    ts: string;
    source: string;
    label?: string | null;
    change_type?: string | null;
    project_slug?: string | null;
  };

  let projectSlug = $state('');
  let entityType = $state<EntityType>('skill');
  let entityId = $state('');

  let versions = $state<Version[]>([]);
  let entityNote = $state<string | null>(null);
  let entitySource = $state<string>('');
  let versionA = $state<string>('');
  let versionB = $state<string>('');

  let recent = $state<RecentEvent[]>([]);
  let recentLoading = $state(false);
  let recentError = $state<string | null>(null);
  let recentSourcesMissing = $state<string[]>([]);

  let loadingVersions = $state(false);
  let versionsError = $state<string | null>(null);

  // ── Derived diff snapshots ─────────────────────────────────────────────
  const leftSnap = $derived.by((): Record<string, unknown> => {
    const v = versions.find((x) => x.version === versionA);
    return (v?.snapshot ?? {}) as Record<string, unknown>;
  });
  const rightSnap = $derived.by((): Record<string, unknown> => {
    const v = versions.find((x) => x.version === versionB);
    return (v?.snapshot ?? {}) as Record<string, unknown>;
  });
  const leftLabel = $derived.by((): string => {
    const v = versions.find((x) => x.version === versionA);
    return v ? (v.label || v.version) : 'A';
  });
  const rightLabel = $derived.by((): string => {
    const v = versions.find((x) => x.version === versionB);
    return v ? (v.label || v.version) : 'B';
  });

  // ── Fetchers ───────────────────────────────────────────────────────────
  async function loadRecent() {
    recentLoading = true;
    recentError = null;
    try {
      const qs = new URLSearchParams();
      if (projectSlug) qs.set('project_slug', projectSlug);
      qs.set('limit', '50');
      const r = await dashFetch(`/api/diff/recent?${qs.toString()}`, {
        headers: { Accept: 'application/json' },
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} ${txt}`);
      }
      const data = await r.json();
      recent = data.events || [];
      recentSourcesMissing = data.sources_missing || [];
    } catch (e) {
      recentError = e instanceof Error ? e.message : String(e);
      recent = [];
    } finally {
      recentLoading = false;
    }
  }

  async function loadVersions() {
    if (!entityId) {
      versionsError = 'Pick an entity id';
      return;
    }
    loadingVersions = true;
    versionsError = null;
    versions = [];
    entityNote = null;
    entitySource = '';
    try {
      const qs = new URLSearchParams({ type: entityType, id: String(entityId) });
      const r = await dashFetch(`/api/diff/entity?${qs.toString()}`, {
        headers: { Accept: 'application/json' },
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status} ${txt}`);
      }
      const data = await r.json();
      versions = data.versions || [];
      entityNote = data.note || null;
      entitySource = data.source || '';
      // Default selections: newest vs next-newest
      if (versions.length >= 2) {
        versionA = versions[1].version;
        versionB = versions[0].version;
      } else if (versions.length === 1) {
        versionA = versions[0].version;
        versionB = versions[0].version;
      }
    } catch (e) {
      versionsError = e instanceof Error ? e.message : String(e);
    } finally {
      loadingVersions = false;
    }
  }

  function pickFromRecent(ev: RecentEvent): void {
    entityType = ev.type;
    entityId = String(ev.id);
    if (ev.project_slug) projectSlug = ev.project_slug;
    loadVersions();
  }

  function fmtTs(ts?: string | null): string {
    if (!ts) return '—';
    try { return new Date(ts).toLocaleString(); } catch { return ts; }
  }

  onMount(() => {
    loadRecent();
  });
</script>

<div class="diff-shell">
  <header class="diff-head">
    <div>
      <h1>Version Diff</h1>
      <p class="muted">Visualize changes to MDL / metrics / skills over time</p>
    </div>
    <div class="ctrls">
      <input
        type="text"
        placeholder="project slug (optional filter)"
        bind:value={projectSlug}
        onkeydown={(e) => { if (e.key === 'Enter') loadRecent(); }}
      />
      <button class="refresh" onclick={loadRecent} disabled={recentLoading}>
        {recentLoading ? '...' : 'Refresh'}
      </button>
    </div>
  </header>

  <section class="picker">
    <div class="picker-row">
      <label>
        <span class="lbl">Type</span>
        <select bind:value={entityType} onchange={() => { versions = []; entityId = ''; }}>
          <option value="skill">skill</option>
          <option value="metric">metric</option>
          <option value="model">model (MDL)</option>
        </select>
      </label>

      <label class="grow">
        <span class="lbl">Entity ID</span>
        <input
          type="text"
          placeholder={entityType === 'skill' ? 'skl_... or builtin slug' : 'numeric id'}
          bind:value={entityId}
          onkeydown={(e) => { if (e.key === 'Enter') loadVersions(); }}
        />
      </label>

      <button class="primary" onclick={loadVersions} disabled={loadingVersions}>
        {loadingVersions ? 'Loading…' : 'Load versions'}
      </button>
    </div>

    {#if versions.length > 0}
      <div class="picker-row">
        <label class="grow">
          <span class="lbl">Version A (left)</span>
          <select bind:value={versionA}>
            {#each versions as v}
              <option value={v.version}>
                {v.label || v.version} · {fmtTs(v.ts)}
              </option>
            {/each}
          </select>
        </label>

        <label class="grow">
          <span class="lbl">Version B (right)</span>
          <select bind:value={versionB}>
            {#each versions as v}
              <option value={v.version}>
                {v.label || v.version} · {fmtTs(v.ts)}
              </option>
            {/each}
          </select>
        </label>
      </div>

      {#if entityNote}
        <p class="hint">{entityNote}</p>
      {/if}
      {#if entitySource}
        <p class="source-hint">source: <code>{entitySource}</code></p>
      {/if}
    {/if}

    {#if versionsError}
      <p class="err">{versionsError}</p>
    {/if}
  </section>

  {#if versions.length > 0 && versionA && versionB}
    <section class="diff-card">
      <DiffPanel
        leftLabel={leftLabel}
        rightLabel={rightLabel}
        leftData={leftSnap}
        rightData={rightSnap}
      />
    </section>
  {/if}

  <section class="recent-card">
    <header class="recent-head">
      <h2>Recent changes</h2>
      <span class="muted small">{recent.length} event{recent.length === 1 ? '' : 's'}</span>
    </header>

    {#if recentSourcesMissing.length > 0}
      <p class="warn small">
        missing history tables: {recentSourcesMissing.join(', ')}
      </p>
    {/if}

    {#if recentLoading}
      <p class="muted small">Loading…</p>
    {:else if recentError}
      <p class="err small">{recentError}</p>
    {:else if recent.length === 0}
      <p class="muted small">No recent changes.</p>
    {:else}
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Name</th>
            <th>Change</th>
            <th>Project</th>
            <th>When</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each recent as ev}
            <tr>
              <td><span class="type-pill type-{ev.type}">{ev.type}</span></td>
              <td><code>{ev.name || ev.id}</code></td>
              <td><span class="muted small">{ev.change_type || ev.label || '—'}</span></td>
              <td><span class="muted small">{ev.project_slug || '—'}</span></td>
              <td><span class="muted small">{fmtTs(ev.ts)}</span></td>
              <td>
                <button class="link" type="button" onclick={() => pickFromRecent(ev)}>
                  open →
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  .diff-shell {
    padding: 24px 32px;
    max-width: 1200px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }

  .diff-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  .diff-head h1 {
    font-size: 22px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err { color: #b3261e; font-size: 13px; margin: 8px 0 0 0; }
  .warn { color: #a06000; margin: 0 0 8px 0; }
  .hint { color: #888; font-size: 11.5px; font-style: italic; margin: 4px 0 0 0; }
  .source-hint { color: #999; font-size: 11px; margin: 2px 0 0 0; }
  .source-hint code { background: #f0ebde; padding: 1px 5px; border-radius: 3px; }

  .ctrls { display: flex; gap: 8px; }
  .ctrls input,
  .ctrls select,
  .ctrls button,
  .picker input,
  .picker select,
  .picker button {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  .ctrls input { cursor: text; min-width: 240px; }
  button.refresh:hover { background: #f7f3e9; }
  button:disabled { opacity: 0.5; cursor: default; }
  button.primary {
    background: #c96342;
    color: #fff;
    border-color: #c96342;
    font-weight: 600;
  }
  button.primary:hover:not(:disabled) { background: #b85838; }

  .picker {
    background: #faf7f0;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 14px 16px;
    margin-bottom: 16px;
  }
  .picker-row {
    display: flex;
    gap: 12px;
    align-items: flex-end;
    margin-bottom: 8px;
  }
  .picker-row:last-child { margin-bottom: 0; }
  .picker label {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .picker label.grow { flex: 1; }
  .lbl {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .picker input,
  .picker select { cursor: text; }
  .picker select { cursor: pointer; }

  .diff-card { margin-bottom: 24px; }

  .recent-card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px;
  }
  .recent-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .recent-head h2 {
    font-size: 14px;
    margin: 0;
    font-weight: 600;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
  }
  th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
  }
  tr:hover td { background: #fafaf5; }
  td code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }

  .type-pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .type-skill { background: rgba(201,99,66,0.14); color: #c96342; }
  .type-metric { background: rgba(22,163,74,0.14); color: #16a34a; }
  .type-model { background: rgba(160,96,0,0.14); color: #a06000; }

  button.link {
    background: transparent;
    border: 0;
    color: #c96342;
    cursor: pointer;
    font-size: 12px;
    padding: 0;
  }
  button.link:hover { text-decoration: underline; }
</style>
