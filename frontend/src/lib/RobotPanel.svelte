<script lang="ts">
  let {
    logs = [] as {text: string; done: boolean; ts?: number}[],
    isTraining = false,
    trainStatus = 'idle' as 'idle'|'training'|'done'|'error',
    trainStep = '',
    trainProgress = 0,
    autoTrainStatus = 'watching' as string,
  }: {
    logs?: {text: string; done: boolean; ts?: number}[];
    isTraining?: boolean;
    trainStatus?: 'idle'|'training'|'done'|'error';
    trainStep?: string;
    trainProgress?: number;
    autoTrainStatus?: string;
  } = $props();

  let expanded = $state(false);
  let logEl: HTMLDivElement;
  let unread = $state(0);
  let prevLen = $state(0);

  // ── Learning feed (polls backend for chat learning events) ──
  type FeedEvent = { ts: number; type: string; text: string };
  let feedEvents = $state<FeedEvent[]>([]);
  let lastFeedTs = $state(0);
  let feedTimer: ReturnType<typeof setInterval> | null = null;

  async function fetchLearningFeed() {
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      if (!token) return;
      const slug = 'citypharma';
      const since = lastFeedTs || (Date.now() / 1000 - 3600);
      const r = await fetch(`/api/projects/${slug}/learning-feed?since=${since}&limit=30`, {
        headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
      });
      if (!r.ok) return;
      const d = await r.json();
      const newEvts: FeedEvent[] = (d.events || []).filter((e: FeedEvent) => e.ts > lastFeedTs);
      if (newEvts.length > 0) {
        feedEvents = [...feedEvents, ...newEvts.reverse()].slice(-200);
        lastFeedTs = Math.max(...feedEvents.map(e => e.ts));
        if (!expanded) unread += newEvts.length;
        if (expanded && logEl) setTimeout(() => { logEl.scrollTop = logEl.scrollHeight; }, 30);
      }
    } catch { /* fail-soft */ }
  }

  $effect(() => {
    fetchLearningFeed();
    feedTimer = setInterval(fetchLearningFeed, 15_000);
    return () => { if (feedTimer) clearInterval(feedTimer); };
  });

  // ── Authoritative training status (same source as the pipeline strip:
  //    the dash_training_runs active run) so the robot NEVER disagrees with it.
  //    The log-based prop only sees the 3 global tail steps and goes "done"
  //    while the run is still on per-table steps — this fixes that. ──
  let srvTraining = $state<boolean | null>(null);
  let srvStep = $state('');
  async function fetchTrainStatus() {
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      if (!token) return;
      const slug = 'citypharma';
      const r = await fetch(`/api/projects/${slug}/auto-train/status`, {
        headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
      });
      if (!r.ok) return;
      const d = await r.json();
      srvTraining = !!d.is_training;
      srvStep = (d.active_run && d.active_run.current_step) || '';
    } catch { /* fail-soft */ }
  }
  $effect(() => {
    fetchTrainStatus();
    const t = setInterval(fetchTrainStatus, 5000);
    return () => clearInterval(t);
  });

  // ── Live training-step log (the missing piece) ──
  //   Streams dash_training_runs.logs (per-step + per-LLM-call lines) into the
  //   console body so the robot shows WHAT is happening during training, not
  //   just "TRAINING". Cursor = array index already seen; resets per run.
  type TrainEvent = { ts: number; type: string; text: string };
  let trainEvents = $state<TrainEvent[]>([]);
  let trainLogIdx = $state(0);
  let trainLogRun = $state<number | null>(null);
  let trainPollFast: ReturnType<typeof setInterval> | null = null;

  function _epochFromHHMMSS(ts: string): number {
    // logs store HH:MM:SS — synthesize a sortable epoch on today's date.
    try {
      const [h, m, s] = (ts || '').split(':').map((x) => parseInt(x, 10));
      if ([h, m, s].some((n) => Number.isNaN(n))) return 0;
      const d = new Date();
      d.setHours(h, m, s, 0);
      return d.getTime();
    } catch { return 0; }
  }

  async function fetchTrainLog() {
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      if (!token) return;
      const slug = 'citypharma';
      const r = await fetch(`/api/projects/${slug}/auto-train/log?since=${trainLogIdx}`, {
        headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
      });
      if (!r.ok) return;
      const d = await r.json();
      // New run started → reset feed + cursor.
      if (d.run_id && d.run_id !== trainLogRun) {
        trainLogRun = d.run_id;
        if (d.total !== undefined && d.events && d.events.length && d.events[0].i === 0) {
          trainEvents = [];
          trainLogIdx = 0;
        }
      }
      const evts: TrainEvent[] = (d.events || [])
        .map((e: any) => ({
          // prefer the absolute epoch (machine-tz correct) → fall back to HH:MM:SS
          ts: (e.tsabs && e.tsabs > 0) ? e.tsabs * 1000 : _epochFromHHMMSS(e.ts),
          type: 'train',
          text: String(e.msg ?? '').trim(),
        }))
        .filter((e: TrainEvent) => e.text);
      if (evts.length > 0) {
        trainEvents = [...trainEvents, ...evts].slice(-400);
        const maxI = Math.max(...d.events.map((e: any) => e.i));
        trainLogIdx = maxI + 1;
        if (!expanded) unread += evts.length;
        if (expanded && logEl) setTimeout(() => { logEl.scrollTop = logEl.scrollHeight; }, 30);
      }
    } catch { /* fail-soft */ }
  }
  $effect(() => {
    // Poll the training log fast (2s) only while training; idle otherwise.
    if (srvTraining) {
      if (!trainPollFast) {
        fetchTrainLog();
        trainPollFast = setInterval(fetchTrainLog, 2000);
      }
    } else {
      // One final fetch to capture the closing ━━━ done ━━━ line, then stop.
      if (trainPollFast) {
        fetchTrainLog();
        clearInterval(trainPollFast);
        trainPollFast = null;
      } else if (trainEvents.length === 0) {
        // idle on mount → load the LAST run's log so logs stay visible after
        // training finished (no more "Waiting for activity" once a run exists).
        fetchTrainLog();
      }
    }
    return () => { if (trainPollFast) { clearInterval(trainPollFast); trainPollFast = null; } };
  });
  // effective training flag — prefer the authoritative server value
  const effTrain = $derived(srvTraining === null ? isTraining : srvTraining);

  // Console body = live training-step log (/auto-train/log) + chat feed.
  // NOTE: the `logs` prop (cliLogs) is intentionally NOT merged here — it's a
  // second, messier training feed (settings-page step poller via dash-cli-log)
  // that caused duplicate + double-timestamp lines. `logs` still drives the
  // header trainStep/trainProgress props. Our /auto-train/log feed is the
  // single, clean training source for the body.
  const allLogs = $derived.by(() => {
    const train = trainEvents.map(e => ({ ts: e.ts || 0, text: e.text, type: 'train' }));
    const combined = [...train, ...feedEvents]
      .filter(l => l.text)
      .sort((a, b) => (a.ts || 0) - (b.ts || 0));
    return combined.slice(-400);
  });

  // ───────────────────────────────────────────────────────────────────────
  //  AGENT ACTIVITY — map each training-log line → a named agent + phase.
  //  Client-side mapping (no backend); tweak the regexes freely.
  // ───────────────────────────────────────────────────────────────────────
  type AgentDef = { id: string; name: string; phase: string };
  // Canonical pipeline order (drives the "pending" list).
  const PIPE_AGENTS: AgentDef[] = [
    { id: 'conductor',    name: 'Conductor',            phase: 'UPLOAD'  },
    { id: 'profiler',     name: 'Profiler',             phase: 'PROFILE' },
    { id: 'codex',        name: 'Codex Enricher',       phase: 'PROFILE' },
    { id: 'qa',           name: 'Q&A Generator',        phase: 'TRAIN'   },
    { id: 'analyst',      name: 'Analyst',              phase: 'TRAIN'   },
    { id: 'persona',      name: 'Persona Agent',        phase: 'TRAIN'   },
    { id: 'workflow',     name: 'Workflow Generator',   phase: 'TRAIN'   },
    { id: 'relationship', name: 'Relationship Mapper',  phase: 'TRAIN'   },
    { id: 'memory',       name: 'Auto-Memory Promoter', phase: 'TRAIN'   },
    { id: 'brain',        name: 'Brain Builder',        phase: 'TRAIN'   },
    { id: 'insights',     name: 'Proactive Insights',   phase: 'TRAIN'   },
    { id: 'triple',       name: 'Triple Extractor',     phase: 'GRAPH'   },
    { id: 'embed',        name: 'Embedder',             phase: 'VECTORS' },
  ];
  const PIPE_BY_ID: Record<string, AgentDef> = Object.fromEntries(PIPE_AGENTS.map(a => [a.id, a]));
  const CHAT_TEAM = ['Leader','Engineer','Researcher','Customer Strategist','Comparator','Diagnostician','Narrator','Validator','Visualizer','Smart Router'];

  function agentFor(text: string): { id: string; name: string; phase: string; action: string; isLlm: boolean } {
    const s = (text || '').toLowerCase();
    const clean = (text || '').replace(/^[✓✗⚠·]\s*/, '').trim();
    // model-call observer line — attach to whatever agent is currently working
    if (/llm\s*·|·\s*gemini|tok\s*·|extraction\s*·|\(\d+ calls\)/.test(s))
      return { id: 'llm', name: 'LLM', phase: '', action: clean, isLlm: true };
    const hit = (id: string, action?: string) => { const a = PIPE_BY_ID[id]; return { id, name: a.name, phase: a.phase, action: action || clean, isLlm: false }; };
    if (/training (done|complete)|━━━|all done|finished/.test(s)) return { id: 'done', name: 'Conductor', phase: 'LIVE', action: 'training complete', isLlm: false };
    if (/knowledge graph|triple/.test(s))                         return hit('triple');
    if (/vector|embed|backfill|knowledge index|reindex/.test(s))  return hit('embed');
    if (/codex/.test(s))                                          return hit('codex');
    if (/verified (with|vs) real data|sql error/.test(s))         return hit('analyst');
    if (/q&a|qa pair|experiment|eval/.test(s))                    return hit('qa');
    if (/persona/.test(s))                                        return hit('persona');
    if (/workflow/.test(s))                                       return hit('workflow');
    if (/relationship/.test(s))                                   return hit('relationship');
    if (/memor/.test(s))                                          return hit('memory');
    if (/domain|brain_fill|glossar/.test(s))                      return hit('brain');
    if (/insight/.test(s))                                        return hit('insights');
    if (/catalog|dimension|sample|profil|hierarchy/.test(s))      return hit('profiler');
    if (/upload|stag|\bload\b|promote|drift/.test(s))             return hit('conductor');
    return { id: '', name: '', phase: '', action: clean, isLlm: false };
  }

  let tab = $state<'log'|'agents'|'history'|'learn'|'chat'>('log');

  // machine-timezone formatting: clock = HH:MM:SS, date = "Thu, Jun 5 2026"
  function fmtClock(ms?: number): string {
    if (!ms) return '';
    try { return new Date(ms).toLocaleTimeString(undefined, { hour12: false }); } catch { return ''; }
  }
  function fmtDate(ms?: number): string {
    if (!ms) return '—';
    try { return new Date(ms).toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' }); } catch { return '—'; }
  }
  function dateKey(ms?: number): string {
    if (!ms) return '';
    try { const d = new Date(ms); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; } catch { return ''; }
  }

  // ── Retained log history (never deleted; partitioned by date) ──
  let histRuns = $state<any[]>([]);
  let histLoaded = $state(false);
  let histLoading = $state(false);
  async function loadHistory() {
    if (histLoading) return;
    histLoading = true;
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      const slug = 'citypharma';
      if (token) {
        const r = await fetch(`/api/projects/${slug}/auto-train/log-history?runs=50`, {
          headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
        });
        if (r.ok) { const d = await r.json(); histRuns = d.runs || []; histLoaded = true; }
      }
    } catch { /* fail-soft */ }
    histLoading = false;
  }
  $effect(() => { if (tab === 'history' && !histLoaded && !histLoading) loadHistory(); });

  // ── LEARN tab: backfill the FULL learning history once (the live poller only
  //    keeps the last hour). since=1 → epoch 1970 → endpoint returns everything. ──
  let learnLoaded = $state(false);
  async function loadLearnHistory() {
    if (learnLoaded) return;
    learnLoaded = true;
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      if (!token) { learnLoaded = false; return; }
      const slug = 'citypharma';
      const r = await fetch(`/api/projects/${slug}/learning-feed?since=1&limit=200`, {
        headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
      });
      if (r.ok) {
        const d = await r.json();
        const seen = new Set(feedEvents.map(e => e.ts + '|' + e.text));
        const add: FeedEvent[] = (d.events || []).filter((e: FeedEvent) => !seen.has(e.ts + '|' + e.text));
        feedEvents = [...add, ...feedEvents].sort((a, b) => a.ts - b.ts).slice(-300);
        if (feedEvents.length) lastFeedTs = Math.max(lastFeedTs, ...feedEvents.map(e => e.ts));
      } else { learnLoaded = false; }
    } catch { learnLoaded = false; }
  }
  $effect(() => { if (tab === 'learn' && !learnLoaded) loadLearnHistory(); });

  // ── CHAT tab: list of chat sessions; turns load lazily on expand ──
  type ChatSession = { session_id: string; first_message: string; created?: number; updated?: number };
  let chatSessions = $state<ChatSession[]>([]);
  let chatLoaded = $state(false);
  let chatLoading = $state(false);
  let expandedSession = $state<string | null>(null);
  let sessionMsgs = $state<Record<string, any[]>>({});
  let sessionLoading = $state<string | null>(null);
  function truncMsg(s: any): string {
    const t = String(s ?? '').replace(/\s+/g, ' ').trim();
    return t.length > 240 ? t.slice(0, 240) + '…' : t;
  }
  async function loadChatSessions() {
    if (chatLoading) return;
    chatLoading = true;
    try {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
      const slug = 'citypharma';
      if (token) {
        const r = await fetch(`/api/projects/${slug}/sessions?limit=40`, {
          headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
        });
        if (r.ok) { const d = await r.json(); chatSessions = d.sessions || []; chatLoaded = true; }
      }
    } catch { /* fail-soft */ }
    chatLoading = false;
  }
  async function toggleSession(sid: string) {
    if (expandedSession === sid) { expandedSession = null; return; }
    expandedSession = sid;
    if (!sessionMsgs[sid]) {
      sessionLoading = sid;
      try {
        const token = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
        const slug = 'citypharma';
        const r = await fetch(`/api/projects/${slug}/sessions/${encodeURIComponent(sid)}/messages`, {
          headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug }
        });
        if (r.ok) { const d = await r.json(); sessionMsgs = { ...sessionMsgs, [sid]: (d.messages || []) }; }
      } catch { /* fail-soft */ }
      sessionLoading = null;
    }
  }
  $effect(() => { if (tab === 'chat' && !chatLoaded && !chatLoading) loadChatSessions(); });

  // group runs by local calendar date (chronological ASCENDING — newest at the
  // bottom so auto-scroll-to-bottom lands on the latest line, like the LOG tab)
  const histByDate = $derived.by(() => {
    const groups: { _k: string; _ms: number; date: string; runs: any[] }[] = [];
    for (const run of histRuns) {
      const ms = (run.events?.[0]?.tsabs ? run.events[0].tsabs * 1000 : (run.started_epoch || 0) * 1000);
      const dk = dateKey(ms) || 'unknown';
      let g = groups.find(x => x._k === dk) as any;
      if (!g) { g = { _k: dk, _ms: ms, date: fmtDate(ms), runs: [] }; groups.push(g); }
      g.runs.push({
        ...run,
        _ms: ms,
        timeLabel: fmtClock(ms),
        enriched: (run.events || []).map((e: any) => {
          const a = agentFor(String(e.msg || ''));
          const ts = (e.tsabs && e.tsabs > 0) ? e.tsabs * 1000 : 0;
          return { ts, text: String(e.msg || ''), ...a };
        }),
      });
    }
    // oldest date first; within a date, oldest run first → newest run at the bottom
    groups.sort((a, b) => a._ms - b._ms);
    for (const g of groups) g.runs.sort((a: any, b: any) => a._ms - b._ms);
    return groups;
  });

  // date header for the live LOG (single run = one date)
  const logDate = $derived.by(() => {
    const first = trainEnriched.find(e => e.ts);
    return first ? fmtDate(first.ts) : '';
  });

  const trainEnriched = $derived.by(() =>
    trainEvents.map(e => { const a = agentFor(e.text); return { ts: e.ts, text: e.text, ...a }; })
  );

  // Phase-grouped log (consecutive lines sharing a phase form a group).
  const logGroups = $derived.by(() => {
    const groups: { phase: string; lines: any[] }[] = [];
    let lastPhase = 'TRAIN';
    for (const ev of trainEnriched) {
      const ph = ev.phase || lastPhase;
      lastPhase = ph;
      let g = groups[groups.length - 1];
      if (!g || g.phase !== ph) { g = { phase: ph, lines: [] }; groups.push(g); }
      g.lines.push(ev);
    }
    return groups;
  });

  // Live counters parsed from the latest observer line: "running $X (N calls)".
  const liveStats = $derived.by(() => {
    let cost = '', calls = '';
    for (let i = trainEnriched.length - 1; i >= 0; i--) {
      const m = (trainEnriched[i].text || '').match(/running\s*\$([\d.]+)\s*\((\d+)\s*calls\)/i);
      if (m) { cost = m[1]; calls = m[2]; break; }
    }
    const ts = trainEnriched.map(e => e.ts).filter(Boolean) as number[];
    let elapsed = '';
    if (ts.length >= 2) { const s = Math.max(0, Math.round((Math.max(...ts) - Math.min(...ts)) / 1000)); elapsed = `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`; }
    return { cost, calls, elapsed };
  });

  // Active agent = last non-LLM tagged event.
  const activeAgent = $derived.by(() => {
    for (let i = trainEnriched.length - 1; i >= 0; i--) {
      const e = trainEnriched[i];
      if (e.id && e.id !== 'llm') return e;
    }
    return null;
  });

  // Roster: working / done / pending / idle.
  const roster = $derived.by(() => {
    const seen = new Map<string, string>();   // id → last action
    for (const e of trainEnriched) { if (e.id && e.id !== 'llm' && e.id !== 'done') seen.set(e.id, e.action); }
    const workingId = effTrain ? (activeAgent?.id && activeAgent.id !== 'done' ? activeAgent.id : '') : '';
    const working: any[] = [];
    const done: any[] = [];
    for (const a of PIPE_AGENTS) {
      if (a.id === workingId) working.push({ ...a, action: seen.get(a.id) || activeAgent?.action || '' });
      else if (seen.has(a.id)) done.push({ ...a, action: seen.get(a.id) || '' });
    }
    const pending = PIPE_AGENTS.filter(a => a.id !== workingId && !seen.has(a.id));
    return { working, done, pending };
  });

  $effect(() => {
    const newLen = (logs || []).length;
    if (newLen > prevLen) {
      if (!expanded) unread += newLen - prevLen;
      prevLen = newLen;
    }
  });

  $effect(() => {
    if (expanded) {
      unread = 0;
      if (logEl) setTimeout(() => { logEl.scrollTop = logEl.scrollHeight; }, 30);
    }
  });

  $effect(() => {
    if (isTraining && logEl && expanded) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  });

  // HISTORY tab: jump to the latest line (bottom) once runs are rendered
  $effect(() => {
    if (tab === 'history' && expanded && histRuns.length > 0 && logEl) {
      setTimeout(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; }, 60);
    }
  });

  function toggle() {
    expanded = !expanded;
    if (expanded) unread = 0;
  }

  function fmtTs(ts?: number) {
    if (!ts) return '';
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
  }

  function agentColor(id: string): string {
    const ph = PIPE_BY_ID[id]?.phase;
    return ph === 'GRAPH' ? '#0ecad4' : ph === 'VECTORS' ? '#d4a0f5' : ph === 'PROFILE' ? '#7ab8f5' : ph === 'UPLOAD' ? '#6dc97a' : '#ffb84d';
  }

  function lineClass(text: string, type?: string) {
    if (type === 'memory' || type === 'evolve') return 'rl-mem';
    if (type === 'quality') return 'rl-ok';
    if (type === 'insight') return 'rl-warn';
    if (type === 'triple') return 'rl-triple';
    if (/error|fail|✗|✕/i.test(text)) return 'rl-err';
    if (/warn|warning|⚠/i.test(text)) return 'rl-warn';
    if (/✓|done|complete|success/i.test(text)) return 'rl-ok';
    if (/step|training|profile|catalog|analysis|Q&A|vector/i.test(text)) return 'rl-info';
    return '';
  }

  // Robot state → eye + body expression (effTrain = authoritative server status)
  const eyeState = $derived(
    effTrain ? 'spin' :
    trainStatus === 'done' ? 'check' :
    trainStatus === 'error' ? 'x' :
    autoTrainStatus === 'detected' ? 'blink' : 'pulse'
  );

  const bodyAnim = $derived(
    effTrain ? 'sway' :
    trainStatus === 'done' ? 'jump' :
    trainStatus === 'error' ? 'shake' :
    autoTrainStatus === 'detected' ? 'bounce' : 'bob'
  );

  const statusLabel = $derived(
    effTrain ? (srvStep ? `⟳ TRAINING · ${srvStep}` : '⟳ TRAINING') :
    trainStatus === 'done' ? '✓ DONE' :
    trainStatus === 'error' ? '✗ ERROR' :
    autoTrainStatus === 'detected' ? '● CHANGE' : '● WATCHING'
  );

  const statusColor = $derived(
    effTrain ? '#c96342' :
    trainStatus === 'done' ? '#2d8a4e' :
    trainStatus === 'error' ? '#e05a4a' :
    autoTrainStatus === 'detected' ? '#d4930e' : '#0ecad4'
  );
