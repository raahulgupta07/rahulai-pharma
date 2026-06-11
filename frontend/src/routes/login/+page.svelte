<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount, onDestroy } from 'svelte';
 import { get } from 'svelte/store';
 import { brand } from '$lib/stores/branding';

 let username = $state('');
 let password = $state('');
 let showPassword = $state(false);
 let rememberMe = $state(true);
 let error = $state('');
 let loading = $state(false);

 // Federated auth methods (LDAP / OIDC) — driven by GET /api/auth/methods
 type AuthMethods = { local: boolean; ldap: boolean; ldap_label?: string; oidc: { id: string; name: string }[]; trusted_header?: boolean };
 let methods = $state<AuthMethods>({ local: true, ldap: false, oidc: [] });

 onMount(() => {
 try {
 const last = localStorage.getItem('dash_last_username') || '';
 if (last) username = last;
 } catch {}
 // SSO callback handoff: backend set a short-lived `dash_sso` cookie + sent us
 // back with ?sso=1 (token never travels in the URL). Move it to localStorage.
 try {
 const qs = new URLSearchParams(window.location.search);
 if (qs.get('sso_error')) {
 error = qs.get('sso_error') === 'not_authorized'
 ? 'Your account is not authorized for this app.'
 : 'SSO sign-in failed. Try again or contact admin.';
 }
 if (qs.get('sso') === '1') {
 const m = document.cookie.match(/(?:^|;\s*)dash_sso=([^;]+)/);
 if (m) {
 const tok = decodeURIComponent(m[1]);
 document.cookie = 'dash_sso=; Max-Age=0; path=/';
 localStorage.setItem('dash_token', tok);
 window.location.href = '/ui/home';
 return;
 }
 }
 } catch {}
 // Discover enabled methods (fail-soft → local only)
 fetch('/api/auth/methods')
 .then((r) => (r.ok ? r.json() : null))
 .then((d) => { if (d) methods = d; })
 .catch(() => {});
 });

 async function ldapLogin() {
 if (!username || !password) { error = 'Enter your directory username and password first.'; return; }
 loading = true; error = '';
 try {
 const res = await fetch('/api/auth/ldap/login', {
 method: 'POST', headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ username, password }),
 });
 const data = await res.json().catch(() => ({}));
 if (!res.ok) { error = data?.detail || 'LDAP sign-in failed.'; loading = false; return; }
 localStorage.setItem('dash_token', data.token);
 localStorage.setItem('dash_user', data.username);
 try { if (rememberMe) localStorage.setItem('dash_last_username', data.username); } catch {}
 window.location.href = '/ui/home';
 } catch (e: any) { error = e?.message || 'Connection failed.'; loading = false; }
 }

 function oidcLogin(id: string) {
 window.location.href = `/api/auth/oidc/${encodeURIComponent(id)}/login`;
 }

 // Time-aware greeting (refreshes if user idles past hour boundary)
 let now = $state(new Date());
 let greeting = $derived.by(() => {
 const h = now.getHours();
 if (h >= 5 && h < 12) return 'Good morning';
 if (h >= 12 && h < 17) return 'Good afternoon';
 if (h >= 17 && h < 23) return 'Good evening';
 return 'Working late';
 });

 // Live tenant counter (graceful fallback if /api/health 4xx/5xx)
 type LiveStats = { teams: string; rows: string; uptime: string };
 let liveStats = $state<LiveStats | null>(null);

 function fmtNum(n: number): string {
 if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
 if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
 return String(n);
 }

 async function loadStats() {
 try {
 const res = await fetch('/api/health', { headers: { 'Accept': 'application/json' } });
 if (!res.ok) { liveStats = null; return; }
 const d = await res.json().catch(() => ({}));
 // Try common shapes; fall back to plausible defaults if missing
 // CityPharma single-agent: show pharma demo facts, not generic SaaS metrics.
 liveStats = { teams: '53', rows: '106K', uptime: '4,892' };
 return;
 } catch {
 liveStats = null;
 }
 }

 // Build version + data freshness + What's-new feed (GET /api/version, public)
 type Release = { version: string; date?: string; title?: string; items: string[] };
 type VersionInfo = {
   version: string; commit?: string; built_at?: string | null;
   image_age_hours?: number | null; stale?: boolean;
   data?: { last_upload?: string | null; catalog_rows?: number | null;
            stock_rows?: number | null;
            shop_flat?: { both: number; catalog_only: number; stock_only: number } | null } | null;
   changelog?: Release[];
 };
 let versionInfo = $state<VersionInfo | null>(null);
 let showWhatsNew = $state(false);

 let shortCommit = $derived(versionInfo?.commit && versionInfo.commit !== 'unknown'
   ? versionInfo.commit.slice(0, 7) : '');
 let builtLabel = $derived.by(() => {
   const iso = versionInfo?.built_at;
   if (!iso) return '';
   try { return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
   catch { return ''; }
 });
 let latestRelease = $derived(versionInfo?.changelog?.[0] ?? null);

 async function loadVersion() {
   try {
     const res = await fetch('/api/version', { headers: { Accept: 'application/json' } });
     if (!res.ok) { versionInfo = null; return; }
     versionInfo = await res.json();
   } catch { versionInfo = null; }
 }

 let _statsTimer: any = null;
 let _clockTimer: any = null;

 onMount(() => {
 document.body.classList.add('pw-login-active');
 loadStats();
 loadVersion();
 _statsTimer = setInterval(loadStats, 30_000);
 _clockTimer = setInterval(() => { now = new Date(); }, 60_000);
 return () => { document.body.classList.remove('pw-login-active'); };
 });

 onDestroy(() => {
 if (_statsTimer) clearInterval(_statsTimer);
 if (_clockTimer) clearInterval(_clockTimer);
 });

 function handleKey(e: KeyboardEvent) {
 if (e.key === 'Enter') login();
 }

 async function login() {
 if (!username || !password) { error = 'Both fields required.'; return; }
 loading = true; error = '';
 try {
 const res = await fetch('/api/auth/login', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ username, password }),
 });
 const data = await res.json().catch(() => ({}));
 if (!res.ok) { error = data?.detail || 'Authentication failed.'; loading = false; return; }
 localStorage.setItem('dash_token', data.token);
 localStorage.setItem('dash_user', data.username);
 if (rememberMe) {
 try { localStorage.setItem('dash_last_username', data.username); } catch {}
 } else {
 try { localStorage.removeItem('dash_last_username'); } catch {}
 }
 if (data.default_project_slug) {
 const slug = data.default_project_slug;
 const next = `/ui/project/${slug}`;
 window.location.href = `/ui/scope-picker?project_slug=${encodeURIComponent(slug)}&next=${encodeURIComponent(next)}`;
 return;
 }
 window.location.href = '/ui/home';
 } catch (e: any) {
 error = e?.message || 'Connection failed.';
 loading = false;
 }
 }
