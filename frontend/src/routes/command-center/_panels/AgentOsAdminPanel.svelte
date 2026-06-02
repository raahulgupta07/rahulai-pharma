<script lang="ts">
  let { sub = 'overview' } = $props<{ sub?: string }>();

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  const ENDPOINTS: Record<string, string> = {
    overview: '/api/agent-os-admin/summary',
    registry: '/api/agent-os-admin/registry',
    capabilities: '/api/agent-os-admin/capabilities',
    quotas: '/api/agent-os-admin/quotas',
    models: '/api/agent-os-admin/models',
    tools: '/api/agent-os-admin/tools',
    memory: '/api/agent-os-admin/memory',
    workflows: '/api/agent-os-admin/workflows',
    kill: '/api/agent-os-admin/kill-switch',
    cost: '/api/agent-os-admin/cost-guard',
  };

  let data = $state<any>(null);
  let loading = $state(false);
  let err = $state<string>('');
  let killConfirm1 = $state(false);
  let killConfirm2 = $state(false);

  const endpoint = $derived(ENDPOINTS[sub] || ENDPOINTS.overview);

  async function load(ep: string) {
    loading = true; err = ''; data = null;
    killConfirm1 = false; killConfirm2 = false;
    try {
      const r = await fetch(ep, { headers: _h() });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e: any) {
      err = e?.message || 'load failed';
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(endpoint); });

  async function toggleItem(kind: 'capabilities' | 'tools', id: string, enabled: boolean) {
    try {
      await fetch(`/api/agent-os-admin/${kind}/${id}/toggle`, {
        method: 'POST',
        headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !enabled }),
      });
      await load(endpoint);
    } catch {}
  }

  async function killSwitchToggle() {
    try {
      await fetch('/api/agent-os-admin/kill-switch/toggle', {
        method: 'POST',
        headers: _h(),
      });
      await load(endpoint);
    } catch {}
  }

  function rows(): any[] {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    for (const k of ['items', 'rows', 'agents', 'capabilities', 'quotas', 'models', 'tools', 'memory', 'workflows', 'overrides']) {
      if (Array.isArray((data as any)[k])) return (data as any)[k];
    }
    return [];
  }

  function stats(): { k: string; v: any }[] {
    if (!data) return [];
    if (data.stats && typeof data.stats === 'object') {
      return Object.entries(data.stats).slice(0, 5).map(([k, v]) => ({ k, v }));
    }
    const out: { k: string; v: any }[] = [];
    for (const [k, v] of Object.entries(data)) {
      if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') out.push({ k, v });
      if (out.length >= 5) break;
    }
    return out;
  }

  function cols(list: any[]): string[] {
    if (!list.length) return [];
    const first = list[0];
    if (typeof first !== 'object' || first === null) return [];
    return Object.keys(first).slice(0, 8);
  }
</script>

