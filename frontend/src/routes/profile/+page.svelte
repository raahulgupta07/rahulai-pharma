<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';

  let profile = $state<any>(null);
  let loading = $state(true);
  let saving = $state(false);
  let saveMsg = $state('');

  // tab: profile | account | password
  let tab = $state<'profile' | 'account' | 'password'>('profile');

  // Editable profile fields
  let firstName = $state('');
  let lastName = $state('');
  let email = $state('');
  let department = $state('');
  let jobTitle = $state('');
  let phone = $state('');
  let bio = $state('');
  let timezone = $state('UTC');

  // Account identity
  let role = $state('USER');

  // API key
  let apiKey = $state('');
  let apiKeyLoading = $state(false);
  let apiKeyCopied = $state(false);

  // Password
  let cpOld = $state('');
  let cpNew = $state('');
  let cpConfirm = $state('');
  let cpError = $state('');
  let cpBusy = $state(false);
  let cpSuccess = $state(false);

  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  const TABS = [
    { id: 'profile', label: 'Profile' },
    { id: 'account', label: 'Account' },
    { id: 'password', label: 'Password' },
  ] as const;

  onMount(async () => {
    // initial tab from ?tab= (back-compat: ?tab=settings → account)
    const q = (page.url.searchParams.get('tab') || '').toLowerCase();
    if (q === 'account' || q === 'settings') tab = 'account';
    else if (q === 'password' || q === 'security') tab = 'password';
    else tab = 'profile';

    const username = localStorage.getItem('dash_user') || '';
    try {
      const res = await fetch(`/api/auth/users/${username}/profile`, { headers: _h() });
      if (res.ok) {
        profile = await res.json();
        firstName = profile.first_name || '';
        lastName = profile.last_name || '';
        email = profile.email || '';
        department = profile.department || '';
        jobTitle = profile.job_title || '';
        phone = profile.phone || '';
        bio = profile.bio || '';
        timezone = profile.timezone || 'UTC';
      }
    } catch {}
    try {
      const c = await fetch('/api/auth/check', { headers: _h() });
      if (c.ok) {
        const d = await c.json();
        role = d.is_super ? 'SUPER ADMIN' : (d.is_admin ? 'ADMIN' : 'USER');
      }
    } catch {}
    loading = false;
  });

  function selectTab(id: typeof tab) {
    tab = id;
    if (id === 'account' && !apiKey && !apiKeyLoading) loadApiKey();
  }

  async function saveProfile() {
    saving = true; saveMsg = '';
    const username = localStorage.getItem('dash_user') || '';
    const params = new URLSearchParams({
      first_name: firstName, last_name: lastName, email, department, job_title: jobTitle, phone, bio, timezone,
    });
    try {
      const res = await fetch(`/api/auth/users/${username}/profile?${params}`, { method: 'PUT', headers: _h() });
      saveMsg = res.ok ? 'Profile saved.' : 'Failed to save.';
    } catch { saveMsg = 'Connection error.'; }
    saving = false;
  }

  async function loadApiKey() {
    apiKeyLoading = true;
    try {
      const res = await fetch('/api/auth/api-key', { headers: _h() });
      if (res.ok) { const d = await res.json(); apiKey = d.api_key || ''; }
    } catch {} finally { apiKeyLoading = false; }
  }
  async function regenApiKey() {
    apiKeyLoading = true;
    try {
      const res = await fetch('/api/auth/api-key/regenerate', { method: 'POST', headers: _h() });
      if (res.ok) { const d = await res.json(); apiKey = d.api_key || ''; }
    } catch {} finally { apiKeyLoading = false; }
  }
  function copyApiKey() {
    if (!apiKey) return;
    navigator.clipboard.writeText(apiKey);
    apiKeyCopied = true;
    setTimeout(() => apiKeyCopied = false, 1500);
  }

  async function changePassword() {
    if (cpNew !== cpConfirm) { cpError = 'Passwords do not match'; return; }
    if (cpNew.length < 4) { cpError = 'Min 4 characters'; return; }
    cpError = ''; cpBusy = true; cpSuccess = false;
    try {
      const res = await fetch(`/api/auth/change-password?old_password=${encodeURIComponent(cpOld)}&new_password=${encodeURIComponent(cpNew)}`, {
        method: 'POST', headers: _h(),
      });
      if (res.ok) { cpSuccess = true; cpOld = ''; cpNew = ''; cpConfirm = ''; }
      else { const d = await res.json().catch(() => ({})); cpError = d.detail || 'Failed'; }
    } catch { cpError = 'Connection failed'; }
    cpBusy = false;
  }

  const initial = $derived((profile?.username || 'U').charAt(0).toUpperCase());
