<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';
 import { base } from '$app/paths';

 let agents = $state<any[]>([]);
 let workflows = $state<any[]>([]);
 let skills = $state<any[]>([]);
 let mcpServers = $state<any[]>([]);
 let approvals = $state<any[]>([]);
 let hitlPending = $state<any[]>([]);
 let loading = $state(true);
 let activeTab = $state<'overview' | 'agents' | 'workflows' | 'skills' | 'mcp' | 'approvals' | 'hitl'>('overview');

 // OS Hub aggregator (live)
 let hub = $state<any>(null);
 let hubError = $state<string>('');
 let hubLastFetch = $state<Date | null>(null);

 async function loadHub() {
 try {
 const r = await fetch('/api/projects/agents/os-hub', {
 headers: { Authorization: `Bearer ${token() || ''}` },
 });
 if (r.ok) {
 hub = await r.json();
 hubError = '';
 } else {
 hubError = `HTTP ${r.status}`;
 }
 hubLastFetch = new Date();
 } catch (e) {
 hubError = String(e);
 }
 }

 $effect(() => {
 loadHub();
 const t = setInterval(loadHub, 30000);
 return () => clearInterval(t);
 });

 function fmt(v: any): string {
 if (v === null || v === undefined) return '—';
 return String(v);
 }
 function relTime(iso: string | null | undefined): string {
 if (!iso) return '—';
 try {
 const diff = Date.now() - new Date(iso).getTime();
 const s = Math.floor(diff / 1000);
 if (s < 60) return `${s}s ago`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m}m ago`;
 const h = Math.floor(m / 60);
 if (h < 24) return `${h}h ago`;
 const d = Math.floor(h / 24);
 return `${d}d ago`;
 } catch { return '—'; }
 }
 function go(url: string | undefined) {
 if (!url) return;
 if (typeof window !== 'undefined') window.location.href = url;
 }

 const SUBVIEW_ICONS: Record<string, string> = {
 skills: '',
 workflows: '',
 sub_agents: '',
 sim_lab: '',
 marketplace: '',
 wizard: '',
 mcp_servers: '',
 };
 const SUBVIEW_LABELS: Record<string, string> = {
 skills: 'SKILLS',
 workflows: 'WORKFLOWS',
 sub_agents: 'SUB-AGENTS',
 sim_lab: 'SIM LAB',
 marketplace: 'MARKET',
 wizard: 'WIZARD',
 mcp_servers: 'MCP',
 };
 const SUBVIEW_ORDER = ['skills', 'workflows', 'sub_agents', 'sim_lab', 'marketplace', 'wizard', 'mcp_servers'];

 function subviewCount(name: string, sv: any): string {
 if (!sv) return '—';
 if (name === 'skills') return fmt(sv.count_total);
 if (name === 'workflows') return fmt(sv.count_total);
 if (name === 'sub_agents') return fmt(sv.count_spawned ?? sv.count_available);
 if (name === 'sim_lab') return fmt(sv.count_total_sims);
 if (name === 'marketplace') return fmt(sv.count_total);
 if (name === 'wizard') return '';
 if (name === 'mcp_servers') return fmt(sv.count);
 return '—';
 }

 const RECENT_ROWS: Array<{ key: string; icon: string; label: string }> = [
 { key: 'last_dream_run_at', icon: '', label: 'Last dream run' },
 { key: 'last_sim_completed_at', icon: '', label: 'Last sim done' },
 { key: 'last_autosim_run_at', icon: '', label: 'Last autosim run' },
 { key: 'last_chat_at', icon: '', label: 'Last chat' },
 ];

 const token = (): string | null =>
 typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;

 async function fetchJson(url: string) {
 try {
 const r = await fetch(url, { headers: { Authorization: `Bearer ${token() || ''}` } });
 if (!r.ok) return null;
 return await r.json();
 } catch {
 return null;
 }
 }

 async function refresh() {
 loading = true;
 const [wf, sk, mcp, apr, hitl] = await Promise.all([
 fetchJson('/api/os/workflows'),
 fetchJson('/api/skills'),
 fetchJson('/api/mcp/servers'),
 fetchJson('/api/approvals/pending'),
 fetchJson('/api/hitl/pending'),
 ]);
 workflows = wf?.workflows || [];
 skills = sk?.skills || [];
 mcpServers = mcp?.servers || [];
 approvals = apr?.requests || apr?.pending || [];
 hitlPending = hitl?.pending || [];
 // Static agent list (Dash-OS additions)
 agents = [
 { name: 'Reporter', category: 'agentic', desc: 'PDF/PPTX/CSV/XLSX/DOCX/JSON/MD generation' },
 { name: 'Reasoner', category: 'agentic', desc: 'Extended-thinking deep analysis' },
 ];
 loading = false;
 }

 async function runWorkflow(id: string) {
 const r = await fetch(`/api/os/workflows/${id}/run`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
 body: JSON.stringify({ inputs: {} }),
 });
 const j = await r.json();
 alert(j.ok ? `Run started: ${j.run_id || 'pending'}` : `Failed: ${j.error || 'unknown'}`);
 refresh();
 }

 async function approveItem(id: string) {
 const reason = prompt('Optional approval note:') || '';
 await fetch(`/api/approvals/${id}/sign`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
 body: JSON.stringify({ decision: 'approve', reason }),
 });
 refresh();
 }

 async function rejectItem(id: string) {
 const reason = prompt('Reason for rejection (required):') || '';
 if (!reason) return;
 await fetch(`/api/approvals/${id}/sign`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
 body: JSON.stringify({ decision: 'reject', reason }),
 });
 refresh();
 }

 async function respondHitl(runId: string, decision: 'approve' | 'reject') {
 await fetch(`/api/hitl/${runId}/respond`, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token() || ''}` },
 body: JSON.stringify({ decision }),
 });
 refresh();
 }

 // URL persist
 const _VALID_TABS = ['overview','agents','workflows','skills','mcp','approvals','hitl'] as const;
 let _osTabInited = $state(false);
 $effect(() => {
 if (!_osTabInited) return;
 if (typeof window === 'undefined') return;
 const url = new URL(window.location.href);
 if (url.searchParams.get('tab') !== activeTab) {
 url.searchParams.set('tab', activeTab);
 window.history.replaceState({}, '', url);
 }
 });
 onMount(() => {
 if (typeof window !== 'undefined') {
 const t = new URL(window.location.href).searchParams.get('tab') as any;
 if (t && _VALID_TABS.includes(t)) activeTab = t;
 }
 _osTabInited = true;
 refresh();
 });
