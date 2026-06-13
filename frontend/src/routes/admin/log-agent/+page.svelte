<script lang="ts">
  type Group = { label: string; count: number; detail?: string };

  let question = $state('');
  let days = $state(14);
  let projectSlug = $state('');

  let answer = $state<string | null>(null);
  let groups = $state<Group[]>([]);
  let pattern = $state<string | null>(null);
  let windowDays = $state(0);
  let loading = $state(false);
  let error = $state<string | null>(null);

  const maxCount = $derived(groups.reduce((m, g) => Math.max(m, g.count), 0) || 1);

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }
             : { 'Content-Type': 'application/json' };
  }

  async function ask() {
    if (!question.trim() || loading) return;
    loading = true;
    error = null;
    try {
      const r = await fetch('/api/admin/log-agent/ask', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          question: question.trim(),
          days: Number(days) || 14,
          project_slug: projectSlug.trim() || null
        })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      answer = j.answer ?? null;
      groups = Array.isArray(j.groups) ? j.groups : [];
      pattern = j.pattern ?? null;
      windowDays = j.window_days ?? (Number(days) || 14);
    } catch (e: any) {
      error = e?.message || 'Failed to query logs';
      answer = null;
      groups = [];
      pattern = null;
    } finally {
      loading = false;
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
  }
</script>

