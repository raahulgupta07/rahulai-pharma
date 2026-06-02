<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { base } from '$app/paths';
  import { confirmDelete } from '$lib/confirmDelete';

  const slug = $derived($page.params.slug);

  let token = $state('');
  let err = $state('');
  let loading = $state(false);

  let pages = $state<any[]>([]);
  let selectedId = $state<number | null>(null);
  let selected = $state<any | null>(null);
  let evidence = $state<any[]>([]);
  let showEvidence = $state(true);

  let newKey = $state('');
  let newTitle = $state('');
  let newEvidence = $state('');
  let newEvSource = $state('user');

  let recompiling = $state(false);
  let adding = $state(false);

  function _h(): Record<string, string> {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  function _hj(): Record<string, string> {
    return { ...(_h()), 'Content-Type': 'application/json' };
  }

  async function loadPages() {
    loading = true; err = '';
    try {
      const r = await fetch(`/api/pages/?project=${encodeURIComponent(slug)}&limit=100`, { headers: _h() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'load failed');
      pages = d.pages || [];
      if (!selectedId && pages.length) selectPage(pages[0].id);
    } catch (e: any) {
      err = e.message; pages = [];
    }
    loading = false;
  }

  async function selectPage(id: number) {
    selectedId = id;
    selected = null; evidence = [];
    try {
      const [pR, eR] = await Promise.all([
        fetch(`/api/pages/${id}`, { headers: _h() }),
        fetch(`/api/pages/${id}/evidence?limit=500`, { headers: _h() }),
      ]);
      if (pR.ok) selected = await pR.json();
      if (eR.ok) { const d = await eR.json(); evidence = d.evidence || []; }
    } catch (e: any) { err = e.message; }
  }

  async function createPage() {
    if (!newKey.trim()) return;
    try {
      const r = await fetch(`/api/pages/`, {
        method: 'POST', headers: _hj(),
        body: JSON.stringify({ project: slug, page_key: newKey.trim(), title: newTitle.trim() || null }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'create failed');
      newKey = ''; newTitle = '';
      await loadPages();
      if (d.page_id) selectPage(d.page_id);
    } catch (e: any) { err = e.message; }
  }

  async function deletePage(id: number) {
    if (!(await confirmDelete({ itemName: `page #${id}`, itemType: 'page (and all its evidence)' }))) return;
    try {
      const r = await fetch(`/api/pages/${id}`, { method: 'DELETE', headers: _h() });
      if (!r.ok) throw new Error((await r.json()).detail || 'delete failed');
      if (selectedId === id) { selectedId = null; selected = null; evidence = []; }
      await loadPages();
    } catch (e: any) { err = e.message; }
  }

  async function appendEvidence() {
    if (!selectedId || !newEvidence.trim()) return;
    adding = true;
    try {
      const r = await fetch(`/api/pages/${selectedId}/evidence`, {
        method: 'POST', headers: _hj(),
        body: JSON.stringify({ content: newEvidence.trim(), source: newEvSource }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'append failed');
      newEvidence = '';
      await selectPage(selectedId);
      await loadPages();
    } catch (e: any) { err = e.message; }
    adding = false;
  }

  async function recompile() {
    if (!selectedId) return;
    recompiling = true; err = '';
    try {
      const r = await fetch(`/api/pages/${selectedId}/recompile`, { method: 'POST', headers: _h() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'recompile failed');
      await selectPage(selectedId);
      await loadPages();
    } catch (e: any) { err = e.message; }
    recompiling = false;
  }

  function fmtTime(s: string) {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }

  onMount(() => {
    token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    loadPages();
  });
</script>

<svelte:head><title>Pages · {slug}</title></svelte:head>

<div class="page">
  <header class="hd">
    <a class="back" href="{base}/project/{slug}/settings">← Settings</a>
    <h1>Memory Pages</h1>
    <p class="sub">Each page has a <b>compiled truth</b> block (LLM-summarised current state) and an append-only <b>evidence timeline</b>. Compiled truth is replaceable; evidence is never erased.</p>
  </header>

  {#if err}<div class="err">{err}</div>{/if}

  <div class="grid">
    <!-- LEFT: list + create -->
    <aside class="left">
      <div class="create">
        <h2>New page</h2>
        <input class="inp" placeholder="page_key (e.g. customer_overview)" bind:value={newKey} />
        <input class="inp" placeholder="Title (optional)" bind:value={newTitle} />
        <button class="btn primary" onclick={createPage} disabled={!newKey.trim()}>+ Create</button>
      </div>

      <div class="list-wrap">
        <h2>Pages <span class="count">{pages.length}</span></h2>
        {#if loading}
          <div class="muted">Loading…</div>
        {:else if pages.length === 0}
          <div class="empty">No pages yet.</div>
        {:else}
          <ul class="plist">
            {#each pages as p (p.id)}
              <li class:active={selectedId === p.id}>
                <button class="pitem" onclick={() => selectPage(p.id)}>
                  <div class="pkey">{p.title || p.page_key}</div>
                  <div class="pmeta">
                    <span class="badge">{p.evidence_count} ev</span>
                    {#if p.compiled_at}
                      <span class="muted">compiled {fmtTime(p.compiled_at)}</span>
                    {:else}
                      <span class="muted unc">never compiled</span>
                    {/if}
                  </div>
                </button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    </aside>

    <!-- RIGHT: detail -->
    <section class="right">
      {#if !selected}
        <div class="empty">Select a page on the left, or create a new one.</div>
      {:else}
        <div class="rhead">
          <div>
            <h2 class="ptitle">{selected.title || selected.page_key}</h2>
            <div class="pmeta-sub">
              <code class="mono">{selected.page_key}</code>
              {#if selected.compiled_by}· compiled by <b>{selected.compiled_by}</b>{/if}
              {#if selected.compiled_at}· {fmtTime(selected.compiled_at)}{/if}
            </div>
          </div>
          <div class="actions">
            <button class="btn" onclick={recompile} disabled={recompiling}>{recompiling ? 'Recompiling…' : '↻ Recompile'}</button>
            <button class="btn danger" onclick={() => deletePage(selected.id)}>Delete</button>
          </div>
        </div>

        <div class="block">
          <div class="block-label">COMPILED TRUTH</div>
          <pre class="truth">{selected.compiled_truth || '(not yet compiled — add evidence and hit Recompile)'}</pre>
        </div>

        <div class="block">
          <div class="block-label add">ADD EVIDENCE</div>
          <textarea class="ev-input" rows="3" placeholder="Observed fact, change, finding…" bind:value={newEvidence}></textarea>
          <div class="ev-row">
            <select class="inp small" bind:value={newEvSource}>
              <option value="user">user</option>
              <option value="chat">chat</option>
              <option value="workflow">workflow</option>
              <option value="import">import</option>
              <option value="automl">automl</option>
            </select>
            <button class="btn primary" onclick={appendEvidence} disabled={adding || !newEvidence.trim()}>{adding ? 'Appending…' : '+ Append'}</button>
          </div>
        </div>

        <div class="block">
          <div class="block-label">
            EVIDENCE TIMELINE <span class="count">{evidence.length}</span>
            <button class="link" onclick={() => (showEvidence = !showEvidence)}>{showEvidence ? 'collapse' : 'expand'}</button>
          </div>
          {#if showEvidence}
            {#if evidence.length === 0}
              <div class="empty small">No evidence yet.</div>
            {:else}
              <ul class="evlist">
                {#each evidence as e (e.id)}
                  <li>
                    <div class="evhead">
                      <span class="evts">{fmtTime(e.ts)}</span>
                      <span class="evsrc">{e.source || '?'}</span>
                      {#if e.source_ref}<code class="mono">{e.source_ref}</code>{/if}
                      {#if e.author}<span class="muted">— {e.author}</span>{/if}
                    </div>
                    <div class="evbody">{e.content}</div>
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}
        </div>
      {/if}
    </section>
  </div>
</div>

<style>
  .page { max-width: 1400px; margin: 0 auto; padding: 24px 32px 80px; }
  .hd { margin-bottom: 20px; }
  .back { font-size: 11px; color: var(--pw-ink-soft, #888); text-decoration: none; }
  .back:hover { color: var(--pw-accent, #c96342); }
  h1 { font-family: var(--pw-serif, Georgia, serif); font-size: 18px; margin: 8px 0 4px; color: var(--pw-ink, #2c2a26); }
  .sub { color: var(--pw-ink-soft, #777); font-size: 11px; max-width: 820px; margin: 0; }
  .err { background: rgba(220,53,53,0.08); color: #c0392b; padding: 8px 12px; border: 1px solid rgba(220,53,53,0.3); border-radius: 0; margin-bottom: 16px; font-size: 11px; }

  .grid { display: grid; grid-template-columns: 320px 1fr; gap: 20px; align-items: start; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }

  .left, .right { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5e2dc); border-radius: 0; padding: 14px 16px; }
  .create { border-bottom: 1px solid var(--pw-border, #efece6); padding-bottom: 12px; margin-bottom: 12px; }
  h2 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #666); margin: 0 0 8px; }
  .count { display: inline-block; margin-left: 6px; padding: 1px 7px; background: var(--pw-bg-alt, #f5f1ea); border-radius: 0; font-size: 10px; color: var(--pw-ink, #2c2a26); font-weight: 600; }
  .inp { width: 100%; padding: 6px 10px; border: 1px solid var(--pw-border, #d8d4cc); border-radius: 0; font-size: 11px; margin-bottom: 6px; background: var(--pw-bg, #fff); color: var(--pw-ink, #2c2a26); box-sizing: border-box; }
  .inp.small { width: auto; margin-bottom: 0; }

  .btn { font-size: 11px; padding: 6px 12px; border: 1px solid var(--pw-border, #d8d4cc); background: var(--pw-bg, #fff); border-radius: 0; cursor: pointer; color: var(--pw-ink, #2c2a26); }
  .btn:hover { background: var(--pw-bg-alt, #f5f1ea); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.primary { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .btn.primary:hover { filter: brightness(0.95); background: var(--pw-accent, #c96342); }
  .btn.danger { color: #c0392b; border-color: rgba(220,53,53,0.3); }
  .btn.danger:hover { background: rgba(220,53,53,0.08); }

  .plist { list-style: none; margin: 0; padding: 0; max-height: 60vh; overflow-y: auto; }
  .plist li { margin-bottom: 4px; }
  .pitem { width: 100%; text-align: left; padding: 8px 10px; border: 1px solid transparent; border-radius: 0; background: transparent; cursor: pointer; color: var(--pw-ink, #2c2a26); }
  .pitem:hover { background: var(--pw-bg-alt, #f5f1ea); }
  .plist li.active .pitem { background: rgba(201,99,66,0.08); border-color: rgba(201,99,66,0.25); }
  .pkey { font-size: 11px; font-weight: 600; margin-bottom: 3px; }
  .pmeta { font-size: 10.5px; color: var(--pw-ink-soft, #888); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .badge { background: var(--pw-bg-alt, #f5f1ea); padding: 1px 6px; border-radius: 0; font-weight: 600; color: var(--pw-ink, #2c2a26); }
  .unc { color: #c98442; font-style: italic; }

  .rhead { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--pw-border, #efece6); margin-bottom: 14px; }
  .ptitle { font-family: var(--pw-serif, Georgia, serif); font-size: 19px; font-weight: 700; margin: 0 0 4px; color: var(--pw-ink, #2c2a26); text-transform: none; letter-spacing: 0; }
  .pmeta-sub { font-size: 11px; color: var(--pw-ink-soft, #777); }
  .actions { display: flex; gap: 6px; flex-shrink: 0; }

  .block { margin-bottom: 18px; }
  .block-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft, #666); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
  .block-label.add { color: var(--pw-accent, #c96342); }
  .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 11px; padding: 0; margin-left: auto; }
  .truth { background: var(--pw-bg-alt, #f5f1ea); border: 1px solid var(--pw-border, #efece6); border-radius: 0; padding: 14px 16px; font-family: inherit; font-size: 13.5px; line-height: 1.55; white-space: pre-wrap; color: var(--pw-ink, #2c2a26); margin: 0; }

  .ev-input { width: 100%; padding: 8px 10px; border: 1px solid var(--pw-border, #d8d4cc); border-radius: 0; font-size: 11px; resize: vertical; box-sizing: border-box; font-family: inherit; background: var(--pw-bg, #fff); color: var(--pw-ink, #2c2a26); }
  .ev-row { display: flex; gap: 6px; align-items: center; margin-top: 6px; }

  .evlist { list-style: none; margin: 0; padding: 0; max-height: 50vh; overflow-y: auto; border-left: 2px solid var(--pw-border, #efece6); padding-left: 12px; }
  .evlist li { padding: 8px 0; border-bottom: 1px dashed var(--pw-border, #efece6); }
  .evlist li:last-child { border-bottom: none; }
  .evhead { font-size: 11px; color: var(--pw-ink-soft, #888); margin-bottom: 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .evts { font-variant-numeric: tabular-nums; }
  .evsrc { background: var(--pw-bg-alt, #f5f1ea); padding: 1px 6px; border-radius: 0; color: var(--pw-ink, #2c2a26); font-weight: 600; text-transform: uppercase; font-size: 10px; }
  .evbody { font-size: 11px; line-height: 1.5; white-space: pre-wrap; color: var(--pw-ink, #2c2a26); }

  .empty, .muted { color: var(--pw-ink-soft, #888); font-size: 11px; }
  .empty.small { font-size: 11px; padding: 6px 0; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11px; background: var(--pw-bg-alt, #f5f1ea); padding: 1px 5px; border-radius: 0; }
</style>
