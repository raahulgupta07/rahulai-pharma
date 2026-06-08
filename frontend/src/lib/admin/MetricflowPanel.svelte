<script lang="ts">
  import { dashFetch } from '$lib/api';
  // Admin UI for the MetricFlow → MDL import endpoint (Phase 4).
  // Style: matches /admin/accuracy, /admin/approvals (cream borders, coral accents,
  // serif h1, Svelte 5 runes).

  type ImportResult = {
    ok?: boolean;
    dry_run?: boolean;
    project_slug?: string;
    models_imported?: number;
    metrics_imported?: number;
    skipped?: Array<{ kind?: string; name?: string; reason?: string } | string>;
    warnings?: string[];
    mdl_preview?: unknown;
    models_in_preview?: number;
    metrics_in_preview?: number;
    // pass-through error envelope
    detail?: string;
  };

  let tab = $state<'upload' | 'paste' | 'example'>('upload');

  // Shared
  let projectSlug = $state('');
  let dryRun = $state(false);
  let busy = $state(false);
  let result = $state<ImportResult | null>(null);
  let errorMsg = $state<string | null>(null);
  let showPreview = $state(false);

  // Upload tab
  let fileInput: HTMLInputElement | null = $state(null);
  let pickedFiles = $state<File[]>([]);

  // Paste tab
  let yamlText = $state('');

  // Example tab
  let exampleYaml = $state<string | null>(null);
  let exampleLoading = $state(false);
  let exampleError = $state<string | null>(null);
  let copyState = $state<'idle' | 'copied'>('idle');

  function resetResult() {
    result = null;
    errorMsg = null;
    showPreview = false;
  }

  function onFilesPicked(e: Event) {
    const input = e.target as HTMLInputElement;
    pickedFiles = input.files ? Array.from(input.files) : [];
  }

  async function submitUpload() {
    resetResult();
    if (!projectSlug.trim()) {
      errorMsg = 'project_slug is required.';
      return;
    }
    if (pickedFiles.length === 0) {
      errorMsg = 'Pick at least one .yaml or .yml file.';
      return;
    }
    busy = true;
    try {
      const fd = new FormData();
      fd.append('project_slug', projectSlug.trim());
      fd.append('dry_run', String(dryRun));
      for (const f of pickedFiles) fd.append('files', f, f.name);

      const r = await dashFetch('/api/metricflow/import', {
        method: 'POST',
        body: fd
      });
      const text = await r.text();
      let json: any = null;
      try { json = JSON.parse(text); } catch {}
      if (!r.ok) {
        errorMsg = (json && (json.detail || json.error)) || `HTTP ${r.status}`;
        result = json;
        return;
      }
      result = json;
    } catch (e: any) {
      errorMsg = e?.message || String(e);
    } finally {
      busy = false;
    }
  }

  async function submitPaste() {
    resetResult();
    if (!projectSlug.trim()) {
      errorMsg = 'project_slug is required.';
      return;
    }
    if (!yamlText.trim()) {
      errorMsg = 'Paste some YAML first.';
      return;
    }
    busy = true;
    try {
      const r = await dashFetch('/api/metricflow/import-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_slug: projectSlug.trim(),
          yaml_text: yamlText,
          dry_run: dryRun
        })
      });
      const text = await r.text();
      let json: any = null;
      try { json = JSON.parse(text); } catch {}
      if (!r.ok) {
        errorMsg = (json && (json.detail || json.error)) || `HTTP ${r.status}`;
        result = json;
        return;
      }
      result = json;
    } catch (e: any) {
      errorMsg = e?.message || String(e);
    } finally {
      busy = false;
    }
  }

  async function loadExample() {
    if (exampleYaml) return;
    exampleLoading = true;
    exampleError = null;
    try {
      const r = await dashFetch('/api/metricflow/example', {
        headers: { 'Accept': 'application/json' }
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      exampleYaml = j?.yaml || '';
    } catch (e: any) {
      exampleError = e?.message || String(e);
    } finally {
      exampleLoading = false;
    }
  }

  async function copyExample() {
    if (!exampleYaml) return;
    try {
      await navigator.clipboard.writeText(exampleYaml);
      copyState = 'copied';
      setTimeout(() => { copyState = 'idle'; }, 1400);
    } catch {
      // swallow
    }
  }

  $effect(() => {
    if (tab === 'example') loadExample();
  });

  // Derived flags for the result panel
  const isSuccess = $derived(!!(result && !errorMsg));
  const isDryRun = $derived(!!result?.dry_run);
  const skipped = $derived<Array<any>>(Array.isArray(result?.skipped) ? (result!.skipped as any[]) : []);
  const warnings = $derived<string[]>(Array.isArray(result?.warnings) ? (result!.warnings as string[]) : []);
</script>

<div class="mf-shell">
  <header class="mf-head">
    <div>
      <h1>MetricFlow Import</h1>
      <p class="muted">
        Upload <a href="https://docs.getdbt.com/docs/build/build-metrics-intro" target="_blank" rel="noreferrer">MetricFlow</a>
        YAML files and install them into a project's MDL (semantic-layer) store.
      </p>
    </div>
  </header>

  <section class="row">
    <label class="lbl">
      <span>Project slug</span>
      <input
        type="text"
        placeholder="e.g. proj_demo_pg_crm"
        bind:value={projectSlug}
        autocomplete="off"
        spellcheck="false"
      />
    </label>
  </section>

  <nav class="tabs">
    <button class:active={tab === 'upload'} onclick={() => (tab = 'upload')}>Upload files</button>
    <button class:active={tab === 'paste'} onclick={() => (tab = 'paste')}>Paste YAML</button>
    <button class:active={tab === 'example'} onclick={() => (tab = 'example')}>Example</button>
  </nav>

  {#if tab === 'upload'}
    <section class="card">
      <h2>Upload .yaml / .yml files</h2>
      <p class="muted small">
        Multi-select supported. Files are staged in a temp directory, parsed, and (unless dry-run) installed.
      </p>

      <div class="ctrls">
        <input
          type="file"
          accept=".yaml,.yml"
          multiple
          bind:this={fileInput}
          onchange={onFilesPicked}
        />
        {#if pickedFiles.length > 0}
          <span class="files">
            {pickedFiles.length} file{pickedFiles.length === 1 ? '' : 's'} selected
          </span>
        {/if}
      </div>

      <label class="check">
        <input type="checkbox" bind:checked={dryRun} />
        Dry-run (preview MDL only, do not write to DB)
      </label>

      <div class="actions">
        <button class="primary" onclick={submitUpload} disabled={busy}>
          {busy ? 'Importing…' : (dryRun ? 'Preview' : 'Import')}
        </button>
      </div>
    </section>
  {/if}

  {#if tab === 'paste'}
    <section class="card">
      <h2>Paste a MetricFlow YAML blob</h2>
      <p class="muted small">For quick experiments. The whole blob is treated as a single file.</p>

      <textarea
        bind:value={yamlText}
        spellcheck="false"
        rows="14"
        placeholder="semantic_models:&#10;  - name: orders&#10;    ..."
      ></textarea>

      <label class="check">
        <input type="checkbox" bind:checked={dryRun} />
        Dry-run (preview MDL only, do not write to DB)
      </label>

      <div class="actions">
        <button class="primary" onclick={submitPaste} disabled={busy}>
          {busy ? 'Importing…' : (dryRun ? 'Preview' : 'Import')}
        </button>
      </div>
    </section>
  {/if}

  {#if tab === 'example'}
    <section class="card">
      <h2>Reference YAML</h2>
      <p class="muted small">Copy this, tweak for your tables, then upload it on the “Upload files” tab.</p>

      {#if exampleLoading}
        <p class="muted">Loading example…</p>
      {:else if exampleError}
        <p class="err">Failed to load example: {exampleError}</p>
      {:else if exampleYaml}
        <div class="actions">
          <button class="ghost" onclick={copyExample}>
            {copyState === 'copied' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <pre class="mono">{exampleYaml}</pre>
      {:else}
        <p class="muted">No example available.</p>
      {/if}
    </section>
  {/if}

  {#if errorMsg}
    <section class="result err-box">
      <h2>Import failed</h2>
      <p class="err">{errorMsg}</p>
      {#if result?.detail && result.detail !== errorMsg}
        <p class="muted small">Server detail: {result.detail}</p>
      {/if}
    </section>
  {:else if isSuccess && result}
    <section class="result ok-box">
      <h2>
        {#if isDryRun}Preview ready{:else}Import succeeded{/if}
        <span class="badge {isDryRun ? 'badge-amber' : 'badge-coral'}">
          {isDryRun ? 'DRY RUN' : 'INSTALLED'}
        </span>
      </h2>

      <div class="tiles">
        {#if isDryRun}
          <div class="tile">
            <div class="tile-label">Models (preview)</div>
            <div class="tile-value">{result.models_in_preview ?? 0}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Metrics (preview)</div>
            <div class="tile-value">{result.metrics_in_preview ?? 0}</div>
          </div>
        {:else}
          <div class="tile">
            <div class="tile-label">Models imported</div>
            <div class="tile-value">{result.models_imported ?? 0}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Metrics imported</div>
            <div class="tile-value">{result.metrics_imported ?? 0}</div>
          </div>
          <div class="tile">
            <div class="tile-label">Skipped</div>
            <div class="tile-value">{skipped.length}</div>
          </div>
        {/if}
      </div>

      {#if skipped.length > 0}
        <div class="sub">
          <h3>Skipped</h3>
          <ul>
            {#each skipped as s}
              <li>
                {#if typeof s === 'string'}
                  <code>{s}</code>
                {:else}
                  <code>{(s.kind || '?')}:{s.name || '?'}</code>
                  {#if s.reason}<span class="muted small"> — {s.reason}</span>{/if}
                {/if}
              </li>
            {/each}
          </ul>
        </div>
      {/if}

      {#if warnings.length > 0}
        <div class="sub">
          <h3>Warnings</h3>
          <ul class="warn">
            {#each warnings as w}<li>{w}</li>{/each}
          </ul>
        </div>
      {/if}

      {#if isDryRun && result.mdl_preview}
        <details class="sub" bind:open={showPreview}>
          <summary>MDL preview (JSON)</summary>
          <pre class="mono">{JSON.stringify(result.mdl_preview, null, 2)}</pre>
        </details>
      {/if}
    </section>
  {/if}
</div>

<style>
  .mf-shell {
    padding: 24px 32px 64px;
    max-width: 1100px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .mf-head {
    margin-bottom: 18px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
  }
  h1 {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 24px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  h2 {
    font-size: 14px;
    margin: 0 0 10px 0;
    font-weight: 600;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  h3 {
    font-size: 12px;
    margin: 14px 0 6px 0;
    font-weight: 600;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err { color: #b3261e; font-size: 13px; margin: 4px 0 0; }

  .row { margin: 0 0 14px; }
  .lbl { display: flex; flex-direction: column; gap: 4px; max-width: 420px; }
  .lbl > span { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: 0.04em; }
  .lbl input[type='text'] {
    padding: 7px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    font-family: inherit;
  }
  .lbl input[type='text']:focus {
    outline: none;
    border-color: #c96342;
    box-shadow: 0 0 0 2px rgba(201,99,66,0.15);
  }

  .tabs { display: flex; gap: 6px; margin: 8px 0 14px; }
  .tabs button {
    padding: 7px 14px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    color: #555;
  }
  .tabs button.active {
    background: #c96342;
    border-color: #c96342;
    color: #fff;
    font-weight: 600;
  }
  .tabs button:hover:not(.active) { background: #f7f3e9; }

  .card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px 18px;
    margin-bottom: 18px;
  }
  .ctrls { display: flex; align-items: center; gap: 12px; margin: 10px 0 14px; flex-wrap: wrap; }
  .files { font-size: 12px; color: #555; }

  textarea {
    width: 100%;
    margin-top: 10px;
    padding: 10px 12px;
    border: 1px solid #d6d1c2;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.5;
    background: #fdfaf3;
    color: #1f1c17;
    resize: vertical;
  }
  textarea:focus {
    outline: none;
    border-color: #c96342;
    box-shadow: 0 0 0 2px rgba(201,99,66,0.15);
  }

  .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin: 6px 0 10px;
    font-size: 13px;
    color: #444;
    cursor: pointer;
    user-select: none;
  }
  .actions { display: flex; gap: 10px; margin-top: 6px; }
  .actions .primary {
    background: #c96342;
    color: #fff;
    border: 1px solid #c96342;
    padding: 7px 16px;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    font-weight: 600;
  }
  .actions .primary:hover:not(:disabled) { background: #b45434; border-color: #b45434; }
  .actions .primary:disabled { opacity: 0.55; cursor: default; }
  .actions .ghost {
    background: #fff;
    color: #555;
    border: 1px solid #d6d1c2;
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .actions .ghost:hover { background: #f7f3e9; }

  .result {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px 18px;
    margin-top: 8px;
  }
  .ok-box { border-left: 3px solid #2e8a4f; background: #f6fbf6; }
  .err-box { border-left: 3px solid #b3261e; background: #fdf6f5; }

  .badge {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .badge-coral { background: #c96342; color: #fff; }
  .badge-amber { background: #b8860b; color: #fff; }

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin: 10px 0 4px;
  }
  .tile {
    background: #fff;
    border: 1px solid #ecdfd2;
    border-radius: 4px;
    padding: 10px 12px;
  }
  .tile-label { font-size: 10px; color: #777; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile-value { font-size: 22px; font-weight: 600; color: #c96342; margin-top: 2px; }

  .sub { margin-top: 12px; }
  .sub ul { margin: 4px 0 0; padding-left: 18px; font-size: 13px; }
  .sub li { margin: 2px 0; }
  .sub code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }
  ul.warn li { color: #8a5b00; }

  details > summary {
    cursor: pointer;
    font-size: 12px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    padding: 4px 0;
  }
  pre.mono {
    margin: 8px 0 0;
    padding: 12px 14px;
    background: #1f1c17;
    color: #f3ebda;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre;
  }
</style>
