<script lang="ts">
  import { onMount } from 'svelte';

  // Super-admin Authentication config. Reads/writes the auth_config override row
  // via /api/auth/config. Secrets (LDAP_APP_PASSWORD, *_CLIENT_SECRET) are ENV-only
  // and never shown/stored here — this edits the non-secret toggles + mappings.

  let loading = $state(true);
  let saving = $state(false);
  let err = $state('');
  let ok = $state('');
  let note = $state('');
  let cfg = $state<any>(null);
  let raw = $state('');          // JSON textarea (advanced)
  let advanced = $state(false);

  function token() { try { return localStorage.getItem('dash_token') || ''; } catch { return ''; } }
  function h() { return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token()}` }; }

  async function load() {
    loading = true; err = '';
    try {
      const r = await fetch('/api/auth/config', { headers: h() });
      if (r.status === 403) { err = 'Super admin only.'; loading = false; return; }
      const d = await r.json();
      cfg = d.config; note = d.note || ''; raw = JSON.stringify(cfg, null, 2);
    } catch (e: any) { err = e?.message || 'Load failed'; }
    loading = false;
  }

  async function save() {
    saving = true; err = ''; ok = '';
    let body: any;
    try { body = advanced ? JSON.parse(raw) : cfg; }
    catch (e: any) { err = 'Invalid JSON: ' + e.message; saving = false; return; }
    try {
      const r = await fetch('/api/auth/config', { method: 'POST', headers: h(), body: JSON.stringify({ config: body }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { err = d?.detail || 'Save failed'; saving = false; return; }
      ok = 'Saved. Changes apply within 30s (config cache TTL).';
      await load();
    } catch (e: any) { err = e?.message || 'Save failed'; }
    saving = false;
  }

  // helpers for structured editor
  function addOidc() {
    cfg.oidc = cfg.oidc || [];
    cfg.oidc = [...cfg.oidc, { id: 'newprovider', name: 'New Provider', issuer: '', client_id: '',
      scopes: 'openid email profile', email_claim: 'email', username_claim: 'preferred_username',
      roles_claim: 'roles', allowed_roles: [], groups_claim: 'groups', group_to_site: {} }];
  }
  function removeOidc(i: number) { cfg.oidc = cfg.oidc.filter((_: any, j: number) => j !== i); }

  onMount(load);
</script>

<svelte:head><title>Authentication — Admin</title></svelte:head>

<div class="aa-wrap">
  <header class="aa-head">
    <h1>Authentication</h1>
    <p class="aa-sub">LDAP + OIDC/SSO. Secrets live in environment variables only — set <code>LDAP_APP_PASSWORD</code> and <code>*_CLIENT_SECRET</code> in <code>.env</code>, configure everything else here.</p>
  </header>

  {#if loading}
    <div class="aa-msg">Loading…</div>
  {:else if err && !cfg}
    <div class="aa-err">{err}</div>
  {:else if cfg}
    <div class="aa-bar">
      <label class="aa-switch"><input type="checkbox" bind:checked={advanced} /> Advanced (raw JSON)</label>
      <div class="aa-spacer"></div>
      {#if err}<span class="aa-err-inline">{err}</span>{/if}
      {#if ok}<span class="aa-ok-inline">{ok}</span>{/if}
      <button class="aa-btn aa-btn--primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
    </div>

    {#if advanced}
      <textarea class="aa-json" bind:value={raw} spellcheck="false"></textarea>
    {:else}
      <!-- General -->
      <section class="aa-card">
        <h2>General</h2>
        <label class="aa-row"><input type="checkbox" bind:checked={cfg.local_enabled} /> Allow local username/password login</label>
        <label class="aa-row"><input type="checkbox" bind:checked={cfg.merge_by_email} /> Merge SSO/LDAP users with existing local accounts by email</label>
        <label class="aa-row aa-col">Trusted reverse-proxy email header (advanced; leave blank unless behind a trusted SSO gateway)
          <input class="aa-input" bind:value={cfg.trusted_email_header} placeholder="(disabled)" /></label>
      </section>

      <!-- LDAP -->
      <section class="aa-card">
        <h2>LDAP / Active Directory</h2>
        <label class="aa-row"><input type="checkbox" bind:checked={cfg.ldap.enabled} /> Enable LDAP login</label>
        <div class="aa-grid">
          <label class="aa-col">Server label<input class="aa-input" bind:value={cfg.ldap.label} /></label>
          <label class="aa-col">Host (no protocol)<input class="aa-input" bind:value={cfg.ldap.host} placeholder="ldap.example.com" /></label>
          <label class="aa-col">Port<input class="aa-input" type="number" bind:value={cfg.ldap.port} /></label>
          <label class="aa-col aa-chk"><input type="checkbox" bind:checked={cfg.ldap.use_tls} /> Use TLS/LDAPS</label>
          <label class="aa-col aa-chk"><input type="checkbox" bind:checked={cfg.ldap.validate_cert} /> Validate certificate</label>
          <label class="aa-col">Bind DN (service account)<input class="aa-input" bind:value={cfg.ldap.app_dn} placeholder="cn=svc,dc=ex,dc=com" /></label>
          <label class="aa-col">Search base<input class="aa-input" bind:value={cfg.ldap.search_base} placeholder="ou=users,dc=ex,dc=com" /></label>
          <label class="aa-col">Username attribute<input class="aa-input" bind:value={cfg.ldap.uid_attr} /></label>
          <label class="aa-col">Mail attribute<input class="aa-input" bind:value={cfg.ldap.mail_attr} /></label>
          <label class="aa-col">Group attribute<input class="aa-input" bind:value={cfg.ldap.group_attr} /></label>
          <label class="aa-col">Extra search filter<input class="aa-input" bind:value={cfg.ldap.search_filter} placeholder="(objectClass=person)" /></label>
        </div>
        <p class="aa-hint">Bind password = env <code>LDAP_APP_PASSWORD</code>. Group→branch mapping (drives Shop-Counter site binding) is editable in Advanced JSON under <code>ldap.group_to_site</code>.</p>
      </section>

      <!-- OIDC -->
      <section class="aa-card">
        <h2>OIDC / SSO providers</h2>
        {#each cfg.oidc || [] as p, i}
          <div class="aa-prov">
            <div class="aa-grid">
              <label class="aa-col">ID (url slug)<input class="aa-input" bind:value={p.id} /></label>
              <label class="aa-col">Display name<input class="aa-input" bind:value={p.name} /></label>
              <label class="aa-col aa-wide">Issuer URL<input class="aa-input" bind:value={p.issuer} placeholder="https://kc.example.com/realms/myrealm" /></label>
              <label class="aa-col">Client ID<input class="aa-input" bind:value={p.client_id} /></label>
              <label class="aa-col">Scopes<input class="aa-input" bind:value={p.scopes} /></label>
              <label class="aa-col">Email claim<input class="aa-input" bind:value={p.email_claim} /></label>
              <label class="aa-col">Username claim<input class="aa-input" bind:value={p.username_claim} /></label>
              <label class="aa-col">Roles claim<input class="aa-input" bind:value={p.roles_claim} /></label>
              <label class="aa-col">Groups claim<input class="aa-input" bind:value={p.groups_claim} /></label>
            </div>
            <p class="aa-hint">Client secret = env <code>OIDC_{(p.id||'').toUpperCase()}_CLIENT_SECRET</code> (or <code>OAUTH_CLIENT_SECRET</code>). Allowed-roles gate + group→branch map: edit in Advanced JSON.</p>
            <button class="aa-btn aa-btn--danger" onclick={() => removeOidc(i)}>Remove provider</button>
          </div>
        {/each}
        <button class="aa-btn" onclick={addOidc}>+ Add OIDC provider</button>
      </section>
    {/if}

    {#if note}<p class="aa-note">{note}</p>{/if}
  {/if}
</div>

<style>
  .aa-wrap { max-width: 920px; margin: 0 auto; padding: 32px 20px 80px; color: var(--pw-ink, #1a1a1a); }
  .aa-head h1 { font-size: 24px; font-weight: 900; margin: 0 0 6px; }
  .aa-sub { font-size: 13px; opacity: 0.7; margin: 0 0 20px; }
  .aa-sub code, .aa-hint code, .aa-prov code { background: rgba(0,0,0,0.06); padding: 1px 5px; border-radius: 4px; font-size: 11px; }
  .aa-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; position: sticky; top: 0; background: var(--pw-bg, #fff); padding: 8px 0; z-index: 2; }
  .aa-spacer { flex: 1; }
  .aa-switch { font-size: 12px; display: flex; gap: 6px; align-items: center; }
  .aa-card { border: 1px solid var(--pw-border, #e3e3e0); border-radius: 10px; padding: 18px; margin-bottom: 18px; }
  .aa-card h2 { font-size: 14px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 14px; }
  .aa-row { display: flex; gap: 8px; align-items: center; font-size: 13px; margin-bottom: 10px; }
  .aa-col { display: flex; flex-direction: column; gap: 4px; font-size: 11px; font-weight: 600; opacity: 0.85; }
  .aa-col.aa-chk { flex-direction: row; align-items: center; gap: 6px; }
  .aa-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .aa-wide { grid-column: 1 / -1; }
  .aa-input { border: 1px solid var(--pw-border, #d8d8d4); border-radius: 6px; padding: 7px 9px; font-size: 13px; font-weight: 400; background: var(--pw-bg, #fff); color: var(--pw-ink, #1a1a1a); }
  .aa-json { width: 100%; min-height: 460px; font-family: ui-monospace, monospace; font-size: 12px; border: 1px solid var(--pw-border, #d8d8d4); border-radius: 8px; padding: 14px; }
  .aa-prov { border: 1px dashed var(--pw-border, #d8d8d4); border-radius: 8px; padding: 14px; margin-bottom: 14px; }
  .aa-hint { font-size: 11px; opacity: 0.65; margin: 10px 0; }
  .aa-btn { border: 1px solid var(--pw-border, #d8d8d4); background: var(--pw-bg, #fff); color: var(--pw-ink, #1a1a1a); border-radius: 7px; padding: 8px 14px; font-size: 12px; font-weight: 700; cursor: pointer; }
  .aa-btn--primary { background: var(--pw-accent, #c96342); color: #fff; border-color: transparent; }
  .aa-btn--danger { color: var(--pw-error, #c0392b); border-color: var(--pw-error, #c0392b); margin-top: 8px; }
  .aa-msg, .aa-err, .aa-note { font-size: 13px; padding: 12px 0; }
  .aa-err, .aa-err-inline { color: var(--pw-error, #c0392b); }
  .aa-ok-inline { color: var(--pw-accent, #2e7d32); font-size: 12px; }
  .aa-err-inline, .aa-ok-inline { font-size: 12px; }
  .aa-note { opacity: 0.6; font-size: 11px; }
</style>
