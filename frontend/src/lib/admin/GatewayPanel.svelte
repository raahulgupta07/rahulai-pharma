<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import RequestFlow from '$lib/RequestFlow.svelte';

  // ---- embed prop ----
  let { embedded = false } = $props();

  // ---- auth / super-admin gate ----
  let checking = $state(true);
  let isSuper = $state(false);

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  // ---- left-rail view (persisted in URL hash so refresh stays put) ----
  const _validViews = ['console', 'provision', 'overview', 'usage', 'docs', 'sandbox', 'keys'];
  function _viewFromHash(): string {
    if (typeof window === 'undefined') return 'overview';
    const h = (window.location.hash || '').replace(/^#/, '');
    // console retired from rail → fold any stale #console hash into overview
    if (h === 'console') return 'overview';
    return _validViews.includes(h) ? h : 'overview';
  }
  let view = $state(_viewFromHash());
  function nav(v: string) {
    view = v;
    if (typeof window !== 'undefined') {
      try { history.replaceState(null, '', `#${v}`); } catch { /* ignore */ }
    }
  }

  const RAIL = [
    { group: 'GATEWAY', items: [
      { id: 'overview', label: 'Overview', icon: 'gauge' },
      { id: 'provision', label: 'Outlet Keys', icon: 'store' },
    ] },
    { group: 'USAGE', items: [{ id: 'usage', label: 'Analytics', icon: 'chart' }] },
    { group: 'DEVELOPER', items: [
      { id: 'docs', label: 'Docs', icon: 'braces' },
    ] },
  ];
  const ICONS: Record<string, string> = {
    'gauge': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2" fill="currentColor"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>',
    'chat': '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
    'key': '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
    'store': '<path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9"/><path d="M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0"/>',
    'chart': '<path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="7"/><rect x="12" y="6" width="3" height="11"/><rect x="17" y="13" width="3" height="4"/>',
    'rocket': '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>',
    'lock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'braces': '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1"/><path d="M16 3h1a2 2 0 0 1 2 2v5a2 2 0 0 0 2 2 2 2 0 0 0-2 2v5a2 2 0 0 1-2 2h-1"/>',
    'activity': '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    'code': '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    'alert': '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    'shield': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    'timer': '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M9 2h6"/>',
    'grid': '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    'sliders': '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>'
  };


  const PAGE: Record<string, { title: string; sub: string }> = {
    console: { title: 'Console', sub: 'Live chat sandbox — test the real API with any key' },
    provision: { title: 'Outlet Keys', sub: 'One secure key per branch — generate, copy code + test' },
    overview: { title: 'Overview', sub: 'Gateway status, endpoints + quick snippets' },
    sandbox: { title: 'Chat Sandbox', sub: 'Live test the real /api/v1/chat/completions endpoint with a key' },
    keys: { title: 'Service Keys', sub: 'Mint, revoke + bind store-scoped API keys' },
    outlets: { title: 'Outlets', sub: 'All site_codes available for store binding' },
    usage: { title: 'Usage Analytics', sub: 'Calls + tokens per key over time' },
    docs: { title: 'Docs', sub: 'Auth · request/response schemas · streaming · error codes' },
  };

  // ---- base_url ----
  let baseUrl = $state('');
  // Canonical public origin (PUBLIC_URL env / AWS domain); falls back to browser origin.
  let pubOrigin = $state('');

  // ---- STATUS ----
  let status = $state<any>(null);
  let statusErr = $state('');
  async function loadStatus() {
    statusErr = '';
    try {
      const r = await fetch('/api/auth/apigw-status', { headers: authHeaders() });
      if (r.ok) status = await r.json();
      else statusErr = `status ${r.status}`;
    } catch (e) { statusErr = 'unreachable'; }
  }

  // ---- SERVICE KEYS ----
  let keys = $state<any[]>([]);
  let keysErr = $state('');
  let expandedKey = $state<string | number | null>(null);

  let mintOpen = $state(false);
  let mintName = $state('');
  let mintScope = $state('store');
  let mintOutlets = $state<string[]>([]);
  let mintBusy = $state(false);
  let mintedKey = $state('');
  let mintErr = $state('');

  // outlets
  let outlets = $state<string[]>([]);
  let outletMeta = $state<{ source_table?: string | null; updated_at?: string | null; row_count?: number | null } | null>(null);
  let outletFilter = $state('');
  let outletSearch = $state('');
  const outletChoices = $derived.by(() => {
    const f = outletFilter.trim().toLowerCase();
    return outlets
      .filter((o) => !mintOutlets.includes(o))
      .filter((o) => !f || o.toLowerCase().includes(f))
      .slice(0, 50);
  });
  const outletPage = $derived.by(() => {
    const f = outletSearch.trim().toLowerCase();
    return outlets.filter((o) => !f || o.toLowerCase().includes(f));
  });

  let outletsBusy = $state(false);
  async function loadOutlets() {
    outletsBusy = true;
    try {
      const r = await fetch('/api/auth/apigw-outlets', { headers: authHeaders() });
      if (r.ok) {
        const d = await r.json();
        outlets = d.outlets || [];
        outletMeta = { source_table: d.source_table ?? null, updated_at: d.updated_at ?? null, row_count: d.row_count ?? null };
      }
    } catch (e) { /* fail-soft */ }
    outletsBusy = false;
  }

  // Which outlets are covered by at least one active store-scoped key — drives
  // the "bound / unbound" role badge (pure client-side join, no extra fetch).
  const boundOutlets = $derived.by(() => {
    const s = new Set<string>();
    for (const k of keys) {
      if (!k.active) continue;
      for (const o of outletList(k)) s.add(o);
    }
    return s;
  });

  // relative-time for the freshness card ("2m ago")
  function relTime(iso?: string | null): string {
    if (!iso) return '';
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '';
    const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }
  function addOutlet(o: string) {
    const v = (o || '').trim();
    if (v && !mintOutlets.includes(v)) mintOutlets = [...mintOutlets, v];
    outletFilter = '';
  }
  function removeOutlet(o: string) { mintOutlets = mintOutlets.filter((x) => x !== o); }
  function addTypedOutlet() { if (outletFilter.trim()) addOutlet(outletFilter.trim()); }

  async function loadKeys() {
    keysErr = '';
    try {
      const r = await fetch('/api/auth/api-keys', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); keys = d.keys || []; }
      else keysErr = `keys ${r.status}`;
    } catch (e) { keysErr = 'unreachable'; }
  }
  async function revokeKey(name: string) {
    try {
      await fetch('/api/auth/api-key/revoke', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ service_account_name: name }),
      });
    } catch (e) { /* fail-soft */ }
    await loadKeys();
  }
  function resetMint() { mintName = ''; mintScope = 'store'; mintOutlets = []; outletFilter = ''; mintErr = ''; }
  async function mintKey() {
    if (!mintName.trim()) { mintErr = 'name required'; return; }
    if (mintScope === 'store' && mintOutlets.length === 0) { mintErr = 'add at least one outlet for store scope'; return; }
    mintErr = ''; mintBusy = true; mintedKey = '';
    try {
      const r = await fetch('/api/auth/api-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          service_account_name: mintName.trim(),
          store_ids: mintScope === 'store' ? mintOutlets : [],
          scope_mode: mintScope,
        }),
      });
      if (r.ok) { const d = await r.json(); mintedKey = d.api_key || d.key || ''; resetMint(); await loadKeys(); }
      else { let detail = ''; try { const e = await r.json(); detail = e.detail || ''; } catch {} mintErr = detail || `mint failed (${r.status})`; }
    } catch (e) { mintErr = 'unreachable'; }
    mintBusy = false;
  }
  // ---- CHAT SANDBOX (live multi-turn test of /api/v1/chat/completions) ----
  type SbStep = { label: string; icon: string };
  type SbTurn = { role: 'user' | 'assistant'; content: string; meta?: string; masked?: boolean; json?: string; open?: boolean; curl?: string; reqMsgs?: number; streamed?: boolean; steps?: SbStep[]; thinking?: boolean; stepsOpen?: boolean };
  // derive "Worked for Xs" — pull a seconds figure out of t.meta if present, else fall back to step count
  function workedLabel(t: SbTurn): string {
    const n = t.steps?.length || 0;
    const stepTxt = `${n} step${n === 1 ? '' : 's'}`;
    const m = (t.meta || '').match(/([\d.]+)\s*s\b/);
    if (m) return `Worked for ${m[1]}s · ${stepTxt}`;
    return `Worked · ${stepTxt}`;
  }
  // inspector: which assistant turn is shown in the right pane (-1 = latest)
  let sbSel = $state(-1);
  const sbInspect = $derived.by(() => {
    const asst = sbTurns.map((t, i) => ({ t, i })).filter(x => x.t.role === 'assistant' && (x.t.json || x.t.curl));
    if (!asst.length) return null;
    if (sbSel >= 0) { const hit = asst.find(x => x.i === sbSel); if (hit) return hit.t; }
    return asst[asst.length - 1].t;
  });
  let sbKey = $state('');
  let sbInput = $state('is paracetamol in stock at my branch?');
  let sbStream = $state(true);   // default ON so the live "thinking" strip shows
  let sbErr = $state('');
  let sbBusy = $state(false);
  let sbTurns = $state<SbTurn[]>([]);
  let sbBodyEl = $state<HTMLDivElement | undefined>();
  // auto-fill the key box with a freshly-minted key
  $effect(() => { if (mintedKey && !sbKey) sbKey = mintedKey; });
  // auto-scroll transcript to the latest message
  $effect(() => {
    void sbTurns.length; void sbBusy;
    if (sbBodyEl) requestAnimationFrame(() => { if (sbBodyEl) sbBodyEl.scrollTop = sbBodyEl.scrollHeight; });
  });

  // Heuristic: did the agent apply tier-2 masking in this reply? Phrases come
  // from our own scope sanitizer, so this is a reliable demo signal.
  function sbDetectMasked(t: string): boolean {
    return /availability only|not your (store|branch)|qty hidden|quantity hidden|no (price|qty|quantity)|cannot show .*other|masked/i.test(t);
  }

  function sbReset() { sbTurns = []; sbErr = ''; }

  async function sendSandbox() {
    sbErr = '';
    const key = sbKey.trim();
    if (!key) { sbErr = 'Paste a dash-key first (mint one under Service keys).'; return; }
    const msg = sbInput.trim();
    if (!msg || sbBusy) return;

    // conversation history = all turns with content so far + this new message
    const history = sbTurns.filter(t => t.content).map(t => ({ role: t.role, content: t.content }));
    history.push({ role: 'user', content: msg });

    sbTurns = [...sbTurns, { role: 'user', content: msg }, { role: 'assistant', content: '', open: false, steps: [], thinking: sbStream }];
    sbInput = '';
    sbBusy = true;
    const ai = sbTurns.length - 1;             // index of the assistant placeholder
    sbSel = -1;                                 // follow the latest turn in the inspector
    const t0 = Date.now();
    try {
      const body = { model: 'citypharma-analyst', messages: history, stream: sbStream };
      // exact reproducible curl for THIS turn (real request the inspector shows)
      sbTurns[ai].curl = `curl ${sbStream ? '-N ' : ''}${baseUrl}/chat/completions \\
  -H "Authorization: Bearer ${key.slice(0, 12)}…" \\
  -H "Content-Type: application/json" \\
  -d '${JSON.stringify(body)}'`;
      sbTurns[ai].reqMsgs = history.length;
      sbTurns[ai].streamed = sbStream;
      sbTurns = [...sbTurns];
      const r = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        // X-Agent-Steps: internal Console opt-in for the live activity strip.
        // External OpenAI clients never send it → answer-only v1 contract intact.
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}`, 'X-Agent-Steps': '1' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        let d = ''; try { const e = await r.json(); d = e.detail || JSON.stringify(e); } catch {}
        sbTurns[ai].content = `✗ HTTP ${r.status}${d ? ' · ' + d : ''}`;
        sbTurns = [...sbTurns]; sbBusy = false; return;
      }
      if (sbStream && r.body) {
        const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
        while (true) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let nl: number;
          while ((nl = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
            if (!line.startsWith('data:')) continue;
            const data = line.slice(5).trim();
            if (data === '[DONE]') continue;
            try {
              const j = JSON.parse(data);
              const delta = j.choices?.[0]?.delta || {};
              const step = delta.x_agent_step;   // non-standard: live activity strip
              if (step && step.label) {
                const arr = sbTurns[ai].steps || (sbTurns[ai].steps = []);
                if (!arr.length || arr[arr.length - 1].label !== step.label) arr.push(step);
                sbTurns = [...sbTurns];
              }
              const c = delta.content;
              if (c) { sbTurns[ai].thinking = false; sbTurns[ai].content += c; sbTurns = [...sbTurns]; }
            } catch {}
          }
        }
        sbTurns[ai].thinking = false;
        const _ns = (sbTurns[ai].steps || []).length;
        sbTurns[ai].json = '// streamed (SSE chat.completion.chunk frames)';
        sbTurns[ai].meta = `${((Date.now() - t0) / 1000).toFixed(1)}s · streamed${_ns ? ` · ${_ns} step${_ns > 1 ? 's' : ''}` : ''}`;
      } else {
        const j = await r.json();
        sbTurns[ai].content = j.choices?.[0]?.message?.content || JSON.stringify(j, null, 2);
        sbTurns[ai].json = JSON.stringify(j, null, 2);
        const u = j.usage;
        sbTurns[ai].meta = `${u?.total_tokens != null ? u.total_tokens + ' tok · ' : ''}${((Date.now() - t0) / 1000).toFixed(1)}s`;
      }
      sbTurns[ai].masked = sbDetectMasked(sbTurns[ai].content);
      sbTurns = [...sbTurns];
    } catch (e: any) {
      sbTurns[ai].content = '✗ ' + String(e?.message || e); sbTurns = [...sbTurns];
    }
    sbBusy = false;
  }

  function outletList(k: any): string[] {
    if (Array.isArray(k.store_ids) && k.store_ids.length) return k.store_ids;
    if (k.store_id) return [k.store_id];
    return [];
  }

  // ---- PROVISION (Outlets page: 1 key per outlet, table + copy + test) ----
  type ProvRow = { outlet: string; name: string; keyed: boolean; scope_mode: string; reqs: number };
  let provRows = $state<ProvRow[]>([]);

  // ---- OUTLET STATS (windowed reqs/errors/tokens/last_used per outlet) ----
  type OutletStat = { outlet: string; reqs: number; errors: number; tokens: number; last_used: string | null };
  let outletStatsDays = $state(7);
  let outletStatsMap = $state<Record<string, OutletStat>>({});
  let outletStatsErr = $state('');
  async function loadOutletStats() {
    outletStatsErr = '';
    try {
      const r = await fetch(`/api/admin/usage/outlet-stats?days=${outletStatsDays}`, { headers: authHeaders() });
      if (r.ok) {
        const d = await r.json();
        const m: Record<string, OutletStat> = {};
        for (const s of (d.stats || [])) m[s.outlet] = s;
        outletStatsMap = m;
      } else {
        outletStatsErr = `stats ${r.status}`;
      }
    } catch (e) { outletStatsErr = 'unreachable'; }
  }
  function setOutletStatsDays(n: number) { outletStatsDays = n; loadOutletStats(); }
  let provSummary = $state<{ total: number; keyed: number; missing: number }>({ total: 0, keyed: 0, missing: 0 });
  let provErr = $state('');
  let provBusy = $state(false);
  let provSearch = $state('');
  let provFilter = $state<'all' | 'keyed' | 'missing'>('all');
  let provExpanded = $state<string | null>(null);       // outlet whose detail row is open
  let provLang = $state<Record<string, string>>({});     // outlet → snippet lang
  // outlet → revealed plaintext key (cached in-session for copy + test)
  let provKeys = $state<Record<string, string>>({});
  let provRevealBusy = $state<string | null>(null);
  // per-outlet inline tester state
  let provTestQ = $state<Record<string, string>>({});
  let provTestRes = $state<Record<string, { content: string; meta: string; masked: boolean; busy: boolean }>>({});

  const provView = $derived.by(() => {
    const f = provSearch.trim().toLowerCase();
    return provRows.filter((r) => {
      if (provFilter === 'keyed' && !r.keyed) return false;
      if (provFilter === 'missing' && r.keyed) return false;
      if (f && !r.outlet.toLowerCase().includes(f)) return false;
      return true;
    });
  });

  async function loadProvision() {
    provErr = ''; provBusy = true;
    try {
      const r = await fetch('/api/auth/apigw-provision', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); provRows = d.rows || []; provSummary = d.summary || provSummary; }
      else provErr = `provision ${r.status}`;
    } catch (e) { provErr = 'unreachable'; }
    provBusy = false;
  }

  // mint keys for a set of outlets (empty/null = all missing). Caches returned plaintext.
  async function provGenerate(outlets: string[] | null, rotate = false) {
    provErr = ''; provBusy = true;
    try {
      const r = await fetch('/api/auth/apigw-provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ outlets: outlets || [], rotate }),
      });
      if (r.ok) {
        const d = await r.json();
        for (const x of (d.results || [])) provKeys[x.outlet] = x.api_key;
        provKeys = { ...provKeys };
        await loadProvision();
      } else { let detail = ''; try { detail = (await r.json()).detail || ''; } catch {} provErr = detail || `mint ${r.status}`; }
    } catch (e) { provErr = 'unreachable'; }
    provBusy = false;
  }
  function provGenAllMissing() { provGenerate(null, false); }
  function provGenOne(o: string) { provGenerate([o], false); }
  function provRotate(o: string) { provGenerate([o], true); }

  async function provReveal(o: string, name: string): Promise<string> {
    if (provKeys[o]) return provKeys[o];
    provRevealBusy = o;
    try {
      const r = await fetch(`/api/auth/apigw-key-reveal?name=${encodeURIComponent(name)}`, { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); provKeys[o] = d.api_key; provKeys = { ...provKeys }; }
    } catch (e) { /* fail-soft */ }
    provRevealBusy = null;
    return provKeys[o] || '';
  }

  async function revokeOutlet(name: string) {
    try {
      await fetch('/api/auth/api-key/revoke', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ service_account_name: name }),
      });
    } catch (e) { /* fail-soft */ }
    await loadProvision();
  }

  async function toggleProvRow(o: string, name: string) {
    if (provExpanded === o) { provExpanded = null; return; }
    provExpanded = o;
    if (!provLang[o]) { provLang[o] = 'php'; provLang = { ...provLang }; }
    await provReveal(o, name);   // pull plaintext so snippet + test are real
  }

  // per-outlet code snippet, real key inlined (falls back to a placeholder)
  function provSnippet(o: string, lang: string): string {
    const key = provKeys[o] || 'dash-key-XXXX';
    // Full deployable client: streams the answer AND the live agent-thinking
    // trace. Warm pharmacist formatting (Medicine·Salt·Stock·Price + Tip) is
    // produced server-side, so it lands automatically on every path.
    //   stream:true            -> SSE chat.completion.chunk frames
    //   header X-Agent-Steps:1  -> extra delta.x_agent_step:{label,icon} frames
    if (lang === 'curl') return `# outlet ${o} — streamed answer + live agent thinking
curl -N ${baseUrl}/chat/completions \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Steps: 1" \\
  -d '{"model":"citypharma-analyst","stream":true,
       "messages":[{"role":"user","content":"is paracetamol in stock at my branch?"}]}'
