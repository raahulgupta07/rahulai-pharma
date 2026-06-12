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
  let editing = $state<any>(null);
  let detail = $state<any>(null);
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
    busy = true; testResult = 'TESTING…';
    try {
      if (!editing.id) { testResult = 'Save the source first, then Test.'; return; }
      const r = await fetch(`/api/s3/sources/${editing.id}/test`, { method: 'POST', headers: authHeaders() });
      const d = await r.json();
      testResult = d.ok
        ? `✓ CONNECTED — ${d.objects} object(s), ${d.matched} match your patterns. Sample: ${(d.sample || []).join(', ')}`
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
  {#if editing}
    <!-- ── Add / Edit form ── -->
    <div class="s3-title">☁ {editing.id ? 'EDIT' : 'ADD'} S3 SOURCE</div>
    <div class="rule-hr"></div>

    <div class="ink-box">
      <label class="fl">NAME</label>
      <input class="fin" bind:value={editing.name} placeholder="Pharma exports" />

      <div class="two">
        <div><label class="fl">BUCKET</label><input class="fin" bind:value={editing.bucket} placeholder="my-pharma-data" /></div>
        <div><label class="fl">PREFIX (FOLDER)</label><input class="fin" bind:value={editing.prefix} placeholder="exports/" /></div>
      </div>
      <div class="two">
        <div><label class="fl">REGION</label><input class="fin" bind:value={editing.region} placeholder="ap-southeast-1" /></div>
        <div><label class="fl">ENDPOINT URL <span class="opt">(optional · MinIO)</span></label><input class="fin" bind:value={editing.endpoint_url} placeholder="" /></div>
      </div>
      <div class="two">
        <div><label class="fl">ACCESS KEY {#if editing.id}<span class="opt">(blank = keep)</span>{/if}</label><input class="fin" autocomplete="off" bind:value={editing.access_key} /></div>
        <div><label class="fl">SECRET KEY {#if editing.id}<span class="opt">(blank = keep)</span>{/if}</label><input class="fin" type="password" autocomplete="off" bind:value={editing.secret_key} /></div>
      </div>

      <div class="rule-hd">FILE → TABLE RULES <button class="sq ghost xs" onclick={addRule}>+ RULE</button></div>
      <div class="rule-row hdr"><span>FILE PATTERN</span><span>→ TABLE</span><span>MODE</span><span></span></div>
      {#each editing.file_map as r, i}
        <div class="rule-row">
          <input class="fin" bind:value={r.pattern} placeholder="articles_*.csv" />
          <input class="fin" bind:value={r.table} placeholder="articles_list" />
          <select class="fin" bind:value={r.action}>
            <option value="replace">replace</option>
            <option value="append">append</option>
            <option value="upsert">upsert</option>
          </select>
          <button class="sq danger xs" onclick={() => delRule(i)}>✕</button>
        </div>
      {/each}
      <div class="hint">"replace" = drop the old table and load the new file (full refresh).</div>

      <div class="two" style="margin-top:14px;">
        <div><label class="fl">CHECK EVERY</label>
          <select class="fin" bind:value={editing.schedule_seconds}>
            <option value={300}>5 minutes</option>
            <option value={900}>15 minutes</option>
            <option value={3600}>1 hour</option>
            <option value={21600}>6 hours</option>
            <option value={86400}>1 day</option>
          </select>
        </div>
        <div class="chk-wrap">
          <label class="chk"><input type="checkbox" bind:checked={editing.retrain_after} /> RETRAIN AFTER A CHANGE</label>
          <label class="chk"><input type="checkbox" bind:checked={editing.enabled} /> ENABLE AUTO SYNC <span class="opt">(S3_SYNC_ENABLED=1)</span></label>
        </div>
      </div>

      {#if testResult}<div class="test-out">{testResult}</div>{/if}

      <div class="rule-hr"></div>
      <div class="form-actions">
        <button class="sq ghost" onclick={() => editing = null}>CANCEL</button>
        <button class="sq ghost" onclick={testConn} disabled={busy || !editing.id}>TEST CONNECTION</button>
        <button class="sq" onclick={save} disabled={busy || !editing.name || !editing.bucket}>{editing.id ? 'SAVE' : 'CREATE'}</button>
      </div>
    </div>
  {:else}
    <!-- ── List ── -->
    <div class="s3-head">
      <div>
        <div class="s3-title">☁ S3 SYNC</div>
        <div class="s3-sub">Auto-pull from S3 → replace tables → retrain.</div>
      </div>
      <button class="sq" onclick={newSource}>+ ADD S3 SOURCE</button>
    </div>
    <div class="rule-hr"></div>

    {#if !boto3}
      <div class="warn">⚠ S3 LIBRARY NOT INSTALLED (boto3 missing). Rebuild the image with boto3 in requirements.</div>
    {/if}

    {#if loading}
      <div class="muted">LOADING…</div>
    {:else if !sources.length}
      <div class="muted">No S3 sources yet. Click <b>+ ADD S3 SOURCE</b> to connect a bucket.</div>
    {:else}
      <div class="grid">
        {#each sources as s (s.id)}
          <div class="ink-box card">
            <div class="card-top">
              <div class="cname">{s.name}</div>
              <span class="pill {s.enabled ? 'on' : 'off'}">{s.enabled ? '● ON' : '○ OFF'}</span>
            </div>
            <div class="cmeta mono">s3://{s.bucket}/{s.prefix}</div>
            <div class="cmeta">{s.file_map?.length || 0} rule(s) · every {fmtSched(s.schedule_seconds)} · {s.retrain_after ? 'retrain' : 'no retrain'}</div>
            <div class="cstatus">
              <span class="dot {s.last_status === 'ok' ? 'ok' : s.last_status === 'error' ? 'err' : s.last_status === 'running' ? 'run' : 'idle'}"></span>
              {s.last_status || 'never run'}{s.last_sync_at ? ' · ' + new Date(s.last_sync_at).toLocaleString() : ''}
              {#if !s.has_credentials}<span class="nocred"> · no creds</span>{/if}
            </div>
            <div class="rule-hr thin"></div>
            <div class="row-actions">
              <button class="sq xs" onclick={() => syncNow(s.id)} disabled={busy}>SYNC NOW</button>
              <button class="sq ghost xs" onclick={() => syncNow(s.id, true)} disabled={busy} title="Re-pull every file even if unchanged">FORCE</button>
              <button class="sq ghost xs" onclick={() => openDetail(s.id)}>LOG</button>
              <button class="sq ghost xs" onclick={() => editSource(s)}>EDIT</button>
              <button class="sq danger xs" onclick={() => remove(s.id)}>DELETE</button>
            </div>
          </div>
        {/each}
      </div>
    {/if}

    {#if detail}
      <div class="rule-hr"></div>
      <div class="rule-hd">☁ {detail.name} — LAST RUN LOG <button class="sq ghost xs" onclick={() => detail = null}>✕ CLOSE</button></div>
      <pre class="log">{detail.last_log || '(no log yet)'}</pre>
      {#if detail.objects?.length}
        <div class="rule-hd">SYNCED OBJECTS</div>
        <table class="objs">
          <thead><tr><th>OBJECT KEY</th><th>TABLE</th><th>ROWS</th><th>WHEN</th></tr></thead>
          <tbody>
            {#each detail.objects as o}
              <tr><td class="mono">{o.key}</td><td>{o.table}</td><td>{o.rows ?? '—'}</td><td>{o.synced_at ? new Date(o.synced_at).toLocaleString() : ''}</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .s3 { font-size: 12px; color: var(--pw-ink, #1c1b18); }
  .s3-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  .s3-title { font-size: 16px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.02em; }
  .s3-sub { font-size: 11px; color: var(--pw-muted, #888); margin-top: 4px; }
  .rule-hr { border-top: 2px solid var(--pw-ink, #1c1b18); margin: 14px 0; }
  .rule-hr.thin { border-top-width: 1px; border-color: var(--pw-bg-alt, #e7e3da); margin: 10px 0; }
  .ink-box { border: 2px solid var(--pw-ink, #1c1b18); background: var(--pw-surface, #fff); padding: 16px; }
  .warn { border: 2px solid #c98a3a; background: #fdf0e6; color: #8a4a23; padding: 8px 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 14px; }
  .muted { color: var(--pw-muted, #888); padding: 20px 0; }
  .mono { font-family: var(--pw-font-body, ui-monospace, monospace); }

  /* form */
  .fl { display: block; font-size: 11px; font-weight: 700; text-transform: uppercase; margin: 10px 0 3px; }
  .opt { color: var(--pw-muted, #999); font-weight: 400; text-transform: none; }
  .fin { width: 100%; box-sizing: border-box; border: 2px solid var(--pw-ink, #1c1b18); padding: 6px 10px; font-family: var(--pw-font-body, ui-monospace, monospace); font-size: 11px; background: var(--pw-bg, #faf9f5); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .rule-hd { font-size: 11px; font-weight: 900; text-transform: uppercase; margin: 16px 0 8px; display: flex; align-items: center; gap: 10px; }
  .rule-row { display: grid; grid-template-columns: 1.3fr 1fr 0.9fr auto; gap: 8px; margin-bottom: 6px; align-items: center; }
  .rule-row.hdr { font-size: 10px; color: var(--pw-muted, #999); font-weight: 700; margin-bottom: 4px; }
  .hint { font-size: 10px; color: var(--pw-muted, #999); margin-top: 4px; }
  .chk-wrap { display: flex; flex-direction: column; gap: 8px; justify-content: flex-end; padding-bottom: 4px; }
  .chk { display: flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; cursor: pointer; }
  .chk input { width: auto; }
  .test-out { border: 2px solid var(--pw-ink, #1c1b18); background: var(--pw-bg-alt, #f3ece1); padding: 8px 12px; font-size: 11px; margin-top: 12px; word-break: break-word; font-family: var(--pw-font-body, monospace); }
  .form-actions { display: flex; justify-content: flex-end; gap: 8px; }

  /* square buttons (brutalist) */
  .sq { border: 2px solid var(--pw-ink, #1c1b18); background: var(--pw-ink, #1c1b18); color: #fff; padding: 7px 16px; font-size: 11px; font-weight: 700; text-transform: uppercase; cursor: pointer; font-family: inherit; }
  .sq.ghost { background: var(--pw-surface, #fff); color: var(--pw-ink, #1c1b18); }
  .sq.danger { background: var(--pw-surface, #fff); color: #c0392b; border-color: #c0392b; }
  .sq.xs { padding: 4px 9px; font-size: 10px; border-width: 1px; }
  .sq:hover { box-shadow: 2px 2px 0 var(--pw-ink, #1c1b18); }
  .sq:disabled { opacity: 0.45; cursor: default; box-shadow: none; }

  /* cards */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
  .card { padding: 14px; }
  .card-top { display: flex; justify-content: space-between; align-items: center; }
  .cname { font-weight: 900; font-size: 13px; text-transform: uppercase; }
  .cmeta { font-size: 11px; color: var(--pw-muted, #777); margin-top: 4px; word-break: break-all; }
  .cstatus { font-size: 11px; color: var(--pw-muted, #666); margin-top: 8px; display: flex; align-items: center; gap: 6px; }
  .nocred { color: #b5651d; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #bbb; display: inline-block; }
  .dot.ok { background: #3a9e4d; } .dot.err { background: #d24a4a; } .dot.run { background: #d9a23b; } .dot.idle { background: #bbb; }
  .pill { font-size: 10px; font-weight: 700; padding: 2px 7px; border: 1px solid var(--pw-ink, #1c1b18); }
  .pill.on { background: #e3f3e6; color: #2c7a3d; border-color: #2c7a3d; }
  .pill.off { background: var(--pw-bg-alt, #efece6); color: var(--pw-muted, #8a8a8a); }
  .row-actions { display: flex; flex-wrap: wrap; gap: 6px; }

  /* log */
  .log { background: #1c1b18; color: #e8e4da; padding: 12px; font-size: 11px; overflow: auto; max-height: 260px; white-space: pre-wrap; font-family: var(--pw-font-body, ui-monospace, monospace); }
  .objs { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 4px; }
  .objs th { text-align: left; padding: 5px 8px; border-bottom: 2px solid var(--pw-ink, #1c1b18); text-transform: uppercase; font-size: 10px; }
  .objs td { padding: 5px 8px; border-bottom: 1px solid var(--pw-bg-alt, #efece6); }
</style>
