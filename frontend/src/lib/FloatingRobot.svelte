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
    // ── state-change reactions (track transitions across polls) ──
    reactToTransition(live);
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
  // ── Rich callout catalog: friendly, varied "thinking" lines by state + the
  //    live action (current_step). Rotates so it never feels canned. ──
  // Map a raw pipeline step → a human line. First substring match wins.
  const STEP_MSG: [string, string][] = [
    ['infer_schema', 'Reading your columns 🔍'],
    ['profile', 'Reading your columns 🔍'],
    ['drift', 'Checking what changed since last time'],
    ['dedup', 'Tidying duplicate rows 🧹'],
    ['qa_generation', 'Writing practice questions ❓'],
    ['qa', 'Writing practice questions ❓'],
    ['relationship', 'Connecting the tables 🔗'],
    ['domain_knowledge', 'Learning pharma terms 💊'],
    ['glossary', 'Learning pharma terms 💊'],
    ['persona', 'Shaping my personality 🎭'],
    ['synthesis', 'Tying insights together'],
    ['semantic_layer', 'Building summary views ◆'],
    ['matview', 'Building summary views ◆'],
    ['knowledge_graph', 'Mapping the drug network 🕸️'],
    ['vector_backfill', 'Memorizing with embeddings 🧠'],
    ['embed', 'Memorizing with embeddings 🧠'],
    ['index', 'Filing everything away 🗂️'],
    ['finaliz', 'Filing everything away 🗂️ (almost done)'],
    ['scope', 'Setting my boundaries 🛡️'],
    ['codex', 'Polishing query logic'],
    ['eval', 'Grading myself ✅'],
  ];
  function stepMessage(step: string): string {
    const s = (step || '').toLowerCase();
    for (const [k, m] of STEP_MSG) if (s.includes(k)) return m;
    return '';
  }
  const IDLE_MSGS = ['Watching for new data…', 'All trained — standing by 🫡', 'Ask me anything 💬', "I'll ping you when data changes"];
  const TRAIN_MSGS = ['Learning your catalog 📚', 'Crunching the data…', 'Training in progress ⚡'];
  const DONE_MSGS = ['Done! Fresh knowledge loaded 🚀', 'All caught up ✓', 'Ready — ask me something 🎉'];
  const ERR_MSGS = ['Hit a snag — training failed ⚠', "Couldn't finish · tap for details"];
  const PAUSE_MSGS = ['Auto-train off — Zzz 😴', 'Sleeping · train manually anytime'];
  // rotation index — changes ~every 12s (nowTs ticks each second)
  const rot = $derived(Math.floor(nowTs / 12000));
  const pickRot = (arr: string[]) => arr[((rot % arr.length) + arr.length) % arr.length];

  let pokeMsg = $state('');  // transient playful line (hover / poke)

  // one-line callout next to the bubble. Priority: poke > done > error >
  // training(step) > paused > idle.
  const callout = $derived.by(() => {
    if (pokeMsg) return pokeMsg;
    if (celebrate) return pickRot(DONE_MSGS);
    if (isError) return pickRot(ERR_MSGS);
    if (isTrainingNow) {
      const step = atStatus?.current_step || atStatus?.active_run?.current_step || '';
      const base = stepMessage(step) || pickRot(TRAIN_MSGS);
      return liveElapsed ? `${base} · ${liveElapsed}` : base;
    }
    if (isPaused) return pickRot(PAUSE_MSGS);
    const lr = lastRun;
    if (lr?.finished_at && lr.status === 'done') {
      // alternate between idle chatter and a concrete "trained when"
      return (rot % 2 === 0) ? `Last trained ${rel(lr.finished_at)}` : pickRot(IDLE_MSGS);
    }
    return pickRot(IDLE_MSGS);
  });
  const autoOn = $derived(atStatus?.daemon?.enabled !== false);
  const runMeta = $derived.by(() => {
    const st = atStatus?.is_training ? 'running' : (atStatus?.recent_runs?.[0]?.status || 'idle');
    const id = atStatus?.active_run?.id ?? atStatus?.recent_runs?.[0]?.id;
    return { st, id };
  });

  // ── NEW: defensive task + attention derivation (older payloads lack these) ──
  const lastRun = $derived.by(() => atStatus?.last_run || atStatus?.recent_runs?.[0] || null);
  const isTrainingNow = $derived(training || !!atStatus?.is_training);
  const task = $derived.by(() => {
    const t = atStatus?.task;
    if (typeof t === 'string' && t) return t;
    // fallback when backend hasn't shipped `task` yet
    if (isTrainingNow) return 'training';
    if (atStatus?.daemon?.enabled === false) return 'paused';
    return 'idle';
  });
  const attention = $derived.by(() => {
    const a = atStatus?.attention;
    if (typeof a === 'number') return a;
    return (lastRun?.status === 'failed') ? 1 : 0;
  });
  const isError = $derived(lastRun?.status === 'failed' || task === 'error');
  const isPaused = $derived(!isTrainingNow && atStatus?.daemon?.enabled === false);

  // antenna / dot color reflects character mood
  const dotColor = $derived(
    isError ? '#e05a4a'
      : isTrainingNow ? '#c96342'
      : isPaused ? '#c8c0b4'
      : '#0ecad4'
  );

  function lineClass(msg: string): string {
    const m = (msg || '').toLowerCase();
    if (/✓|complete|trained|done|success/.test(msg) || /complete|success/.test(m)) return 'l-ok';
    if (/✗|error|fail|crash|abort/.test(msg) || /error|fail/.test(m)) return 'l-err';
    if (/🧹|purge|skip|warn|orphan/.test(msg) || /purge|skip|warn/.test(m)) return 'l-warn';
    return 'l-dim';
  }

  // ════════════════════════════════════════════════════════════════════
  //  CHARACTER STATE (animations, blink, look-around, celebration, callout)
  // ════════════════════════════════════════════════════════════════════
  let blinking = $state(false);     // eyes → thin lines briefly
  let lookDir = $state(0);          // -1 left, 0 center, +1 right (pupil shift)
  let celebrate = $state(false);    // one-shot DONE burst
  let poke = $state(false);         // click wiggle
  let calloutShow = $state(false);  // smart auto-show callout pill

  let _blinkT: any = null;
  let _lookT: any = null;
  let _celebT: any = null;
  let _calloutT: any = null;
  let _pokeT: any = null;

  // confetti dots for the celebration burst (fixed seed of offsets)
  const confetti = [
    { x: -18, c: '#c96342', d: 0 },
    { x: -8, c: '#0ecad4', d: 80 },
    { x: 2, c: '#e0a82e', d: 40 },
    { x: 12, c: '#4ec77a', d: 120 },
    { x: 20, c: '#c96342', d: 60 },
  ];

  function scheduleBlink() {
    if (_blinkT) clearTimeout(_blinkT);
    const next = 4000 + Math.random() * 2500; // every ~4-6.5s
    _blinkT = setTimeout(() => {
      blinking = true;
      setTimeout(() => { blinking = false; scheduleBlink(); }, 150);
    }, next);
  }
  function scheduleLook() {
    if (_lookT) clearTimeout(_lookT);
    const next = 6000 + Math.random() * 4000; // every ~6-10s
    _lookT = setTimeout(() => {
      lookDir = Math.random() < 0.5 ? -1 : 1;
      setTimeout(() => { lookDir = 0; scheduleLook(); }, 900);
    }, next);
  }

  // Show callout on any meaningful state change; auto-hide ~6s when idle,
  // keep persistently visible WHILE training.
  function flashCallout(persist: boolean) {
    calloutShow = true;
    if (_calloutT) { clearTimeout(_calloutT); _calloutT = null; }
    if (!persist) _calloutT = setTimeout(() => { calloutShow = false; }, 6000);
  }

  // Detect training→idle / error transitions across polls.
  function reactToTransition(live: boolean) {
    // training just STARTED
    if (!_prevLive && live) {
      flashCallout(true);
    }
    // training just ENDED
    if (_prevLive && !live) {
      const st = lastRun?.status;
      if (st === 'done') {
        celebrate = true;
        if (_celebT) clearTimeout(_celebT);
        _celebT = setTimeout(() => { celebrate = false; }, 3000);
      }
      flashCallout(false); // auto-hide ~6s after settling
    }
    // keep the pill up the whole time we're training
    if (live && !calloutShow) flashCallout(true);
    // surface errors as a (non-persistent) callout too
    if (!live && lastRun?.status === 'failed' && !calloutShow) flashCallout(false);
  }

  // Playful wave line on hover — transient (doesn't fight the real callout).
  let _hoverT: any = null;
  function hoverWave() {
    if (open) return;
    pokeMsg = isTrainingNow ? 'Working… but hi 😄' : (['👋 hey!', 'Boop! 😄', 'Yes? Tap me 🤖'][rot % 3]);
    calloutShow = true;
    if (_hoverT) clearTimeout(_hoverT);
    _hoverT = setTimeout(() => { pokeMsg = ''; if (!isTrainingNow) calloutShow = false; }, 1800);
  }

  // POKE on click — quick wiggle before opening the console.
  function pokeAndToggle() {
    poke = true;
    if (_pokeT) clearTimeout(_pokeT);
    _pokeT = setTimeout(() => { poke = false; }, 380);
    toggleOpen();
  }

  // ════════════════════════════════════════════════════════════════════
  //  DRAG TO REPOSITION (persist in localStorage; distinguish from click)
  // ════════════════════════════════════════════════════════════════════
  let pos = $state<{ right: number; bottom: number }>({ right: 20, bottom: 20 });
  let dragging = false;
  let _dragMoved = false;
  let _startX = 0, _startY = 0, _startRight = 0, _startBottom = 0;
  const DRAG_THRESH = 5; // px before it counts as a drag (not a click)

  function loadPos() {
    try {
      const raw = typeof localStorage !== 'undefined' ? localStorage.getItem('fr_pos') : null;
      if (raw) {
        const p = JSON.parse(raw);
        if (typeof p.right === 'number' && typeof p.bottom === 'number') pos = clampPos(p.right, p.bottom);
      }
    } catch { /* ignore */ }
  }
  function clampPos(right: number, bottom: number) {
    const W = typeof window !== 'undefined' ? window.innerWidth : 1200;
    const H = typeof window !== 'undefined' ? window.innerHeight : 800;
    const margin = 4, sz = 120; // keep the character on-screen
    return {
      right: Math.max(margin, Math.min(right, W - sz)),
      bottom: Math.max(margin, Math.min(bottom, H - sz)),
    };
  }
  function onPointerDown(e: PointerEvent) {
    // left button only; don't hijack the console popover
    if (e.button !== 0) return;
    dragging = true;
    _dragMoved = false;
    _startX = e.clientX; _startY = e.clientY;
    _startRight = pos.right; _startBottom = pos.bottom;
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    const dx = e.clientX - _startX;
    const dy = e.clientY - _startY;
    if (!_dragMoved && Math.hypot(dx, dy) < DRAG_THRESH) return;
    _dragMoved = true;
    pos = clampPos(_startRight - dx, _startBottom - dy);
  }
  function onPointerUp(e: PointerEvent) {
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
    const wasDrag = _dragMoved;
    dragging = false;
    if (wasDrag) {
      try { localStorage.setItem('fr_pos', JSON.stringify(pos)); } catch { /* ignore */ }
    } else {
      // treated as a click → poke + open console
      pokeAndToggle();
    }
  }
  function onResize() { pos = clampPos(pos.right, pos.bottom); }

  function _onVis() { if (typeof document !== 'undefined' && !document.hidden) loadStatus(); }
  let _tick: any = null;
  onMount(() => {
    loadPos();
    loadStatus();
    schedulePoll(false);
    scheduleBlink();
    scheduleLook();
    _tick = setInterval(() => { nowTs = Date.now(); }, 1000); // live elapsed clock
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', _onVis);
    if (typeof window !== 'undefined') { window.addEventListener('focus', _onVis); window.addEventListener('resize', onResize); }
  });
  onDestroy(() => {
    if (_poll) clearInterval(_poll);
    if (_logPoll) clearInterval(_logPoll);
    if (_tick) clearInterval(_tick);
    if (_blinkT) clearTimeout(_blinkT);
    if (_lookT) clearTimeout(_lookT);
    if (_celebT) clearTimeout(_celebT);
    if (_calloutT) clearTimeout(_calloutT);
    if (_pokeT) clearTimeout(_pokeT);
    if (_hoverT) clearTimeout(_hoverT);
    if (typeof window !== 'undefined') {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      window.removeEventListener('resize', onResize);
    }
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', _onVis);
    if (typeof window !== 'undefined') window.removeEventListener('focus', _onVis);
  });