# SSE frames:
#   data: {"choices":[{"delta":{"content":"..."}}]}             <- answer tokens
#   data: {"choices":[{"delta":{"x_agent_step":{"label","icon"}}}]} <- live thinking
#   data: [DONE]`;
    if (lang === 'python') return `import json, sys, requests   # pip install requests

KEY, BASE = "${key}", "${baseUrl}"

resp = requests.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": f"Bearer {KEY}",
             "X-Agent-Steps": "1"},          # opt-in: live agent thinking
    json={"model": "citypharma-analyst", "stream": True,
          "messages": [{"role": "user", "content": "is paracetamol in stock at my branch?"}]},
    stream=True,
)

answer = ""
for line in resp.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data:"):
        continue
    data = line[5:].strip()
    if data == "[DONE]":
        break
    delta = json.loads(data)["choices"][0].get("delta", {})
    step = delta.get("x_agent_step")               # live thinking trace
    if step:
        print(f"  \\u27f3 {step.get('icon','')} {step.get('label','')}", file=sys.stderr)
    chunk = delta.get("content")                   # answer tokens
    if chunk:
        answer += chunk
        print(chunk, end="", flush=True)
# 'answer' holds the full formatted reply (Medicine·Salt·Stock·Price + Tip)`;
    if (lang === 'env') return `# outlet ${o}
CITYPHARMA_BASE=${baseUrl}
CITYPHARMA_KEY_${o.replace(/[^A-Za-z0-9]/g, '_')}=${key}
# streaming + live thinking: POST {"stream":true} + header  X-Agent-Steps: 1
# warm pharmacist formatting is applied server-side automatically`;
    // php (default) — runnable streaming consumer via CURLOPT_WRITEFUNCTION
    return `<?php // outlet ${o} — streamed answer + live agent thinking
$key  = "${key}";
$base = "${baseUrl}";

$answer = "";
$ch = curl_init("$base/chat/completions");
curl_setopt_array($ch, [
  CURLOPT_POST       => true,
  CURLOPT_HTTPHEADER => [
    "Authorization: Bearer $key",
    "Content-Type: application/json",
    "X-Agent-Steps: 1",                 // opt-in: live agent thinking
  ],
  CURLOPT_POSTFIELDS => json_encode([
    "model"    => "citypharma-analyst",
    "stream"   => true,
    "messages" => [["role" => "user", "content" => "is paracetamol in stock at my branch?"]],
  ]),
  CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use (&$answer) {
    static $buf = "";
    $buf .= $chunk;
    while (($nl = strpos($buf, "\\n")) !== false) {
      $line = trim(substr($buf, 0, $nl));  $buf = substr($buf, $nl + 1);
      if (strpos($line, "data:") !== 0) continue;
      $data = trim(substr($line, 5));
      if ($data === "[DONE]") continue;
      $delta = json_decode($data, true)["choices"][0]["delta"] ?? [];
      if (!empty($delta["x_agent_step"])) {            // live thinking
        $s = $delta["x_agent_step"];
        fwrite(STDERR, "  ⟳ " . ($s["icon"] ?? "") . " " . ($s["label"] ?? "") . "\\n");
      }
      if (isset($delta["content"])) {                  // answer tokens
        $answer .= $delta["content"];  echo $delta["content"];  flush();
      }
    }
    return strlen($chunk);
  },
]);
curl_exec($ch);  curl_close($ch);
// $answer holds the full formatted reply (Medicine·Salt·Stock·Price + Tip)`;
  }

  async function provTest(o: string, name: string) {
    const q = (provTestQ[o] || 'is paracetamol in stock at my branch?').trim();
    if (!q) return;
    const key = await provReveal(o, name);
    if (!key) { provTestRes[o] = { content: '✗ no key — generate first', meta: '', masked: false, busy: false }; provTestRes = { ...provTestRes }; return; }
    provTestRes[o] = { content: '', meta: '', masked: false, busy: true }; provTestRes = { ...provTestRes };
    const t0 = Date.now();
    try {
      const r = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
        body: JSON.stringify({ model: 'citypharma-analyst', messages: [{ role: 'user', content: q }], stream: false }),
      });
      if (!r.ok) { let d = ''; try { d = (await r.json()).detail || ''; } catch {} provTestRes[o] = { content: `✗ HTTP ${r.status}${d ? ' · ' + d : ''}`, meta: '', masked: false, busy: false }; provTestRes = { ...provTestRes }; return; }
      const j = await r.json();
      const content = j.choices?.[0]?.message?.content || JSON.stringify(j);
      const u = j.usage;
      provTestRes[o] = {
        content,
        meta: `${u?.total_tokens != null ? u.total_tokens + ' tok · ' : ''}${((Date.now() - t0) / 1000).toFixed(1)}s · 🛡 own-scope`,
        masked: sbDetectMasked(content),
        busy: false,
      };
      provTestRes = { ...provTestRes };
    } catch (e: any) {
      provTestRes[o] = { content: '✗ ' + String(e?.message || e), meta: '', masked: false, busy: false }; provTestRes = { ...provTestRes };
    }
  }

  // bulk export — all revealed keys as .env / CSV. Reveals any missing first.
  async function provExportAll(fmt: 'env' | 'csv') {
    provBusy = true;
    const keyed = provRows.filter((r) => r.keyed);
    // reveal any not-yet-cached keys
    await Promise.all(keyed.filter((r) => !provKeys[r.outlet]).map((r) => provReveal(r.outlet, r.name)));
    let body = '';
    if (fmt === 'env') {
      body = `CITYPHARMA_BASE=${baseUrl}\n` + keyed.map((r) =>
        `CITYPHARMA_KEY_${r.outlet.replace(/[^A-Za-z0-9]/g, '_')}=${provKeys[r.outlet] || ''}`).join('\n');
    } else {
      body = ['outlet,name,key,endpoint'].concat(keyed.map((r) =>
        `"${r.outlet}","${r.name}","${provKeys[r.outlet] || ''}","${baseUrl}/chat/completions"`)).join('\n');
    }
    const blob = new Blob([body], { type: fmt === 'csv' ? 'text/csv' : 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = fmt === 'csv' ? 'citypharma-outlet-keys.csv' : 'citypharma-outlets.env'; a.click();
    URL.revokeObjectURL(url);
    provBusy = false;
  }

  // ---- per-outlet CHAT DRAWER (reuses the sandbox chat engine, key auto-bound) ----
  let chatOutlet = $state<string | null>(null);
  let chatName = $state('');
  let chatKeyMissing = $state(false);
  async function openChat(outlet: string, name: string) {
    chatName = name; chatOutlet = outlet; chatKeyMissing = false;
    sbReset(); sbInput = 'is paracetamol in stock at my branch?';
    if (typeof document !== 'undefined') document.body.classList.add('gw-drawer-open');
    const key = await provReveal(outlet, name);
    if (key) { sbKey = key; } else { sbKey = ''; chatKeyMissing = true; }
  }
  function closeChat() {
    chatOutlet = null;
    if (typeof document !== 'undefined') document.body.classList.remove('gw-drawer-open');
  }

  // lightweight markdown → HTML for chat bubbles (bold, code, lists, line breaks)
  function renderMd(md: string): string {
    if (!md) return '';
    let html = md.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(?!\s)(.+?)(?!\s)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    const lines = html.split('\n');
    const out: string[] = [];
    let inList = false;
    const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
    // separator row: only pipes / dashes / colons / spaces, and has a dash
    const isSep = (l: string) => /^\s*\|?[\s:|-]+\|?\s*$/.test(l) && l.includes('-');
    const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      // GFM table: a row followed by a separator row
      if (isRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
        if (inList) { out.push('</ul>'); inList = false; }
        const header = cells(line);
        i += 2;
        const body: string[][] = [];
        while (i < lines.length && isRow(lines[i]) && !isSep(lines[i])) { body.push(cells(lines[i])); i++; }
        let t = '<table class="gw-md-table"><thead><tr>' + header.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>';
        for (const r of body) t += '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>';
        t += '</tbody></table>';
        out.push(t);
        continue;
      }
      const m = line.match(/^\s*[-*]\s+(.*)$/);
      const n = line.match(/^\s*\d+\.\s+(.*)$/);
      if (m || n) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push(`<li>${(m ? m[1] : n![1])}</li>`);
      } else {
        if (inList) { out.push('</ul>'); inList = false; }
        if (line.trim()) out.push(`<p>${line}</p>`);
      }
      i++;
    }
    if (inList) out.push('</ul>');
    return out.join('');
  }

  // ---- RATE LIMIT ----
  let rateCap = $state(60);
  let rateErr = $state('');
  let rateSaved = $state(false);
  let rateBusy = $state(false);
  async function loadConfig() {
    rateErr = '';
    try {
      const r = await fetch('/api/auth/apigw-config', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); rateCap = d.rate_per_min ?? 60; }
      else rateErr = `config ${r.status}`;
    } catch (e) { rateErr = 'unreachable'; }
  }
  async function saveConfig() {
    rateBusy = true; rateErr = '';
    try {
      const r = await fetch('/api/auth/apigw-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ rate_per_min: Number(rateCap) || 60 }),
      });
      if (r.ok) { rateSaved = true; setTimeout(() => rateSaved = false, 1800); }
      else rateErr = `save ${r.status}`;
    } catch (e) { rateErr = 'unreachable'; }
    rateBusy = false;
  }

  // ---- USAGE ----
  let usageDays = $state(7);
  let usage = $state<any>(null);
  let usageErr = $state('');
  async function loadUsage() {
    usageErr = '';
    try {
      const r = await fetch(`/api/auth/apigw-usage?days=${usageDays}`, { headers: authHeaders() });
      if (r.ok) usage = await r.json();
      else usageErr = `usage ${r.status}`;
    } catch (e) { usageErr = 'unreachable'; }
  }
  function setUsageDays(n: number) { usageDays = n; loadUsage(); }
  const sparkBars = $derived.by(() => {
    const daily = Array.isArray(usage?.daily) ? usage.daily : [];
    const vals = daily.map((d: any) => Number(d?.calls) || 0);
    const max = Math.max(1, ...vals);
    return daily.map((d: any) => ({ label: d?.date || '', calls: Number(d?.calls) || 0, pct: Math.round(((Number(d?.calls) || 0) / max) * 100) }));
  });
  const byKeyRows = $derived.by(() => Array.isArray(usage?.by_key) ? usage.by_key : []);
  // service_account → {calls,last} for enriching the Service Keys cards
  const keyUsage = $derived.by(() => {
    const m: Record<string, { calls: number; last: string | null }> = {};
    for (const r of byKeyRows) {
      const id = r?.service_account; if (!id) continue;
      m[id] = { calls: Number(r.calls) || 0, last: r.last ?? null };
    }
    return m;
  });
  // plain-words access tier for a key (derived from scope + binding)
  function keyTier(k: any): { label: string; cls: string } {
    if (k.scope_mode === 'global') return { label: 'global · full data + aggregates', cls: 'gw-tier-g' };
    const n = outletList(k).length;
    if (n === 0) return { label: 'reference only · no stock', cls: 'gw-tier-3' };
    return { label: `tier-1 own (${n} outlet${n > 1 ? 's' : ''}) · tier-2 cross`, cls: 'gw-tier-1' };
  }
  // ---- per-key drill-down ----
  let keyDetail = $state<any>(null);
  let keyDetailName = $state<string | null>(null);
  let keyDetailBusy = $state(false);
  let keyDetailErr = $state('');
  let qExpanded = $state<string | null>(null);
  async function openKeyDetail(name: string) {
    keyDetailName = name; keyDetail = null; keyDetailErr = ''; keyDetailBusy = true; qExpanded = null;
    try {
      const r = await fetch(`/api/admin/usage/key/${encodeURIComponent(name)}`, { headers: authHeaders() });
      if (r.ok) keyDetail = await r.json(); else keyDetailErr = `error ${r.status}`;
    } catch { keyDetailErr = 'unreachable'; }
    keyDetailBusy = false;
  }
  function closeKeyDetail() { keyDetailName = null; keyDetail = null; qExpanded = null; }
  function toggleQ(sid: string) { qExpanded = qExpanded === sid ? null : sid; }
  function fmtLatency(ms: any): string {
    const n = Number(ms) || 0;
    return n > 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`;
  }
  function truncQ(s: any, n = 60): string {
    const t = (s ?? '').toString();
    return t.length > n ? t.slice(0, n) + '…' : t;
  }
  const keyQuestions = $derived.by(() => Array.isArray(keyDetail?.questions) ? keyDetail.questions : []);

  // ════════════════════════════════════════════════════════════
  //  PER-OUTLET DETAIL PAGE (full-width view, replaces the modal)
  //  Independent state so it never collides with the main analytics screen.
  // ════════════════════════════════════════════════════════════
  let outletKey = $state<string | null>(null);
  let outletRange = $state('7d');
  let outletOv = $state<any>(null);       // gateway-overview?key
  let outletDetail = $state<any>(null);   // key/{name}  (header + questions)
  let outletIntents = $state<any>(null);  // gateway-questions?key
  let outletBusy = $state(false);
  let outletErr = $state('');
  let outletQExpanded = $state<string | null>(null);
  let outletMetric = $state<'requests' | 'tokens' | 'latency'>('requests');

  async function openOutlet(key: string) {
    outletKey = key; view = 'outlet'; outletOv = null; outletDetail = null; outletIntents = null;
    outletErr = ''; outletQExpanded = null;
    await loadOutlet();
  }
  async function loadOutlet() {
    if (!outletKey) return;
    outletBusy = true; outletErr = '';
    try {
      const h = authHeaders();
      const k = encodeURIComponent(outletKey);
      const [a, b, c] = await Promise.all([
        fetch(`/api/admin/usage/gateway-overview?range=${outletRange}&key=${k}`, { headers: h }),
        fetch(`/api/admin/usage/key/${k}`, { headers: h }),
        fetch(`/api/admin/usage/gateway-questions?range=${outletRange}&key=${k}`, { headers: h }),
      ]);
      if (a.ok) outletOv = await a.json();
      if (b.ok) outletDetail = await b.json();
      if (c.ok) outletIntents = await c.json();
      if (!a.ok && !b.ok) outletErr = `error ${a.status}`;
    } catch { outletErr = 'unreachable'; }
    outletBusy = false;
  }
  function backToAnalytics() { view = 'usage'; outletKey = null; }
  function setOutletRange(r: string) { outletRange = r; loadOutlet(); }
  function toggleOutletQ(sid: string) { outletQExpanded = outletQExpanded === sid ? null : sid; }

  // derived views over the outlet's scoped overview (mirror the main analytics derived state)
  const outKpi = $derived.by(() => outletOv?.kpi ?? {});
  const outHeader = $derived.by(() => outletDetail?.header ?? {});
  const outSeries = $derived.by(() => Array.isArray(outletOv?.series) ? outletOv.series : []);
  const outLatHist = $derived.by(() => Array.isArray(outletOv?.latency_hist) ? outletOv.latency_hist : []);
  const outErrors = $derived.by(() => outletOv?.errors ?? null);
  const outQuestions = $derived.by(() => Array.isArray(outletDetail?.questions) ? outletDetail.questions : []);
  const outTokSplit = $derived.by(() => {
    const sp = Number(outletOv?.token_split?.prompt ?? 0);
    const cp = Number(outletOv?.token_split?.completion ?? 0);
    const tot = sp + cp;
    return {
      prompt: sp, completion: cp, total: tot,
      ppct: tot ? Math.round((sp / tot) * 100) : 0,
      cpct: tot ? Math.round((cp / tot) * 100) : 0,
    };
  });
  const outActBars = $derived.by(() => {
    const ser = outSeries;
    const metric = outletMetric;
    const valOf = (s: any) =>
      metric === 'requests' ? Number(s.requests) || 0
      : metric === 'latency' ? Number(s.avg_latency_ms) || 0
      : Number(s.tokens) || 0;
    const max = Math.max(1, ...ser.map(valOf));
    return ser.map((s: any) => {
      const v = valOf(s);
      const pt = Number(s.prompt_tokens) || 0;
      const ct = Number(s.completion_tokens) || 0;
      const tt = pt + ct || 1;
      return {
        label: shortBucket(s.bucket),
        pct: Math.max(2, Math.round((v / max) * 100)),
        ppct: Math.round((pt / tt) * 100),
        cpct: Math.round((ct / tt) * 100),
        title: metric === 'requests' ? `${s.bucket}: ${v} requests`
          : metric === 'latency' ? `${s.bucket}: ${fmtLatency(v)} avg`
          : `${s.bucket}: ${v} tokens (${pt} in / ${ct} out)`,
      };
    });
  });
  function outletExportJson() {
    const blob = new Blob([JSON.stringify(outletOv ?? {}, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `outlet-${outletKey}-${outletRange}.json`; a.click();
    URL.revokeObjectURL(url);
  }

  function exportUsageCsv() {
    const rows = byKeyRows;
    const lines = [['key', 'store', 'calls', 'tokens', 'last'].join(',')];
    for (const row of rows) {
      const vals = [row.service_account ?? '', row.store_id ?? '', row.calls ?? 0, row.tokens ?? 0, row.last ?? ''];
      lines.push(vals.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `apigw-usage-${usageDays}d.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  // ════════════════════════════════════════════════════════════
  //  GATEWAY ANALYTICS (OpenRouter-style) — overview + Q&T feeds
  // ════════════════════════════════════════════════════════════
  let anRange = $state('7d');
  let anGran = $state('day');
  let anKey = $state('');
  let anModel = $state('');
  let anStore = $state('');
  let anMetric = $state<'requests' | 'tokens' | 'latency'>('requests');
  let overview = $state<any>(null);
  let ovBusy = $state(false);
  let ovErr = $state('');
  let questions = $state<any>(null);
  let tools = $state<any>(null);

  async function loadOverview() {
    ovBusy = true; ovErr = '';
    try {
      const qs = new URLSearchParams({
        range: anRange, gran: anGran,
        key: anKey || '', model: anModel || '', store: anStore || '',
      });
      const r = await fetch(`/api/admin/usage/gateway-overview?${qs.toString()}`, { headers: authHeaders() });
      if (r.ok) overview = await r.json();
      else { ovErr = `overview ${r.status}`; }
    } catch { ovErr = 'unreachable'; }
    ovBusy = false;
  }
  async function loadQT() {
    try {
      const [rq, rt] = await Promise.all([
        fetch(`/api/admin/usage/gateway-questions?range=${anRange}`, { headers: authHeaders() }),
        fetch(`/api/admin/usage/gateway-tools?range=${anRange}`, { headers: authHeaders() }),
      ]);
      if (rq.ok) questions = await rq.json(); else questions = null;
      if (rt.ok) tools = await rt.json(); else tools = null;
    } catch { questions = null; tools = null; }
  }
  function setAnRange(r: string) { if (anRange === r) return; anRange = r; loadOverview(); loadQT(); }
  function setAnGran(g: string) { if (anGran === g) return; anGran = g; loadOverview(); }
  function onAnFilter() { loadOverview(); }

  // derived views over the loaded overview
  const ovKpi = $derived.by(() => overview?.kpi ?? {});
  const ovSeries = $derived.by(() => Array.isArray(overview?.series) ? overview.series : []);
  const ovByModel = $derived.by(() => Array.isArray(overview?.by_model) ? overview.by_model : []);
  const ovByKey = $derived.by(() => Array.isArray(overview?.by_key) ? overview.by_key : []);
  const ovByStore = $derived.by(() => Array.isArray(overview?.by_store) ? overview.by_store : []);
  const ovLatHist = $derived.by(() => Array.isArray(overview?.latency_hist) ? overview.latency_hist : []);
  const ovErrors = $derived.by(() => overview?.errors ?? null);
  // filter dropdown options sourced from the loaded arrays (no extra call)
  const modelOpts = $derived.by(() => ovByModel.map((m: any) => m.model).filter(Boolean));
  const storeOpts = $derived.by(() => ovByStore.map((s: any) => s.store).filter(Boolean));
  const keyOpts = $derived.by(() => ovByKey.map((k: any) => k.key).filter(Boolean));

  // is cost tracking on anywhere?
  const costOff = $derived.by(() => {
    const k = Number(ovKpi.cost ?? 0);
    const anySeries = ovSeries.some((s: any) => Number(s.cost) > 0);
    const anyModel = ovByModel.some((m: any) => Number(m.cost) > 0);
    return k === 0 && !anySeries && !anyModel;
  });

  // token split percentages
  const tokSplit = $derived.by(() => {
    const sp = Number(overview?.token_split?.prompt ?? 0);
    const cp = Number(overview?.token_split?.completion ?? 0);
    const tot = sp + cp;
    return {
      prompt: sp, completion: cp, total: tot,
      ppct: tot ? Math.round((sp / tot) * 100) : 0,
      cpct: tot ? Math.round((cp / tot) * 100) : 0,
    };
  });

  // activity-chart bars (height ∝ chosen metric)
  const actBars = $derived.by(() => {
    const ser = ovSeries;
    const metric = anMetric;
    const valOf = (s: any) =>
      metric === 'requests' ? Number(s.requests) || 0
      : metric === 'latency' ? Number(s.avg_latency_ms) || 0
      : Number(s.tokens) || 0;
    const max = Math.max(1, ...ser.map(valOf));
    return ser.map((s: any) => {
      const v = valOf(s);
      const pt = Number(s.prompt_tokens) || 0;
      const ct = Number(s.completion_tokens) || 0;
      const tt = pt + ct || 1;
      return {
        raw: s,
        label: shortBucket(s.bucket),
        value: v,
        pct: Math.max(2, Math.round((v / max) * 100)),
        // for stacked token bars
        ppct: Math.round((pt / tt) * 100),
        cpct: Math.round((ct / tt) * 100),
        title: metric === 'requests' ? `${s.bucket}: ${v} requests`
          : metric === 'latency' ? `${s.bucket}: ${fmtLatency(v)} avg`
          : `${s.bucket}: ${v} tokens (${pt} in / ${ct} out)`,
      };
    });
  });

  // cost-over-time bars (spend panel)
  const costBars = $derived.by(() => {
    const ser = ovSeries;
    const max = Math.max(0.0000001, ...ser.map((s: any) => Number(s.cost) || 0));
    return ser.map((s: any) => ({
      label: shortBucket(s.bucket),
      cost: Number(s.cost) || 0,
      pct: Math.max(2, Math.round(((Number(s.cost) || 0) / max) * 100)),
      title: `${s.bucket}: $${(Number(s.cost) || 0).toFixed(4)}`,
    }));
  });

  function shortBucket(b: any): string {
    const s = (b ?? '').toString();
    if (!s) return '';
    // "2026-06-04 00:00:00+00" → hour buckets show HH:00, day buckets MM-DD
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):/);
    if (m) {
      if (anGran === 'hour') return `${m[4]}:00`;
      return `${m[2]}-${m[3]}`;
    }
    return s.slice(5, 10);
  }

  function fmtTokens(n: any): string {
    const v = Number(n) || 0;
    if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
    return String(v);
  }
  function fmtLatS(ms: any): string {
    const n = Number(ms) || 0;
    return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`;
  }
  function pctChange(cur: any, prev: any): number | null {
    const c = Number(cur), p = Number(prev);
    if (!isFinite(c) || !isFinite(p) || p === 0) return null;
    return Math.round(((c - p) / p) * 100);
  }
  function isTail(bucket: string): boolean {
    return bucket === '30-60s' || bucket === '>60s';
  }

  // ── exports for the analytics screen ──
  function anExportCsv() {
    const rows = ovByKey;
    const lines = [['key', 'scope', 'store', 'requests', 'tokens', 'cost', 'avg_latency_ms', 'errors', 'stream_pct', 'last'].join(',')];
    for (const row of rows) {
      const vals = [row.key ?? '', row.scope ?? '', row.store ?? '', row.requests ?? 0, row.tokens ?? 0, row.cost ?? 0, row.avg_latency_ms ?? 0, row.errors ?? 0, row.stream_pct ?? 0, row.last ?? ''];
      lines.push(vals.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `gateway-analytics-${anRange}.csv`; a.click();
    URL.revokeObjectURL(url);
  }
  function anExportJson() {
    const blob = new Blob([JSON.stringify(overview ?? {}, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `gateway-analytics-${anRange}.json`; a.click();
    URL.revokeObjectURL(url);
  }

  // auto-load analytics when entering the usage view
  $effect(() => {
    if (view === 'usage' && !overview && !ovBusy && isSuper) {
      loadOverview();
      loadQT();
    }
  });

  // ---- copy ----
  let copied = $state('');
  function flashCopied(which: string) { copied = which; setTimeout(() => { if (copied === which) copied = ''; }, 1500); }
  function copyText(txt: string, which: string) {
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(() => flashCopied(which)).catch(() => {});
  }

  // ---- snippets ----
  const curlSnippet = $derived(`curl ${baseUrl}/chat/completions \\
  -H "Authorization: Bearer dash-key-XXXX" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "citypharma-analyst",
    "messages": [{"role": "user", "content": "is paracetamol in stock?"}],
    "stream": false
  }'`);
  const phpSnippet = $derived(`<?php
$ch = curl_init("${baseUrl}/chat/completions");
curl_setopt_array($ch, [
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_HTTPHEADER => [
    "Authorization: Bearer dash-key-XXXX",
    "Content-Type: application/json",
  ],
  CURLOPT_POSTFIELDS => json_encode([
    "model" => "citypharma-analyst",
    "messages" => [["role" => "user", "content" => "is paracetamol in stock?"]],
    "stream" => false,
  ]),
]);
echo curl_exec($ch);`);
  const pySnippet = $derived(`import requests

r = requests.post(
    "${baseUrl}/chat/completions",
    headers={"Authorization": "Bearer dash-key-XXXX"},
    json={
        "model": "citypharma-analyst",
        "messages": [{"role": "user", "content": "is paracetamol in stock?"}],
        "stream": False,
    },
)
print(r.json())`);
  const phpSdkSnippet = $derived(`<?php
// composer require openai-php/client guzzlehttp/guzzle
$client = OpenAI::factory()
    ->withBaseUri('${baseUrl}')          // note: /api/v1
    ->withApiKey('dash-key-XXXX')
    ->make();

$res = $client->chat()->create([
    'model' => 'citypharma-analyst',
    'messages' => [['role' => 'user', 'content' => 'is paracetamol in stock?']],
]);
echo $res->choices[0]->message->content;`);
  const pyOpenaiSnippet = $derived(`# pip install openai
from openai import OpenAI
client = OpenAI(base_url="${baseUrl}", api_key="dash-key-XXXX")
res = client.chat.completions.create(
    model="citypharma-analyst",
    messages=[{"role": "user", "content": "is paracetamol in stock?"}],
)
print(res.choices[0].message.content)`);
  const nodeSnippet = $derived(`// npm i openai
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "${baseUrl}", apiKey: "dash-key-XXXX" });
const res = await client.chat.completions.create({
  model: "citypharma-analyst",
  messages: [{ role: "user", content: "is paracetamol in stock?" }],
});
console.log(res.choices[0].message.content);`);
  const fetchSnippet = $derived(`const r = await fetch("${baseUrl}/chat/completions", {
  method: "POST",
  headers: { "Authorization": "Bearer dash-key-XXXX", "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "citypharma-analyst",
    messages: [{ role: "user", content: "is paracetamol in stock?" }],
  }),
});
console.log((await r.json()).choices[0].message.content);`);
  const streamCurlSnippet = $derived(`curl -N ${baseUrl}/chat/completions \\
  -H "Authorization: Bearer dash-key-XXXX" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"citypharma-analyst","stream":true,
       "messages":[{"role":"user","content":"substitutes for paracetamol?"}]}'

# → text/event-stream of chat.completion.chunk frames, ends with: data: [DONE]`);
  const streamPhpSnippet = $derived(`<?php
$stream = $client->chat()->createStreamed([
    'model' => 'citypharma-analyst', 'stream' => true,
    'messages' => [['role' => 'user', 'content' => 'substitutes?']],
]);
foreach ($stream as $chunk) { echo $chunk->choices[0]->delta->content ?? ''; flush(); }`);
  const reqSchema = `POST /api/v1/chat/completions
{
  "model": "citypharma-analyst",   // optional, ignored (one model)
  "messages": [ { "role": "user", "content": "..." } ],
  "stream": false,                  // false = JSON, true = SSE
  "session_id": "abc"               // optional: pin a multi-turn thread
}`;
  const respSchema = `200 OK  (stream:false)
{
  "id": "chatcmpl-...", "object": "chat.completion",
  "model": "citypharma-analyst",
  "choices": [{ "index": 0,
    "message": { "role": "assistant", "content": "..." },
    "finish_reason": "stop" }],
  "usage": { "prompt_tokens": 9, "completion_tokens": 41, "total_tokens": 50 }
}`;
  const chunkSchema = `200 OK  (stream:true)  Content-Type: text/event-stream

data: {"object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"}}]}
data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"..."}}]}
data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]`;
  const mintCurlSnippet = $derived(`# super-admin session token (not a dash-key)
curl -X POST ${pubOrigin}/api/auth/api-key \\
  -H "Authorization: Bearer <SUPER_ADMIN_TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{"service_account_name":"php-multi",
       "store_ids":["20060-CCBHSC","20063-CCBRBKMY"],
       "scope_mode":"store"}'
# → {"api_key":"dash-key-XXXX", "store_ids":[...]}   (key shown once)`);

  onMount(async () => {
    if (typeof window !== 'undefined') {
      window.addEventListener('hashchange', () => { view = _viewFromHash(); });
    }
    pubOrigin = typeof window !== 'undefined' ? window.location.origin : '';
    // Prefer the server's canonical public origin (PUBLIC_URL env, e.g. AWS domain).
    try {
      const fr = await fetch('/api/flags');
      if (fr.ok) { const f = await fr.json(); if (f.public_base_url) pubOrigin = String(f.public_base_url).replace(/\/+$/, ''); }
    } catch { /* fall back to browser origin */ }
    baseUrl = pubOrigin + '/api/v1';
    try {
      const r = await fetch('/api/auth/check', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); isSuper = !!d.is_super; }
    } catch (e) { /* fail */ }
    checking = false;
    if (!isSuper) return;
    loadStatus(); loadKeys(); loadConfig(); loadUsage(); loadOutlets();
    await loadProvision();
    loadOutletStats();
  });