</script>

<svelte:head>
  <title>Sign in — {$brand.name}</title>
</svelte:head>

<div class="pw-login">

  <header class="pw-top">
    <div class="pw-brand">
      <img src="/brand/cityagent.png?v=3" alt="CityPharma" class="pw-brand-logo" />
    </div>
    {#if versionInfo}
      <button class="pw-ver" class:pw-ver--stale={versionInfo.stale}
              type="button" onclick={() => showWhatsNew = !showWhatsNew}
              title={versionInfo.stale ? 'Dev / stale build — rebuild to deploy latest' : 'View what\'s new'}>
        <span class="pw-ver-dot"></span>
        <span class="pw-ver-v">v{versionInfo.version}</span>
        {#if shortCommit}<span class="pw-ver-sep">·</span><span class="pw-ver-c">{shortCommit}</span>{/if}
        {#if builtLabel}<span class="pw-ver-sep">·</span><span class="pw-ver-d">{builtLabel}</span>{/if}
        {#if versionInfo.stale}<span class="pw-ver-warn">⚠ stale</span>{/if}
      </button>
    {/if}
  </header>

  <main class="pw-split">

    <section class="pw-left">
      <h1 class="pw-hero">
        {greeting},<br/>sign in to {'CityAgent Pharma'}
      </h1>
      <p class="pw-sub">
        Your pharmacy intelligence — stock, substitutes, and shelf insights at the counter.
      </p>

      {#if liveStats}
        <div class="pw-stats">
          <span class="pw-stats-dot"></span>
          {liveStats.teams} branches · {liveStats.rows} stock rows · {liveStats.uptime} SKUs{#if versionInfo?.data?.last_upload} · data {versionInfo.data.last_upload}{/if}
        </div>
      {/if}

      <div class="pw-card">
        {#each methods.oidc as p}
          <button class="pw-sso" type="button" onclick={() => oidcLogin(p.id)}>
            <span class="pw-sso-icon">◌</span> Continue with {p.name}
          </button>
        {/each}
        {#if methods.ldap}
          <button class="pw-sso" type="button" onclick={ldapLogin} disabled={loading}>
            <span class="pw-sso-icon"></span> Continue with {methods.ldap_label || 'LDAP / Active Directory'}
          </button>
        {/if}

        {#if (methods.oidc.length || methods.ldap) && methods.local}
          <div class="pw-divider"><span>or</span></div>
        {/if}

        {#if methods.local}
        <form method="post" action="/api/auth/login" onsubmit={(e) => { e.preventDefault(); login(); }}>
          <div class="pw-field">
            <input class="pw-input" type="text" name="username" id="username" bind:value={username}
                   placeholder="User ID or email" autocomplete="username" autocapitalize="off" spellcheck="false" />
          </div>

          <div class="pw-field">
            <div class="pw-input-wrap">
              <input class="pw-input" type={showPassword ? 'text' : 'password'}
                     name="password" id="password"
                     bind:value={password} placeholder="Password"
                     autocomplete="current-password"
                     style="padding-right: 64px;" />
              <button type="button" class="pw-show" onclick={() => showPassword = !showPassword}
                      aria-label={showPassword ? 'Hide password' : 'Show password'}>
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <label class="pw-remember">
            <input type="checkbox" bind:checked={rememberMe} />
            <span>Remember me on this device</span>
          </label>

          {#if error}
            <div class="pw-error" role="alert">{error}</div>
          {/if}

          <button class="pw-cta" type="submit" disabled={loading}>
            {loading ? 'Signing in…' : 'Continue with email'}
          </button>
        </form>
        {/if}
        {#if error && !methods.local}
          <div class="pw-error" role="alert">{error}</div>
        {/if}
      </div>
    </section>

    <aside class="pw-right">
      <div class="pw-canvas">
        <div class="pw-grid-bg"></div>

        <div class="pw-tiles">
          <div class="pw-tile" data-tile="1">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v6c0 1.66 4 3 9 3s9-1.34 9-3V5"/><path d="M3 11v6c0 1.66 4 3 9 3s9-1.34 9-3v-6"/></svg></span>
            Check stock
          </div>
          <div class="pw-tile pw-tile--morph" data-tile="2">
            <span class="pw-tile-icon pw-morph">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="pw-morph-svg pw-morph-bar">
                <line x1="12" y1="20" x2="12" y2="10"/>
                <line x1="18" y1="20" x2="18" y2="4"/>
                <line x1="6" y1="20" x2="6" y2="14"/>
                <line x1="3" y1="20" x2="21" y2="20"/>
              </svg>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="pw-morph-svg pw-morph-line">
                <polyline points="3 17 9 11 13 15 21 5"/>
                <polyline points="14 5 21 5 21 12" opacity="0.4"/>
              </svg>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="pw-morph-svg pw-morph-pie">
                <path d="M12 2a10 10 0 1 0 10 10h-10z"/>
                <path d="M12 2v10h10A10 10 0 0 0 12 2z" opacity="0.5"/>
              </svg>
            </span>
            Find substitute
          </div>
          <div class="pw-tile" data-tile="3">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 0-4 4v.5A3.5 3.5 0 0 0 5 10c0 1.5.8 2.8 2 3.5V15a3 3 0 0 0 3 3h.5"/><path d="M12 2a4 4 0 0 1 4 4v.5A3.5 3.5 0 0 1 19 10c0 1.5-.8 2.8-2 3.5V15a3 3 0 0 1-3 3h-.5"/><path d="M10 21h4"/><path d="M12 18v3"/></svg></span>
            Branch transfer
          </div>
          <div class="pw-tile" data-tile="4">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg></span>
            Salt lookup
          </div>
          <div class="pw-tile" data-tile="5">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.7.5 1.3 1.3 1.5 2.3h5c.2-1 .8-1.8 1.5-2.3A7 7 0 0 0 12 2z"/></svg></span>
            Ask brain
          </div>
          <div class="pw-tile" data-tile="6">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18.4a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></span>
            Low stock
          </div>
          <div class="pw-tile" data-tile="7">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M10 6.5h4M17.5 10v4M14 17.5h-4M6.5 14v-4"/></svg></span>
            Reorder list
          </div>
          <div class="pw-tile" data-tile="8">
            <span class="pw-tile-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="14" x2="15" y2="14"/><line x1="9" y1="18" x2="13" y2="18"/></svg></span>
            Export report
          </div>
        </div>

        <div class="pw-cursor"></div>

        <div class="pw-bubble pw-bubble-1">"Is paracetamol in stock at my branch?"</div>
        <div class="pw-bubble pw-bubble-2"><Icon name="check" size={14} /> BIOGESIC · 168 in stock</div>
        <div class="pw-bubble pw-bubble-3"><Icon name="brain" size={14} /> 41,042 substitute links</div>

        <div class="pw-dock">
          <button class="pw-dock-folder" type="button"><Icon name="flask" size={14} /> CityAgent Pharma · Counter assistant</button>
          <button class="pw-dock-add" type="button">+</button>
          <button class="pw-dock-go" type="button">Let's go →</button>
        </div>
      </div>
    </aside>

  </main>

  {#if latestRelease}
    <section class="pw-whatsnew" class:pw-whatsnew--open={showWhatsNew}>
      <button class="pw-wn-head" type="button" onclick={() => showWhatsNew = !showWhatsNew}>
        <span class="pw-wn-spark">✦</span>
        <span class="pw-wn-title">
          What's new {#if versionInfo?.version}<span class="pw-wn-ver">in v{versionInfo.version}</span>{/if}
          {#if latestRelease.title} — {latestRelease.title}{/if}
        </span>
        <span class="pw-wn-toggle">{showWhatsNew ? 'Hide' : 'See all ▸'}</span>
      </button>

      {#if !showWhatsNew}
        <ul class="pw-wn-list">
          {#each latestRelease.items.slice(0, 3) as it}
            <li>{it}</li>
          {/each}
        </ul>
      {:else}
        <div class="pw-wn-all">
          {#each (versionInfo?.changelog ?? []) as rel}
            <div class="pw-wn-rel">
              <div class="pw-wn-relhead">
                <span class="pw-wn-relver">v{rel.version}</span>
                {#if rel.title}<span class="pw-wn-reltitle">{rel.title}</span>{/if}
                {#if rel.date}<span class="pw-wn-reldate">{rel.date}</span>{/if}
              </div>
              <ul class="pw-wn-list">
                {#each rel.items as it}<li>{it}</li>{/each}
              </ul>
            </div>
          {/each}
        </div>
      {/if}
    </section>
  {/if}

  <footer class="pw-footer">
    © 2026 CityAgent Pharma{#if versionInfo} · v{versionInfo.version}{#if shortCommit} ({shortCommit}){/if}{/if}
  </footer>
</div>

<style>
 :global(body.pw-login-active) {
 background: var(--pw-bg) !important;
 color-scheme: light !important;
 font-family: var(--pw-font-body);
 }
 :global(body.pw-login-active main) {
 background: transparent !important;
 }

 .pw-login {
 height: 100vh;
 max-height: 100vh;
 display: flex;
 flex-direction: column;
 overflow: hidden;
 background: var(--pw-bg);
 color: var(--pw-ink);
 font-family: var(--pw-font-body), 'Inter', system-ui, -apple-system, sans-serif;
 font-size: 14.5px;
 line-height: 1.55;
 -webkit-font-smoothing: antialiased;
 -moz-osx-font-smoothing: grayscale;
 }
 :global(html), :global(body) { overflow: hidden; height: 100%; }
 .pw-split { flex: 1; min-height: 0; overflow: hidden; }

 .pw-top {
 padding: 8px 16px 0 12px;
 display: flex;
 align-items: center;
 justify-content: space-between;
 gap: 12px;
 }

 /* version chip (top-right) — warm pill, matches inline-code theme */
 .pw-ver {
 display: inline-flex;
 align-items: center;
 gap: 6px;
 padding: 5px 11px;
 border-radius: 999px;
 background: var(--pw-accent-bg, #f3ece1);
 color: var(--pw-accent, #9a4a2f);
 border: 1px solid var(--pw-accent-soft, rgba(154,74,47,.18));
 font-size: 12px;
 font-weight: 600;
 letter-spacing: .2px;
 cursor: pointer;
 transition: box-shadow .15s, transform .15s;
 white-space: nowrap;
 }
 .pw-ver:hover { box-shadow: 0 0 0 3px var(--pw-accent-bg, #f3ece1); transform: translateY(-1px); }
 .pw-ver-dot {
 width: 7px; height: 7px; border-radius: 50%;
 background: #2fa36b; box-shadow: 0 0 0 3px rgba(47,163,107,.18);
 }
 .pw-ver-sep { opacity: .4; }
 .pw-ver-c, .pw-ver-d { font-weight: 500; opacity: .85; }
 .pw-ver--stale {
 background: #fbf0d6; color: #8a5a00; border-color: rgba(180,120,0,.28);
 }
 .pw-ver--stale .pw-ver-dot { background: #d4930e; box-shadow: 0 0 0 3px rgba(212,147,14,.18); }
 .pw-ver-warn { font-weight: 700; }

 /* What's new feed (above footer) */
 .pw-whatsnew {
 max-width: 760px;
 margin: 4px auto 0;
 width: calc(100% - 48px);
 background: var(--pw-bg-alt, #faf6f0);
 border: 1px solid var(--pw-line, rgba(0,0,0,.08));
 border-radius: 14px;
 padding: 12px 16px;
 }
 .pw-wn-head {
 display: flex;
 align-items: center;
 gap: 9px;
 width: 100%;
 background: none;
 border: none;
 padding: 0;
 cursor: pointer;
 text-align: left;
 color: var(--pw-ink, #2a2622);
 font: inherit;
 }
 .pw-wn-spark { color: var(--pw-accent, #9a4a2f); font-size: 14px; }
 .pw-wn-title { font-size: 13.5px; font-weight: 650; flex: 1; }
 .pw-wn-ver { color: var(--pw-accent, #9a4a2f); font-weight: 700; }
 .pw-wn-toggle { font-size: 12px; color: var(--pw-accent, #9a4a2f); font-weight: 600; white-space: nowrap; }
 .pw-wn-list {
 margin: 8px 0 2px;
 padding-left: 20px;
 color: var(--pw-ink-soft, #5a5550);
 font-size: 12.5px;
 line-height: 1.55;
 }
 .pw-wn-list li { margin: 2px 0; }
 .pw-wn-all { margin-top: 10px; display: flex; flex-direction: column; gap: 12px; max-height: 240px; overflow-y: auto; }
 .pw-wn-rel { border-top: 1px dashed var(--pw-line, rgba(0,0,0,.08)); padding-top: 8px; }
 .pw-wn-rel:first-child { border-top: none; padding-top: 0; }
 .pw-wn-relhead { display: flex; align-items: baseline; gap: 8px; }
 .pw-wn-relver { font-weight: 700; color: var(--pw-accent, #9a4a2f); font-size: 12.5px; }
 .pw-wn-reltitle { font-weight: 600; font-size: 12.5px; color: var(--pw-ink, #2a2622); }
 .pw-wn-reldate { margin-left: auto; font-size: 11px; color: var(--pw-dim, #9a948c); }

 .pw-brand {
 display: flex;
 align-items: center;
 gap: 12px;
 }

 .pw-brand-mark { display: none; }
 .pw-brand-logo {
 height: 56px;
 width: auto;
 max-width: 240px;
 object-fit: contain;
 display: block;
 }
 .pw-remember {
 display: flex;
 align-items: center;
 gap: 8px;
 margin: 8px 2px 4px;
 font-size: 13px;
 color: var(--pw-ink-soft, #5a5550);
 cursor: pointer;
 user-select: none;
 }
 .pw-remember input[type="checkbox"] {
 width: 14px;
 height: 14px;
 accent-color: var(--pw-accent);
 cursor: pointer;
 }

 .pw-brand-text {
 font-size: 20px;
 font-weight: 600;
 font-family: var(--pw-font-serif), Georgia, serif;
 color: var(--pw-ink);
 letter-spacing: -0.01em;
 }

 .pw-split {
 flex: 1;
 min-height: 0;
 display: grid;
 grid-template-columns: 1fr 1fr;
 gap: 24px;
 padding: 8px 48px 16px;
 max-width: 1320px;
 margin: 0 auto;
 width: 100%;
 align-items: center;
 box-sizing: border-box;
 overflow: hidden;
 }

 .pw-left {
 display: flex;
 flex-direction: column;
 max-width: 460px;
 margin-left: auto;
 margin-right: 24px;
 width: 100%;
 }

 .pw-hero {
 font-family: var(--pw-font-serif), Georgia, serif;
 font-size: 44px;
 line-height: 1.08;
 font-weight: 500;
 letter-spacing: -0.035em;
 margin: 0 0 16px;
 color: var(--pw-ink);
 }

 .pw-sub {
 font-size: 16px;
 color: var(--pw-muted);
 margin: 0 0 36px;
 max-width: 380px;
 }

 .pw-stats {
 display: flex;
 align-items: center;
 gap: 8px;
 font-family: var(--pw-font-body);
 font-size: 12px;
 font-weight: 500;
 color: var(--pw-muted);
 letter-spacing: 0.01em;
 margin: -24px 0 32px;
 }

 .pw-stats-dot {
 width: 6px;
 height: 6px;
 border-radius: 50%;
 background: var(--pw-success);
 box-shadow: 0 0 0 0 rgba(45, 106, 79, 0.5);
 animation: pw-stats-pulse 2s ease-in-out infinite;
 flex: 0 0 auto;
 }

 @keyframes pw-stats-pulse {
 0%, 100% { box-shadow: 0 0 0 0 rgba(45, 106, 79, 0.5); }
 50% { box-shadow: 0 0 0 6px rgba(45, 106, 79, 0); }
 }

 .pw-card {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 24px;
 box-shadow: var(--pw-shadow-sm);
 }

 .pw-field {
 margin-bottom: 12px;
 }

 .pw-row {
 display: flex;
 align-items: center;
 justify-content: space-between;
 margin-bottom: 6px;
 }

 .pw-label {
 display: block;
 font-size: 12.5px;
 font-weight: 500;
 color: var(--pw-muted);
 letter-spacing: 0.02em;
 margin-bottom: 6px;
 }

 .pw-row .pw-label {
 margin-bottom: 0;
 }

 .pw-input-wrap {
 position: relative;
 }

 .pw-input {
 width: 100%;
 box-sizing: border-box;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border-strong);
 border-radius: 0;
 padding: 12px 14px;
 font-family: inherit;
 font-size: 14.5px;
 color: var(--pw-ink);
 outline: none;
 transition: border-color 0.15s ease, box-shadow 0.15s ease;
 }

 .pw-input::placeholder {
 color: var(--pw-dim);
 }

 .pw-input:focus {
 border-color: var(--pw-accent);
 box-shadow: 0 0 0 3px var(--pw-accent-bg);
 }

 .pw-show {
 position: absolute;
 right: 10px;
 top: 50%;
 transform: translateY(-50%);
 background: transparent;
 border: none;
 font-family: inherit;
 font-size: 12.5px;
 font-weight: 500;
 color: var(--pw-muted);
 cursor: pointer;
 padding: 4px 8px;
 border-radius: 0;
 transition: background 0.15s ease, color 0.15s ease;
 }

 .pw-show:hover {
 background: var(--pw-surface-warm);
 color: var(--pw-ink);
 }

 .pw-cta {
 width: 100%;
 background: #1c1c1c;
 color: #fff;
 border: 1px solid #1c1c1c;
 padding: 13px 16px;
 border-radius: 0;
 font-family: inherit;
 font-size: 14.5px;
 font-weight: 500;
 cursor: pointer;
 margin-top: 4px;
 transition: background 0.15s ease, transform 0.1s ease;
 }

 .pw-cta:hover:not(:disabled) {
 background: #2c2c2c;
 }

 .pw-cta:active:not(:disabled) {
 transform: scale(0.99);
 }

 .pw-cta:disabled {
 opacity: 0.55;
 cursor: not-allowed;
 }

 .pw-divider {
 display: flex;
 align-items: center;
 gap: 12px;
 margin: 14px 0 14px;
 color: var(--pw-dim);
 font-size: 11px;
 text-transform: uppercase;
 letter-spacing: 0.1em;
 }

 .pw-divider::before,
 .pw-divider::after {
 content: '';
 flex: 1;
 height: 1px;
 background: var(--pw-border);
 }

 .pw-sso {
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 gap: 10px;
 background: var(--pw-surface);
 color: var(--pw-ink);
 border: 1px solid var(--pw-border-strong);
 padding: 11px 16px;
 border-radius: 0;
 font-family: inherit;
 font-size: 13.5px;
 font-weight: 500;
 cursor: pointer;
 margin-bottom: 8px;
 transition: background 0.15s ease, border-color 0.15s ease;
 }

 .pw-sso:hover {
 background: var(--pw-surface-warm);
 border-color: var(--pw-dim);
 }

 .pw-sso-icon {
 font-size: 14px;
 width: 18px;
 text-align: center;
 font-weight: 700;
 }

 .pw-error {
 font-size: 13px;
 color: var(--pw-error);
 background: var(--pw-error-soft);
 border: 1px solid #f0c2c2;
 border-radius: 0;
 padding: 9px 12px;
 margin: 6px 0 12px;
 }

 .pw-link-btn {
 background: none;
 border: none;
 cursor: pointer;
 font-family: inherit;
 font-size: 13px;
 color: var(--pw-accent);
 font-weight: 500;
 padding: 4px 8px;
 border-radius: 0;
 }

 .pw-link-btn:hover {
 text-decoration: underline;
 }

 .pw-foot-link {
 margin-top: 14px;
 text-align: center;
 font-size: 13px;
 color: var(--pw-muted);
 }

 .pw-right {
 display: flex;
 align-items: center;
 justify-content: center;
 padding-left: 24px;
 min-height: 540px;
 }

 .pw-canvas {
 position: relative;
 width: 100%;
 max-width: 560px;
 height: 540px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 overflow: hidden;
 box-shadow: var(--pw-shadow-sm);
 }

 .pw-grid-bg {
 position: absolute;
 inset: 0;
 background-image:
 linear-gradient(to right, rgba(0, 0, 0, 0.04) 1px, transparent 1px),
 linear-gradient(to bottom, rgba(0, 0, 0, 0.04) 1px, transparent 1px);
 background-size: 32px 32px;
 pointer-events: none;
 animation: pw-grid-drift 30s linear infinite;
 }

 @keyframes pw-grid-drift {
 to { background-position: 32px 32px; }
 }

 .pw-tiles {
 position: absolute;
 top: 50%;
 left: 50%;
 transform: translate(-50%, -55%);
 display: grid;
 grid-template-columns: repeat(4, 1fr);
 gap: 12px;
 width: 92%;
 max-width: 510px;
 }

 .pw-tile {
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 12px 10px;
 font-size: 11.5px;
 font-weight: 500;
 color: var(--pw-ink);
 display: flex;
 flex-direction: column;
 align-items: flex-start;
 gap: 6px;
 transition: all 0.25s ease;
 box-shadow: var(--pw-shadow-sm);
 animation: pw-tile-rest 12s ease-in-out infinite;
 line-height: 1.2;
 }

 .pw-tile[data-tile="1"] { animation-delay: 0s; }
 .pw-tile[data-tile="2"] { animation-delay: -10.5s; }
 .pw-tile[data-tile="3"] { animation-delay: -9s; }
 .pw-tile[data-tile="4"] { animation-delay: -7.5s; }
 .pw-tile[data-tile="5"] { animation-delay: -6s; }
 .pw-tile[data-tile="6"] { animation-delay: -4.5s; }
 .pw-tile[data-tile="7"] { animation-delay: -3s; }
 .pw-tile[data-tile="8"] { animation-delay: -1.5s; }

 @keyframes pw-tile-rest {
 0%, 9% {
 background: var(--pw-bg-alt);
 border-color: var(--pw-accent);
 transform: scale(1.06);
 box-shadow: 0 4px 14px rgba(201, 99, 66, 0.18);
 color: var(--pw-accent-ink);
 }
 16%, 100% {
 background: var(--pw-surface);
 border-color: var(--pw-border);
 transform: scale(1);
 box-shadow: var(--pw-shadow-sm);
 color: var(--pw-ink);
 }
 }

 .pw-tile-icon {
 display: inline-flex;
 align-items: center;
 justify-content: center;
 width: 22px;
 height: 22px;
 color: var(--pw-ink-soft);
 }

 .pw-tile-icon svg {
 width: 20px;
 height: 20px;
 }

 .pw-cursor {
 position: absolute;
 width: 18px;
 height: 18px;
 pointer-events: none;
 z-index: 10;
 background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%231c1c1c' stroke='white' stroke-width='1.5'><path d='M3 2l6 17 2.5-7 7-2.5L3 2z'/></svg>");
 background-size: contain;
 background-repeat: no-repeat;
 filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.2));
 animation: pw-cursor-tour 12s ease-in-out infinite;
 top: 30%;
 left: 30%;
 }

 @keyframes pw-cursor-tour {
 0% { top: 32%; left: 18%; }
 9% { top: 32%; left: 18%; }
 12% { top: 32%; left: 38%; }
 21% { top: 32%; left: 38%; }
 25% { top: 32%; left: 58%; }
 34% { top: 32%; left: 58%; }
 37% { top: 32%; left: 78%; }
 46% { top: 32%; left: 78%; }
 50% { top: 58%; left: 18%; }
 59% { top: 58%; left: 18%; }
 62% { top: 58%; left: 38%; }
 71% { top: 58%; left: 38%; }
 75% { top: 58%; left: 58%; }
 84% { top: 58%; left: 58%; }
 87% { top: 58%; left: 78%; }
 96% { top: 58%; left: 78%; }
 100% { top: 32%; left: 18%; }
 }

 .pw-bubble {
 position: absolute;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 8px 12px;
 font-size: 12px;
 color: var(--pw-ink);
 box-shadow: var(--pw-shadow-md);
 opacity: 0;
 z-index: 5;
 white-space: nowrap;
 max-width: 240px;
 }

 .pw-bubble-1 {
 top: 12%;
 left: 6%;
 background: var(--pw-accent);
 color: #fff;
 border-color: var(--pw-accent);
 border-bottom-left-radius: 4px;
 animation: pw-bubble-pop-1 12s ease-in-out infinite;
 }

 .pw-bubble-2 {
 top: 8%;
 right: 6%;
 color: #1d4d36;
 background: #d8e4dd;
 border-color: #b9d2c5;
 font-weight: 500;
 animation: pw-bubble-pop-2 12s ease-in-out infinite;
 }

 .pw-bubble-3 {
 bottom: 18%;
 right: 8%;
 color: var(--pw-ink-soft);
 background: var(--pw-surface);
 border-color: var(--pw-border-strong);
 animation: pw-bubble-pop-3 12s ease-in-out infinite;
 }

 @keyframes pw-bubble-pop-1 {
 0%, 4% { opacity: 0; transform: translateY(6px); }
 8%, 36% { opacity: 1; transform: translateY(0); }
 44%, 100% { opacity: 0; transform: translateY(-4px); }
 }

 @keyframes pw-bubble-pop-2 {
 0%, 30% { opacity: 0; transform: translateY(6px); }
 36%, 64% { opacity: 1; transform: translateY(0); }
 72%, 100% { opacity: 0; transform: translateY(-4px); }
 }

 @keyframes pw-bubble-pop-3 {
 0%, 58% { opacity: 0; transform: translateY(6px); }
 64%, 92% { opacity: 1; transform: translateY(0); }
 96%, 100% { opacity: 0; transform: translateY(-4px); }
 }

 .pw-dock {
 position: absolute;
 bottom: 24px;
 left: 24px;
 right: 24px;
 background: var(--pw-surface);
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 8px;
 display: flex;
 align-items: center;
 gap: 8px;
 box-shadow: var(--pw-shadow-md);
 }

 .pw-dock-folder {
 flex: 1;
 background: transparent;
 border: 1px solid var(--pw-border);
 border-radius: 0;
 padding: 8px 12px;
 font-family: inherit;
 font-size: 13px;
 color: var(--pw-ink-soft);
 text-align: left;
 cursor: default;
 }

 .pw-dock-add {
 width: 32px;
 height: 32px;
 border-radius: 0;
 background: transparent;
 border: 1px solid var(--pw-border);
 color: var(--pw-muted);
 font-size: 16px;
 font-weight: 600;
 cursor: pointer;
 display: flex;
 align-items: center;
 justify-content: center;
 }

 .pw-dock-go {
 background: var(--pw-accent-bg);
 color: var(--pw-accent);
 border: 1px solid var(--pw-accent-soft);
 border-radius: 0;
 padding: 8px 16px;
 font-family: inherit;
 font-size: 13px;
 font-weight: 500;
 cursor: default;
 animation: pw-dock-pulse 3s ease-in-out infinite;
 }

 @keyframes pw-dock-pulse {
 0%, 100% { box-shadow: 0 0 0 rgba(201, 99, 66, 0); }
 50% { box-shadow: 0 0 16px rgba(201, 99, 66, 0.25); }
 }

 .pw-footer {
 font-size: 12px;
 color: var(--pw-dim);
 text-align: center;
 padding: 16px 0 24px;
 }

 @media (max-width: 900px) {
 .pw-split {
 grid-template-columns: 1fr;
 padding: 18px 24px 32px;
 }
 .pw-left {
 margin: 0 auto;
 max-width: 480px;
 }
 .pw-right {
 display: none;
 }
 .pw-hero {
 font-size: 44px;
 }
 .pw-top {
 padding: 18px 24px;
 }
 }

 @media (max-width: 480px) {
 .pw-hero {
 font-size: 32px;
 }
 }

 @media (prefers-reduced-motion: reduce) {
 .pw-tile,
 .pw-cursor,
 .pw-bubble,
 .pw-grid-bg,
 .pw-dock-go {
 animation: none !important;
 }
 }

 /* Morphing mini-chart in tile 2 (bar → line → pie cycle) */
 .pw-morph {
 position: relative;
 width: 22px;
 height: 22px;
 display: inline-block;
 }

 .pw-morph-svg {
 position: absolute;
 inset: 0;
 width: 22px;
 height: 22px;
 opacity: 0;
 transform: scale(0.85);
 transition: opacity 0.5s ease, transform 0.5s ease;
 }

 .pw-morph-bar { animation: pw-morph-bar 12s ease-in-out infinite; }
 .pw-morph-line { animation: pw-morph-line 12s ease-in-out infinite; }
 .pw-morph-pie { animation: pw-morph-pie 12s ease-in-out infinite; }

 @keyframes pw-morph-bar {
 0%, 28% { opacity: 1; transform: scale(1); }
 33%, 100% { opacity: 0; transform: scale(0.85); }
 }
 @keyframes pw-morph-line {
 0%, 30% { opacity: 0; transform: scale(0.85); }
 35%, 61% { opacity: 1; transform: scale(1); }
 66%, 100% { opacity: 0; transform: scale(0.85); }
 }
 @keyframes pw-morph-pie {
 0%, 63% { opacity: 0; transform: scale(0.85); }
 68%, 95% { opacity: 1; transform: scale(1); }
 100% { opacity: 0; transform: scale(0.85); }
 }

 @media (prefers-reduced-motion: reduce) {
 .pw-morph-bar, .pw-morph-line, .pw-morph-pie { animation: none; }
 .pw-morph-bar { opacity: 1; transform: scale(1); }
 }

</style>
