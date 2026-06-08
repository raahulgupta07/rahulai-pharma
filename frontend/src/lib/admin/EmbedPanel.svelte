<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { markdownToHtml } from '$lib/markdown';
  import RequestFlow from '$lib/RequestFlow.svelte';

  let { embedded = false } = $props();

  // ---- auth / super-admin gate ----
  let checking = $state(true);
  let isSuper = $state(false);

  const slug = 'citypharma';

  async function apiFetch(path: string, opts: RequestInit = {}) {
    const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('dash_token') || '') : '';
    return fetch(path, {
      ...opts,
      headers: {
        'Authorization': `Bearer ${token}`,
        'X-Scope-Id': slug,
        'Content-Type': 'application/json',
        ...(opts.headers as Record<string, string> || {}),
      },
    });
  }

  // ---- left-rail view (persisted in URL hash so refresh stays put) ----
  const _validViews = ['overview', 'widgets', 'playground', 'usage', 'monitoring', 'developer'];
  function _viewFromHash(): string {
    if (typeof window === 'undefined') return 'overview';
    const h = (window.location.hash || '').replace(/^#/, '');
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
    { group: 'EMBED', items: [{ id: 'overview', label: 'Overview', icon: 'gauge' }] },
    { group: 'MANAGE', items: [
      { id: 'widgets', label: 'Widgets', icon: 'grid' },
      { id: 'playground', label: 'Playground', icon: 'rocket' },
    ] },
    { group: 'ANALYTICS', items: [
      { id: 'monitoring', label: 'Monitoring', icon: 'activity' },
      { id: 'usage', label: 'Usage', icon: 'chart' },
    ] },
    { group: 'DEVELOPER', items: [
      { id: 'developer', label: 'Snippet & Docs', icon: 'code' },
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
    overview: { title: 'Overview', sub: 'Embed status, endpoints + quick test' },
    widgets: { title: 'Widgets', sub: 'List, create + revoke embed widgets' },
    playground: { title: 'Playground', sub: 'Appearance · live chat test · shareable test link' },
    monitoring: { title: 'Monitoring', sub: 'Live widget traffic, latency, errors + per-store breakdown' },
    usage: { title: 'Usage Analytics', sub: 'Calls, tokens + sessions over time' },
    developer: { title: 'Developer Docs', sub: 'Snippet, code examples + 3-tier access reference' },
  };

  // ---- embeds list ----
  let embeds = $state<any[]>([]);
  let embedsErr = $state('');
  let embedsLoading = $state(false);

  async function loadEmbeds() {
    embedsLoading = true; embedsErr = '';
    try {
      const r = await apiFetch(`/api/projects/${slug}/embeds`);
      if (r.ok) { const d = await r.json(); embeds = d.embeds || d || []; }
      else embedsErr = `embeds ${r.status}`;
    } catch (e) { embedsErr = 'unreachable'; }
    embedsLoading = false;
  }

  // ---- outlets for store picker ----
  let outlets = $state<string[]>([]);
  let outletFilter = $state('');
  const outletChoices = $derived.by(() => {
    const f = outletFilter.trim().toLowerCase();
    return outlets
      .filter((o) => !newEmbedOutlets.includes(o))
      .filter((o) => !f || o.toLowerCase().includes(f))
      .slice(0, 50);
  });

  async function loadOutlets() {
    try {
      const r = await apiFetch('/api/auth/apigw-outlets');
      if (r.ok) { const d = await r.json(); outlets = d.outlets || []; }
    } catch (e) { /* fail-soft */ }
  }

  // ---- new embed form ----
  let newEmbedOpen = $state(false);
  let newEmbedName = $state('');
  let newEmbedScope = $state<'store' | 'global'>('store');
  let newEmbedOutlets = $state<string[]>([]);
  let newEmbedBusy = $state(false);
  let newEmbedErr = $state('');
  let newEmbedKey = $state('');

  function addOutlet(o: string) {
    const v = (o || '').trim();
    if (v && !newEmbedOutlets.includes(v)) newEmbedOutlets = [...newEmbedOutlets, v];
    outletFilter = '';
  }
  function removeOutlet(o: string) { newEmbedOutlets = newEmbedOutlets.filter((x) => x !== o); }
  function addTypedOutlet() { if (outletFilter.trim()) addOutlet(outletFilter.trim()); }
  function resetNewEmbed() { newEmbedName = ''; newEmbedScope = 'store'; newEmbedOutlets = []; outletFilter = ''; newEmbedErr = ''; }

  async function createEmbed() {
    if (!newEmbedName.trim()) { newEmbedErr = 'name required'; return; }
    if (newEmbedScope === 'store' && newEmbedOutlets.length === 0) { newEmbedErr = 'add at least one outlet for store scope'; return; }
    newEmbedBusy = true; newEmbedErr = ''; newEmbedKey = '';
    try {
      const r = await apiFetch(`/api/projects/${slug}/embeds`, {
        method: 'POST',
        body: JSON.stringify({
          name: newEmbedName.trim(),
          scope_mode: newEmbedScope,
          store_ids: newEmbedScope === 'store' ? newEmbedOutlets : [],
          auth_mode: 'public',
        }),
      });
      if (r.ok) {
        const d = await r.json();
        newEmbedKey = d.public_key || d.embed_id || d.key || '';
        resetNewEmbed();
        await loadEmbeds();
      } else {
        let detail = ''; try { const e = await r.json(); detail = e.detail || ''; } catch {}
        newEmbedErr = detail || `create failed (${r.status})`;
      }
    } catch (e) { newEmbedErr = 'unreachable'; }
    newEmbedBusy = false;
  }

  async function revokeEmbed(embedId: string) {
    if (!confirm('Revoke this widget? Its snippet stops working immediately.')) return;
    try {
      await apiFetch(`/api/projects/${slug}/embeds/${embedId}`, { method: 'DELETE' });
    } catch (e) { /* fail-soft */ }
    await loadEmbeds();
  }

  let rotatingKey = $state<string | null>(null);
  async function rotateEmbedKey(embedId: string) {
    if (!confirm('Rotate the public key? The OLD key stops working now — the store must update its snippet with the new key.')) return;
    rotatingKey = embedId;
    try {
      const r = await apiFetch(`/api/projects/${slug}/embeds/${embedId}/rotate-key`, { method: 'POST' });
      if (r.ok) {
        const d = await r.json();
        // patch the in-memory row so the expansion shows the new key without a full reload
        embeds = embeds.map((x) => ((x.embed_id || x.id) === embedId ? { ...x, public_key: d.public_key } : x));
      }
    } catch (e) { /* fail-soft */ }
    rotatingKey = null;
  }

  // ---- config (first / selected embed) ----
  let configEmbed = $state<any>(null);
  let configColor = $state('#c96342');
  let configPosition = $state('bottom-right');
  let configTheme = $state('default');
  let configWelcome = $state('');
  let configLogo = $state('');
  let configSaved = $state(false);
  let configBusy = $state(false);
  let configErr = $state('');

  function loadConfig() {
    configEmbed = embeds[0] || null;
    if (configEmbed) {
      configColor = configEmbed.primary_color || '#c96342';
      configPosition = configEmbed.position || 'bottom-right';
      configTheme = configEmbed.theme || 'default';
      configWelcome = configEmbed.welcome_msg || '';
      configLogo = configEmbed.logo_url || '';
    }
  }

  // ---- logo upload ----
  let logoUploading = $state(false);
  let logoErr = $state('');
  async function uploadLogo(e: Event) {
    const input = e.target as HTMLInputElement;
    const f = input.files?.[0];
    if (!f || !configEmbed) return;
    logoUploading = true; logoErr = '';
    try {
      const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('dash_token') || '') : '';
      const fd = new FormData();
      fd.append('file', f);
      const r = await fetch(`/api/projects/${slug}/embeds/${configEmbed.embed_id || configEmbed.id}/logo`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'X-Scope-Id': slug },  // NO Content-Type — browser sets multipart boundary
        body: fd,
      });
      if (r.ok) { const d = await r.json(); configLogo = d.logo_url; await loadEmbeds(); }
      else { let d = ''; try { const e2 = await r.json(); d = e2.detail || ''; } catch {} logoErr = d || `upload ${r.status}`; }
    } catch { logoErr = 'unreachable'; }
    logoUploading = false;
    input.value = '';
  }

  async function saveConfig() {
    if (!configEmbed) return;
    configBusy = true; configErr = '';
    try {
      const r = await apiFetch(`/api/projects/${slug}/embeds/${configEmbed.embed_id || configEmbed.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          primary_color: configColor,
          position: configPosition,
          theme: configTheme,
          welcome_msg: configWelcome,
          logo_url: configLogo,
        }),
      });
      if (r.ok) { configSaved = true; setTimeout(() => configSaved = false, 1800); await loadEmbeds(); }
      else { let d = ''; try { const e = await r.json(); d = e.detail || ''; } catch {} configErr = d || `save ${r.status}`; }
    } catch (e) { configErr = 'unreachable'; }
    configBusy = false;
  }

  // ---- usage ----
  let usageDays = $state(7);
  let usageEmbed = $state<any>(null);
  let usageData = $state<any>(null);
  let usageErr = $state('');

  async function loadUsage() {
    usageErr = '';
    const eid = usageEmbed?.embed_id || usageEmbed?.id || (embeds[0]?.embed_id || embeds[0]?.id);
    if (!eid) return;
    try {
      const r = await apiFetch(`/api/projects/${slug}/embeds/${eid}/usage?days=${usageDays}`);
      if (r.ok) usageData = await r.json();
      else usageErr = `usage ${r.status}`;
    } catch (e) { usageErr = 'unreachable'; }
  }
  function setUsageDays(n: number) { usageDays = n; loadUsage(); }

  // ---- monitoring (rich embed usage dashboard) ----
  let monDays = $state(7);
  let monBucket = $state<'hour' | 'day'>('day');
  let monEmbed = $state('');            // '' = all widgets, else embed_id
  let monData = $state<any>(null);
  let monErr = $state('');
  let monBusy = $state(false);

  async function loadMonitoring() {
    monErr = ''; monBusy = true;
    try {
      const q = `days=${monDays}&bucket=${monBucket}${monEmbed ? '&embed_id=' + encodeURIComponent(monEmbed) : ''}`;
      const r = await apiFetch(`/api/admin/usage/embed-overview?${q}`);
      if (r.ok) monData = await r.json();
      else { monData = null; monErr = `monitoring ${r.status}`; }
    } catch (e) { monData = null; monErr = 'unreachable'; }
    monBusy = false;
  }
  function setMonDays(n: number) { monDays = n; loadMonitoring(); }
  function setMonBucket(b: 'hour' | 'day') { monBucket = b; loadMonitoring(); }
  function onMonEmbed() { loadMonitoring(); }

  // activity bars derived from series (height = requests / max)
  let monActBars = $derived.by(() => {
    const s: any[] = monData?.series || [];
    const max = Math.max(1, ...s.map((x: any) => Number(x.requests) || 0));
    return s.map((x: any) => {
      const t = String(x.t || '');
      // label: short date or hour
      let label = t;
      try {
        const d = new Date(t);
        if (!isNaN(d.getTime())) {
          label = monBucket === 'hour'
            ? `${String(d.getMonth() + 1)}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}h`
            : `${String(d.getMonth() + 1)}/${d.getDate()}`;
        }
      } catch { /* keep raw */ }
      return {
        label,
        requests: Number(x.requests) || 0,
        errors: Number(x.errors) || 0,
        p95: Number(x.p95_ms) || 0,
        pct: Math.round(((Number(x.requests) || 0) / max) * 100),
        title: `${t} · ${x.requests} req · ${x.errors} err · p95 ${x.p95_ms}ms`,
      };
    });
  });

  function monExportJson() {
    if (!monData) return;
    const blob = new Blob([JSON.stringify(monData, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `embed-monitoring-${monDays}d.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  function monExportCsv() {
    const rows: any[] = monData?.by_embed || [];
    const head = ['embed_id', 'name', 'store', 'requests', 'errors', 'p95_ms', 'last'];
    const esc = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [head.join(',')].concat(
      rows.map((r: any) => head.map((h) => esc(r[h])).join(',')),
    );
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `embed-by-widget-${monDays}d.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  function monIsTail(b: string) { return b === '30-60s' || b === '>60s'; }

  // ---- widget detail (full-screen drill-down, mirrors gateway outlet detail) ----
  let wdId = $state<string | null>(null);     // embed_id whose detail screen is open (null = list)
  let wdRange = $state<'24h' | '7d' | '30d'>('7d');
  let wdData = $state<any>(null);
  let wdErr = $state('');
  let wdBusy = $state(false);
  let wdCallOpen = $state<number | null>(null);  // expanded call id

  async function loadWidgetDetail() {
    if (!wdId) return;
    wdErr = ''; wdBusy = true;
    try {
      const r = await apiFetch(`/api/admin/usage/embed-detail?embed_id=${encodeURIComponent(wdId)}&range=${wdRange}`);
      if (r.ok) wdData = await r.json();
      else { wdData = null; wdErr = `detail ${r.status}`; }
    } catch (e) { wdData = null; wdErr = 'unreachable'; }
    wdBusy = false;
  }
  function openWidget(embed_id: string) {
    wdId = embed_id; wdData = null; wdErr = ''; wdCallOpen = null;
    loadWidgetDetail();
  }
  function backToMonitoring() { wdId = null; wdData = null; wdCallOpen = null; }
  function setWdRange(r: '24h' | '7d' | '30d') { wdRange = r; loadWidgetDetail(); }
  function toggleWdCall(id: number) { wdCallOpen = wdCallOpen === id ? null : id; }
  function fmtS(ms: any) { const n = Number(ms); return n ? `${(n / 1000).toFixed(1)}s` : '—'; }

  // activity bars for the detail screen
  let wdActBars = $derived.by(() => {
    const s: any[] = wdData?.series || [];
    const max = Math.max(1, ...s.map((x: any) => Number(x.requests) || 0));
    const bkt = wdData?.bucket || 'day';
    return s.map((x: any) => {
      const t = String(x.t || '');
      let label = t;
      try {
        const d = new Date(t);
        if (!isNaN(d.getTime())) {
          label = bkt === 'hour'
            ? `${String(d.getMonth() + 1)}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}h`
            : `${String(d.getMonth() + 1)}/${d.getDate()}`;
        }
      } catch { /* keep raw */ }
      return { label, pct: Math.round(((Number(x.requests) || 0) / max) * 100),
               title: `${t} · ${x.requests} req · ${x.errors} err · p95 ${x.p95_ms}ms` };
    });
  });

  function wdExportJson() {
    if (!wdData) return;
    const blob = new Blob([JSON.stringify(wdData, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `embed-detail-${wdId}-${wdRange}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ---- copy ----
  let copied = $state('');
  function flashCopied(which: string) { copied = which; setTimeout(() => { if (copied === which) copied = ''; }, 1500); }
  function copyText(txt: string, which: string) {
    if (navigator.clipboard) navigator.clipboard.writeText(txt).then(() => flashCopied(which)).catch(() => {});
  }

  // ---- downloadable SDK (server-side example files, templated with real keys) ----
  type SdkFile = { name: string; lang: string; desc: string; size: number };
  let sdkFiles = $state<SdkFile[]>([]);
  let sdkLoaded = $state(false);
  let sdkPreviewName = $state('');
  let sdkPreviewBody = $state('');
  let sdkPreviewBusy = $state(false);

  function sdkQuery(): string {
    // template placeholders in the example files with this embed's real values
    const p = new URLSearchParams({ base: baseUrl, embed: eid, pubkey: pkey });
    return p.toString();
  }
  async function loadSdk() {
    if (sdkLoaded) return;
    try {
      const r = await fetch(`${baseUrl}/api/embed/sdk`);
      if (r.ok) { sdkFiles = (await r.json()).files || []; }
    } catch { /* ignore */ }
    sdkLoaded = true;
  }
  async function previewSdk(name: string) {
    if (sdkPreviewName === name) { sdkPreviewName = ''; sdkPreviewBody = ''; return; }
    sdkPreviewName = name; sdkPreviewBody = ''; sdkPreviewBusy = true;
    try {
      const r = await fetch(`${baseUrl}/api/embed/sdk/file/${encodeURIComponent(name)}?${sdkQuery()}`);
      sdkPreviewBody = r.ok ? await r.text() : `// failed to load ${name}`;
    } catch { sdkPreviewBody = `// failed to load ${name}`; }
    sdkPreviewBusy = false;
  }
  function downloadSdk(name: string) {
    const a = document.createElement('a');
    a.href = `${baseUrl}/api/embed/sdk/file/${encodeURIComponent(name)}?${sdkQuery()}&download=1`;
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  function downloadSdkZip() {
    const a = document.createElement('a');
    a.href = `${baseUrl}/api/embed/sdk.zip?${sdkQuery()}`;
    a.download = 'cityagent-sdk.zip'; document.body.appendChild(a); a.click(); a.remove();
  }

  // ---- expandable widget rows (mirror Gateway Outlet Keys) ----
  let expandedEmbed = $state<string | null>(null);
  function toggleEmbedRow(id: string) { expandedEmbed = expandedEmbed === id ? null : id; }
  function embedScopeLabel(e: any): string {
    const intent = (e.bound_intent || 'public');
    const qty = intent === 'private' ? 'qty + cost'
              : intent === 'network' ? 'availability + cost'
              : 'availability only';
    if (e.bound_scope_id) return `store ${e.bound_scope_id} · ${qty}`;
    return `global · full`;
  }

  // ---- snippet helpers ----
  let baseUrl = $state('');
  function buildSnippet(embedId: string, apiKey: string, storeId?: string): string {
    const attrs = storeId ? ` data-user='${JSON.stringify({ store_id: storeId })}'` : '';
    return `<script
  src="${baseUrl}/embed/widget.js"
  data-embed-id="${embedId}"
  data-key="${apiKey}"${attrs}
><\/script>`;
  }

  const publicSnippet = $derived(
    `<!-- Public embed (no auth, catalog-only) -->
<script
  src="${baseUrl}/api/embed/widget.js"
  data-embed-id="EMBED_ID"
  data-key="PUBLIC_KEY"
><\/script>`
  );

  const storeScopedSnippet = $derived(
    `<!-- Store-scoped embed (Tier-1 qty+cost for owned stores) -->
<script
  src="${baseUrl}/api/embed/widget.js"
  data-embed-id="EMBED_ID"
  data-key="STORE_KEY"
  data-user='{"store_id":"20063-CCBRBKMY"}'
><\/script>`
  );

  const hmacSnippet = $derived(
    `<!-- HMAC-signed embed (server-side signature prevents key sharing) -->
<?php
$payload = json_encode(["store_id" => "20063-CCBRBKMY", "role" => "staff", "ts" => time()]);
$sig = hash_hmac("sha256", $payload, "YOUR_SECRET_KEY");
?>
<script
  src="${baseUrl}/api/embed/widget.js"
  data-embed-id="EMBED_ID"
  data-key="STORE_KEY"
  data-user='<?= $payload ?>'
  data-sig="<?= $sig ?>"
><\/script>`
  );

  const widgetJsTest = $derived(`curl -I "${baseUrl}/api/embed/widget.js"`);

  // ---- active embed count ----
  const liveCount = $derived(embeds.filter((e) => e.enabled !== false && e.status !== 'revoked' && e.status !== 'disabled').length);

  // ---- primary embed (real keys for snippets + sandbox) ----
  const primaryEmbed = $derived(embeds[0] || null);
  const eid = $derived(primaryEmbed?.embed_id || primaryEmbed?.id || 'EMBED_ID');
  const pkey = $derived(primaryEmbed?.public_key || primaryEmbed?.public_id || 'PUBLIC_KEY');
  const isLive = $derived(!!primaryEmbed && primaryEmbed.enabled !== false && primaryEmbed.status !== 'draft' && primaryEmbed.status !== 'disabled' && primaryEmbed.status !== 'revoked');
  const origins = $derived(Array.isArray(primaryEmbed?.allowed_origins) ? primaryEmbed.allowed_origins : []);

  // ---- real-key snippets (fall back to placeholders when no embed yet) ----
  const dropInSnippet = $derived(
`<!-- Drop-in chat bubble · anonymous -->
<script
  src="${baseUrl}/api/embed/widget.js"
  data-embed-id="${eid}"
  data-public-key="${pkey}"
  data-title="CityAgent Pharma"
  data-greeting="Hi! Ask about stock, substitutes, or indications."
  async><\/script>`);

  const restSnippet = $derived(
`// Custom UI · raw REST (Node, public mode)
const BASE = "${baseUrl}";
const s = await fetch(BASE + "/api/embed/session/create", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Origin": "https://yoursite.com" },
  body: JSON.stringify({ embed_id: "${eid}", public_key: "${pkey}" }),
});
const { session_token } = await s.json();
const c = await fetch(BASE + "/api/embed/chat", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_token, message: "is paracetamol in stock?" }),
});
console.log((await c.json()).content);`);

  const phpHmacSnippet = $derived(
`<?php // User-scoped · server signs the payload (secret_key stays server-side)
$user = ["id" => (string)$me->id, "store_id" => (string)$me->store_code, "role" => "staff"];
ksort($user);
$canonical = json_encode($user, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
$sig = hash_hmac("sha256", $canonical, getenv("CITYAGENT_EMBED_SECRET")); ?>
<script
  src="${baseUrl}/api/embed/widget.js"
  data-embed-id="${eid}"
  data-public-key="${pkey}"
  data-user='<?= htmlspecialchars($canonical, ENT_QUOTES) ?>'
  data-user-sig="<?= $sig ?>"
  async><\/script>`);

  let docPath = $state<'dropin'|'php'|'rest'>('dropin');
  const docSnippet = $derived(docPath === 'php' ? phpHmacSnippet : docPath === 'rest' ? restSnippet : dropInSnippet);

  // ---- Sandbox (in-page live chat) ----
  let sbMsgs = $state<{ role: string; text: string; ms?: number }[]>([]);
  let sbInput = $state('');
  let sbBusy = $state(false);
  let sbSession = $state('');
  let sbErr = $state('');
  let sbBodyEl = $state<HTMLDivElement | undefined>();
  $effect(() => {
    // auto-scroll chat to latest
    void sbMsgs.length; void sbBusy;
    if (sbBodyEl) setTimeout(() => { if (sbBodyEl) sbBodyEl.scrollTop = sbBodyEl.scrollHeight; }, 30);
  });
  let sbStore = $state('');
  let sbRole = $state('staff');
  let sbGlobal = $state(false); // opt-out: test tier-3 catalog (no store)

  async function sbEnsureSession(): Promise<string> {
    if (sbSession) return sbSession;
    const r = await fetch(`${baseUrl}/api/embed/session/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Origin': baseUrl },
      body: JSON.stringify({ embed_id: eid, public_key: pkey }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `session ${r.status}`);
    sbSession = d.session_token;
    return sbSession;
  }
  async function sbSend() {
    const msg = sbInput.trim();
    if (!msg || sbBusy) return;
    sbInput = ''; sbErr = '';
    sbMsgs = [...sbMsgs, { role: 'you', text: msg }];
    sbBusy = true;
    try {
      const t = await sbEnsureSession();
      const t0 = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const r = await fetch(`${baseUrl}/api/embed/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_token: t, message: msg }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `chat ${r.status}`);
      const ms = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0);
      sbMsgs = [...sbMsgs, { role: 'bot', text: d.content || '(empty)', ms }];
    } catch (e: any) { sbErr = String(e?.message || e); }
    sbBusy = false;
  }
  function sbReset() { sbSession = ''; sbMsgs = []; sbErr = ''; }

  // ---- shareable test link (signed token) ----
  let sbLink = $state('');
  let sbLinkExp = $state(0);
  let sbLinkTtl = $state(3600);
  let sbLinkErr = $state('');
  let sbLinkBusy = $state(false);
  async function sbGenLink() {
    sbLinkErr = ''; sbLink = '';
    if (!sbGlobal && !sbStore) {
      sbLinkErr = 'Pick a store — or tick "global view" to test the catalog tier.';
      return;
    }
    sbLinkBusy = true;
    try {
      const claims: any = {};
      if (!sbGlobal && sbStore) claims.store_id = sbStore;
      if (sbRole) claims.role = sbRole;
      const r = await apiFetch(`/api/projects/${slug}/embeds/${eid}/test-token`, {
        method: 'POST',
        body: JSON.stringify({ ttl_seconds: sbLinkTtl, claims: Object.keys(claims).length ? claims : undefined }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { sbLinkErr = d.detail || `token ${r.status} (rotate secret first?)`; }
      else { sbLink = d.url; sbLinkExp = d.expires_at || 0; }
    } catch (e: any) { sbLinkErr = String(e?.message || e); }
    sbLinkBusy = false;
  }
  function fmtExpIn(exp: number): string {
    if (!exp) return '';
    const s = exp - Math.floor(Date.now() / 1000);
    if (s <= 0) return 'expired';
    const m = Math.floor(s / 60), ss = s % 60;
    return `${m}:${String(ss).padStart(2, '0')}`;
  }

  onMount(async () => {
    baseUrl = typeof window !== 'undefined' ? window.location.origin : '';
    // Prefer the server's canonical public origin (PUBLIC_URL env, e.g. the AWS
    // domain) so snippets/SDK show the real public host, not whatever internal
    // host the dashboard happens to be served from.
    try {
      const fr = await fetch('/api/flags');
      if (fr.ok) {
        const flags = await fr.json();
        if (flags.public_base_url) baseUrl = String(flags.public_base_url).replace(/\/+$/, '');
      }
    } catch { /* fall back to window.location.origin */ }
    view = _viewFromHash();
    if (typeof window !== 'undefined') {
      window.addEventListener('hashchange', () => { view = _viewFromHash(); });
    }
    try {
      const r = await apiFetch('/api/auth/check');
      if (r.ok) { const d = await r.json(); isSuper = !!d.is_super; }
    } catch (e) { /* fail */ }
    checking = false;
    if (!isSuper) return;
    await loadEmbeds();
    await loadOutlets();
    loadSdk();
  });

  // reload config when embeds change
  $effect(() => {
    if (embeds.length > 0) loadConfig();
  });
  $effect(() => {
    if (view === 'usage' && embeds.length > 0) { usageEmbed = embeds[0]; loadUsage(); }
  });
  $effect(() => {
    if (view === 'monitoring' && monData === null && !monBusy) loadMonitoring();
  });
</script>

{#if checking}
  <div class="emp-center"><span class="emp-muted">◐ checking access…</span></div>
{:else if !isSuper}
  <div class="emp-center">
    <div class="emp-denied">
      <div class="emp-denied-mark">✗</div>
      <div class="emp-denied-title">super-admin only</div>
      <div class="emp-muted">The Embed console is restricted to platform administrators.</div>
      {#if !embedded}
        <button class="emp-btn" onclick={() => goto('/ui/home')}>← back</button>
      {/if}
    </div>
  </div>
{:else}
  <div class="emp-wrap" class:emp-embedded={embedded}>

    <!-- ===== LEFT RAIL ===== -->
    <nav class="emp-rail">
      {#each RAIL as grp (grp.group)}
        <div class="emp-rg">
          <div class="emp-rg-label">{grp.group}</div>
          {#each grp.items as it (it.id)}
            <button class="emp-rg-item" class:emp-rg-on={view === it.id} onclick={() => nav(it.id)}>
              <svg class="emp-rg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{@html ICONS[it.icon] || ''}</svg><span class="emp-rg-text">{it.label}</span>
            </button>
          {/each}
        </div>
      {/each}
    </nav>

    <!-- ===== RIGHT CONTENT ===== -->
    <main class="emp-main">
      <header class="emp-pagehead">
        <h1 class="emp-pagetitle">{PAGE[view]?.title ?? ''}</h1>
        <p class="emp-pagesub">{PAGE[view]?.sub ?? ''}</p>
      </header>

      <!-- ==================== OVERVIEW ==================== -->
      {#if view === 'overview'}
        <section class="emp-panel">
          <div class="emp-h">HOW IT WORKS</div>
          <p class="emp-doc-p">What happens when a visitor's browser talks to your embedded widget.</p>
          <RequestFlow
            title="Embed · question → answer"
            live={liveCount > 0}
            badge={liveCount > 0 ? `${liveCount} live · ${origins.length} origin${origins.length === 1 ? '' : 's'}` : 'no widget live'}
            inputBubble={{ who: 'staff @ 20063-CCBRBKMY', text: 'is paracetamol in stock at my branch?' }}
            outputBubble={{ who: 'masked stream', text: 'Paracetamol — 5,200 units in stock at your branch. Need substitutes or another store?' }}
            stages={[
              { label: 'Handshake', title: 'session/create', color: 'amber', lines: ['origin allowlist', 'HMAC → store bound', 'widget.js + public key'], sub: '15-min token' },
              { label: 'Agent', title: '37-agent team', color: 'red', lines: ['router → stock_check', 'KG + brain (substitutes)', 'same team as UI'], sub: 'tools run' },
              { label: 'Mask', title: '3-tier scope', color: 'coral', lines: ['own branch → full qty', 'other stores → hidden', 'sanitize prose'], sub: 'stream steps' },
            ]}
            legend={[
              { label: 'origin + HMAC', color: 'amber' },
              { label: 'agent + tools', color: 'red' },
              { label: 'tier-mask + stream', color: 'coral' },
            ]}
          />
        </section>

        <section class="emp-panel">
          <div class="emp-h">STATUS</div>
          <div class="emp-status-grid">
            <div><span class="emp-k">widgets live</span><span class="emp-dot emp-on">●</span> {liveCount} widget{liveCount === 1 ? '' : 's'} live</div>
            <div><span class="emp-k">project</span><code class="emp-code">{slug}</code></div>
            <div><span class="emp-k">base_url</span><code class="emp-code">{baseUrl}</code></div>
            <div><span class="emp-k">auth modes</span>public · hmac · jwt</div>
          </div>
        </section>

        <section class="emp-panel">
          <div class="emp-h">ENDPOINTS</div>
          <table class="emp-table">
            <thead><tr><th>method</th><th>path</th><th>description</th></tr></thead>
            <tbody>
              <tr>
                <td><span class="emp-method">GET</span></td>
                <td><code class="emp-code">/api/embed/widget.js</code></td>
                <td class="emp-muted">embed widget loader script</td>
              </tr>
              <tr>
                <td><span class="emp-method emp-post">POST</span></td>
                <td><code class="emp-code">/api/embed/session/create</code></td>
                <td class="emp-muted">create a chat session (returns token)</td>
              </tr>
              <tr>
                <td><span class="emp-method emp-post">POST</span></td>
                <td><code class="emp-code">/api/embed/chat</code></td>
                <td class="emp-muted">send a message (rate-limited, audited)</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="emp-panel">
          <div class="emp-h">QUICK TEST</div>
          <p class="emp-doc-p">Verify widget.js loads from your domain:</p>
          <div class="emp-codeblock">
            <button class="emp-copybtn" onclick={() => copyText(widgetJsTest, 'wjstest')}>{copied === 'wjstest' ? '✓' : 'copy'}</button>
            <pre class="emp-pre">{widgetJsTest}</pre>
          </div>
          <p class="emp-muted emp-fineprint">Expect: HTTP 200 + Content-Type: application/javascript</p>
        </section>
      {/if}

      <!-- ==================== WIDGETS ==================== -->
      {#if view === 'widgets'}
        <section class="emp-panel">
          <div class="emp-h-row">
            <div class="emp-h">WIDGETS ({embeds.length})</div>
            <button class="emp-btn emp-btn-accent" onclick={() => { newEmbedOpen = !newEmbedOpen; newEmbedKey = ''; newEmbedErr = ''; }}>+ NEW EMBED</button>
          </div>

          {#if newEmbedOpen}
            <div class="emp-mint">
              <div class="emp-mint-grid">
                <label class="emp-field">
                  <span class="emp-flabel">name</span>
                  <input class="emp-input" placeholder="pharmacy-counter" bind:value={newEmbedName} />
                </label>
                <div class="emp-field">
                  <span class="emp-flabel">scope</span>
                  <div class="emp-radio">
                    <label><input type="radio" bind:group={newEmbedScope} value="store" /> store <span class="emp-muted">(tiered)</span></label>
                    <label><input type="radio" bind:group={newEmbedScope} value="global" /> global <span class="emp-muted">(full)</span></label>
                  </div>
                </div>
              </div>

              {#if newEmbedScope === 'store'}
                <div class="emp-field">
                  <span class="emp-flabel">outlets <span class="emp-muted">— one embed, many stores (Tier-1 set)</span></span>
                  {#if newEmbedOutlets.length}
                    <div class="emp-chips">
                      {#each newEmbedOutlets as o (o)}
                        <span class="emp-chip">{o}<button class="emp-chip-x" onclick={() => removeOutlet(o)}>×</button></span>
                      {/each}
                    </div>
                  {/if}
                  <div class="emp-picker">
                    <input class="emp-input" placeholder="filter / type a site_code…" bind:value={outletFilter}
                           onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTypedOutlet(); } }} />
                    <button class="emp-btn emp-btn-sm" onclick={addTypedOutlet} disabled={!outletFilter.trim()}>+ add</button>
                  </div>
                  {#if outletChoices.length}
                    <div class="emp-choices">
                      {#each outletChoices as o (o)}
                        <button class="emp-choice" onclick={() => addOutlet(o)}>{o}</button>
                      {/each}
                    </div>
                  {:else if outlets.length === 0}
                    <div class="emp-muted emp-fineprint">no outlet list — type site_codes manually + add</div>
                  {/if}
                </div>
              {/if}

              <div class="emp-mint-actions">
                <button class="emp-btn emp-btn-accent" disabled={newEmbedBusy} onclick={createEmbed}>{newEmbedBusy ? '◐…' : 'CREATE'}</button>
                <button class="emp-btn" onclick={() => { newEmbedOpen = false; resetNewEmbed(); }}>cancel</button>
                {#if newEmbedErr}<span class="emp-err">✗ {newEmbedErr}</span>{/if}
              </div>

              {#if newEmbedKey}
                <div class="emp-minted">
                  <div class="emp-warn">◐ key shown once — copy now, it will not be displayed again</div>
                  <div class="emp-minted-row">
                    <code class="emp-code emp-code-key">{newEmbedKey}</code>
                    <button class="emp-btn" onclick={() => copyText(newEmbedKey, 'emkey')}>{copied === 'emkey' ? '✓ copied' : 'copy'}</button>
                  </div>
                </div>
              {/if}
            </div>
          {/if}

          {#if embedsLoading}
            <div class="emp-row emp-muted">◐ loading…</div>
          {:else if embedsErr}
            <div class="emp-row emp-err">✗ {embedsErr}</div>
          {:else if embeds.length === 0}
            <div class="emp-row emp-muted">no embeds yet — create one above</div>
          {:else}
            <table class="emp-table">
              <thead><tr><th>name</th><th>auth</th><th>origins</th><th>status</th><th>snippet</th><th>test</th><th>revoke</th></tr></thead>
              <tbody>
                {#each embeds as e (e.embed_id || e.id)}
                  {@const isLive = e.enabled !== false && e.status !== 'revoked' && e.status !== 'disabled'}
                  {@const eid = e.embed_id || e.id}
                  {@const isOpen = expandedEmbed === eid}
                  <tr class="emp-row-click" class:emp-row-open={isOpen} onclick={() => toggleEmbedRow(eid)}>
                    <td><button class="emp-expand" onclick={(ev) => { ev.stopPropagation(); toggleEmbedRow(eid); }}>{isOpen ? '▾' : '▸'}</button> <code class="emp-code">{e.name || eid}</code></td>
                    <td>{e.auth_mode ?? 'public'}</td>
                    <td>
                      {#if Array.isArray(e.allowed_origins) && e.allowed_origins.length}
                        {e.allowed_origins.length} origin{e.allowed_origins.length > 1 ? 's' : ''}
                      {:else}
                        <span class="emp-muted">any</span>
                      {/if}
                    </td>
                    <td>
                      {#if isLive}
                        <span class="emp-badge emp-badge-on">● live</span>
                      {:else}
                        <span class="emp-badge emp-badge-off">✗ revoked</span>
                      {/if}
                    </td>
                    <td>
                      <button class="emp-btn emp-btn-sm" onclick={(ev) => {
                        ev.stopPropagation();
                        const snip = buildSnippet(eid, e.public_key || '', e.bound_scope_id || e.store_id || '');
                        copyText(snip, `snip-${eid}`);
                      }}>{copied === `snip-${eid}` ? '✓ copied' : 'snippet'}</button>
                    </td>
                    <td>
                      {#if isLive}
                        <button class="emp-btn emp-btn-sm" onclick={(ev) => { ev.stopPropagation(); window.open(`${baseUrl}/api/embed/try/${eid}`, '_blank'); }} title="Open a live chat sandbox for this widget">▶ Test chat</button>
                      {/if}
                    </td>
                    <td>
                      {#if isLive}
                        <button class="emp-btn emp-btn-sm emp-btn-danger" onclick={(ev) => { ev.stopPropagation(); revokeEmbed(eid); }}>Revoke</button>
                      {/if}
                    </td>
                  </tr>

                  {#if isOpen}
                    <tr class="emp-detail-row">
                      <td colspan="7">
                        <div class="emp-detail">
                          <div class="emp-detail-line">
                            <span class="emp-dl-k">EMBED ID</span>
                            <code class="emp-code emp-code-key">{eid}</code>
                            <button class="emp-btn emp-btn-sm" onclick={(ev) => { ev.stopPropagation(); copyText(eid, `eid-${eid}`); }}>{copied === `eid-${eid}` ? '✓' : 'copy'}</button>
                          </div>
                          <div class="emp-detail-line">
                            <span class="emp-dl-k">PUBLIC KEY</span>
                            <code class="emp-code emp-code-key">{e.public_key || '—'}</code>
                            {#if e.public_key}<button class="emp-btn emp-btn-sm" onclick={(ev) => { ev.stopPropagation(); copyText(e.public_key, `pk-${eid}`); }}>{copied === `pk-${eid}` ? '✓' : 'copy'}</button>{/if}
                            <button class="emp-btn emp-btn-sm" disabled={rotatingKey === eid} onclick={(ev) => { ev.stopPropagation(); rotateEmbedKey(eid); }} title="Generate a new public key — the old one stops working">{rotatingKey === eid ? '◐…' : '↻ rotate'}</button>
                            {#if isLive}<button class="emp-btn emp-btn-sm emp-btn-danger" onclick={(ev) => { ev.stopPropagation(); revokeEmbed(eid); }} title="Disable this widget entirely">Revoke</button>{/if}
                          </div>
                          <div class="emp-detail-line">
                            <span class="emp-dl-k">ENDPOINT</span>
                            <code class="emp-code">{baseUrl}/api/embed/chat</code>
                          </div>
                          <div class="emp-detail-line">
                            <span class="emp-dl-k">SCOPE</span>
                            <span class="emp-muted">{embedScopeLabel(e)}</span>
                          </div>
                          <div class="emp-detail-line">
                            <span class="emp-dl-k">RATE</span>
                            <span class="emp-muted">{e.rate_limit_per_min ?? 30}/min · auth {e.auth_mode ?? 'public'}</span>
                          </div>

                          <div class="emp-dl-snip">
                            <div class="emp-dl-snip-head">
                              <span class="emp-dl-k">SNIPPET</span>
                              <button class="emp-btn emp-btn-sm" onclick={(ev) => { ev.stopPropagation(); copyText(buildSnippet(eid, e.public_key || '', e.bound_scope_id || e.store_id || ''), `dsnip-${eid}`); }}>{copied === `dsnip-${eid}` ? '✓ copied' : 'copy'}</button>
                            </div>
                            <pre class="emp-codeblock">{buildSnippet(eid, e.public_key || '', e.bound_scope_id || e.store_id || '')}</pre>
                          </div>

                          {#if isLive}
                            <div class="emp-detail-actions">
                              <button class="emp-btn emp-btn-sm emp-btn-accent" onclick={(ev) => { ev.stopPropagation(); window.open(`${baseUrl}/api/embed/try/${eid}`, '_blank'); }}>▶ Open chat test</button>
                              <button class="emp-btn emp-btn-sm" onclick={(ev) => { ev.stopPropagation(); configEmbed = e; view = 'playground'; }}>⚙ Playground</button>
                            </div>
                          {/if}
                        </div>
                      </td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>
          {/if}
        </section>
      {/if}

      <!-- ==================== CONFIG ==================== -->
      {#if view === 'playground'}
        {#if embeds.length === 0}
          <section class="emp-panel">
            <div class="emp-row emp-muted">no embeds yet — create one in Widgets first</div>
          </section>
        {:else}
          <div class="emp-pg-grid">
          <section class="emp-panel emp-pg-col">
            <div class="emp-h">APPEARANCE — {configEmbed?.name || configEmbed?.embed_id || 'first embed'}</div>
            <div class="emp-config-grid">
              <label class="emp-field">
                <span class="emp-flabel">primary color</span>
                <div class="emp-color-row">
                  <input class="emp-input emp-input-color" type="color" bind:value={configColor} />
                  <input class="emp-input emp-input-hex" bind:value={configColor} placeholder="#c96342" />
                </div>
              </label>
              <label class="emp-field">
                <span class="emp-flabel">position</span>
                <select class="emp-input emp-select" bind:value={configPosition}>
                  <option value="bottom-right">bottom-right</option>
                  <option value="bottom-left">bottom-left</option>
                </select>
              </label>
              <label class="emp-field">
                <span class="emp-flabel">theme</span>
                <select class="emp-input emp-select" bind:value={configTheme}>
                  <option value="default">default</option>
                  <option value="dark">dark</option>
                </select>
              </label>
              <label class="emp-field">
                <span class="emp-flabel">welcome message</span>
                <input class="emp-input" placeholder="Hi! How can I help you today?" bind:value={configWelcome} />
              </label>
              <label class="emp-field emp-field-full">
                <span class="emp-flabel">logo <span class="emp-muted">(upload or paste a URL)</span></span>
                <div class="emp-logo-row">
                  {#if configLogo}
                    <img class="emp-logo-preview" src={configLogo} alt="logo" />
                  {:else}
                    <span class="emp-logo-empty">no logo</span>
                  {/if}
                  <input class="emp-input emp-logo-url" placeholder="https://example.com/logo.png" bind:value={configLogo} />
                  <input id="emp-logo-file" class="emp-logo-file" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif" onchange={uploadLogo} />
                  <button type="button" class="emp-btn emp-btn-sm" disabled={logoUploading || !configEmbed} onclick={() => document.getElementById('emp-logo-file')?.click()}>{logoUploading ? '◐ uploading…' : '⤓ upload'}</button>
                  {#if configLogo}<button type="button" class="emp-btn emp-btn-sm" onclick={() => configLogo = ''}>clear</button>{/if}
                </div>
                {#if logoErr}<span class="emp-err emp-logo-err">✗ {logoErr}</span>{/if}
                <span class="emp-muted emp-fineprint">png · jpg · webp · svg · gif · max 1MB. Upload saves immediately + fills the URL; click SAVE to keep other appearance changes.</span>
              </label>
            </div>
            <div class="emp-btnrow emp-mt">
              <button class="emp-btn emp-btn-accent" disabled={configBusy} onclick={saveConfig}>{configBusy ? '◐…' : 'SAVE'}</button>
              {#if configSaved}<span class="emp-saved">✓ saved</span>{/if}
              {#if configErr}<span class="emp-err">✗ {configErr}</span>{/if}
            </div>
          </section>

          <!-- ── right column: live chat bubble ── -->
          <section class="emp-panel emp-pg-col emp-pg-chatcol">
            <div class="emp-chat-card">
              <div class="emp-chat-head" style="background:{configColor};">
                <span class="emp-chat-av">{(configEmbed?.name || 'C').slice(0,1).toUpperCase()}</span>
                <div class="emp-chat-htext">
                  <strong>{configEmbed?.name || 'CityPharma'}</strong>
                  <span class="emp-chat-status">● online · we reply instantly</span>
                </div>
              </div>
              <div class="emp-chat-body" bind:this={sbBodyEl}>
                <div class="emp-msg emp-msg-bot">
                  <span class="emp-msg-av" style="background:{configColor};">{(configEmbed?.name || 'C').slice(0,1).toUpperCase()}</span>
                  <div class="emp-bubble emp-bubble-bot">{configWelcome || 'Hi! How can I help?'}</div>
                </div>
                {#each sbMsgs as m}
                  {#if m.role === 'you'}
                    <div class="emp-msg emp-msg-you">
                      <div class="emp-bubble emp-bubble-you" style="background:{configColor};">{m.text}</div>
                    </div>
                  {:else}
                    <div class="emp-msg emp-msg-bot">
                      <span class="emp-msg-av" style="background:{configColor};">{(configEmbed?.name || 'C').slice(0,1).toUpperCase()}</span>
                      <div class="emp-bubble emp-bubble-bot">{@html markdownToHtml(m.text)}{#if m.ms}<span class="emp-bubble-ms">{m.ms}ms</span>{/if}</div>
                    </div>
                  {/if}
                {/each}
                {#if sbBusy}
                  <div class="emp-msg emp-msg-bot">
                    <span class="emp-msg-av" style="background:{configColor};">{(configEmbed?.name || 'C').slice(0,1).toUpperCase()}</span>
                    <div class="emp-bubble emp-bubble-bot emp-typing"><span></span><span></span><span></span></div>
                  </div>
                {/if}
                {#if sbErr}<div class="emp-warn-text emp-fineprint">{sbErr}</div>{/if}
              </div>
              <div class="emp-chat-foot">
                <input class="emp-chat-input" placeholder="ask something…" bind:value={sbInput}
                  onkeydown={(e) => { if (e.key === 'Enter') sbSend(); }} disabled={sbBusy} />
                <button class="emp-chat-send" style="background:{configColor};" onclick={sbSend} disabled={sbBusy || !sbInput.trim()} aria-label="Send">→</button>
              </div>
            </div>
            <div class="emp-fineprint emp-muted">
              real session ({sbSession ? sbSession.slice(0,12) + '…' : 'new'}) · markdown rendered
              <button class="emp-btn emp-btn-sm emp-ml-8" onclick={sbReset}>Reset</button>
            </div>
            {#if !isLive || origins.length === 0}
              <div class="emp-warn-text emp-fineprint">⚠ widget not live / no origins — calls 403 until fixed in Widgets.</div>
            {/if}
          </section>
          </div>

          <!-- ── shareable test link ── -->
          <section class="emp-panel">
            <div class="emp-h">SHARE TEST LINK</div>
            <p class="emp-doc-p">Signed, expiring URL — share with your dev, no login. Impersonate a store/role to preview 3-tier masking. Secret never leaves the server.</p>
            <div class="emp-sb-controls">
              <label class="emp-field">expiry
                <select class="emp-input emp-sel" bind:value={sbLinkTtl}>
                  <option value={900}>15 min</option>
                  <option value={3600}>1 hour</option>
                  <option value={86400}>24 hours</option>
                </select>
              </label>
              <label class="emp-field">store <span class="emp-req">*</span>
                {#if outlets.length}
                  <select class="emp-input emp-sel" bind:value={sbStore} disabled={sbGlobal}>
                    <option value="">— pick a store —</option>
                    {#each outlets as o}<option value={o}>{o}</option>{/each}
                  </select>
                {:else}
                  <input class="emp-input" placeholder="20063-CCBRBKMY" bind:value={sbStore} disabled={sbGlobal} />
                {/if}
              </label>
              <label class="emp-field">role
                <select class="emp-input emp-sel" bind:value={sbRole}>
                  <option value="staff">staff</option>
                  <option value="customer">customer</option>
                </select>
              </label>
              <button class="emp-btn" onclick={sbGenLink} disabled={sbLinkBusy || (!sbGlobal && !sbStore)}>{sbLinkBusy ? '…' : 'Generate'}</button>
            </div>
            <label class="emp-fineprint emp-sb-global">
              <input type="checkbox" bind:checked={sbGlobal} />
              test global / catalog view (no store — tier 3)
            </label>
            {#if sbLinkErr}<div class="emp-warn-text emp-fineprint">{sbLinkErr}</div>{/if}
            {#if sbLink}
              <div class="emp-codeblock emp-mt">
                <button class="emp-copybtn" onclick={() => copyText(sbLink, 'sblink')}>{copied === 'sblink' ? '✓' : 'copy'}</button>
                <pre class="emp-pre emp-pre-wrap">{sbLink}</pre>
              </div>
              <div class="emp-fineprint">
                <span class="emp-muted">expires in {fmtExpIn(sbLinkExp)}</span>
                <button class="emp-btn emp-btn-sm emp-ml-8" onclick={() => window.open(sbLink, '_blank')}>Open ↗</button>
              </div>
            {/if}
          </section>
        {/if}
      {/if}

      <!-- ==================== MONITORING ==================== -->
      {#if view === 'monitoring' && wdId}
        <!-- ============ WIDGET DETAIL (full-screen drill-down) ============ -->
        {@const wh = wdData?.header || {}}
        {@const wk = wdData?.kpi || {}}
        <section class="emp-panel emp-wd-headpanel">
          <div class="emp-wd-headbar">
            <button class="emp-btn emp-btn-sm" onclick={backToMonitoring}>← Back to Monitoring</button>
            <div class="emp-wd-headmeta">
              <strong class="emp-wd-name">{wh.name || wdId}</strong>
              <code class="emp-code">{wdId}</code>
              {#if wh.scope}<span class="emp-scope-badge">● {wh.scope}</span>{/if}
              {#if wh.store}<span class="emp-muted"><span class="emp-k">store</span>{wh.store}</span>{/if}
              {#if wh.enabled === false}<span class="emp-err">✗ disabled</span>{/if}
              {#if wdBusy}<span class="emp-muted">◐ loading…</span>{/if}
            </div>
            <div class="emp-wd-headact">
              <div class="emp-pills">
                <button class="emp-pill" class:emp-pill-on={wdRange === '24h'} onclick={() => setWdRange('24h')}>24h</button>
                <button class="emp-pill" class:emp-pill-on={wdRange === '7d'} onclick={() => setWdRange('7d')}>7d</button>
                <button class="emp-pill" class:emp-pill-on={wdRange === '30d'} onclick={() => setWdRange('30d')}>30d</button>
              </div>
              <button class="emp-btn emp-btn-sm" onclick={wdExportJson} disabled={!wdData}>⬇ JSON</button>
            </div>
          </div>
        </section>

        {#if wdErr}
          <section class="emp-panel"><div class="emp-row emp-err">✗ widget detail unavailable ({wdErr})</div></section>
        {:else if wdBusy && !wdData}
          <section class="emp-panel"><div class="emp-row emp-muted">◐ loading widget…</div></section>
        {:else if wdData}
          <!-- KPI STRIP -->
          <section class="emp-panel">
            <div class="emp-kpi-grid">
              {#snippet wdKpi(label: string, value: any, sub: string = '', warn: boolean = false)}
                <div class="emp-kpi" class:emp-kpi-warn={warn}>
                  <div class="emp-kpi-label">{label}</div>
                  <div class="emp-kpi-val">{value}{#if warn} <span class="emp-err">⚠</span>{/if}</div>
                  {#if sub}<div class="emp-kpi-sub">{sub}</div>{/if}
                </div>
              {/snippet}
              {@render wdKpi('Calls', wk.requests ?? 0)}
              {@render wdKpi('Error %', `${Number(wk.error_pct ?? 0).toFixed(1)}%`, '', Number(wk.error_pct) > 5)}
              {@render wdKpi('Avg latency', fmtS(wk.latency_avg_ms), '', Number(wk.latency_avg_ms) > 10000)}
              {@render wdKpi('p95 latency', fmtS(wk.latency_p95_ms))}
              {@render wdKpi('p99 latency', fmtS(wk.latency_p99_ms))}
              {@render wdKpi('Users', wk.uniq_users ?? 0)}
              {@render wdKpi('Sessions', wk.uniq_sessions ?? 0)}
              {@render wdKpi('Errors', wk.errors ?? 0, '', Number(wk.errors) > 0)}
              {@render wdKpi('Avg reply', `${wk.avg_resp_chars ?? 0}ch`, `${wk.avg_msg_chars ?? 0}ch in`)}
            </div>
          </section>

          <div class="emp-mon-2col">
            <!-- ACTIVITY -->
            <section class="emp-panel">
              <div class="emp-subh">Activity <span class="emp-muted">· requests per {wdData.bucket}</span></div>
              {#if wdActBars.length === 0}
                <div class="emp-row emp-muted">no activity in window</div>
              {:else}
                <div class="emp-chart">
                  {#each wdActBars as b}
                    <div class="emp-chart-col" title={b.title}>
                      <div class="emp-chart-barwrap"><div class="emp-chart-bar" style={`height:${Math.max(2, b.pct)}%`}></div></div>
                      <div class="emp-chart-x">{b.label}</div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>

            <!-- LATENCY DIST -->
            <section class="emp-panel">
              <div class="emp-subh">Latency distribution</div>
              <div class="emp-lat-pcts">
                <span><span class="emp-k">p50</span>{fmtS(wk.latency_p50_ms)}</span>
                <span><span class="emp-k">p95</span>{fmtS(wk.latency_p95_ms)}</span>
                <span><span class="emp-k">p99</span>{fmtS(wk.latency_p99_ms)}</span>
              </div>
              {#if (wdData.latency_hist || []).every((h: any) => !h.count)}
                <div class="emp-row emp-muted">no latency data</div>
              {:else}
                <div class="emp-hbars">
                  {#each wdData.latency_hist as h}
                    <div class="emp-hbar-row" class:emp-hbar-tail={monIsTail(h.bucket)}>
                      <div class="emp-hbar-lbl">{h.bucket}{#if monIsTail(h.bucket) && h.count} <span class="emp-warn">⚠</span>{/if}</div>
                      <div class="emp-hbar-track"><div class="emp-hbar-fill" class:emp-hbar-fill-tail={monIsTail(h.bucket)} style={`width:${Math.max(1, Number(h.pct) || 0)}%`}></div></div>
                      <div class="emp-hbar-val">{h.count} <span class="emp-muted">({(Number(h.pct) || 0).toFixed(0)}%)</span></div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          </div>

          <div class="emp-mon-2col">
            <!-- ERRORS -->
            <section class="emp-panel">
              <div class="emp-subh">Errors & reliability</div>
              {#if !wdData.errors || Number(wdData.errors.total) === 0}
                <div class="emp-row emp-muted">✓ no errors in window</div>
              {:else}
                <div class="emp-row"><span class="emp-k">total</span>{wdData.errors.total}</div>
                {#if (wdData.errors.recent || []).length}
                  <table class="emp-table emp-mt">
                    <thead><tr><th>time</th><th>user</th><th>error</th></tr></thead>
                    <tbody>
                      {#each wdData.errors.recent as e}
                        <tr><td class="emp-muted">{e.ts}</td><td><code class="emp-code">{e.user}</code></td><td><span class="emp-err">{e.error}</span></td></tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
              {/if}
            </section>

            <!-- USERS + ORIGINS -->
            <section class="emp-panel">
              <div class="emp-subh">Top users</div>
              {#if (wdData.top_users || []).length === 0}
                <div class="emp-row emp-muted">no identified users</div>
              {:else}
                <table class="emp-table">
                  <tbody>
                    {#each wdData.top_users as u (u.user)}
                      <tr><td><code class="emp-code">{u.user}</code></td><td>{u.requests ?? 0}</td></tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
              <div class="emp-subh emp-mt">Origins</div>
              {#if (wdData.origins || []).length === 0}
                <div class="emp-row emp-muted">no origin data</div>
              {:else}
                <table class="emp-table">
                  <tbody>
                    {#each wdData.origins as o (o.origin)}
                      <tr><td><code class="emp-code">{o.origin}</code></td><td>{o.requests ?? 0}</td></tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            </section>
          </div>

          <!-- CALLS -->
          <section class="emp-panel">
            <div class="emp-subh">Calls ({(wdData.calls || []).length})</div>
            {#if !wdData.messages_enabled}
              <div class="emp-row emp-muted emp-wd-notice">chat bodies not logged · enable <code class="emp-code">EMBED_LOG_BODIES</code> to capture question/answer text</div>
            {/if}
            {#if (wdData.calls || []).length === 0}
              <div class="emp-row emp-muted">no calls in window</div>
            {:else}
              <table class="emp-table emp-table-click">
                <thead><tr><th>time</th><th>user</th><th>status</th><th>msg→resp</th><th>latency</th><th>origin</th><th></th></tr></thead>
                <tbody>
                  {#each wdData.calls as c (c.id)}
                    <tr class="emp-mon-row" onclick={() => toggleWdCall(c.id)}>
                      <td class="emp-muted">{c.ts}</td>
                      <td><code class="emp-code">{c.user}</code></td>
                      <td>{#if c.success}<span class="emp-wd-ok">✓</span>{:else}<span class="emp-err" title={c.error || 'error'}>✗</span>{/if}</td>
                      <td>{c.msg_chars}→{c.resp_chars}ch</td>
                      <td>{fmtS(c.latency_ms)}</td>
                      <td class="emp-muted">{c.origin}</td>
                      <td class="emp-mon-chev">{wdCallOpen === c.id ? '▾' : '›'}</td>
                    </tr>
                    {#if wdCallOpen === c.id}
                      <tr class="emp-wd-calldetail"><td colspan="7">
                        <div class="emp-wd-callfull">
                          <div class="emp-wd-qlabel">QUESTION</div>
                          <div class="emp-wd-qtext">{c.question ?? '— not logged —'}</div>
                          <div class="emp-wd-qlabel emp-mt">ANSWER</div>
                          <div class="emp-wd-qtext">{c.answer ?? '— not logged —'}</div>
                          {#if c.error}<div class="emp-wd-qlabel emp-mt">ERROR</div><div class="emp-row emp-err">{c.error}</div>{/if}
                          <div class="emp-wd-qmeta emp-mt">
                            <span class="emp-k">session</span><code class="emp-code">{c.session ?? '—'}</code>
                            <span class="emp-k">latency</span>{fmtS(c.latency_ms)}
                            <span class="emp-k">chars</span>{c.msg_chars}→{c.resp_chars}
                            <span class="emp-k">origin</span>{c.origin}
                          </div>
                        </div>
                      </td></tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>
        {/if}
      {:else if view === 'monitoring'}
        <!-- A. FILTER ROW -->
        <section class="emp-panel">
          <div class="emp-h-row">
            <div class="emp-h">WIDGET MONITORING <span class="emp-an-live" title="live from dash_embed_calls">● LIVE</span></div>
            <div class="emp-btnrow">
              <button class="emp-btn emp-btn-sm" onclick={monExportCsv} disabled={!monData}>⬇ CSV</button>
              <button class="emp-btn emp-btn-sm" onclick={monExportJson} disabled={!monData}>⬇ JSON</button>
            </div>
          </div>
          <div class="emp-mon-filters">
            <div class="emp-pills">
              <button class="emp-pill" class:emp-pill-on={monDays === 1} onclick={() => setMonDays(1)}>24h</button>
              <button class="emp-pill" class:emp-pill-on={monDays === 7} onclick={() => setMonDays(7)}>7d</button>
              <button class="emp-pill" class:emp-pill-on={monDays === 30} onclick={() => setMonDays(30)}>30d</button>
            </div>
            <select class="emp-sel" bind:value={monEmbed} onchange={onMonEmbed} title="filter by widget">
              <option value="">all widgets</option>
              {#each embeds as e (e.embed_id || e.id)}
                <option value={e.embed_id || e.id}>{e.name || e.bound_scope_id || e.embed_id || e.id}</option>
              {/each}
            </select>
            <div class="emp-pills" title="bucket granularity">
              <button class="emp-pill" class:emp-pill-on={monBucket === 'hour'} onclick={() => setMonBucket('hour')}>○ hour</button>
              <button class="emp-pill" class:emp-pill-on={monBucket === 'day'} onclick={() => setMonBucket('day')}>● day</button>
            </div>
            {#if monBusy}<span class="emp-muted">◐ loading…</span>{/if}
          </div>
        </section>

        {#if monErr}
          <section class="emp-panel"><div class="emp-row emp-err">✗ monitoring unavailable ({monErr})</div></section>
        {:else if !monData && monBusy}
          <section class="emp-panel"><div class="emp-row emp-muted">◐ loading monitoring…</div></section>
        {:else if monData}
          {@const k = monData.kpi || {}}
          <!-- B. KPI STRIP -->
          <section class="emp-panel">
            <div class="emp-kpi-grid">
              {#snippet kpi(label: string, value: any, sub: string = '', warn: boolean = false)}
                <div class="emp-kpi" class:emp-kpi-warn={warn}>
                  <div class="emp-kpi-label">{label}</div>
                  <div class="emp-kpi-val">{value}{#if warn} <span class="emp-err">⚠</span>{/if}</div>
                  {#if sub}<div class="emp-kpi-sub">{sub}</div>{/if}
                </div>
              {/snippet}
              {@render kpi('Requests', k.requests ?? 0)}
              {@render kpi('Error %', `${Number(k.error_pct ?? 0).toFixed(1)}%`, '', Number(k.error_pct) > 5)}
              {@render kpi('Avg latency', k.latency_avg_ms ? `${(k.latency_avg_ms / 1000).toFixed(1)}s` : '—', '', Number(k.latency_avg_ms) > 10000)}
              {@render kpi('p95 latency', k.latency_p95_ms ? `${(k.latency_p95_ms / 1000).toFixed(1)}s` : '—')}
              {@render kpi('p99 latency', k.latency_p99_ms ? `${(k.latency_p99_ms / 1000).toFixed(1)}s` : '—')}
              {@render kpi('Unique users', k.uniq_users ?? 0)}
              {@render kpi('Sessions', k.uniq_sessions ?? 0)}
              {@render kpi('Active widgets', k.active_embeds ?? '0/0')}
              {@render kpi('Avg reply chars', k.avg_resp_chars ?? 0)}
            </div>
          </section>

          <div class="emp-mon-2col">
            <!-- C. ACTIVITY CHART -->
            <section class="emp-panel">
              <div class="emp-subh">Activity <span class="emp-muted">· requests per {monBucket}</span></div>
              {#if monActBars.length === 0}
                <div class="emp-row emp-muted">no activity in window</div>
              {:else}
                <div class="emp-chart">
                  {#each monActBars as b}
                    <div class="emp-chart-col" title={b.title}>
                      <div class="emp-chart-barwrap"><div class="emp-chart-bar" style={`height:${Math.max(2, b.pct)}%`}></div></div>
                      <div class="emp-chart-x">{b.label}</div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>

            <!-- D. LATENCY DISTRIBUTION -->
            <section class="emp-panel">
              <div class="emp-subh">Latency distribution</div>
              <div class="emp-lat-pcts">
                <span><span class="emp-k">p50</span>{k.latency_p50_ms ? `${(k.latency_p50_ms / 1000).toFixed(1)}s` : '—'}</span>
                <span><span class="emp-k">p95</span>{k.latency_p95_ms ? `${(k.latency_p95_ms / 1000).toFixed(1)}s` : '—'}</span>
                <span><span class="emp-k">p99</span>{k.latency_p99_ms ? `${(k.latency_p99_ms / 1000).toFixed(1)}s` : '—'}</span>
              </div>
              {#if (monData.latency_hist || []).every((h: any) => !h.count)}
                <div class="emp-row emp-muted">no latency data</div>
              {:else}
                <div class="emp-hbars">
                  {#each monData.latency_hist as h}
                    <div class="emp-hbar-row" class:emp-hbar-tail={monIsTail(h.bucket)}>
                      <div class="emp-hbar-lbl">{h.bucket}{#if monIsTail(h.bucket) && h.count} <span class="emp-warn">⚠</span>{/if}</div>
                      <div class="emp-hbar-track"><div class="emp-hbar-fill" class:emp-hbar-fill-tail={monIsTail(h.bucket)} style={`width:${Math.max(1, Number(h.pct) || 0)}%`}></div></div>
                      <div class="emp-hbar-val">{h.count} <span class="emp-muted">({(Number(h.pct) || 0).toFixed(0)}%)</span></div>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          </div>

          <!-- E. PER-WIDGET / PER-STORE -->
          <section class="emp-panel">
            <div class="emp-subh">By widget / store</div>
            {#if (monData.by_embed || []).length === 0}
              <div class="emp-row emp-muted">no widget traffic in window</div>
            {:else}
              <table class="emp-table emp-table-click">
                <thead><tr><th>widget</th><th>store</th><th>req</th><th>err</th><th>p95</th><th>last</th><th></th></tr></thead>
                <tbody>
                  {#each monData.by_embed as r (r.embed_id)}
                    <tr class="emp-mon-row" onclick={() => openWidget(r.embed_id)} title="open widget detail">
                      <td>{r.name}<br /><code class="emp-code">{r.embed_id}</code></td>
                      <td><code class="emp-code">{r.store}</code></td>
                      <td>{r.requests ?? 0}</td>
                      <td>{r.errors ?? 0}{#if Number(r.errors) > 0} <span class="emp-err">⚠</span>{/if}</td>
                      <td>{r.p95_ms ? `${(r.p95_ms / 1000).toFixed(1)}s` : '—'}</td>
                      <td class="emp-muted">{r.last ?? '—'}</td>
                      <td class="emp-mon-chev">›</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>

          <div class="emp-mon-2col">
            <!-- F. TOP USERS -->
            <section class="emp-panel">
              <div class="emp-subh">Top users</div>
              {#if (monData.top_users || []).length === 0}
                <div class="emp-row emp-muted">no identified users</div>
              {:else}
                <table class="emp-table">
                  <thead><tr><th>user</th><th>req</th></tr></thead>
                  <tbody>
                    {#each monData.top_users as u (u.user)}
                      <tr><td><code class="emp-code">{u.user}</code></td><td>{u.requests ?? 0}</td></tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            </section>

            <!-- G. ORIGINS -->
            <section class="emp-panel">
              <div class="emp-subh">Origins</div>
              {#if (monData.origins || []).length === 0}
                <div class="emp-row emp-muted">no origin data</div>
              {:else}
                <table class="emp-table">
                  <thead><tr><th>origin</th><th>req</th></tr></thead>
                  <tbody>
                    {#each monData.origins as o (o.origin)}
                      <tr><td><code class="emp-code">{o.origin}</code></td><td>{o.requests ?? 0}</td></tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            </section>
          </div>
        {:else}
          <section class="emp-panel">
            <div class="emp-empty-state emp-muted">
              <div class="emp-empty-icon">○</div>
              <div>No monitoring data yet</div>
              <div class="emp-fineprint">Traffic will appear here once your embed widgets receive calls.</div>
            </div>
          </section>
        {/if}
      {/if}

      <!-- ==================== USAGE ==================== -->
      {#if view === 'usage'}
        <section class="emp-panel">
          <div class="emp-h-row">
            <div class="emp-h">USAGE</div>
            <div class="emp-pills">
              <button class="emp-pill" class:emp-pill-on={usageDays === 1} onclick={() => setUsageDays(1)}>1d</button>
              <button class="emp-pill" class:emp-pill-on={usageDays === 7} onclick={() => setUsageDays(7)}>7d</button>
              <button class="emp-pill" class:emp-pill-on={usageDays === 30} onclick={() => setUsageDays(30)}>30d</button>
            </div>
          </div>
          {#if usageErr}
            <div class="emp-row emp-err">✗ usage unavailable ({usageErr})</div>
          {:else if usageData}
            <div class="emp-status-grid">
              <div><span class="emp-k">calls</span>{usageData.calls ?? usageData.total_calls ?? 0}</div>
              <div><span class="emp-k">tokens</span>{usageData.tokens ?? usageData.total_tokens ?? 0}</div>
              <div><span class="emp-k">sessions</span>{usageData.sessions ?? usageData.total_sessions ?? 0}</div>
              <div><span class="emp-k">p50 latency</span>{usageData.p50_ms ? `${usageData.p50_ms}ms` : '—'}</div>
            </div>
          {:else}
            <div class="emp-row emp-muted">◐ loading… (or no usage data yet)</div>
            <div class="emp-empty-state emp-muted">
              <div class="emp-empty-icon">○</div>
              <div>No usage data yet</div>
              <div class="emp-fineprint">Usage will appear here once the embed widget receives traffic.</div>
            </div>
          {/if}
        </section>
      {/if}

      <!-- ==================== DEVELOPER ==================== -->
      {#if view === 'developer'}
        <!-- status banner: live? origins? -->
        <section class="emp-panel">
          <div class="emp-statusbar" class:emp-status-warn={!isLive || origins.length === 0}>
            <span class="emp-dot {isLive ? 'emp-on' : 'emp-amber'}">●</span>
            <strong>{isLive ? 'live' : 'draft'}</strong>
            <span class="emp-muted">·</span>
            <span class="emp-muted">origins:</span>
            {#if origins.length}
              {#each origins as o}<code class="emp-code">{o}</code>{/each}
            {:else}
              <span class="emp-warn-text">none — widget is blocked everywhere</span>
            {/if}
            <button class="emp-btn emp-btn-sm emp-ml-auto" onclick={() => nav('widgets')}>Edit → Widgets</button>
          </div>
          {#if !isLive || origins.length === 0}
            <div class="emp-muted emp-fineprint">⚠ Before any snippet works: set the widget <strong>live</strong> and add your site origin(s) to the allowlist.</div>
          {/if}
        </section>

        <section class="emp-panel">
          <div class="emp-h">INTEGRATION SNIPPET</div>
          <p class="emp-doc-p">Keys below are your real <code class="emp-code">{eid}</code> values — copy-paste ready.</p>
          <div class="emp-pathtabs">
            <button class="emp-pathtab" class:emp-pt-on={docPath === 'dropin'} onclick={() => docPath = 'dropin'}>Drop-in</button>
            <button class="emp-pathtab" class:emp-pt-on={docPath === 'php'} onclick={() => docPath = 'php'}>HMAC (PHP)</button>
            <button class="emp-pathtab" class:emp-pt-on={docPath === 'rest'} onclick={() => docPath = 'rest'}>REST</button>
          </div>
          <div class="emp-pathdesc emp-muted">
            {#if docPath === 'dropin'}Floating chat bubble · anonymous · simplest.
            {:else if docPath === 'php'}Logged-in app · server signs the user payload → per-store masking. <code class="emp-code">secret_key</code> stays server-side.
            {:else}Build your own UI · session/create → chat.{/if}
          </div>
          <div class="emp-codeblock">
            <button class="emp-copybtn" onclick={() => copyText(docSnippet, 'doc')}>{copied === 'doc' ? '✓' : 'copy'}</button>
            <pre class="emp-pre">{docSnippet}</pre>
          </div>
          <button class="emp-btn emp-btn-sm emp-mt" onclick={() => nav('sandbox')}>⚡ Test in Sandbox →</button>
        </section>

        <section class="emp-panel">
          <div class="emp-h-row">
            <div class="emp-h">DOWNLOAD SDK · READY-TO-PASTE CODE</div>
            <button class="emp-btn emp-btn-sm" onclick={downloadSdkZip}>⤓ Download all (.zip)</button>
          </div>
          <p class="emp-doc-p">Drop-in client code for your stack. Every file is pre-filled with your real <code class="emp-code">{eid}</code> / key / host — preview, copy, or download and run as-is. No dependencies.</p>
          {#if !sdkFiles.length}
            <div class="emp-muted emp-fineprint">{sdkLoaded ? 'No SDK files deployed on the server.' : 'Loading…'}</div>
          {:else}
            <div class="emp-sdk-list">
              {#each sdkFiles as f}
                <div class="emp-sdk-row">
                  <div class="emp-sdk-meta">
                    <code class="emp-sdk-name">{f.name}</code>
                    <span class="emp-sdk-lang">{f.lang}</span>
                    <span class="emp-muted emp-sdk-desc">{f.desc}</span>
                  </div>
                  <div class="emp-sdk-actions">
                    <button class="emp-btn emp-btn-sm" onclick={() => previewSdk(f.name)}>{sdkPreviewName === f.name ? 'hide' : 'preview'}</button>
                    <button class="emp-btn emp-btn-sm" onclick={() => downloadSdk(f.name)}>⤓ download</button>
                  </div>
                </div>
                {#if sdkPreviewName === f.name}
                  <div class="emp-codeblock emp-sdk-preview">
                    <button class="emp-copybtn" onclick={() => copyText(sdkPreviewBody, `sdk-${f.name}`)}>{copied === `sdk-${f.name}` ? '✓' : 'copy'}</button>
                    <pre class="emp-pre">{sdkPreviewBusy ? 'loading…' : sdkPreviewBody}</pre>
                  </div>
                {/if}
              {/each}
            </div>
          {/if}
          <div class="emp-muted emp-fineprint">PHP page <code class="emp-code">widget-embed.php</code> = fastest user-scoped path. <code class="emp-code">CityAgentClient.php</code> / <code class="emp-code">rest_client.py</code> / <code class="emp-code">rest_client.js</code> = SDKs for your own UI. <code class="emp-code">quickstart.sh</code> = 10-sec smoke test.</div>
        </section>

        <section class="emp-panel">
          <div class="emp-h">ERRORS</div>
          <table class="emp-table">
            <thead><tr><th>response</th><th>fix</th></tr></thead>
            <tbody>
              <tr><td><code class="emp-code">403 origin not allowed</code></td><td>add your site origin in Widgets allowlist; send the <code class="emp-code">Origin</code> header</td></tr>
              <tr><td><code class="emp-code">403 embed disabled</code></td><td>widget status is draft — set it live</td></tr>
              <tr><td><code class="emp-code">403 invalid user signature</code></td><td>canonical JSON must be sorted-keys + no spaces, same <code class="emp-code">secret_key</code></td></tr>
              <tr><td><code class="emp-code">429</code></td><td>rate limited — back off or raise the per-embed limit in Config</td></tr>
            </tbody>
          </table>
          <div class="emp-muted emp-fineprint">Public shareable docs (no login): <a class="emp-link" href="{baseUrl}/api/embed/docs" target="_blank">{baseUrl}/api/embed/docs ↗</a></div>
        </section>

        <section class="emp-panel">
          <div class="emp-h">3-TIER ACCESS REFERENCE</div>
          <div class="emp-tiers">
            <div class="emp-tier"><span class="emp-dot emp-on">●</span> <strong>Tier 1 — own stores</strong> <span class="emp-muted">any site_code in the key's SET → full qty + cost</span></div>
            <div class="emp-tier"><span class="emp-dot emp-amber">◐</span> <strong>Tier 2 — other stores</strong> <span class="emp-muted">availability only — no qty / no price</span></div>
            <div class="emp-tier"><span class="emp-dot emp-open">○</span> <strong>Tier 3 — no site_code</strong> <span class="emp-muted">open catalog (substitutes, indications, reference)</span></div>
          </div>
          <div class="emp-muted emp-fineprint">Boundary = toolset, not prompt. Store-bound keys lose raw SQL at build time — the API cannot leak data outside its tier even if the widget is reverse-engineered.</div>
        </section>

        <section class="emp-panel">
          <div class="emp-h">DATA ATTRIBUTES</div>
          <table class="emp-table">
            <thead><tr><th>attribute</th><th>required</th><th>description</th></tr></thead>
            <tbody>
              <tr><td><code class="emp-code">data-embed-id</code></td><td>yes</td><td>embed widget ID (from Widgets tab)</td></tr>
              <tr><td><code class="emp-code">data-key</code></td><td>yes</td><td>public key (rotatable, non-secret)</td></tr>
              <tr><td><code class="emp-code">data-user</code></td><td>no</td><td>JSON string: <code class="emp-code">{"{"}"store_id":"...","role":"..."{"}"}</code></td></tr>
              <tr><td><code class="emp-code">data-sig</code></td><td>hmac only</td><td>HMAC-SHA256 of data-user payload with your secret key</td></tr>
              <tr><td><code class="emp-code">data-accent</code></td><td>no</td><td>override primary color at embed time (e.g. <code class="emp-code">#3a8dff</code>)</td></tr>
              <tr><td><code class="emp-code">data-logo</code></td><td>no</td><td>URL of logo shown in widget header</td></tr>
            </tbody>
          </table>
        </section>
      {/if}


    </main>
  </div>
{/if}

<style>
  /* expandable widget rows (mirror Gateway Outlet Keys) */
  .emp-row-click { cursor: pointer; }
  .emp-row-click:hover { background: var(--pw-bg-alt, #f6f2ea); }
  .emp-row-open { background: var(--pw-bg-alt, #f6f2ea); }
  .emp-expand { background: none; border: none; cursor: pointer; color: var(--pw-accent, #9a4a2f); font-size: 12px; padding: 0 4px 0 0; font-family: inherit; }
  .emp-detail-row > td { padding: 0 !important; background: var(--pw-bg-alt, #f6f2ea); border-bottom: 1px solid var(--pw-border, #e5ddcf); }
  .emp-detail { display: flex; flex-direction: column; gap: 8px; padding: 14px 18px 18px; }
  .emp-detail-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .emp-dl-k { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); font-weight: 600; min-width: 90px; }
  .emp-dl-snip { margin-top: 6px; }
  .emp-dl-snip-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .emp-detail-actions { display: flex; gap: 8px; margin-top: 8px; }

  .emp-center { min-height: 60vh; display: flex; align-items: center; justify-content: center; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .emp-denied { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; }
  .emp-denied-mark { font-size: 32px; color: #c0392b; }
  .emp-denied-title { font-size: 18px; font-weight: 600; color: var(--pw-accent); }

  /* layout */
  .emp-wrap { display: grid; grid-template-columns: 240px 1fr; min-height: calc(100vh - 56px); }
  .emp-embedded { min-height: 0; }
  .emp-rail {
    background: var(--pw-bg-alt, #f6f2ea);
    border-right: 1px solid var(--pw-border, #e5ddcf);
    padding: 0 8px 120px;
    font-family: inherit;
    align-self: stretch;
    overflow-y: auto;
  }
  .emp-rg { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
  .emp-rg-label { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); padding: 12px 14px 6px; font-weight: 600; }
  .emp-rg-item {
    display: flex; align-items: center; gap: 10px; width: 100%;
    background: transparent; border: none; cursor: pointer;
    padding: 8px 12px; font-family: inherit; font-size: 12px; line-height: 1.3;
    color: var(--pw-ink, #2c2a26); text-align: left; border-radius: 0;
    border-left: 2px solid transparent;
  }
  .emp-rg-item:hover { background: rgba(201, 99, 66, 0.04); }
  .emp-rg-on { background: rgba(201, 99, 66, 0.08); color: var(--pw-accent); font-weight: 600; }
  .emp-rg-icon { width: 14px; height: 14px; flex: 0 0 auto; color: var(--pw-muted, #877f74); }
  .emp-rg-on .emp-rg-icon { color: var(--pw-accent); }
  .emp-rg-text { flex: 1; }

  .emp-main { padding: 28px 48px 80px; max-width: 1340px; margin: 0 auto; width: 100%; font-family: inherit; box-sizing: border-box; }
  .emp-pagehead { margin-bottom: 22px; }
  .emp-pagetitle { font-family: var(--pw-serif, Georgia, serif); font-size: 26px; font-weight: 600; color: var(--pw-ink, #2c2a26); margin: 0 0 4px; }
  .emp-pagesub { color: var(--pw-muted, #877f74); font-size: 13px; margin: 0; }

  .emp-panel { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e5ddcf); border-radius: 0; padding: 16px 18px; margin-bottom: 16px; }
  .emp-h { color: var(--pw-accent); font-weight: 700; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px; }
  .emp-h-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; gap: 10px; }
  .emp-subh { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-muted, #877f74); }
  .emp-mt { margin-top: 14px; }

  .emp-status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; font-size: 13px; }
  .emp-status-grid > div { display: flex; align-items: center; gap: 8px; }
  .emp-k { display: inline-block; min-width: 88px; color: var(--pw-muted, #877f74); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }

  .emp-dot { font-size: 11px; }
  .emp-on { color: #2d8a4e; }
  .emp-off { color: #c0392b; }
  .emp-amber { color: #c08a00; }
  .emp-open { color: var(--pw-muted, #877f74); }

  .emp-code { background: #f3ece1; color: #9a4a2f; border: 1px solid #e5ddcf; padding: 1px 6px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; border-radius: 3px; }
  .emp-code-key { display: inline-block; flex: 1; }

  .emp-method { display: inline-block; background: #2d8a4e; color: #fff; font-size: 10px; font-weight: 700; padding: 2px 6px; letter-spacing: 0.04em; }
  .emp-method.emp-post { background: var(--pw-accent); }

  .emp-btnrow { display: flex; gap: 8px; flex-wrap: wrap; }
  .emp-btn { background: transparent; color: var(--pw-ink-soft, #4a4438); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 6px 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; cursor: pointer; border-radius: 0; }
  .emp-btn:hover { border-color: var(--pw-accent); color: var(--pw-accent); }
  .emp-btn:disabled { opacity: 0.5; cursor: default; }
  .emp-btn-sm { padding: 3px 10px; font-size: 11px; }
  .emp-btn-accent { background: var(--pw-accent); color: #fff; border-color: var(--pw-accent); }
  .emp-btn-accent:hover { background: var(--pw-accent-strong, #b8553a); color: #fff; }
  .emp-btn-danger { color: #c0392b; border-color: #c0392b; }
  .emp-btn-danger:hover { background: #c0392b; color: #fff; }

  .emp-mint { border: 1px dashed var(--pw-border-strong, #cdc6b8); padding: 14px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 12px; }
  .emp-mint-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .emp-field { display: flex; flex-direction: column; gap: 5px; }
  .emp-field-full { grid-column: 1 / -1; }
  .emp-flabel { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted, #877f74); }
  .emp-radio { display: flex; gap: 16px; font-size: 12px; align-items: center; }
  .emp-radio label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
  .emp-mint-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

  .emp-input { background: var(--pw-bg-alt, #f6f2ea); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 6px 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--pw-ink, #2c2a26); border-radius: 0; outline: none; }
  .emp-input:focus { border-color: var(--pw-accent); }
  .emp-select { padding: 6px 10px; appearance: none; cursor: pointer; }

  .emp-picker { display: flex; gap: 8px; align-items: center; }
  .emp-picker .emp-input { flex: 1; }
  .emp-choices { display: flex; flex-wrap: wrap; gap: 5px; max-height: 120px; overflow-y: auto; padding: 4px; background: var(--pw-bg-alt, #f6f2ea); }
  .emp-choice { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 3px 9px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; cursor: pointer; border-radius: 0; }
  .emp-choice:hover { border-color: var(--pw-accent); color: var(--pw-accent); }

  .emp-chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .emp-chip { display: inline-flex; align-items: center; gap: 4px; background: var(--pw-accent); color: #fff; padding: 3px 4px 3px 9px; font-size: 11px; }
  .emp-chip-x { background: rgba(255,255,255,0.2); border: none; color: #fff; cursor: pointer; padding: 0 5px; font-size: 12px; line-height: 1; }
  .emp-chip-x:hover { background: rgba(255,255,255,0.4); }

  .emp-minted { display: flex; flex-direction: column; gap: 6px; }
  .emp-minted-row { display: flex; gap: 8px; align-items: center; }

  .emp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .emp-table th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-muted, #877f74); padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #e5ddcf); font-weight: 600; }
  .emp-table td { padding: 7px 8px; border-bottom: 1px solid var(--pw-border, #ece6d9); vertical-align: middle; }
  .emp-badge { font-size: 11px; }
  .emp-badge-on { color: #2d8a4e; }
  .emp-badge-off { color: #c0392b; }

  /* config */
  .emp-config-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .emp-color-row { display: flex; align-items: center; gap: 8px; }
  .emp-input-color { width: 44px; height: 32px; padding: 2px; cursor: pointer; }
  .emp-input-hex { width: 90px; }

  /* preview */
  .emp-preview-wrap { display: flex; gap: 24px; align-items: flex-start; }
  .emp-preview-canvas {
    position: relative; width: 260px; height: 160px; min-height: 160px;
    background: var(--pw-bg-alt, #f6f2ea); border: 1px solid var(--pw-border, #e5ddcf);
    flex-shrink: 0;
  }
  .emp-preview-bubble {
    position: absolute; bottom: 16px; width: 44px; height: 44px;
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.18); cursor: pointer;
  }
  .emp-preview-logo { width: 22px; height: 22px; object-fit: contain; border-radius: 50%; }
  .emp-preview-welcome {
    position: absolute; bottom: 70px; right: 16px; left: 16px;
    background: var(--pw-surface, #fff); border: 1px solid; border-radius: 0;
    padding: 8px 10px; font-size: 11px; color: var(--pw-ink, #2c2a26);
    box-shadow: 0 1px 6px rgba(0,0,0,0.1);
  }
  .emp-preview-meta { font-size: 11px; display: flex; flex-direction: column; gap: 4px; }

  /* usage */
  .emp-empty-state { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 24px 0; text-align: center; }
  .emp-empty-icon { font-size: 22px; }

  /* tiers */
  .emp-tiers { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; font-size: 13px; }
  .emp-tier { display: flex; align-items: center; gap: 8px; }

  /* pills */
  .emp-pills { display: flex; gap: 4px; }
  .emp-pill { background: transparent; border: 1px solid var(--pw-border-strong, #cdc6b8); padding: 3px 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; cursor: pointer; border-radius: 0; color: var(--pw-ink-soft, #4a4438); }
  .emp-pill-on { background: var(--pw-accent); color: #fff; border-color: var(--pw-accent); }

  /* misc */
  .emp-row { font-size: 13px; padding: 4px 0; }
  .emp-muted { color: var(--pw-muted, #877f74); }
  .emp-err { color: #c0392b; font-size: 12px; }
  .emp-warn { color: #c08a00; font-size: 11px; }
  .emp-saved { color: #2d8a4e; font-size: 12px; }
  .emp-fineprint { font-size: 11px; margin-top: 8px; }
  .emp-doc-p { margin: 0 0 10px; line-height: 1.55; color: var(--pw-ink, #2c2a26); font-size: 13px; }

  .emp-codeblock { position: relative; margin: 8px 0 4px; }
  .emp-pre { background: #1a1614; color: #e8e3d6; padding: 14px 16px; margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.55; overflow-x: auto; white-space: pre; border-radius: 0; }
  .emp-copybtn { position: absolute; top: 8px; right: 8px; background: rgba(255,255,255,0.08); color: #e8e3d6; border: 1px solid rgba(255,255,255,0.18); padding: 3px 10px; font-size: 11px; cursor: pointer; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; border-radius: 0; }
  .emp-copybtn:hover { border-color: var(--pw-accent); color: #fff; }

  /* logo upload row */
  .emp-logo-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .emp-logo-url { flex: 1 1 220px; min-width: 160px; }
  .emp-logo-file { display: none; }
  .emp-logo-preview { width: 34px; height: 34px; object-fit: contain; border: 1px solid var(--pw-border, #e5ddcf); border-radius: 4px; background: #fff; flex-shrink: 0; }
  .emp-logo-empty { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; font-size: 8px; color: #b0a898; border: 1px dashed var(--pw-border, #e5ddcf); border-radius: 4px; flex-shrink: 0; text-align: center; line-height: 1; }
  .emp-logo-err { display: block; margin-top: 6px; }

  /* downloadable SDK list */
  .emp-h-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
  .emp-h-row .emp-h { margin-bottom: 0; }
  .emp-sdk-list { display: flex; flex-direction: column; border: 1px solid var(--pw-border, #e5ddcf); }
  .emp-sdk-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 9px 12px; border-bottom: 1px solid var(--pw-border, #e5ddcf); }
  .emp-sdk-row:last-child { border-bottom: none; }
  .emp-sdk-meta { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; min-width: 0; }
  .emp-sdk-name { background: #f3ece1; color: #9a4a2f; border: 1px solid #e5ddcf; padding: 2px 8px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; border-radius: 3px; }
  .emp-sdk-lang { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-accent); border: 1px solid var(--pw-accent); padding: 1px 6px; }
  .emp-sdk-desc { font-size: 12px; }
  .emp-sdk-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .emp-sdk-preview { margin: 0; border-top: 1px solid var(--pw-border, #e5ddcf); }
  .emp-sdk-preview .emp-pre { max-height: 360px; overflow-y: auto; }

  @media (max-width: 760px) {
    .emp-wrap { grid-template-columns: 1fr; }
    .emp-rail { border-right: none; border-bottom: 1px solid var(--pw-border, #e5ddcf); }
    .emp-main { padding: 20px; }
    .emp-status-grid, .emp-mint-grid, .emp-config-grid { grid-template-columns: 1fr; }
    .emp-preview-wrap { flex-direction: column; }
  }

  /* ── status banner ── */
  .emp-statusbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
  .emp-status-warn { color: #8a5a00; }
  .emp-warn-text { color: #c0392b; }
  .emp-req { color: #c0392b; font-weight: 700; }
  .emp-sb-global { display: flex; align-items: center; gap: 6px; margin-top: 8px; cursor: pointer; }
  .emp-sb-global input { margin: 0; }
  .emp-ml-auto { margin-left: auto; }
  .emp-ml-8 { margin-left: 8px; }
  .emp-link { color: var(--pw-accent, #c96342); text-decoration: none; }
  .emp-link:hover { text-decoration: underline; }

  /* ── path tabs ── */
  .emp-pathtabs { display: inline-flex; border: 1px solid var(--pw-border, #e5ddcf); margin-bottom: 8px; }
  .emp-pathtab { padding: 6px 14px; font-size: 12px; font-weight: 600; background: #fff; color: #6b6557; border: none; border-right: 1px solid var(--pw-border, #e5ddcf); cursor: pointer; }
  .emp-pathtab:last-child { border-right: none; }
  .emp-pt-on { background: var(--pw-accent, #c96342); color: #fff; }
  .emp-pathdesc { font-size: 12px; margin-bottom: 8px; }

  /* ── sandbox chat ── */
  .emp-sb-chat { background: #faf8f1; border: 1px solid var(--pw-border, #e5ddcf); padding: 10px; min-height: 140px; max-height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
  .emp-sb-empty { font-style: italic; }
  .emp-sb-row { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
  .emp-sb-who { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: #8a8478; flex-shrink: 0; }
  .emp-sb-you .emp-sb-text { color: #2c2a26; }
  .emp-sb-bot .emp-sb-text { color: #1a1614; }
  .emp-sb-text { flex: 1; min-width: 0; }
  .emp-sb-ms { font-size: 11px; color: #3a7563; flex-shrink: 0; }
  .emp-sb-input { display: flex; gap: 8px; margin-top: 8px; }
  .emp-sb-input .emp-input { flex: 1; }
  .emp-sb-controls { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
  .emp-field { display: flex; flex-direction: column; gap: 3px; font-size: 11px; color: #6b6557; text-transform: uppercase; letter-spacing: 0.04em; }
  .emp-sel { min-width: 120px; }
  .emp-pre-wrap { white-space: pre-wrap; word-break: break-all; }

  /* ── playground 2-col ── */
  .emp-pg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 980px) { .emp-pg-grid { grid-template-columns: 1fr; } }
  .emp-pg-col { margin: 0; }

  /* ── chat bubble card ── */
  .emp-chat-card { border: 1px solid var(--pw-border, #e5ddcf); border-radius: 12px; overflow: hidden; background: #fff; display: flex; flex-direction: column; height: 440px; box-shadow: 0 6px 24px rgba(0,0,0,.06); }
  .emp-chat-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; color: #fff; }
  .emp-chat-av { width: 30px; height: 30px; border-radius: 50%; background: rgba(255,255,255,.25); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; flex-shrink: 0; }
  .emp-chat-htext { display: flex; flex-direction: column; line-height: 1.2; }
  .emp-chat-htext strong { font-size: 14px; }
  .emp-chat-status { font-size: 11px; opacity: .85; }
  .emp-chat-body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: #faf8f1; }
  .emp-msg { display: flex; gap: 8px; align-items: flex-end; max-width: 100%; }
  .emp-msg-you { justify-content: flex-end; }
  .emp-msg-av { width: 24px; height: 24px; border-radius: 50%; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .emp-bubble { padding: 8px 12px; border-radius: 14px; font-size: 13px; line-height: 1.5; max-width: 78%; word-wrap: break-word; }
  .emp-bubble-bot { background: #fff; border: 1px solid var(--pw-border, #e5ddcf); color: #2c2a26; border-bottom-left-radius: 4px; }
  .emp-bubble-you { color: #fff; border-bottom-right-radius: 4px; }
  .emp-bubble :global(ul) { margin: 4px 0; padding-left: 18px; }
  .emp-bubble :global(li) { margin: 2px 0; }
  .emp-bubble :global(p) { margin: 4px 0; }
  .emp-bubble :global(strong) { font-weight: 700; }
  .emp-bubble :global(code) { background: #efeadd; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  .emp-bubble-ms { display: block; font-size: 10px; color: #3a7563; margin-top: 4px; }
  .emp-typing { display: inline-flex; gap: 4px; align-items: center; }
  .emp-typing span { width: 6px; height: 6px; border-radius: 50%; background: #b8b2a4; animation: emp-blink 1.2s infinite; }
  .emp-typing span:nth-child(2) { animation-delay: .2s; }
  .emp-typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes emp-blink { 0%,80%,100% { opacity: .3; } 40% { opacity: 1; } }
  .emp-chat-foot { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--pw-border, #e5ddcf); background: #fff; }
  .emp-chat-input { flex: 1; border: 1px solid var(--pw-border, #e5ddcf); border-radius: 20px; padding: 8px 14px; font-size: 13px; font-family: inherit; outline: none; }
  .emp-chat-input:focus { border-color: var(--pw-accent, #c96342); }
  .emp-chat-send { width: 36px; height: 36px; border-radius: 50%; border: none; color: #fff; font-size: 16px; cursor: pointer; flex-shrink: 0; }
  .emp-chat-send:disabled { opacity: .5; cursor: default; }

  /* ---- monitoring dashboard ---- */
  .emp-an-live { color: #3a7563; font-size: 10px; font-weight: 700; letter-spacing: .05em; margin-left: 8px; }
  .emp-mon-filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
  .emp-mon-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 900px) { .emp-mon-2col { grid-template-columns: 1fr; } }

  .emp-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
  .emp-kpi { border: 1px solid var(--pw-border, #e5ddcf); border-radius: 8px; padding: 10px 12px; background: #fdfbf6; }
  .emp-kpi-warn { border-color: #d98b6a; background: #fbf2ec; }
  .emp-kpi-label { font-size: 10px; letter-spacing: .06em; text-transform: uppercase; color: var(--pw-muted, #877f74); }
  .emp-kpi-val { font-size: 20px; font-weight: 700; color: var(--pw-ink, #2a2620); margin-top: 2px; font-family: var(--pw-font-body); }
  .emp-kpi-sub { font-size: 10px; color: var(--pw-muted, #877f74); margin-top: 2px; }

  .emp-chart { display: flex; align-items: flex-end; gap: 3px; height: 140px; padding-top: 8px; }
  .emp-chart-col { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; height: 100%; }
  .emp-chart-barwrap { flex: 1; width: 100%; display: flex; align-items: flex-end; justify-content: center; }
  .emp-chart-bar { width: 70%; min-height: 2px; background: var(--pw-accent, #c96342); border-radius: 2px 2px 0 0; transition: height .2s; }
  .emp-chart-x { font-size: 9px; color: var(--pw-muted, #877f74); margin-top: 4px; white-space: nowrap; transform: rotate(-30deg); transform-origin: top center; }

  .emp-lat-pcts { display: flex; gap: 16px; font-size: 12px; margin-bottom: 8px; }
  .emp-lat-pcts .emp-k { min-width: 0; margin-right: 4px; }
  .emp-hbars { display: flex; flex-direction: column; gap: 6px; }
  .emp-hbar-row { display: grid; grid-template-columns: 56px 1fr 78px; align-items: center; gap: 8px; font-size: 12px; }
  .emp-hbar-lbl { color: var(--pw-ink-soft, #4a4438); }
  .emp-hbar-track { height: 10px; background: #efeadd; border-radius: 5px; overflow: hidden; }
  .emp-hbar-fill { height: 100%; background: var(--pw-accent, #c96342); border-radius: 5px; }
  .emp-hbar-fill-tail { background: #d98b6a; }
  .emp-hbar-tail .emp-hbar-lbl { color: #9a4a2f; }
  .emp-hbar-val { text-align: right; color: var(--pw-ink-soft, #4a4438); }

  /* ---- clickable rows + widget detail screen ---- */
  .emp-scope-badge { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: rgba(201,99,66,0.1); color: var(--pw-accent, #c96342); font-weight: 600; }
  .emp-table-click tbody tr.emp-mon-row { cursor: pointer; }
  .emp-table-click tbody tr.emp-mon-row:hover { background: rgba(201,99,66,0.05); }
  .emp-mon-chev { text-align: right; color: var(--pw-muted, #877f74); font-size: 15px; width: 20px; }

  .emp-wd-headpanel { padding: 12px 16px; }
  .emp-wd-headbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .emp-wd-headmeta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; flex: 1; min-width: 0; }
  .emp-wd-name { font-family: var(--pw-serif, Georgia, serif); font-size: 16px; color: var(--pw-ink, #2c2a26); }
  .emp-wd-headact { display: flex; align-items: center; gap: 10px; }
  .emp-wd-notice { font-size: 12px; color: #9a4a2f; background: rgba(201,99,66,0.06); padding: 6px 10px; border-radius: 4px; margin-bottom: 8px; }
  .emp-wd-ok { color: #4a7c59; }
  .emp-wd-calldetail > td { background: #faf7f1; padding: 0 !important; }
  .emp-wd-callfull { padding: 14px 16px; }
  .emp-wd-qlabel { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--pw-muted, #877f74); font-weight: 600; margin-bottom: 3px; }
  .emp-wd-qtext { font-size: 13px; line-height: 1.5; color: var(--pw-ink, #2c2a26); white-space: pre-wrap; word-break: break-word; }
  .emp-wd-qmeta { display: flex; align-items: center; gap: 6px 14px; flex-wrap: wrap; font-size: 12px; color: var(--pw-ink-soft, #4a4438); }
  .emp-wd-qmeta .emp-k { min-width: 0; margin-right: 2px; }
</style>