</script>

<svelte:head>{#if !embedded}<title>API Gateway · CityAgent Pharma</title>{/if}</svelte:head>

{#if checking}
  <div class="gw-center"><span class="gw-muted">◐ checking access…</span></div>
{:else if !isSuper}
  <div class="gw-center">
    <div class="gw-denied">
      <div class="gw-denied-mark">✗</div>
      <div class="gw-denied-title">super-admin only</div>
      <div class="gw-muted">The API Gateway console is restricted to platform administrators.</div>
      {#if !embedded}
        <button class="gw-btn" onclick={() => goto('/ui/home')}>← back</button>
      {/if}
    </div>
  </div>
{:else}
  <div class="gw-layout">

    <!-- ===== LEFT RAIL ===== -->
    <nav class="gw-rail">
      {#each RAIL as grp (grp.group)}
        <div class="gw-rg">
          <div class="gw-rg-label">{grp.group}</div>
          {#each grp.items as it (it.id)}
            <button class="gw-rg-item" class:gw-rg-on={view === it.id} onclick={() => nav(it.id)}>
              <svg class="gw-rg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{@html ICONS[it.icon] || ''}</svg><span class="gw-rg-text">{it.label}</span>
            </button>
          {/each}
        </div>
      {/each}
    </nav>

    <!-- ===== RIGHT CONTENT ===== -->
    <main class="gw-main" class:gw-main-wide={view === 'usage' || view === 'outlet'}>
      <header class="gw-pagehead">
        <h1 class="gw-pagetitle">{PAGE[view]?.title ?? ''}</h1>
        <p class="gw-pagesub">{PAGE[view]?.sub ?? ''}</p>
      </header>

      <!-- OVERVIEW -->
      {#if view === 'overview'}
        <section class="gw-panel">
          <div class="gw-h">HOW IT WORKS</div>
          <p class="gw-doc-p">What happens when an external app calls the OpenAI-compatible API.</p>
          <RequestFlow
            title="Gateway · input → output"
            live={true}
            badge={`LIVE · ${status?.rate_per_min ?? rateCap}/min cap`}
            inputBubble={{ who: 'PHP app · key bound to 20063', text: 'stock of panadol at my branch?' }}
            outputBubble={{ who: 'OpenAI JSON / SSE', text: 'Panadol — 5,200 units at your branch. Other stores: availability only.' }}
            stages={[
              { label: 'Gate', title: 'auth · rate · scope', color: 'amber', lines: ['Bearer dash-key → user', 'Redis rate window', '3-tier scope · raw-SQL off'], sub: 'store bound' },
              { label: 'Agent', title: '37-agent team', color: 'red', lines: ['router → stock_check', 'KG + brain · tools', 'same team as UI'], sub: 'tools run' },
              { label: 'Mask', title: 'tier filter', color: 'coral', lines: ['own → full qty/cost', 'others → no qty/price', 'usage metered'], sub: 'OpenAI shape' },
            ]}
            legend={[
              { label: 'auth + rate + scope', color: 'amber' },
              { label: 'agent + tools', color: 'red' },
              { label: 'tier-mask + response', color: 'coral' },
            ]}
          />
        </section>

        <section class="gw-panel">
          <div class="gw-h">STATUS</div>
          {#if statusErr}
            <div class="gw-row gw-err">✗ status unavailable ({statusErr})</div>
          {:else if status}
            <div class="gw-status-grid">
              <div><span class="gw-k">base_url</span><code class="gw-code">{baseUrl}</code></div>
              <div><span class="gw-k">model</span><code class="gw-code">citypharma-analyst</code></div>
              <div><span class="gw-k">gateway</span><span class="gw-dot gw-on">●</span> LIVE</div>
              <div><span class="gw-k">redis</span>{#if (status.redis ?? status.redis_up)}<span class="gw-dot gw-on">●</span> up{:else}<span class="gw-dot gw-off">✗</span> down{/if}</div>
              <div><span class="gw-k">rate-cap</span>{status.rate_per_min ?? rateCap}/min</div>
              <div><span class="gw-k">keys</span>{status.keys_active ?? '—'} active · {status.keys_revoked ?? '—'} revoked</div>
            </div>
          {:else}<div class="gw-row gw-muted">◐ loading…</div>{/if}
        </section>

        <section class="gw-panel">
          <div class="gw-h">ENDPOINTS</div>
          <div class="gw-ep">
            <div class="gw-ep-line"><span class="gw-method">GET</span> <code class="gw-code">/api/v1/models</code> <span class="gw-muted">— list models</span></div>
            <div class="gw-ep-line"><span class="gw-method gw-post">POST</span> <code class="gw-code">/api/v1/chat/completions</code></div>
            <div class="gw-ep-sub"><span class="gw-muted">stream:false</span> → JSON · <span class="gw-muted">stream:true</span> → SSE · Auth <code class="gw-code">Bearer dash-key-XXXX</code></div>
          </div>
          <div class="gw-btnrow">
            <button class="gw-btn" onclick={() => copyText(curlSnippet, 'curl')}>{copied === 'curl' ? '✓ copied' : 'copy cURL'}</button>
            <button class="gw-btn" onclick={() => copyText(phpSnippet, 'php')}>{copied === 'php' ? '✓ copied' : 'copy PHP'}</button>
            <button class="gw-btn" onclick={() => copyText(pySnippet, 'py')}>{copied === 'py' ? '✓ copied' : 'copy Python'}</button>
          </div>
        </section>
      {/if}

      <!-- CHAT SANDBOX -->
      {#snippet sandboxBody()}
        <div class="gw-chat">
          <!-- header: key + stream + scope scenarios -->
          <div class="gw-chat-head">
            <input class="gw-input gw-chat-key" placeholder="dash-key-… (mint under Service keys)" bind:value={sbKey} />
            <label class="gw-sb-chk"><input type="checkbox" bind:checked={sbStream} /> stream</label>
            <div class="gw-chat-scenarios">
              <button class="gw-chip" title="own branch — full qty" onclick={() => sbInput = 'is paracetamol in stock at my branch?'}>own stock</button>
              <button class="gw-chip" title="other branch — masked" onclick={() => sbInput = 'how much stock of paracetamol at site 20003?'}>other branch</button>
              <button class="gw-chip" title="open catalog" onclick={() => sbInput = 'what are substitutes for amlodipine?'}>catalog</button>
            </div>
          </div>

          <!-- transcript -->
          <div class="gw-chat-body" bind:this={sbBodyEl}>
            {#if sbTurns.length === 0}
              <div class="gw-chat-empty">
                Live test the real <code class="gw-code">POST /api/v1/chat/completions</code>. Paste a service key, ask a question — the 3-tier store masking applies exactly as it would for an external app. Try a scenario chip above.
              </div>
            {/if}
            {#each sbTurns as t, i}
              {#if t.role === 'user'}
                <div class="gw-msg gw-msg-user"><div class="gw-bubble gw-bubble-user">{t.content}</div></div>
              {:else}
                <div class="gw-msg gw-msg-bot">
                  <div class="gw-bot-avatar">🤖</div>
                  <div class="gw-bot-col">
                    {#if (t.steps && t.steps.length) || t.thinking}
                      {#if t.thinking}
                        <!-- LIVE: done-step rail + shimmering active phase -->
                        <div class="gw-steps gw-steps-live">
                          <div class="gw-step-rail">
                            {#each (t.steps || []) as s}
                              <div class="gw-rail-row gw-rail-done" class:gw-step-phase={s.icon === '🧠'}><span class="gw-rail-tick">✓</span> {s.label} <span class="gw-step-ic">{s.icon}</span></div>
                            {/each}
                            <div class="gw-rail-row gw-rail-active">
                              <span class="gw-sb-cursor">●</span>
                              <span class="gw-shimmer">{(t.steps && t.steps.length ? t.steps[t.steps.length - 1].label : 'Thinking…')}</span>
                              <span class="gw-wave"><span class="gw-wave-dot">•</span><span class="gw-wave-dot">•</span><span class="gw-wave-dot">•</span></span>
                            </div>
                          </div>
                        </div>
                      {:else if t.steps?.length}
                        <!-- DONE: collapsible "Worked for Xs" fold -->
                        <div class="gw-steps">
                          <button class="gw-worked" onclick={() => { sbTurns[i].stepsOpen = !sbTurns[i].stepsOpen; sbTurns = [...sbTurns]; }}>{t.stepsOpen ? '▾' : '▸'} {workedLabel(t)}</button>
                          {#if t.stepsOpen}
                            <div class="gw-step-rail">
                              {#each (t.steps || []) as s}
                                <div class="gw-rail-row gw-rail-done" class:gw-step-phase={s.icon === '🧠'}><span class="gw-rail-dot">○</span> {s.label} <span class="gw-step-ic">{s.icon}</span></div>
                              {/each}
                            </div>
                          {/if}
                        </div>
                      {/if}
                    {/if}
                    <div class="gw-bubble gw-bubble-bot">{t.content || (sbBusy && i === sbTurns.length - 1 ? '…' : '')}{#if sbBusy && sbStream && i === sbTurns.length - 1}<span class="gw-sb-cursor">▋</span>{/if}</div>
                    {#if t.meta || t.json || t.curl}
                      <div class="gw-msg-meta">
                        {#if t.masked}<span class="gw-mask-badge">🛡 masked</span>{/if}
                        {#if t.meta}<span class="gw-muted">⤷ {t.meta}</span>{/if}
                        {#if t.json || t.curl}<button class="gw-json-toggle" onclick={() => { sbTurns[i].open = !sbTurns[i].open; sbTurns = [...sbTurns]; }}>{t.open ? '▾ hide inspect' : '▸ inspect'}</button>{/if}
                      </div>
                      {#if t.open}
                        <div class="gw-inspect">
                          <div class="gw-inspect-row"><span class="gw-inspect-k">request</span><code class="gw-code">POST /api/v1/chat/completions · msgs {t.reqMsgs ?? '—'} · stream {t.streamed ? 'true' : 'false'}</code></div>
                          {#if t.curl}
                            <div class="gw-inspect-head"><span class="gw-inspect-k">cURL</span><button class="gw-btn gw-btn-sm" onclick={() => copyText(t.curl || '', `c-${i}`)}>{copied === `c-${i}` ? '✓' : 'copy'}</button></div>
                            <pre class="gw-insp-curl">{t.curl}</pre>
                          {/if}
                          {#if t.json}
                            <div class="gw-inspect-head"><span class="gw-inspect-k">raw JSON</span><button class="gw-btn gw-btn-sm" onclick={() => copyText(t.json || '', `j-${i}`)}>{copied === `j-${i}` ? '✓' : 'copy'}</button></div>
                            <pre class="gw-json">{t.json}</pre>
                          {/if}
                          <div class="gw-insp-note">masking enforced server-side; 🛡 badge is a client heuristic on sanitizer phrasing.</div>
                        </div>
                      {/if}
                    {/if}
                  </div>
                </div>
              {/if}
            {/each}
          </div>

          {#if sbErr}<div class="gw-sb-err">✗ {sbErr}</div>{/if}

          <!-- composer -->
          <div class="gw-chat-composer">
            <textarea class="gw-chat-input" rows="1" placeholder="ask something…" bind:value={sbInput}
              onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendSandbox(); } }}></textarea>
            <button class="gw-chat-send" disabled={sbBusy} onclick={sendSandbox} aria-label="Send">{sbBusy ? '◐' : '→'}</button>
            {#if sbTurns.length}<button class="gw-btn gw-btn-sm gw-chat-reset" onclick={sbReset}>Reset</button>{/if}
          </div>
          <div class="gw-muted gw-fineprint">real request · multi-turn · masking enforced server-side · ▸ inspect any reply for curl + raw JSON · Enter to send</div>
        </div>
      {/snippet}
      {#if view === 'sandbox'}{@render sandboxBody()}{/if}

      <!-- KEYS -->
      {#snippet keysBody(showList = true)}
        {@const shownKeys = showList ? keys : keys.filter((k) => k.scope_mode === 'global')}
        <section class="gw-panel">
          <div class="gw-h-row">
            <div class="gw-h">{showList ? 'SERVICE KEYS' : 'GLOBAL / BI KEY'}</div>
            <button class="gw-btn gw-btn-accent" onclick={() => { mintOpen = !mintOpen; mintedKey = ''; mintErr=''; }}>+ MINT KEY</button>
          </div>
          {#if !showList}<div class="gw-muted gw-fineprint" style="margin:-4px 0 8px;">Per-outlet keys → <button class="gw-link" onclick={() => nav('provision')}>Outlet Keys</button>. Mint a <strong>global</strong> key here for cross-branch BI / analytics.</div>{/if}

          {#if mintOpen}
            <div class="gw-mint">
              <div class="gw-mint-grid">
                <label class="gw-field"><span class="gw-flabel">name</span><input class="gw-input" placeholder="php-multi" bind:value={mintName} /></label>
                <div class="gw-field"><span class="gw-flabel">scope</span>
                  <div class="gw-radio">
                    <label><input type="radio" bind:group={mintScope} value="store" /> store <span class="gw-muted">(tiered)</span></label>
                    <label><input type="radio" bind:group={mintScope} value="global" /> global <span class="gw-muted">(full)</span></label>
                  </div>
                </div>
              </div>
              {#if mintScope === 'store'}
                <div class="gw-field">
                  <span class="gw-flabel">outlets <span class="gw-muted">— one key, many stores (Tier-1 set)</span></span>
                  {#if mintOutlets.length}
                    <div class="gw-chips">{#each mintOutlets as o (o)}<span class="gw-chip">{o}<button class="gw-chip-x" onclick={() => removeOutlet(o)}>×</button></span>{/each}</div>
                  {/if}
                  <div class="gw-picker">
                    <input class="gw-input" placeholder="filter / type a site_code…" bind:value={outletFilter}
                           onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTypedOutlet(); } }} />
                    <button class="gw-btn gw-btn-sm" onclick={addTypedOutlet} disabled={!outletFilter.trim()}>+ add</button>
                  </div>
                  {#if outletChoices.length}
                    <div class="gw-choices">{#each outletChoices as o (o)}<button class="gw-choice" onclick={() => addOutlet(o)}>{o}</button>{/each}</div>
                  {:else if outlets.length === 0}
                    <div class="gw-muted gw-fineprint">no outlet list — type site_codes manually + add</div>
                  {/if}
                </div>
              {/if}
              <div class="gw-mint-actions">
                <button class="gw-btn gw-btn-accent" disabled={mintBusy} onclick={mintKey}>{mintBusy ? '◐…' : 'CREATE KEY'}</button>
                <button class="gw-btn" onclick={() => { mintOpen = false; resetMint(); }}>cancel</button>
                {#if mintErr}<span class="gw-err">✗ {mintErr}</span>{/if}
              </div>
              {#if mintedKey}
                <div class="gw-minted">
                  <div class="gw-warn">◐ shown once — copy now, it will not be displayed again</div>
                  <div class="gw-minted-row"><code class="gw-code gw-code-key">{mintedKey}</code><button class="gw-btn" onclick={() => copyText(mintedKey, 'minted')}>{copied === 'minted' ? '✓ copied' : 'copy'}</button></div>
                </div>
              {/if}
            </div>
          {/if}

          {#if keysErr}<div class="gw-row gw-err">✗ keys unavailable ({keysErr})</div>
          {:else if shownKeys.length === 0}<div class="gw-row gw-muted">{showList ? 'no keys yet — mint one above' : 'no global keys yet — mint one for BI / analytics'}</div>
          {:else}
            <div class="gw-keylist">
              {#each shownKeys as k (k.id ?? k.service_account_name)}
                {@const ol = outletList(k)}
                {@const tier = keyTier(k)}
                {@const u = keyUsage[k.service_account_name]}
                {@const kid = k.id ?? k.service_account_name}
                <div class="gw-keycard" class:gw-keycard-off={!k.active}>
                  <div class="gw-keycard-top">
                    <span class="gw-dot {k.active ? 'gw-on' : 'gw-off'}">●</span>
                    <code class="gw-code gw-keyname">{k.service_account_name}</code>
                    <span class="gw-scope-pill {tier.cls}">{k.scope_mode ?? 'store'}</span>
                    <div class="gw-keycard-actions">
                      {#if k.active}<button class="gw-btn gw-btn-sm" onclick={() => revokeKey(k.service_account_name)}>Revoke</button>
                      {:else}<span class="gw-badge gw-badge-off">revoked</span>{/if}
                    </div>
                  </div>
                  <div class="gw-keycard-tier">{tier.label}</div>
                  <div class="gw-keycard-meta">
                    <span class="gw-keycard-binding">
                      {#if k.scope_mode === 'global'}<span class="gw-muted">all stores</span>
                      {:else if ol.length === 0}<span class="gw-muted">no outlet bound</span>
                      {:else if ol.length === 1}<code class="gw-code">{ol[0]}</code>
                      {:else}
                        <button class="gw-expand" onclick={() => expandedKey = (expandedKey === kid ? null : kid)}>{ol[0]} +{ol.length - 1} {expandedKey === kid ? '▾' : '▸'}</button>
                      {/if}
                    </span>
                    <span class="gw-keycard-usage">
                      {#if u}{u.calls} req{#if u.last} · last {u.last}{/if}{:else}<span class="gw-muted">no calls in window</span>{/if}
                    </span>
                  </div>
                  {#if expandedKey === kid && ol.length > 1}
                    <div class="gw-chips gw-keycard-chips">{#each ol as o (o)}<span class="gw-chip gw-chip-ro">{o}</span>{/each}</div>
                  {/if}
                </div>
              {/each}
            </div>
            <div class="gw-muted gw-fineprint">req counts from the selected usage window · secret shown once at mint, never retrievable after</div>
          {/if}
        </section>
      {/snippet}
      {#if view === 'keys'}{@render keysBody(true)}{/if}

      <!-- OUTLETS -->
      {#snippet outletsBody()}
        <!-- freshness card — proves the list reflects the LATEST upload -->
        <section class="gw-fresh">
          <div class="gw-fresh-main">
            <span class="gw-dot gw-on">●</span>
            <span class="gw-fresh-live">live</span>
            <span class="gw-muted">auto-tracks latest upload (≤30s, no restart)</span>
            <button class="gw-btn gw-btn-sm gw-fresh-refresh" disabled={outletsBusy} onclick={loadOutlets}>{outletsBusy ? '◐' : '⟳ refresh'}</button>
          </div>
          <div class="gw-fresh-stats">
            <div><span class="gw-k">source</span><code class="gw-code">{outletMeta?.source_table ?? '—'}</code></div>
            <div><span class="gw-k">uploaded</span>{#if outletMeta?.updated_at}{relTime(outletMeta.updated_at)} <span class="gw-muted">({outletMeta.updated_at.slice(0, 16).replace('T', ' ')})</span>{:else}<span class="gw-muted">—</span>{/if}</div>
            <div><span class="gw-k">stock rows</span>{outletMeta?.row_count != null ? outletMeta.row_count.toLocaleString() : '—'}</div>
            <div><span class="gw-k">outlets</span>{outlets.length}</div>
          </div>
        </section>

        <section class="gw-panel">
          <div class="gw-h-row">
            <div class="gw-h">OUTLETS ({outletPage.length}{#if outletPage.length !== outlets.length} / {outlets.length}{/if})</div>
            <input class="gw-input gw-input-search" placeholder="search site_code…" bind:value={outletSearch} />
          </div>
          {#if outlets.length === 0}
            <div class="gw-row gw-muted">{outletsBusy ? '◐ loading…' : 'no outlets in current stock data'}</div>
          {:else}
            <div class="gw-outlet-grid">
              {#each outletPage as o (o)}
                {@const bound = boundOutlets.has(o)}
                <button class="gw-outlet" class:gw-outlet-bound={bound} onclick={() => copyText(o, `o-${o}`)} title={bound ? 'covered by a key · click to copy' : 'not bound to any key · click to copy'}>
                  <span class="gw-outlet-role">{bound ? '▣' : '○'}</span>
                  <code class="gw-code">{o}</code>{#if copied === `o-${o}`}<span class="gw-saved"> ✓</span>{/if}
                </button>
              {/each}
            </div>
            <div class="gw-muted gw-fineprint">▣ bound to a key · ○ unbound · click any code to copy · bind site_codes to keys in the mint form</div>
          {/if}
        </section>
      {/snippet}
      {#if view === 'outlets'}{@render outletsBody()}{/if}

      <!-- PROVISION (Outlets page: 1 key per outlet, table + copy + test) -->
      {#if view === 'provision'}
        <section class="gw-panel">
          <!-- 3-tier access legend (folded from Access model) + live rate cap (folded from Rate limit) -->
          <div class="gw-prov-banner">
            <div class="gw-prov-legend">
              <span class="gw-prov-legtitle">Access</span>
              <span class="gw-prov-leg"><span class="gw-dot gw-on">●</span> own <span class="gw-muted">full qty + cost</span></span>
              <span class="gw-prov-leg"><span class="gw-dot gw-amber">◐</span> other <span class="gw-muted">availability only</span></span>
              <span class="gw-prov-leg"><span class="gw-dot gw-open">○</span> catalog <span class="gw-muted">open</span></span>
            </div>
            <div class="gw-prov-rate">
              <span class="gw-prov-legtitle">Rate</span>
              <input class="gw-input gw-input-num" type="number" min="1" bind:value={rateCap} />
              <span class="gw-muted">req/min/key</span>
              <button class="gw-btn gw-btn-sm gw-btn-accent" disabled={rateBusy} onclick={saveConfig}>{rateBusy ? '◐' : 'save'}</button>
              {#if rateSaved}<span class="gw-saved">✓</span>{/if}
              {#if rateErr}<span class="gw-err">✗ {rateErr}</span>{/if}
            </div>
          </div>

          <div class="gw-prov-top">
            <div class="gw-prov-summary">
              <span class="gw-prov-stat"><b>{provSummary.total}</b> outlets</span>
              <span class="gw-prov-stat gw-prov-ok"><b>{provSummary.keyed}</b> keyed</span>
              <span class="gw-prov-stat gw-prov-miss"><b>{provSummary.missing}</b> missing</span>
            </div>
            <div class="gw-prov-actions">
              <div class="gw-pills">
                <button class="gw-pill" class:gw-pill-on={outletStatsDays === 1} onclick={() => setOutletStatsDays(1)}>1d</button>
                <button class="gw-pill" class:gw-pill-on={outletStatsDays === 7} onclick={() => setOutletStatsDays(7)}>7d</button>
                <button class="gw-pill" class:gw-pill-on={outletStatsDays === 30} onclick={() => setOutletStatsDays(30)}>30d</button>
              </div>
              <button class="gw-btn gw-btn-sm" disabled={provBusy} onclick={loadProvision}>{provBusy ? '◐' : '⟳ sync'}</button>
              <button class="gw-btn gw-btn-accent" disabled={provBusy || provSummary.missing === 0} onclick={provGenAllMissing}>{provBusy ? '◐ working…' : `+ Generate all(${provSummary.missing})`}</button>
            </div>
          </div>

          <div class="gw-prov-tools">
            <input class="gw-input gw-input-search" placeholder="search site_code…" bind:value={provSearch} />
            <div class="gw-pills">
              <button class="gw-pill" class:gw-pill-on={provFilter === 'all'} onclick={() => provFilter = 'all'}>all</button>
              <button class="gw-pill" class:gw-pill-on={provFilter === 'keyed'} onclick={() => provFilter = 'keyed'}>keyed</button>
              <button class="gw-pill" class:gw-pill-on={provFilter === 'missing'} onclick={() => provFilter = 'missing'}>missing</button>
            </div>
            <div class="gw-prov-export">
              <button class="gw-btn gw-btn-sm" disabled={provBusy || provSummary.keyed === 0} onclick={() => provExportAll('env')}>Copy .env</button>
              <button class="gw-btn gw-btn-sm" disabled={provBusy || provSummary.keyed === 0} onclick={() => provExportAll('csv')}>CSV</button>
              <button class="gw-btn gw-btn-sm" title="One key-agnostic client (php + py + README) that serves every shop. Drop your .env in and run." onclick={() => { const origin = baseUrl.replace(/\/api\/v1\/?$/, ''); window.open(`${origin}/api/embed/sdk/gateway-bundle.zip?base=${encodeURIComponent(baseUrl)}`, '_blank'); }}>⬇ Bundle .zip</button>
            </div>
          </div>

          {#if provErr}<div class="gw-row gw-err">✗ provision unavailable ({provErr})</div>{/if}

          {#if provRows.length === 0}
            <div class="gw-row gw-muted">{provBusy ? '◐ loading…' : 'no outlets in current stock data'}</div>
          {:else}
            <div class="gw-prov-table">
              <div class="gw-prov-head">
                <span>Outlet</span><span>Key</span><span>Scope</span><span>Reqs</span><span>Errors</span><span>Last used</span><span>Test</span><span>Copy</span>
              </div>
              {#each provView as r (r.outlet)}
                {@const stat = outletStatsMap[r.outlet]}
                <div class="gw-prov-row" class:gw-prov-open={provExpanded === r.outlet}>
                  <button class="gw-prov-cell gw-prov-code" onclick={() => toggleProvRow(r.outlet, r.name)} title="expand">
                    <span class="gw-prov-caret">{provExpanded === r.outlet ? '▾' : '▸'}</span><code class="gw-code">{r.outlet}</code>
                  </button>
                  <span class="gw-prov-cell">
                    {#if r.keyed}
                      <span class="gw-dot gw-on">●</span>
                      {#if provKeys[r.outlet]}<code class="gw-code gw-prov-keytxt">{provKeys[r.outlet].slice(0, 16)}…</code>
                      {:else}<span class="gw-muted">keyed</span>{/if}
                    {:else}<span class="gw-prov-warn">⚠ no key</span>{/if}
                  </span>
                  <span class="gw-prov-cell">{#if r.keyed}<span class="gw-scope-pill gw-tier-1">own ● full</span>{:else}<span class="gw-muted">—</span>{/if}</span>
                  <span class="gw-prov-cell gw-muted">{stat ? stat.reqs : (r.keyed ? '0' : '—')}</span>
                  <span class="gw-prov-cell">{#if stat && stat.errors > 0}<span class="gw-prov-errs">{stat.errors}</span>{:else}<span class="gw-muted">{stat ? '0' : '—'}</span>{/if}</span>
                  <span class="gw-prov-cell gw-muted">{stat?.last_used ? relTime(stat.last_used) : '—'}</span>
                  <span class="gw-prov-cell">
                    {#if r.keyed}<button class="gw-btn gw-btn-sm" onclick={() => openChat(r.outlet, r.name)}>⏵ test</button>{:else}<span class="gw-muted">—</span>{/if}
                  </span>
                  <span class="gw-prov-cell">
                    {#if r.keyed}
                      <button class="gw-btn gw-btn-sm" onclick={async () => { const k = await provReveal(r.outlet, r.name); if (k) copyText(k, `pk-${r.outlet}`); }}>{copied === `pk-${r.outlet}` ? '✓' : (provRevealBusy === r.outlet ? '◐' : '📋 key')}</button>
                    {:else}
                      <button class="gw-btn gw-btn-sm gw-btn-accent" disabled={provBusy} onclick={() => provGenOne(r.outlet)}>+ gen</button>
                    {/if}
                  </span>
                </div>

                {#if provExpanded === r.outlet}
                  <div class="gw-prov-detail">
                    {#if r.keyed}
                      <div class="gw-prov-drow">
                        <span class="gw-prov-dk">Key</span>
                        <code class="gw-code gw-code-key">{provKeys[r.outlet] || '◐ revealing…'}</code>
                        <button class="gw-btn gw-btn-sm" onclick={() => provKeys[r.outlet] && copyText(provKeys[r.outlet], `pkf-${r.outlet}`)}>{copied === `pkf-${r.outlet}` ? '✓' : '📋'}</button>
                        <button class="gw-btn gw-btn-sm" disabled={provBusy} onclick={() => provRotate(r.outlet)}>↻ rotate</button>
                        <button class="gw-btn gw-btn-sm" onclick={() => revokeOutlet(r.name)}>Revoke</button>
                      </div>
                      <div class="gw-prov-drow"><span class="gw-prov-dk">Endpoint</span><code class="gw-code">{baseUrl}/chat/completions</code></div>
                      <div class="gw-prov-drow"><span class="gw-prov-dk">Scope</span><span class="gw-muted">own=full qty/cost · other=masked · ref=global · catalog=open</span></div>

                      <!-- code snippet, lang tabs -->
                      <div class="gw-prov-snip">
                        <div class="gw-prov-langtabs">
                          {#each ['php', 'curl', 'python', 'env'] as lg}
                            <button class="gw-pill" class:gw-pill-on={(provLang[r.outlet] || 'php') === lg} onclick={() => { provLang[r.outlet] = lg; provLang = { ...provLang }; }}>{lg === 'env' ? '.env' : lg.toUpperCase()}</button>
                          {/each}
                          <button class="gw-btn gw-btn-sm gw-prov-snipcopy" onclick={() => copyText(provSnippet(r.outlet, provLang[r.outlet] || 'php'), `ps-${r.outlet}`)}>{copied === `ps-${r.outlet}` ? '✓ copied' : '📋 copy'}</button>
                        </div>
                        <pre class="gw-json gw-prov-pre">{provSnippet(r.outlet, provLang[r.outlet] || 'php')}</pre>
                      </div>

                      <!-- open full chat tester -->
                      <div class="gw-prov-test">
                        <button class="gw-btn gw-btn-sm gw-btn-accent" onclick={() => openChat(r.outlet, r.name)}>💬 Open chat test</button>
                        <span class="gw-muted gw-fineprint">multi-turn chatbot · key auto-bound · masking live</span>
                      </div>
                    {:else}
                      <div class="gw-prov-drow gw-muted">No key for this outlet yet<button class="gw-btn gw-btn-sm gw-btn-accent" disabled={provBusy} onclick={() => provGenOne(r.outlet)}>+ generate key</button></div>
                    {/if}
                  </div>
                {/if}
              {/each}
            </div>
            <div class="gw-muted gw-fineprint">1 store-locked key per outlet (own=full, other=masked) · Reqs/Errors/Last used from the selected window (1d/7d/30d) · store keys answer pharma tools (stock/substitutes); use a global key for cross-branch analytics</div>
          {/if}
        </section>

        <!-- ===== per-outlet CHAT DRAWER (slide-over) ===== -->
        {#if chatOutlet}
          <div class="gw-drawer-back" onclick={closeChat} role="presentation"></div>
          <div class="gw-drawer" role="dialog" aria-label="Outlet chat test" tabindex="-1">
            <div class="gw-drawer-head">
              <div class="gw-drawer-id">
                <span class="gw-drawer-av">🏪</span>
                <div>
                  <div class="gw-drawer-title">CityPharma · <code class="gw-code">{chatOutlet}</code></div>
                  <div class="gw-drawer-sub"><span class="gw-dot gw-on">●</span> online · key bound · <span class="gw-mask-badge">🛡 masked live</span></div>
                </div>
              </div>
              <button class="gw-drawer-x" onclick={closeChat} aria-label="Close">✕</button>
            </div>

            <div class="gw-drawer-toolbar">
              <label class="gw-sb-chk"><input type="checkbox" bind:checked={sbStream} /> stream</label>
              <div class="gw-chat-scenarios">
                <button class="gw-chip" title="own branch — full qty" onclick={() => sbInput = 'is paracetamol in stock at my branch?'}>own</button>
                <button class="gw-chip" title="other branch — masked" onclick={() => sbInput = 'how much stock of paracetamol at site 20003?'}>other</button>
                <button class="gw-chip" title="open catalog" onclick={() => sbInput = 'what are substitutes for amlodipine?'}>catalog</button>
              </div>
              {#if sbTurns.length}<button class="gw-btn gw-btn-sm gw-drawer-reset" onclick={sbReset}>Reset</button>{/if}
            </div>

            <div class="gw-drawer-body" bind:this={sbBodyEl}>
              {#if chatKeyMissing}
                <div class="gw-chat-empty gw-err">✗ could not reveal key for {chatOutlet}. Generate / rotate it first.</div>
              {:else if sbTurns.length === 0}
                <div class="gw-chat-empty">
                  Live chat as outlet <code class="gw-code">{chatOutlet}</code> — real <code class="gw-code">/api/v1/chat/completions</code>, key auto-bound, 3-tier masking enforced.<br/>
                  <span class="gw-muted">Test directly as this outlet — key bound, masking live.</span>
                </div>
              {/if}
              {#each sbTurns as t, i}
                {#if t.role === 'user'}
                  <div class="gw-msg gw-msg-user"><div class="gw-bubble gw-bubble-user">{t.content}</div></div>
                {:else}
                  <div class="gw-msg gw-msg-bot">
                    <div class="gw-bot-avatar">🤖</div>
                    <div class="gw-bot-col">
                      {#if (t.steps && t.steps.length) || t.thinking}
                        {#if t.thinking}
                          <!-- LIVE: done-step rail + shimmering active phase -->
                          <div class="gw-steps gw-steps-live">
                            <div class="gw-step-rail">
                              {#each (t.steps || []) as s}
                                <div class="gw-rail-row gw-rail-done" class:gw-step-phase={s.icon === '🧠'}><span class="gw-rail-tick">✓</span> {s.label} <span class="gw-step-ic">{s.icon}</span></div>
                              {/each}
                              <div class="gw-rail-row gw-rail-active">
                                <span class="gw-sb-cursor">●</span>
                                <span class="gw-shimmer">{(t.steps && t.steps.length ? t.steps[t.steps.length - 1].label : 'Thinking…')}</span>
                                <span class="gw-wave"><span class="gw-wave-dot">•</span><span class="gw-wave-dot">•</span><span class="gw-wave-dot">•</span></span>
                              </div>
                            </div>
                          </div>
                        {:else if t.steps?.length}
                          <!-- DONE: collapsible "Worked for Xs" fold -->
                          <div class="gw-steps">
                            <button class="gw-worked" onclick={() => { sbTurns[i].stepsOpen = !sbTurns[i].stepsOpen; sbTurns = [...sbTurns]; }}>{t.stepsOpen ? '▾' : '▸'} {workedLabel(t)}</button>
                            {#if t.stepsOpen}
                              <div class="gw-step-rail">
                                {#each (t.steps || []) as s}
                                  <div class="gw-rail-row gw-rail-done" class:gw-step-phase={s.icon === '🧠'}><span class="gw-rail-dot">○</span> {s.label} <span class="gw-step-ic">{s.icon}</span></div>
                                {/each}
                              </div>
                            {/if}
                          </div>
                        {/if}
                      {/if}
                      <div class="gw-bubble gw-bubble-bot gw-bubble-md">{#if t.content}{@html renderMd(t.content)}{:else if sbBusy && i === sbTurns.length - 1}<span class="gw-typing">●●●</span>{/if}{#if sbBusy && sbStream && i === sbTurns.length - 1}<span class="gw-sb-cursor">▋</span>{/if}</div>
                      {#if t.meta || t.json || t.curl}
                        <div class="gw-msg-meta">
                          {#if t.masked}<span class="gw-mask-badge">🛡 masked</span>{/if}
                          {#if t.meta}<span class="gw-muted">⤷ {t.meta}</span>{/if}
                          {#if t.json || t.curl}<button class="gw-json-toggle" onclick={() => { sbTurns[i].open = !sbTurns[i].open; sbTurns = [...sbTurns]; }}>{t.open ? '▾ hide inspect' : '▸ inspect'}</button>{/if}
                        </div>
                        {#if t.open}
                          <div class="gw-inspect">
                            {#if t.curl}
                              <div class="gw-inspect-head"><span class="gw-inspect-k">cURL</span><button class="gw-btn gw-btn-sm" onclick={() => copyText(t.curl || '', `dc-${i}`)}>{copied === `dc-${i}` ? '✓' : 'copy'}</button></div>
                              <pre class="gw-insp-curl">{t.curl}</pre>
                            {/if}
                            {#if t.json}
                              <div class="gw-inspect-head"><span class="gw-inspect-k">raw JSON</span><button class="gw-btn gw-btn-sm" onclick={() => copyText(t.json || '', `dj-${i}`)}>{copied === `dj-${i}` ? '✓' : 'copy'}</button></div>
                              <pre class="gw-json">{t.json}</pre>
                            {/if}
                          </div>
                        {/if}
                      {/if}
                    </div>
                  </div>
                {/if}
              {/each}
            </div>

            {#if sbErr}<div class="gw-sb-err">✗ {sbErr}</div>{/if}

            <div class="gw-drawer-composer">
              <textarea class="gw-chat-input" rows="1" placeholder="ask something…" bind:value={sbInput}
                onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendSandbox(); } }}></textarea>
              <button class="gw-chat-send" disabled={sbBusy || chatKeyMissing} onclick={sendSandbox} aria-label="Send">{sbBusy ? '◐' : '→'}</button>
            </div>
            <div class="gw-muted gw-fineprint gw-drawer-foot">real key · multi-turn · ▸ inspect for curl + JSON · Enter to send</div>
          </div>
        {/if}
      {/if}

      <!-- CONSOLE (slim): global-key mint + live chatbot. Outlet keys → Outlet Keys page. -->
      {#if view === 'console'}
        <div class="gw-console">
          <div class="gw-console-cell gw-console-globalkey">{@render keysBody(false)}</div>
          <div class="gw-console-cell gw-console-chat">{@render sandboxBody()}</div>
        </div>
      {/if}

      <!-- USAGE -->
      {#if view === 'usage'}
        <!-- ═══════════ A. HEADER + FILTER BAR ═══════════ -->
        <section class="gw-panel">
          <div class="gw-an-head">
            <div class="gw-an-title">
              <span class="gw-h">Gateway Analytics</span>
              <span class="gw-an-live" title="auto-tracked from gateway traces">● LIVE</span>
            </div>
            <div class="gw-an-exports">
              <button class="gw-btn gw-btn-sm" onclick={anExportCsv} disabled={!overview}>⬇ CSV</button>
              <button class="gw-btn gw-btn-sm" onclick={anExportJson} disabled={!overview}>⬇ JSON</button>
            </div>
          </div>

          <div class="gw-an-filters">
            <div class="gw-pills">
              <button class="gw-pill" class:gw-pill-on={anRange === '24h'} onclick={() => setAnRange('24h')}>24h</button>
              <button class="gw-pill" class:gw-pill-on={anRange === '7d'} onclick={() => setAnRange('7d')}>7d</button>
              <button class="gw-pill" class:gw-pill-on={anRange === '30d'} onclick={() => setAnRange('30d')}>30d</button>
            </div>
            <select class="gw-an-sel" bind:value={anKey} onchange={onAnFilter} title="filter by key">
              <option value="">all keys</option>
              {#each keyOpts as k}<option value={k}>{k}</option>{/each}
            </select>
            <select class="gw-an-sel" bind:value={anModel} onchange={onAnFilter} title="filter by model">
              <option value="">all models</option>
              {#each modelOpts as m}<option value={m}>{m}</option>{/each}
            </select>
            <select class="gw-an-sel" bind:value={anStore} onchange={onAnFilter} title="filter by store">
              <option value="">all stores</option>
              {#each storeOpts as s}<option value={s}>{s}</option>{/each}
            </select>
            <div class="gw-pills gw-an-gran" title="bucket granularity (24h/7d)">
              <button class="gw-pill" class:gw-pill-on={anGran === 'hour'} onclick={() => setAnGran('hour')}>○ hour</button>
              <button class="gw-pill" class:gw-pill-on={anGran === 'day'} onclick={() => setAnGran('day')}>● day</button>
            </div>
            {#if ovBusy}<span class="gw-muted gw-an-busy">◐ loading…</span>{/if}
          </div>
        </section>

        {#if ovErr}
          <section class="gw-panel"><div class="gw-row gw-err">✗ analytics unavailable ({ovErr})</div></section>
        {:else if !overview && ovBusy}
          <section class="gw-panel"><div class="gw-row gw-muted">◐ loading analytics…</div></section>
        {:else if overview}
          <!-- ═══════════ B. KPI STRIP ═══════════ -->
          <section class="gw-panel">
            <div class="gw-kpi-grid">
              {#snippet kpi(label: string, value: any, sub: string = '', delta: number | null = null, warn: boolean = false)}
                <div class="gw-kpi" class:gw-kpi-warn={warn}>
                  <div class="gw-kpi-label">{label}</div>
                  <div class="gw-kpi-val">{value}{#if warn} <span class="gw-err">⚠</span>{/if}</div>
                  {#if sub}<div class="gw-kpi-sub">{sub}</div>{/if}
                  {#if delta !== null}
                    <div class="gw-kpi-delta" class:gw-up={delta > 0} class:gw-down={delta < 0}>{delta > 0 ? '▲' : delta < 0 ? '▼' : '■'} {Math.abs(delta)}%</div>
                  {/if}
                </div>
              {/snippet}
              {@render kpi('Requests', ovKpi.requests ?? 0, '', pctChange(ovKpi.requests, ovKpi.requests_prev))}
              {@render kpi('Tokens', fmtTokens(ovKpi.tokens), `${fmtTokens(ovKpi.prompt_tokens)} in / ${fmtTokens(ovKpi.completion_tokens)} out`, pctChange(ovKpi.tokens, ovKpi.tokens_prev))}
              {@render kpi('Cost', `$${Number(ovKpi.cost ?? 0).toFixed(4)}`, '', pctChange(ovKpi.cost, ovKpi.cost_prev))}
              {@render kpi('Avg latency', fmtLatS(ovKpi.avg_latency_ms), '', null, Number(ovKpi.avg_latency_ms) > 10000)}
              {@render kpi('p95 latency', fmtLatS(ovKpi.p95))}
              {@render kpi('Error %', `${((Number(ovKpi.error_rate) || 0) * 100).toFixed(1)}%`, '', null, Number(ovKpi.error_rate) > 0.05)}
              {@render kpi('Cache-hit %', `${((Number(ovKpi.cache_hit_rate) || 0) * 100).toFixed(0)}%`)}
              {@render kpi('Stream %', `${Number(ovKpi.stream_pct ?? 0)}%`)}
              {@render kpi('Active keys', `${ovKpi.active_keys ?? 0}/${ovKpi.total_keys ?? 0}`)}
            </div>
          </section>

          <div class="gw-an-2col">
            <!-- ═══════════ C. ACTIVITY CHART ═══════════ -->
            <section class="gw-panel">
              <div class="gw-h-row">
                <div class="gw-subh">Activity</div>
                <div class="gw-pills">
                  <button class="gw-pill" class:gw-pill-on={anMetric === 'requests'} onclick={() => anMetric = 'requests'}>● requests</button>
                  <button class="gw-pill" class:gw-pill-on={anMetric === 'tokens'} onclick={() => anMetric = 'tokens'}>○ tokens</button>
                  <button class="gw-pill" class:gw-pill-on={anMetric === 'latency'} onclick={() => anMetric = 'latency'}>○ latency</button>
                </div>
              </div>
              {#if actBars.length === 0}
                <div class="gw-row gw-muted">no activity in window</div>
              {:else}
                <div class="gw-chart">
                  {#each actBars as b}
                    <div class="gw-chart-col" title={b.title}>
                      <div class="gw-chart-barwrap">
                        {#if anMetric === 'tokens'}
                          <div class="gw-chart-stack" style={`height:${b.pct}%`}>
                            <div class="gw-chart-seg-c" style={`height:${b.cpct}%`}></div>
                            <div class="gw-chart-seg-p" style={`height:${b.ppct}%`}></div>
                          </div>
                        {:else}
                          <div class="gw-chart-bar" style={`height:${b.pct}%`}></div>
                        {/if}
                      </div>
                      <div class="gw-chart-x">{b.label}</div>
                    </div>
                  {/each}
                </div>
                {#if anMetric === 'tokens'}
                  <div class="gw-legend"><span class="gw-leg-p">■</span> prompt <span class="gw-leg-c">■</span> completion</div>
                {/if}
              {/if}
            </section>

            <!-- ═══════════ D. SPEND PANEL ═══════════ -->
            <section class="gw-panel">
              <div class="gw-subh">Spend</div>
              {#if costOff}
                <div class="gw-soft-card">
                  <div class="gw-soft-title">Cost tracking off</div>
                  <div class="gw-muted gw-soft-note">⚙ model pricing not set — set per-model rates to see spend over time.</div>
                </div>
              {:else if costBars.length === 0}
                <div class="gw-row gw-muted">no spend in window</div>
              {:else}
                <div class="gw-chart gw-chart-sm">
                  {#each costBars as b}
                    <div class="gw-chart-col" title={b.title}>
                      <div class="gw-chart-barwrap"><div class="gw-chart-bar gw-chart-bar-cost" style={`height:${b.pct}%`}></div></div>
                      <div class="gw-chart-x">{b.label}</div>
                    </div>
                  {/each}
                </div>
                <div class="gw-muted gw-soft-note">total ${Number(ovKpi.cost ?? 0).toFixed(4)} this window</div>
              {/if}
            </section>
          </div>

          <div class="gw-an-2col">
            <!-- ═══════════ E. LATENCY DISTRIBUTION ═══════════ -->
            <section class="gw-panel">
              <div class="gw-subh">Latency distribution</div>
              <div class="gw-lat-pcts">
                <span><span class="gw-k">p50</span>{fmtLatS(ovKpi.p50)}</span>
                <span><span class="gw-k">p90</span>{fmtLatS(ovKpi.p90)}</span>
                <span><span class="gw-k">p95</span>{fmtLatS(ovKpi.p95)}</span>
                <span><span class="gw-k">p99</span>{fmtLatS(ovKpi.p99)}</span>
              </div>
              {#if ovLatHist.length === 0}
                <div class="gw-row gw-muted">no latency data</div>
              {:else}
                <div class="gw-hbars">
                  {#each ovLatHist as h}
                    <div class="gw-hbar-row" class:gw-hbar-tail={isTail(h.bucket)}>
                      <div class="gw-hbar-lbl">{h.bucket}{#if isTail(h.bucket)} <span class="gw-warn">⚠</span>{/if}</div>
                      <div class="gw-hbar-track"><div class="gw-hbar-fill" class:gw-hbar-fill-tail={isTail(h.bucket)} style={`width:${Math.max(1, Number(h.pct) || 0)}%`}></div></div>
                      <div class="gw-hbar-val">{h.count} <span class="gw-muted">({(Number(h.pct) || 0).toFixed(0)}%)</span></div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>

            <!-- ═══════════ F. TOKEN SPLIT ═══════════ -->
            <section class="gw-panel">
              <div class="gw-subh">Token split</div>
              {#if tokSplit.total === 0}
                <div class="gw-row gw-muted">no tokens in window</div>
              {:else}
                <div class="gw-tsplit-bar">
                  <div class="gw-tsplit-p" style={`width:${tokSplit.ppct}%`} title={`prompt ${tokSplit.prompt}`}></div>
                  <div class="gw-tsplit-c" style={`width:${tokSplit.cpct}%`} title={`completion ${tokSplit.completion}`}></div>
                </div>
                <div class="gw-tsplit-legend">
                  <span><span class="gw-leg-p">■</span> prompt {fmtTokens(tokSplit.prompt)} · {tokSplit.ppct}%</span>
                  <span><span class="gw-leg-c">■</span> completion {fmtTokens(tokSplit.completion)} · {tokSplit.cpct}%</span>
                  <span class="gw-muted">total {fmtTokens(tokSplit.total)}</span>
                </div>
              {/if}
            </section>
          </div>

          <!-- ═══════════ G. BY MODEL ═══════════ -->
          <section class="gw-panel">
            <div class="gw-subh">By model</div>
            {#if ovByModel.length === 0}<div class="gw-row gw-muted">no model data</div>
            {:else}
              {@const mTot = ovByModel.reduce((a: number, m: any) => a + (Number(m.requests) || 0), 0) || 1}
              <table class="gw-table">
                <thead><tr><th>model</th><th>req</th><th>tok (in/out)</th><th>cost</th><th>avg lat</th><th>err</th><th>share</th></tr></thead>
                <tbody>
                  {#each ovByModel as m (m.model)}
                    {@const share = Math.round(((Number(m.requests) || 0) / mTot) * 100)}
                    <tr>
                      <td><code class="gw-code">{m.model}</code></td>
                      <td>{m.requests ?? 0}</td>
                      <td>{fmtTokens(m.tokens)} <span class="gw-muted">({fmtTokens(m.prompt_tokens)}/{fmtTokens(m.completion_tokens)})</span></td>
                      <td>${Number(m.cost ?? 0).toFixed(4)}</td>
                      <td>{fmtLatS(m.avg_latency_ms)}</td>
                      <td>{m.errors ?? 0}{#if Number(m.errors) > 0} <span class="gw-err">⚠</span>{/if}</td>
                      <td><div class="gw-share"><div class="gw-share-track"><div class="gw-share-fill" style={`width:${share}%`}></div></div><span class="gw-share-pct">{share}%</span></div></td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>

          <!-- ═══════════ H. BY KEY (drill-down preserved) ═══════════ -->
          <section class="gw-panel">
            <div class="gw-h-row"><div class="gw-subh">By key</div><button class="gw-btn gw-btn-sm" onclick={anExportCsv}>⬇ CSV</button></div>
            {#if ovByKey.length === 0}<div class="gw-row gw-muted">no usage in window</div>
            {:else}
              <table class="gw-table">
                <thead><tr><th>key</th><th>scope</th><th>store</th><th>req</th><th>tok</th><th>cost</th><th>avg lat</th><th>err</th><th>stream</th><th>last</th></tr></thead>
                <tbody>
                  {#each ovByKey as row (row.key)}
                    <tr>
                      <td>{#if row.key}<button type="button" class="gw-code gw-code-btn" onclick={() => openOutlet(row.key)} title="open outlet detail page">{row.key}</button>{:else}<code class="gw-code">—</code>{/if}</td>
                      <td><span class="gw-scope-badge" class:gw-scope-g={row.scope === 'global'}>● {row.scope ?? '—'}</span></td>
                      <td>{row.store ?? '—'}</td>
                      <td>{row.requests ?? 0}</td>
                      <td>{fmtTokens(row.tokens)}</td>
                      <td>${Number(row.cost ?? 0).toFixed(4)}</td>
                      <td>{fmtLatS(row.avg_latency_ms)}</td>
                      <td>{row.errors ?? 0}{#if Number(row.errors) > 0} <span class="gw-err">⚠</span>{/if}</td>
                      <td>{Number(row.stream_pct ?? 0)}%</td>
                      <td class="gw-muted">{row.last ?? '—'}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>

          <!-- ═══════════ I. BY OUTLET ═══════════ -->
          <section class="gw-panel">
            <div class="gw-subh">By outlet</div>
            {#if ovByStore.length === 0}<div class="gw-row gw-muted">no outlet data</div>
            {:else}
              <table class="gw-table">
                <thead><tr><th>store</th><th>req</th><th>tok</th><th>cost</th><th>err</th><th>top key</th><th>last</th></tr></thead>
                <tbody>
                  {#each ovByStore as s (s.store)}
                    <tr>
                      <td><code class="gw-code">{s.store}</code></td>
                      <td>{s.requests ?? 0}</td>
                      <td>{fmtTokens(s.tokens)}</td>
                      <td>${Number(s.cost ?? 0).toFixed(4)}</td>
                      <td>{s.errors ?? 0}{#if Number(s.errors) > 0} <span class="gw-err">⚠</span>{/if}</td>
                      <td><code class="gw-code">{s.top_key ?? '—'}</code></td>
                      <td class="gw-muted">{s.last ?? '—'}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>

          <!-- ═══════════ J. ERRORS & RELIABILITY ═══════════ -->
          <section class="gw-panel">
            <div class="gw-subh">Errors & reliability</div>
            {#if !ovErrors || (Number(ovErrors.total) === 0)}
              <div class="gw-row gw-muted">✓ no errors in window — {((Number(ovKpi.error_rate) || 0) * 100).toFixed(1)}% rate</div>
            {:else}
              <div class="gw-status-grid">
                <div><span class="gw-k">rate</span>{((Number(ovErrors.rate) || 0) * 100).toFixed(1)}%</div>
                <div><span class="gw-k">total</span>{ovErrors.total ?? 0}</div>
              </div>
              {#if Array.isArray(ovErrors.by_status) && ovErrors.by_status.length}
                <div class="gw-err-status">
                  {#each ovErrors.by_status as st}
                    <span class="gw-err-chip">{st.status} <b>{st.count}</b></span>
                  {/each}
                </div>
              {/if}
              {#if Array.isArray(ovErrors.recent) && ovErrors.recent.length}
                <table class="gw-table gw-mt">
                  <thead><tr><th>time</th><th>key</th><th>store</th><th>status</th></tr></thead>
                  <tbody>
                    {#each ovErrors.recent as e}
                      <tr>
                        <td class="gw-muted">{e.ts ?? '—'}</td>
                        <td><code class="gw-code">{e.key ?? '—'}</code></td>
                        <td>{e.store ?? '—'}</td>
                        <td><span class="gw-err">{e.status ?? '—'}</span></td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            {/if}
          </section>

          <div class="gw-an-2col">
            <!-- ═══════════ K. TOP QUESTIONS / INTENTS ═══════════ -->
            <section class="gw-panel">
              <div class="gw-subh">Top questions / intents</div>
              {#if questions && questions.messages_enabled === false}
                <div class="gw-row gw-muted gw-soft-note">chat bodies not logged — enable APIGW_LOG_BODIES to classify intents.</div>
              {:else if !questions || !Array.isArray(questions.intents) || questions.intents.length === 0}
                <div class="gw-row gw-muted">no intent data</div>
              {:else}
                {@const qMax = Math.max(1, ...questions.intents.map((i: any) => Number(i.count) || 0))}
                <div class="gw-hbars">
                  {#each questions.intents as it}
                    <div class="gw-hbar-row">
                      <div class="gw-hbar-lbl">{it.label}</div>
                      <div class="gw-hbar-track"><div class="gw-hbar-fill" style={`width:${Math.max(1, Math.round(((Number(it.count) || 0) / qMax) * 100))}%`}></div></div>
                      <div class="gw-hbar-val">{it.count} <span class="gw-muted">({(Number(it.pct) || 0).toFixed(0)}%)</span></div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>

            <!-- Top Tools -->
            <section class="gw-panel">
              <div class="gw-subh">Top tools</div>
              {#if !tools || !Array.isArray(tools.tools) || tools.tools.length === 0}
                <div class="gw-row gw-muted">no tool data</div>
              {:else}
                {@const tMax = Math.max(1, ...tools.tools.map((t: any) => Number(t.count) || 0))}
                <div class="gw-hbars">
                  {#each tools.tools as t}
                    <div class="gw-hbar-row">
                      <div class="gw-hbar-lbl"><code class="gw-code">{t.tool}</code></div>
                      <div class="gw-hbar-track"><div class="gw-hbar-fill gw-hbar-fill-tool" style={`width:${Math.max(1, Math.round(((Number(t.count) || 0) / tMax) * 100))}%`}></div></div>
                      <div class="gw-hbar-val">{t.count}</div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          </div>
        {:else}
          <section class="gw-panel"><div class="gw-row gw-muted">◐ loading analytics…</div></section>
        {/if}
      {/if}

      <!-- ═══════════════════ PER-OUTLET DETAIL PAGE ═══════════════════ -->
      {#if view === 'outlet'}
        {@const oh = outHeader}
        <!-- 1. HEADER BAR -->
        <section class="gw-panel gw-ol-headpanel">
          <div class="gw-ol-headbar">
            <button class="gw-btn gw-btn-sm gw-ol-back" onclick={backToAnalytics}>← Back to Analytics</button>
            <div class="gw-ol-headmeta">
              <code class="gw-code gw-ol-keychip">{outletKey ?? '—'}</code>
              {#if oh.scope}<span class="gw-scope-badge" class:gw-scope-g={oh.scope === 'global'}>● {oh.scope}</span>{/if}
              <span class="gw-muted gw-ol-headsub"><span class="gw-k">stores</span>{oh.stores ?? '—'}</span>
              <span class="gw-muted gw-ol-headsub"><span class="gw-k">minted</span>{oh.minted ?? '—'}</span>
              {#if outletBusy}<span class="gw-muted gw-an-busy">◐ loading…</span>{/if}
            </div>
            <div class="gw-ol-headact">
              <div class="gw-pills">
                <button class="gw-pill" class:gw-pill-on={outletRange === '24h'} onclick={() => setOutletRange('24h')}>24h</button>
                <button class="gw-pill" class:gw-pill-on={outletRange === '7d'} onclick={() => setOutletRange('7d')}>7d</button>
                <button class="gw-pill" class:gw-pill-on={outletRange === '30d'} onclick={() => setOutletRange('30d')}>30d</button>
              </div>
              <button class="gw-btn gw-btn-sm" onclick={outletExportJson} disabled={!outletOv}>⬇ JSON</button>
            </div>
          </div>
        </section>

        {#if outletErr}
          <section class="gw-panel"><div class="gw-row gw-err">✗ outlet detail unavailable ({outletErr})</div></section>
        {:else if outletBusy && !outletOv && !outletDetail}
          <section class="gw-panel"><div class="gw-row gw-muted">◐ loading outlet…</div></section>
        {:else}
          <!-- 2. KPI STRIP -->
          <section class="gw-panel">
            <div class="gw-kpi-grid">
              {#snippet olKpi(label: string, value: any, sub: string = '', warn: boolean = false)}
                <div class="gw-kpi" class:gw-kpi-warn={warn}>
                  <div class="gw-kpi-label">{label}</div>
                  <div class="gw-kpi-val">{value}{#if warn} <span class="gw-err">⚠</span>{/if}</div>
                  {#if sub}<div class="gw-kpi-sub">{sub}</div>{/if}
                </div>
              {/snippet}
              {@render olKpi('Calls', outKpi.requests ?? oh.calls ?? 0)}
              {@render olKpi('Tokens', fmtTokens(outKpi.tokens ?? oh.tokens), `${fmtTokens(outKpi.prompt_tokens ?? oh.prompt_tokens)} in / ${fmtTokens(outKpi.completion_tokens ?? oh.completion_tokens)} out`)}
              {@render olKpi('Cost', `$${Number(outKpi.cost ?? oh.cost ?? 0).toFixed(4)}`)}
              {@render olKpi('Avg latency', fmtLatS(outKpi.avg_latency_ms ?? oh.avg_latency_ms), '', Number(outKpi.avg_latency_ms ?? oh.avg_latency_ms) > 10000)}
              {@render olKpi('p95 latency', fmtLatS(outKpi.p95))}
              {@render olKpi('Errors', outKpi.errors ?? oh.errors ?? 0, '', Number(outKpi.errors ?? oh.errors) > 0)}
              {@render olKpi('Sessions', oh.sessions ?? 0)}
              {@render olKpi('Stream %', `${Number(outKpi.stream_pct ?? oh.stream_pct ?? 0)}%`)}
              {@render olKpi('Cache-hit %', `${((Number(outKpi.cache_hit_rate) || 0) * 100).toFixed(0)}%`)}
            </div>
          </section>

          <div class="gw-an-2col">
            <!-- 3a. ACTIVITY -->
            <section class="gw-panel">
              <div class="gw-h-row">
                <div class="gw-subh">Activity</div>
                <div class="gw-pills">
                  <button class="gw-pill" class:gw-pill-on={outletMetric === 'requests'} onclick={() => outletMetric = 'requests'}>● requests</button>
                  <button class="gw-pill" class:gw-pill-on={outletMetric === 'tokens'} onclick={() => outletMetric = 'tokens'}>○ tokens</button>
                  <button class="gw-pill" class:gw-pill-on={outletMetric === 'latency'} onclick={() => outletMetric = 'latency'}>○ latency</button>
                </div>
              </div>
              {#if outActBars.length === 0}
                <div class="gw-row gw-muted">no activity in window</div>
              {:else}
                <div class="gw-chart">
                  {#each outActBars as b}
                    <div class="gw-chart-col" title={b.title}>
                      <div class="gw-chart-barwrap">
                        {#if outletMetric === 'tokens'}
                          <div class="gw-chart-stack" style={`height:${b.pct}%`}>
                            <div class="gw-chart-seg-c" style={`height:${b.cpct}%`}></div>
                            <div class="gw-chart-seg-p" style={`height:${b.ppct}%`}></div>
                          </div>
                        {:else}
                          <div class="gw-chart-bar" style={`height:${b.pct}%`}></div>
                        {/if}
                      </div>
                      <div class="gw-chart-x">{b.label}</div>
                    </div>
                  {/each}
                </div>
                {#if outletMetric === 'tokens'}
                  <div class="gw-legend"><span class="gw-leg-p">■</span> prompt <span class="gw-leg-c">■</span> completion</div>
                {/if}
              {/if}
            </section>

            <!-- 3b. LATENCY DISTRIBUTION -->
            <section class="gw-panel">
              <div class="gw-subh">Latency distribution</div>
              <div class="gw-lat-pcts">
                <span><span class="gw-k">p50</span>{fmtLatS(outKpi.p50)}</span>
                <span><span class="gw-k">p90</span>{fmtLatS(outKpi.p90)}</span>
                <span><span class="gw-k">p95</span>{fmtLatS(outKpi.p95)}</span>
                <span><span class="gw-k">p99</span>{fmtLatS(outKpi.p99)}</span>
              </div>
              {#if outLatHist.length === 0}
                <div class="gw-row gw-muted">no latency data</div>
              {:else}
                <div class="gw-hbars">
                  {#each outLatHist as h}
                    <div class="gw-hbar-row" class:gw-hbar-tail={isTail(h.bucket)}>
                      <div class="gw-hbar-lbl">{h.bucket}{#if isTail(h.bucket)} <span class="gw-warn">⚠</span>{/if}</div>
                      <div class="gw-hbar-track"><div class="gw-hbar-fill" class:gw-hbar-fill-tail={isTail(h.bucket)} style={`width:${Math.max(1, Number(h.pct) || 0)}%`}></div></div>
                      <div class="gw-hbar-val">{h.count} <span class="gw-muted">({(Number(h.pct) || 0).toFixed(0)}%)</span></div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          </div>

          <div class="gw-an-2col">
            <!-- 4a. TOKEN SPLIT -->
            <section class="gw-panel">
              <div class="gw-subh">Token split</div>
              {#if outTokSplit.total === 0}
                <div class="gw-row gw-muted">no tokens in window</div>
              {:else}
                <div class="gw-tsplit-bar">
                  <div class="gw-tsplit-p" style={`width:${outTokSplit.ppct}%`} title={`prompt ${outTokSplit.prompt}`}></div>
                  <div class="gw-tsplit-c" style={`width:${outTokSplit.cpct}%`} title={`completion ${outTokSplit.completion}`}></div>
                </div>
                <div class="gw-tsplit-legend">
                  <span><span class="gw-leg-p">■</span> prompt {fmtTokens(outTokSplit.prompt)} · {outTokSplit.ppct}%</span>
                  <span><span class="gw-leg-c">■</span> completion {fmtTokens(outTokSplit.completion)} · {outTokSplit.cpct}%</span>
                  <span class="gw-muted">total {fmtTokens(outTokSplit.total)}</span>
                </div>
              {/if}
            </section>

            <!-- 4b. ERRORS & RELIABILITY -->
            <section class="gw-panel">
              <div class="gw-subh">Errors & reliability</div>
              {#if !outErrors || (Number(outErrors.total) === 0)}
                <div class="gw-row gw-muted">✓ no errors in window</div>
              {:else}
                <div class="gw-status-grid">
                  <div><span class="gw-k">rate</span>{((Number(outErrors.rate) || 0) * 100).toFixed(1)}%</div>
                  <div><span class="gw-k">total</span>{outErrors.total ?? 0}</div>
                </div>
                {#if Array.isArray(outErrors.by_status) && outErrors.by_status.length}
                  <div class="gw-err-status">
                    {#each outErrors.by_status as st}
                      <span class="gw-err-chip">{st.status} <b>{st.count}</b></span>
                    {/each}
                  </div>
                {/if}
                {#if Array.isArray(outErrors.recent) && outErrors.recent.length}
                  <table class="gw-table gw-mt">
                    <thead><tr><th>time</th><th>store</th><th>status</th></tr></thead>
                    <tbody>
                      {#each outErrors.recent as e}
                        <tr>
                          <td class="gw-muted">{e.ts ?? '—'}</td>
                          <td>{e.store ?? '—'}</td>
                          <td><span class="gw-err">{e.status ?? '—'}</span></td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
              {/if}
            </section>
          </div>

          <!-- 5. QUESTIONS -->
          <section class="gw-panel">
            <div class="gw-subh">Questions ({outQuestions.length})</div>
            {#if outletDetail && outletDetail.messages_enabled === false}
              <div class="gw-row gw-muted gw-ol-notice">chat bodies not logged (enable APIGW_LOG_BODIES to see questions)</div>
            {/if}
            {#if outQuestions.length === 0}
              <div class="gw-row gw-muted">no questions in window</div>
            {:else}
              <table class="gw-table gw-ol-qtable">
                <thead><tr><th>time</th><th>q</th><th>tok</th><th>lat</th><th>flags</th></tr></thead>
                <tbody>
                  {#each outQuestions as q (q.session_id + '|' + q.ts)}
                    {@const err = q.error || q.status}
                    <tr class="gw-ol-qrow" class:gw-ol-qrow-on={outletQExpanded === q.session_id} onclick={() => toggleOutletQ(q.session_id)}>
                      <td class="gw-ol-ts">{q.ts ?? '—'}</td>
                      <td>{q.question ? truncQ(q.question) : '—'}</td>
                      <td>{q.tokens ?? 0}</td>
                      <td>{fmtLatency(q.latency_ms)}</td>
                      <td class="gw-ol-flags">{#if q.masked}<span title="masked (store tier)">🛡</span>{/if}{#if err}<span class="gw-err" title={String(err)}>✗</span>{:else}<span class="gw-ol-ok" title="ok">✓</span>{/if}</td>
                    </tr>
                    {#if outletQExpanded === q.session_id}
                      <tr class="gw-ol-qdetail"><td colspan="5">
                        <div class="gw-ol-qfull">
                          <div class="gw-ol-qlabel">QUESTION</div>
                          <div class="gw-ol-qtext">{q.question ?? '—'}</div>
                          <div class="gw-ol-qlabel gw-mt">{q.masked ? '🛡 ANSWER · masked (store tier)' : 'ANSWER'}</div>
                          <div class="gw-ol-qtext">{q.answer ?? '—'}</div>
                          {#if err}<div class="gw-ol-qlabel gw-mt">ERROR</div><div class="gw-row gw-err">{q.error ?? q.status}</div>{/if}
                          <div class="gw-ol-qmeta gw-mt">
                            <span class="gw-k">session</span><code class="gw-code">{q.session_id ?? '—'}</code>
                            <span class="gw-k">model</span><code class="gw-code">{q.model ?? '—'}</code>
                            <span class="gw-k">tokens</span>{q.tokens ?? 0}
                            <span class="gw-k">latency</span>{fmtLatency(q.latency_ms)}
                          </div>
                        </div>
                      </td></tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>

          <!-- 6. TOP INTENTS (this outlet) -->
          <section class="gw-panel">
            <div class="gw-subh">Top intents (this outlet)</div>
            {#if outletIntents && outletIntents.messages_enabled === false}
              <div class="gw-row gw-muted gw-ol-notice">chat bodies not logged — enable APIGW_LOG_BODIES to classify intents.</div>
            {:else if !outletIntents || !Array.isArray(outletIntents.intents) || outletIntents.intents.length === 0}
              <div class="gw-row gw-muted">no intent data</div>
            {:else}
              {@const oqMax = Math.max(1, ...outletIntents.intents.map((i: any) => Number(i.count) || 0))}
              <div class="gw-hbars">
                {#each outletIntents.intents as it}
                  <div class="gw-hbar-row">
                    <div class="gw-hbar-lbl">{it.label}</div>
                    <div class="gw-hbar-track"><div class="gw-hbar-fill" style={`width:${Math.max(1, Math.round(((Number(it.count) || 0) / oqMax) * 100))}%`}></div></div>
                    <div class="gw-hbar-val">{it.count} <span class="gw-muted">({(Number(it.pct) || 0).toFixed(0)}%)</span></div>
                  </div>
                {/each}
              </div>
            {/if}
          </section>
        {/if}
      {/if}

      <!-- DOCS (folded: auth · schemas · streaming · errors) -->
      {#if view === 'docs'}
        <div class="gw-doc-banner">
          <span class="gw-muted">Per-outlet copy-paste code (PHP · cURL · Python · .env) lives in <button class="gw-link" onclick={() => nav('provision')}>Outlet Keys</button>. This page = API reference only.</span>
          <a class="gw-doc-link" href="/api/v1/docs" target="_blank" rel="noopener">Open full docs ↗</a>
        </div>

        <section class="gw-panel">
          <div class="gw-h">AUTH</div>
          <p class="gw-doc-p">Every request needs a service-account key in the header. Outlet keys are minted in <button class="gw-link" onclick={() => nav('provision')}>Outlet Keys</button> (one per branch); global keys via <button class="gw-link" onclick={() => nav('console')}>Console</button>.</p>
          <div class="gw-codeblock"><pre class="gw-pre">Authorization: Bearer dash-key-XXXX</pre></div>
          <p class="gw-doc-p"><strong>scope_mode</strong>:</p>
          <ul class="gw-ul">
            <li><code class="gw-code">store</code> — tiered: own outlet = full (qty+cost), other stores = availability-only, reference open.</li>
            <li><code class="gw-code">global</code> — no masking, full data + aggregates (internal/BI only).</li>
          </ul>
          <p class="gw-doc-p gw-muted">Boundary = toolset, not prompt — store keys lose raw SQL at build time.</p>
        </section>

        <section class="gw-panel">
          <div class="gw-h">SCHEMAS</div>
          <div class="gw-subh">Request</div>
          <div class="gw-codeblock"><button class="gw-copybtn" onclick={() => copyText(reqSchema, 'req')}>{copied === 'req' ? '✓' : 'copy'}</button><pre class="gw-pre">{reqSchema}</pre></div>
          <div class="gw-subh gw-mt">Response — blocking</div>
          <div class="gw-codeblock"><button class="gw-copybtn" onclick={() => copyText(respSchema, 'resp')}>{copied === 'resp' ? '✓' : 'copy'}</button><pre class="gw-pre">{respSchema}</pre></div>
          <div class="gw-subh gw-mt">Response — streaming chunks</div>
          <div class="gw-codeblock"><button class="gw-copybtn" onclick={() => copyText(chunkSchema, 'chunk')}>{copied === 'chunk' ? '✓' : 'copy'}</button><pre class="gw-pre">{chunkSchema}</pre></div>
        </section>

        <section class="gw-panel">
          <div class="gw-h">STREAMING</div>
          <p class="gw-doc-p">Set <code class="gw-code">"stream": true</code> → <code class="gw-code">text/event-stream</code> of <code class="gw-code">chat.completion.chunk</code> frames, terminated by <code class="gw-code">data: [DONE]</code>.</p>
          <div class="gw-codeblock"><button class="gw-copybtn" onclick={() => copyText(streamCurlSnippet, 'scurl')}>{copied === 'scurl' ? '✓' : 'copy'}</button><pre class="gw-pre">{streamCurlSnippet}</pre></div>
        </section>

        <section class="gw-panel">
          <div class="gw-h">ERRORS</div>
          <table class="gw-table">
            <thead><tr><th>status</th><th>meaning</th></tr></thead>
            <tbody>
              <tr><td><code class="gw-code">401</code></td><td>Missing / invalid API key</td></tr>
              <tr><td><code class="gw-code">400</code></td><td>Bad body — non-JSON, empty <code class="gw-code">messages</code>, or no user message</td></tr>
              <tr><td><code class="gw-code">413</code></td><td>Message too long (&gt; 50,000 chars)</td></tr>
              <tr><td><code class="gw-code">429</code></td><td>Rate limit exceeded — honour <code class="gw-code">Retry-After</code> (seconds)</td></tr>
              <tr><td><code class="gw-code">403</code></td><td>(mint/revoke) not a super-admin session, or an api-key bearer</td></tr>
            </tbody>
          </table>
        </section>
      {/if}

    </main>
  </div>
{/if}

<style>
  .gw-center { min-height: 60vh; display: flex; align-items: center; justify-content: center; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .gw-denied { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; }
  .gw-denied-mark { font-size: 32px; color: #c0392b; }
  .gw-denied-title { font-size: 18px; font-weight: 600; color: var(--pw-accent); }

  /* layout */
  .gw-layout { display: grid; grid-template-columns: 240px 1fr; height: calc(100vh - 64px); min-height: 0; overflow: hidden; align-items: stretch; }
  .gw-rail {
    background: var(--pw-bg-alt, #f6f2ea);
    border-right: 1px solid var(--pw-border, #e5ddcf);
    padding: 0 8px 120px;
    font-family: inherit;
    /* Pin the rail: it stays in view while only the right pane scrolls. */
    align-self: stretch;
    height: 100%;
    min-height: 0;
    overflow-y: auto;
  }
  .gw-rg { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
  .gw-rg-label { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); padding: 12px 14px 6px; font-weight: 600; }
  .gw-rg-item {
    display: flex; align-items: center; gap: 10px; width: 100%;
    background: transparent; border: none; cursor: pointer;
    padding: 8px 12px; font-family: inherit; font-size: 12px; line-height: 1.3;
    color: var(--pw-ink, #2c2a26); text-align: left; border-radius: var(--pw-radius-sm);
    border-left: 2px solid transparent;
  }
  .gw-rg-item:hover { background: rgba(201, 99, 66, 0.04); }
  .gw-rg-on { background: rgba(201, 99, 66, 0.08); color: var(--pw-accent); font-weight: 600; }
  .gw-rg-icon { width: 14px; height: 14px; flex: 0 0 auto; color: var(--pw-muted, #877f74); }
  .gw-rg-on .gw-rg-icon { color: var(--pw-accent); }
  .gw-rg-text { flex: 1; }

  .gw-main { padding: 28px 48px 80px; max-width: 1100px; min-height: 0; overflow-y: auto; overscroll-behavior: contain; font-family: inherit; }
  .gw-main-wide { max-width: none; }

  /* ── per-outlet detail page ── */
  .gw-ol-headpanel { background: #fff6ec; border-color: #e5ddcf; }
  .gw-ol-headbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .gw-ol-back { border-color: #c96342; color: #9a4a2f; font-weight: 600; }
  .gw-ol-back:hover { background: #f3ece1; }
  .gw-ol-headmeta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; flex: 1; }
  .gw-ol-keychip { font-weight: 600; }
  .gw-ol-headsub { font-size: 12px; display: inline-flex; align-items: center; gap: 4px; }
  .gw-ol-headact { display: flex; align-items: center; gap: 10px; margin-left: auto; }
  .gw-ol-notice { font-style: italic; }
  .gw-ol-qtable td { vertical-align: top; }
  .gw-ol-qrow { cursor: pointer; }
  .gw-ol-qrow:hover { background: rgba(201, 99, 66, 0.04); }
  .gw-ol-qrow-on { background: rgba(201, 99, 66, 0.08); }
  .gw-ol-ts { color: var(--pw-muted, #877f74); font-size: 12px; white-space: nowrap; }
  .gw-ol-flags { white-space: nowrap; }
  .gw-ol-ok { color: #2d8a4e; }
  .gw-ol-qdetail > td { background: #fff6ec; }
  .gw-ol-qfull { padding: 10px 4px; }
  .gw-ol-qlabel { font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: #9a4a2f; font-weight: 700; margin-bottom: 4px; }
  .gw-ol-qtext { font-size: 13px; line-height: 1.5; color: var(--pw-ink, #2c2a26); white-space: pre-wrap; }
  .gw-ol-qmeta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; }
  .gw-pagehead { margin-bottom: 22px; }
  .gw-pagetitle { font-family: var(--pw-serif, Georgia, serif); font-size: 26px; font-weight: 600; color: var(--pw-ink, #2c2a26); margin: 0 0 4px; }
  .gw-pagesub { color: var(--pw-muted, #877f74); font-size: 13px; margin: 0; }

  .gw-panel { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5ddcf); border-radius: var(--pw-radius-sm); padding: 16px 18px; margin-bottom: 16px; }
  .gw-h { color: var(--pw-accent); font-weight: 700; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px; }
  .gw-h-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; gap: 10px; }
  .gw-subh { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); }
  .gw-mt { margin-top: 14px; }

  .gw-status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; font-size: 13px; }
  .gw-status-grid > div { display: flex; align-items: center; gap: 8px; }
  .gw-k { display: inline-block; min-width: 78px; color: var(--pw-muted, #877f74); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }

  .gw-dot { font-size: 11px; }
  .gw-on { color: #2d8a4e; }
  .gw-off { color: #c0392b; }
  .gw-amber { color: #c08a00; }
  .gw-open { color: var(--pw-muted, #877f74); }

  .gw-code { background: #f3ece1; color: #9a4a2f; border: 1px solid #e5ddcf; padding: 1px 6px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; border-radius: 3px; }
  .gw-code-key { display: inline-block; flex: 1; }

  .gw-ep { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .gw-ep-line { font-size: 13px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .gw-ep-sub { font-size: 12px; padding-left: 4px; }
  .gw-method { display: inline-block; background: #2d8a4e; color: #fff; font-size: 10px; font-weight: 700; padding: 2px 6px; letter-spacing: 0.04em; }
  .gw-method.gw-post { background: var(--pw-accent); }

  .gw-btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
  .gw-btn { background: transparent; color: var(--pw-ink-soft, #4a4438); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 6px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; cursor: pointer; border-radius: var(--pw-radius-sm); }
  .gw-btn:hover { border-color: var(--pw-accent); color: var(--pw-accent); }
  .gw-btn:disabled { opacity: 0.5; cursor: default; }
  .gw-btn-sm { padding: 3px 10px; font-size: 11px; }
  .gw-btn-accent { background: var(--pw-accent); color: #fff; border-color: var(--pw-accent); }
  .gw-btn-accent:hover { background: var(--pw-accent-strong, #b8553a); color: #fff; }

  .gw-mint { border: 1px dashed var(--pw-border-strong, #cdc6b8); padding: 14px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 12px; }
  .gw-mint-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .gw-field { display: flex; flex-direction: column; gap: 5px; }
  .gw-flabel { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted, #877f74); }
  .gw-radio { display: flex; gap: 16px; font-size: 12px; align-items: center; }
  .gw-radio label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
  .gw-mint-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

  .gw-input { background: var(--pw-bg-alt, #f6f2ea); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 6px 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--pw-ink, #2c2a26); border-radius: var(--pw-radius-sm); outline: none; }
  .gw-input:focus { border-color: var(--pw-accent); }
  .gw-input-num { width: 90px; }
  .gw-input-search { width: 220px; }

  .gw-picker { display: flex; gap: 8px; align-items: center; }
  .gw-picker .gw-input { flex: 1; }
  .gw-choices { display: flex; flex-wrap: wrap; gap: 5px; max-height: 120px; overflow-y: auto; padding: 4px; background: var(--pw-bg-alt, #f6f2ea); }
  .gw-choice { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 3px 9px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; cursor: pointer; border-radius: var(--pw-radius-sm); }
  .gw-choice:hover { border-color: var(--pw-accent); color: var(--pw-accent); }

  .gw-outlet-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
  .gw-outlet { background: var(--pw-bg-alt, #f6f2ea); border: 1px solid var(--pw-border, #e5ddcf); padding: 6px 8px; text-align: left; cursor: pointer; border-radius: var(--pw-radius-sm); }
  .gw-outlet:hover { border-color: var(--pw-accent); }

  .gw-chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .gw-chip { display: inline-flex; align-items: center; gap: 4px; background: var(--pw-accent); color: #fff; padding: 3px 4px 3px 9px; font-size: 11px; }
  .gw-chip-ro { background: var(--pw-bg-alt, #f6f2ea); color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 3px 9px; }
  .gw-chip-x { background: rgba(255,255,255,0.2); border: none; color: #fff; cursor: pointer; padding: 0 5px; font-size: 12px; line-height: 1; }
  .gw-chip-x:hover { background: rgba(255,255,255,0.4); }

  .gw-minted { display: flex; flex-direction: column; gap: 6px; }
  .gw-minted-row { display: flex; gap: 8px; align-items: center; }

  .gw-expand { background: none; border: none; padding: 0; cursor: pointer; color: var(--pw-accent); font-family: inherit; font-size: 12px; }
  .gw-subrow td { background: var(--pw-bg-alt, #f6f2ea); }

  .gw-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .gw-table th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted, #877f74); padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e5ddcf); font-weight: 600; }
  .gw-table td { padding: 7px 8px; border-bottom: 1px solid var(--pw-border, #ece6d9); vertical-align: middle; }
  .gw-badge { font-size: 11px; }
  .gw-badge-on { color: #2d8a4e; }
  .gw-badge-off { color: #c0392b; }

  .gw-rate { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .gw-saved { color: #2d8a4e; font-size: 12px; }
  .gw-fineprint { font-size: 11px; margin-top: 8px; }

  .gw-pills { display: flex; gap: 4px; }
  .gw-pill { background: transparent; border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 3px 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; cursor: pointer; border-radius: var(--pw-radius-sm); color: var(--pw-ink-soft, #4a4438); }
  .gw-pill-on { background: var(--pw-accent); color: #fff; border-color: var(--pw-accent); }

  .gw-spark { display: flex; align-items: flex-end; gap: 3px; height: 60px; margin: 14px 0; padding: 4px; background: var(--pw-bg-alt, #f6f2ea); }
  .gw-spark-col { flex: 1; height: 100%; display: flex; align-items: flex-end; }
  .gw-spark-bar { width: 100%; background: var(--pw-accent); min-height: 2px; }

  /* ── Gateway Analytics (OpenRouter-style) ── */
  .gw-an-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .gw-an-title { display: flex; align-items: center; gap: 10px; }
  .gw-an-live { color: #2d8a4e; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0.5px; }
  .gw-an-exports { display: flex; gap: 6px; }
  .gw-an-filters { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
  .gw-an-sel { border: 1px solid var(--pw-border-strong, #cdc6b8); background: #fff6ec; color: var(--pw-ink-soft, #4a4438); font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; padding: 3px 8px; border-radius: var(--pw-radius-sm); max-width: 200px; }
  .gw-an-gran { margin-left: auto; }
  .gw-an-busy { font-size: 12px; }
  .gw-an-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

  /* KPI strip */
  .gw-kpi-grid { display: flex; flex-wrap: wrap; gap: 10px; }
  .gw-kpi { flex: 1 1 110px; min-width: 110px; background: #fff6ec; border: 1px solid var(--pw-border, #ece6d9); border-radius: 8px; padding: 10px 12px; }
  .gw-kpi-warn { border-color: #e3b96a; }
  .gw-kpi-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--pw-muted, #877f74); }
  .gw-kpi-val { font-size: 19px; font-weight: 600; color: var(--pw-ink, #2c2a26); margin-top: 2px; }
  .gw-kpi-sub { font-size: 10.5px; color: var(--pw-muted, #877f74); margin-top: 1px; }
  .gw-kpi-delta { font-size: 11px; margin-top: 3px; color: var(--pw-muted, #877f74); }
  .gw-kpi-delta.gw-up { color: #2d8a4e; }
  .gw-kpi-delta.gw-down { color: #c0392b; }

  /* vertical bar chart */
  .gw-chart { display: flex; align-items: flex-end; gap: 4px; height: 150px; margin: 12px 0 4px; padding: 6px 4px 0; background: var(--pw-bg-alt, #f6f2ea); border-radius: 6px; }
  .gw-chart-sm { height: 110px; }
  .gw-chart-col { flex: 1; min-width: 6px; display: flex; flex-direction: column; align-items: center; height: 100%; }
  .gw-chart-barwrap { flex: 1; width: 100%; display: flex; align-items: flex-end; justify-content: center; }
  .gw-chart-bar { width: 72%; max-width: 26px; background: var(--pw-accent, #9a4a2f); min-height: 2px; border-radius: 2px 2px 0 0; }
  .gw-chart-bar-cost { background: #c96342; }
  .gw-chart-stack { width: 72%; max-width: 26px; display: flex; flex-direction: column; justify-content: flex-end; min-height: 2px; border-radius: 2px 2px 0 0; overflow: hidden; }
  .gw-chart-seg-c { width: 100%; background: #c96342; }
  .gw-chart-seg-p { width: 100%; background: #6b3320; }
  .gw-chart-x { font-size: 9px; color: var(--pw-muted, #877f74); margin-top: 3px; white-space: nowrap; transform: rotate(0deg); overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
  .gw-legend { font-size: 11px; color: var(--pw-muted, #877f74); display: flex; gap: 12px; }
  .gw-leg-p { color: #6b3320; }
  .gw-leg-c { color: #c96342; }

  /* soft card (cost off etc.) */
  .gw-soft-card { background: #fff6ec; border: 1px dashed var(--pw-border-strong, #cdc6b8); border-radius: 8px; padding: 16px; margin-top: 8px; }
  .gw-soft-title { font-size: 13px; font-weight: 600; color: var(--pw-ink, #2c2a26); }
  .gw-soft-note { font-size: 11.5px; margin-top: 4px; }

  /* latency pcts + horizontal bars */
  .gw-lat-pcts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin: 8px 0 10px; }
  .gw-hbars { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .gw-hbar-row { display: grid; grid-template-columns: 90px 1fr auto; align-items: center; gap: 10px; font-size: 12px; }
  .gw-hbar-lbl { color: var(--pw-ink-soft, #4a4438); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .gw-hbar-track { background: var(--pw-bg-alt, #f0ebe1); height: 14px; border-radius: 3px; overflow: hidden; }
  .gw-hbar-fill { height: 100%; background: var(--pw-accent, #9a4a2f); border-radius: 3px; }
  .gw-hbar-fill-tool { background: #c96342; }
  .gw-hbar-fill-tail { background: #c08a00; }
  .gw-hbar-tail .gw-hbar-lbl { color: #a06b00; }
  .gw-hbar-val { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--pw-ink-soft, #4a4438); }

  /* token split bar */
  .gw-tsplit-bar { display: flex; height: 22px; border-radius: 5px; overflow: hidden; margin: 8px 0; background: var(--pw-bg-alt, #f0ebe1); }
  .gw-tsplit-p { background: #6b3320; }
  .gw-tsplit-c { background: #c96342; }
  .gw-tsplit-legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 11.5px; color: var(--pw-ink-soft, #4a4438); }

  /* share bar (by model) */
  .gw-share { display: flex; align-items: center; gap: 6px; min-width: 90px; }
  .gw-share-track { flex: 1; background: var(--pw-bg-alt, #f0ebe1); height: 10px; border-radius: 3px; overflow: hidden; }
  .gw-share-fill { height: 100%; background: var(--pw-accent, #9a4a2f); }
  .gw-share-pct { font-size: 11px; color: var(--pw-muted, #877f74); font-variant-numeric: tabular-nums; }

  /* scope badge */
  .gw-scope-badge { font-size: 10.5px; padding: 1px 7px; border-radius: 10px; background: #f3ece1; color: #9a4a2f; border: 1px solid #e6d9c8; white-space: nowrap; }
  .gw-scope-g { background: #e8f3ec; color: #2d8a4e; border-color: #cfe6d6; }

  /* errors panel */
  .gw-err-status { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .gw-err-chip { font-size: 11.5px; background: #fbece9; color: #c0392b; border: 1px solid #f0cfca; border-radius: 10px; padding: 2px 9px; }

  @media (max-width: 760px) {
    .gw-an-2col { grid-template-columns: 1fr; }
    .gw-an-gran { margin-left: 0; }
    .gw-hbar-row { grid-template-columns: 70px 1fr auto; }
  }

  .gw-tiers { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; font-size: 13px; }
  .gw-tier { display: flex; align-items: center; gap: 8px; }

  .gw-row { font-size: 13px; padding: 4px 0; }
  .gw-muted { color: var(--pw-muted, #877f74); }
  .gw-err { color: #c0392b; font-size: 12px; }
  .gw-warn { color: #c08a00; font-size: 11px; }

  .gw-doc-banner { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 6px 0 12px; border-bottom: 1px solid var(--pw-border, #e5ddcf); margin-bottom: 14px; font-size: 12px; }
  .gw-doc-link { color: var(--pw-accent); text-decoration: none; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .gw-doc-link:hover { text-decoration: underline; }
  .gw-doc-p { margin: 0 0 10px; line-height: 1.55; color: var(--pw-ink, #2c2a26); font-size: 13px; }
  .gw-kv { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .gw-ul { margin: 0 0 10px; padding-left: 18px; line-height: 1.7; font-size: 13px; }
  .gw-ul li { margin-bottom: 2px; }
  .gw-link { background: none; border: none; padding: 0; cursor: pointer; color: var(--pw-accent); text-decoration: underline; font-family: inherit; font-size: inherit; }
  .gw-codeblock { position: relative; margin: 8px 0 4px; }
  .gw-pre { background: #1a1614; color: #e8e3d6; padding: 14px 16px; margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.55; overflow-x: auto; white-space: pre; border-radius: var(--pw-radius-sm); }
  .gw-copybtn { position: absolute; top: 8px; right: 8px; background: rgba(255,255,255,0.08); color: #e8e3d6; border: 1px solid rgba(255,255,255,0.18); padding: 3px 10px; font-size: 11px; cursor: pointer; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; border-radius: var(--pw-radius-sm); }
  .gw-copybtn:hover { border-color: var(--pw-accent); color: #fff; }

  @media (max-width: 760px) {
    .gw-layout { grid-template-columns: 1fr; }
    .gw-rail { border-right: none; border-bottom: 1px solid var(--pw-border, #e5ddcf);
      position: static; height: auto; overflow-y: visible; }
    .gw-main { padding: 20px; }
    .gw-status-grid, .gw-mint-grid { grid-template-columns: 1fr; }
  }
  /* ── Chat sandbox ── */
  .gw-card { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5ddcf); border-radius: 10px; padding: 18px; }
  .gw-sb-keyrow { display: flex; gap: 10px; align-items: center; margin: 12px 0 8px; }
  .gw-sb-key { flex: 1; font-family: var(--pw-font-mono, monospace); font-size: 12px; }
  .gw-sb-chk { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--pw-ink-soft, #6b6557); white-space: nowrap; }
  .gw-sb-msg { width: 100%; resize: vertical; font-size: 13px; }
  .gw-sb-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
  .gw-sb-meta { font-size: 11px; font-family: var(--pw-font-mono, monospace); }
  .gw-sb-examples { display: flex; gap: 6px; margin-left: auto; flex-wrap: wrap; }
  .gw-sb-err { margin-top: 12px; color: #c0392b; font-size: 12px; font-family: var(--pw-font-mono, monospace); }
  .gw-sb-out { margin-top: 14px; background: var(--pw-bg-alt, #f6f1e7); border: 1px solid var(--pw-border, #e5ddcf); border-radius: 8px; padding: 14px 16px; font-size: 13.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; color: var(--pw-ink, #2c2a26); }
  .gw-sb-cursor { animation: pulse 1s ease-in-out infinite; }

  /* ── chat sandbox (chatbot transcript) ── */
  .gw-chat { border: 1px solid var(--pw-border, #e5ddcf); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; max-width: 760px; background: #fff; }
  .gw-chat-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-bg-alt, #f6f2ea); flex-wrap: wrap; }
  .gw-chat-key { flex: 1 1 220px; min-width: 160px; }
  .gw-chat-scenarios { display: flex; gap: 6px; flex-wrap: wrap; }

  .gw-chat-body { padding: 16px; min-height: 320px; max-height: 460px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
  .gw-chat-empty { color: var(--pw-muted, #8a8276); font-size: 13px; line-height: 1.6; max-width: 520px; margin: auto; text-align: center; }

  .gw-msg { display: flex; gap: 8px; }
  .gw-msg-user { justify-content: flex-end; }
  .gw-msg-bot { justify-content: flex-start; }
  .gw-bubble { padding: 9px 13px; border-radius: 14px; font-size: 13.5px; line-height: 1.5; max-width: 80%; white-space: pre-wrap; word-break: break-word; }
  .gw-bubble-user { background: var(--pw-accent, #c96342); color: #fff; border-bottom-right-radius: 4px; }
  .gw-bubble-bot { background: var(--pw-bg-alt, #f5efe6); color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-border, #e5ddcf); border-bottom-left-radius: 4px; }
  .gw-bot-avatar { font-size: 18px; flex-shrink: 0; line-height: 1.6; }
  .gw-bot-col { display: flex; flex-direction: column; gap: 5px; max-width: calc(80% + 26px); }

  .gw-msg-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 11px; padding-left: 2px; }
  .gw-mask-badge { background: #eef6ef; color: #2d8a4e; border: 1px solid #cfe6d4; padding: 1px 7px; border-radius: 10px; font-weight: 600; }
  .gw-json-toggle { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 11px; padding: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .gw-json-toggle:hover { text-decoration: underline; }
  .gw-json { margin: 4px 0 0; background: #1a1614; color: #e8e3d6; padding: 12px 14px; border-radius: 8px; font-size: 11px; line-height: 1.5; overflow-x: auto; max-height: 280px; overflow-y: auto; white-space: pre; }

  .gw-chat-composer { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--pw-border, #e5ddcf); background: var(--pw-bg-alt, #f6f2ea); }
  .gw-chat-input { flex: 1; resize: none; background: #fff; border: 1px solid var(--pw-border-strong, #cdc6b8); border-radius: 20px; padding: 9px 14px; font-size: 13px; font-family: inherit; color: var(--pw-ink, #2c2a26); outline: none; max-height: 120px; line-height: 1.4; }
  .gw-chat-input:focus { border-color: var(--pw-accent); }
  .gw-chat-send { flex-shrink: 0; width: 38px; height: 38px; border-radius: 50%; border: none; background: var(--pw-accent, #c96342); color: #fff; font-size: 18px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
  .gw-chat-send:disabled { opacity: 0.5; cursor: default; }
  .gw-chat-reset { flex-shrink: 0; }

  /* ── sandbox + inspector split ── */
  .gw-sandbox { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
  .gw-sandbox .gw-chat { max-width: none; }
  .gw-inspector { border: 1px solid var(--pw-border, #e5ddcf); border-radius: 10px; background: var(--pw-surface, #fff); padding: 12px 14px; font-size: 12px; position: sticky; top: 16px; max-height: calc(100vh - 90px); overflow-y: auto; }
  .gw-insp-head { font-weight: 700; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-accent); margin-bottom: 10px; }
  .gw-insp-empty { color: var(--pw-muted, #877f74); font-size: 12px; line-height: 1.6; }
  .gw-insp-sec { padding: 10px 0; border-top: 1px solid var(--pw-border, #ece6d9); }
  .gw-insp-sec:first-of-type { border-top: none; padding-top: 0; }
  .gw-insp-label { font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); font-weight: 700; margin-bottom: 6px; }
  .gw-insp-kv { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 2px 0; font-size: 11.5px; }
  .gw-insp-kv > span:first-child { color: var(--pw-muted, #877f74); text-transform: uppercase; font-size: 10px; letter-spacing: 0.04em; }
  .gw-insp-curlhead { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 8px 0 4px; }
  .gw-insp-curlhead > span { color: var(--pw-muted, #877f74); text-transform: uppercase; font-size: 10px; letter-spacing: 0.04em; }
  .gw-insp-curl { background: #1a1614; color: #e8e3d6; padding: 10px 12px; border-radius: 8px; font-size: 10.5px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 0; }
  .gw-insp-json { max-height: 240px; }
  .gw-insp-note { font-size: 10.5px; color: var(--pw-muted, #877f74); line-height: 1.5; margin-top: 6px; }

  /* ── service-key cards ── */
  .gw-keylist { display: flex; flex-direction: column; gap: 8px; }
  .gw-keycard { border: 1px solid var(--pw-border, #e5ddcf); border-radius: 8px; padding: 11px 13px; background: var(--pw-surface, #fff); }
  .gw-keycard-off { opacity: 0.6; }
  .gw-keycard-top { display: flex; align-items: center; gap: 8px; }
  .gw-keyname { font-weight: 600; }
  .gw-keycard-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
  .gw-scope-pill { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; padding: 1px 8px; border-radius: 10px; font-weight: 600; border: 1px solid; }
  .gw-tier-1 { background: #eef6ef; color: #2d8a4e; border-color: #cfe6d4; }
  .gw-tier-3 { background: var(--pw-bg-alt, #f3ece1); color: #877f74; border-color: #e5ddcf; }
  .gw-tier-g { background: #fdf0e9; color: #9a4a2f; border-color: #f0d8cb; }
  .gw-keycard-tier { font-size: 12px; color: var(--pw-ink-soft, #4a4438); margin: 6px 0 4px; }
  .gw-keycard-meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; font-size: 11.5px; }
  .gw-keycard-usage { color: var(--pw-muted, #877f74); font-size: 11px; }
  .gw-keycard-chips { margin-top: 8px; }

  /* ── outlets freshness ── */
  .gw-fresh { border: 1px solid var(--pw-border, #e5ddcf); border-left: 3px solid #2d8a4e; border-radius: 8px; background: var(--pw-surface, #fff); padding: 12px 16px; margin-bottom: 16px; }
  .gw-fresh-main { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
  .gw-fresh-live { font-weight: 700; color: #2d8a4e; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; }
  .gw-fresh-refresh { margin-left: auto; }
  .gw-fresh-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 6px 20px; margin-top: 10px; font-size: 12.5px; }
  .gw-fresh-stats > div { display: flex; align-items: center; gap: 8px; }

  .gw-outlet { display: flex; align-items: center; gap: 6px; }
  .gw-outlet-role { font-size: 11px; color: var(--pw-muted, #877f74); flex-shrink: 0; }
  .gw-outlet-bound { border-color: #cfe6d4; background: #f4faf5; }
  .gw-outlet-bound .gw-outlet-role { color: #2d8a4e; }

  @media (max-width: 900px) {
    .gw-sandbox { grid-template-columns: 1fr; }
    .gw-inspector { position: static; max-height: none; }
  }

  /* ── all-in-one console: keys left · chatbot right · store bottom ── */
  .gw-console { display: flex; flex-direction: column; gap: 16px; }
  .gw-console-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); gap: 16px; align-items: start; }
  .gw-console-cell { min-width: 0; }
  .gw-console-cell .gw-panel, .gw-console-cell .gw-fresh { margin-bottom: 0; }
  .gw-console-chat .gw-chat { max-width: none; }
  .gw-console-store { min-width: 0; }
  .gw-console-store .gw-fresh { margin-bottom: 12px; }
  @media (max-width: 1000px) {
    .gw-console-row { grid-template-columns: 1fr; }
  }

  /* inline inspect (folds into each bot reply) */
  .gw-inspect { margin-top: 6px; border: 1px solid var(--pw-border, #e5ddcf); border-radius: 8px; padding: 10px 12px; background: var(--pw-bg-alt, #f8f4ec); display: flex; flex-direction: column; gap: 6px; }
  .gw-inspect-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 11px; }
  .gw-inspect-k { color: var(--pw-muted, #877f74); text-transform: uppercase; font-size: 10px; letter-spacing: 0.04em; min-width: 56px; }
  .gw-inspect-head { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
  .gw-inspect .gw-insp-curl { background: #1a1614; color: #e8e3d6; padding: 9px 11px; border-radius: 6px; font-size: 10.5px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 0; }
  .gw-inspect .gw-json { margin: 0; max-height: 220px; }
  .gw-insp-note { font-size: 10px; color: var(--pw-muted, #877f74); line-height: 1.45; }

  /* ---- PROVISION (Outlets) ---- */
  .gw-prov-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  /* Outlet Keys top banner — 3-tier legend (folded Access) + rate cap (folded Rate limit) */
  .gw-prov-banner { display: flex; gap: 14px; align-items: center; justify-content: space-between; flex-wrap: wrap; padding: 9px 12px; margin-bottom: 12px; background: var(--pw-accent-soft, #f3ece1); border: 1px solid var(--pw-border, #ece2d4); border-radius: 8px; font-size: 11.5px; }
  .gw-prov-legend { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  .gw-prov-leg { display: inline-flex; align-items: center; gap: 4px; }
  .gw-prov-legtitle { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700; color: var(--pw-ink, #2c2620); margin-right: 2px; }
  .gw-prov-rate { display: inline-flex; align-items: center; gap: 6px; }
  .gw-console-globalkey { max-width: 640px; }

  /* bilingual Burmese sub-labels */
  .gw-bur { display: block; font-size: 9.5px; font-weight: 400; letter-spacing: 0; text-transform: none; color: var(--pw-muted, #877f74); line-height: 1.35; margin-top: 1px; }
  .gw-rg-bur { display: block; font-size: 9.5px; font-weight: 400; letter-spacing: 0; color: var(--pw-muted, #9a9285); line-height: 1.3; margin-top: 1px; opacity: 0.85; }
  .gw-pagetitle-bur { display: block; font-size: 15px; font-weight: 500; color: var(--pw-muted, #6b6052); margin-top: 2px; }
  .gw-pagesub-bur { display: block; font-size: 12px; color: var(--pw-muted, #877f74); margin-top: 1px; }
  .gw-prov-summary { display: flex; gap: 16px; align-items: center; font-size: 13px; }
  .gw-prov-stat { color: var(--pw-muted, #877f74); }
  .gw-prov-stat b { color: var(--pw-ink, #2c2620); font-size: 15px; }
  .gw-prov-ok b { color: #2d8a4e; }
  .gw-prov-miss b { color: var(--pw-accent, #c96342); }
  .gw-prov-actions { display: flex; gap: 8px; align-items: center; }
  .gw-prov-tools { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .gw-prov-export { display: flex; gap: 6px; margin-left: auto; }

  .gw-prov-table { border: 1px solid var(--pw-border, #ece2d4); border-radius: 8px; overflow: hidden; }
  .gw-prov-head, .gw-prov-row { display: grid; grid-template-columns: 2.2fr 1.8fr 1fr 0.6fr 0.5fr 0.9fr 0.8fr 0.9fr; align-items: center; gap: 8px; }
  .gw-prov-head { background: var(--pw-accent-soft, #f3ece1); padding: 8px 14px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted, #877f74); font-weight: 600; }
  .gw-prov-row { padding: 7px 14px; border-top: 1px solid var(--pw-border, #ece2d4); font-size: 12.5px; }
  .gw-prov-row:hover { background: var(--pw-surface, #faf6f0); }
  .gw-prov-open { background: var(--pw-surface, #faf6f0); }
  .gw-prov-cell { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .gw-prov-code { background: none; border: none; cursor: pointer; padding: 0; text-align: left; font: inherit; color: inherit; }
  .gw-prov-caret { color: var(--pw-accent, #c96342); font-size: 10px; width: 10px; }
  .gw-prov-keytxt { font-size: 11px; color: var(--pw-muted, #877f74); }
  .gw-prov-warn { color: var(--pw-accent, #c96342); font-size: 11.5px; }
  .gw-prov-errs { color: #c04040; font-weight: 600; font-size: 12px; }

  .gw-prov-detail { padding: 12px 16px 16px 30px; background: var(--pw-surface, #faf6f0); border-top: 1px dashed var(--pw-border-strong, #cdc6b8); display: flex; flex-direction: column; gap: 8px; }
  .gw-prov-drow { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
  .gw-prov-dk { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted, #877f74); width: 64px; font-weight: 600; }
  .gw-prov-snip { margin-top: 4px; }
  .gw-prov-langtabs { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; }
  .gw-prov-snipcopy { margin-left: auto; }
  .gw-prov-pre { margin: 0; max-height: 220px; }
  .gw-prov-test { margin-top: 6px; border-top: 1px solid var(--pw-border, #ece2d4); padding-top: 10px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

  /* live agent activity strip ("what the agent is doing") */
  .gw-steps { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 0 0 5px; font-size: 10.5px; }
  .gw-steps-cap { text-transform: uppercase; letter-spacing: 0.05em; font-size: 9px; color: var(--pw-muted, #9a948a); margin-right: 2px; }
  .gw-step { display: inline-flex; align-items: center; gap: 4px; background: var(--pw-bg-alt, #f3ece1); border: 1px solid var(--pw-border, #e5ddcf); color: var(--pw-text, #4b443c); border-radius: 11px; padding: 2px 8px; line-height: 1.5; white-space: nowrap; animation: gw-step-in 0.2s ease; }
  .gw-step-ic { font-size: 10px; }
  .gw-step-pending { background: #fff6ec; border-color: #e7c9a3; color: #9a4a2f; }
  .gw-steps-live .gw-step-pending .gw-sb-cursor { animation: gw-pulse 1s ease-in-out infinite; }
  @keyframes gw-step-in { from { opacity: 0; transform: translateY(-2px); } to { opacity: 1; transform: none; } }
  @keyframes gw-pulse { 0%,100% { opacity: 0.35; } 50% { opacity: 1; } }

  /* ChatGPT-style reasoning trace: step rail + shimmer + worked-fold */
  .gw-step-rail { display: flex; flex-direction: column; gap: 3px; margin: 1px 0 4px; }
  .gw-rail-row { font-size: 11px; line-height: 1.45; color: var(--pw-muted, #877f74); display: flex; align-items: center; gap: 5px; animation: gw-step-in 0.2s ease; }
  .gw-rail-row .gw-step-ic { font-size: 10px; opacity: 0.7; }
  .gw-rail-done .gw-rail-tick { color: #5b8c63; font-weight: 700; }   /* greenish-muted ✓ */
  .gw-rail-done .gw-rail-dot { color: var(--pw-muted, #877f74); }      /* plain muted ○ */
  .gw-rail-active { color: var(--pw-muted, #877f74); }
  .gw-rail-active .gw-sb-cursor { color: #9a4a2f; animation: gw-pulse 1s ease-in-out infinite; }
  .gw-step-phase { font-weight: 600; color: #4b443c; }

  /* shimmering active phase title (warm, subtle) */
  .gw-shimmer {
    background: linear-gradient(90deg, currentColor 0%, currentColor 35%, #fff8 50%, currentColor 65%, currentColor 100%);
    background-size: 200% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    color: var(--pw-muted, #877f74);
    -webkit-text-fill-color: transparent;
    animation: gw-shimmer 1.4s linear infinite;
  }
  @keyframes gw-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

  /* 3-dot wave fallback */
  .gw-wave { display: inline-flex; gap: 1px; }
  .gw-wave-dot { color: var(--pw-muted, #877f74); animation: gw-wave 0.9s ease-in-out infinite; }
  .gw-wave-dot:nth-child(1) { animation-delay: 0s; }
  .gw-wave-dot:nth-child(2) { animation-delay: 0.16s; }
  .gw-wave-dot:nth-child(3) { animation-delay: 0.32s; }
  @keyframes gw-wave { 0%,100% { transform: translateY(0); opacity: 0.4; } 50% { transform: translateY(-2px); opacity: 1; } }

  /* "Worked for Xs · N steps" collapsible summary — matches .gw-json-toggle */
  .gw-worked {
    background: none; border: none; padding: 0; cursor: pointer;
    font-size: 10.5px; color: var(--pw-muted, #877f74);
    display: inline-flex; align-items: center; gap: 4px;
  }
  .gw-worked:hover { color: #4b443c; text-decoration: underline; }

  /* per-outlet chat drawer (slide-over) */
  .gw-drawer-back { position: fixed; inset: 0; background: rgba(30,25,20,0.32); z-index: 60; animation: gw-fade 0.15s ease; }
  .gw-drawer { position: fixed; top: 0; right: 0; height: 100vh; width: 440px; max-width: 92vw; background: var(--pw-surface, #faf6f0); border-left: 1px solid var(--pw-border, #ece2d4); box-shadow: -10px 0 40px rgba(0,0,0,0.18); z-index: 61; display: flex; flex-direction: column; animation: gw-slide 0.18s ease; }
  @keyframes gw-fade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes gw-slide { from { transform: translateX(30px); opacity: 0.4; } to { transform: translateX(0); opacity: 1; } }
  .gw-drawer-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; background: #1d2540; color: #fff; }
  .gw-drawer-id { display: flex; align-items: center; gap: 10px; }
  .gw-drawer-av { font-size: 22px; }
  .gw-drawer-title { font-size: 14px; font-weight: 600; }
  .gw-drawer-title .gw-code { background: rgba(255,255,255,0.16); color: #fff; }
  .gw-drawer-sub { font-size: 11px; opacity: 0.85; display: flex; align-items: center; gap: 6px; margin-top: 2px; }
  .gw-drawer-sub .gw-mask-badge { font-size: 9.5px; }
  .gw-drawer-x { background: rgba(255,255,255,0.12); border: none; color: #fff; width: 26px; height: 26px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .gw-drawer-x:hover { background: rgba(255,255,255,0.25); }
  .gw-drawer-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 12px; border-bottom: 1px solid var(--pw-border, #ece2d4); background: var(--pw-bg-alt, #f6f2ea); }
  .gw-drawer-reset { margin-left: auto; }
  .gw-drawer-body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
  .gw-drawer-composer { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--pw-border, #ece2d4); background: var(--pw-bg-alt, #f6f2ea); }
  .gw-drawer-composer .gw-chat-input { flex: 1; }
  .gw-drawer-foot { padding: 0 12px 10px; }
  /* hide the global floating robot while the chat drawer is open (it overlaps the composer) */
  :global(body.gw-drawer-open .rp-wrap) { display: none !important; }
  /* markdown bubble */
  .gw-bubble-md :global(p) { margin: 0 0 6px; }
  .gw-bubble-md :global(p:last-child) { margin-bottom: 0; }
  .gw-bubble-md :global(ul) { margin: 4px 0; padding-left: 18px; }
  .gw-bubble-md :global(li) { margin: 2px 0; }
  .gw-bubble-md :global(strong) { font-weight: 700; }
  .gw-bubble-md :global(code) { background: rgba(0,0,0,0.06); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
  .gw-bubble-md :global(table.gw-md-table) { border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 11.5px; }
  .gw-bubble-md :global(.gw-md-table th) { text-align: left; padding: 4px 8px; background: var(--pw-bg-alt, #f3ece1); border: 1px solid var(--pw-border, #e5ddcf); font-weight: 700; }
  .gw-bubble-md :global(.gw-md-table td) { padding: 4px 8px; border: 1px solid var(--pw-border, #e5ddcf); }
  .gw-bubble-md :global(.gw-md-table td:last-child) { text-align: right; font-variant-numeric: tabular-nums; }
  .gw-bubble-md :global(.gw-md-table tbody tr:nth-child(even)) { background: rgba(0,0,0,0.02); }
  .gw-typing { letter-spacing: 2px; opacity: 0.5; animation: gw-typing 1s steps(3) infinite; }
  @keyframes gw-typing { 0% { opacity: 0.25; } 50% { opacity: 0.7; } 100% { opacity: 0.25; } }

  /* per-key drill-down — clickable key chip */
  .gw-code-btn { cursor: pointer; transition: background 0.12s ease, border-color 0.12s ease; }
  .gw-code-btn:hover { background: #fff6ec; border-color: #e7c9a3; color: #9a4a2f; }

  /* per-key drill-down modal */
  .gw-kd-overlay { position: fixed; inset: 0; background: rgba(30,25,20,0.34); z-index: 70; display: flex; align-items: flex-start; justify-content: center; padding: 5vh 16px; overflow-y: auto; animation: gw-fade 0.15s ease; }
  .gw-kd-card { width: 720px; max-width: 96vw; background: var(--pw-surface, #faf6f0); border: 1px solid var(--pw-border, #ece2d4); border-radius: 8px; box-shadow: 0 18px 50px rgba(0,0,0,0.22); padding: 16px 18px 20px; animation: gw-slide 0.18s ease; }
  .gw-kd-head { display: flex; align-items: center; gap: 9px; margin-bottom: 14px; }
  .gw-kd-dot { width: 9px; height: 9px; border-radius: 50%; background: #cdc6b8; flex-shrink: 0; }
  .gw-kd-dot-on { background: #3c9a4a; box-shadow: 0 0 0 3px rgba(60,154,74,0.18); }
  .gw-kd-badge { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.05em; background: #f3ece1; color: #9a4a2f; border: 1px solid #e5ddcf; border-radius: 11px; padding: 2px 9px; }
  .gw-kd-badge-g { background: #1d2540; color: #fff; border-color: #1d2540; }
  .gw-kd-x { margin-left: auto; background: var(--pw-bg-alt, #f3ece1); border: 1px solid var(--pw-border, #e5ddcf); color: #4b443c; width: 26px; height: 26px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .gw-kd-x:hover { background: #fff6ec; border-color: #e7c9a3; color: #9a4a2f; }
  .gw-kd-grid { gap: 8px 24px; }
  .gw-kd-sub { font-size: 11px; }
  .gw-kd-notice { font-size: 11.5px; background: #fff6ec; border: 1px solid #e7c9a3; color: #9a4a2f; border-radius: 5px; padding: 6px 10px; }
  .gw-kd-qtable td, .gw-kd-qtable th { font-size: 11.5px; }
  .gw-kd-ts { white-space: nowrap; color: var(--pw-muted, #877f74); font-variant-numeric: tabular-nums; }
  .gw-kd-flags { white-space: nowrap; }
  .gw-kd-ok { color: #3c9a4a; }
  .gw-kd-qrow { cursor: pointer; }
  .gw-kd-qrow:hover { background: #fff6ec; }
  .gw-kd-qrow-on { background: #f3ece1; }
  .gw-kd-qdetail td { background: #f7f2ea; }
  .gw-kd-qfull { font-size: 12px; padding: 4px 2px 8px; }
  .gw-kd-qlabel { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-muted, #877f74); margin-bottom: 3px; }
  .gw-kd-qtext { white-space: pre-wrap; word-break: break-word; color: #4b443c; line-height: 1.5; }
  .gw-kd-qmeta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 11px; }
  @media (max-width: 640px) { .gw-kd-grid { grid-template-columns: 1fr; } }
</style>