</script>

<div class="ps-wrap">
  <div class="ps-inner">
    <h1 class="ps-title">Profile &amp; Settings</h1>
    <p class="ps-sub">Manage your profile, account, and password.</p>

    <!-- Tabs -->
    <div class="ps-tabs">
      {#each TABS as t}
        <button class="ps-tab" class:ps-tab-active={tab === t.id} onclick={() => selectTab(t.id)}>{t.label}</button>
      {/each}
    </div>

    {#if loading}
      <div class="ps-muted">Loading…</div>
    {:else if profile}

      <!-- ───────── PROFILE TAB ───────── -->
      {#if tab === 'profile'}
        <div class="ps-card">
          <div class="ps-avatar-row">
            <div class="ps-avatar">{initial}</div>
            <div style="flex:1;">
              <div class="ps-name">{profile.username}</div>
              <div class="ps-meta">Signed in via {profile.auth_provider || 'local'} · since {profile.created_at?.slice(0, 10) || '—'}</div>
            </div>
            <button type="button" class="ps-btn-ghost">Upload photo</button>
          </div>

          <div class="ps-fields">
            <div class="ps-grid2">
              <div><label class="ps-label">First name</label><input class="ps-input" type="text" bind:value={firstName} /></div>
              <div><label class="ps-label">Last name</label><input class="ps-input" type="text" bind:value={lastName} /></div>
            </div>
            <div><label class="ps-label">Email</label><input class="ps-input" type="email" bind:value={email} /></div>
            <div class="ps-grid2">
              <div><label class="ps-label">Department</label><input class="ps-input" type="text" bind:value={department} /></div>
              <div><label class="ps-label">Job title</label><input class="ps-input" type="text" bind:value={jobTitle} /></div>
            </div>
            <div class="ps-grid2">
              <div><label class="ps-label">Phone</label><input class="ps-input" type="text" bind:value={phone} /></div>
              <div><label class="ps-label">Timezone</label><input class="ps-input" type="text" bind:value={timezone} placeholder="UTC" /></div>
            </div>
            <div><label class="ps-label">Bio</label><textarea class="ps-input" bind:value={bio} rows="3" placeholder="Tell us a little about yourself…"></textarea></div>

            {#if saveMsg}<div class="ps-savemsg" class:ok={saveMsg === 'Profile saved.'}>{saveMsg}</div>{/if}

            <div style="padding-top:8px;">
              <button type="button" class="ps-btn-primary" onclick={saveProfile} disabled={saving}>{saving ? 'Saving…' : 'Save profile'}</button>
            </div>
          </div>
        </div>

      {/if}

      <!-- ───────── ACCOUNT TAB ───────── -->
      {#if tab === 'account'}
        <div class="ps-card">
          <div class="ps-section-title">Identity</div>
          <div class="ps-kv"><span class="ps-k">Username</span><span class="ps-v">{profile.username}</span></div>
          <div class="ps-kv"><span class="ps-k">Email</span><span class="ps-v">{profile.email || '—'}</span></div>
          <div class="ps-kv"><span class="ps-k">Role</span><span class="ps-v"><span class="ps-tier" class:super={role === 'SUPER ADMIN'} class:admin={role === 'ADMIN'}>{role}</span></span></div>
          <div class="ps-kv"><span class="ps-k">Sign-in method</span><span class="ps-v">{profile.auth_provider || 'local'}</span></div>
          <div class="ps-kv ps-kv-last"><span class="ps-k">Member since</span><span class="ps-v">{profile.created_at?.slice(0, 10) || '—'}</span></div>
        </div>

        <div class="ps-card" style="margin-top:18px;">
          <div class="ps-section-title">API key</div>
          <p class="ps-muted" style="margin:0 0 12px;">Use this key for programmatic access (OpenAI-compatible gateway).</p>
          {#if apiKeyLoading}
            <div class="ps-muted">Loading key…</div>
          {:else}
            <div class="ps-keybox">{apiKey || 'No key generated yet'}</div>
            <div style="display:flex; gap:10px; margin-top:12px;">
              <button type="button" class="ps-btn-ghost" onclick={copyApiKey} disabled={!apiKey}>{apiKeyCopied ? '✓ Copied' : 'Copy'}</button>
              <button type="button" class="ps-btn-ghost" onclick={regenApiKey}>Regenerate</button>
            </div>
          {/if}
        </div>
      {/if}

      <!-- ───────── PASSWORD TAB ───────── -->
      {#if tab === 'password'}
        <div class="ps-card">
          <div class="ps-section-title">Change password</div>
          {#if cpSuccess}
            <div class="ps-savemsg ok">Password updated.</div>
          {/if}
          <div class="ps-fields" style="max-width:380px;">
            <div><label class="ps-label">Current password</label><input class="ps-input" type="password" bind:value={cpOld} autocomplete="current-password" /></div>
            <div><label class="ps-label">New password</label><input class="ps-input" type="password" bind:value={cpNew} autocomplete="new-password" /></div>
            <div><label class="ps-label">Confirm new password</label><input class="ps-input" type="password" bind:value={cpConfirm} autocomplete="new-password" /></div>
            {#if cpError}<div class="ps-savemsg">{cpError}</div>{/if}
            <div style="padding-top:8px;">
              <button type="button" class="ps-btn-primary" onclick={changePassword} disabled={cpBusy}>{cpBusy ? 'Updating…' : 'Update password'}</button>
            </div>
          </div>
        </div>
      {/if}

    {/if}
  </div>
</div>

<style>
  .ps-wrap { padding: 40px 24px; overflow-y: auto; height: 100%; background: var(--pw-bg); font-family: var(--pw-font-body); }
  .ps-inner { max-width: 680px; margin: 0 auto; }
  .ps-title { font-family: var(--pw-font-headline); font-size: 20px; font-weight: 500; letter-spacing: -0.02em; color: var(--pw-ink); margin: 0 0 6px; }
  .ps-sub { font-size: 11px; color: var(--pw-muted); margin: 0 0 22px; }
  .ps-muted { font-size: 11px; color: var(--pw-muted); }

  .ps-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--pw-border); margin-bottom: 24px; }
  .ps-tab { background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 14px; font-family: var(--pw-font-body); font-size: 12.5px; font-weight: 500; color: var(--pw-muted); cursor: pointer; margin-bottom: -1px; transition: color .12s, border-color .12s; }
  .ps-tab:hover { color: var(--pw-ink); }
  .ps-tab-active { color: var(--pw-accent); border-bottom-color: var(--pw-accent); font-weight: 600; }

  .ps-card { background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: var(--pw-radius); padding: 28px; box-shadow: var(--pw-shadow-sm); }
  .ps-avatar-row { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid var(--pw-border); }
  .ps-avatar { width: 64px; height: 64px; border-radius: 50%; background: var(--pw-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-headline); font-size: 26px; font-weight: 500; }
  .ps-name { font-size: 12px; font-weight: 500; color: var(--pw-ink); }
  .ps-meta { font-size: 11px; color: var(--pw-muted); margin-top: 2px; }

  .ps-fields { display: flex; flex-direction: column; gap: 16px; }
  .ps-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .ps-label { display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px; }
  .ps-input { width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none; resize: vertical; }
  .ps-input:focus { border-color: var(--pw-accent); }

  .ps-savemsg { font-size: 11px; color: var(--pw-error); }
  .ps-savemsg.ok { color: var(--pw-success); }

  .ps-btn-primary { background: var(--pw-accent); color: #fff; border: none; padding: 11px 22px; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer; }
  .ps-btn-primary:disabled { opacity: .6; cursor: default; }
  .ps-btn-ghost { background: transparent; border: 1px solid var(--pw-border-strong); color: var(--pw-ink-soft); padding: 8px 16px; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer; }
  .ps-btn-ghost:disabled { opacity: .5; cursor: default; }

  /* account k/v */
  .ps-section-title { font-family: var(--pw-font-headline); font-size: 13px; font-weight: 600; color: var(--pw-ink); margin-bottom: 16px; }
  .ps-kv { display: flex; justify-content: space-between; align-items: center; padding: 11px 0; border-bottom: 1px solid var(--pw-border); font-size: 12.5px; }
  .ps-kv-last { border-bottom: none; padding-bottom: 0; }
  .ps-k { color: var(--pw-muted); }
  .ps-v { color: var(--pw-ink); font-weight: 500; }
  .ps-tier { font-size: 9px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; padding: 2px 8px; border-radius: var(--pw-radius-pill); background: var(--pw-bg-alt); color: var(--pw-ink-soft); }
  .ps-tier.super { background: rgba(201,99,66,0.12); color: var(--pw-accent); }
  .ps-tier.admin { background: var(--pw-bg-alt); color: var(--pw-ink-soft); }

  .ps-keybox { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: var(--pw-ink); background: var(--pw-bg-alt); border: 1px solid var(--pw-border); border-radius: var(--pw-radius-sm); padding: 12px 14px; word-break: break-all; }
</style>
