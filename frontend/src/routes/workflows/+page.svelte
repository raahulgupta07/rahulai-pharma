<script lang="ts">
  import { onMount } from 'svelte';

  let workflows = $state<any[]>([]);
  let runs = $state<any[]>([]);
  let selected = $state<any>(null);
  let runOutput = $state<string>('');

  const token = (): string | null =>
    typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;

  async function load() {
    const r = await fetch('/api/os/workflows', { headers: { Authorization: `Bearer ${token() || ''}` } });
    const j = await r.json();
    workflows = j?.workflows || [];
    const rr = await fetch('/api/os/workflows/runs?limit=30', { headers: { Authorization: `Bearer ${token() || ''}` } });
    const rj = await rr.json();
    runs = rj?.runs || [];
  }

  async function run(id: string) {
    runOutput = 'starting…';
    const r = await fetch(`/api/os/workflows/${id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
      body: JSON.stringify({ inputs: {} }),
    });
    const j = await r.json();
    runOutput = JSON.stringify(j, null, 2);
    load();
  }

  function select(wf: any) {
    selected = wf;
  }

  onMount(load);
</script>

<div class="page">
  <header><h1>Workflows</h1><p class="muted">5 builtin DAGs + your custom workflows.</p></header>

  <div class="layout">
    <aside>
      {#each workflows as wf}
        <button class="row" class:active={selected?.id === wf.id} onclick={() => select(wf)}>
          <div class="rname">{wf.name}</div>
          <div class="rmeta">
            <span class="chip">{wf.category || '—'}</span>
            <span class="chip">{wf.trigger_kind}</span>
            {#if wf.is_builtin}<span class="chip builtin">builtin</span>{/if}
          </div>
        </button>
      {/each}
    </aside>

    <main>
      {#if !selected}
        <div class="empty">Pick a workflow to view + run.</div>
      {:else}
        <h2>{selected.name}</h2>
        <p class="muted">{selected.description}</p>
        <div class="meta">
          <span><strong>ID:</strong> <code>{selected.id}</code></span>
          <span><strong>Category:</strong> {selected.category || '—'}</span>
          <span><strong>Trigger:</strong> {selected.trigger_kind}{selected.cron_expr ? ` · ${selected.cron_expr}` : ''}</span>
          <span><strong>Steps:</strong> {(selected.spec?.steps || []).length}</span>
        </div>
        <button class="primary" onclick={() => run(selected.id)}>▶ run now</button>

        {#if selected.spec}
          <h3>DAG</h3>
          <pre class="dag">{JSON.stringify(selected.spec, null, 2)}</pre>
        {/if}

        {#if runOutput}
          <h3>Last Trigger Result</h3>
          <pre class="dag">{runOutput}</pre>
        {/if}
      {/if}
    </main>
  </div>

  <h2>Recent Runs</h2>
  <table class="tbl">
    <thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Pass Rate / Result</th><th>Started</th></tr></thead>
    <tbody>
      {#each runs as r}
        <tr>
          <td class="mono">{r.id.slice(0, 14)}…</td>
          <td>{r.def_id}</td>
          <td><span class="chip status-{r.status}">{r.status}</span></td>
          <td class="muted">{r.error || '—'}</td>
          <td class="muted">{r.started_at}</td>
        </tr>
      {/each}
      {#if !runs.length}<tr><td colspan="5" class="empty">No runs yet.</td></tr>{/if}
    </tbody>
  </table>
</div>

<style>
  .page { padding: 24px 32px 60px; max-width: 1280px; margin: 0 auto; font: 14px Inter; color: var(--pw-ink, #2c2a26); }
  h1 { font: 600 28px 'Source Serif 4', Georgia, serif; margin: 0; color: var(--pw-accent, #c96342); }
  h2 { font: 600 20px 'Source Serif 4', Georgia, serif; margin: 24px 0 8px; }
  h3 { font: 600 14px Inter; text-transform: uppercase; letter-spacing: 0.04em; margin: 16px 0 6px; }
  .muted { color: var(--pw-ink-soft, #87837a); font-size: 11px; }
  .layout { display: grid; grid-template-columns: 320px 1fr; gap: 20px; margin: 24px 0; }
  aside { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); padding: 8px; max-height: 600px; overflow-y: auto; }
  .row { display: block; width: 100%; text-align: left; background: none; border: none; padding: 10px 12px; cursor: pointer; border-radius: var(--pw-radius-sm); }
  .row:hover { background: rgba(201, 99, 66, 0.04); }
  .row.active { background: rgba(201, 99, 66, 0.08); }
  .rname { font-weight: 600; font-size: 11px; }
  .rmeta { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
  main { background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); padding: 24px; min-height: 400px; }
  .empty { text-align: center; color: var(--pw-ink-soft, #87837a); padding: 60px; }
  .meta { display: flex; gap: 16px; flex-wrap: wrap; margin: 12px 0; font-size: 11px; }
  .chip { display: inline-block; background: var(--pw-bg-alt, #f1ede4); border-radius: var(--pw-radius-sm); padding: 2px 8px; font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.04em; }
  .chip.builtin { background: rgba(201, 99, 66, 0.14); color: var(--pw-accent, #c96342); }
  .chip.status-done { background: #d1fae5; color: #065f46; }
  .chip.status-running { background: #fef3c7; color: #92400e; }
  .chip.status-failed { background: #fee2e2; color: #991b1b; }
  .chip.status-regressed { background: #fee2e2; color: #991b1b; }
  pre.dag { background: #1a1614; color: #e7e3da; padding: 16px; border-radius: var(--pw-radius-sm); overflow: auto; font: 12px/1.5 'JetBrains Mono', monospace; }
  button.primary { background: var(--pw-accent, #c96342); color: white; border: none; border-radius: var(--pw-radius-sm); padding: 10px 18px; cursor: pointer; font: 600 13px Inter; margin: 8px 0; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--pw-radius-sm); overflow: hidden; font-size: 11px; }
  .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
  .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; }
  .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
  .mono { font-family: 'JetBrains Mono', monospace; }
</style>
