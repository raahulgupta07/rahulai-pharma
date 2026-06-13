<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { base } from '$app/paths';
  import { confirmDelete } from '$lib/confirmDelete';

  const slug = $derived($page.params.slug);

  let token = $state('');
  let loading = $state(false);
  let err = $state('');

  let rules = $state<any[]>([]);
  let history = $state<any[]>([]);
  let showInactive = $state(true);

  function _h(): Record<string, string> {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadRules() {
    loading = true; err = '';
    try {
      const params = new URLSearchParams({ project: slug, include_inactive: String(showInactive) });
      const r = await fetch(`/api/corrections/rules?${params}`, { headers: _h() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || 'load failed');
      rules = d.rules || [];
    } catch (e: any) {
      err = e.message; rules = [];
    }
    loading = false;
  }

  async function loadHistory() {
    try {
      const params = new URLSearchParams({ project: slug, limit: '50' });
      const r = await fetch(`/api/corrections/history?${params}`, { headers: _h() });
      const d = await r.json();
      if (r.ok) history = d.corrections || [];
    } catch {}
  }

  async function toggleRule(id: number) {
    try {
      const r = await fetch(`/api/corrections/rules/${id}/toggle`, { method: 'POST', headers: _h() });
      if (!r.ok) throw new Error((await r.json()).detail || 'toggle failed');
      await loadRules();
    } catch (e: any) { err = e.message; }
  }

  async function deleteRule(id: number) {
    if (!(await confirmDelete({ itemName: `rule #${id}`, itemType: 'rule' }))) return;
    try {
      const r = await fetch(`/api/corrections/rules/${id}`, { method: 'DELETE', headers: _h() });
      if (!r.ok) throw new Error((await r.json()).detail || 'delete failed');
      await loadRules();
    } catch (e: any) { err = e.message; }
  }

  function fmtTime(s: string) {
    if (!s) return '—';
    try { return new Date(s).toLocaleString(); } catch { return s; }
  }

  function scopeBadge(r: any): string {
    if (r.scope === 'agent') return `agent · ${r.scope_target || '—'}`;
    if (r.scope === 'skill') return `skill · ${r.scope_target || '—'}`;
    return 'project';
  }

  onMount(() => {
    token = (typeof localStorage !== 'undefined' && localStorage.getItem('dash_token')) || '';
    loadRules();
    loadHistory();
  });

  $effect(() => {
    if (slug) { void showInactive; loadRules(); }
  });
</script>

<svelte:head><title>Rules · {slug}</title></svelte:head>

<div class="page">
  <header class="hd">
    <a class="back" href="{base}/project/{slug}/settings">← Settings</a>
    <h1>Learned Rules</h1>
    <p class="sub">Durable rules extracted from your edits to agent output. Active rules are injected into the Leader's system prompt on every run.</p>
  </header>

  {#if err}
    <div class="err">{err}</div>
  {/if}

  <section class="sec">
    <div class="row">
      <h2>Active rules <span class="count">{rules.filter(r => r.active).length}</span></h2>
      <label class="chk"><input type="checkbox" bind:checked={showInactive} /> Show inactive</label>
    </div>
    {#if loading}
      <div class="muted">Loading…</div>
    {:else if rules.length === 0}
      <div class="empty">No rules yet. Edit any agent output and a rule will be extracted automatically.</div>
    {:else}
      <table class="tbl">
        <thead><tr><th>Rule</th><th>Scope</th><th>Hits</th><th>Created</th><th></th></tr></thead>
        <tbody>
          {#each rules as r (r.id)}
            <tr class:inactive={!r.active}>
              <td class="rule">{r.rule_text}</td>
              <td class="scope">{scopeBadge(r)}</td>
              <td class="num">{r.hit_count ?? 0}</td>
              <td class="muted">{fmtTime(r.created_at)}</td>
              <td class="actions">
                <button class="btn" onclick={() => toggleRule(r.id)}>{r.active ? 'Disable' : 'Enable'}</button>
                <button class="btn danger" onclick={() => deleteRule(r.id)}>Delete</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <section class="sec">
    <div class="row"><h2>Recent corrections <span class="count">{history.length}</span></h2></div>
    {#if history.length === 0}
      <div class="empty">No corrections recorded yet.</div>
    {:else}
      <table class="tbl">
        <thead><tr><th>When</th><th>Agent</th><th>Run</th><th>By</th><th>Diff</th></tr></thead>
        <tbody>
          {#each history as c (c.id)}
            <tr>
              <td class="muted">{fmtTime(c.created_at)}</td>
              <td>{c.agent_name || '—'}</td>
              <td class="mono">{c.run_id || '—'}</td>
              <td>{c.created_by || '—'}</td>
              <td><details><summary>view</summary><pre class="diff">{c.diff_summary || ''}</pre></details></td>
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
  .err { background: rgba(220, 53, 53, 0.08); color: #c0392b; padding: 8px 12px; border: 1px solid rgba(220, 53, 53, 0.3); border-radius: var(--pw-radius-sm); margin-bottom: 16px; font-size: 11px; }
  .sec { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5e2dc); border-radius: var(--pw-radius-sm); padding: 16px 20px; margin-bottom: 20px; }
  .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  h2 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); margin: 0; }
  .count { display: inline-block; margin-left: 8px; padding: 1px 8px; background: var(--pw-bg-alt, #f5f1ea); border-radius: var(--pw-radius-sm); font-size: 11px; color: var(--pw-ink, #2c2a26); }
  .chk { font-size: 11px; color: var(--pw-ink-soft, #666); cursor: pointer; }
  .empty, .muted { color: var(--pw-ink-soft, #888); font-size: 11px; }
  .tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
  .tbl thead th { text-align: left; padding: 6px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #666); border-bottom: 1px solid var(--pw-border, #e5e2dc); background: var(--pw-bg-alt, #f5f1ea); }
  .tbl tbody td { padding: 8px; border-bottom: 1px solid var(--pw-border, #efece6); vertical-align: top; }
  .tbl tbody tr:hover { background: rgba(201, 99, 66, 0.03); }
  .tbl tbody tr.inactive { opacity: 0.5; }
  .rule { max-width: 520px; }
  .scope { font-size: 11px; color: var(--pw-ink-soft, #666); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
  .actions { white-space: nowrap; }
  .btn { font-size: 11px; padding: 4px 10px; border: 1px solid var(--pw-border, #d8d4cc); background: var(--pw-bg, #fff); border-radius: var(--pw-radius-sm); cursor: pointer; margin-left: 4px; }
  .btn:hover { background: var(--pw-bg-alt, #f5f1ea); }
  .btn.danger { color: #c0392b; border-color: rgba(220, 53, 53, 0.3); }
  .btn.danger:hover { background: rgba(220, 53, 53, 0.08); }
  pre.diff { font-family: ui-monospace, Menlo, monospace; font-size: 11px; background: var(--pw-bg-alt, #f5f1ea); padding: 8px; border-radius: var(--pw-radius-sm); max-height: 280px; overflow: auto; margin: 6px 0 0; white-space: pre-wrap; }
  details summary { cursor: pointer; font-size: 11px; color: var(--pw-accent, #c96342); }
</style>
