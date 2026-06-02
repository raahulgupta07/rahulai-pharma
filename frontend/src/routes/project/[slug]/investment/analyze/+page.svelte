<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { page } from '$app/stores';
 import { goto } from '$app/navigation';
 import { base } from '$app/paths';
 import { authHeaders } from '$lib/api';

 const slug = $derived($page.params.slug);
 const runId = $derived($page.url.searchParams.get('run_id') || '');

 type AgentStatus = 'queued' | 'running' | 'done' | 'failed';

 interface AgentCard {
 key: string;
 name: string;
 status: AgentStatus;
 snippet: string;
 }

 const AGENT_ORDER: { key: string; name: string }[] = [
 { key: 'market', name: 'Market' },
 { key: 'financial', name: 'Financial' },
 { key: 'technical', name: 'Technical' },
 { key: 'risk', name: 'Risk' },
 { key: 'researcher', name: 'Researcher' },
 { key: 'memo', name: 'Memo Writer' },
 { key: 'chair', name: 'Committee Chair' },
 ];

 const AGENT_ALIASES: Record<string, string> = {
 market: 'market', 'market analyst': 'market', marketanalyst: 'market',
 financial: 'financial', 'financial analyst': 'financial', finance: 'financial',
 technical: 'technical', 'technical analyst': 'technical', technicals: 'technical',
 risk: 'risk', 'risk analyst': 'risk',
 researcher: 'researcher', research: 'researcher',
 memo: 'memo', 'memo writer': 'memo', memowriter: 'memo', writer: 'memo',
 chair: 'chair', 'committee chair': 'chair', committee: 'chair', committeechair: 'chair',
 };

 function normAgent(raw: string | undefined): string | null {
 if (!raw) return null;
 const k = raw.toString().trim().toLowerCase();
 return AGENT_ALIASES[k] || (AGENT_ORDER.find((a) => a.key === k) ? k : null);
 }

 let agents = $state<Record<string, AgentCard>>(
 Object.fromEntries(
 AGENT_ORDER.map((a) => [a.key, { key: a.key, name: a.name, status: 'queued' as AgentStatus, snippet: '' }])
 )
 );
 let activeAgent = $state<string | null>(null);

 interface LogEvent { ts: string; tag: string; agent?: string; msg: string; kind: string }
 let logs = $state<LogEvent[]>([]);
 let logBox: HTMLDivElement | null = null;

 let status = $state<'idle' | 'running' | 'done' | 'failed' | 'unknown'>('idle');
 let symbol = $state('—');
 let pattern = $state('');
 let verdict = $state<{ symbol: string; verdict: string; conviction?: number; rationale?: string; memo_id?: string } | null>(null);
 let errMsg = $state('');
 let memoId = $state<string | null>(null);

 let es: EventSource | null = null;
 let retries = 0;
 const MAX_RETRIES = 3;
 let destroyed = false;

 const VERDICT_COLORS: Record<string, { bg: string; fg: string }> = {
 BUY: { bg: 'rgba(34,197,94,0.14)', fg: '#16a34a' },
 HOLD: { bg: 'rgba(245,158,11,0.14)', fg: '#d97706' },
 PASS: { bg: 'rgba(120,113,108,0.16)', fg: '#57534e' },
 SELL: { bg: 'rgba(239,68,68,0.14)', fg: '#dc2626' },
 };

 function verdictStyle(v: string): string {
 const c = VERDICT_COLORS[v?.toUpperCase()] || VERDICT_COLORS.PASS;
 return `background:${c.bg};color:${c.fg};`;
 }

 function stars(n: number | undefined): string {
 const k = Math.max(0, Math.min(5, Math.round(n || 0)));
 return ''.repeat(k) + ''.repeat(5 - k);
 }

 function nowTs(): string {
 return new Date().toLocaleTimeString('en-GB', { hour12: false });
 }

 function pushLog(tag: string, msg: string, kind: string, agent?: string, ts?: string) {
 const t = ts || nowTs();
 logs = [...logs.slice(-499), { ts: t, tag, agent, msg, kind }];
 queueMicrotask(() => {
 if (logBox) logBox.scrollTop = logBox.scrollHeight;
 });
 }

 function setAgent(key: string | null, patch: Partial<AgentCard>) {
 if (!key || !agents[key]) return;
 agents[key] = { ...agents[key], ...patch };
 }

 function handleEvent(ev: { type?: string; event?: string; kind?: string; data?: any } & Record<string, any>) {
 const evt = (ev.type || ev.event || ev.kind || '').toString().toLowerCase();
 const dataObj = ev.data && typeof ev.data === 'object' ? ev.data : ev;
 const agentRaw = dataObj.agent || dataObj.agent_name || dataObj.role || ev.agent;
 const agentKey = normAgent(agentRaw);
 const msg = (dataObj.message || dataObj.msg || dataObj.text || '').toString();
 const ts = dataObj.ts || dataObj.timestamp || undefined;

 switch (evt) {
 case 'start':
 status = 'running';
 if (dataObj.symbol) symbol = dataObj.symbol;
 if (dataObj.team_pattern || dataObj.pattern) pattern = dataObj.team_pattern || dataObj.pattern;
 pushLog('●', `analysis started · ${symbol}`, 'info', undefined, ts);
 break;

 case 'agent_start':
 case 'agent_started':
 if (agentKey) {
 setAgent(agentKey, { status: 'running', snippet: msg || 'starting…' });
 activeAgent = agentKey;
 pushLog('●', msg || 'started', 'running', agentKey, ts);
 }
 break;

 case 'agent_msg':
 case 'agent_message':
 if (agentKey) {
 if (agents[agentKey].status === 'queued') setAgent(agentKey, { status: 'running' });
 if (msg) setAgent(agentKey, { snippet: msg.slice(0, 140) });
 activeAgent = agentKey;
 }
 if (msg) pushLog('●', msg, 'running', agentKey || undefined, ts);
 break;

 case 'agent_done':
 case 'agent_complete':
 if (agentKey) {
 setAgent(agentKey, { status: 'done', snippet: msg || 'done' });
 pushLog('', msg || 'done', 'done', agentKey, ts);
 }
 break;

 case 'memo_draft':
 setAgent('memo', { status: 'running', snippet: msg || 'drafting memo…' });
 activeAgent = 'memo';
 pushLog('', msg || 'memo draft', 'info', 'memo', ts);
 if (dataObj.memo_id) memoId = String(dataObj.memo_id);
 break;

 case 'verdict':
 verdict = {
 symbol: dataObj.symbol || symbol,
 verdict: (dataObj.verdict || '').toString().toUpperCase(),
 conviction: dataObj.conviction,
 rationale: dataObj.rationale || msg || '',
 memo_id: dataObj.memo_id ? String(dataObj.memo_id) : undefined,
 };
 if (verdict.memo_id) memoId = verdict.memo_id;
 setAgent('chair', { status: 'done', snippet: `verdict: ${verdict.verdict}` });
 pushLog('', `VERDICT ${verdict.verdict} · ${verdict.symbol}`, 'done', 'chair', ts);
 break;

 case 'done':
 case 'finished':
 case 'complete':
 status = 'done';
 // mark any non-done as done (e.g., chair)
 for (const k of Object.keys(agents)) {
 if (agents[k].status === 'running' || agents[k].status === 'queued') {
 setAgent(k, { status: 'done' });
 }
 }
 activeAgent = null;
 if (dataObj.memo_id) memoId = String(dataObj.memo_id);
 pushLog('', 'analysis complete', 'done', undefined, ts);
 closeSSE();
 break;

 case 'error':
 case 'failed':
 status = 'failed';
 errMsg = msg || 'analysis failed';
 if (agentKey) setAgent(agentKey, { status: 'failed', snippet: msg });
 pushLog('', msg || 'error', 'error', agentKey || undefined, ts);
 closeSSE();
 break;

 default:
 // unknown event — log it but don't crash
 if (msg) pushLog('·', `[${evt}] ${msg}`, 'info', agentKey || undefined, ts);
 }
 }

 async function loadRun() {
 if (!runId) {
 errMsg = 'no run_id in URL';
 status = 'idle';
 return;
 }
 try {
 const res = await fetch(`/api/projects/${slug}/investment/runs/${runId}`, { headers: authHeaders() });
 if (res.status === 404) {
 errMsg = `run #${runId} not found`;
 status = 'unknown';
 return;
 }
 if (!res.ok) throw new Error(`HTTP ${res.status}`);
 const run = await res.json();

 symbol = run.symbol || symbol;
 pattern = run.team_pattern || run.pattern || '';
 const runStatus = (run.status || '').toString().toLowerCase();
 if (runStatus) status = (runStatus === 'running' || runStatus === 'done' || runStatus === 'failed') ? runStatus as typeof status : 'running';
 if (run.memo_id) memoId = String(run.memo_id);

 // replay events JSONB
 const events: any[] = Array.isArray(run.events) ? run.events : (run.events_json || []);
 pushLog('●', `replaying ${events.length} event(s) for run #${runId}`, 'info');
 for (const e of events) {
 handleEvent(e);
 }
 // if run already finished, don't subscribe to SSE
 if (status === 'done' || status === 'failed') {
 return;
 }
 } catch (e) {
 errMsg = e instanceof Error ? e.message : String(e);
 status = 'unknown';
 pushLog('', `failed to load run: ${errMsg}`, 'error');
 return;
 }
 connectSSE();
 }

 function connectSSE() {
 if (!runId || destroyed) return;
 closeSSE();
 try {
 const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 const qs = token ? `?token=${encodeURIComponent(token)}` : '';
 es = new EventSource(`/api/projects/${slug}/investment/runs/${runId}/stream${qs}`);

 es.onopen = () => {
 retries = 0;
 pushLog('●', 'SSE connected', 'info');
 };

 es.onmessage = (msg) => {
 const raw = msg.data || '';
 if (!raw || raw.startsWith(':')) return; // heartbeat / comment
 try {
 const data = JSON.parse(raw);
 handleEvent(data);
 } catch {
 pushLog('·', raw, 'info');
 }
 };

 es.onerror = () => {
 if (destroyed) return;
 if (status === 'done' || status === 'failed') {
 closeSSE();
 return;
 }
 pushLog('', `SSE disconnected (attempt ${retries + 1}/${MAX_RETRIES})`, 'warn');
 closeSSE();
 if (retries < MAX_RETRIES) {
 retries += 1;
 setTimeout(connectSSE, 1500 * retries);
 } else {
 pushLog('', 'SSE retries exhausted', 'error');
 }
 };
 } catch (e) {
 pushLog('', `SSE init failed: ${e}`, 'error');
 }
 }

 function closeSSE() {
 if (es) {
 try { es.close(); } catch { /* ignore */ }
 es = null;
 }
 }

 onMount(() => {
 pushLog('●', `session started · slug ${slug} · run ${runId || '(none)'}`, 'info');
 loadRun();
 });

 onDestroy(() => {
 destroyed = true;
 closeSSE();
 });

 function logColor(kind: string): string {
 switch (kind) {
 case 'done': return '#10b981';
 case 'running': return '#22d3ee';
 case 'error': return '#ef4444';
 case 'warn': return '#f59e0b';
 default: return '#a8a29e';
 }
 }

 function statusDotColor(): string {
 if (status === 'running') return '#22d3ee';
 if (status === 'done') return '#10b981';
 if (status === 'failed') return '#ef4444';
 return '#a8a29e';
 }

 function viewMemo() {
 // Memos now live in project chat + Settings DOCS tab.
 // Route to project chat w/ memo_id query param (chat can auto-scroll/highlight).
 if (memoId) {
 goto(`${base}/project/${slug}?memo_id=${memoId}`);
 } else {
 goto(`${base}/project/${slug}/settings?tab=docs`);
 }
 }