<div class="la-shell">
  <header class="la-head">
    <div class="la-title">$ ask-the-logs</div>
    <div class="la-sub">Super-admin · query platform log tables in natural language</div>
  </header>

  <div class="la-box">
    <textarea
      class="la-q"
      bind:value={question}
      onkeydown={onKey}
      placeholder="e.g. what is failing the most? which task burns the most LLM calls? any drift?"
      rows="3"
    ></textarea>
    <div class="la-controls">
      <label class="la-ctl">
        <span>WINDOW (days)</span>
        <input type="number" min="1" max="365" bind:value={days} />
      </label>
      <label class="la-ctl">
        <span>PROJECT SLUG (optional)</span>
        <input type="text" bind:value={projectSlug} placeholder="all projects" />
      </label>
      <button class="la-run" onclick={ask} disabled={loading || !question.trim()}>
        {loading ? 'QUERYING…' : 'ASK ▸'}
      </button>
    </div>
    <div class="la-hint">⌘/Ctrl + Enter to run</div>
  </div>

  {#if error}
    <div class="la-error">! {error}</div>
  {/if}

  {#if answer !== null}
    <section class="la-answer">
      <div class="la-label">ANSWER · last {windowDays}d</div>
      <p class="la-answer-text">{answer}</p>
    </section>
  {/if}

  {#if pattern}
    <section class="la-pattern">
      <div class="la-pattern-tag">⚠ RECURRING PATTERN</div>
      <p>{pattern}</p>
    </section>
  {/if}

  {#if groups.length > 0}
    <section class="la-groups">
      <div class="la-label">LOG GROUPS</div>
      <div class="la-bars">
        {#each groups as g (g.label)}
          <div class="la-bar-row">
            <div class="la-bar-label" title={g.detail ?? ''}>{g.label}</div>
            <div class="la-bar-track">
              <div class="la-bar-fill" style="width:{Math.max(2, (g.count / maxCount) * 100)}%"></div>
            </div>
            <div class="la-bar-count">{g.count}</div>
          </div>
        {/each}
      </div>
    </section>
  {:else if answer !== null && !error}
    <div class="la-empty">No grouped log activity in this window.</div>
  {/if}
</div>

<style>
  .la-shell {
    max-width: 880px;
    margin: 0 auto;
    padding: 32px 24px 80px;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    color: var(--pw-ink);
  }
  .la-head { margin-bottom: 20px; }
  .la-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--pw-accent);
    letter-spacing: 0.02em;
  }
  .la-sub {
    font-size: 12px;
    color: var(--pw-ink-soft);
    margin-top: 4px;
  }
  .la-box {
    border: 2px solid var(--pw-border-strong);
    border-radius: var(--pw-radius-sm);
    background: var(--pw-bg-alt);
    padding: 14px;
  }
  .la-q {
    width: 100%;
    box-sizing: border-box;
    background: var(--pw-bg);
    border: 1px solid var(--pw-border);
    border-radius: var(--pw-radius-sm);
    padding: 10px;
    font-family: inherit;
    font-size: 14px;
    color: var(--pw-ink);
    resize: vertical;
  }
  .la-q:focus { outline: none; border-color: var(--pw-accent); }
  .la-controls {
    display: flex;
    gap: 12px;
    align-items: flex-end;
    margin-top: 12px;
    flex-wrap: wrap;
  }
  .la-ctl { display: flex; flex-direction: column; gap: 4px; }
  .la-ctl span { font-size: 10px; letter-spacing: 0.06em; color: var(--pw-ink-soft); }
  .la-ctl input {
    background: var(--pw-bg);
    border: 1px solid var(--pw-border);
    border-radius: var(--pw-radius-sm);
    padding: 7px 9px;
    font-family: inherit;
    font-size: 13px;
    color: var(--pw-ink);
  }
  .la-ctl input:focus { outline: none; border-color: var(--pw-accent); }
  .la-ctl input[type='number'] { width: 110px; }
  .la-run {
    margin-left: auto;
    background: var(--pw-accent);
    color: #fff;
    border: none;
    border-radius: var(--pw-radius-sm);
    padding: 9px 18px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: pointer;
  }
  .la-run:disabled { opacity: 0.5; cursor: not-allowed; }
  .la-hint { font-size: 10px; color: var(--pw-ink-soft); margin-top: 8px; }

  .la-error {
    margin-top: 16px;
    border: 1px solid #d44;
    color: #b22;
    background: #fdecec;
    border-radius: var(--pw-radius-sm);
    padding: 10px 12px;
    font-size: 13px;
  }

  .la-label {
    font-size: 10px;
    letter-spacing: 0.08em;
    color: var(--pw-ink-soft);
    margin-bottom: 8px;
  }
  .la-answer {
    margin-top: 20px;
    border-left: 3px solid var(--pw-accent);
    background: var(--pw-surface-warm);
    border-radius: var(--pw-radius-sm);
    padding: 14px 16px;
  }
  .la-answer-text { margin: 0; font-size: 14.5px; line-height: 1.6; }

  .la-pattern {
    margin-top: 16px;
    border: 2px solid var(--pw-accent);
    background: var(--pw-accent-soft);
    border-radius: var(--pw-radius-sm);
    padding: 12px 14px;
  }
  .la-pattern-tag {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--pw-accent-ink);
    margin-bottom: 6px;
  }
  .la-pattern p { margin: 0; font-size: 13.5px; line-height: 1.5; }

  .la-groups { margin-top: 22px; }
  .la-bars { display: flex; flex-direction: column; gap: 6px; }
  .la-bar-row {
    display: grid;
    grid-template-columns: 220px 1fr 56px;
    align-items: center;
    gap: 10px;
  }
  .la-bar-label {
    font-size: 12px;
    color: var(--pw-ink);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .la-bar-track {
    background: var(--pw-bg-alt);
    border: 1px solid var(--pw-border);
    border-radius: var(--pw-radius-sm);
    height: 18px;
    overflow: hidden;
  }
  .la-bar-fill {
    height: 100%;
    background: var(--pw-accent);
    transition: width 0.3s ease;
  }
  .la-bar-count {
    font-size: 12px;
    font-weight: 700;
    text-align: right;
    color: var(--pw-accent-ink);
  }
  .la-empty {
    margin-top: 20px;
    font-size: 13px;
    color: var(--pw-ink-soft);
  }
  @media (max-width: 640px) {
    .la-bar-row { grid-template-columns: 130px 1fr 44px; }
  }
</style>
