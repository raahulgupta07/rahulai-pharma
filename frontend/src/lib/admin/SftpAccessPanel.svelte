<script lang="ts">
  import Icon from '$lib/Icon.svelte';
  import { onMount } from 'svelte';

  let { embedded = false } = $props();

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  function jh(): Record<string, string> { return { 'Content-Type': 'application/json', ...authHeaders() }; }

  type SUser = {
    username: string; status: number; auth: string; mode: string;
    feeds_table: string; ingest_action: string; quota_mb: number;
    max_sessions: number; expiration_date: number; allowed_extensions: string[];
    last_login: number; num_keys: number;
  };

  let status = $state<any>({ configured: false, reachable: false, user_count: 0, sftp_port: '2222', host: '' });
  let users = $state<SUser[]>([]);
  let loading = $state(true);
  let editing = $state<any>(null);
  let busy = $state(false);
  let secret = $state<{ kind: string; value: string; username: string } | null>(null);
  let browse = $state<{ username: string; files: any[] } | null>(null);
  let ingestLog = $state<any[]>([]);

  function blankForm() {
    return {
      isNew: true, username: '', auth: 'key', public_key: '', password: '',
      mode: 'drop', feeds_table: '', ingest_action: 'replace',
      allowed_extensions: '.csv,.xlsx,.parquet',
      quota_mb: 500, max_sessions: 2, expiration_date: 0,
      _genPriv: '', // holds generated private key to show once
    };
  }

  async function loadStatus() {
    try { const r = await fetch('/api/admin/sftp/status', { headers: authHeaders() }); if (r.ok) status = await r.json(); } catch {}
  }
  async function load() {
    loading = true;
    await loadStatus();
    try {
      const r = await fetch('/api/admin/sftp/users', { headers: authHeaders() });
      if (r.ok) users = (await r.json()).users || [];
    } finally { loading = false; }
    loadIngestLog();
  }
  async function loadIngestLog() {
    try { const r = await fetch('/api/admin/sftp/ingest-log?limit=30', { headers: authHeaders() }); if (r.ok) ingestLog = (await r.json()).log || []; } catch {}
  }
  onMount(load);

  function newUser() { secret = null; editing = blankForm(); }
  function editUser(u: SUser) {
    secret = null;
    editing = {
      isNew: false, username: u.username, auth: u.auth, public_key: '', password: '',
      mode: u.mode, feeds_table: u.feeds_table, ingest_action: u.ingest_action,
      allowed_extensions: (u.allowed_extensions || []).map((e: string) => e.replace(/^\*/, '')).join(','),
      quota_mb: u.quota_mb, max_sessions: u.max_sessions, expiration_date: u.expiration_date, _genPriv: '',
    };
  }

  async function genKey() {
    busy = true;
    try {
      const r = await fetch('/api/admin/sftp/keygen', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); editing.public_key = d.public_key; editing._genPriv = d.private_key; }
    } finally { busy = false; }
  }

  function extList(): string[] {
    return String(editing.allowed_extensions || '').split(',').map((s: string) => s.trim()).filter(Boolean);
  }

  async function save() {
    busy = true;
    try {
      if (editing.isNew) {
        const body = {
          username: editing.username.trim(), auth: editing.auth,
          public_key: editing.public_key || null, password: editing.password || null,
          mode: editing.mode, feeds_table: editing.feeds_table.trim(),
          ingest_action: editing.ingest_action, allowed_extensions: extList(),
          quota_mb: Number(editing.quota_mb) || 0, max_sessions: Number(editing.max_sessions) || 0,
          expiration_date: Number(editing.expiration_date) || 0,
        };
        const r = await fetch('/api/admin/sftp/users', { method: 'POST', headers: jh(), body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { alert(d.detail || 'Create failed'); return; }
        // show secret(s) once
        if (editing._genPriv) secret = { kind: 'key', value: editing._genPriv, username: body.username };
        else if (d.generated_password) secret = { kind: 'password', value: d.generated_password, username: body.username };
        editing = null;
        await load();
      } else {
        const body: any = {
          mode: editing.mode, feeds_table: editing.feeds_table.trim(),
          ingest_action: editing.ingest_action, quota_mb: Number(editing.quota_mb) || 0,
          max_sessions: Number(editing.max_sessions) || 0, expiration_date: Number(editing.expiration_date) || 0,
        };
        const r = await fetch(`/api/admin/sftp/users/${encodeURIComponent(editing.username)}`,
          { method: 'PATCH', headers: jh(), body: JSON.stringify(body) });
        if (!r.ok) { alert('Update failed'); return; }
        editing = null;
        await load();
      }
    } finally { busy = false; }
  }

  async function resetPassword(u: SUser) {
    if (!confirm(`Generate a NEW password for "${u.username}"? The old one stops working.`)) return;
    busy = true;
    try {
      const r = await fetch(`/api/admin/sftp/users/${encodeURIComponent(u.username)}`,
        { method: 'PATCH', headers: jh(), body: JSON.stringify({ new_password: '' }) });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.generated_password) secret = { kind: 'password', value: d.generated_password, username: u.username };
    } finally { busy = false; }
  }

  async function rotateKey(u: SUser) {
    if (!confirm(`Generate a NEW key for "${u.username}"? The old key stops working.`)) return;
    busy = true;
    try {
      const g = await fetch('/api/admin/sftp/keygen', { headers: authHeaders() });
      if (!g.ok) { alert('keygen failed'); return; }
      const k = await g.json();
      const r = await fetch(`/api/admin/sftp/users/${encodeURIComponent(u.username)}`,
        { method: 'PATCH', headers: jh(), body: JSON.stringify({ public_key: k.public_key }) });
      if (r.ok) secret = { kind: 'key', value: k.private_key, username: u.username };
    } finally { busy = false; }
  }

  async function toggle(u: SUser) {
    busy = true;
    try {
      await fetch(`/api/admin/sftp/users/${encodeURIComponent(u.username)}`,
        { method: 'PATCH', headers: jh(), body: JSON.stringify({ status: u.status ? 0 : 1 }) });
      await load();
    } finally { busy = false; }
  }

  async function remove(u: SUser) {
    if (!confirm(`Delete SFTP user "${u.username}"? Their dropped files stay; only access is removed.`)) return;
    busy = true;
    try {
      await fetch(`/api/admin/sftp/users/${encodeURIComponent(u.username)}`, { method: 'DELETE', headers: authHeaders() });
      await load();
    } finally { busy = false; }
  }

  let purgeDays = $state(7);
  async function purgeOld() {
    if (!confirm(`Delete all dropped files older than ${purgeDays} day(s) across every SFTP user? Already-ingested data in tables is unaffected.`)) return;
    busy = true;
    try {
      const r = await fetch(`/api/admin/sftp/retention-all?hours=${purgeDays * 24}`, { method: 'POST', headers: jh() });
      const d = await r.json().catch(() => ({}));
      if (r.ok) alert(`Purge started for ${d.users_swept ?? 0} user folder(s).`);
      else alert('Purge failed');
    } finally { busy = false; }
  }

  async function openBrowse(u: SUser) {
    const r = await fetch(`/api/admin/sftp/files?username=${encodeURIComponent(u.username)}`, { headers: authHeaders() });
    if (r.ok) { const d = await r.json(); browse = { username: u.username, files: d.files || [] }; }
  }

  function copy(v: string) { try { navigator.clipboard.writeText(v); } catch {} }
  function fmtBytes(b: number) { if (b < 1024) return b + ' B'; if (b < 1048576) return (b / 1024).toFixed(0) + ' KB'; return (b / 1048576).toFixed(1) + ' MB'; }
  function fmtTime(t: number) { return t ? new Date(t * 1000).toLocaleString() : '—'; }
  function fmtExpiry(ms: number) { return ms ? new Date(ms).toLocaleDateString() : 'never'; }
</script>

<div class="sf" class:embedded>
  <div class="sf-head">
    <div>
      <div class="sf-title"><Icon name="upload" size={16} /> SFTP ACCESS</div>
      <div class="sf-sub">
        Pharmacies <Icon name="arrow-right" size={14} /> drop files over SFTP <Icon name="arrow-right" size={14} /> auto-ingest.
        Each user is jailed to their own folder — no access to the app.
      </div>
    </div>
    <button class="sq" onclick={newUser} disabled={!status.configured}>+ ADD SFTP USER</button>
  </div>

  <div class="sf-status">
    <span class="dot {status.reachable ? 'ok' : (status.configured ? 'err' : 'idle')}"></span>
    {#if !status.configured}
      SFTP server not configured (SFTPGO_ADMIN_PASS unset)
    {:else if status.reachable}
      server online · port {status.sftp_port} · {status.user_count} user{status.user_count === 1 ? '' : 's'}
      {#if status.host}· host {status.host}{/if}
    {:else}
      server unreachable (cp-sftpgo down?)
    {/if}
    {#if status.reachable}
      <span class="sf-purge">
        Purge drops older than
        <select bind:value={purgeDays}><option value={1}>1d</option><option value={7}>7d</option><option value={30}>30d</option><option value={90}>90d</option></select>
        <button class="sq ghost xs" onclick={purgeOld} disabled={busy}>Purge now</button>
      </span>
    {/if}
  </div>

  <div class="sf-hr"></div>

  {#if loading}
    <div class="muted">LOADING…</div>
  {:else if !users.length}
    <div class="muted">No SFTP users yet. Click <b>+ ADD SFTP USER</b> to give a pharmacy a drop folder.</div>
  {:else}
    <table class="sf-tbl">
      <thead>
        <tr><th>USER</th><th>AUTH</th><th>PERM</th><th>FEEDS TABLE</th><th>QUOTA</th><th>EXPIRES</th><th>LAST LOGIN</th><th class="ar">ACTIONS</th></tr>
      </thead>
      <tbody>
        {#each users as u (u.username)}
          <tr class:off={!u.status}>
            <td class="strong">{u.username}{#if !u.status}<span class="muted2"> · disabled</span>{/if}</td>
            <td>{#if u.auth === 'key'}<Icon name="key" size={13} /> key{:else}<Icon name="lock" size={13} /> pass{/if}</td>
            <td><span class="pill {u.mode}">{u.mode === 'drop' ? 'upload-only' : 'read+write'}</span></td>
            <td class="mono">{u.feeds_table || '—'} <span class="muted2">{u.feeds_table ? u.ingest_action : ''}</span></td>
            <td>{u.quota_mb ? u.quota_mb + ' MB' : '∞'}</td>
            <td class="muted2">{fmtExpiry(u.expiration_date)}</td>
            <td class="muted2">{fmtTime(u.last_login)}</td>
            <td class="acts">
              <button class="ico" title="Browse drops" onclick={() => openBrowse(u)}><Icon name="folder" size={15} /></button>
              {#if u.auth === 'password'}
                <button class="ico" title="Reset password" onclick={() => resetPassword(u)} disabled={busy}><Icon name="refresh" size={15} /></button>
              {:else}
                <button class="ico" title="Rotate key" onclick={() => rotateKey(u)} disabled={busy}><Icon name="refresh" size={15} /></button>
              {/if}
              <button class="ico" title="Edit" onclick={() => editUser(u)}><Icon name="edit" size={15} /></button>
              <button class="ico" title={u.status ? 'Disable' : 'Enable'} onclick={() => toggle(u)} disabled={busy}><Icon name={u.status ? 'pause' : 'play'} size={15} /></button>
              <button class="ico danger" title="Delete" onclick={() => remove(u)} disabled={busy}><Icon name="trash" size={15} /></button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  {#if ingestLog.length}
    <div class="sf-hr"></div>
    <div class="sf-feedhead"><Icon name="activity" size={14} /> RECENT INGESTS <button class="sq ghost xs" onclick={loadIngestLog}>Refresh</button></div>
    <table class="sf-tbl">
      <thead><tr><th>WHEN</th><th>USER</th><th>FILE</th><th>TABLE</th><th>STATUS</th><th>ROWS</th></tr></thead>
      <tbody>
        {#each ingestLog as l}
          <tr>
            <td class="muted2">{l.ts}</td>
            <td>{l.username}</td>
            <td class="mono">{l.rel_path}</td>
            <td class="mono">{l.table_name || '—'}</td>
            <td><span class="st {l.status}">{l.status}</span>{#if l.status !== 'ok' && l.message}<span class="muted2" title={l.message}> · {l.message.slice(0, 40)}</span>{/if}</td>
            <td>{l.rows_loaded ?? '—'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<!-- secret reveal (shown ONCE) -->
{#if secret}
  <div class="modal-bg" role="presentation" onclick={() => (secret = null)}>
    <div class="modal sm" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="modal-h"><Icon name="key" size={16} /> {secret.kind === 'key' ? 'PRIVATE KEY' : 'PASSWORD'} FOR "{secret.username}"</div>
      <div class="modal-body">
        <div class="warn"><Icon name="alert-triangle" size={14} /> Shown ONCE. Copy it now and hand it to the pharmacy over a secure channel. It cannot be retrieved later.</div>
        <textarea class="secret-box" readonly rows={secret.kind === 'key' ? 8 : 1}>{secret.value}</textarea>
        <div class="sf-conn">
          Connect: <span class="mono">sftp -P {status.sftp_port} {secret.username}@{status.host || 'YOUR-SERVER'}</span>
        </div>
      </div>
      <div class="modal-f">
        <button class="sq ghost" onclick={() => copy(secret.value)}>Copy</button>
        <button class="sq" onclick={() => (secret = null)}>Done</button>
      </div>
    </div>
  </div>
{/if}

<!-- add / edit modal -->
{#if editing}
  <div class="modal-bg" role="presentation" onclick={() => (editing = null)}>
    <div class="modal" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="modal-h"><Icon name="upload" size={16} /> {editing.isNew ? 'ADD' : 'EDIT'} SFTP USER</div>
      <div class="modal-body">
        {#if editing.isNew}
          <label class="fl">USERNAME</label>
          <input class="fin" bind:value={editing.username} placeholder="acme_pharmacy" />

          <label class="fl">AUTH METHOD</label>
          <div class="seg">
            <button class:on={editing.auth === 'key'} onclick={() => (editing.auth = 'key')}>SSH key (recommended)</button>
            <button class:on={editing.auth === 'password'} onclick={() => (editing.auth = 'password')}>Password</button>
          </div>
          {#if editing.auth === 'key'}
            <div class="rule-row hdr2">
              <label class="fl">PUBLIC KEY <span class="opt">(paste theirs, or generate a pair)</span></label>
              <button class="sq ghost xs" onclick={genKey} disabled={busy}>Generate keypair</button>
            </div>
            <textarea class="fin ta" bind:value={editing.public_key} placeholder="ssh-ed25519 AAAA…"></textarea>
            {#if editing._genPriv}<div class="hint ok-hint"><Icon name="check" size={13} /> Keypair generated — the private key will be shown once after you create the user.</div>{/if}
          {:else}
            <label class="fl">PASSWORD <span class="opt">(blank = auto-generate a strong one)</span></label>
            <input class="fin" type="text" autocomplete="off" bind:value={editing.password} placeholder="leave blank to generate" />
          {/if}
        {:else}
          <div class="hint">Editing <b>{editing.username}</b>. Use the row buttons to reset password / rotate key.</div>
        {/if}

        <label class="fl">PERMISSION</label>
        <div class="seg">
          <button class:on={editing.mode === 'drop'} onclick={() => (editing.mode = 'drop')}>Upload-only (dropbox)</button>
          <button class:on={editing.mode === 'readwrite'} onclick={() => (editing.mode = 'readwrite')}>Read + write</button>
        </div>
        <div class="hint">Upload-only = they can see their own folder and drop files, but cannot download or delete. Safest.</div>

        <div class="two">
          <div><label class="fl">FEEDS TABLE <span class="opt">(which table their files load into)</span></label>
            <input class="fin" bind:value={editing.feeds_table} placeholder="balance_stock" /></div>
          <div><label class="fl">ON NEW FILE</label>
            <select class="fin" bind:value={editing.ingest_action}>
              <option value="replace">replace (full refresh)</option>
              <option value="append">append</option>
              <option value="upsert">upsert</option>
            </select></div>
        </div>

        <label class="fl">ALLOWED FILE TYPES</label>
        <input class="fin" bind:value={editing.allowed_extensions} placeholder=".csv,.xlsx,.parquet" />

        <div class="two">
          <div><label class="fl">QUOTA (MB) <span class="opt">(0 = unlimited)</span></label>
            <input class="fin" type="number" bind:value={editing.quota_mb} /></div>
          <div><label class="fl">MAX SESSIONS</label>
            <input class="fin" type="number" bind:value={editing.max_sessions} /></div>
        </div>

        <div class="hint">Files land in the drop folder. Auto-ingest into the table runs via the Phase-2 watcher.</div>
      </div>
      <div class="modal-f">
        <button class="sq ghost" onclick={() => (editing = null)}>Cancel</button>
        <button class="sq" onclick={save} disabled={busy || (editing.isNew && !editing.username)}>{busy ? '…' : (editing.isNew ? 'Create user' : 'Save')}</button>
      </div>
    </div>
  </div>
{/if}

<!-- browse drops drawer -->
{#if browse}
  <div class="modal-bg" role="presentation" onclick={() => (browse = null)}>
    <div class="modal" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="modal-h"><Icon name="folder" size={16} /> DROPS — {browse.username}</div>
      <div class="modal-body">
        {#if !browse.files.length}
          <div class="muted">No files dropped yet.</div>
        {:else}
          <table class="sf-tbl">
            <thead><tr><th>FILE</th><th>SIZE</th><th>DROPPED</th></tr></thead>
            <tbody>
              {#each browse.files as f}
                <tr><td class="mono">{f.rel}</td><td>{fmtBytes(f.size)}</td><td class="muted2">{fmtTime(f.mtime)}</td></tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </div>
      <div class="modal-f"><button class="sq" onclick={() => (browse = null)}>Close</button></div>
    </div>
  </div>
{/if}

<style>
  .sf { font-size: 13px; }
  .sf-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  .sf-title { font-weight: 800; letter-spacing: 0.04em; display: flex; align-items: center; gap: 7px; }
  .sf-sub { color: var(--pw-muted); margin-top: 4px; max-width: 640px; display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  .sf-status { margin-top: 12px; color: var(--pw-dim); display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
  .sf-purge { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; }
  .sf-purge select { padding: 3px 6px; border: 1px solid var(--pw-border, #ddd); border-radius: 6px; font-size: 11px; background: var(--pw-bg, #fff); }
  .sf-hr { height: 1px; background: var(--pw-border, #e7e4dd); margin: 12px 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: #bbb; }
  .dot.ok { background: #3f8f5f; } .dot.err { background: #c0492f; } .dot.idle { background: #bbb; }
  .sq { background: var(--pw-accent, #c2683f); color: #fff; border: none; border-radius: var(--pw-button, 8px); padding: 8px 14px; font-weight: 700; font-size: 12px; cursor: pointer; }
  .sq:disabled { opacity: 0.5; cursor: not-allowed; }
  .sq.ghost { background: transparent; color: var(--pw-text, #2b2a27); border: 1px solid var(--pw-border, #ddd); }
  .sq.xs { padding: 4px 9px; font-size: 11px; }
  .sf-tbl { width: 100%; border-collapse: collapse; margin-top: 8px; }
  .sf-tbl th { text-align: left; font-size: 10.5px; letter-spacing: 0.05em; color: var(--pw-muted); padding: 6px 8px; border-bottom: 1px solid var(--pw-border, #eee); }
  .sf-tbl td { padding: 7px 8px; border-bottom: 1px solid var(--pw-border, #f1efea); vertical-align: middle; }
  .sf-tbl tr.off { opacity: 0.5; }
  .ar { text-align: right; } .acts { text-align: right; white-space: nowrap; }
  .strong { font-weight: 700; } .mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .muted { color: var(--pw-muted); padding: 12px 0; } .muted2 { color: var(--pw-dim); font-size: 11.5px; }
  .pill { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .pill.drop { background: #e6f0e9; color: #2f6f4a; } .pill.readwrite { background: #f0e9da; color: #8a6a2a; }
  .ico { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; cursor: pointer; color: var(--pw-text, #444); }
  .ico:hover { background: var(--pw-bg-alt, #f3f1ec); } .ico.danger:hover { background: #fbe9e6; color: #c0492f; }
  .ico:disabled { opacity: 0.4; cursor: not-allowed; }
  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 60; }
  .modal { background: var(--pw-bg, #fff); border-radius: 14px; width: 560px; max-width: 92vw; max-height: 88vh; overflow: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.25); }
  .modal.sm { width: 480px; }
  .modal-h { padding: 14px 18px; font-weight: 800; letter-spacing: 0.03em; border-bottom: 1px solid var(--pw-border, #eee); display: flex; align-items: center; gap: 8px; }
  .modal-body { padding: 16px 18px; }
  .modal-f { padding: 12px 18px; border-top: 1px solid var(--pw-border, #eee); display: flex; justify-content: flex-end; gap: 8px; }
  .fl { display: block; font-size: 10.5px; letter-spacing: 0.05em; color: var(--pw-muted); margin: 12px 0 4px; font-weight: 700; }
  .opt { color: var(--pw-dim); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fin { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid var(--pw-border, #ddd); border-radius: 8px; font-size: 13px; background: var(--pw-bg, #fff); color: var(--pw-text, #222); }
  .fin.ta, .ta { min-height: 64px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; resize: vertical; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .seg { display: flex; gap: 6px; }
  .seg button { flex: 1; padding: 7px 10px; border: 1px solid var(--pw-border, #ddd); background: var(--pw-bg, #fff); border-radius: 8px; cursor: pointer; font-size: 12px; color: var(--pw-text, #333); }
  .seg button.on { background: var(--pw-accent, #c2683f); color: #fff; border-color: var(--pw-accent, #c2683f); }
  .hint { color: var(--pw-dim); font-size: 11.5px; margin-top: 6px; }
  .ok-hint { color: #2f6f4a; display: flex; align-items: center; gap: 5px; }
  .rule-row.hdr2 { display: flex; justify-content: space-between; align-items: center; }
  .warn { background: #fbe9e6; color: #9a3a2a; padding: 8px 10px; border-radius: 8px; font-size: 11.5px; display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }
  .secret-box { width: 100%; box-sizing: border-box; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; padding: 8px 10px; border: 1px solid var(--pw-border, #ddd); border-radius: 8px; resize: vertical; background: var(--pw-bg-alt, #f7f5f0); }
  .sf-conn { margin-top: 8px; font-size: 11.5px; color: var(--pw-dim); }
  .sf-feedhead { display: flex; align-items: center; gap: 7px; font-weight: 700; font-size: 11.5px; letter-spacing: 0.04em; color: var(--pw-muted); margin-top: 6px; }
  .st { padding: 1px 7px; border-radius: 999px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; }
  .st.ok { background: #e6f0e9; color: #2f6f4a; }
  .st.error { background: #fbe9e6; color: #c0492f; }
  .st.held { background: #f7ecd6; color: #9a6a18; }
  .st.skipped { background: #eee; color: #777; }
</style>
