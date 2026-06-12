<script lang="ts">
  import { onMount } from 'svelte';

  function authHeaders(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  function j(): Record<string, string> { return { 'Content-Type': 'application/json', ...authHeaders() }; }

  type Rule = { pattern: string; table: string; action: string };
  type Source = {
    id: number; name: string; bucket: string; prefix: string; region: string;
    endpoint_url: string | null; file_map: Rule[]; schedule_seconds: number;
    retrain_after: boolean; enabled: boolean; last_sync_at: string | null;
    last_status: string | null; has_credentials: boolean;
  };

  let sources = $state<Source[]>([]);
  let boto3 = $state(true);
  let loading = $state(true);
  let editing = $state<any>(null);       // the form model (null = closed)
  let detail = $state<any>(null);        // expanded detail/log
  let testResult = $state<string>('');
  let busy = $state(false);

  function blankForm() {
    return {
      id: 0, name: '', bucket: '', prefix: '', region: 'us-east-1', endpoint_url: '',
      access_key: '', secret_key: '',
      file_map: [{ pattern: '', table: '', action: 'replace' }] as Rule[],
      schedule_seconds: 300, retrain_after: true, enabled: false,
    };
  }

  async function load() {
    loading = true;
    try {
      const r = await fetch('/api/s3/sources', { headers: authHeaders() });
      if (r.ok) { const d = await r.json(); sources = d.sources || []; boto3 = d.boto3_available; }
    } finally { loading = false; }
  }
  onMount(load);

  function newSource() { testResult = ''; editing = blankForm(); }
  function editSource(s: Source) {
    testResult = '';
    editing = {
      id: s.id, name: s.name, bucket: s.bucket, prefix: s.prefix, region: s.region,
      endpoint_url: s.endpoint_url || '', access_key: '', secret_key: '',
      file_map: s.file_map?.length ? JSON.parse(JSON.stringify(s.file_map)) : [{ pattern: '', table: '', action: 'replace' }],
      schedule_seconds: s.schedule_seconds, retrain_after: s.retrain_after, enabled: s.enabled,
    };
  }
  function addRule() { editing.file_map = [...editing.file_map, { pattern: '', table: '', action: 'replace' }]; }
  function delRule(i: number) { editing.file_map = editing.file_map.filter((_: any, k: number) => k !== i); }

  async function save() {
    busy = true;
    try {
      const body = {
        name: editing.name, bucket: editing.bucket, prefix: editing.prefix,
        region: editing.region, endpoint_url: editing.endpoint_url || null,
        access_key: editing.access_key || null, secret_key: editing.secret_key || null,
        file_map: editing.file_map.filter((r: Rule) => r.pattern && r.table),
        schedule_seconds: Number(editing.schedule_seconds) || 300,
        retrain_after: !!editing.retrain_after, enabled: !!editing.enabled,
      };
      const url = editing.id ? `/api/s3/sources/${editing.id}` : '/api/s3/sources';
      const method = editing.id ? 'PUT' : 'POST';
      const r = await fetch(url, { method, headers: j(), body: JSON.stringify(body) });
      if (r.ok) { editing = null; await load(); }
      else { testResult = 'Save failed: ' + (await r.text()); }
    } finally { busy = false; }
  }

  async function testConn() {
    busy = true; testResult = 'Testing…';
    try {
      // must be a saved source to test (needs stored creds). Save first if new.
      if (!editing.id) { testResult = 'Save the source first, then Test.'; return; }
      const r = await fetch(`/api/s3/sources/${editing.id}/test`, { method: 'POST', headers: authHeaders() });
      const d = await r.json();
      testResult = d.ok
        ? `✓ Connected — ${d.objects} object(s), ${d.matched} match your patterns. Sample: ${(d.sample || []).join(', ')}`
        : `✗ ${d.error}`;
    } finally { busy = false; }
  }

  async function syncNow(id: number, force = false) {
    busy = true;
    try {
      await fetch(`/api/s3/sources/${id}/sync?force=${force ? 1 : 0}`, { method: 'POST', headers: authHeaders() });
      setTimeout(() => { load(); if (detail?.id === id) openDetail(id); }, 1500);
    } finally { busy = false; }
  }

  async function remove(id: number) {
    if (!confirm('Delete this S3 source? (Data already loaded stays; only the sync config is removed.)')) return;
    await fetch(`/api/s3/sources/${id}`, { method: 'DELETE', headers: authHeaders() });
    if (detail?.id === id) detail = null;
    await load();
  }

  async function openDetail(id: number) {
    const r = await fetch(`/api/s3/sources/${id}`, { headers: authHeaders() });
    if (r.ok) detail = await r.json();
  }

  function fmtSched(s: number) {
    if (s % 86400 === 0) return `${s / 86400}d`;
    if (s % 3600 === 0) return `${s / 3600}h`;
    if (s % 60 === 0) return `${s / 60}m`;
    return `${s}s`;
  }
</script>

<div class="s3">
  <div class="s3-head">
    <div>
      <h1>S3 Sync</h1>
      <p class="sub">Auto-pull data files from an S3 bucket, replace the matching tables, and retrain — on a schedule.</p>
    </div>
    <button class="btn primary" onclick={newSource}>＋ Add S3 source</button>
  </div>

  {#if !boto3}
    <div class="warn">⚠ S3 support library not installed in this build (boto3 missing). Rebuild the image after adding boto3 to requirements.</div>
  {/if}

  {#if loading}
    <div class="muted">Loading…</div>
  {:else if !sources.length}
    <div class="empty">No S3 sources yet. Click <b>Add S3 source</b> to connect a bucket.</div>
  {:else}
    <div class="grid">
      {#each sources as s (s.id)}
        <div class="card">
          <div class="card-top">
            <div class="name">{s.name}</div>
            <span class="pill {s.enabled ? 'on' : 'off'}">{s.enabled ? 'Auto-sync ON' : 'OFF'}</span>
          </div>
          <div class="meta">s3://{s.bucket}/{s.prefix}</div>
          <div class="meta small">
            {s.file_map?.length || 0} file rule(s) · every {fmtSched(s.schedule_seconds)} · {s.retrain_after ? 'retrains after' : 'no retrain'}
          </div>
          <div class="status">
            <span class="dot {s.last_status === 'ok' ? 'ok' : s.last_status === 'error' ? 'err' : s.last_status === 'running' ? 'run' : 'idle'}"></span>
            {s.last_status || 'never run'}{s.last_sync_at ? ' · ' + new Date(s.last_sync_at).toLocaleString() : ''}
            {#if !s.has_credentials}<span class="nocred"> · no credentials</span>{/if}
          </div>
          <div class="row-actions">
            <button class="btn sm" onclick={() => syncNow(s.id)} disabled={busy}>Sync now</button>
            <button class="btn sm ghost" onclick={() => syncNow(s.id, true)} disabled={busy} title="Re-pull every file even if unchanged">Force</button>
            <button class="btn sm ghost" onclick={() => openDetail(s.id)}>Log</button>
            <button class="btn sm ghost" onclick={() => editSource(s)}>Edit</button>
            <button class="btn sm danger" onclick={() => remove(s.id)}>Delete</button>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  {#if detail}
    <div class="detail">
      <div class="detail-head"><b>{detail.name}</b> — last run log <button class="x" onclick={() => detail = null}>✕</button></div>
      <pre class="log">{detail.last_log || '(no log yet)'}</pre>
      {#if detail.objects?.length}
        <div class="objs-title">Synced objects</div>
        <table class="objs">
          <thead><tr><th>Object key</th><th>Table</th><th>Rows</th><th>When</th></tr></thead>
          <tbody>
            {#each detail.objects as o}
              <tr><td>{o.key}</td><td>{o.table}</td><td>{o.rows ?? '—'}</td><td>{o.synced_at ? new Date(o.synced_at).toLocaleString() : ''}</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  {/if}
</div>

{#if editing}
  <div class="modal-bg" onclick={() => editing = null} role="presentation">
    <div class="modal" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
      <h2>{editing.id ? 'Edit' : 'Add'} S3 source</h2>
      <label>Name <input bind:value={editing.name} placeholder="Pharma exports" /></label>
      <div class="two">
        <label>Bucket <input bind:value={editing.bucket} placeholder="my-pharma-data" /></label>
        <label>Prefix (folder) <input bind:value={editing.prefix} placeholder="exports/" /></label>
      </div>
      <div class="two">
        <label>Region <input bind:value={editing.region} placeholder="ap-southeast-1" /></label>
        <label>Endpoint URL (optional, MinIO) <input bind:value={editing.endpoint_url} placeholder="" /></label>
      </div>
      <div class="two">
        <label>Access Key {#if editing.id}<span class="hint">(leave blank to keep)</span>{/if}<input bind:value={editing.access_key} autocomplete="off" /></label>
        <label>Secret Key {#if editing.id}<span class="hint">(leave blank to keep)</span>{/if}<input type="password" bind:value={editing.secret_key} autocomplete="off" /></label>
      </div>

      <div class="rules">
        <div class="rules-head">File → table rules <button class="btn sm ghost" onclick={addRule}>＋ rule</button></div>
        <div class="rule head"><span>File pattern</span><span>→ Table</span><span>Mode</span><span></span></div>
        {#each editing.file_map as r, i}
          <div class="rule">
            <input bind:value={r.pattern} placeholder="articles_*.csv" />
            <input bind:value={r.table} placeholder="articles_list" />
            <select bind:value={r.action}>
              <option value="replace">replace (drop+load)</option>
              <option value="append">append</option>
              <option value="upsert">upsert</option>
            </select>
            <button class="btn sm danger" onclick={() => delRule(i)}>✕</button>
          </div>
        {/each}
        <div class="hint">"replace" = delete the old table and load the new file (what you want for full refreshes).</div>
      </div>

      <div class="two">
        <label>Check every
          <select bind:value={editing.schedule_seconds}>
            <option value={300}>5 minutes</option>
            <option value={900}>15 minutes</option>
            <option value={3600}>1 hour</option>
            <option value={21600}>6 hours</option>
            <option value={86400}>1 day</option>
          </select>
        </label>
        <label class="chk"><input type="checkbox" bind:checked={editing.retrain_after} /> Retrain after a change</label>
      </div>
      <label class="chk"><input type="checkbox" bind:checked={editing.enabled} /> Enable automatic syncing (the S3 daemon must also be on: <code>S3_SYNC_ENABLED=1</code>)</label>

      {#if testResult}<div class="test">{testResult}</div>{/if}

      <div class="modal-actions">
        <button class="btn ghost" onclick={() => editing = null}>Cancel</button>
        <button class="btn ghost" onclick={testConn} disabled={busy || !editing.id}>Test connection</button>
        <button class="btn primary" onclick={save} disabled={busy || !editing.name || !editing.bucket}>{editing.id ? 'Save' : 'Create'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .s3 { padding: 24px 28px; max-width: 1100px; margin: 0 auto; }
  .s3-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
  h1 { margin: 0; font-size: 24px; }
  .sub { margin: 4px 0 0; color: #6b6b6b; font-size: 13px; }
  .warn { background: #fdf0e6; border: 1px solid #e7b58f; color: #8a4a23; padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }
  .empty, .muted { color: #888; padding: 30px; text-align: center; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
  .card { border: 1px solid #e7e3da; border-radius: 12px; padding: 14px 16px; background: #fff; }
  .card-top { display: flex; justify-content: space-between; align-items: center; }
  .name { font-weight: 600; font-size: 15px; }
  .meta { color: #555; font-size: 13px; margin-top: 4px; word-break: break-all; }
  .meta.small { color: #888; font-size: 12px; }
  .status { font-size: 12px; color: #666; margin-top: 8px; display: flex; align-items: center; gap: 6px; }
  .nocred { color: #b5651d; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: #ccc; }
  .dot.ok { background: #3a9e4d; } .dot.err { background: #d24a4a; } .dot.run { background: #d9a23b; } .dot.idle { background: #bbb; }
  .pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
  .pill.on { background: #e3f3e6; color: #2c7a3d; } .pill.off { background: #efece6; color: #8a8a8a; }
  .row-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .btn { border: 1px solid #d8d2c6; background: #fff; border-radius: 8px; padding: 7px 12px; cursor: pointer; font-size: 13px; }
  .btn.sm { padding: 4px 9px; font-size: 12px; }
  .btn.primary { background: #1c1b18; color: #fff; border-color: #1c1b18; }
  .btn.ghost { background: #f6f3ee; } .btn.danger { color: #c0392b; border-color: #e3b4ad; }
  .btn:disabled { opacity: .5; cursor: default; }
  .detail { margin-top: 18px; border: 1px solid #e7e3da; border-radius: 12px; padding: 14px 16px; background: #fff; }
  .detail-head { display: flex; align-items: center; gap: 8px; }
  .x { margin-left: auto; border: none; background: none; cursor: pointer; font-size: 16px; }
  .log { background: #1c1b18; color: #e8e4da; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 260px; white-space: pre-wrap; }
  .objs-title { font-weight: 600; margin: 12px 0 6px; font-size: 13px; }
  .objs { width: 100%; border-collapse: collapse; font-size: 12px; }
  .objs th, .objs td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #efece6; }
  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 20px; }
  .modal { background: #fff; border-radius: 14px; padding: 22px 24px; width: 640px; max-width: 100%; max-height: 90vh; overflow: auto; }
  .modal h2 { margin: 0 0 14px; font-size: 18px; }
  .modal label { display: block; font-size: 12px; color: #555; margin-bottom: 10px; }
  .modal input, .modal select { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #d8d2c6; border-radius: 8px; font-size: 13px; margin-top: 3px; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .chk { display: flex; align-items: center; gap: 8px; }
  .chk input { width: auto; margin: 0; }
  .hint { color: #999; font-size: 11px; }
  .rules { border: 1px solid #ece8e0; border-radius: 10px; padding: 12px; margin: 6px 0 12px; }
  .rules-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; margin-bottom: 8px; }
  .rule { display: grid; grid-template-columns: 1.3fr 1fr .9fr auto; gap: 8px; margin-bottom: 6px; align-items: center; }
  .rule.head { font-size: 11px; color: #999; font-weight: 600; }
  .rule input, .rule select { margin-top: 0; }
  .test { background: #f3efe8; border-radius: 8px; padding: 10px 12px; font-size: 12px; margin: 8px 0; word-break: break-word; }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
  code { background: #efece6; padding: 1px 5px; border-radius: 4px; font-size: 11px; }
</style>