</script>

<div class="os-shell">
  <header class="os-hdr">
    <div>
      <h1>Dash-OS</h1>
      <p class="muted">Agentic control plane · 14 routers · 8 phases</p>
    </div>
    <button class="ghost" onclick={refresh}>↻ refresh</button>
  </header>

  <nav class="pill-tabs">
    {#each ['overview','agents','workflows','skills','mcp','approvals','hitl'] as t}
      <button class="pill-tab" class:active={activeTab === t} onclick={() => (activeTab = t as any)}>
        {t === 'agents' ? 'sub-agents' : t}
        {#if t === 'workflows' && workflows.length}<span class="pill-count">{workflows.length}</span>{/if}
        {#if t === 'skills' && skills.length}<span class="pill-count">{skills.length}</span>{/if}
        {#if t === 'mcp' && mcpServers.length}<span class="pill-count">{mcpServers.length}</span>{/if}
        {#if t === 'approvals' && approvals.length}<span class="pill-count danger">{approvals.length}</span>{/if}
        {#if t === 'hitl' && hitlPending.length}<span class="pill-count danger">{hitlPending.length}</span>{/if}
      </button>
    {/each}
  </nav>

  {#if loading}
    <div class="loading">loading…</div>
  {:else if activeTab === 'overview'}
    {#if hubError && !hub}
      <div class="os-hub-error">
        OS Hub aggregator unreachable ({hubError}). <button class="ghost sm" onclick={loadHub}>↻ retry</button>
      </div>
    {/if}

    <!-- Row 1: Quick metrics -->
    <section class="os-hub-row5">
      <button class="os-hub-card os-hub-metric" onclick={() => go('/ui/command-center?tab=fleet')}>
        <div class="os-hub-label">TOTAL AGENTS</div>
        <div class="os-hub-big">{fmt(hub?.header?.agents_total)}</div>
        <div class="os-hub-sub">{fmt(hub?.header?.agents_active)} active · → Fleet</div>
      </button>
      <button class="os-hub-card os-hub-metric" onclick={() => go('/ui/command-center?tab=fleet&filter=sub_agents')}>
        <div class="os-hub-label">SUB-AGENTS</div>
        <div class="os-hub-big">{fmt(hub?.header?.sub_agents_online)}</div>
        <div class="os-hub-sub">spawned · → Sub-Agents</div>
      </button>
      <button class="os-hub-card os-hub-metric" onclick={() => go('/ui/command-center?tab=minions')}>
        <div class="os-hub-label">MINIONS RUNNING</div>
        <div class="os-hub-big">{fmt(hub?.header?.minions_running)}</div>
        <div class="os-hub-sub">{fmt(hub?.header?.minions_queued)} queued · → Minions log</div>
      </button>
      <button class="os-hub-card os-hub-metric" onclick={() => go('/ui/command-center?tab=cost')}>
        <div class="os-hub-label">EST COST / DAY</div>
        <div class="os-hub-big">{hub?.header?.estimated_daily_cost_usd != null ? `$${Number(hub.header.estimated_daily_cost_usd).toFixed(2)}` : '—'}</div>
        <div class="os-hub-sub">→ Cost dashboard</div>
      </button>
      <button class="os-hub-card os-hub-metric" onclick={() => go('/ui/admin/agent-os?tab=schedules')}>
        <div class="os-hub-label">CRON SCHEDULES</div>
        <div class="os-hub-big">{fmt((hub?.cron_schedules || []).length)}</div>
        <div class="os-hub-sub">→ Schedules tab</div>
      </button>
    </section>

    <!-- Row 2: Sub-views (7 tiles) -->
    <h2 class="os-hub-h2">SUB-VIEWS</h2>
    <section class="os-hub-row7">
      {#each SUBVIEW_ORDER as svKey}
        {@const sv = hub?.subviews?.[svKey]}
        <button class="os-hub-card os-hub-tile" onclick={() => go(sv?.drill_url)} disabled={!sv?.drill_url}>
          <div class="os-hub-tile-icon">{SUBVIEW_ICONS[svKey]}</div>
          <div class="os-hub-tile-label">{SUBVIEW_LABELS[svKey]}</div>
          <div class="os-hub-tile-count">{subviewCount(svKey, sv)}</div>
        </button>
      {/each}
    </section>

    <!-- Row 3: Category breakdown -->
    <h2 class="os-hub-h2">CATEGORIES</h2>
    <section class="os-hub-row12">
      {#each Object.entries(hub?.categories || {}) as [catName, cat]}
        <button class="os-hub-card os-hub-cat" onclick={() => go((cat as any)?.drill_url)} disabled={!(cat as any)?.drill_url}>
          <div class="os-hub-cat-name">{catName}</div>
          <div class="os-hub-cat-num">{fmt((cat as any)?.count)}</div>
          <div class="os-hub-cat-sub">{fmt((cat as any)?.active)} active</div>
        </button>
      {/each}
      {#if !hub || Object.keys(hub?.categories || {}).length === 0}
        <div class="empty" style="grid-column:1/-1;">No categories loaded.</div>
      {/if}
    </section>

    <!-- Row 4: Recent activity -->
    <h2 class="os-hub-h2">RECENT ACTIVITY</h2>
    <section class="os-hub-recent">
      {#each RECENT_ROWS as row}
        {@const v = hub?.recent_activity?.[row.key]}
        <div class="os-hub-recent-row">
          <span class="os-hub-recent-icon">{row.icon}</span>
          <span class="os-hub-recent-label">{row.label}</span>
          <span class="os-hub-recent-time">{relTime(v)}</span>
          <button class="ghost sm" disabled={!v} onclick={() => go('/ui/command-center?tab=runs')}>view</button>
        </div>
      {/each}
    </section>

    <!-- Row 5: Cron schedules -->
    <h2 class="os-hub-h2">CRON SCHEDULES</h2>
    <table class="tbl">
      <thead><tr><th>ID</th><th>Name</th><th>Cron</th><th>Next Run</th><th>Last Run</th></tr></thead>
      <tbody>
        {#each (hub?.cron_schedules || []) as s}
          <tr>
            <td class="mono">{s.id}</td>
            <td><strong>{s.name}</strong></td>
            <td class="mono">{s.cron_expr || '—'}</td>
            <td class="muted">{relTime(s.next_run_at)}</td>
            <td class="muted">{relTime(s.last_run_at)}</td>
          </tr>
        {/each}
        {#if !(hub?.cron_schedules || []).length}
          <tr><td colspan="5" class="empty">No cron schedules registered.</td></tr>
        {/if}
      </tbody>
    </table>

    {#if hubLastFetch}
      <p class="muted" style="margin-top:14px;text-align:right;">Updated {relTime(hubLastFetch.toISOString())} · auto-refresh 30s</p>
    {/if}
  {:else if activeTab === 'agents'}
    <table class="tbl">
      <thead><tr><th>Name</th><th>Category</th><th>Description</th></tr></thead>
      <tbody>
        {#each agents as a}
          <tr>
            <td><strong>{a.name}</strong></td>
            <td><span class="chip">{a.category}</span></td>
            <td class="muted">{a.desc}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else if activeTab === 'workflows'}
    <table class="tbl">
      <thead><tr><th>Name</th><th>Category</th><th>Trigger</th><th>Type</th><th>Action</th></tr></thead>
      <tbody>
        {#each workflows as wf}
          <tr>
            <td><a href="{base}/workflows#{wf.id}"><strong>{wf.name}</strong></a></td>
            <td><span class="chip">{wf.category || '—'}</span></td>
            <td class="muted">{wf.trigger_kind}{wf.cron_expr ? ` · ${wf.cron_expr}` : ''}</td>
            <td>{wf.is_builtin ? ' builtin' : 'custom'}</td>
            <td><button class="primary sm" onclick={() => runWorkflow(wf.id)}>▶ run</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else if activeTab === 'skills'}
    <table class="tbl">
      <thead><tr><th>Name</th><th>Category</th><th>Triggers</th><th>Invocations (30d)</th></tr></thead>
      <tbody>
        {#each skills as s}
          <tr>
            <td><strong>{s.name}</strong></td>
            <td><span class="chip">{s.category}</span></td>
            <td class="muted">{(s.trigger_keywords || []).slice(0, 3).join(', ')}</td>
            <td>{s.invocations_30d || 0}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else if activeTab === 'mcp'}
    <table class="tbl">
      <thead><tr><th>Name</th><th>Transport</th><th>Status</th><th>Tools</th></tr></thead>
      <tbody>
        {#each mcpServers as m}
          <tr>
            <td><strong>{m.name}</strong></td>
            <td><span class="chip">{m.transport}</span></td>
            <td><span class="dot {m.status}"></span> {m.status}</td>
            <td>{m.tool_count || 0}</td>
          </tr>
        {/each}
        {#if !mcpServers.length}
          <tr><td colspan="4" class="empty">No MCP servers configured. POST /api/mcp/servers to add.</td></tr>
        {/if}
      </tbody>
    </table>
  {:else if activeTab === 'approvals'}
    <table class="tbl">
      <thead><tr><th>Action</th><th>Resource</th><th>Signatures</th><th>Actions</th></tr></thead>
      <tbody>
        {#each approvals as a}
          <tr>
            <td><span class="chip danger">{a.action_type}</span></td>
            <td class="muted">{a.resource_id || '—'}</td>
            <td>{a.signature_count || 0}/{a.required_approvers || 1}</td>
            <td>
              <button class="primary sm" onclick={() => approveItem(a.id)}><Icon name="check" size={14} /> approve</button>
              <button class="ghost sm" onclick={() => rejectItem(a.id)}><Icon name="x" size={14} /> reject</button>
            </td>
          </tr>
        {/each}
        {#if !approvals.length}<tr><td colspan="4" class="empty">No pending approvals.</td></tr>{/if}
      </tbody>
    </table>
  {:else if activeTab === 'hitl'}
    <table class="tbl">
      <thead><tr><th>Agent</th><th>Action</th><th>Run</th><th>Expires</th><th>Actions</th></tr></thead>
      <tbody>
        {#each hitlPending as h}
          <tr>
            <td><strong>{h.agent_name}</strong></td>
            <td><span class="chip">{h.action_type}</span></td>
            <td class="muted mono">{h.run_id?.slice(0, 12)}…</td>
            <td class="muted">{h.expires_at}</td>
            <td>
              <button class="primary sm" onclick={() => respondHitl(h.run_id, 'approve')}><Icon name="check" size={14} /></button>
              <button class="ghost sm" onclick={() => respondHitl(h.run_id, 'reject')}><Icon name="x" size={14} /></button>
            </td>
          </tr>
        {/each}
        {#if !hitlPending.length}<tr><td colspan="5" class="empty">No HITL gates waiting.</td></tr>{/if}
      </tbody>
    </table>
  {/if}
</div>

<style>
 .os-shell {
 padding: 24px 32px 60px;
 max-width: 1280px;
 margin: 0 auto;
 font: 14px/1.5 Inter, system-ui, sans-serif;
 color: var(--pw-ink, #2c2a26);
 }
 .os-hdr {
 display: flex;
 justify-content: space-between;
 align-items: flex-end;
 margin-bottom: 24px;
 }
 h1 {
 font: 600 32px/1 'Source Serif 4', Georgia, serif;
 margin: 0;
 color: var(--pw-accent, #c96342);
 }
 .muted {
 color: var(--pw-ink-soft, #87837a);
 font-size: 11px;
 margin: 4px 0 0;
 }
 .pill-tabs {
 display: inline-flex;
 gap: 6px;
 background: var(--pw-bg-alt, #f1ede4);
 padding: 4px;
 border-radius: 0;
 border: 1px solid var(--pw-border, #e7e3da);
 margin-bottom: 20px;
 flex-wrap: wrap;
 }
 .pill-tab {
 background: none;
 border: 1px solid transparent;
 border-radius: 0;
 padding: 8px 22px;
 cursor: pointer;
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-ink-soft, #87837a);
 transition: all 0.15s;
 display: inline-flex;
 align-items: center;
 gap: 6px;
 }
 .pill-tab:hover {
 background: rgba(255, 255, 255, 0.5);
 color: var(--pw-ink, #2c2a26);
 }
 .pill-tab.active {
 background: var(--pw-ink, #2c2a26);
 color: #fff;
 border-color: var(--pw-ink, #2c2a26);
 }
 .pill-count {
 background: rgba(255, 255, 255, 0.2);
 color: inherit;
 border-radius: 0;
 padding: 1px 7px;
 font-size: 10px;
 font-weight: 700;
 }
 .pill-tab:not(.active) .pill-count {
 background: var(--pw-surface, #faf9f5);
 color: var(--pw-ink-soft, #87837a);
 }
 .pill-count.danger {
 background: var(--pw-accent, #c96342);
 color: #fff;
 }
 .pill-tab.active .pill-count.danger {
 background: #fff;
 color: var(--pw-accent, #c96342);
 }
 .grid {
 display: grid;
 grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
 gap: 14px;
 }
 .card {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 18px;
 }
 .card.danger { border-color: var(--pw-accent, #c96342); }
 .card.hidden { display: none; }
 .card h3 { font: 600 13px Inter; margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.04em; display: flex; align-items: center; gap: 8px; }
 .card h3 svg { opacity: 0.75; }
 .big { font: 600 32px/1 'Source Serif 4', Georgia, serif; color: var(--pw-accent, #c96342); }
 .tbl {
 width: 100%;
 border-collapse: collapse;
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 overflow: hidden;
 font-size: 11px;
 }
 .tbl th, .tbl td { padding: 10px 14px; text-align: left; }
 .tbl th { background: var(--pw-bg-alt, #f1ede4); font: 600 11px Inter; text-transform: uppercase; letter-spacing: 0.05em; }
 .tbl tbody tr { border-top: 1px solid var(--pw-border, #e7e3da); }
 .tbl tbody tr:hover { background: rgba(201, 99, 66, 0.04); }
 .chip {
 display: inline-block;
 background: var(--pw-bg-alt, #f1ede4);
 border-radius: 0;
 padding: 2px 8px;
 font: 600 10px Inter;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 }
 .chip.danger { background: rgba(201, 99, 66, 0.14); color: var(--pw-accent, #c96342); }
 .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #888; margin-right: 4px; }
 .dot.connected { background: #10b981; }
 .dot.failed { background: #ef4444; }
 .empty { text-align: center; color: var(--pw-ink-soft, #87837a); padding: 30px; }
 .mono { font-family: 'JetBrains Mono', monospace; }
 button.primary {
 background: var(--pw-accent, #c96342);
 color: white;
 border: none;
 border-radius: 0;
 padding: 6px 12px;
 cursor: pointer;
 font: 600 12px Inter;
 }
 button.ghost {
 background: var(--pw-bg-alt, #f1ede4);
 color: var(--pw-ink, #2c2a26);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 6px 12px;
 cursor: pointer;
 font: 600 12px Inter;
 }
 button.sm { padding: 4px 10px; font-size: 11px; }
 .loading { text-align: center; padding: 60px; color: var(--pw-ink-soft, #87837a); }

 /* OS Hub overview */
 .os-hub-error {
 background: rgba(201, 99, 66, 0.08);
 border: 1px solid var(--pw-accent, #c96342);
 color: var(--pw-accent, #c96342);
 padding: 10px 14px;
 border-radius: 0;
 margin-bottom: 14px;
 font-size: 11px;
 }
 .os-hub-h2 {
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.08em;
 color: var(--pw-ink-soft, #87837a);
 margin: 24px 0 10px;
 }
 .os-hub-row5 {
 display: grid;
 grid-template-columns: repeat(5, 1fr);
 gap: 12px;
 }
 .os-hub-row7 {
 display: grid;
 grid-template-columns: repeat(7, 1fr);
 gap: 12px;
 }
 .os-hub-row12 {
 display: grid;
 grid-template-columns: repeat(6, 1fr);
 gap: 10px;
 }
 @media (max-width: 1100px) {
 .os-hub-row5 { grid-template-columns: repeat(3, 1fr); }
 .os-hub-row7 { grid-template-columns: repeat(4, 1fr); }
 .os-hub-row12 { grid-template-columns: repeat(4, 1fr); }
 }
 @media (max-width: 640px) {
 .os-hub-row5,
 .os-hub-row7,
 .os-hub-row12 { grid-template-columns: repeat(2, 1fr); }
 }
 .os-hub-card {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 padding: 14px 16px;
 text-align: left;
 cursor: pointer;
 transition: transform 0.12s, border-color 0.12s, box-shadow 0.12s;
 font: inherit;
 color: inherit;
 display: flex;
 flex-direction: column;
 gap: 4px;
 }
 .os-hub-card:hover:not(:disabled) {
 transform: translateY(-1px);
 border-color: var(--pw-accent, #c96342);
 box-shadow: 0 4px 14px rgba(201, 99, 66, 0.08);
 }
 .os-hub-card:disabled {
 cursor: default;
 opacity: 0.6;
 }
 .os-hub-label {
 font: 600 10px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-ink-soft, #87837a);
 }
 .os-hub-big {
 font: 600 30px/1 'Source Serif 4', Georgia, serif;
 color: var(--pw-accent, #c96342);
 }
 .os-hub-sub {
 font-size: 11px;
 color: var(--pw-ink-soft, #87837a);
 }
 .os-hub-tile {
 align-items: center;
 text-align: center;
 padding: 16px 8px;
 }
 .os-hub-tile-icon { font-size: 16px; line-height: 1; }
 .os-hub-tile-label {
 font: 700 10px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.06em;
 color: var(--pw-ink, #2c2a26);
 }
 .os-hub-tile-count {
 font: 600 22px 'Source Serif 4', Georgia, serif;
 color: var(--pw-accent, #c96342);
 }
 .os-hub-cat { padding: 12px 14px; }
 .os-hub-cat-name {
 font: 600 11px Inter, system-ui, sans-serif;
 text-transform: uppercase;
 letter-spacing: 0.04em;
 color: var(--pw-ink, #2c2a26);
 }
 .os-hub-cat-num {
 font: 600 22px 'Source Serif 4', Georgia, serif;
 color: var(--pw-accent, #c96342);
 }
 .os-hub-cat-sub {
 font-size: 10.5px;
 color: var(--pw-ink-soft, #87837a);
 }
 .os-hub-recent {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: 0;
 overflow: hidden;
 }
 .os-hub-recent-row {
 display: grid;
 grid-template-columns: 32px 1fr auto auto;
 gap: 12px;
 align-items: center;
 padding: 10px 14px;
 border-top: 1px solid var(--pw-border, #e7e3da);
 font-size: 11px;
 }
 .os-hub-recent-row:first-child { border-top: none; }
 .os-hub-recent-icon { font-size: 11px; }
 .os-hub-recent-label { color: var(--pw-ink, #2c2a26); }
 .os-hub-recent-time { color: var(--pw-ink-soft, #87837a); font-size: 11px; }
</style>
