<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import {
 listAgentOsWorkflows,
 setWorkflowCron,
 runWorkflowNow,
 pauseWorkflow,
 resumeWorkflow,
 getWorkflowHistory,
 type AgentOsWorkflow,
 type AgentOsWorkflowGroup,
 type AgentOsWorkflowRun,
 } from '$lib/api';

 // ── State ────────────────────────────────────────────────────
 let groups = $state<AgentOsWorkflowGroup[]>([]);
 let totals = $state({ total: 0, owned: 0, shared: 0, active: 0, paused: 0, failed: 0 });
 let loading = $state(true);
 let errorMsg = $state<string | null>(null);

 // Toolbar filters
 let scopeFilter = $state<'all' | 'owned' | 'shared'>('all');
 let statusFilter = $state<'all' | 'active' | 'paused' | 'failed'>('all');
 let agentFilter = $state<string>('all');
 let searchQuery = $state('');

 // UI: collapsed groups + expanded workflow rows
 let collapsedGroups = $state<Record<string, boolean>>({});
 let expandedWorkflows = $state<Record<number, boolean>>({});

 // Left rail: active selection. 'all' | project_slug | 'favorites' | 'scheduled' | 'paused' | 'failed'
 let activeRail = $state<string>('all');

 // Derived rail counts + filtered groups
 const railProjects = $derived.by(() => {
   const map = new Map<string, { slug: string; name: string; count: number }>();
   for (const g of (Array.isArray(groups) ? groups : [])) {
     const wfs = Array.isArray(g.workflows) ? g.workflows : [];
     map.set(g.agent_slug, { slug: g.agent_slug, name: g.agent_name, count: wfs.length });
   }
   return Array.from(map.values()).sort((a, b) => b.count - a.count);
 });
 // Field map confirmed via api.ts AgentOsWorkflow interface:
 //   wf.cron               → schedule string (presence = scheduled)
 //   wf.status             → 'live' | 'ready' | 'paused' | 'failed'
 //   wf.last_status        → 'ok' | 'fail'
 const _isScheduled = (wf: any) => !!(wf?.cron || wf?.schedule_cron || wf?.cron_label);
 const _isPaused    = (wf: any) => (wf?.status || '').toLowerCase() === 'paused';
 const _isFailed    = (wf: any) => {
   const s = (wf?.status || '').toLowerCase();
   const ls = (wf?.last_status || '').toLowerCase();
   return s === 'failed' || s === 'fail' || ls === 'fail' || ls === 'error';
 };

 const railCounts = $derived.by(() => {
   let scheduled = 0, paused = 0, failed = 0, total = 0;
   for (const g of (Array.isArray(groups) ? groups : [])) {
     for (const wf of (Array.isArray(g.workflows) ? g.workflows : [])) {
       total++;
       if (_isScheduled(wf)) scheduled++;
       if (_isPaused(wf)) paused++;
       if (_isFailed(wf)) failed++;
     }
   }
   return { total, scheduled, paused, failed };
 });
 const filteredGroups = $derived.by(() => {
   const all = Array.isArray(groups) ? groups : [];
   if (activeRail === 'all') return all;
   if (activeRail === 'scheduled' || activeRail === 'paused' || activeRail === 'failed') {
     return all.map(g => ({
       ...g,
       workflows: (Array.isArray(g.workflows) ? g.workflows : []).filter(wf => {
         if (activeRail === 'scheduled') return _isScheduled(wf);
         if (activeRail === 'paused')    return _isPaused(wf);
         if (activeRail === 'failed')    return _isFailed(wf);
         return true;
       }),
     })).filter(g => g.workflows.length > 0);
   }
   return all.filter(g => g.agent_slug === activeRail);
 });

 // Cron modal
 let cronModalWf = $state<AgentOsWorkflow | null>(null);
 let cronPreset = $state<'daily' | 'weekly' | 'monthly' | 'hourly' | 'custom'>('daily');
 let cronHH = $state('02');
 let cronMM = $state('00');
 let cronDOW = $state('1');
 let cronDay = $state('1');
 let cronEveryN = $state('6');
 let cronCustom = $state('');
 let cronCostCap = $state('1.00');
 let cronActions = $state<{ post_insight: boolean; alert: boolean; suggest: boolean }>({ post_insight: true, alert: false, suggest: false });
 let cronEnabled = $state(true);
 let cronSaving = $state(false);

 // History drawer
 let historyWf = $state<AgentOsWorkflow | null>(null);
 let historyRuns = $state<AgentOsWorkflowRun[]>([]);
 let historyLoading = $state(false);
 let expandedRun = $state<string | null>(null);
 let runDetailTab = $state<'data' | 'sql' | 'transcript'>('data');

 // Live tail (SSE)
 interface LiveEvent { ts: string; icon: string; agent: string; workflow: string; status: string; duration: string; }
 let liveEvents = $state<LiveEvent[]>([]);
 let liveEs: EventSource | null = null;

 // Filter chip counts (derived)
 const allAgents = $derived.by(() => {
 const seen = new Set<string>();
 const list: { slug: string; name: string }[] = [];
 for (const g of (Array.isArray(groups) ? groups : [])) {
 if (g.agent_slug && !seen.has(g.agent_slug)) {
 seen.add(g.agent_slug);
 list.push({ slug: g.agent_slug, name: g.agent_name });
 }
 }
 return list;
 });

 // ── Load ─────────────────────────────────────────────────────
 let reloadTimer: ReturnType<typeof setTimeout> | null = null;
 async function reload() {
 loading = true;
 errorMsg = null;
 try {
 const r = await listAgentOsWorkflows({
 status: statusFilter,
 agent_slug: agentFilter,
 search: searchQuery,
 scope: scopeFilter,
 });
 // Backend returns flat {workflows, stats}. Group client-side by project_slug.
 if (Array.isArray((r as any).groups)) {
 groups = (r as any).groups;
 } else if (Array.isArray((r as any).workflows)) {
 const byAgent: Record<string, any> = {};
 for (const wf of (r as any).workflows) {
 const key = wf.project_slug || 'unknown';
 if (!byAgent[key]) {
 byAgent[key] = {
 agent_slug: key,
 agent_name: wf.agent_name || wf.project_name || key,
 ownership: wf.ownership || 'owned',
 share_role: wf.share_role || 'admin',
 workflows: [],
 };
 }
 byAgent[key].workflows.push(wf);
 }
 groups = Object.values(byAgent);
 } else {
 groups = [];
 }
 totals = (r as any).stats || (r as any).totals || totals;
 } catch (e) {
 errorMsg = e instanceof Error ? e.message : 'Failed to load workflows';
 groups = [];
 } finally {
 loading = false;
 }
 }
 function debouncedReload() {
 if (reloadTimer) clearTimeout(reloadTimer);
 reloadTimer = setTimeout(reload, 200);
 }

 // ── Live tail ────────────────────────────────────────────────
 function connectLiveTail() {
 if (typeof window === 'undefined') return;
 try {
 const tok = localStorage.getItem('dash_token') || '';
 const url = `/api/agent-os/workflows/live-tail?token=${encodeURIComponent(tok)}`;
 liveEs = new EventSource(url);
 liveEs.onmessage = (msg) => {
 try {
 const d = JSON.parse(msg.data);
 const ts = (d.ts || new Date().toISOString()).slice(11, 19);
 const status = String(d.status || '').toLowerCase();
 const icon = status === 'ok' || status === 'done' ? '' : status === 'fail' || status === 'error' ? '' : '◐';
 const next: LiveEvent = {
 ts,
 icon,
 agent: d.agent_name || d.agent || 'agent',
 workflow: d.workflow_name || d.workflow || '—',
 status: status || 'running',
 duration: d.duration_s ? `${d.duration_s}s` : (d.duration || ''),
 };
 liveEvents = [...liveEvents, next].slice(-10);
 } catch {
 // skip
 }
 };
 liveEs.onerror = () => { /* keep open, browser auto-retries */ };
 } catch {
 // ignore — page works without live tail
 }
 }

 onMount(() => {
 reload();
 connectLiveTail();
 });
 onDestroy(() => {
 if (liveEs) { try { liveEs.close(); } catch {} liveEs = null; }
 if (reloadTimer) clearTimeout(reloadTimer);
 });

 // ── Cron helpers ─────────────────────────────────────────────
 function presetToCron(): string {
 if (cronPreset === 'daily') return `${parseInt(cronMM || '0', 10)} ${parseInt(cronHH || '0', 10)} * * *`;
 if (cronPreset === 'weekly') return `${parseInt(cronMM || '0', 10)} ${parseInt(cronHH || '0', 10)} * * ${cronDOW}`;
 if (cronPreset === 'monthly') return `${parseInt(cronMM || '0', 10)} ${parseInt(cronHH || '0', 10)} ${cronDay} * *`;
 if (cronPreset === 'hourly') {
 const n = Math.max(1, Math.min(23, parseInt(cronEveryN || '1', 10) || 1));
 return `0 */${n} * * *`;
 }
 return cronCustom.trim();
 }
 function validateCron(expr: string): boolean {
 const parts = expr.trim().split(/\s+/);
 if (parts.length !== 5) return false;
 // Lightweight validator: each field is digits, *, /, -, or , combos.
 return parts.every((p) => /^[0-9*,\-\/]+$/.test(p));
 }
 function openCronModal(wf: AgentOsWorkflow) {
 cronModalWf = wf;
 cronCustom = wf.cron || '';
 cronPreset = 'custom';
 cronCostCap = wf.cost_cap_usd != null ? String(wf.cost_cap_usd) : '1.00';
 const acts = Array.isArray(wf.actions) ? wf.actions : ['post_insight'];
 cronActions = {
 post_insight: acts.includes('post_insight'),
 alert: acts.includes('alert'),
 suggest: acts.includes('suggest'),
 };
 cronEnabled = (wf.status || '').toLowerCase() !== 'paused';
 }
 function closeCronModal() {
 cronModalWf = null;
 cronSaving = false;
 }
 async function saveCron() {
 if (!cronModalWf) return;
 const cron = presetToCron();
 if (!validateCron(cron)) {
 errorMsg = `Invalid cron expression: "${cron}". Expected 5 fields.`;
 return;
 }
 cronSaving = true;
 try {
 const actions: string[] = [];
 if (cronActions.post_insight) actions.push('post_insight');
 if (cronActions.alert) actions.push('alert');
 if (cronActions.suggest) actions.push('suggest');
 await setWorkflowCron(cronModalWf.id, {
 cron,
 enabled: cronEnabled,
 cost_cap_usd: parseFloat(cronCostCap) || 0,
 actions,
 });
 closeCronModal();
 await reload();
 } catch (e) {
 errorMsg = e instanceof Error ? e.message : 'Failed to save cron';
 } finally {
 cronSaving = false;
 }
 }

 // ── History drawer ───────────────────────────────────────────
 async function openHistory(wf: AgentOsWorkflow) {
 historyWf = wf;
 historyRuns = [];
 historyLoading = true;
 expandedRun = null;
 try {
 const runs = await getWorkflowHistory(wf.id, 20);
 historyRuns = Array.isArray(runs) ? runs : [];
 } finally {
 historyLoading = false;
 }
 }
 function closeHistory() {
 historyWf = null;
 historyRuns = [];
 expandedRun = null;
 }

 // ── Actions ──────────────────────────────────────────────────
 async function doRun(wf: AgentOsWorkflow) {
   try {
     const res = await runWorkflowNow(wf.id);
     const runId = (res as any)?.run_id;
     if (!runId) { errorMsg = 'run failed: no run_id'; return; }
     const slug = (wf as any).project_slug || '';
     if (!slug) { errorMsg = 'no project slug'; return; }
     await goto(`${base}/project/${slug}/agent-os/run/${runId}`);
   } catch (e) { errorMsg = String(e); }
 }
 async function doToggle(wf: AgentOsWorkflow) {
 const isPaused = (wf.status || '').toLowerCase() === 'paused';
 try {
 if (isPaused) await resumeWorkflow(wf.id);
 else await pauseWorkflow(wf.id);
 await reload();
 } catch (e) {
 errorMsg = e instanceof Error ? e.message : 'Toggle failed';
 }
 }
 function openInAgent(wf: AgentOsWorkflow) {
 if (!wf.project_slug) return;
 goto(`${base}/project/${wf.project_slug}/settings#workflows`);
 }

 // ── Formatting helpers ───────────────────────────────────────
 function statusPill(s?: string) {
 const v = (s || '').toLowerCase();
 if (v === 'live' || v === 'active' || v === 'running') return { dot: '●', label: 'LIVE', color: '#16a34a', bg: 'rgba(22,163,74,0.10)' };
 if (v === 'paused') return { dot: '', label: 'PAUSED', color: '#6b7280', bg: 'rgba(107,114,128,0.10)' };
 if (v === 'fail' || v === 'failed' || v === 'error') return { dot: '', label: 'FAIL', color: '#dc2626', bg: 'rgba(220,38,38,0.10)' };
 return { dot: '◐', label: 'READY', color: '#2563eb', bg: 'rgba(37,99,235,0.10)' };
 }
 function relTime(iso?: string): string {
 if (!iso) return '—';
 const t = Date.parse(iso);
 if (Number.isNaN(t)) return iso;
 const diff = Math.max(0, Date.now() - t);
 const s = Math.floor(diff / 1000);
 if (s < 60) return `${s}s ago`;
 const m = Math.floor(s / 60);
 if (m < 60) return `${m}m ago`;
 const h = Math.floor(m / 60);
 if (h < 24) return `${h}h ago`;
 return `${Math.floor(h / 24)}d ago`;
 }
 function relFuture(iso?: string): string {
 if (!iso) return '—';
 const t = Date.parse(iso);
 if (Number.isNaN(t)) return iso;
 const diff = t - Date.now();
 if (diff <= 0) return 'due';
 const s = Math.floor(diff / 1000);
 if (s < 60) return `in ${s}s`;
 const m = Math.floor(s / 60);
 if (m < 60) return `in ${m}m`;
 const h = Math.floor(m / 60);
 if (h < 24) return `in ${h}h`;
 return `in ${Math.floor(h / 24)}d`;
 }

 function isViewer(wf: AgentOsWorkflow): boolean {
 return (wf.role || (wf as any).share_role || '').toLowerCase() === 'viewer';
 }

 function formatCron(wf: any): string {
 const raw = wf.schedule_cron || wf.cron || wf.cron_label;
 if (!raw) return 'none (manual)';
 const parts = String(raw).trim().split(/\s+/);
 if (parts.length !== 5) return `custom ${raw}`;
 const [mm, hh, dom, mon, dow] = parts;
 const t = (h: string, m: string) => `${h.padStart(2,'0')}:${m.padStart(2,'0')}`;
 const dows = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
 if (mm !== '*' && hh !== '*' && dom === '*' && mon === '*' && dow === '*') return `daily ${t(hh,mm)} UTC`;
 if (mm !== '*' && hh !== '*' && dow !== '*' && dom === '*' && mon === '*') {
 const d = Number(dow);
 return `weekly ${isFinite(d) ? dows[d % 7] : dow} ${t(hh,mm)}`;
 }
 if (mm !== '*' && hh !== '*' && dom !== '*' && mon === '*' && dow === '*') return `monthly day ${dom} ${t(hh,mm)}`;
 if (hh.startsWith('*/')) return `hourly /${hh.slice(2)}h`;
 return `custom ${raw}`;
 }

 function formatLast(wf: any): { glyph: string; cls: string; text: string } {
 const st = (wf.last_status || '').toLowerCase();
 const ms = wf.last_duration_ms ?? (wf.last_duration_s != null ? wf.last_duration_s * 1000 : null);
 const dur = ms == null ? '—' : (ms < 1000 ? `${ms}ms` : `${(ms/1000).toFixed(1)}s`);
 if (!wf.last_run_at) return { glyph: '—', cls: 'muted', text: 'never run' };
 if (st === 'ok' || st === 'done' || st === 'success') return { glyph: '', cls: 'ok', text: `${dur} · ${relTime(wf.last_run_at)}` };
 if (st === 'fail' || st === 'error' || st === 'timeout') return { glyph: '', cls: 'err', text: `${dur} · ${relTime(wf.last_run_at)}` };
 return { glyph: '◐', cls: 'muted', text: `${dur} · ${relTime(wf.last_run_at)}` };
 }

 function actionLabel(wf: any): string {
 const a = (wf.schedule_action || wf.action || '').toLowerCase();
 if (a === 'post_insight') return ' → chat';
 if (a === 'email') return ' email';
 if (a === 'webhook') return '↗ webhook';
 if (a === 'alert') return '! alert';
 return '— no action';
 }

 function groupSummary(g: any): string {
 const wfs = Array.isArray(g.workflows) ? g.workflows : [];
 const cron = wfs.filter((w: any) => w.schedule_cron).length;
 const manual = wfs.length - cron;
 const failed = wfs.filter((w: any) => (w.last_status || '').toLowerCase() === 'fail').length;
 const bits: string[] = [];
 if (cron) bits.push(`${cron} cron`);
 if (manual) bits.push(`${manual} manual`);
 if (failed) bits.push(`${failed} failed`);
 return bits.join(' · ');
 }

 // ESC handler for modal/drawer
 function onKeydown(e: KeyboardEvent) {
 if (e.key === 'Escape') {
 if (cronModalWf) closeCronModal();
 else if (historyWf) closeHistory();
 }
 }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="wf-shell">

  <!-- Left rail -->
  <aside class="wf-rail">
    <div class="wf-rail-group">
      <div class="wf-rail-label">Library</div>
      <button class:active={activeRail === 'all'} onclick={() => activeRail = 'all'}>
        <span>All workflows</span><span class="wf-rail-count">{railCounts.total}</span>
      </button>
      <button class:active={activeRail === 'scheduled'} onclick={() => activeRail = 'scheduled'}>
        <span>Scheduled</span><span class="wf-rail-count">{railCounts.scheduled}</span>
      </button>
      <button class:active={activeRail === 'paused'} onclick={() => activeRail = 'paused'}>
        <span>Paused</span><span class="wf-rail-count">{railCounts.paused}</span>
      </button>
      <button class:active={activeRail === 'failed'} onclick={() => activeRail = 'failed'}>
        <span>Failed</span><span class="wf-rail-count">{railCounts.failed}</span>
      </button>
    </div>

    {#if railProjects.length > 0}
      <div class="wf-rail-group">
        <div class="wf-rail-label">Projects</div>
        {#each railProjects as p (p.slug)}
          <button class:active={activeRail === p.slug} onclick={() => activeRail = p.slug} title={p.name}>
            <span class="wf-rail-name">{p.name}</span>
            <span class="wf-rail-count">{p.count}</span>
          </button>
        {/each}
      </div>
    {/if}
  </aside>

  <!-- Right main pane -->
  <main class="wf-main">

  <!-- Header -->
  <div class="ds-page-head wf-head">
    <div>
      <h1 class="ds-page-title">Workflows</h1>
      <p class="ds-page-sub">
        {railCounts.total} workflows · <span class="ok">{totals.active} active</span> · <span class="muted">{totals.paused} paused</span>{#if totals.failed > 0} · <span class="err">{totals.failed} failed</span>{/if}
      </p>
    </div>

    <div class="wf-controls">
      <div class="wf-search">
        <span class="wf-search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input class="ds-input wf-search-input" type="text" placeholder="Search workflows…" bind:value={searchQuery} oninput={debouncedReload} />
      </div>

      <div class="wf-filter-pills">
        <button class="pill-segment" class:active={statusFilter === 'all'} onclick={() => { statusFilter = 'all'; debouncedReload(); }}>All</button>
        <button class="pill-segment" class:active={statusFilter === 'active'} onclick={() => { statusFilter = 'active'; debouncedReload(); }}>Active</button>
        <button class="pill-segment" class:active={statusFilter === 'paused'} onclick={() => { statusFilter = 'paused'; debouncedReload(); }}>Paused</button>
        <button class="pill-segment" class:active={statusFilter === 'failed'} onclick={() => { statusFilter = 'failed'; debouncedReload(); }}>Failed</button>
      </div>

      <select class="picker" bind:value={scopeFilter} onchange={debouncedReload} title="Scope">
        <option value="all">All scopes</option>
        <option value="owned">Owned</option>
        <option value="shared">Shared</option>
      </select>
    </div>
  </div>

  {#if errorMsg}
    <div class="banner-err"><Icon name="alert-triangle" size={14} /> {errorMsg} <button class="btn-dismiss" onclick={() => errorMsg = null}><Icon name="x" size={14} /></button></div>
  {/if}

  <!-- Groups -->
  {#if loading}
    <div class="loading">LOADING…</div>
  {:else if !Array.isArray(filteredGroups) || filteredGroups.length === 0}
    <div class="ds-empty">
      <div class="ds-empty-icon">∅</div>
      <div class="ds-empty-title">{activeRail === 'all' ? 'No workflows yet' : 'Nothing in this view'}</div>
      <div class="ds-empty-text">{activeRail === 'all' ? 'Workflows scheduled in any of your agents will surface here.' : 'Switch rail or clear filters to see more.'}</div>
    </div>
  {:else}
    {#each filteredGroups as g (g.agent_slug)}
      {@const collapsed = !!collapsedGroups[g.agent_slug]}
      <div class="grp">
        <button class="grp-bar" onclick={() => collapsedGroups = { ...collapsedGroups, [g.agent_slug]: !collapsed }}>
          <span class="grp-chev2">{collapsed ? '▸' : '▾'}</span>
          <span class="grp-title">{g.agent_name}</span>
          <span class="grp-meta">{Array.isArray(g.workflows) ? g.workflows.length : 0} workflows</span>
          {#if g.scope === 'shared'}<span class="grp-scope">Shared</span>{/if}
        </button>
        {#if !collapsed}
          <div class="grp-list">
            {#if !Array.isArray(g.workflows) || g.workflows.length === 0}
              <div class="row-empty">No workflows.</div>
            {:else}
              {#each g.workflows as wf (wf.id)}
                {@const pill = statusPill(wf.status || (wf.last_status === 'fail' ? 'fail' : (wf.schedule_cron ? 'active' : 'ready')))}
                {@const expanded = !!expandedWorkflows[wf.id]}
                {@const viewer = isViewer(wf)}
                {@const isPaused = (wf.status || '').toLowerCase() === 'paused'}
                {@const isRunning = (wf.status || '').toLowerCase() === 'running'}
                {@const isFail = (wf.last_status || '').toLowerCase() === 'fail'}
                {@const hasCron = !!wf.schedule_cron}
                {@const last = formatLast(wf)}
                <div class="row" class:row-open={expanded}>
                  <div class="row-main">
                    <span class="dot-status"
                      class:dot-ready={!isRunning && !isPaused && !isFail}
                      class:dot-run={isRunning}
                      class:dot-pause={isPaused}
                      class:dot-fail={isFail}>●</span>
                    <button class="row-click" onclick={() => expandedWorkflows = { ...expandedWorkflows, [wf.id]: !expanded }}>
                      <div class="row-name">{wf.name}</div>
                      {#if wf.description}<div class="row-desc">{wf.description}</div>{/if}
                      <div class="row-sub">
                        <span>{isPaused ? 'paused' : (hasCron ? formatCron(wf) : 'manual')}</span>
                        {#if hasCron && !isPaused && wf.next_run_at}
                          <span class="dot">·</span><span>next {relFuture(wf.next_run_at)}</span>
                        {/if}
                        <span class="dot">·</span>
                        <span class={last.cls}>{last.text}</span>
                      </div>
                    </button>
                    <div class="row-right">
                      <div class="row-actions">
                        {#if viewer}
                          <span class="viewer-note">viewer</span>
                          <button class="btn-sm" onclick={() => goto(`${base}/agent-os/workflows/${wf.id}/history`)}>History</button>
                        {:else}
                          <button class="btn-sm btn-run" onclick={() => doRun(wf)}>Run</button>
                          <button class="btn-sm" onclick={() => goto(`${base}/agent-os/workflows/${wf.id}/history`)}>{isFail ? 'Debug' : 'History'}</button>
                          <button class="btn-sm" onclick={() => openCronModal(wf)}>{hasCron ? 'Cron' : 'Schedule'}</button>
                          {#if hasCron}<button class="btn-sm" onclick={() => doToggle(wf)}>{isPaused ? 'Resume' : 'Pause'}</button>{/if}
                          {#if wf.project_slug && (wf as any).last_dashboard_id}
                            <button class="btn-sm" onclick={() => goto(`${base}/project/${wf.project_slug}/studio/${(wf as any).last_dashboard_id}`)}>Studio</button>
                          {/if}
                        {/if}
                      </div>
                    </div>
                  </div>
                  {#if expanded}
                    <div class="row-detail">
                      {#if Array.isArray(wf.steps) && wf.steps.length > 0}
                        <div class="rd-label">Steps ({wf.steps.length})</div>
                        <div class="rd-steps">
                          {#each wf.steps as st, si}
                            <div class="rd-step">
                              <span class="rd-sid">s{si + 1}</span>
                              <span class="rd-skind">{st.kind || 'sql'}</span>
                              <span class="rd-sbody">{(st.sql || st.prompt || st.agent || '').slice(0, 140)}</span>
                            </div>
                          {/each}
                        </div>
                      {/if}
                      {#if wf.last_output_preview || wf.last_error}
                        <div class="rd-label">Last run</div>
                        <div class="rd-output">
                          {#if wf.last_error}<span class="err">{wf.last_error}</span>
                          {:else if typeof wf.last_output_preview === 'string'}{wf.last_output_preview}
                          {:else}<span class="muted">no output preview</span>{/if}
                        </div>
                      {/if}
                    </div>
                  {/if}
                </div>
              {/each}
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  {/if}
  </main>
</div>


<!-- ─── Cron modal ─── -->
{#if cronModalWf}
  <div class="modal-backdrop" onclick={closeCronModal}></div>
  <div class="modal" role="dialog" aria-label="Schedule workflow">
    <div class="modal-head">
      <div>
        <div class="modal-eyebrow">SCHEDULE</div>
        <div class="modal-title">{cronModalWf.name}</div>
      </div>
      <button class="modal-close" onclick={closeCronModal}><Icon name="x" size={14} /></button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label class="lbl">PRESET</label>
        <div class="radio-row">
          {#each [['daily','Daily'],['weekly','Weekly'],['monthly','Monthly'],['hourly','Hourly / N'],['custom','Custom']] as [v,l]}
            <label class="radio">
              <input type="radio" name="preset" value={v} checked={cronPreset === v} onchange={() => cronPreset = v as any} />
              <span>{l}</span>
            </label>
          {/each}
        </div>
      </div>

      {#if cronPreset === 'daily'}
        <div class="field-row">
          <label class="lbl">TIME (UTC)</label>
          <input class="tb-input sm" type="number" min="0" max="23" bind:value={cronHH} />
          <span class="sep">:</span>
          <input class="tb-input sm" type="number" min="0" max="59" bind:value={cronMM} />
        </div>
      {:else if cronPreset === 'weekly'}
        <div class="field-row">
          <label class="lbl">DAY OF WEEK</label>
          <select class="tb-select" bind:value={cronDOW}>
            <option value="0">Sun</option><option value="1">Mon</option><option value="2">Tue</option>
            <option value="3">Wed</option><option value="4">Thu</option><option value="5">Fri</option>
            <option value="6">Sat</option>
          </select>
          <label class="lbl">TIME</label>
          <input class="tb-input sm" type="number" min="0" max="23" bind:value={cronHH} />
          <span class="sep">:</span>
          <input class="tb-input sm" type="number" min="0" max="59" bind:value={cronMM} />
        </div>
      {:else if cronPreset === 'monthly'}
        <div class="field-row">
          <label class="lbl">DAY OF MONTH</label>
          <input class="tb-input sm" type="number" min="1" max="31" bind:value={cronDay} />
          <label class="lbl">TIME</label>
          <input class="tb-input sm" type="number" min="0" max="23" bind:value={cronHH} />
          <span class="sep">:</span>
          <input class="tb-input sm" type="number" min="0" max="59" bind:value={cronMM} />
        </div>
      {:else if cronPreset === 'hourly'}
        <div class="field-row">
          <label class="lbl">EVERY N HOURS</label>
          <input class="tb-input sm" type="number" min="1" max="23" bind:value={cronEveryN} />
        </div>
      {:else}
        <div class="field">
          <label class="lbl">CRON EXPRESSION (5 fields)</label>
          <input class="tb-input" type="text" placeholder="0 2 * * *" bind:value={cronCustom} />
          <div class="hint mono">{validateCron(cronCustom || '') ? 'valid' : 'invalid — expected: MIN HOUR DAY MONTH DOW'}</div>
        </div>
      {/if}

      <div class="field">
        <label class="lbl">COMPUTED CRON</label>
        <code class="cron-out mono">{presetToCron() || '—'}</code>
      </div>

      <div class="field">
        <label class="lbl">NEXT 3 RUNS</label>
        <div class="hint mono">preview unavailable — will fire per cron expression</div>
      </div>

      <div class="field-row">
        <label class="lbl">COST CAP (USD/run)</label>
        <input class="tb-input sm" type="number" step="0.01" min="0" bind:value={cronCostCap} />
      </div>

      <div class="field">
        <label class="lbl">ACTIONS</label>
        <div class="radio-row">
          <label class="radio"><input type="checkbox" bind:checked={cronActions.post_insight} /><span>post_insight</span></label>
          <label class="radio"><input type="checkbox" bind:checked={cronActions.alert} /><span>alert</span></label>
          <label class="radio"><input type="checkbox" bind:checked={cronActions.suggest} /><span>suggest</span></label>
        </div>
      </div>

      <div class="field-row">
        <label class="radio"><input type="checkbox" bind:checked={cronEnabled} /><span>Enabled (resume on save)</span></label>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn-ghost" onclick={closeCronModal} disabled={cronSaving}>CANCEL</button>
      <button class="btn-primary" onclick={saveCron} disabled={cronSaving || !validateCron(presetToCron())}>
        {cronSaving ? 'SAVING…' : 'SAVE'}
      </button>
    </div>
  </div>
{/if}

<!-- ─── History drawer ─── -->
{#if historyWf}
  <div class="drawer-backdrop" onclick={closeHistory}></div>
  <div class="drawer" role="dialog" aria-label="Run history">
    <div class="drawer-head">
      <div>
        <div class="modal-eyebrow">RUN HISTORY</div>
        <div class="drawer-title">{historyWf.name}</div>
      </div>
      <button class="modal-close" onclick={closeHistory}><Icon name="x" size={14} /></button>
    </div>
    <div class="drawer-body">
      {#if historyLoading}
        <div class="loading">LOADING…</div>
      {:else if historyRuns.length === 0}
        <div class="ds-empty">
          <div class="ds-empty-title">No runs yet</div>
          <div class="ds-empty-text">Run history will appear here after the first execution.</div>
        </div>
      {:else}
        <table class="hist-tbl">
          <thead>
            <tr>
              <th>STARTED</th>
              <th>DURATION</th>
              <th>STATUS</th>
              <th>STEPS</th>
              <th>COST</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each historyRuns as r (r.id)}
              {@const open = expandedRun === r.id}
              <tr class="hist-row" onclick={() => expandedRun = open ? null : r.id}>
                <td class="mono">{(r.started_at || '').replace('T', ' ').slice(0, 19)}</td>
                <td class="mono">{r.duration_s != null ? `${r.duration_s}s` : '—'}</td>
                <td>
                  {#if r.status === 'ok' || r.status === 'done'}<span class="ok"><Icon name="check" size={14} /> OK</span>
                  {:else if r.status === 'fail' || r.status === 'error'}<span class="err"><Icon name="x" size={14} /> FAIL</span>
                  {:else}<span class="muted">◐ {r.status}</span>{/if}
                </td>
                <td class="mono">{r.steps_done ?? '—'}/{r.steps_total ?? '—'}</td>
                <td class="mono">${(r.cost_usd ?? 0).toFixed(3)}</td>
                <td><span class="mono">{open ? '▾' : '▸'}</span></td>
              </tr>
              {#if open}
                <tr><td colspan="6">
                  <div class="run-detail">
                    <div class="rd-tabs">
                      <button class="rd-tab" class:active={runDetailTab === 'data'} onclick={() => runDetailTab = 'data'}>▸ DATA</button>
                      <button class="rd-tab" class:active={runDetailTab === 'sql'} onclick={() => runDetailTab = 'sql'}>▸ SQL</button>
                      <button class="rd-tab" class:active={runDetailTab === 'transcript'} onclick={() => runDetailTab = 'transcript'}>▸ AGENT TRANSCRIPT</button>
                    </div>
                    <pre class="rd-pre">{runDetailTab === 'data' ? (r.output_preview || 'no preview') : runDetailTab === 'sql' ? '— SQL not captured in summary; load via /runs/{id} —' : (r.error || '— transcript via /runs/{id} —')}</pre>
                  </div>
                </td></tr>
              {/if}
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  </div>
{/if}

<style>
 /* ───── Option D: two-pane rail layout ───── */
 .wf-shell {
   display: grid;
   grid-template-columns: 240px 1fr;
   min-height: calc(100vh - 64px);
   height: calc(100vh - 64px);
   overflow: hidden;
   background: var(--pw-bg, #faf8f3);
 }
 .wf-rail {
   background: var(--pw-bg-alt, #f1ede4);
   border-right: 1px solid var(--pw-border, #e7e3da);
   padding: 14px 8px 100px;
   overflow-y: auto;
   overflow-x: hidden;
   display: flex;
   flex-direction: column;
   gap: 4px;
   scrollbar-width: thin;
 }
 .wf-rail-group { display: flex; flex-direction: column; gap: 1px; margin-bottom: 8px; }
 .wf-rail-label { font-size: 10px; font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase; color: var(--pw-muted, #87837a); padding: 6px 12px 4px; }
 .wf-rail button {
   display: flex; align-items: center; gap: 8px; width: 100%;
   background: transparent; border: none; padding: 6px 12px;
   color: var(--pw-ink, #2c2a26); font-family: inherit; font-size: 12px; font-weight: 500;
   cursor: pointer; text-align: left; border-radius: 4px;
   transition: background .12s, color .12s;
 }
 .wf-rail button:hover { background: rgba(201, 99, 66, 0.05); }
 .wf-rail button.active { background: rgba(201, 99, 66, 0.10); color: var(--pw-accent, #c96342); font-weight: 600; }
 .wf-rail-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .wf-rail-count { font-size: 10px; font-weight: 600; color: var(--pw-muted, #87837a); padding: 1px 7px; background: var(--pw-bg, #faf8f3); border-radius: 9px; min-width: 22px; text-align: center; }
 .wf-rail button.active .wf-rail-count { background: rgba(201,99,66,0.18); color: var(--pw-accent, #c96342); }

 .wf-main { overflow-y: auto; overflow-x: hidden; padding: 24px 32px 80px; min-width: 0; }

 .wf-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; flex-wrap: wrap; margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--pw-border, #e7e3da); }
 .wf-head .ds-page-title { font-family: 'Source Serif Pro', Georgia, serif; font-size: 26px; font-weight: 600; color: var(--pw-ink); margin: 0; letter-spacing: -0.01em; }
 .wf-head .ds-page-sub { font-size: 13px; color: var(--pw-muted, #87837a); margin: 4px 0 0; }
 .wf-head .ok { color: #16a34a; }
 .wf-head .err { color: #dc2626; }
 .wf-head .muted { color: var(--pw-muted); }

 .wf-controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
 .wf-search { position: relative; display: inline-flex; align-items: center; }
 .wf-search-icon { position: absolute; left: 10px; color: var(--pw-muted, #87837a); pointer-events: none; display: inline-flex; }
 .wf-search-input { padding-left: 32px !important; min-width: 240px; font-size: 13px !important; height: 34px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 6px; color: var(--pw-ink); }
 .wf-filter-pills { display: inline-flex; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 6px; overflow: hidden; }
 .pill-segment { background: transparent; border: none; padding: 6px 14px; font-size: 12px; font-weight: 500; color: var(--pw-muted, #87837a); cursor: pointer; border-right: 1px solid var(--pw-border, #e7e3da); }
 .pill-segment:last-child { border-right: none; }
 .pill-segment:hover { color: var(--pw-ink); }
 .pill-segment.active { background: var(--pw-accent, #c96342); color: #fff; font-weight: 600; }
 /* legacy fallback */
.page {
 padding: 20px 32px 140px;
 max-width: 1400px;
 margin: 0 auto;
 color: var(--pw-ink);
 font-size: 13px;
 }

 /* Claude-style header + tabs + rows */
 .hdr { padding: 4px 4px 16px; }
 .hdr-title { font-family: 'Source Serif Pro', Georgia, serif; font-size: 28px; font-weight: 600; color: var(--pw-ink); letter-spacing: -0.01em; }
 .hdr-sub { margin-top: 4px; font-size: 13px; color: var(--pw-muted, #87837a); display: flex; gap: 6px; align-items: center; }
 .hdr-sub .ok { color: #16a34a; }
 .hdr-sub .err { color: #dc2626; }
 .hdr-sub .muted { color: var(--pw-muted); }
 .hdr-sub .dot { color: var(--pw-border, #e7e3da); }

 .tabs-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--pw-border, #e7e3da); margin-bottom: 8px; flex-wrap: wrap; }
 .tabs { display: flex; gap: 4px; }
 .tab { background: transparent; border: none; padding: 10px 14px; font-size: 13px; font-weight: 500; color: var(--pw-muted, #87837a); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
 .tab:hover { color: var(--pw-ink); }
 .tab-on { color: var(--pw-ink); border-bottom-color: var(--pw-accent, #c96342); }
 .tabs-right { display: flex; gap: 8px; align-items: center; padding-bottom: 8px; }
 .search { font: inherit; font-size: 13px; padding: 6px 12px; min-width: 240px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 6px; color: var(--pw-ink); }
 .picker { font: inherit; font-size: 12px; padding: 6px 10px; background: var(--pw-surface, #faf9f5); border: 1px solid var(--pw-border, #e7e3da); border-radius: 6px; color: var(--pw-ink); }

 .grp { margin-top: 18px; }
 .grp-bar { width: 100%; display: flex; align-items: baseline; gap: 10px; background: transparent; border: none; padding: 8px 4px; cursor: pointer; text-align: left; border-bottom: 1px solid var(--pw-border, #e7e3da); }
 .grp-chev2 { font-family: ui-monospace; color: var(--pw-muted); font-size: 11px; }
 .grp-title { font-family: 'Source Serif Pro', Georgia, serif; font-size: 17px; font-weight: 600; color: var(--pw-ink); }
 .grp-meta { font-size: 12px; color: var(--pw-muted, #87837a); }
 .grp-scope { font-size: 10px; font-weight: 600; letter-spacing: 0.04em; padding: 1px 6px; background: rgba(37,99,235,0.10); color: #2563eb; border-radius: 3px; margin-left: auto; }
 .grp-list { display: flex; flex-direction: column; }

 .row { border-bottom: 1px solid var(--pw-border-soft, #efeae0); transition: background 0.12s; }
 .row:hover { background: rgba(201,99,66,0.03); }
 .row:hover .row-actions { opacity: 1; }
 .row-open { background: rgba(201,99,66,0.05); }
 .row-main { display: flex; align-items: flex-start; gap: 12px; padding: 14px 8px; }
 .dot-status { font-size: 10px; margin-top: 6px; }
 .dot-ready { color: #87837a; }
 .dot-run { color: #c96342; animation: pulse 1.5s infinite; }
 .dot-pause { color: #d97706; }
 .dot-fail { color: #dc2626; }
 @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
 .row-click { flex: 1; background: transparent; border: none; padding: 0; text-align: left; cursor: pointer; min-width: 0; }
 .row-name { font-family: 'Source Serif Pro', Georgia, serif; font-size: 15px; font-weight: 600; color: var(--pw-ink); }
 .row-desc { margin-top: 2px; font-size: 13px; color: var(--pw-muted, #87837a); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
 .row-sub { margin-top: 4px; font-size: 12px; color: var(--pw-muted, #87837a); display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
 .row-sub .dot { color: var(--pw-border, #e7e3da); }
 .row-sub .ok { color: #16a34a; }
 .row-sub .err { color: #dc2626; }
 .row-right { display: flex; align-items: center; }
 .row-actions { display: flex; gap: 4px; opacity: 0; transition: opacity 0.12s; }
 .row-open .row-actions { opacity: 1; }
 .btn-sm { font: inherit; font-size: 12px; font-weight: 500; padding: 5px 10px; background: var(--pw-surface, #faf9f5); color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-border, #e7e3da); border-radius: 5px; cursor: pointer; }
 .btn-sm:hover:not(:disabled) { background: var(--pw-bg-alt, #f1ede4); }
 .btn-sm:disabled { opacity: 0.4; cursor: not-allowed; }
 .btn-run { background: var(--pw-ink, #2c2a26); color: #faf9f5; border-color: var(--pw-ink, #2c2a26); }
 .btn-run:hover:not(:disabled) { filter: brightness(1.1); background: var(--pw-ink, #2c2a26); }
 .viewer-note { font-size: 11px; color: var(--pw-muted); font-style: italic; padding-right: 6px; }

 .row-detail { margin: 0 8px 14px 32px; padding: 12px 14px; background: var(--pw-bg-alt, #f1ede4); border: 1px solid var(--pw-border-soft, #efeae0); border-radius: 6px; font-size: 12px; line-height: 1.5; }
 .rd-label { font-size: 11px; font-weight: 600; letter-spacing: 0.04em; color: var(--pw-muted, #87837a); margin: 0 0 6px; }
 .rd-steps { display: flex; flex-direction: column; gap: 3px; margin-bottom: 10px; }
 .rd-step { display: flex; gap: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
 .rd-sid { color: var(--pw-accent, #c96342); width: 30px; font-weight: 600; }
 .rd-skind { color: #2563eb; width: 60px; }
 .rd-sbody { color: var(--pw-ink); overflow: hidden; text-overflow: ellipsis; }
 .rd-output { padding: 8px 10px; background: #1a1614; color: #e8e3d6; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
 .rd-output .err { color: #fca5a5; }
 .rd-output .muted { color: #6b6760; }

 /* Legacy CLI styles below (cron modal / history drawer reuse some) */
 /* CLI banner */
 .cli-banner {
 background: #1a1614;
 color: #e8e3d6;
 border-radius: var(--r-md, 8px);
 padding: 12px 16px;
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 12px;
 margin-bottom: 16px;
 }
 .cli-line { display: flex; align-items: center; gap: 8px; }
 .cli-prompt { color: var(--pw-accent, #c96342); font-weight: 700; }
 .cli-cmd { color: #e8e3d6; }
 .cli-flag { color: #87837a; }
 .cli-stats { margin-top: 6px; color: #a59f92; font-size: 11px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
 .cli-stats .ok { color: #34d399; }
 .cli-stats .err { color: #f87171; }
 .cli-stats .muted { color: #87837a; }
 .sep { color: #6b6760; padding: 0 2px; }

 /* Toolbar */
 .toolbar {
 display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
 padding: 10px 12px; background: var(--pw-bg-alt, #f1ede4);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: var(--r-md, 8px);
 margin-bottom: 16px;
 }
 .tb-group { display: inline-flex; align-items: center; gap: 6px; }
 .tb-lbl { font-size: 10px; font-weight: 700; color: var(--pw-muted, #87837a); letter-spacing: 0.06em; text-transform: uppercase; }
 .tb-select, .tb-input {
 font: inherit; font-size: 12px;
 padding: 6px 10px;
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 color: var(--pw-ink, #2c2a26);
 border-radius: var(--r-sm, 6px);
 }
 .tb-input { min-width: 220px; }
 .tb-input.sm { min-width: 60px; width: 70px; }
 .chip {
 font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
 padding: 5px 12px; background: transparent;
 border: 1px solid var(--pw-border, #e7e3da); color: var(--pw-muted, #87837a);
 border-radius: 0; cursor: pointer;
 }
 .chip:hover { color: var(--pw-ink); }
 .chip.active {
 background: rgba(201, 99, 66, 0.12);
 color: var(--pw-accent, #c96342);
 border-color: var(--pw-accent, #c96342);
 }

 .banner-err {
 background: rgba(220,38,38,0.08); color: #991b1b; border: 1px solid rgba(220,38,38,0.3);
 border-radius: var(--r-sm, 6px); padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;
 }
 .btn-dismiss { background: transparent; border: none; color: #991b1b; cursor: pointer; font-size: 14px; }

 .loading { text-align: center; padding: 40px; color: var(--pw-muted, #87837a); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0.1em; }

 /* Groups */
 .group-card {
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: var(--r-md, 8px);
 margin-bottom: 12px;
 overflow: hidden;
 }
 .group-head {
 width: 100%; display: flex; align-items: center; gap: 10px;
 padding: 12px 16px; background: transparent;
 border: none; cursor: pointer; text-align: left;
 border-bottom: 1px solid transparent;
 }
 .group-head:hover { background: rgba(201,99,66,0.04); }
 .grp-chev { font-family: ui-monospace; color: var(--pw-muted); width: 14px; }
 .grp-icon { font-size: 16px; }
 .grp-name { font-weight: 600; color: var(--pw-ink); }
 .grp-badge {
 font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
 padding: 2px 8px; border-radius: 0;
 background: rgba(201,99,66,0.10); color: var(--pw-accent, #c96342);
 }
 .grp-badge.shared { background: rgba(37,99,235,0.10); color: #2563eb; }
 .grp-role { font-size: 11px; color: var(--pw-muted, #87837a); }
 .grp-summary {
 margin-left: auto; font-size: 11px; color: var(--pw-muted);
 padding-right: 12px; border-right: 1px solid var(--pw-border-soft, #efeae0);
 }
 .grp-count { font-size: 11px; color: var(--pw-muted); font-family: ui-monospace; padding-left: 12px; }
 .group-body { padding: 4px 0; }

 /* Workflow row */
 .wf-row {
 border-top: 1px solid var(--pw-border-soft, #efeae0);
 padding: 10px 16px;
 transition: background 0.12s;
 }
 .wf-row:first-child { border-top: none; }
 .wf-row:hover { background: rgba(201,99,66,0.04); }
 .wf-row-expanded { background: rgba(201,99,66,0.06); }
 .wf-head {
 width: 100%; display: flex; align-items: center; gap: 10px;
 background: transparent; border: none; cursor: pointer; text-align: left; padding: 0;
 }
 .wf-chev { font-family: ui-monospace; color: var(--pw-muted); width: 14px; }
 .wf-name { font-size: 13px; font-weight: 600; color: var(--pw-ink); text-transform: uppercase; letter-spacing: 0.02em; }
 .wf-pill {
 font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
 padding: 2px 8px; border-radius: 0;
 display: inline-flex; align-items: center; gap: 4px;
 }
 .wf-desc {
 margin: 4px 0 0 24px; font-size: 12px; color: var(--pw-muted, #87837a);
 overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
 }
 .wf-meta {
 padding: 6px 0 0 24px; font-size: 11px; color: var(--pw-muted, #87837a);
 display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
 }
 .meta-spacer { flex: 1; }
 .dim { color: var(--pw-muted, #87837a); }
 .sep { color: var(--pw-border, #e7e3da); }
 .wf-detail {
 margin: 10px 0 4px 24px; padding: 10px 12px;
 background: var(--pw-bg-alt, #f1ede4);
 border: 1px solid var(--pw-border-soft, #efeae0);
 border-radius: 0;
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 11px; line-height: 1.5;
 }
 .detail-label {
 font-size: 9px; font-weight: 800; letter-spacing: 0.12em;
 color: var(--pw-muted, #87837a); margin: 0 0 4px 0; text-transform: uppercase;
 }
 .detail-steps { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
 .step-row { display: flex; gap: 8px; }
 .step-id { color: var(--pw-accent, #c96342); width: 30px; font-weight: 700; }
 .step-kind { color: #2563eb; width: 60px; }
 .step-body { color: var(--pw-ink, #2c2a26); overflow: hidden; text-overflow: ellipsis; }
 .detail-output {
 padding: 6px 8px; background: #1a1614; color: #e8e3d6;
 border-radius: 0; white-space: pre-wrap; word-break: break-word;
 }
 .detail-output .err { color: #fca5a5; }
 .detail-output .dim { color: #6b6760; }
 .wf-meta .ok { color: #16a34a; }
 .wf-meta .err { color: #dc2626; }
 .wf-meta .muted { color: var(--pw-muted); }
 .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
 .wf-actions {
 margin: 10px 0 4px 24px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
 }
 .viewer-note { font-size: 11px; color: var(--pw-muted, #87837a); font-style: italic; }
 .row-empty { padding: 16px; text-align: center; color: var(--pw-muted, #87837a); font-size: 11px; }

 /* Buttons */
 .btn-primary {
 font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
 padding: 5px 12px; background: var(--pw-accent, #c96342); color: #fff;
 border: 1px solid var(--pw-accent, #c96342); border-radius: var(--r-sm, 6px);
 cursor: pointer;
 }
 .btn-primary:hover:not(:disabled) { filter: brightness(0.95); }
 .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
 .btn-ghost {
 font: inherit; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
 padding: 5px 12px; background: transparent; color: var(--pw-ink, #2c2a26);
 border: 1px solid var(--pw-border, #e7e3da); border-radius: var(--r-sm, 6px);
 cursor: pointer;
 }
 .btn-ghost:hover { background: var(--pw-bg-alt, #f1ede4); }

 /* Live tail */
 .live-tail {
 position: fixed; bottom: 0; left: 0; right: 0;
 background: #1a1614; color: #e8e3d6;
 border-top: 1px solid #2a2522;
 z-index: 100;
 max-height: 200px; display: flex; flex-direction: column;
 }
 .lt-head {
 padding: 6px 16px; background: #15110f;
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: #34d399;
 display: flex; gap: 12px; align-items: center;
 border-bottom: 1px solid #2a2522;
 }
 .lt-dim { color: #6b6760; }
 .lt-body {
 flex: 1; overflow-y: auto;
 padding: 6px 16px;
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 11px; line-height: 1.55;
 }
 .lt-line { display: flex; gap: 8px; align-items: center; color: #34d399; }
 .lt-line.muted { color: #6b6760; }
 .lt-ts { color: #87837a; }
 .lt-icon.ok { color: #34d399; }
 .lt-icon.err { color: #f87171; }
 .lt-agent { color: #d4cfc4; }
 .lt-sep { color: #6b6760; }
 .lt-wf { color: #fcd34d; }
 .lt-status { color: #34d399; }

 /* Modal */
 .modal-backdrop {
 position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200;
 }
 .modal {
 position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
 width: 560px; max-width: 95vw; max-height: 90vh;
 background: var(--pw-surface, #faf9f5);
 border: 1px solid var(--pw-border, #e7e3da);
 border-radius: var(--r-md, 8px);
 box-shadow: 0 20px 60px rgba(0,0,0,0.3);
 z-index: 201; display: flex; flex-direction: column;
 }
 .modal-head, .drawer-head {
 padding: 14px 18px; border-bottom: 1px solid var(--pw-border, #e7e3da);
 display: flex; justify-content: space-between; align-items: center; background: var(--pw-bg-alt, #f1ede4);
 }
 .modal-eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: var(--pw-muted, #87837a); }
 .modal-title, .drawer-title { font-family: var(--pw-serif, Georgia, serif); font-size: 18px; font-weight: 500; color: var(--pw-ink, #2c2a26); margin-top: 2px; }
 .modal-close {
 background: none; border: none; color: var(--pw-muted, #87837a);
 cursor: pointer; padding: 4px; font-size: 18px; line-height: 1;
 }
 .modal-body { padding: 18px; overflow-y: auto; flex: 1; }
 .modal-foot {
 padding: 12px 18px; border-top: 1px solid var(--pw-border, #e7e3da);
 display: flex; justify-content: flex-end; gap: 8px; background: var(--pw-bg-alt, #f1ede4);
 }

 .field { margin-bottom: 14px; display: flex; flex-direction: column; gap: 6px; }
 .field-row { margin-bottom: 14px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
 .lbl { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; color: var(--pw-muted, #87837a); text-transform: uppercase; }
 .radio-row { display: flex; flex-wrap: wrap; gap: 12px; }
 .radio { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
 .hint { font-size: 11px; color: var(--pw-muted, #87837a); }
 .cron-out {
 padding: 8px 12px; background: #1a1614; color: #34d399; border-radius: var(--r-sm, 6px); font-size: 12px;
 display: inline-block;
 }

 /* Drawer */
 .drawer-backdrop {
 position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200;
 }
 .drawer {
 position: fixed; right: 0; top: 0; bottom: 0; width: 540px; max-width: 95vw;
 background: var(--pw-surface, #faf9f5);
 border-left: 1px solid var(--pw-border, #e7e3da);
 box-shadow: -8px 0 24px rgba(0,0,0,0.15);
 z-index: 201; display: flex; flex-direction: column;
 animation: slidein 0.18s ease-out;
 }
 @keyframes slidein { from { transform: translateX(100%); } to { transform: translateX(0); } }
 .drawer-body { padding: 14px; overflow-y: auto; flex: 1; }

 .hist-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
 .hist-tbl th {
 text-align: left; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; color: var(--pw-muted, #87837a);
 padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e7e3da); text-transform: uppercase;
 }
 .hist-tbl td { padding: 6px 8px; border-bottom: 1px solid var(--pw-border-soft, #efeae0); }
 .hist-row { cursor: pointer; }
 .hist-row:hover { background: rgba(201,99,66,0.04); }
 .hist-tbl .ok { color: #16a34a; }
 .hist-tbl .err { color: #dc2626; }
 .hist-tbl .muted { color: var(--pw-muted); }

 .run-detail { padding: 8px 0 12px; }
 .rd-tabs { display: flex; gap: 4px; margin-bottom: 6px; }
 .rd-tab {
 font: inherit; font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
 padding: 4px 10px; background: transparent;
 border: 1px solid var(--pw-border, #e7e3da); color: var(--pw-muted, #87837a);
 border-radius: var(--r-sm, 6px); cursor: pointer;
 }
 .rd-tab.active { background: rgba(201,99,66,0.12); color: var(--pw-accent, #c96342); border-color: var(--pw-accent, #c96342); }
 .rd-pre {
 background: #1a1614; color: #e8e3d6;
 font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
 font-size: 11px; line-height: 1.55;
 padding: 10px 12px; border-radius: var(--r-sm, 6px);
 max-height: 240px; overflow: auto;
 margin: 0;
 }
</style>
