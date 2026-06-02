<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 type Score = {
 tool: string;
 project: string;
 score: number;
 calls: number;
 success_pct: number;
 last_used?: string;
 patched?: boolean;
 };

 let loading = $state(true);
 let error = $state<string | null>(null);
 let rows = $state<Score[]>([]);
 let projectFilter = $state('');
 let sortDir = $state<'asc' | 'desc'>('asc');

 function authHeaders(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 function normalize(items: any[], project: string): Score[] {
 return items.map((x: any) => ({
 tool: x.tool || x.tool_name || x.name || '—',
 project: x.project || x.project_slug || project,
 score: Number(x.score ?? x.utility_score ?? 0),
 calls: Number(x.calls ?? x.invocations ?? x.count ?? 0),
 success_pct: Number(x.success_pct ?? x.success_rate ?? 0),
 last_used: x.last_used || x.last_used_at || x.updated_at,
 patched: !!(x.patched || x.has_patch || x.is_patched)
 }));
 }

 async function load() {
 loading = true;
 error = null;
 try {
 const r = await fetch('/api/projects/_admin_/refine-tools/scores', { headers: authHeaders() });
 if (r.ok) {
 const j = await r.json();
 const list = Array.isArray(j) ? j : j.items || j.scores || [];
 rows = normalize(list, '_admin_');
 } else {
 await fanOut();
 }
 } catch {
 await fanOut();
 } finally {
 loading = false;
 }
 }

 async function fanOut() {
 try {
 const pr = await fetch('/api/projects', { headers: authHeaders() });
 if (!pr.ok) throw new Error(`HTTP ${pr.status}`);
 const pj = await pr.json();
 const list = Array.isArray(pj) ? pj : pj.items || pj.projects || [];
 const slugs = list.map((p: any) => p.slug || p.project_slug || p.id).filter(Boolean).slice(0, 30);
 const collected: Score[] = [];
 const tasks = slugs.map(async (slug: string) => {
 try {
 const rr = await fetch(`/api/projects/${encodeURIComponent(slug)}/refine-tools/scores`, { headers: authHeaders() });
 if (!rr.ok) return;
 const jj = await rr.json();
 const items = Array.isArray(jj) ? jj : jj.items || jj.scores || [];
 collected.push(...normalize(items, slug));
 } catch {}
 });
 await Promise.all(tasks);
 rows = collected;
 if (rows.length === 0) error = 'No tool scores available.';
 } catch (e: any) {
 error = e?.message || 'Failed to load';
 }
 }

 const projects = $derived(Array.from(new Set(rows.map((r) => r.project))).sort());

 const filtered = $derived.by(() => {
 const filt = projectFilter ? rows.filter((r) => r.project === projectFilter) : rows;
 return [...filt].sort((a, b) => sortDir === 'asc' ? a.score - b.score : b.score - a.score);
 });

 function scoreColor(s: number) {
 if (s < 60) return '#c96342';
 if (s < 80) return '#87837a';
 return '#10b981';
 }
 function fmtTime(s?: string) {
 if (!s) return '—';
 try { return new Date(s).toLocaleString(); } catch { return s; }
 }
 function toggleSort() { sortDir = sortDir === 'asc' ? 'desc' : 'asc'; }

 onMount(load);
</script>

<p class="muted">Per-tool utility scores. Best-effort across projects.</p>

<div class="toolbar">
  <label class="muted-sm" for="proj-filter">Project:</label>
  <select id="proj-filter" bind:value={projectFilter}>
    <option value="">All</option>
    {#each projects as p}<option value={p}>{p}</option>{/each}
  </select>
  <button class="chip-btn" onclick={toggleSort}>Score {sortDir === 'asc' ? '↑' : '↓'}</button>
  <button class="link" onclick={load}>↻ Refresh</button>
</div>

{#if loading}
  <div class="empty">Loading…</div>
{:else if error}
  <div class="empty err"><Icon name="alert-triangle" size={14} /> {error}</div>
{:else if filtered.length === 0}
  <div class="empty">No tool data.</div>
{:else}
  <table class="tbl">
    <thead>
      <tr><th>Tool</th><th>Project</th><th>Score</th><th class="ra">Calls</th><th class="ra">Success %</th><th>Last Used</th><th>Patched</th></tr>
    </thead>
    <tbody>
      {#each filtered as r}
        <tr>
          <td class="mono">{r.tool}</td>
          <td>{r.project}</td>
          <td>
            <div class="bar-wrap">
              <div class="bar" style="width: {Math.max(0, Math.min(100, r.score))}%; background: {scoreColor(r.score)};"></div>
              <span class="bar-val">{r.score.toFixed(0)}</span>
            </div>
          </td>
          <td class="ra mono">{r.calls}</td>
          <td class="ra mono">{r.success_pct.toFixed(1)}%</td>
          <td class="small">{fmtTime(r.last_used)}</td>
          <td>{#if r.patched}<span class="badge">PATCHED</span>{/if}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
 .muted { color: var(--pw-ink-soft, #87837a); font-size: 12px; margin: 0 0 12px; }
 .muted-sm { color: var(--pw-ink-soft, #87837a); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
 .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; }
 .toolbar select {
 padding: 4px 8px;
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 background: var(--pw-surface, #faf9f5);
 font: 12px Inter, system-ui, sans-serif;
 color: var(--pw-ink, #2c2a26);
 }
 .chip-btn { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; padding: 4px 10px; font: 600 11px Inter, system-ui, sans-serif; cursor: pointer; color: var(--pw-ink-soft, #87837a); }
 .link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font: 12px Inter, system-ui, sans-serif; margin-left: auto; }
 .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 0; overflow: hidden; font-size: 13px; }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; vertical-align: middle; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter, system-ui, sans-serif; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #87837a); }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .ra { text-align: right; }
 .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
 .small { font-size: 12px; color: var(--pw-ink-soft, #87837a); }
 .bar-wrap { position: relative; background: var(--pw-bg-alt, #f1ede4); border-radius: 0; height: 16px; width: 120px; }
 .bar { height: 100%; border-radius: 0; }
 .bar-val { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 10px 'JetBrains Mono', monospace; color: var(--pw-ink, #2c2a26); }
 .badge { display: inline-block; background: rgba(201, 99, 66, 0.12); color: var(--pw-accent, #c96342); border-radius: 0; padding: 2px 6px; font: 600 10px Inter, system-ui, sans-serif; }
 .empty { padding: 40px; text-align: center; color: var(--pw-ink-soft, #87837a); background: var(--pw-surface, #faf9f5); border: 1px dashed var(--pw-border, #e7e3da); border-radius: 0; }
 .empty.err { color: #ef4444; }
</style>