</script>

<div class="fr-wrap" style="right:{pos.right}px; bottom:{pos.bottom}px;">
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

  {#if !open && calloutShow}
    <button class="fr-callout co-{status}" onclick={toggleOpen} title="Open training console">
      <span class="co-dot" style="background:{dotColor}" class:co-pulse={isTrainingNow}></span>
      <span class="co-lbl">{status === 'training' ? 'Training' : status === 'disabled' ? 'Paused' : 'CityAgent'}</span>
      <span class="co-txt">{callout}</span>
    </button>
  {/if}

  <!-- ════════ BARE ANIMATED CHARACTER (collapsed view) ════════ -->
  <!-- outer = drag/position target (NO bob, so transforms don't fight) -->
  <div
    class="fr-char"
    class:fr-dragging={dragging}
    role="button"
    tabindex="0"
    aria-label="Auto-train robot — {callout}"
    title="Auto-train robot — {callout}"
    onpointerdown={onPointerDown}
    onmouseenter={hoverWave}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pokeAndToggle(); } }}
  >
    <!-- inner = bob/celebration/poke animations (separate from drag transform) -->
    <div
      class="fr-inner state-{status}"
      class:fr-bob={!isTrainingNow}
      class:fr-bob-fast={isTrainingNow}
      class:fr-celebrate={celebrate}
      class:fr-poke={poke}
      class:fr-error={isError}
      class:fr-paused={isPaused}
    >
      <!-- F3 notification badge -->
      {#if attention > 0}
        <span class="fr-attn">{attention > 9 ? '9+' : attention}</span>
      {/if}

      <!-- confetti burst (one-shot celebration) -->
      {#if celebrate}
        <div class="fr-confetti">
          {#each confetti as c}
            <span class="fr-conf" style="left:calc(50% + {c.x}px); background:{c.c}; animation-delay:{c.d}ms;"></span>
          {/each}
        </div>
        <span class="fr-check">✓</span>
      {/if}

      <!-- error / paused floating glyphs -->
      {#if isError}<span class="fr-warn">⚠</span>{/if}
      {#if isPaused}<span class="fr-zzz">Zzz</span>{/if}

      <svg viewBox="0 0 64 78" width="60" height="73" class="fr-svg" class:fr-tint-err={isError}>
        <!-- indeterminate progress ring while training -->
        {#if isTrainingNow}
          <circle class="fr-ring" cx="32" cy="34" r="28" fill="none" stroke="#c96342"
                  stroke-width="2" stroke-linecap="round" stroke-dasharray="14 12" opacity="0.85" />
        {/if}

        <!-- spinning gear near the body while training -->
        {#if isTrainingNow}
          <g class="fr-gear" transform="translate(48 50)">
            <path d="M0,-6 L1.6,-5.5 L2.4,-7.2 L4,-6.4 L3.6,-4.6 L5.2,-3.6 L6.8,-4.4 L7.6,-2.8 L6.2,-1.6 L6.6,0 L8.2,0.6 L7.6,2.4 L5.8,2.2 L4.8,3.8 L5.6,5.4 L4,6 L3,4.6 L1.2,5 L0.6,6.8 L-1.2,6.2 L-1,4.4 L-2.6,3.4 L-4.2,4.2 L-5,2.6 L-3.6,1.4 L-4,-0.2 L-5.6,-0.8 L-5,-2.6 L-3.2,-2.4 L-2.2,-4 L-3,-5.6 L-1.4,-6.2 Z"
                  fill="#e0a82e" />
            <circle cx="0" cy="0" r="2.2" fill="#1d1926" />
          </g>
        {/if}

        <!-- body -->
        <rect x="14" y="20" width="36" height="26" rx="6" fill="#c96342" />

        <!-- eyes: open rects OR thin lines when blinking / sleeping -->
        {#if blinking || isPaused}
          <line x1="21" y1="31" x2="29" y2="31" stroke="#1a1414" stroke-width="2.4" stroke-linecap="round" />
          <line x1="35" y1="31" x2="43" y2="31" stroke="#1a1414" stroke-width="2.4" stroke-linecap="round" />
        {:else}
          <rect x="21" y="28" width="8" height="7" rx="2" fill="#fff" />
          <rect x="35" y="28" width="8" height="7" rx="2" fill="#fff" />
          <!-- pupils shift on look-around -->
          <circle cx={25 + lookDir * 1.8} cy="31.5" r="2" fill="#1a1414" class="fr-pupil" />
          <circle cx={39 + lookDir * 1.8} cy="31.5" r="2" fill="#1a1414" class="fr-pupil" />
        {/if}

        <!-- mouth: smile normally, flat when error -->
        {#if isError}
          <line x1="28" y1="41" x2="36" y2="41" stroke="#7a1f12" stroke-width="1.6" stroke-linecap="round" />
        {:else if celebrate}
          <path d="M27,40 Q32,45 37,40" fill="none" stroke="#7a1f12" stroke-width="1.8" stroke-linecap="round" />
        {:else}
          <path d="M28,41 Q32,43.5 36,41" fill="none" stroke="#7a1f12" stroke-width="1.4" stroke-linecap="round" />
        {/if}

        <!-- antenna + pulsing tip (color = state) -->
        <line x1="32" y1="20" x2="32" y2="14" stroke="#c96342" stroke-width="2.5" stroke-linecap="round" />
        <circle cx="32" cy="12" r="2.6" fill={dotColor} class="fr-antdot" class:fr-antdot-fast={isTrainingNow} />

        <!-- feet -->
        <rect x="22" y="46" width="5" height="8" rx="1.5" fill="#c96342" />
        <rect x="30" y="46" width="5" height="8" rx="1.5" fill="#c96342" />
        <rect x="38" y="46" width="5" height="8" rx="1.5" fill="#c96342" />

        <!-- ════ TASK-SPECIFIC PROPS ════ -->
        {#if task === 'training' || isTrainingNow}
          <!-- hard hat -->
          <g class="fr-hat">
            <path d="M16,16 Q32,2 48,16 Z" fill="#e0a82e" />
            <rect x="13" y="15" width="38" height="3.4" rx="1.7" fill="#caa024" />
            <rect x="30.4" y="6" width="3.2" height="9" rx="1.2" fill="#caa024" />
          </g>
        {:else if task === 'indexing' || task === 'finalizing'}
          <!-- magnifying glass -->
          <g class="fr-prop-mag">
            <circle cx="49" cy="16" r="6" fill="none" stroke="#5b6b7a" stroke-width="2.2" />
            <circle cx="49" cy="16" r="6" fill="#bfe3ea" opacity="0.4" />
            <line x1="53.5" y1="20.5" x2="58" y2="25" stroke="#5b6b7a" stroke-width="2.6" stroke-linecap="round" />
          </g>
        {:else if task === 'embedding'}
          <!-- 3 dots orbiting the head -->
          <g class="fr-orbit">
            <circle cx="32" cy="6" r="2" fill="#0ecad4" />
            <circle cx="48" cy="14" r="2" fill="#c96342" />
            <circle cx="16" cy="14" r="2" fill="#e0a82e" />
          </g>
        {:else if task === 'eval'}
          <!-- tiny clipboard with a check -->
          <g class="fr-prop-clip">
            <rect x="44" y="8" width="14" height="18" rx="2" fill="#f3ece1" stroke="#9a8f7e" stroke-width="1.2" />
            <rect x="48" y="6" width="6" height="3.5" rx="1.2" fill="#9a8f7e" />
            <path d="M47,17 l2.5,2.5 l4.5,-5" fill="none" stroke="#4ec77a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </g>
        {/if}

        <!-- sweat drop on error -->
        {#if isError}
          <path class="fr-sweat" d="M46,22 q-2,3 0,5 q2,-2 0,-5 Z" fill="#5db4e0" />
        {/if}
      </svg>

      <!-- soft ground shadow so it "sits" -->
      <span class="fr-shadow"></span>
    </div>
  </div>
</div>

<style>
  .fr-wrap { position: fixed; z-index: 9000; display: flex; flex-direction: column; align-items: flex-end; gap: 10px; font-family: var(--pw-font-body, system-ui); }

  /* ── bare animated character ── */
  .fr-char { position: relative; cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }
  .fr-char.fr-dragging { cursor: grabbing; }
  .fr-inner { position: relative; display: flex; align-items: center; justify-content: center; transition: filter 0.2s; }
  .fr-svg { display: block; overflow: visible; filter: drop-shadow(0 3px 5px rgba(0,0,0,0.28)); }
  .fr-svg.fr-tint-err { filter: drop-shadow(0 3px 5px rgba(0,0,0,0.28)) hue-rotate(-25deg) saturate(1.3); }

  /* ground shadow ellipse */
  .fr-shadow { position: absolute; bottom: -2px; left: 50%; transform: translateX(-50%); width: 38px; height: 8px; border-radius: 50%; background: rgba(20,15,12,0.32); filter: blur(3px); z-index: -1; }
  .fr-bob .fr-shadow { animation: fr-shadow-bob 3s ease-in-out infinite; }

  /* idle bob (applied to inner so it never fights the drag transform on .fr-char) */
  .fr-bob { animation: fr-bob 3s ease-in-out infinite; }
  .fr-bob-fast { animation: fr-bob 1.1s ease-in-out infinite; }
  .fr-paused { animation: fr-bob 5s ease-in-out infinite; }
  .fr-celebrate { animation: fr-celebrate 0.6s ease-in-out 2; }
  .fr-poke { animation: fr-poke 0.38s ease; }

  /* antenna tip pulse */
  .fr-antdot { animation: fr-pulse 1.6s ease-in-out infinite; }
  .fr-antdot-fast { animation: fr-pulse 0.7s ease-in-out infinite; }

  /* pupil look-around shift is via x attr + this smooths it */
  .fr-pupil { transition: cx 0.45s ease; }

  /* training accessories */
  .fr-ring { animation: fr-spin 1.4s linear infinite; transform-origin: 32px 34px; }
  .fr-gear { animation: fr-spin 2.2s linear infinite; transform-box: fill-box; transform-origin: center; }
  .fr-hat { transform-origin: 32px 16px; animation: fr-hat-pop 0.35s ease; }
  .fr-orbit { animation: fr-spin 3s linear infinite; transform-origin: 32px 14px; }
  .fr-prop-mag { animation: fr-mag 2.4s ease-in-out infinite; transform-origin: 49px 16px; }
  .fr-prop-clip { animation: fr-bob 2.5s ease-in-out infinite; }
  .fr-sweat { animation: fr-sweat 1.8s ease-in-out infinite; }

  /* F3 notification badge */
  .fr-attn { position: absolute; top: 2px; right: 2px; min-width: 16px; height: 16px; padding: 0 3px; border-radius: 9px; background: #e05a4a; color: #fff; font-size: 10px; font-weight: 800; line-height: 16px; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,0.35); z-index: 4; animation: fr-pulse 1.4s ease-in-out infinite; }

  /* celebration */
  .fr-confetti { position: absolute; inset: 0; pointer-events: none; z-index: 3; }
  .fr-conf { position: absolute; top: 26px; width: 5px; height: 5px; border-radius: 50%; animation: fr-confetti 0.9s ease-out forwards; }
  .fr-check { position: absolute; top: -6px; left: 50%; transform: translateX(-50%); color: #4ec77a; font-weight: 900; font-size: 18px; text-shadow: 0 1px 2px rgba(0,0,0,0.25); animation: fr-checkflash 0.9s ease-out; z-index: 3; }

  /* error / paused glyphs */
  .fr-warn { position: absolute; top: -8px; left: 50%; transform: translateX(-50%); color: #e0a82e; font-size: 16px; animation: fr-float 1.6s ease-in-out infinite; z-index: 3; }
  .fr-error { animation: fr-shake 0.5s ease-in-out, fr-bob 4s ease-in-out 0.5s infinite; }
  .fr-zzz { position: absolute; top: -10px; right: 6px; color: #9aa0a6; font-size: 13px; font-weight: 700; font-style: italic; animation: fr-zzz 2.6s ease-in-out infinite; z-index: 3; }

  /* ── always-on callout pill — "what the robot is doing" ── */
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
  @keyframes fr-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
  @keyframes fr-shadow-bob { 0%,100% { transform: translateX(-50%) scaleX(1); opacity: 0.32; } 50% { transform: translateX(-50%) scaleX(0.82); opacity: 0.2; } }
  @keyframes fr-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  @keyframes fr-blink { 0% { opacity: 1; } 50% { opacity: 0; } 100% { opacity: 1; } }
  @keyframes fr-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  @keyframes fr-celebrate { 0%,100% { transform: translateY(0); } 30% { transform: translateY(-10px); } 60% { transform: translateY(-3px); } }
  @keyframes fr-poke { 0% { transform: translateY(0) rotate(0); } 30% { transform: translateY(-6px) rotate(-6deg); } 60% { transform: translateY(-2px) rotate(5deg); } 100% { transform: translateY(0) rotate(0); } }
  @keyframes fr-shake { 0%,100% { transform: translateX(0); } 20% { transform: translateX(-3px); } 40% { transform: translateX(3px); } 60% { transform: translateX(-2px); } 80% { transform: translateX(2px); } }
  @keyframes fr-confetti { 0% { transform: translateY(0) scale(1); opacity: 1; } 100% { transform: translateY(-34px) scale(0.5); opacity: 0; } }
  @keyframes fr-checkflash { 0% { transform: translateX(-50%) scale(0.4); opacity: 0; } 30% { transform: translateX(-50%) scale(1.2); opacity: 1; } 100% { transform: translateX(-50%) scale(1); opacity: 0; } }
  @keyframes fr-float { 0%,100% { transform: translateX(-50%) translateY(0); } 50% { transform: translateX(-50%) translateY(-4px); } }
  @keyframes fr-zzz { 0% { transform: translateY(0); opacity: 0; } 30% { opacity: 1; } 100% { transform: translateY(-12px); opacity: 0; } }
  @keyframes fr-hat-pop { 0% { transform: translateY(-8px) scale(0.7); opacity: 0; } 100% { transform: translateY(0) scale(1); opacity: 1; } }
  @keyframes fr-mag { 0%,100% { transform: translate(0,0) rotate(0); } 50% { transform: translate(-2px,2px) rotate(-8deg); } }
  @keyframes fr-sweat { 0% { transform: translateY(0); opacity: 0; } 30% { opacity: 0.9; } 100% { transform: translateY(8px); opacity: 0; } }

  @media (prefers-reduced-motion: reduce) {
    .fr-bob, .fr-bob-fast, .fr-paused, .fr-celebrate, .fr-poke, .fr-error,
    .fr-antdot, .fr-antdot-fast, .fr-ring, .fr-gear, .fr-orbit, .fr-prop-mag,
    .fr-sweat, .fr-attn, .fr-shadow, .fr-warn, .fr-zzz { animation: none !important; }
  }

  @media (max-width: 640px) {
    .fr-svg { width: 52px; height: 64px; }
  }
</style>
