<script lang="ts">
  import { dashFetch } from '$lib/api';
  import { onMount } from 'svelte';

  type GoldenEntry = {
    id: number;
    question: string;
    expected_answer?: string;
    sql?: string;
    tags?: string[];
    notes?: string;
    source?: string;
    promoted_by?: string;
    promoted_at?: string;
    updated_at?: string;
    expected_rowcount?: number;
    expected_value?: string;
  };

  type DriftStatus = {
    project_slug: string;
    last_run_at: string | null;
    checked: number;
    passed: number;
    drifted_count: number;
    pass_rate: number;
    regressions: { question: string; reason: string }[];
  };

  let projectSlug = $state('');
  let projectsInput = $state('');
  let entries = $state<GoldenEntry[]>([]);
  let drift = $state<DriftStatus | null>(null);
  let loading = $state(false);
  let runningEval = $state(false);
  let error = $state<string | null>(null);
  let showAddModal = $state(false);
  let editingId = $state<number | null>(null);

  // form state
  let formQuestion = $state('');
  let formExpected = $state('');
  let formSql = $state('');
  let formTags = $state('');
  let formNotes = $state('');

  // drifted question set for quick lookup
  const driftedSet = $derived(new Set((drift?.regressions || []).map(r => r.question)));

  function rowResult(e: GoldenEntry): 'fail' | 'pass' | 'unknown' {
    if (!drift || drift.checked === 0) return 'unknown';
    return driftedSet.has((e.question || '').slice(0, 200)) ? 'fail' : 'pass';
  }

  async function loadAll() {
    if (!projectSlug.trim()) {
      entries = [];
      drift = null;
      return;
    }
    loading = true;
    error = null;
    try {
      const slug = encodeURIComponent(projectSlug.trim());
      const [listR, driftR] = await Promise.allSettled([
        dashFetch(`/api/golden?project_slug=${slug}`, { headers: { Accept: 'application/json' } }),
        dashFetch(`/api/golden/drift?project_slug=${slug}`, { headers: { Accept: 'application/json' } })
      ]);

      if (listR.status === 'fulfilled') {
        const r = listR.value;
        if (r.ok) {
          const data = await r.json();
          entries = data.entries || [];
        } else if (r.status === 503) {
          entries = [];
          error = `Golden corpus unavailable (HTTP 503)`;
        } else {
          entries = [];
          error = `Failed to load entries: HTTP ${r.status}`;
        }
      } else {
        entries = [];
        error = listR.reason?.message || 'Failed to load entries';
      }

      if (driftR.status === 'fulfilled' && driftR.value.ok) {
        drift = await driftR.value.json();
      } else {
        drift = null;
      }
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      loading = false;
    }
  }

  async function runEvalNow() {
    if (!projectSlug.trim()) return;
    runningEval = true;
    error = null;
    try {
      const slug = encodeURIComponent(projectSlug.trim());
      const r = await dashFetch(`/api/golden/run?project_slug=${slug}`, {
        method: 'POST',
        headers: { Accept: 'application/json' }
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const result = await r.json();
      drift = {
        project_slug: result.project_slug,
        last_run_at: new Date().toISOString(),
        checked: result.checked,
        passed: result.passed,
        drifted_count: result.drifted_count,
        pass_rate: result.pass_rate,
        regressions: result.regressions || []
      };
    } catch (e: any) {
      error = e?.message || String(e);
    } finally {
      runningEval = false;
    }
  }

  function resetForm() {
    formQuestion = '';
    formExpected = '';
    formSql = '';
    formTags = '';
    formNotes = '';
    editingId = null;
  }

  function openAdd() {
    resetForm();
    showAddModal = true;
  }

  function openEdit(e: GoldenEntry) {
    editingId = e.id;
    formQuestion = e.question || '';
    formExpected = e.expected_answer || '';
    formSql = e.sql || '';
    formTags = (e.tags || []).join(', ');
    formNotes = e.notes || '';
    showAddModal = true;
  }

  function closeModal() {
    showAddModal = false;
    resetForm();
  }

  async function saveEntry() {
    if (!projectSlug.trim()) return;
    if (!formQuestion.trim()) {
      error = 'Question is required';
      return;
    }
    if (!formExpected.trim() && !formSql.trim()) {
      error = 'Either expected_answer or sql is required';
      return;
    }
    error = null;
    try {
      const body = {
        project_slug: projectSlug.trim(),
        question: formQuestion.trim(),
        expected_answer: formExpected.trim(),
        sql: formSql.trim(),
        tags: formTags.split(',').map(t => t.trim()).filter(Boolean),
        notes: formNotes.trim()
      };
      const isEdit = editingId !== null;
      const url = isEdit ? `/api/golden/${editingId}` : `/api/golden`;
      const r = await dashFetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body)
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
      }
      closeModal();
      await loadAll();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  async function deleteEntry(id: number, question: string) {
    if (!projectSlug.trim()) return;
    if (!confirm(`Delete this golden Q&A?\n\n${question.slice(0, 120)}`)) return;
    try {
      const slug = encodeURIComponent(projectSlug.trim());
      const r = await dashFetch(`/api/golden/${id}?project_slug=${slug}`, {
        method: 'DELETE',
        headers: { Accept: 'application/json' }
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadAll();
    } catch (e: any) {
      error = e?.message || String(e);
    }
  }

  function truncate(s: string | undefined, n: number): string {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  function formatTimestamp(iso: string | null | undefined): string {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  const passPct = $derived(drift ? (drift.pass_rate * 100).toFixed(1) : '—');

  let singleAgent = $state(false);

  onMount(async () => {
    // optional URL ?slug= deeplink takes priority
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const s = params.get('slug') || params.get('project_slug');
      if (s) {
        projectSlug = s;
        projectsInput = s;
        loadAll();
        return;
      }
    }
    // single-tenant: auto-fill the locked slug — no manual entry needed
    try {
      const f = await fetch('/api/flags').then(r => (r.ok ? r.json() : null));
      if (f?.single_agent && f.locked_slug) {
        singleAgent = true;
        projectSlug = f.locked_slug;
        projectsInput = f.locked_slug;
        loadAll();
      }
    } catch { /* ignore */ }
  });

  function onSlugSubmit(e: Event) {
    e.preventDefault();
    projectSlug = projectsInput.trim();
    loadAll();
  }
</script>

<div class="gld-shell">
  <header class="gld-head">
    <div>
      <h1>Golden Q&amp;A Corpus</h1>
      <p class="muted">Curated truth pairs — view, edit, and run drift checks per project</p>
    </div>
    <div class="ctrls">
      {#if !singleAgent}
        <form onsubmit={onSlugSubmit} class="slug-form">
          <input
            type="text"
            placeholder="project_slug"
            bind:value={projectsInput}
            class="slug-input"
          />
          <button type="submit" class="btn-ghost">Load</button>
        </form>
      {/if}
      <button class="btn-ghost" onclick={loadAll} disabled={loading || !projectSlug}>
        {loading ? '…' : 'Refresh'}
      </button>
    </div>
  </header>

  {#if projectSlug && drift}
    <section class="banner" class:has-regressions={drift.drifted_count > 0}>
      <div class="banner-main">
        <span class="banner-label">Last run:</span>
        <strong>{formatTimestamp(drift.last_run_at)}</strong>
        <span class="banner-sep">·</span>
        <span class="banner-stat">{passPct}% pass</span>
        <span class="banner-sep">·</span>
        <span class="banner-stat" class:warn={drift.drifted_count > 0}>
          {drift.drifted_count} regression{drift.drifted_count === 1 ? '' : 's'}
        </span>
        <span class="banner-sep">·</span>
        <span class="banner-stat">{drift.checked} checked</span>
      </div>
      <a href="/admin/accuracy" class="banner-link">View accuracy →</a>
    </section>
  {/if}

  {#if error}
    <p class="err">⚠ {error}</p>
  {/if}

  {#if projectSlug}
    <section class="actions-row">
      <button class="btn-primary" onclick={openAdd}>+ Add Golden Q</button>
      <button class="btn-secondary" onclick={runEvalNow} disabled={runningEval || entries.length === 0}>
        {runningEval ? 'Running…' : 'Run drift check'}
      </button>
      <span class="count-pill">{entries.length} entries</span>
    </section>

    <section class="table-card">
      {#if loading}
        <p class="muted small">Loading…</p>
      {:else if entries.length === 0}
        <p class="muted small">No golden Q&amp;A pairs yet. Click <strong>+ Add Golden Q</strong> to create one, or promote one via 👍 feedback in chat.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th class="col-id">#</th>
              <th>Question</th>
              <th>Expected / SQL</th>
              <th class="col-tags">Tags</th>
              <th class="col-result">Last result</th>
              <th class="col-actions">Actions</th>
            </tr>
          </thead>
          <tbody>
            {#each entries as e (e.id)}
              {@const result = rowResult(e)}
              <tr>
                <td class="muted">{e.id}</td>
                <td class="cell-q" title={e.question}>{truncate(e.question, 120)}</td>
                <td class="cell-a">
                  {#if e.expected_answer}
                    <div class="exp" title={e.expected_answer}>{truncate(e.expected_answer, 100)}</div>
                  {/if}
                  {#if e.sql}
                    <code class="sql-snip" title={e.sql}>{truncate(e.sql, 80)}</code>
                  {/if}
                  {#if !e.expected_answer && !e.sql}
                    <span class="muted small">—</span>
                  {/if}
                </td>
                <td class="cell-tags">
                  {#if e.tags && e.tags.length}
                    {#each e.tags as t}
                      <span class="tag">{t}</span>
                    {/each}
                  {:else}
                    <span class="muted small">—</span>
                  {/if}
                </td>
                <td>
                  {#if result === 'pass'}
                    <span class="pill pill-pass">✓ pass</span>
                  {:else if result === 'fail'}
                    <span class="pill pill-fail">✗ fail</span>
                  {:else}
                    <span class="pill pill-unknown">—</span>
                  {/if}
                </td>
                <td class="cell-actions">
                  <button class="btn-mini" onclick={() => openEdit(e)}>Edit</button>
                  <button class="btn-mini btn-danger" onclick={() => deleteEntry(e.id, e.question)}>Delete</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    {#if drift && drift.regressions.length > 0}
      <section class="regress-card">
        <h2>Regressions in last run</h2>
        <ul>
          {#each drift.regressions as r}
            <li>
              <code>{r.reason}</code>
              <div class="muted small">{truncate(r.question, 160)}</div>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {:else}
    <section class="empty-state">
      <p class="muted">Enter a <code>project_slug</code> above to view its golden Q&amp;A corpus.</p>
    </section>
  {/if}
</div>

{#if showAddModal}
  <div class="modal-backdrop" onclick={closeModal} role="presentation"></div>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <header class="modal-head">
      <h2 id="modal-title">{editingId !== null ? 'Edit Golden Q&A' : 'Add Golden Q&A'}</h2>
      <button class="modal-close" onclick={closeModal} aria-label="Close">×</button>
    </header>
    <div class="modal-body">
      <label>
        <span class="lbl">Question *</span>
        <textarea bind:value={formQuestion} rows="2" placeholder="What was total revenue last quarter?"></textarea>
      </label>
      <label>
        <span class="lbl">Expected answer</span>
        <textarea bind:value={formExpected} rows="2" placeholder="$1,234,567 (Q3 2025)"></textarea>
      </label>
      <label>
        <span class="lbl">SQL (read-only)</span>
        <textarea bind:value={formSql} rows="4" class="mono" placeholder="SELECT SUM(amount) FROM sales WHERE quarter = '2025-Q3'"></textarea>
      </label>
      <label>
        <span class="lbl">Tags (comma-separated)</span>
        <input type="text" bind:value={formTags} placeholder="revenue, quarterly, finance" />
      </label>
      <label>
        <span class="lbl">Notes</span>
        <textarea bind:value={formNotes} rows="2" placeholder="Verified by finance team 2026-01-15"></textarea>
      </label>
      <p class="hint muted small">Either <strong>expected answer</strong> or <strong>SQL</strong> is required.</p>
    </div>
    <footer class="modal-foot">
      <button class="btn-ghost" onclick={closeModal}>Cancel</button>
      <button class="btn-primary" onclick={saveEntry}>
        {editingId !== null ? 'Save changes' : 'Add to corpus'}
      </button>
    </footer>
  </div>
{/if}

<style>
  .gld-shell {
    padding: 24px 32px;
    max-width: 1200px;
    margin: 0 auto;
    font-family: system-ui, -apple-system, sans-serif;
    color: #1f1c17;
  }
  .gld-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid #e8e3d6;
    flex-wrap: wrap;
  }
  .gld-head h1 {
    font-family: 'Source Serif Pro', Georgia, serif;
    font-size: 24px;
    margin: 0 0 4px 0;
    font-weight: 600;
  }
  .muted { color: #777; font-size: 13px; margin: 0; }
  .small { font-size: 12px; }
  .err {
    color: #b3261e;
    font-size: 13px;
    background: #fdecea;
    border: 1px solid #f5c2c0;
    border-radius: 4px;
    padding: 8px 12px;
    margin: 0 0 16px 0;
  }
  .ctrls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .slug-form { display: flex; gap: 6px; }
  .slug-input {
    padding: 6px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    min-width: 200px;
    font-family: monospace;
  }
  .btn-ghost, .btn-primary, .btn-secondary, .btn-mini {
    padding: 6px 12px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .btn-ghost:hover, .btn-secondary:hover { background: #f7f3e9; }
  .btn-primary {
    background: #c96342;
    color: #fff;
    border-color: #c96342;
  }
  .btn-primary:hover { background: #b35636; }
  .btn-primary:disabled, .btn-secondary:disabled, .btn-ghost:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .btn-secondary {
    background: #fff;
    border-color: #c96342;
    color: #c96342;
  }
  .btn-mini {
    padding: 3px 8px;
    font-size: 11px;
    margin-right: 4px;
  }
  .btn-mini.btn-danger {
    color: #b3261e;
    border-color: #f5c2c0;
  }
  .btn-mini.btn-danger:hover { background: #fdecea; }

  .banner {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-left: 3px solid #c96342;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .banner.has-regressions { border-left-color: #b3261e; }
  .banner-main { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
  .banner-label { color: #777; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  .banner-sep { color: #d6d1c2; }
  .banner-stat { color: #1f1c17; }
  .banner-stat.warn { color: #b3261e; font-weight: 600; }
  .banner-link { color: #c96342; font-size: 12px; text-decoration: none; }
  .banner-link:hover { text-decoration: underline; }

  .actions-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 12px;
  }
  .count-pill {
    margin-left: auto;
    background: #f7f3e9;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
    color: #777;
  }

  .table-card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 4px;
    overflow-x: auto;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #f0ebde;
    font-size: 13px;
    vertical-align: top;
  }
  th {
    font-size: 11px;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
    background: #fafaf5;
  }
  tbody tr:hover { background: #fafaf5; }
  .col-id { width: 40px; }
  .col-tags { width: 140px; }
  .col-result { width: 110px; }
  .col-actions { width: 130px; white-space: nowrap; }
  .cell-q { max-width: 280px; }
  .cell-a { max-width: 320px; }
  .cell-a .exp { color: #1f1c17; margin-bottom: 4px; }
  .sql-snip {
    display: block;
    background: #f7f3e9;
    padding: 3px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-family: 'SF Mono', Menlo, monospace;
    color: #555;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tag {
    display: inline-block;
    background: #f0ebde;
    padding: 2px 7px;
    border-radius: 3px;
    font-size: 11px;
    color: #555;
    margin-right: 3px;
    margin-bottom: 3px;
  }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
  }
  .pill-pass { background: #e8f5e9; color: #1b5e20; }
  .pill-fail { background: #fdecea; color: #b3261e; }
  .pill-unknown { background: #f0ebde; color: #777; }

  .regress-card {
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 6px;
    padding: 16px;
    margin-top: 16px;
  }
  .regress-card h2 {
    font-size: 13px;
    margin: 0 0 12px 0;
    font-weight: 600;
    color: #b3261e;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .regress-card ul { list-style: none; padding: 0; margin: 0; }
  .regress-card li {
    padding: 8px 0;
    border-bottom: 1px solid #f0ebde;
    font-size: 12px;
  }
  .regress-card li:last-child { border-bottom: none; }
  .regress-card code {
    background: #fdecea;
    padding: 2px 6px;
    border-radius: 3px;
    color: #b3261e;
    font-size: 11px;
  }

  .empty-state {
    background: #fff;
    border: 1px dashed #d6d1c2;
    border-radius: 6px;
    padding: 32px;
    text-align: center;
  }
  .empty-state code {
    background: #f7f3e9;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }

  /* Modal */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(31, 28, 23, 0.5);
    z-index: 9998;
  }
  .modal {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: #fff;
    border: 1px solid #e8e3d6;
    border-radius: 8px;
    width: 90%;
    max-width: 600px;
    max-height: 90vh;
    overflow-y: auto;
    z-index: 9999;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
  }
  .modal-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid #e8e3d6;
  }
  .modal-head h2 {
    font-family: 'Source Serif Pro', Georgia, serif;
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }
  .modal-close {
    background: none;
    border: none;
    font-size: 24px;
    cursor: pointer;
    color: #777;
    line-height: 1;
    padding: 0 4px;
  }
  .modal-close:hover { color: #1f1c17; }
  .modal-body {
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .modal-body label { display: flex; flex-direction: column; gap: 4px; }
  .lbl {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #777;
    font-weight: 500;
  }
  .modal-body input, .modal-body textarea {
    padding: 8px 10px;
    border: 1px solid #d6d1c2;
    background: #fff;
    border-radius: 4px;
    font-size: 13px;
    font-family: inherit;
    color: #1f1c17;
    resize: vertical;
  }
  .modal-body textarea.mono {
    font-family: 'SF Mono', Menlo, monospace;
    font-size: 12px;
  }
  .modal-body input:focus, .modal-body textarea:focus {
    outline: none;
    border-color: #c96342;
  }
  .hint { margin: 0; }
  .modal-foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid #e8e3d6;
    background: #fafaf5;
  }
</style>
