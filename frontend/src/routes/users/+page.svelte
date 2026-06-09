<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';

  // Users & Access — compact fit-to-screen table; click a row to expand full detail inline.
  // Account CRUD = admin tier. Role grant (admin tier) = super admin only.

  let loading = $state(true);
  let err = $state('');
  let info = $state('');
  let users = $state<any[]>([]);
  let superName = $state('');
  let amSuper = $state(false);
  let q = $state('');
  let busy = $state('');
  let expanded = $state('');      // username of open row
  let copied = $state('');

  // create modal
  let showCreate = $state(false);
  let cUser = $state(''); let cEmail = $state(''); let cPass = $state('');
  let cErr = $state(''); let cBusy = $state(false);

  // reset-password modal
  let resetFor = $state(''); let rPass = $state(''); let rErr = $state(''); let rBusy = $state(false);

  // delete modal
  let delFor = $state(''); let delConfirm = $state(''); let dBusy = $state(false);

  // role change (super only)
  let savingRole = $state(''); let savedRole = $state('');

  function token() { try { return localStorage.getItem('dash_token') || ''; } catch { return ''; } }
  function h() { return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token()}` }; }

  const filtered = $derived(
    !q.trim() ? users
      : users.filter(u => {
          const s = q.toLowerCase();
          return (u.username || '').toLowerCase().includes(s) ||
                 (u.email || '').toLowerCase().includes(s) ||
                 String(u.id || '').includes(s) ||
                 (u.external_id || '').toLowerCase().includes(s);
        })
  );

  const stats = $derived({
    total: users.length,
    admins: users.filter(u => u.username === superName || u.role === 'admin' || u.role === 'super').length,
    active: users.filter(u => u.is_active !== false).length,
    sso: users.filter(u => u.auth_provider && u.auth_provider !== 'local').length,
  });

  function isProtected(u: any) {
    if (amSuper) return u.username === superName;
    return u.username === superName || u.role === 'admin' || u.role === 'super';
  }
  function tier(u: any) {
    if (u.username === superName || u.role === 'super') return 'Super Admin';
    return u.role === 'admin' ? 'Admin' : 'User';
  }
  function initials(name: string) {
    const s = (name || '?').replace(/^svc:/, '');
    const parts = s.split(/[\s._:-]+/).filter(Boolean);
    return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || s.slice(0, 2).toUpperCase();
  }
  const AV = ['#c96342', '#2a9d8f', '#b8860b', '#7c5cbf', '#2e7d32', '#3a6ea5'];
  function avatarColor(name: string) {
    let hsum = 0; for (let i = 0; i < (name || '').length; i++) hsum = (hsum * 31 + name.charCodeAt(i)) >>> 0;
    return AV[hsum % AV.length];
  }
  function fullName(u: any) {
    const n = [u.first_name, u.last_name].filter(Boolean).join(' ');
    return n || '—';
  }
  function relTime(ts: string | null) {
    if (!ts) return 'never';
    const t = Date.parse(ts); if (isNaN(t)) return ts;
    const diff = Date.now() - t;
    const m = Math.floor(diff / 60000), hh = Math.floor(diff / 3600000), d = Math.floor(diff / 86400000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m} min ago`;
    if (hh < 24) return `${hh} h ago`;
    if (d < 30) return `${d} d ago`;
    return ts.slice(0, 10);
  }
  function dateOnly(ts: string | null) { return ts ? ts.slice(0, 10) : '—'; }
  function fullTs(ts: string | null) { return ts ? ts.replace('T', ' ').slice(0, 16) : '—'; }
  function authLabel(p: string) { return p === 'local' || !p ? 'local' : p; }

  function flash(msg: string) { info = msg; setTimeout(() => { if (info === msg) info = ''; }, 2000); }
  async function copy(val: string, key: string) {
    try { await navigator.clipboard.writeText(val); copied = key; setTimeout(() => { if (copied === key) copied = ''; }, 1200); } catch {}
  }

  async function load() {
    loading = true; err = '';
    try {
      const [cr, ur] = await Promise.all([
        fetch('/api/auth/check', { headers: h() }).catch(() => null),
        fetch('/api/auth/users', { headers: h() })
      ]);
      if (cr && cr.ok) { const cd = await cr.json(); amSuper = !!(cd.is_super); }
      if (ur.status === 403) { err = 'Admin access required.'; loading = false; return; }
      if (!ur.ok) { err = 'Failed to load users'; loading = false; return; }
      const d = await ur.json();
      users = d.users || []; superName = d.super_admin || '';
    } catch (e: any) { err = e?.message || 'Load failed'; }
    loading = false;
  }

  async function createUser() {
    cErr = '';
    if (!cUser || cUser.length < 2) { cErr = 'Username must be at least 2 characters'; return; }
    if (!cPass || cPass.length < 4) { cErr = 'Password must be at least 4 characters'; return; }
    cBusy = true;
    try {
      const url = `/api/auth/users/create?username=${encodeURIComponent(cUser)}&password=${encodeURIComponent(cPass)}&email=${encodeURIComponent(cEmail)}`;
      const r = await fetch(url, { method: 'POST', headers: h() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { cErr = d?.detail || 'Create failed'; cBusy = false; return; }
      showCreate = false; cUser = ''; cEmail = ''; cPass = '';
      flash('User created'); await load();
    } catch (e: any) { cErr = e?.message || 'Create failed'; }
    cBusy = false;
  }

  async function toggleActive(u: any) {
    if (isProtected(u) && u.username !== superName) return;
    busy = u.username; err = '';
    try {
      const r = await fetch(`/api/auth/users/${encodeURIComponent(u.username)}/toggle-active`, { method: 'POST', headers: h() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err = d?.detail || 'Failed'; busy = ''; return; }
      u.is_active = d.is_active; users = [...users];
      flash(d.is_active ? 'User enabled' : 'User disabled');
    } catch (e: any) { err = e?.message || 'Failed'; }
    busy = '';
  }

  async function doReset() {
    rErr = '';
    if (!rPass || rPass.length < 4) { rErr = 'Password must be at least 4 characters'; return; }
    rBusy = true;
    try {
      const r = await fetch(`/api/auth/users/${encodeURIComponent(resetFor)}/reset-password?new_password=${encodeURIComponent(rPass)}`, { method: 'POST', headers: h() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { rErr = d?.detail || 'Reset failed'; rBusy = false; return; }
      resetFor = ''; rPass = ''; flash('Password reset');
    } catch (e: any) { rErr = e?.message || 'Reset failed'; }
    rBusy = false;
  }

  async function doDelete() {
    if (delConfirm !== delFor) return;
    dBusy = true; err = '';
    try {
      const r = await fetch(`/api/auth/users/${encodeURIComponent(delFor)}`, { method: 'DELETE', headers: h() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err = d?.detail || 'Delete failed'; dBusy = false; delFor = ''; return; }
      flash('User deleted'); delFor = ''; delConfirm = ''; expanded = ''; await load();
    } catch (e: any) { err = e?.message || 'Delete failed'; }
    dBusy = false;
  }

  async function setRole(u: any, role: string) {
    if (u.role === role) return;
    if (u.username === superName || u.role === 'super') return;
    savingRole = u.username; err = '';
    try {
      const r = await fetch(`/api/auth/users/${encodeURIComponent(u.username)}/role?role=${encodeURIComponent(role)}`, { method: 'POST', headers: h() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err = d?.detail || 'Role change failed'; savingRole = ''; return; }
      u.role = role; users = [...users];
      savedRole = u.username; setTimeout(() => { if (savedRole === u.username) savedRole = ''; }, 1500);
      flash('Role updated');
    } catch (e: any) { err = e?.message || 'Role change failed'; }
    savingRole = '';
  }

  function toggleRow(u: any) { expanded = expanded === u.username ? '' : u.username; }

  onMount(load);
</script>

<svelte:head><title>Users &amp; Access</title></svelte:head>

<div class="ua-wrap">
  <header class="ua-head">
    <div class="ua-head-text">
      <h1>Users &amp; Access</h1>
      <p class="ua-sub">{amSuper ? 'Accounts, tiers and federation. Click a row for full detail.' : 'Accounts and federation. Click a row for full detail.'} The super admin is a fixed account.</p>
    </div>
    <button class="ua-btn ua-btn-primary" onclick={() => { showCreate = true; cErr = ''; }}>+ New user</button>
  </header>

  {#if info}<div class="ua-info">{info}</div>{/if}
  {#if err}<div class="ua-err">{err}</div>{/if}

  {#if loading}
    <div class="ua-msg">Loading…</div>
  {:else}
    <div class="ua-toolbar">
      <div class="ua-stats">
        <div class="ua-stat"><span class="ua-stat-n">{stats.total}</span><span class="ua-stat-l">Users</span></div>
        <div class="ua-stat"><span class="ua-stat-n">{stats.admins}</span><span class="ua-stat-l">Admins</span></div>
        <div class="ua-stat"><span class="ua-stat-n">{stats.active}</span><span class="ua-stat-l">Active</span></div>
        <div class="ua-stat"><span class="ua-stat-n">{stats.sso}</span><span class="ua-stat-l">SSO</span></div>
      </div>
      <div class="ua-search">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
        <input placeholder="Search user / email / id…" bind:value={q} />
      </div>
    </div>

    <div class="ua-table">
      <div class="ua-thead">
        <span class="c-user">User</span>
        <span class="c-role">Role</span>
        <span class="c-auth">Auth</span>
        <span class="c-status">Status</span>
        <span class="c-last">Last active</span>
        <span class="c-chev"></span>
      </div>

      {#each filtered as u}
        {@const locked = isProtected(u)}
        {@const isOpen = expanded === u.username}
        <div class="ua-rowgroup" class:open={isOpen}>
          <button class="ua-row" class:ua-inactive={u.is_active === false} onclick={() => toggleRow(u)}>
            <span class="c-user">
              <span class="ua-av" style="background:{avatarColor(u.username)}">{initials(u.username)}</span>
              <span class="ua-id">
                <span class="ua-name">{u.username}{#if u.username === superName}<span class="ua-lock">🔒</span>{/if}</span>
                <span class="ua-metaline">#{u.id} · {u.email || '—'}</span>
              </span>
            </span>
            <span class="c-role">
              <span class="ua-tier" class:ua-tier-super={u.username === superName || u.role === 'super'} class:ua-tier-admin={u.role === 'admin' && u.username !== superName}>{tier(u)}</span>
            </span>
            <span class="c-auth">
              <span class="ua-auth" class:fed={u.auth_provider && u.auth_provider !== 'local'}>{u.auth_provider && u.auth_provider !== 'local' ? '🔗 ' : ''}{authLabel(u.auth_provider)}</span>
            </span>
            <span class="c-status">
              <span class="ua-dot" class:on={u.is_active !== false} class:off={u.is_active === false}></span>
              {u.is_active === false ? 'disabled' : 'active'}
            </span>
            <span class="c-last" title={fullTs(u.last_login)}>{relTime(u.last_login)}</span>
            <span class="c-chev"><svg class="ua-chev" class:rot={isOpen} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></span>
          </button>

          {#if isOpen}
            <div class="ua-detail">
              <div class="ua-grid">
                <section>
                  <h4>Identity</h4>
                  <dl>
                    <dt>User ID</dt><dd>{u.id} <button class="ua-cp" onclick={() => copy(String(u.id), 'id'+u.id)}>{copied === 'id'+u.id ? '✓' : '⧉'}</button></dd>
                    <dt>Username</dt><dd>{u.username}</dd>
                    <dt>Email</dt><dd>{u.email || '—'}{#if u.email} <button class="ua-cp" onclick={() => copy(u.email, 'em'+u.id)}>{copied === 'em'+u.id ? '✓' : '⧉'}</button>{/if}</dd>
                    <dt>Full name</dt><dd>{fullName(u)}</dd>
                    <dt>Department</dt><dd>{u.department || '—'}</dd>
                    <dt>Job title</dt><dd>{u.job_title || '—'}</dd>
                  </dl>
                </section>
                <section>
                  <h4>Federation / SSO</h4>
                  <dl>
                    <dt>Provider</dt><dd>{u.auth_provider && u.auth_provider !== 'local' ? '🔗 ' + u.auth_provider.toUpperCase() : 'local (password)'}</dd>
                    <dt>External ID</dt><dd class="ua-mono" title={u.external_id || ''}>{u.external_id || '—'}{#if u.external_id} <button class="ua-cp" onclick={() => copy(u.external_id, 'ex'+u.id)}>{copied === 'ex'+u.id ? '✓' : '⧉'}</button>{/if}</dd>
                    <dt>Mapped email</dt><dd>{u.email || '—'}</dd>
                    <dt>Site / branch</dt><dd>{u.site_code || '—'}</dd>
                    <dt>Group→role</dt><dd><a href="{base}/ui/command-center?tab=auth">map in Authentication →</a></dd>
                  </dl>
                </section>
                <section>
                  <h4>Activity</h4>
                  <dl>
                    <dt>Created</dt><dd>{dateOnly(u.created_at)}</dd>
                    <dt>Last active</dt><dd>{fullTs(u.last_login)}<span class="ua-rel"> ({relTime(u.last_login)})</span></dd>
                    <dt>Projects</dt><dd>{u.project_count ?? 0}</dd>
                    <dt>Status</dt><dd>{u.is_active === false ? 'disabled' : 'active'}</dd>
                  </dl>
                </section>
                <section>
                  <h4>Access</h4>
                  <dl>
                    <dt>Tier</dt>
                    <dd>
                      {#if amSuper && u.username !== superName && u.role !== 'super'}
                        <select class="ua-role-select" disabled={savingRole === u.username}
                          value={u.role === 'admin' ? 'admin' : 'user'}
                          onchange={(e) => setRole(u, (e.currentTarget as HTMLSelectElement).value)}>
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </select>
                        {#if savingRole === u.username}<span class="ua-flag">…</span>{/if}
                        {#if savedRole === u.username}<span class="ua-flag ua-ok">✓ saved</span>{/if}
                      {:else}
                        {tier(u)} {#if locked}<span class="ua-rel">(fixed)</span>{/if}
                      {/if}
                    </dd>
                  </dl>
                </section>
              </div>

              <div class="ua-actions">
                {#if locked && u.username !== superName}
                  <span class="ua-fixed">🔒 admin-tier account — only super admin can manage</span>
                {:else if u.username === superName}
                  <span class="ua-fixed">🔒 super admin — fixed account, no changes</span>
                {:else}
                  <button class="ua-btn" disabled={busy === u.username} onclick={() => { resetFor = u.username; rPass = ''; rErr = ''; }}>🔑 Reset password</button>
                  <button class="ua-btn" disabled={busy === u.username} onclick={() => toggleActive(u)}>{u.is_active === false ? '▶ Enable' : '⏸ Disable'}</button>
                  <button class="ua-btn ua-btn-dangerghost" onclick={() => { delFor = u.username; delConfirm = ''; }}>🗑 Delete user</button>
                {/if}
              </div>
            </div>
          {/if}
        </div>
      {/each}

      {#if filtered.length === 0}
        <div class="ua-empty">No users match.</div>
      {/if}
    </div>
  {/if}
</div>

{#if showCreate}
  <div class="ua-modal-bg" onclick={() => (showCreate = false)} role="presentation">
    <div class="ua-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
      <h2>Create user</h2>
      <label class="ua-field"><span>Username</span><input bind:value={cUser} autocomplete="off" /></label>
      <label class="ua-field"><span>Email <em>(optional)</em></span><input bind:value={cEmail} type="email" autocomplete="off" /></label>
      <label class="ua-field"><span>Temporary password</span><input bind:value={cPass} type="text" autocomplete="off" /></label>
      {#if cErr}<div class="ua-err">{cErr}</div>{/if}
      <div class="ua-modal-actions">
        <button class="ua-btn" onclick={() => (showCreate = false)}>Cancel</button>
        <button class="ua-btn ua-btn-primary" disabled={cBusy} onclick={createUser}>{cBusy ? 'Creating…' : 'Create'}</button>
      </div>
    </div>
  </div>
{/if}

{#if resetFor}
  <div class="ua-modal-bg" onclick={() => (resetFor = '')} role="presentation">
    <div class="ua-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
      <h2>Reset password</h2>
      <p class="ua-sub">New password for <b>{resetFor}</b>. Share it securely; they should change it on next login.</p>
      <label class="ua-field"><span>New password</span><input bind:value={rPass} type="text" autocomplete="off" /></label>
      {#if rErr}<div class="ua-err">{rErr}</div>{/if}
      <div class="ua-modal-actions">
        <button class="ua-btn" onclick={() => (resetFor = '')}>Cancel</button>
        <button class="ua-btn ua-btn-primary" disabled={rBusy} onclick={doReset}>{rBusy ? 'Resetting…' : 'Reset password'}</button>
      </div>
    </div>
  </div>
{/if}

{#if delFor}
  <div class="ua-modal-bg" onclick={() => (delFor = '')} role="presentation">
    <div class="ua-modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
      <h2 class="ua-danger-h">Delete user</h2>
      <p class="ua-sub">This permanently deletes <b>{delFor}</b> and their schema. Type the username to confirm.</p>
      <input class="ua-field-input" bind:value={delConfirm} placeholder={delFor} autocomplete="off" />
      <div class="ua-modal-actions">
        <button class="ua-btn" onclick={() => (delFor = '')}>Cancel</button>
        <button class="ua-btn ua-btn-danger" disabled={dBusy || delConfirm !== delFor} onclick={doDelete}>{dBusy ? 'Deleting…' : 'Delete permanently'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .ua-wrap { max-width: 1040px; margin: 0 auto; padding: 32px 20px 80px; color: var(--pw-ink, #1a1a1a); }
  .ua-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
  .ua-head-text h1 { font-size: 26px; font-weight: 900; margin: 0 0 6px; letter-spacing: -0.01em; }
  .ua-sub { font-size: 13px; opacity: 0.7; margin: 0; line-height: 1.5; }
  .ua-info { background: rgba(46,125,50,0.1); color: #2e7d32; font-size: 13px; padding: 8px 12px; border-radius: 8px; margin-bottom: 12px; }
  .ua-err { background: rgba(192,57,43,0.1); color: var(--pw-error, #c0392b); font-size: 13px; padding: 8px 12px; border-radius: 8px; margin-bottom: 12px; }
  .ua-msg { font-size: 13px; padding: 12px 0; opacity: 0.7; }

  .ua-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .ua-stats { display: flex; gap: 10px; }
  .ua-stat { display: flex; flex-direction: column; align-items: flex-start; min-width: 74px; padding: 9px 13px; border: 1px solid var(--pw-border, #e3e3e0); border-radius: 10px; background: var(--pw-surface, #fff); }
  .ua-stat-n { font-size: 21px; font-weight: 900; line-height: 1; }
  .ua-stat-l { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.55; margin-top: 4px; }
  .ua-search { display: flex; align-items: center; gap: 8px; border: 1px solid var(--pw-border, #d8d8d4); border-radius: 10px; padding: 0 12px; background: var(--pw-bg, #fff); min-width: 240px; flex: 1; max-width: 320px; }
  .ua-search svg { opacity: 0.45; flex-shrink: 0; }
  .ua-search input { border: none; outline: none; background: transparent; font-size: 13px; padding: 10px 0; width: 100%; color: var(--pw-ink, #1a1a1a); }

  .ua-table { border: 1px solid var(--pw-border, #e3e3e0); border-radius: 12px; background: var(--pw-surface, #fff); overflow: hidden; }
  .ua-thead { display: grid; grid-template-columns: 1fr 130px 110px 110px 120px 36px; align-items: center; gap: 12px; padding: 0 16px; height: 40px; background: var(--pw-bg-alt, #f6f5f1); border-bottom: 1px solid var(--pw-border, #e3e3e0); }
  .ua-thead span { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-ink-soft, #888); }
  .ua-rowgroup { border-bottom: 1px solid var(--pw-border-soft, #ededea); }
  .ua-rowgroup:last-child { border-bottom: none; }
  .ua-rowgroup.open { background: rgba(201,99,66,0.025); }
  .ua-row { display: grid; grid-template-columns: 1fr 130px 110px 110px 120px 36px; align-items: center; gap: 12px; padding: 0 16px; min-height: 56px; width: 100%; border: none; background: transparent; text-align: left; cursor: pointer; font: inherit; color: inherit; }
  .ua-row:hover { background: rgba(201,99,66,0.04); }
  .ua-inactive { opacity: 0.55; }

  .c-user { display: flex; align-items: center; gap: 12px; min-width: 0; padding: 8px 0; }
  .ua-av { width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; font-weight: 800; }
  .ua-id { display: flex; flex-direction: column; min-width: 0; }
  .ua-name { font-weight: 700; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ua-lock { margin-left: 6px; font-size: 11px; opacity: 0.6; }
  .ua-metaline { font-size: 12px; opacity: 0.55; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .ua-tier { font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 11px; background: var(--pw-bg-alt, rgba(0,0,0,0.05)); color: var(--pw-ink-soft, #777); }
  .ua-tier-super { background: rgba(192,57,43,0.12); color: var(--pw-error, #c0392b); }
  .ua-tier-admin { background: rgba(201,99,66,0.12); color: var(--pw-accent, #c96342); }
  .ua-auth { font-size: 12.5px; opacity: 0.8; }
  .ua-auth.fed { color: var(--pw-accent, #c96342); font-weight: 600; opacity: 1; }
  .c-status { display: flex; align-items: center; gap: 7px; font-size: 12.5px; opacity: 0.8; }
  .ua-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .ua-dot.on { background: #2e7d32; } .ua-dot.off { background: #b0b0b0; }
  .c-last { font-size: 12.5px; opacity: 0.7; }
  .c-chev { display: flex; justify-content: flex-end; }
  .ua-chev { opacity: 0.5; transition: transform 0.15s; }
  .ua-chev.rot { transform: rotate(90deg); color: var(--pw-accent, #c96342); opacity: 1; }

  .ua-detail { padding: 4px 18px 20px 18px; }
  .ua-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 28px; }
  .ua-detail section { padding: 12px 0; }
  .ua-detail h4 { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.06em; color: var(--pw-accent, #c96342); margin: 0 0 10px; }
  .ua-detail dl { display: grid; grid-template-columns: 110px 1fr; gap: 7px 12px; margin: 0; }
  .ua-detail dt { font-size: 12.5px; opacity: 0.55; }
  .ua-detail dd { font-size: 13px; margin: 0; word-break: break-word; }
  .ua-detail dd a { color: var(--pw-accent, #c96342); font-weight: 600; }
  .ua-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  .ua-rel { opacity: 0.5; font-size: 12px; }
  .ua-cp { border: none; background: transparent; cursor: pointer; opacity: 0.5; font-size: 12px; padding: 0 2px; color: inherit; }
  .ua-cp:hover { opacity: 1; }
  .ua-role-select { border: 1px solid var(--pw-border, #d8d8d4); border-radius: 7px; padding: 4px 9px; font-size: 12.5px; background: var(--pw-bg, #fff); color: var(--pw-ink, #1a1a1a); cursor: pointer; }
  .ua-flag { margin-left: 8px; font-size: 12px; opacity: 0.7; }
  .ua-ok { color: #2e7d32; }

  .ua-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--pw-border-soft, #ededea); }
  .ua-fixed { font-size: 12.5px; opacity: 0.6; }
  .ua-empty { text-align: center; opacity: 0.5; padding: 28px 0; font-size: 13px; }

  .ua-btn { border: 1px solid var(--pw-border, #d8d8d4); background: var(--pw-bg, #fff); color: var(--pw-ink, #1a1a1a); font-size: 13px; font-weight: 600; padding: 8px 14px; border-radius: 9px; cursor: pointer; white-space: nowrap; }
  .ua-btn:hover { background: var(--pw-bg-alt, #f6f5f1); }
  .ua-btn:disabled { opacity: 0.5; cursor: default; }
  .ua-btn-primary { background: var(--pw-accent, #c96342); color: #fff; border-color: var(--pw-accent, #c96342); }
  .ua-btn-primary:hover { filter: brightness(0.95); }
  .ua-btn-danger { background: var(--pw-error, #c0392b); color: #fff; border-color: var(--pw-error, #c0392b); }
  .ua-btn-dangerghost { color: var(--pw-error, #c0392b); border-color: rgba(192,57,43,0.4); }
  .ua-btn-dangerghost:hover { background: rgba(192,57,43,0.07); }

  .ua-modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 9000; }
  .ua-modal { background: var(--pw-surface, #fff); border: 1px solid var(--pw-border, #e3e3e0); border-radius: 14px; padding: 24px; width: 420px; max-width: calc(100vw - 32px); box-shadow: 0 12px 40px rgba(0,0,0,0.2); }
  .ua-modal h2 { font-size: 18px; font-weight: 800; margin: 0 0 14px; }
  .ua-danger-h { color: var(--pw-error, #c0392b); }
  .ua-field { display: block; margin-bottom: 14px; }
  .ua-field span { display: block; font-size: 12px; font-weight: 600; margin-bottom: 5px; opacity: 0.8; }
  .ua-field em { opacity: 0.5; font-weight: 400; }
  .ua-field input, .ua-field-input { width: 100%; box-sizing: border-box; border: 1px solid var(--pw-border, #d8d8d4); border-radius: 9px; padding: 9px 12px; font-size: 14px; background: var(--pw-bg, #fff); color: var(--pw-ink, #1a1a1a); }
  .ua-field-input { margin-bottom: 16px; }
  .ua-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }

  @media (max-width: 760px) {
    .ua-thead, .ua-row { grid-template-columns: 1fr 110px 36px; }
    .c-auth, .c-status, .c-last { display: none; }
    .ua-grid { grid-template-columns: 1fr; }
  }
</style>