<div class="sub-content">
  {#if loading}
    <div class="muted">loading…</div>
  {:else if err}
    <div class="err">error: {err}</div>
  {:else if sub === 'overview'}
    {@const st = stats()}
    {#if st.length}
      <div class="stat-strip">
        {#each st.slice(0, 5) as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{String(s.v)}</div>
          </div>
        {/each}
      </div>
    {:else}
      <div class="muted">no summary data</div>
    {/if}
    <div class="section-title">Recent activity</div>
    <table class="tbl">
      <thead><tr><th>when</th><th>agent</th><th>event</th><th>detail</th></tr></thead>
      <tbody>
        <tr><td colspan="4" class="muted" style="text-align:center; padding:20px;">no recent activity</td></tr>
      </tbody>
    </table>
  {:else}
    {@const st = stats()}
    {@const lst = rows()}
    {@const cs = cols(lst)}
    {#if st.length}
      <div class="stat-strip">
        {#each st as s}
          <div class="stat">
            <div class="stat-k">{s.k}</div>
            <div class="stat-v">{String(s.v)}</div>
          </div>
        {/each}
      </div>
    {/if}

    {#if sub === 'kill'}
      <div class="kill-box">
        <div class="kill-title">⚠ EMERGENCY KILL SWITCH</div>
        <div class="kill-sub">Halts ALL agent execution platform-wide. Requires double confirmation.</div>
        {#if !killConfirm1}
          <button class="btn-danger" onclick={() => killConfirm1 = true}>ARM KILL SWITCH</button>
        {:else if !killConfirm2}
          <button class="btn-danger" onclick={() => killConfirm2 = true}>CONFIRM (1/2)</button>
          <button class="btn" onclick={() => { killConfirm1 = false; killConfirm2 = false; }}>cancel</button>
        {:else}
          <button class="btn-danger" onclick={killSwitchToggle}>EXECUTE TOGGLE (2/2)</button>
          <button class="btn" onclick={() => { killConfirm1 = false; killConfirm2 = false; }}>cancel</button>
        {/if}
      </div>
    {/if}

    {#if cs.length}
      <table class="tbl">
        <thead>
          <tr>
            {#each cs as c}<th>{c}</th>{/each}
            {#if sub === 'capabilities' || sub === 'tools'}<th>ACTION</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each lst as r}
            <tr>
              {#each cs as c}<td>{typeof r[c] === 'object' ? JSON.stringify(r[c]) : (r[c] ?? '')}</td>{/each}
              {#if sub === 'capabilities' || sub === 'tools'}
                <td>
                  <button class="btn-sm" onclick={() => toggleItem(sub as any, r.id || r.name, !!r.enabled)}>
                    {r.enabled ? 'disable' : 'enable'}
                  </button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {:else if sub !== 'kill'}
      <div class="muted">no data</div>
    {/if}
  {/if}
</div>

<style>
  .sub-content { width: 100%; min-width: 0; }
  .muted { color: var(--pw-ink-soft); font: 12px Inter; padding: 20px 0; }
  .err { color: #c0392b; font: 12px Inter; padding: 12px; background: rgba(192,57,43,0.08); border: 1px solid #c0392b; }
  .stat-strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 16px; }
  .stat { background: var(--pw-surface); border: 1px solid var(--pw-border); padding: 10px 12px; }
  .stat-k { font: 600 10px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin-bottom: 4px; }
  .stat-v { font: 600 18px Inter; color: var(--pw-ink); }
  .section-title { font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-ink-soft); margin: 16px 0 8px; }
  .tbl { width: 100%; border-collapse: collapse; background: var(--pw-surface); border: 1px solid var(--pw-border); font-size: 12px; }
  .tbl th { text-align: left; padding: 8px 12px; font: 600 10px Inter; text-transform: uppercase; background: var(--pw-bg-alt); border-bottom: 1px solid var(--pw-border); }
  .tbl td { padding: 8px 12px; border-top: 1px solid var(--pw-border); vertical-align: top; }
  .btn { padding: 6px 14px; font: 600 11px Inter; letter-spacing: 0.04em; background: var(--pw-bg-alt); color: var(--pw-ink); border: 1px solid var(--pw-border); cursor: pointer; margin-left: 8px; }
  .btn-sm { padding: 4px 10px; font: 600 10px Inter; border: 1px solid var(--pw-border); background: var(--pw-bg-alt); cursor: pointer; }
  .btn-danger { padding: 8px 18px; font: 700 12px Inter; letter-spacing: 0.06em; background: #c0392b; color: #fff; border: none; cursor: pointer; }
  .kill-box { padding: 20px; background: rgba(192,57,43,0.06); border: 2px solid #c0392b; margin-bottom: 16px; }
  .kill-title { font: 700 14px Inter; color: #c0392b; margin-bottom: 6px; }
  .kill-sub { font: 12px Inter; color: var(--pw-ink-soft); margin-bottom: 14px; }
</style>
