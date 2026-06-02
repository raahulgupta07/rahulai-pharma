<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { base } from '$app/paths';

  const slug = $derived($page.params.slug);

  let token = $state('');
  let query = $state('');
  let topK = $state(3);
  let loading = $state(false);
  let err = $state('');

  let chosen = $state<string | null>(null);
  let reason = $state('');
  let method = $state('');
  let candidates = $state<any[]>([]);

  function _h(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }

  async function submit() {
    if (!query.trim()) { err = 'Enter a query.'; return; }
    loading = true; err = ''; chosen = null; reason = ''; candidates = [];
    try {
      const r = await fetch(`/api/resolver/resolve`, {
        method: 'POST',
        headers: _h(),
        body: JSON.stringify({ project: slug, query, top_k: topK }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || 'resolve failed');
      chosen = d.chosen;
      reason = d.reason || '';
      method = d.method || '';
      candidates = d.candidates || [];
    } catch (e: any) {
      err = e.message;
    }
    loading = false;
  }

  async function loadCandidates() {
    try {
      const r = await fetch(`/api/resolver/candidates?project=${encodeURIComponent(slug)}`, { headers: _h() });
      const d = await r.json();
      if (r.ok) candidates = d.candidates || [];
    } catch {}
  }

  onMount(() => {
    token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    loadCandidates();
  });
</script>

<svelte:head><title>Resolver · {slug}</title></svelte:head>

<div class="page">
  <header class="hd">
    <a class="back" href="{base}/project/{slug}/settings">← Settings</a>
    <h1>Skill Resolver</h1>
    <p class="sub">LLM intent-classification router. Picks the best downstream skill for a user query from the registered skills.</p>
  </header>

  {#if err}<div class="err">{err}</div>{/if}

  <section class="sec">
    <div class="row"><h2>Test query</h2></div>
    <div class="form">
      <textarea class="ta" placeholder="e.g. Review this pull request for SQL injection and N+1 queries" bind:value={query} rows="3"></textarea>
      <div class="ctrls">
        <label class="lbl">Top K
          <input type="range" min="1" max="10" bind:value={topK} />
          <span class="k">{topK}</span>
        </label>
        <button class="btn primary" onclick={submit} disabled={loading}>{loading ? 'Resolving…' : 'Resolve'}</button>
      </div>
    </div>
  </section>

  {#if chosen !== null || reason}
    <section class="sec">
      <div class="row"><h2>Result</h2><span class="meta">method: {method || '—'}</span></div>
      {#if chosen}
        <div class="chosen">{chosen}</div>
      {:else}
        <div class="chosen none">No clear match</div>
      {/if}
      {#if reason}
        <div class="reason">{reason}</div>
      {/if}
    </section>
  {/if}

  <section class="sec">
    <div class="row"><h2>Candidates <span class="count">{candidates.length}</span></h2></div>
    {#if candidates.length === 0}
      <div class="empty">No skills registered for this project.</div>
    {:else}
      <table class="tbl">
        <thead><tr><th>Name</th><th>Category</th><th>Description</th><th>Tags</th></tr></thead>
        <tbody>
          {#each candidates as c}
            <tr class:hi={chosen && c.name === chosen}>
              <td class="nm">{c.name}</td>
              <td class="cat">{c.category || '—'}</td>
              <td class="desc">{c.description || '—'}</td>
              <td class="tags">{(c.tags || []).join(', ') || '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  .page { max-width: 1100px; margin: 0 auto; padding: 24px 32px 80px; }
  .hd { margin-bottom: 24px; }
  .back { font-size: 11px; color: var(--pw-ink-soft, #888); text-decoration: none; }
  .back:hover { color: var(--pw-accent, #c96342); }
  h1 { font-family: var(--pw-serif, Georgia, serif); font-size: 18px; margin: 8px 0 4px; color: var(--pw-ink, #2c2a26); }
  .sub { color: var(--pw-ink-soft, #777); font-size: 11px; max-width: 720px; margin: 0; }
  .err { background: rgba(220,53,53,0.08); color: #c0392b; padding: 8px 12px; border: 1px solid rgba(220,53,53,0.3); border-radius: 0; margin-bottom: 16px; font-size: 11px; }
  .sec { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5e2dc); border-radius: 0; padding: 16px 20px; margin-bottom: 20px; }
  .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  h2 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); margin: 0; }
  .count { display: inline-block; margin-left: 8px; padding: 1px 8px; background: var(--pw-bg-alt, #f5f1ea); border-radius: 0; font-size: 11px; color: var(--pw-ink, #2c2a26); }
  .meta { font-size: 11px; color: var(--pw-ink-soft, #888); font-family: ui-monospace, Menlo, monospace; }
  .empty { color: var(--pw-ink-soft, #888); font-size: 11px; }
  .form { display: flex; flex-direction: column; gap: 12px; }
  .ta { width: 100%; font: inherit; font-size: 11px; padding: 8px 10px; border: 1px solid var(--pw-border, #d8d4cc); border-radius: 0; background: var(--pw-bg, #fff); color: var(--pw-ink, #2c2a26); resize: vertical; }
  .ctrls { display: flex; align-items: center; gap: 16px; }
  .lbl { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--pw-ink-soft, #666); }
  .lbl input[type="range"] { width: 160px; }
  .k { font-variant-numeric: tabular-nums; min-width: 22px; text-align: right; font-weight: 700; color: var(--pw-ink, #2c2a26); }
  .btn { font-size: 11px; padding: 6px 14px; border: 1px solid var(--pw-border, #d8d4cc); background: var(--pw-bg, #fff); border-radius: 0; cursor: pointer; }
  .btn:hover { background: var(--pw-bg-alt, #f5f1ea); }
  .btn.primary { background: var(--pw-accent, #c96342); border-color: var(--pw-accent, #c96342); color: #fff; font-weight: 700; }
  .btn.primary:hover { filter: brightness(0.95); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .chosen { font-family: var(--pw-serif, Georgia, serif); font-size: 18px; color: var(--pw-accent, #c96342); font-weight: 700; margin-bottom: 6px; }
  .chosen.none { color: var(--pw-ink-soft, #888); font-style: italic; }
  .reason { font-size: 11px; color: var(--pw-ink, #2c2a26); }
  .tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
  .tbl thead th { text-align: left; padding: 6px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); border-bottom: 1px solid var(--pw-border, #e5e2dc); background: var(--pw-bg-alt, #f5f1ea); }
  .tbl tbody td { padding: 8px; border-bottom: 1px solid var(--pw-border, #efece6); vertical-align: top; }
  .tbl tbody tr:hover { background: rgba(201,99,66,0.03); }
  .tbl tbody tr.hi { background: rgba(201,99,66,0.10); }
  .nm { font-weight: 700; color: var(--pw-ink, #2c2a26); }
  .cat { font-size: 11px; color: var(--pw-ink-soft, #666); }
  .desc { color: var(--pw-ink, #2c2a26); }
  .tags { font-size: 11px; color: var(--pw-ink-soft, #888); font-family: ui-monospace, Menlo, monospace; }
</style>