</script>

<svelte:head><title>IC Analysis · {symbol} · {slug}</title></svelte:head>

<div class="ana-page">
  <header class="topbar">
    <button class="back-btn" onclick={() => goto(`${base}/project/${slug}`)} aria-label="Back">←</button>
    <span class="brand"><Icon name="briefcase" size={14} /> IC ANALYSIS:</span>
    <span class="sym">{symbol}</span>
    <span class="status-dot" style="background:{statusDotColor()}" title={status}></span>
    {#if pattern}<span class="pattern-badge">{pattern}</span>{/if}
    <span class="run-id">Run #{runId || '—'}</span>
    <div class="spacer"></div>
    {#if status === 'done' || memoId}
      <button class="primary-btn" onclick={viewMemo}><Icon name="file-text" size={14} /> VIEW MEMO</button>
    {/if}
  </header>

  {#if errMsg && status === 'unknown'}
    <div class="page-err">
      <Icon name="alert-triangle" size={14} /> {errMsg}
      <button class="ghost-btn" onclick={() => goto(`${base}/project/${slug}`)}>← BACK TO PROJECT</button>
    </div>
  {/if}

  <div class="split">
    <!-- LEFT 55% — agent grid -->
    <section class="agent-pane">
      <div class="pane-head">AGENT ACTIVITY</div>
      <div class="agent-grid">
        {#each AGENT_ORDER as def (def.key)}
          {@const a = agents[def.key]}
          <div
            class="agent-card"
            class:active={activeAgent === def.key}
            class:done={a.status === 'done'}
            class:failed={a.status === 'failed'}
          >
            <div class="agent-head">
              <span class="agent-name">{a.name}</span>
              <span class="agent-status status-{a.status}">
                {#if a.status === 'running'}
                  <span class="dots"><span></span><span></span><span></span></span>
                {/if}
                {a.status}
              </span>
            </div>
            <div class="agent-snippet">{a.snippet || (a.status === 'queued' ? '(queued)' : '')}</div>
          </div>
        {/each}
      </div>

      {#if verdict}
        <div class="verdict-card">
          <div class="verdict-head">
            <span class="verdict-sym">{verdict.symbol}</span>
            <span class="verdict-pill" style={verdictStyle(verdict.verdict)}>{verdict.verdict}</span>
            <span class="verdict-stars">{stars(verdict.conviction)}</span>
            <div class="spacer"></div>
            <button class="primary-btn" onclick={viewMemo}>→ VIEW FULL MEMO</button>
          </div>
          {#if verdict.rationale}
            <div class="verdict-rationale">{verdict.rationale}</div>
          {/if}
        </div>
      {/if}
    </section>

    <!-- RIGHT 45% — CLI dashboard -->
    <section class="cli-pane">
      <div class="pane-head dark">
        <span>CLI DASHBOARD</span>
        <span class="cli-meta">{logs.length} event(s)</span>
      </div>
      <div class="cli-log" bind:this={logBox}>
        {#each logs as e, i (i)}
          <div class="cli-line" style="color:{logColor(e.kind)}">
            <span class="cli-ts">{e.ts}</span>
            <span class="cli-tag">{e.tag}</span>
            {#if e.agent}<span class="cli-agent">[{e.agent}]</span>{/if}
            <span class="cli-msg">{e.msg}</span>
          </div>
        {/each}
        {#if logs.length === 0}
          <div class="cli-line muted">awaiting events…</div>
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
 .ana-page {
 display: flex;
 flex-direction: column;
 height: calc(100vh - 56px);
 background: var(--pw-bg, #faf7f0);
 color: var(--pw-ink, #2c2a26);
 font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
 }

 .topbar {
 display: flex;
 align-items: center;
 gap: 10px;
 height: 56px;
 padding: 0 20px;
 border-bottom: 1px solid var(--pw-border, #d8d3c4);
 background: var(--pw-surface, #fff);
 flex-shrink: 0;
 }
 .back-btn {
 background: none;
 border: 1px solid var(--pw-border, #d8d3c4);
 border-radius: 0;
 width: 32px; height: 32px;
 font-size: 12px;
 cursor: pointer;
 color: var(--pw-ink);
 }
 .back-btn:hover { background: var(--pw-bg-alt, #f0ebdc); }
 .brand { font-weight: 600; font-size: 11px; letter-spacing: 0.02em; }
 .sym { font-weight: 700; font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }
 .status-dot {
 width: 10px; height: 10px;
 border-radius: 50%;
 box-shadow: 0 0 6px currentColor;
 }
 .pattern-badge {
 background: var(--pw-bg-alt, #f0ebdc);
 color: var(--pw-ink-soft, #6b6660);
 padding: 3px 8px;
 border-radius: 0;
 font-size: 10.5px;
 font-weight: 600;
 letter-spacing: 0.04em;
 text-transform: uppercase;
 }
 .run-id {
 font-size: 11px;
 color: var(--pw-ink-soft, #6b6660);
 font-family: 'SF Mono', Menlo, monospace;
 }
 .spacer { flex: 1; }

 .ghost-btn, .primary-btn {
 background: var(--pw-bg-alt, #f0ebdc);
 border: 1px solid var(--pw-border, #d8d3c4);
 color: var(--pw-ink);
 padding: 6px 12px;
 border-radius: 0;
 font-size: 11px;
 font-weight: 600;
 letter-spacing: 0.04em;
 cursor: pointer;
 text-transform: uppercase;
 }
 .ghost-btn:hover { background: #e8e2d0; }
 .primary-btn {
 background: var(--pw-accent, #c96342);
 color: #fff;
 border-color: var(--pw-accent, #c96342);
 }
 .primary-btn:hover { background: #b3573a; }

 .page-err {
 padding: 14px 20px;
 background: rgba(239,68,68,0.08);
 color: #b91c1c;
 border-bottom: 1px solid rgba(239,68,68,0.2);
 font-size: 11px;
 display: flex;
 align-items: center;
 gap: 12px;
 }

 .split {
 display: grid;
 grid-template-columns: 55% 45%;
 flex: 1;
 min-height: 0;
 }

 .agent-pane {
 display: flex;
 flex-direction: column;
 border-right: 1px solid var(--pw-border, #d8d3c4);
 overflow-y: auto;
 padding: 14px 16px;
 min-height: 0;
 }
 .cli-pane {
 display: flex;
 flex-direction: column;
 min-height: 0;
 overflow: hidden;
 }

 .pane-head {
 font-size: 10.5px;
 font-weight: 600;
 letter-spacing: 0.06em;
 text-transform: uppercase;
 color: var(--pw-ink-soft, #6b6660);
 padding: 0 0 10px;
 }
 .pane-head.dark {
 background: #1a1614;
 color: #a8a29e;
 padding: 10px 16px;
 display: flex;
 justify-content: space-between;
 align-items: center;
 border-bottom: 1px solid #2d2926;
 }
 .cli-meta { font-size: 10px; opacity: 0.7; }

 .agent-grid {
 display: grid;
 grid-template-columns: repeat(2, 1fr);
 gap: 10px;
 }
 .agent-card {
 border: 1px solid var(--pw-border, #d8d3c4);
 border-left: 3px solid var(--pw-border, #d8d3c4);
 background: var(--pw-surface, #fff);
 border-radius: 0;
 padding: 10px 12px;
 min-height: 70px;
 transition: border-color 0.2s, background 0.2s;
 }
 .agent-card.active {
 border-color: var(--pw-accent, #c96342);
 border-left-color: var(--pw-accent, #c96342);
 box-shadow: 0 0 0 1px rgba(201,99,66,0.18);
 }
 .agent-card.done { border-left-color: #10b981; }
 .agent-card.failed { border-left-color: #ef4444; background: rgba(239,68,68,0.04); }

 .agent-head {
 display: flex;
 justify-content: space-between;
 align-items: center;
 margin-bottom: 6px;
 }
 .agent-name {
 font-weight: 600;
 font-size: 11px;
 }
 .agent-status {
 font-size: 10px;
 font-weight: 600;
 text-transform: uppercase;
 letter-spacing: 0.05em;
 padding: 2px 6px;
 border-radius: 0;
 display: inline-flex;
 align-items: center;
 gap: 4px;
 }
 .status-queued { background: rgba(120,113,108,0.14); color: #57534e; }
 .status-running { background: rgba(34,211,238,0.14); color: #0e7490; }
 .status-done { background: rgba(16,185,129,0.14); color: #047857; }
 .status-failed { background: rgba(239,68,68,0.14); color: #b91c1c; }

 .dots { display: inline-flex; gap: 2px; }
 .dots span {
 width: 4px; height: 4px;
 background: currentColor;
 border-radius: 50%;
 animation: blink 1.2s infinite;
 }
 .dots span:nth-child(2) { animation-delay: 0.2s; }
 .dots span:nth-child(3) { animation-delay: 0.4s; }
 @keyframes blink {
 0%, 100% { opacity: 0.3; }
 50% { opacity: 1; }
 }

 .agent-snippet {
 font-size: 11.5px;
 color: var(--pw-ink-soft, #6b6660);
 line-height: 1.4;
 overflow: hidden;
 text-overflow: ellipsis;
 display: -webkit-box;
 -webkit-line-clamp: 2;
 -webkit-box-orient: vertical;
 }

 .verdict-card {
 margin-top: 16px;
 border: 1px solid var(--pw-accent, #c96342);
 background: var(--pw-surface, #fff);
 border-radius: 0;
 padding: 16px;
 box-shadow: 0 4px 12px rgba(201,99,66,0.12);
 }
 .verdict-head {
 display: flex;
 align-items: center;
 gap: 10px;
 flex-wrap: wrap;
 margin-bottom: 8px;
 }
 .verdict-sym {
 font-size: 16px;
 font-weight: 700;
 font-family: 'SF Mono', Menlo, monospace;
 }
 .verdict-pill {
 padding: 4px 10px;
 border-radius: 0;
 font-size: 11px;
 font-weight: 700;
 letter-spacing: 0.05em;
 }
 .verdict-stars {
 color: #d97706;
 font-size: 12px;
 letter-spacing: 2px;
 }
 .verdict-rationale {
 font-size: 11px;
 line-height: 1.55;
 color: var(--pw-ink);
 margin-top: 6px;
 }

 .cli-log {
 flex: 1;
 overflow-y: auto;
 background: #1a1614;
 color: #e8e3d6;
 font-family: 'SF Mono', Menlo, Consolas, monospace;
 font-size: 11px;
 line-height: 1.55;
 padding: 10px 14px;
 min-height: 200px;
 }
 .cli-line {
 display: flex;
 gap: 8px;
 align-items: baseline;
 padding: 2px 0;
 white-space: nowrap;
 }
 .cli-ts { color: #6b6660; flex-shrink: 0; }
 .cli-tag { width: 14px; text-align: center; flex-shrink: 0; }
 .cli-agent {
 color: #c96342;
 font-weight: 600;
 flex-shrink: 0;
 }
 .cli-msg {
 flex: 1;
 overflow: hidden;
 text-overflow: ellipsis;
 white-space: pre-wrap;
 word-break: break-word;
 }
 .cli-line.muted { color: #6b6660; font-style: italic; }
</style>