</script>

<!-- Floating robot panel — bottom-right -->
<div class="rp-wrap" class:rp-open={expanded}>

  <!-- Collapsed: robot icon bubble -->
  {#if !expanded}
    <button class="rp-bubble" onclick={toggle} aria-label="Open agent monitor">
      <!-- Pixel-art robot (Claude Code style — pure rects, no curves) -->
      <svg viewBox="0 0 32 36" width="38" height="42" class="rp-robot body-{bodyAnim}" shape-rendering="crispEdges">
        <!-- antenna -->
        <rect x="15" y="0" width="2" height="4" fill="#c96342"/>
        <rect x="14" y="3" width="4" height="3" fill="#c96342" class="ant-dot"/>
        <!-- head — solid coral block -->
        <rect x="4"  y="6"  width="24" height="18" fill="#c96342"/>
        <!-- head shading (darker top edge) -->
        <rect x="4"  y="6"  width="24" height="2"  fill="#b8553a"/>
        <!-- eyes -->
        {#if eyeState === 'spin'}
          <rect x="9"  y="12" width="5" height="5" fill="#1a1614" class="eye-spin-sq"/>
          <rect x="18" y="12" width="5" height="5" fill="#1a1614" class="eye-spin-sq" style="animation-delay:0.5s"/>
        {:else if eyeState === 'check'}
          <rect x="9"  y="13" width="5" height="4" fill="#6dc97a"/>
          <rect x="18" y="13" width="5" height="4" fill="#6dc97a"/>
        {:else if eyeState === 'x'}
          <rect x="9"  y="12" width="5" height="5" fill="#e05a4a" class="eye-blink"/>
          <rect x="18" y="12" width="5" height="5" fill="#e05a4a" class="eye-blink" style="animation-delay:0.3s"/>
        {:else if eyeState === 'blink'}
          <rect x="9"  y="12" width="5" height="5" fill="#d4930e" class="eye-blink"/>
          <rect x="18" y="12" width="5" height="5" fill="#d4930e" class="eye-blink" style="animation-delay:0.3s"/>
        {:else}
          <rect x="9"  y="12" width="5" height="5" fill="#1a1614" class="eye-pulse-sq"/>
          <rect x="18" y="12" width="5" height="5" fill="#1a1614" class="eye-pulse-sq" style="animation-delay:0.5s"/>
        {/if}
        <!-- body -->
        <rect x="6"  y="24" width="20" height="8" fill="#c96342"/>
        <rect x="6"  y="24" width="20" height="2" fill="#b8553a"/>
        <!-- arms (small side blocks) -->
        <rect x="0"  y="25" width="5" height="6" fill="#c96342"/>
        <rect x="27" y="25" width="5" height="6" fill="#c96342"/>
        <!-- legs -->
        <rect x="8"  y="32" width="6" height="4" fill="#c96342"/>
        <rect x="18" y="32" width="6" height="4" fill="#c96342"/>
      </svg>
      <!-- unread badge -->
      {#if unread > 0}
        <span class="rp-badge">{unread > 99 ? '99+' : unread}</span>
      {/if}
      <!-- status dot -->
      <span class="rp-status-dot" style="background:{statusColor}"></span>
    </button>

  {:else}
    <!-- Expanded panel -->
    <div class="rp-panel">
      <!-- Header -->
      <div class="rp-head">
        <div class="rp-head-left">
          <!-- Mini pixel robot -->
          <svg viewBox="0 0 32 36" width="24" height="28" class="rp-mini body-{bodyAnim}" shape-rendering="crispEdges">
            <rect x="15" y="0"  width="2" height="3"  fill="#c96342"/>
            <rect x="14" y="2"  width="4" height="3"  fill="#c96342" class="ant-dot"/>
            <rect x="4"  y="5"  width="24" height="18" fill="#c96342"/>
            <rect x="4"  y="5"  width="24" height="2"  fill="#b8553a"/>
            {#if eyeState === 'check'}
              <rect x="9"  y="13" width="5" height="4" fill="#6dc97a"/>
              <rect x="18" y="13" width="5" height="4" fill="#6dc97a"/>
            {:else if eyeState === 'spin'}
              <rect x="9"  y="12" width="5" height="5" fill="#1a1614" class="eye-spin-sq"/>
              <rect x="18" y="12" width="5" height="5" fill="#1a1614" class="eye-spin-sq" style="animation-delay:0.5s"/>
            {:else}
              <rect x="9"  y="12" width="5" height="5" fill="#1a1614" class="eye-pulse-sq"/>
              <rect x="18" y="12" width="5" height="5" fill="#1a1614" class="eye-pulse-sq" style="animation-delay:0.5s"/>
            {/if}
            <rect x="6"  y="23" width="20" height="8" fill="#c96342"/>
            <rect x="6"  y="23" width="20" height="2" fill="#b8553a"/>
            <rect x="0"  y="24" width="5"  height="6" fill="#c96342"/>
            <rect x="27" y="24" width="5"  height="6" fill="#c96342"/>
            <rect x="8"  y="31" width="6"  height="4" fill="#c96342"/>
            <rect x="18" y="31" width="6"  height="4" fill="#c96342"/>
          </svg>
          <div class="rp-head-info">
            <span class="rp-agent-name">CityAgent</span>
            <span class="rp-status-label" style="color:{statusColor}">{statusLabel}</span>
          </div>
        </div>
        <button class="rp-close" onclick={toggle} aria-label="Close">✕</button>
      </div>

      <!-- Live active-agent ticker -->
      {#if effTrain && (activeAgent || liveStats.calls)}
        <div class="rp-ticker">
          <span class="rp-tk-dot"></span>
          <span class="rp-tk-agent">{activeAgent?.name || 'working'}</span>
          {#if activeAgent?.action}<span class="rp-tk-act">{activeAgent.action}</span>{/if}
          <span class="rp-tk-meta">{#if liveStats.calls}{liveStats.calls} calls{/if}{#if liveStats.cost} · ${liveStats.cost}{/if}{#if liveStats.elapsed} · {liveStats.elapsed}{/if}</span>
        </div>
      {/if}

      <!-- Training progress bar -->
      {#if isTraining && trainProgress > 0}
        <div class="rp-progress">
          <div class="rp-prog-bar">
            <div class="rp-prog-fill" style="width:{Math.round(trainProgress/14*100)}%"></div>
          </div>
          <span class="rp-prog-label">{trainStep || `Step ${trainProgress}/14`}</span>
        </div>
      {/if}

      <!-- Thinking bubble when training but no logs yet -->
      {#if isTraining && logs.length === 0}
        <div class="rp-thinking">
          <span class="rp-shimmer">{srvStep || trainStep || 'Thinking…'}</span>
        </div>
      {/if}

      <!-- Body: LOG (phase-grouped) or AGENTS roster -->
      <div class="rp-logs" bind:this={logEl}>
        {#if tab === 'agents'}
          <!-- ── AGENTS ROSTER ── -->
          {#if roster.working.length}
            <div class="rp-rsec">WORKING ({roster.working.length})</div>
            {#each roster.working as a (a.id)}
              <div class="rp-ag rp-ag-on">
                <span class="rp-ag-dot"></span>
                <span class="rp-ag-name" style="color:{agentColor(a.id)}">{a.name}</span>
                <span class="rp-ag-act">{a.action}</span>
              </div>
            {/each}
          {/if}
          {#if roster.done.length}
            <div class="rp-rsec">DONE ({roster.done.length})</div>
            {#each roster.done as a (a.id)}
              <div class="rp-ag rp-ag-done">
                <span class="rp-ag-x">✓</span>
                <span class="rp-ag-name">{a.name}</span>
                <span class="rp-ag-act">{a.action}</span>
              </div>
            {/each}
          {/if}
          {#if roster.pending.length}
            <div class="rp-rsec">PENDING ({roster.pending.length})</div>
            {#each roster.pending as a (a.id)}
              <div class="rp-ag rp-ag-idle">
                <span class="rp-ag-o">○</span>
                <span class="rp-ag-name">{a.name}</span>
                <span class="rp-ag-act rp-muted">{a.phase.toLowerCase()}</span>
              </div>
            {/each}
          {/if}
          <div class="rp-rsec">IDLE — chat team ({CHAT_TEAM.length}+)</div>
          <div class="rp-idleteam">{CHAT_TEAM.join(' · ')}</div>
          {#if !effTrain && roster.done.length === 0}
            <div class="rp-empty">No training running. Upload data or retrain to watch agents work.</div>
          {/if}
        {:else if tab === 'history'}
          <!-- ── HISTORY (all retained runs, partitioned by date) ── -->
          {#if histLoading && histRuns.length === 0}
            <div class="rp-empty">Loading log history…</div>
          {:else if histRuns.length === 0}
            <div class="rp-empty">No training runs recorded yet.</div>
          {:else}
            {#each histByDate as day (day._k)}
              <div class="rp-datehdr">📅 {day.date}</div>
              {#each day.runs as run (run.run_id)}
                <div class="rp-runhdr">
                  <span class="rp-run-time">{run.timeLabel}</span>
                  <span class="rp-run-status rp-run-{run.status}">{run.status}</span>
                  <span class="rp-run-n">{run.total} events</span>
                </div>
                {#each run.enriched as ev, li (li)}
                  <div class="rp-line {ev.isLlm ? 'rl-llm' : lineClass(ev.text, 'train')}">
                    <span class="rp-ts">{ev.ts ? fmtClock(ev.ts) : ''}</span>
                    {#if ev.name && !ev.isLlm}<span class="rp-agent" style="color:{agentColor(ev.id)}">{ev.name}</span>{/if}
                    <span class="rp-msg">{ev.action || ev.text}</span>
                  </div>
                {/each}
              {/each}
            {/each}
          {/if}
        {:else if tab === 'learn'}
          <!-- ── LEARN (persistent auto-saved learnings feed) ── -->
          {#if feedEvents.length === 0}
            <div class="rp-empty">No learnings yet…<br/>Chat with the agent — saved learnings, insights & KG facts land here.</div>
          {:else}
            {#each feedEvents as ev, i (ev.ts + '_' + i)}
              <div class="rp-line {lineClass(ev.text, ev.type)}">
                <span class="rp-ts">{fmtTs(ev.ts ? ev.ts * (ev.ts < 1e12 ? 1000 : 1) : undefined)}</span>
                <span class="rp-msg">{ev.text}</span>
              </div>
            {/each}
          {/if}
        {:else if tab === 'chat'}
          <!-- ── CHAT (session history; turns expand lazily) ── -->
          {#if chatLoading && chatSessions.length === 0}
            <div class="rp-empty">Loading chat history…</div>
          {:else if chatSessions.length === 0}
            <div class="rp-empty">No chats yet.<br/>Ask the agent something — your conversations appear here.</div>
          {:else}
            {#each chatSessions as s (s.session_id)}
              <button class="rp-chatrow" class:on={expandedSession === s.session_id} onclick={() => toggleSession(s.session_id)}>
                <span class="rp-chat-ar">{expandedSession === s.session_id ? '▾' : '▸'}</span>
                <span class="rp-chat-q">{s.first_message}</span>
                <span class="rp-chat-t">{fmtClock((s.updated || s.created || 0) * 1000)}</span>
              </button>
              {#if expandedSession === s.session_id}
                {#if sessionLoading === s.session_id}
                  <div class="rp-line rp-muted"><span class="rp-ts">&nbsp;</span><span class="rp-msg">loading turns…</span></div>
                {:else if (sessionMsgs[s.session_id] || []).length === 0}
                  <div class="rp-line rp-muted"><span class="rp-ts">&nbsp;</span><span class="rp-msg">no messages</span></div>
                {:else}
                  {#each sessionMsgs[s.session_id] as m, mi (mi)}
                    <div class="rp-cmsg rp-cmsg-{m.role}">
                      <span class="rp-cmsg-ic">{m.role === 'user' ? '👤' : '🤖'}</span>
                      <span class="rp-cmsg-tx">{truncMsg(m.content)}</span>
                    </div>
                  {/each}
                {/if}
              {/if}
            {/each}
          {/if}
        {:else if logGroups.length > 0}
          <!-- ── LOG (phase-grouped) ── -->
          {#if logDate}<div class="rp-datehdr">📅 {logDate}</div>{/if}
          {#each logGroups as g, gi (g.phase + '_' + gi)}
            {@const _st = !effTrain ? 'done' : (gi === logGroups.length - 1 ? 'on' : 'done')}
            <div class="rp-phase rp-phase-{_st}">
              <span class="rp-phx">{_st === 'done' ? '✓' : '●'}</span>
              <span class="rp-phl" class:rp-shimmer={_st === 'on'}>{g.phase}</span>
              <span class="rp-phc">{g.lines.length}</span>
            </div>
            {#each g.lines as ev, li (li)}
              <div class="rp-line {ev.isLlm ? 'rl-llm' : lineClass(ev.text, 'train')}">
                <span class="rp-ts">{fmtTs(ev.ts)}</span>
                {#if ev.name && !ev.isLlm}<span class="rp-agent" style="color:{agentColor(ev.id)}">{ev.name}</span>{/if}
                <span class="rp-msg">{ev.action || ev.text}</span>
              </div>
            {/each}
          {/each}
          {#if isTraining}
            <div class="rp-line rp-thinking-inline">
              <span class="rp-ts">&nbsp;</span>
              <span class="rp-msg">
                <span class="rp-shimmer">{srvStep || trainStep || 'Thinking…'}</span>
              </span>
            </div>
          {/if}
        {:else if allLogs.length > 0}
          <!-- ── idle: chat learning feed ── -->
          {#each allLogs as log, i (i)}
            {@const text = (log as any).text ?? ''}
            {@const type = (log as any).type ?? ''}
            <div class="rp-line {lineClass(text, type)}">
              <span class="rp-ts">{fmtTs((log as any).ts ? (log as any).ts * ((log as any).ts < 1e12 ? 1000 : 1) : undefined)}</span>
              <span class="rp-msg">{text}</span>
            </div>
          {/each}
        {:else if effTrain}
          <div class="rp-empty">Training in progress…<br/>Step logs and model calls will stream here live.</div>
        {:else}
          <div class="rp-empty">Waiting for activity…<br/>Upload data to train, or chat with the agent — events appear here.</div>
        {/if}
      </div>

      <!-- Footer -->
      <div class="rp-foot">
        <div class="rp-tabs">
          <button class="rp-tab" class:on={tab === 'log'} onclick={() => tab = 'log'}>▸ LOG</button>
          <button class="rp-tab" class:on={tab === 'agents'} onclick={() => tab = 'agents'}>AGENTS{#if effTrain && roster.working.length} ({roster.done.length + roster.working.length}/{PIPE_AGENTS.length}){/if}</button>
          <button class="rp-tab" class:on={tab === 'learn'} onclick={() => tab = 'learn'}>LEARN{#if feedEvents.length} ({feedEvents.length}){/if}</button>
          <button class="rp-tab" class:on={tab === 'chat'} onclick={() => tab = 'chat'}>CHAT</button>
          <button class="rp-tab" class:on={tab === 'history'} onclick={() => tab = 'history'}>RUNS</button>
        </div>
        <span class="rp-foot-sp"></span>
        {#if effTrain}
          <span class="rp-foot-active">● training</span>
        {:else}
          <span class="rp-foot-idle">○ idle</span>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  /* ── Wrapper ── */
  .rp-wrap {
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    pointer-events: none;
  }
  .rp-wrap > * { pointer-events: auto; }

  /* ── Collapsed bubble ── */
  .rp-bubble {
    width: 56px;
    height: 60px;
    border-radius: var(--pw-radius-sm);
    background: transparent;
    border: none;
    box-shadow: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    transition: transform 0.1s;
    image-rendering: pixelated;
  }
  .rp-bubble:hover { transform: scale(1.06) translateY(-2px); }
  .rp-badge {
    position: absolute;
    top: -4px;
    right: -4px;
    background: #c96342;
    color: white;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 4px;
    border-radius: 8px;
    min-width: 16px;
    text-align: center;
    font-family: ui-monospace, monospace;
  }
  .rp-status-dot {
    position: absolute;
    bottom: 2px;
    right: 2px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 2px solid white;
    animation: rp-pulse 2s ease-in-out infinite;
  }

  /* ── Expanded panel ── */
  .rp-panel {
    width: 340px;
    max-height: 480px;
    background: white;
    border: 1px solid #e5ddd0;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(26,22,20,0.16), 0 2px 8px rgba(26,22,20,0.08);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    animation: rp-slidein 0.2s ease-out;
  }
  @keyframes rp-slidein {
    from { opacity: 0; transform: translateY(12px) scale(0.97); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
  }

  /* ── Header ── */
  .rp-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 12px;
    border-bottom: 1px solid #f0ebe0;
    background: #faf7f2;
    flex-shrink: 0;
  }
  .rp-head-left { display: flex; align-items: center; gap: 8px; }
  .rp-head-info { display: flex; flex-direction: column; gap: 1px; }
  .rp-agent-name { font-size: 12px; font-weight: 700; color: #1a1614; letter-spacing: 0.02em; }
  .rp-status-label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
  .rp-close {
    background: none; border: none; cursor: pointer;
    color: #877f74; font-size: 13px; padding: 2px 6px;
    border-radius: 4px; line-height: 1;
  }
  .rp-close:hover { background: #f0ebe0; color: #1a1614; }

  /* ── Progress ── */
  .rp-progress { padding: 8px 12px 4px; flex-shrink: 0; }
  .rp-prog-bar { height: 3px; background: #f0ebe0; border-radius: 2px; overflow: hidden; }
  .rp-prog-fill { height: 100%; background: #c96342; border-radius: 2px; transition: width 0.4s ease; }
  .rp-prog-label { font-size: 9.5px; color: #877f74; margin-top: 3px; display: block; font-family: ui-monospace, monospace; }

  /* ── Log feed ── */
  .rp-logs {
    flex: 1;
    overflow-y: auto;
    background: #0f0d0c;
    padding: 8px 10px;
    min-height: 200px;
    max-height: 340px;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 11px;
    line-height: 1.55;
    scrollbar-width: thin;
    scrollbar-color: #3a3228 transparent;
  }
  .rp-empty { color: #8a8070; font-style: italic; padding: 8px 0; line-height: 1.6; }
  .rp-line { display: flex; gap: 8px; padding: 2px 0; }
  .rp-ts { color: #c96342; flex-shrink: 0; min-width: 58px; font-weight: 600; }
  .rp-msg { color: #e8e3d6; word-break: break-word; flex: 1; }
  .rp-line.rl-ok     .rp-msg { color: #6dc97a; }
  .rp-line.rl-err    .rp-msg { color: #ff7960; }
  .rp-line.rl-warn   .rp-msg { color: #ffb84d; }
  .rp-line.rl-info   .rp-msg { color: #7ab8f5; }
  .rp-line.rl-mem    .rp-msg { color: #d4a0f5; }
  .rp-line.rl-triple .rp-msg { color: #0ecad4; }

  /* ── CHAT tab: session rows + lazy turns ── */
  .rp-chatrow {
    display: flex; align-items: baseline; gap: 6px; width: 100%;
    padding: 5px 10px; background: none; border: none; cursor: pointer;
    text-align: left; font: inherit; color: #cfc8ba;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .rp-chatrow:hover { background: rgba(255,255,255,0.04); }
  .rp-chatrow.on { background: rgba(201,99,66,0.12); color: #f2ead9; }
  .rp-chat-ar { color: #c96342; flex-shrink: 0; font-size: 10px; }
  .rp-chat-q { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rp-chat-t { flex-shrink: 0; color: #7d756a; font-size: 10px; font-variant-numeric: tabular-nums; }
  .rp-cmsg {
    display: flex; gap: 6px; padding: 3px 10px 3px 22px; align-items: flex-start;
    font-size: 11px; line-height: 1.4;
  }
  .rp-cmsg-ic { flex-shrink: 0; font-size: 10px; opacity: 0.85; }
  .rp-cmsg-tx { flex: 1; word-break: break-word; }
  .rp-cmsg-user .rp-cmsg-tx { color: #e8e3d6; }
  .rp-cmsg-assistant .rp-cmsg-tx { color: #a8d8b0; }

  /* ── Thinking dots ── */
  .rp-thinking {
    display: flex; gap: 4px; padding: 8px 12px;
    align-items: center; flex-shrink: 0;
  }
  .rp-thinking-inline { align-items: center; }

  /* ── Shimmer (ChatGPT-style active-phase sweep) ── */
  .rp-shimmer {
    background: linear-gradient(90deg, #877f74 0%, #877f74 35%, #f2ead9 50%, #877f74 65%, #877f74 100%);
    background-size: 200% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    color: transparent;
    animation: rp-shimmer 1.4s linear infinite;
  }
  /* shimmer overrides the active-phase flat color */
  .rp-phase-on .rp-phl.rp-shimmer { color: transparent; }
  @keyframes rp-shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }

  /* ── Footer ── */
  .rp-foot {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 12px;
    border-top: 1px solid #1e1b18;
    background: #0f0d0c;
    font-size: 9.5px;
    font-family: ui-monospace, monospace;
    flex-shrink: 0;
  }
  .rp-foot-sp { flex: 1; }
  .rp-foot-active { color: #c96342; font-weight: 700; animation: rp-pulse 1s ease-in-out infinite; }
  .rp-foot-idle   { color: #3a3228; }

  /* ── Footer tabs ── */
  .rp-tabs { display: flex; gap: 4px; }
  .rp-tab {
    background: none; border: none; cursor: pointer;
    color: #6a6258; font-size: 9.5px; font-weight: 700; letter-spacing: 0.06em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 4px;
    font-family: ui-monospace, monospace;
  }
  .rp-tab:hover { color: #e8e3d6; }
  .rp-tab.on { color: #c96342; background: rgba(201,99,66,0.14); }

  /* ── Live ticker ── */
  .rp-ticker {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    padding: 6px 12px; background: #14110f; border-bottom: 1px solid #1e1b18;
    font-family: ui-monospace, monospace; font-size: 10px; flex-shrink: 0;
  }
  .rp-tk-dot { width: 7px; height: 7px; border-radius: 50%; background: #c96342; animation: rp-pulse 1s ease-in-out infinite; flex-shrink: 0; }
  .rp-tk-agent { color: #ffb84d; font-weight: 700; letter-spacing: 0.03em; }
  .rp-tk-act { color: #cfc8ba; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rp-tk-meta { color: #6a6258; flex-shrink: 0; }

  /* ── Agent-tagged log line ── */
  .rp-agent { flex-shrink: 0; font-weight: 700; min-width: 76px; max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rp-line.rl-llm .rp-msg { color: #5f7a8a; }

  /* ── Phase header ── */
  .rp-phase {
    display: flex; align-items: center; gap: 7px;
    margin: 6px 0 2px; padding: 2px 0;
    font-size: 9.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    border-bottom: 1px dashed #221e1a;
  }
  .rp-phx { font-size: 10px; }
  .rp-phase-done .rp-phx { color: #3fae5a; }
  .rp-phase-done .rp-phl { color: #6a8f72; }
  .rp-phase-on .rp-phx { color: #c96342; animation: rp-pulse 1s ease-in-out infinite; }
  .rp-phase-on .rp-phl { color: #ffb84d; }
  .rp-phl { flex: 1; }
  .rp-phc { color: #4a4438; font-weight: 600; }

  /* ── Agent roster ── */
  .rp-rsec {
    color: #6a6258; font-size: 9px; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; margin: 8px 0 4px; padding-bottom: 2px;
    border-bottom: 1px solid #1e1b18;
  }
  .rp-rsec:first-child { margin-top: 0; }
  .rp-ag { display: flex; align-items: center; gap: 7px; padding: 3px 0; font-size: 11px; }
  .rp-ag-dot { width: 7px; height: 7px; border-radius: 50%; background: #c96342; animation: rp-pulse 1s ease-in-out infinite; flex-shrink: 0; }
  .rp-ag-x { color: #3fae5a; flex-shrink: 0; width: 7px; text-align: center; }
  .rp-ag-o { color: #3a3228; flex-shrink: 0; width: 7px; text-align: center; }
  .rp-ag-name { font-weight: 700; flex-shrink: 0; min-width: 96px; }
  .rp-ag-done .rp-ag-name { color: #9a948a; }
  .rp-ag-idle .rp-ag-name { color: #5a544c; }
  .rp-ag-act { color: #877f74; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rp-ag-on .rp-ag-act { color: #cfc8ba; }
  .rp-muted { color: #4a4438; font-style: italic; }
  .rp-idleteam { color: #4a4438; font-size: 10px; line-height: 1.5; padding: 2px 0 6px; }

  /* ── Date / run headers (history + live date partition) ── */
  .rp-datehdr {
    color: #ffb84d; font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
    margin: 10px 0 4px; padding-bottom: 3px; border-bottom: 1px solid #2a241e;
    position: sticky; top: -8px; background: #0f0d0c; z-index: 1;
  }
  .rp-datehdr:first-child { margin-top: 0; }
  .rp-runhdr {
    display: flex; align-items: center; gap: 7px; margin: 6px 0 2px;
    font-size: 9.5px; font-family: ui-monospace, monospace;
  }
  .rp-run-time { color: #c96342; font-weight: 700; }
  .rp-run-status { text-transform: uppercase; letter-spacing: 0.05em; padding: 0 5px; border-radius: 3px; font-weight: 700; }
  .rp-run-done { color: #6dc97a; background: rgba(109,201,122,0.12); }
  .rp-run-running, .rp-run-queued { color: #ffb84d; background: rgba(255,184,77,0.12); }
  .rp-run-failed, .rp-run-error { color: #ff7960; background: rgba(255,121,96,0.12); }
  .rp-run-n { color: #4a4438; }

  /* ── Robot animations ── */
  .rp-robot, .rp-mini { display: block; }
  .body-bob   { animation: rp-bob   3s ease-in-out infinite; }
  .body-bounce { animation: rp-bounce 0.8s ease-in-out infinite; }
  .body-sway  { animation: rp-sway  1s ease-in-out infinite; }
  .body-jump  { animation: rp-jump  0.6s ease-out forwards; }
  .body-shake { animation: rp-shake 0.4s ease-in-out 3; }
  @keyframes rp-bob    { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-3px)} }
  @keyframes rp-bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-5px)} }
  @keyframes rp-sway   { 0%,100%{transform:translateX(0)} 50%{transform:translateX(2px)} }
  @keyframes rp-jump   { 0%{transform:translateY(0)} 40%{transform:translateY(-8px)} 100%{transform:translateY(0)} }
  @keyframes rp-shake  { 0%{transform:translateX(0)} 25%{transform:translateX(-3px)} 75%{transform:translateX(3px)} 100%{transform:translateX(0)} }

  /* Pixel eye animations */
  .eye-pulse-sq { animation: rp-pulse 2.4s ease-in-out infinite; }
  .eye-blink    { animation: rp-blink 0.5s steps(1) infinite; }
  .eye-spin-sq  { animation: rp-flash 0.4s steps(1) infinite; }
  .ant-dot      { animation: rp-pulse 2s ease-in-out infinite; }
  @keyframes rp-pulse { 0%,100%{opacity:1} 50%{opacity:0.25} }
  @keyframes rp-blink { 0%,49%{opacity:1} 50%,100%{opacity:0} }
  @keyframes rp-flash { 0%,49%{fill:#c96342} 50%,100%{fill:#1a1614} }
</style>
