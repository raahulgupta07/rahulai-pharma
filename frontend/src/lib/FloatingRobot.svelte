<script lang="ts">
  import { onMount, onDestroy, tick } from 'svelte';

  // Single-tenant locked slug (matches RobotPanel precedent).
  let { slug = 'citypharma' }: { slug?: string } = $props();

  let open = $state(false);
  let atStatus = $state<any>(null);
  let training = $state(false);
  let logs = $state<{ i: number; ts: string; msg: string; table?: string }[]>([]);
  let logCursor = $state(-1);
  let _poll: any = null;
  let _pollMs = 0;
  let _logPoll: any = null;
  let _prevLive = false;
  let _userClosed = false;
  let consoleEl: HTMLDivElement | null = $state(null);

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  async function _j(url: string): Promise<any> {
    try { const r = await fetch(url, { headers: _h() }); return r.ok ? await r.json() : null; } catch { return null; }
  }

  async function streamLogs() {
    const d = await _j(`/api/projects/${slug}/auto-train/log?since=${logCursor}&limit=200`);
    const ev = (d?.events || []) as { i: number; ts: string; msg: string; table?: string }[];
    const fresh = ev.filter((e) => e.i > logCursor);
    if (fresh.length) {
      logs = [...logs, ...fresh].slice(-400);
      logCursor = fresh[fresh.length - 1].i;
      await tick();
      if (consoleEl) consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  }

  let trainErr = $state('');
  // Adaptive heartbeat — fast while training, slow while idle. Always runs so a
  // backgrounded tab self-heals on the next tick (no more frozen "training" state).
  function schedulePoll(live: boolean) {
    const ms = live ? 6000 : 20000;
    if (_poll && _pollMs === ms) return;
    if (_poll) clearInterval(_poll);
    _pollMs = ms;
    _poll = setInterval(loadStatus, ms);
  }

  async function loadStatus() {
    atStatus = await _j(`/api/projects/${slug}/auto-train/status`);
    const live = !!atStatus?.is_training;
    // self-heal: backend is the source of truth — never let the local flag stick.
    if (!live) training = false;
    if (live) {
      if (!_logPoll) _logPoll = setInterval(streamLogs, 3000);
      streamLogs();
      // auto-expand on training start (unless the user explicitly closed it this run)
      if (!_prevLive && !open && !_userClosed) open = true;
    } else {
      _userClosed = false;
      if (_logPoll && !open) { clearInterval(_logPoll); _logPoll = null; }
    }
    _prevLive = live;
    schedulePoll(live);
  }

  async function trainNow() {
    if (training || atStatus?.is_training) return;
    trainErr = '';
    training = true;
    const d = await _j(`/api/projects/${slug}/datasource?quality=false&preview=false`);
    const names = (d?.tables || []).map((t: any) => t.name);
    if (!names.length) { training = false; trainErr = 'no tables to train'; return; }
    if (!open) open = true;
    try {
      const r = await fetch(`/api/projects/${slug}/retrain?force=1`, {
        method: 'POST', headers: { ..._h(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ table_names: names, force: true }),
      });
      if (!r.ok) { training = false; trainErr = `start failed (${r.status})`; }
    } catch { training = false; trainErr = 'unreachable'; }
    setTimeout(loadStatus, 1200);
    if (!_logPoll) _logPoll = setInterval(streamLogs, 3000);
  }

  function toggleOpen() {
    open = !open;
    if (open) { streamLogs(); }
    else { _userClosed = !!atStatus?.is_training; if (_logPoll && !atStatus?.is_training) { clearInterval(_logPoll); _logPoll = null; } }
  }

  function rel(ts: string): string {
    try {
      const t = new Date((ts || '').replace(' ', 'T') + (/(Z|[+-]\d\d:?\d\d)$/.test(ts) ? '' : 'Z')).getTime();
      const s = Math.max(0, Math.round((Date.now() - t) / 1000));
      if (s < 60) return s + 's ago';
      if (s < 3600) return Math.round(s / 60) + 'm ago';
      if (s < 86400) return Math.round(s / 3600) + 'h ago';
      return Math.round(s / 86400) + 'd ago';
    } catch { return ''; }
  }

  const status = $derived(
    (training || atStatus?.is_training) ? 'training'
      : atStatus?.daemon?.enabled === false ? 'disabled'
      : 'watching'
  );
  const lastTrained = $derived.by(() => { const r = atStatus?.recent_runs?.[0]; return r?.finished_at ? rel(r.finished_at) : ''; });
  // live elapsed for the "what it's doing" callout
  let nowTs = $state(Date.now());
  const liveElapsed = $derived.by(() => {
    const s = atStatus?.active_run?.started_at;
    if (!s) return '';
    try {
      const t = new Date(s.replace(' ', 'T') + (/(Z|[+-]\d\d:?\d\d)$/.test(s) ? '' : 'Z')).getTime();
      const sec = Math.max(0, Math.round((nowTs - t) / 1000));
      const m = Math.floor(sec / 60), x = sec % 60;
      return m ? `${m}m ${x}s` : `${x}s`;
    } catch { return ''; }
  });
  // one-line callout next to the bubble — "what it's doing" / "trained when"
  const callout = $derived.by(() => {
    if (training || atStatus?.is_training) {
      const step = atStatus?.current_step || atStatus?.active_run?.current_step || 'working';
      return liveElapsed ? `${step} · ${liveElapsed}` : String(step);
    }
    const lr = atStatus?.last_run || atStatus?.recent_runs?.[0];
    if (lr?.finished_at) {
      const why = (lr.status && lr.status !== 'done') ? `${lr.status} ` : '';
      return `${why}trained ${rel(lr.finished_at)}`;
    }
    return 'Watching for new data';
  });
  const autoOn = $derived(atStatus?.daemon?.enabled !== false);
  const runMeta = $derived.by(() => {
    const st = atStatus?.is_training ? 'running' : (atStatus?.recent_runs?.[0]?.status || 'idle');
    const id = atStatus?.active_run?.id ?? atStatus?.recent_runs?.[0]?.id;
    return { st, id };
  });
  const dotColor = $derived(status === 'training' ? '#c96342' : status === 'disabled' ? '#c8c0b4' : '#0ecad4');

  function lineClass(msg: string): string {
    const m = (msg || '').toLowerCase();
    if (/✓|complete|trained|done|success/.test(msg) || /complete|success/.test(m)) return 'l-ok';
    if (/✗|error|fail|crash|abort/.test(msg) || /error|fail/.test(m)) return 'l-err';
    if (/🧹|purge|skip|warn|orphan/.test(msg) || /purge|skip|warn/.test(m)) return 'l-warn';
    return 'l-dim';
  }

  function _onVis() { if (typeof document !== 'undefined' && !document.hidden) loadStatus(); }
  let _tick: any = null;
  onMount(() => {
    loadStatus();
    schedulePoll(false);
    _tick = setInterval(() => { nowTs = Date.now(); }, 1000); // live elapsed clock
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', _onVis);
    if (typeof window !== 'undefined') window.addEventListener('focus', _onVis);
  });
  onDestroy(() => {
    if (_poll) clearInterval(_poll);
    if (_logPoll) clearInterval(_logPoll);
    if (_tick) clearInterval(_tick);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', _onVis);
    if (typeof window !== 'undefined') window.removeEventListener('focus', _onVis);
  });
</script>

<div class="fr-wrap">
  {#if open}
    <div class="fr-pop">
      <!-- compact header -->
      <div class="fr-head">
        <svg viewBox="0 0 32 28" width="20" height="18" class="fr-mini">
          <rect x="6" y="6" width="20" height="14" rx="3" fill="#c96342" />
          <rect x="10" y="10" width="4" height="4" rx="1" fill="#1a1414" class:fr-eye-on={status === 'watching'} />
          <rect x="18" y="10" width="4" height="4" rx="1" fill="#1a1414" class:fr-eye-on={status === 'watching'} />
          <line x1="16" y1="6" x2="16" y2="2" stroke="#c96342" stroke-width="2" stroke-linecap="round" />
          <circle cx="16" cy="2" r="1.6" fill="#c96342" />
        </svg>
        <span class="fr-badge b-{status}"><span class="fr-bdot" style="background:{dotColor}"></span>{status === 'training' ? 'TRAINING' : status === 'disabled' ? 'PAUSED' : 'WATCHING'}</span>
        {#if lastTrained}<span class="fr-last">· last train {lastTrained}</span>{/if}
        <span class="fr-sp"></span>
        <span class="fr-auto" class:fr-auto-off={!autoOn} title="auto-train daemon">⚡ auto {autoOn ? 'on' : 'off'}</span>
        <button class="fr-train" disabled={training || atStatus?.is_training} onclick={trainNow}>{(training || atStatus?.is_training) ? '⟳' : '▶ train'}</button>
        <button class="fr-close" onclick={toggleOpen} aria-label="close">✕</button>
      </div>

      <!-- log meta strip -->
      <div class="fr-meta">
        LIVE LOG
        {#if runMeta.id}· run #{runMeta.id}{/if}
        {#if atStatus?.is_training && atStatus?.current_step}· {atStatus.current_step}{/if}
        <span class="fr-meta-st st-{runMeta.st}">● {runMeta.st}</span>
      </div>
      {#if trainErr}<div class="fr-trainerr">⚠ {trainErr}</div>{/if}

      <!-- dark console -->
      <div class="fr-console" bind:this={consoleEl}>
        {#if !logs.length}
          <div class="fr-log-empty">no recent training activity — robot is watching for new data…</div>
        {:else}
          {#each logs as l (l.i)}
            <div class="fr-log {lineClass(l.msg)}"><span class="fr-ts">{l.ts}</span> {l.msg}</div>
          {/each}
        {/if}
        <div class="fr-cursor">▌</div>
      </div>
    </div>
  {/if}

  {#if !open}
    <button class="fr-callout co-{status}" onclick={toggleOpen} title="Open training console">
      <span class="co-dot" style="background:{dotColor}" class:co-pulse={status === 'training'}></span>
      <span class="co-lbl">{status === 'training' ? 'Training' : status === 'disabled' ? 'Paused' : 'CityAgent'}</span>
      <span class="co-txt">{callout}</span>
    </button>
  {/if}

  <button class="fr-bubble status-{status}" onclick={toggleOpen} title="Auto-train robot — {callout}">
    <svg viewBox="0 0 64 64" width="40" height="40" class="fr-svg">
      <rect x="14" y="14" width="36" height="26" rx="6" fill="#c96342" />
      <rect x="22" y="22" width="6" height="6" rx="1.5" fill="#1a1414" />
      <rect x="36" y="22" width="6" height="6" rx="1.5" fill="#1a1414" />
      <line x1="32" y1="14" x2="32" y2="9" stroke="#c96342" stroke-width="2.5" stroke-linecap="round" />
      <circle cx="32" cy="8" r="2.5" fill="#c96342" />
      <rect x="22" y="44" width="5" height="8" rx="1.5" fill="#c96342" />
      <rect x="30" y="44" width="5" height="8" rx="1.5" fill="#c96342" />
      <rect x="38" y="44" width="5" height="8" rx="1.5" fill="#c96342" />
    </svg>
    <span class="fr-dot" style="background:{dotColor}" class:fr-dot-pulse={status === 'training'}></span>
  </button>
</div>

<style>
  .fr-wrap { position: fixed; right: 20px; bottom: 20px; z-index: 9000; display: flex; flex-direction: column; align-items: flex-end; gap: 10px; font-family: var(--pw-font-body, system-ui); }

  /* collapsed bubble */
  .fr-bubble { position: relative; width: 58px; height: 58px; border-radius: 50%; background: #fff; border: 2px solid #c96342; cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 16px rgba(0,0,0,0.18); transition: transform 0.15s, box-shadow 0.15s; }
  .fr-bubble:hover { transform: translateY(-2px) scale(1.04); box-shadow: 0 6px 20px rgba(201,99,66,0.32); }
  .fr-bubble.status-training { animation: fr-bob 1.1s ease-in-out infinite; }
  .fr-svg { display: block; }
  .fr-dot { position: absolute; top: 4px; right: 4px; width: 11px; height: 11px; border-radius: 50%; border: 2px solid #fff; }
  .fr-dot-pulse { animation: fr-pulse 1s ease-in-out infinite; }

  /* always-on callout pill — "what the robot is doing" */
  .fr-callout { display: inline-flex; align-items: center; gap: 7px; max-width: 320px; padding: 7px 12px 7px 10px; border-radius: 16px; background: #fff; border: 1px solid var(--pw-border, #ece6d9); box-shadow: 0 3px 14px rgba(0,0,0,0.12); cursor: pointer; font-family: var(--pw-font-body, system-ui); transition: transform 0.15s, box-shadow 0.15s; animation: fr-rise 0.16s ease; }
  .fr-callout:hover { transform: translateY(-1px); box-shadow: 0 5px 18px rgba(201,99,66,0.22); }
  .fr-callout.co-training { border-color: #c96342; }
  .co-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .co-pulse { animation: fr-pulse 1s ease-in-out infinite; }
  .co-lbl { font-size: 11px; font-weight: 800; letter-spacing: 0.02em; color: var(--pw-ink, #3a352c); flex-shrink: 0; }
  .co-txt { font-size: 11px; color: var(--pw-muted, #8a8275); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .fr-trainerr { padding: 5px 11px; font-size: 10.5px; color: #ff8b7e; background: #1a1622; border-bottom: 1px solid #2c2638; }

  /* expanded console popover */
  .fr-pop { width: 440px; max-width: calc(100vw - 36px); background: #1d1926; border: 1px solid #3a3346; border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,0.4); overflow: hidden; animation: fr-rise 0.16s ease; }

  .fr-head { display: flex; align-items: center; gap: 7px; padding: 9px 10px; background: #221d2e; border-bottom: 1px solid #3a3346; }
  .fr-mini { display: block; flex-shrink: 0; }
  .fr-eye-on { animation: fr-pulse 1.4s ease-in-out infinite; }
  .fr-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 10px; font-weight: 800; letter-spacing: 0.06em; color: #e8e2f0; }
  .fr-bdot { width: 7px; height: 7px; border-radius: 50%; }
  .b-training .fr-bdot { animation: fr-pulse 1s ease-in-out infinite; }
  .fr-last { font-size: 10.5px; color: #9890a8; white-space: nowrap; }
  .fr-sp { flex: 1; }
  .fr-auto { font-size: 10px; font-weight: 700; color: #0ecad4; white-space: nowrap; }
  .fr-auto-off { color: #8a8398; }
  .fr-train { font-size: 10.5px; font-weight: 700; padding: 3px 9px; border-radius: 6px; border: 1px solid #c96342; background: #c96342; color: #fff; cursor: pointer; white-space: nowrap; }
  .fr-train:hover:not(:disabled) { background: #d97a5e; }
  .fr-train:disabled { opacity: 0.55; cursor: default; }
  .fr-close { width: 20px; height: 20px; border-radius: 50%; border: none; background: rgba(255,255,255,0.08); color: #b8b0c8; cursor: pointer; font-size: 10px; line-height: 1; display: flex; align-items: center; justify-content: center; }
  .fr-close:hover { background: rgba(201,99,66,0.3); color: #fff; }

  .fr-meta { padding: 5px 11px; font-size: 9.5px; font-weight: 700; letter-spacing: 0.05em; color: #8a8398; background: #1a1622; border-bottom: 1px solid #2c2638; display: flex; align-items: center; gap: 6px; }
  .fr-meta-st { margin-left: auto; font-size: 9.5px; }
  .st-running { color: #c96342; }
  .st-done { color: #4ec77a; }
  .st-error, .st-failed { color: #e05a4a; }
  .st-idle { color: #6b6478; }

  .fr-console { height: 300px; max-height: 50vh; overflow-y: auto; padding: 9px 11px; background: #16131a; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11.5px; line-height: 1.55; }
  .fr-console::-webkit-scrollbar { width: 6px; }
  .fr-console::-webkit-scrollbar-thumb { background: #3a3346; border-radius: 3px; }
  .fr-log { white-space: pre-wrap; word-break: break-word; }
  .fr-ts { color: #5d566e; margin-right: 6px; }
  .l-ok { color: #6fe09a; }
  .l-err { color: #ff8b7e; }
  .l-warn { color: #e6b35c; }
  .l-dim { color: #b3aac6; }
  .fr-log-empty { color: #6b6478; font-style: italic; font-size: 11px; padding: 6px 0; }
  .fr-cursor { color: #c96342; animation: fr-blink 1s steps(2) infinite; }

  @keyframes fr-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes fr-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
  @keyframes fr-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  @keyframes fr-blink { 0% { opacity: 1; } 50% { opacity: 0; } 100% { opacity: 1; } }

  @media (max-width: 640px) {
    .fr-wrap { right: 12px; bottom: 12px; }
    .fr-bubble { width: 50px; height: 50px; }
    .fr-pop { width: calc(100vw - 24px); }
  }
</style>
