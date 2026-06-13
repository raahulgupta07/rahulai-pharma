<script lang="ts">
  let { slug, onCount, onOpenRules }: { slug: string; onCount?: (n: number) => void; onOpenRules?: () => void } = $props();

  // ─── Auth helper ────────────────────────────────────────────────
  function _h(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  }
  function _hNoJson(): Record<string, string> {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
    return t ? { Authorization: `Bearer ${t}` } : {};
  }
  function apiBase() { return `/api/projects/${slug}`; }

  // ─── Sub-view state machine ──────────────────────────────────────
  // views: dashboard | editor | nl-draft | review-queue | templates | import | drift | permissions | history | aliases | schema-explorer | tier-compare
  let view = $state<string>('dashboard');
  let drawerView = $state<'schema' | 'history' | 'aliases' | null>(null);
  let drawerMetricName = $state<string>('');

  // ─── Dashboard state ─────────────────────────────────────────────
  let metrics = $state<any[]>([]);
  let rules = $state<any[]>([]);            // dash_rules_db — NL definitions (unified view)
  let typeFilter = $state<'all' | 'metrics' | 'rules'>('all');
  let dispMode = $state<'table' | 'cards' | 'ai'>('table');   // view: dense table (default) | cards | AI sub-tab
  let showMore = $state(false);   // overflow menu for secondary actions
  let crmEligible = $state(false); // schema looks like a CRM → show "Seed CRM metrics"
  let crmSeeding = $state(false);
  let metricsLoading = $state(false);
  let metricsSearch = $state('');
  let driftData = $state<any[]>([]);
  let reviewQueue = $state<any[]>([]);
  let templates = $state<any[]>([]);
  let permissions = $state<any>(null);
  let columns = $state<any[]>([]);    // from /metrics/columns
  let columnsTable = $state('');

  // Distinct tables present in the project schema (for the SOURCE TABLES picker).
  let tableList = $derived.by(() => {
    const seen = new Set<string>();
    for (const c of (Array.isArray(columns) ? columns : [])) {
      if (c?.table) seen.add(c.table);
    }
    return Array.from(seen).sort();
  });

  // Columns scoped to the chosen source tables (falls back to all when none chosen).
  // Each entry carries its table so dropdown labels can disambiguate same-named columns.
  let scopedColumns = $derived.by(() => {
    const cols = Array.isArray(columns) ? columns : [];
    const picked = Array.isArray(editSpec?.source_tables) ? editSpec.source_tables : [];
    const inScope = picked.length ? cols.filter((c: any) => picked.includes(c.table)) : cols;
    // Dedupe by column name (first table wins) so the group/measure dropdowns stay clean,
    // but keep a `tables` list so the label can show where a name lives.
    const byName = new Map<string, any>();
    for (const c of inScope) {
      const k = c.column;
      if (!byName.has(k)) byName.set(k, { column: c.column, dtype: c.dtype, samples: c.samples, tables: [c.table] });
      else byName.get(k).tables.push(c.table);
    }
    return Array.from(byName.values());
  });

  // Numeric-ish columns only (for the measure-column picker on sum/avg metrics).
  let numericColumns = $derived.by(() =>
    scopedColumns.filter((c: any) => /INT|NUMERIC|DECIMAL|REAL|DOUBLE|FLOAT|MONEY|SERIAL/i.test(String(c.dtype || '')))
  );

  function colLabel(c: any): string {
    const t = Array.isArray(c.tables) && c.tables.length > 1 ? ` · ${c.tables.length} tbls` : (Array.isArray(c.tables) ? ` · ${c.tables[0]}` : '');
    return `${c.column} (${c.dtype})${t}`;
  }

  // ─── AI Recommendations ──────────────────────────────────────────
  let recNew = $state<any[]>([]);        // suggested new metrics (LLM, training-derived)
  let chatSug = $state<any[]>([]);       // dash_suggested_rules — derived from chat
  let recLoading = $state(false);
  let recDismissed = $state<Set<string>>(new Set());
  let sugSource = $state<'all' | 'human' | 'training' | 'chat'>('all');  // suggestion-source tab

  // Rules worth promoting → metric: not already a metric name, definition reads computable.
  let recPromote = $derived.by(() => {
    const metricNames = new Set((Array.isArray(metrics) ? metrics : []).map((m: any) => (m.name || '').toLowerCase()));
    const computable = /\b(count|rate|ratio|sum|avg|average|per|percent|%|=|\/|total|share)\b/i;
    return (Array.isArray(rules) ? rules : []).filter((r: any) => {
      const nm = (r.name || '').toLowerCase();
      if (metricNames.has(nm) || recDismissed.has('rule:' + nm)) return false;
      return computable.test(r.definition || '') || r.type === 'kpi' || r.type === 'calculation';
    }).slice(0, 8);
  });

  // Metrics needing attention: drift detected (pin ≠ live).
  let recAttention = $derived.by(() =>
    (Array.isArray(driftData) ? driftData : []).filter((d: any) => d && d.ok === false)
  );

  // Unified suggestion list, each tagged with a source:
  //   human    = user-authored rules worth promoting
  //   training = schema-derived new metrics + drift fixes + auto-suggested rules
  //   chat     = dash_suggested_rules (extracted from chat)
  let allSug = $derived.by(() => {
    const out: any[] = [];
    for (const s of recNew) {
      if (!recDismissed.has('new:' + (s.name || ''))) out.push({ stype: 'new', source: 'training', data: s });
    }
    for (const r of recPromote) {
      out.push({ stype: 'promote', source: r.source === 'user' ? 'human' : 'training', data: r });
    }
    for (const d of recAttention) {
      out.push({ stype: 'drift', source: 'training', data: d });
    }
    for (const c of chatSug) {
      if (!recDismissed.has('chat:' + c.id)) out.push({ stype: 'chat', source: 'chat', data: c });
    }
    return out;
  });
  let sugByTab = $derived.by(() =>
    sugSource === 'all' ? allSug : allSug.filter((s: any) => s.source === sugSource)
  );
  let sugCounts = $derived.by(() => ({
    all: allSug.length,
    human: allSug.filter((s: any) => s.source === 'human').length,
    training: allSug.filter((s: any) => s.source === 'training').length,
    chat: allSug.filter((s: any) => s.source === 'chat').length,
  }));

  // Unified rows: metrics (locked/executable) + rules (NL hints). Each tagged
  // with _rowtype so the table renders the right cells + actions.
  let unifiedRows = $derived.by(() => {
    const s = metricsSearch.toLowerCase();
    const matches = (txt: string) => !s || (txt || '').toLowerCase().includes(s);
    const out: any[] = [];
    if (typeFilter !== 'rules' && Array.isArray(metrics)) {
      for (const m of metrics) {
        if (matches(m.name) || matches(m.kind) || matches((m.group_dims || '').toString())) {
          out.push({ ...m, _rowtype: 'metric' });
        }
      }
    }
    if (typeFilter !== 'metrics' && Array.isArray(rules)) {
      for (const r of rules) {
        if (matches(r.name) || matches(r.type) || matches(r.definition)) {
          out.push({ ...r, _rowtype: 'rule' });
        }
      }
    }
    return out;
  });
  // metrics-only filtered list, kept for the empty-state check
  let filteredMetrics = $derived(Array.isArray(metrics) ? metrics : []);

  // ─── Editor state ────────────────────────────────────────────────
  let editSpec = $state<any>(emptySpec());
  let editSaving = $state(false);
  let editError = $state('');
  let testResult = $state<any>(null);
  let testLoading = $state(false);
  let tierResult = $state<any>(null);
  let tierLoading = $state(false);
  let tierQuestion = $state('');
  let showTierCompare = $state(false);
  let editIsNew = $state(true);

  function emptySpec() {
    return {
      name: '',
      synonyms: [] as string[],
      description: '',
      kind: 'count' as string,
      source_glob: '',
      source_tables: [] as string[],
      measure_col: '',
      filters: [] as any[],
      denom_filters: [] as any[],
      group_dims: [] as string[],
      default_group: [] as string[],
      trim_values: false,
      verified_answer: '',
      status: 'draft' as string,
    };
  }

  // ─── NL Draft state ──────────────────────────────────────────────
  let nlText = $state('');
  let nlLoading = $state(false);
  let nlResult = $state<any>(null);

  // Editor mode: 'describe' = conversational KPI builder; 'manual' = dropdown builder.
  let editMode = $state<'describe' | 'manual'>('describe');

  // ─── Conversational KPI builder state ─────────────────────────────
  let chatMsgs = $state<any[]>([]);              // {role:'user'|'ai', text}
  let chatInput = $state('');
  let chatBusy = $state(false);
  let candidates = $state<any[]>([]);            // {id, spec, checked, status, value, error}
  let buildPhase = $state<'chat' | 'building' | 'done'>('chat');
  let savingAll = $state(false);
  let createdCount = $state(0);
  let _candId = 0;

  // Plain-English description of what a spec does — no SQL, no jargon.
  function explainSpec(s: any): string {
    const tbls = Array.isArray(s.source_tables) && s.source_tables.length
      ? `${s.source_tables.length} table${s.source_tables.length > 1 ? 's' : ''}` : 'all tables';
    const fstr = (f: any) => `${f.col} ${f.op} ${f.value ?? ''}`.trim();
    const filters = Array.isArray(s.filters) ? s.filters.filter((f: any) => f.col) : [];
    const grp = Array.isArray(s.group_dims) && s.group_dims.length ? `, broken down by ${s.group_dims.join(', ')}` : '';
    let core = '';
    if (s.kind === 'count') {
      core = filters.length ? `counts records where ${filters.map(fstr).join(' and ')}` : 'counts every record';
    } else if (s.kind === 'sum') {
      core = `adds up ${s.measure_col || '?'}`;
    } else if (s.kind === 'avg') {
      core = `averages ${s.measure_col || '?'}`;
    } else if (s.kind === 'rate' || s.kind === 'ratio') {
      core = filters.length ? `share of records where ${filters.map(fstr).join(' and ')}` : 'a ratio';
    } else {
      core = s.kind;
    }
    return `${core} · ${tbls}${grp}`;
  }

  // Distinct real columns a spec touches (for "columns used").
  function columnsOfSpec(s: any): string[] {
    const out: string[] = [];
    for (const f of [...(s.filters || []), ...(s.denom_filters || [])]) if (f.col && !out.includes(f.col)) out.push(f.col);
    for (const g of (s.group_dims || [])) if (g && !out.includes(g)) out.push(g);
    if (s.measure_col && !out.includes(s.measure_col)) out.push(s.measure_col);
    return out;
  }

  function resetBuilder() {
    chatMsgs = []; chatInput = ''; candidates = []; buildPhase = 'chat'; savingAll = false; chatBusy = false; createdCount = 0;
  }

  function _normSpec(s: any) {
    const spec = { ...emptySpec(), ...s };
    for (const k of ['synonyms', 'filters', 'denom_filters', 'group_dims', 'source_tables']) {
      if (!Array.isArray((spec as any)[k])) (spec as any)[k] = [];
    }
    return spec;
  }

  function addCandidate(spec: any): boolean {
    const name = (spec?.name || '').trim();
    if (!name) return false;
    if (candidates.some((c: any) => (c.spec.name || '').toLowerCase() === name.toLowerCase())) return false;
    candidates = [...candidates, { id: ++_candId, spec: _normSpec(spec), checked: true, status: 'idle', value: null, error: '' }];
    return true;
  }

  function toggleCand(id: number) {
    candidates = candidates.map((c: any) => c.id === id ? { ...c, checked: !c.checked } : c);
  }

  // One chat turn: try single-metric derive first; if it pins a metric, add it.
  // Otherwise treat it as an exploration → recommend-new → add all proposals.
  async function chatSend() {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    chatMsgs = [...chatMsgs, { role: 'user', text }];
    chatInput = ''; chatBusy = true;
    try {
      await deriveDraft(text);                    // POST /metrics/derive
      if (nlResult?.spec?.name) {
        const added = addCandidate(nlResult.spec);
        chatMsgs = [...chatMsgs, { role: 'ai', text: added ? `Added ${nlResult.spec.name} (${nlResult.spec.kind}). Refine more or generate.` : `${nlResult.spec.name} is already in the list.` }];
      } else {
        await loadRecNew();
        const colNames = [...new Set((Array.isArray(columns) ? columns : []).map((c: any) => c.column))].slice(0, 12);
        let n = 0;
        for (const rec of recNew) { if (addCandidate(rec)) n++; }
        const colLine = colNames.length ? `Found columns: ${colNames.join(' · ')}. ` : '';
        chatMsgs = [...chatMsgs, { role: 'ai', text: n
          ? `${colLine}Proposing ${n} KPI(s) below — uncheck any you don't want, then Generate.`
          : `${colLine}No new KPIs to propose (they may already exist). Try describing a specific measure.` }];
      }
    } catch (e: any) {
      chatMsgs = [...chatMsgs, { role: 'ai', text: `Sorry — ${e?.message || 'something went wrong'}.` }];
    }
    chatBusy = false;
  }

  // Test each checked candidate in turn, live-updating its status.
  async function genSelected() {
    buildPhase = 'building'; editError = '';
    candidates = candidates.map((c: any) => c.checked ? { ...c, status: 'idle', value: null, error: '' } : c);
    for (const c of candidates.filter((x: any) => x.checked)) {
      candidates = candidates.map((x: any) => x.id === c.id ? { ...x, status: 'testing' } : x);
      try {
        const r = await fetch(`${apiBase()}/metrics/test`, { method: 'POST', headers: _h(), body: JSON.stringify({ spec: c.spec }) });
        const d = await r.json().catch(() => ({ ok: false, error: 'test failed' }));
        candidates = candidates.map((x: any) => x.id === c.id
          ? { ...x, status: (r.ok && d.ok !== false) ? 'done' : 'fail', value: d.total ?? d.rate_pct ?? null, error: d.error || (r.ok ? '' : 'test failed') }
          : x);
      } catch (e: any) {
        candidates = candidates.map((x: any) => x.id === c.id ? { ...x, status: 'fail', error: e?.message || 'error' } : x);
      }
    }
  }

  // Save every checked candidate that passed its test.
  async function saveAll() {
    savingAll = true; editError = '';
    let ok = 0;
    for (const c of candidates.filter((x: any) => x.checked && x.status === 'done')) {
      try {
        const r = await fetch(`${apiBase()}/metrics`, { method: 'POST', headers: _h(), body: JSON.stringify(c.spec) });
        if (r.ok) ok++;
      } catch {}
    }
    savingAll = false;
    if (ok) { createdCount = ok; await loadMetrics(); buildPhase = 'done'; }
    else editError = 'Nothing saved — generate KPIs first.';
  }

  // ─── Import state ────────────────────────────────────────────────
  let importText = $state('');
  let importLoading = $state(false);
  let importResult = $state<any>(null);

  // ─── Schema explorer ─────────────────────────────────────────────
  let schemaColumns = $state<any[]>([]);
  let schemaLoading = $state(false);
  let schemaTableFilter = $state('');

  // ─── History ─────────────────────────────────────────────────────
  let historyData = $state<any[]>([]);
  let historyLoading = $state(false);

  // ─── Aliases inline editing ──────────────────────────────────────
  let aliasesText = $state('');
  let aliasesSaving = $state(false);

  // ──────────────────────────────────────────────────────────────────
  // LOAD FUNCTIONS
  // ──────────────────────────────────────────────────────────────────

  async function loadMetrics(status?: string) {
    metricsLoading = true;
    try {
      const url = status ? `${apiBase()}/metrics?status=${status}` : `${apiBase()}/metrics`;
      const r = await fetch(url, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        metrics = Array.isArray(d) ? d : [];
        try { onCount?.(metrics.length + (Array.isArray(rules) ? rules.length : 0)); } catch {}
      }
    } catch {}
    metricsLoading = false;
  }

  async function loadRules() {
    try {
      const r = await fetch(`${apiBase()}/rules`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        rules = Array.isArray(d?.rules) ? d.rules : [];
        try { onCount?.((Array.isArray(metrics) ? metrics.length : 0) + rules.length); } catch {}
      }
    } catch {}
  }

  // Promote an NL rule into a structured, locked metric: open the editor
  // prefilled. User adds filters, tests, saves → it becomes executable.
  function promoteRule(rule: any) {
    const slugName = String(rule.name || 'metric').trim().toLowerCase()
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'metric';
    editSpec = {
      ...emptySpec(),
      name: slugName,
      description: rule.definition || '',
      synonyms: rule.name && rule.name !== slugName ? [rule.name] : [],
      kind: (rule.type === 'kpi' || rule.type === 'calculation') ? 'rate' : 'count',
      status: 'draft',
    };
    editIsNew = true; testResult = null; editError = '';
    editMode = 'manual';
    view = 'editor'; loadColumns();
  }

  async function loadRecNew() {
    recLoading = true;
    try {
      const r = await fetch(`${apiBase()}/metrics/recommend-new`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        recNew = Array.isArray(d?.suggestions) ? d.suggestions : [];
      }
    } catch {}
    recLoading = false;
  }

  async function loadChatSug() {
    try {
      const r = await fetch(`${apiBase()}/suggested-rules`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        chatSug = Array.isArray(d?.suggestions) ? d.suggestions : [];
      }
    } catch {}
  }

  async function acceptChatSug(c: any) {
    try {
      await fetch(`${apiBase()}/suggested-rules/${c.id}/approve`, { method: 'POST', headers: _h() });
    } catch {}
    await loadChatSug(); await loadRules();
  }
  async function rejectChatSug(c: any) {
    try {
      await fetch(`${apiBase()}/suggested-rules/${c.id}/reject`, { method: 'POST', headers: _h() });
    } catch {}
    recDismissed = new Set([...recDismissed, 'chat:' + c.id]);
    await loadChatSug();
  }

  // Open editor prefilled from an AI suggestion (a draft metric spec).
  function createFromSuggestion(s: any) {
    editSpec = {
      ...emptySpec(),
      name: s.name || '',
      kind: s.kind || 'count',
      description: s.description || '',
      filters: Array.isArray(s.filters) ? s.filters : [],
      group_dims: Array.isArray(s.group_dims) ? s.group_dims : [],
      measure_col: s.measure_col || '',
      status: 'draft',
    };
    editIsNew = true; testResult = null; editError = '';
    editMode = 'manual';
    view = 'editor'; loadColumns();
  }

  async function loadDrift() {
    try {
      const r = await fetch(`${apiBase()}/metrics/drift`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        driftData = Array.isArray(d) ? d : [];
      }
    } catch {}
  }

  async function loadReviewQueue() {
    try {
      const r = await fetch(`${apiBase()}/metrics/review-queue`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        reviewQueue = Array.isArray(d) ? d : [];
      }
    } catch {}
  }

  async function loadTemplates() {
    try {
      const r = await fetch(`${apiBase()}/metrics/templates`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        templates = Array.isArray(d) ? d : [];
      }
    } catch {}
  }

  async function loadPermissions() {
    try {
      const r = await fetch(`${apiBase()}/metrics/permissions`, { headers: _hNoJson() });
      if (r.ok) permissions = await r.json();
    } catch {}
  }

  async function loadColumns(table?: string) {
    try {
      const url = table ? `${apiBase()}/metrics/columns?table=${encodeURIComponent(table)}` : `${apiBase()}/metrics/columns`;
      const r = await fetch(url, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        columns = Array.isArray(d) ? d : [];
      }
    } catch {}
  }

  async function loadMetricForEdit(name: string) {
    try {
      const r = await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        editSpec = { ...emptySpec(), ...d };
        if (!Array.isArray(editSpec.synonyms)) editSpec.synonyms = [];
        if (!Array.isArray(editSpec.filters)) editSpec.filters = [];
        if (!Array.isArray(editSpec.denom_filters)) editSpec.denom_filters = [];
        if (!Array.isArray(editSpec.group_dims)) editSpec.group_dims = [];
        if (!Array.isArray(editSpec.source_tables)) editSpec.source_tables = [];
        editIsNew = false;
        editMode = 'manual';
        view = 'editor';
        await loadColumns();
      }
    } catch {}
  }

  async function saveMetric() {
    editSaving = true; editError = '';
    try {
      const r = await fetch(`${apiBase()}/metrics`, {
        method: 'POST',
        headers: _h(),
        body: JSON.stringify(editSpec),
      });
      if (r.ok) {
        await loadMetrics();
        view = 'dashboard';
      } else {
        const e = await r.json().catch(() => ({ detail: 'Save failed' }));
        editError = e.detail || 'Save failed';
      }
    } catch (e: any) { editError = e?.message || 'Save failed'; }
    editSaving = false;
  }

  async function deprecateMetric(name: string) {
    try {
      await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}`, { method: 'DELETE', headers: _hNoJson() });
      await loadMetrics();
      view = 'dashboard';
    } catch {}
  }

  async function testMetric() {
    testLoading = true; testResult = null;
    try {
      const r = await fetch(`${apiBase()}/metrics/test`, {
        method: 'POST', headers: _h(),
        body: JSON.stringify({ spec: editSpec }),
      });
      if (r.ok) testResult = await r.json();
      else testResult = { ok: false, error: 'Test failed' };
    } catch (e: any) { testResult = { ok: false, error: e?.message }; }
    testLoading = false;
  }

  async function runTierCompare() {
    tierLoading = true; tierResult = null;
    try {
      const r = await fetch(`${apiBase()}/metrics/tier-compare`, {
        method: 'POST', headers: _h(),
        body: JSON.stringify({ spec: editSpec, question: tierQuestion || editSpec.description || editSpec.name }),
      });
      if (r.ok) tierResult = await r.json();
      else tierResult = { error: 'Tier compare failed' };
    } catch (e: any) { tierResult = { error: e?.message }; }
    tierLoading = false;
  }

  async function deriveDraft(text?: string) {
    nlLoading = true; nlResult = null;
    try {
      const r = await fetch(`${apiBase()}/metrics/derive`, {
        method: 'POST', headers: _h(),
        body: JSON.stringify({ text: text ?? nlText }),
      });
      if (r.ok) nlResult = await r.json();
      else nlResult = { error: 'Derive failed' };
    } catch (e: any) { nlResult = { error: e?.message }; }
    nlLoading = false;
  }

  function fillSpecFromDraft() {
    if (!nlResult?.spec) return;
    editSpec = { ...emptySpec(), ...nlResult.spec };
    if (!Array.isArray(editSpec.filters)) editSpec.filters = [];
    if (!Array.isArray(editSpec.denom_filters)) editSpec.denom_filters = [];
    if (!Array.isArray(editSpec.synonyms)) editSpec.synonyms = [];
    if (!Array.isArray(editSpec.group_dims)) editSpec.group_dims = [];
    if (!Array.isArray(editSpec.source_tables)) editSpec.source_tables = [];
    editIsNew = true;
  }

  function acceptNlDraft() {
    fillSpecFromDraft();
    view = 'editor';
    editMode = 'manual';
    loadColumns();
  }

  // Describe-it: plain English → LLM drafts the spec → fill editSpec → auto Test live.
  // The user never types a column name; they see the proposed spec + its live number, then Save or Tweak.
  async function generateFromDescribe() {
    if (!nlText.trim()) return;
    await deriveDraft();              // sets nlResult {spec, confidence, error?}
    if (nlResult?.spec?.name) {       // only fill on a real single-metric draft
      fillSpecFromDraft();            // fills editSpec, stays in describe mode
      await testMetric();            // auto-run so the user sees the number before saving
    } else {
      // Exploratory ask ("which KPIs can we build?") → offer schema-derived proposals.
      await loadRecNew();
    }
  }

  // Apply a recommend-new proposal into the editor spec, then auto-test.
  async function applyRec(rec: any) {
    editSpec = { ...emptySpec(), ...rec };
    if (!Array.isArray(editSpec.filters)) editSpec.filters = [];
    if (!Array.isArray(editSpec.denom_filters)) editSpec.denom_filters = [];
    if (!Array.isArray(editSpec.synonyms)) editSpec.synonyms = [];
    if (!Array.isArray(editSpec.group_dims)) editSpec.group_dims = [];
    if (!Array.isArray(editSpec.source_tables)) editSpec.source_tables = [];
    editIsNew = true;
    // Surface it in the draft card by faking an nlResult spec.
    nlResult = { spec: { ...rec }, confidence: 'medium' };
    await testMetric();
  }

  async function doImport() {
    importLoading = true; importResult = null;
    try {
      const rows = JSON.parse(importText);
      const r = await fetch(`${apiBase()}/metrics/import`, {
        method: 'POST', headers: _h(),
        body: JSON.stringify({ rows: Array.isArray(rows) ? rows : [rows] }),
      });
      if (r.ok) { importResult = await r.json(); await loadMetrics(); }
      else importResult = { error: 'Import failed' };
    } catch (e: any) { importResult = { error: e?.message || 'Invalid JSON' }; }
    importLoading = false;
  }

  async function loadHistory(name: string) {
    historyLoading = true; historyData = [];
    try {
      const r = await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}/history`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        historyData = Array.isArray(d) ? d : [];
      }
    } catch {}
    historyLoading = false;
  }

  async function rollback(name: string, version: number) {
    try {
      await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}/rollback/${version}`, { method: 'POST', headers: _hNoJson() });
      await loadHistory(name);
    } catch {}
  }

  async function approveMetric(name: string) {
    try {
      await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}/approve`, { method: 'POST', headers: _hNoJson() });
      await loadMetrics();
      await loadReviewQueue();
    } catch {}
  }

  // Render pinned truth (verified_answer is a JSON object like {total:1544} or
  // {successful_pct:64.3, unsuccessful_pct:35.7}) as a readable string.
  function formatPin(v: any): string {
    if (v == null || v === '') return '—';
    if (typeof v === 'number' || typeof v === 'string') return String(v);
    if (typeof v === 'object') {
      const keys = Object.keys(v);
      if (!keys.length) return '—';
      return keys.map(k => `${k.replace(/_/g, ' ')}: ${v[k]}`).join(' · ');
    }
    return String(v);
  }

  async function saveAliases(name: string) {
    aliasesSaving = true;
    try {
      const syns = aliasesText.split(',').map(s => s.trim()).filter(Boolean);
      await fetch(`${apiBase()}/metrics/${encodeURIComponent(name)}/aliases`, {
        method: 'PATCH', headers: _h(),
        body: JSON.stringify({ synonyms: syns }),
      });
      await loadMetrics();
    } catch {}
    aliasesSaving = false;
  }

  async function loadSchemaExplorer() {
    schemaLoading = true;
    try {
      const r = await fetch(`${apiBase()}/metrics/columns`, { headers: _hNoJson() });
      if (r.ok) {
        const d = await r.json();
        schemaColumns = Array.isArray(d) ? d : [];
      }
    } catch {}
    schemaLoading = false;
  }

  // ─── Filter helpers ───────────────────────────────────────────────
  const OPS = ['=', '!=', 'IN', '>', '>=', '<', '<=', 'BETWEEN', 'LIKE', 'IS NULL', 'IS NOT NULL'];

  function addFilter(target: 'filters' | 'denom_filters') {
    editSpec[target] = [...(Array.isArray(editSpec[target]) ? editSpec[target] : []), { col: '', op: '=', value: '', trim: false }];
  }
  function removeFilter(target: 'filters' | 'denom_filters', i: number) {
    const arr = Array.isArray(editSpec[target]) ? [...editSpec[target]] : [];
    arr.splice(i, 1);
    editSpec[target] = arr;
  }
  function updateFilter(target: 'filters' | 'denom_filters', i: number, field: string, val: any) {
    const arr = Array.isArray(editSpec[target]) ? editSpec[target].map((x: any, idx: number) => idx === i ? { ...x, [field]: val } : x) : [];
    editSpec[target] = arr;
  }

  function colSamples(colName: string): string[] {
    const col = columns.find((c: any) => c.column === colName);
    return Array.isArray(col?.samples) ? col.samples : [];
  }

  function useAsFilter(colName: string) {
    addFilter('filters');
    const arr = Array.isArray(editSpec.filters) ? editSpec.filters : [];
    if (arr.length > 0) {
      updateFilter('filters', arr.length - 1, 'col', colName);
    }
    view = 'editor';
  }

  function useAsGroup(colName: string) {
    if (!Array.isArray(editSpec.group_dims)) editSpec.group_dims = [];
    if (!editSpec.group_dims.includes(colName)) editSpec.group_dims = [...editSpec.group_dims, colName];
    view = 'editor';
  }

  const NUMERIC_RE = /INT|NUMERIC|DECIMAL|REAL|DOUBLE|FLOAT|MONEY|SERIAL/i;
  function isNumericCol(dtype: any): boolean { return NUMERIC_RE.test(String(dtype || '')); }

  // Mark a numeric column as the sum/avg measure. Flips KIND to sum if it's still
  // a count, so the user doesn't have to know to switch the dropdown first.
  function useAsMeasure(colName: string) {
    if (editSpec.kind !== 'sum' && editSpec.kind !== 'avg') editSpec.kind = 'sum';
    editSpec.measure_col = colName;
    view = 'editor';
  }

  // ─── Init ─────────────────────────────────────────────────────────
  $effect(() => {
    if (slug) {
      loadMetrics();
      loadRules();
      loadDrift();
      loadReviewQueue();
      loadRecNew();
      loadChatSug();
      loadCrmEligible();
    }
  });

  async function loadCrmEligible() {
    try {
      const r = await fetch(`${apiBase()}/metrics/crm-eligible`, { headers: _hNoJson() });
      if (r.ok) { const d = await r.json(); crmEligible = !!d?.eligible; }
    } catch { /* fail-soft */ }
  }

  let crmCandidates = $state<any[]>([]);
  let crmSelected = $state<Record<string, boolean>>({});
  let crmCols = $state<any>({});
  let crmTables = $state<string[]>([]);

  async function openCrmPick() {
    showMore = false;
    view = 'crm-pick';
    crmCandidates = [];
    try {
      const r = await fetch(`${apiBase()}/metrics/crm-preview`, { headers: _hNoJson() });
      const d = await r.json().catch(() => ({}));
      crmCandidates = Array.isArray(d?.candidates) ? d.candidates : [];
      crmCols = d?.columns || {};
      crmTables = Array.isArray(d?.tables) ? d.tables : [];
      // Default-check everything not already existing.
      const sel: Record<string, boolean> = {};
      for (const c of crmCandidates) sel[c.name] = !c.already_exists;
      crmSelected = sel;
    } catch { crmCandidates = []; }
  }

  async function seedSelectedCrm() {
    if (crmSeeding) return;
    const names = crmCandidates.filter((c) => crmSelected[c.name]).map((c) => c.name);
    if (names.length === 0) { alert('Select at least one metric.'); return; }
    crmSeeding = true;
    try {
      const r = await fetch(`${apiBase()}/metrics/seed-crm`, { method: 'POST', headers: _h(), body: JSON.stringify({ names }) });
      const d = await r.json().catch(() => ({}));
      const n = Array.isArray(d?.seeded) ? d.seeded.length : 0;
      alert(n ? `Seeded ${n} CRM metric(s) (status: suggested — review & confirm).` : `No CRM metrics seeded${d?.skipped_reason ? ' (' + d.skipped_reason + ')' : ''}.`);
      await loadMetrics();
      view = 'dashboard';
    } catch (e) {
      alert('Seed CRM failed: ' + e);
    } finally {
      crmSeeding = false;
    }
  }

  // ─── Badges ───────────────────────────────────────────────────────
  function statusBadge(status: string) {
    if (status === 'verified') return '#22c55e';
    if (status === 'deprecated') return '#6b7280';
    return '#f59e0b';
  }
</script>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- METRICS TAB ROOT                                           -->
<!-- ═══════════════════════════════════════════════════════════ -->
<div class="met-root">

  <!-- ── B1 DASHBOARD ── -->
  {#if view === 'dashboard'}
    <!-- Toolbar -->
    <div class="met-toolbar">
      <!-- Row 1: title + summary -->
      <div class="met-tb-row met-tb-head">
        <div class="cli-strip">
          <span class="cli-prompt">$</span>
          <span class="cli-cmd">dash definitions</span>
          <span class="cli-dim">{slug}</span>
        </div>
        <div class="met-summary">
          <span><b>{Array.isArray(metrics) ? metrics.length : 0}</b> metrics</span>
          <span><b>{Array.isArray(rules) ? rules.length : 0}</b> rules</span>
          <span><b>{Array.isArray(reviewQueue) ? reviewQueue.length : 0}</b> in review</span>
        </div>
      </div>

      <!-- Row 2: filters + view toggle (left) · search + actions (right) -->
      <div class="met-tb-row met-tb-controls">
        <div class="met-tb-left">
          <div class="met-typefilter">
            <button class="met-chip" class:on={typeFilter === 'all'} onclick={() => typeFilter = 'all'}>All</button>
            <button class="met-chip" class:on={typeFilter === 'metrics'} onclick={() => typeFilter = 'metrics'}>✅ Metrics</button>
            <button class="met-chip" class:on={typeFilter === 'rules'} onclick={() => typeFilter = 'rules'}>📝 Rules</button>
          </div>
          <div class="met-viewtoggle">
            <button class="met-vbtn" class:on={dispMode === 'table'} onclick={() => dispMode = 'table'}>▤ Table</button>
            <button class="met-vbtn" class:on={dispMode === 'cards'} onclick={() => dispMode = 'cards'}>▦ Cards</button>
            <button class="met-vbtn" class:on={dispMode === 'ai'} onclick={() => dispMode = 'ai'}>🧁 AI ({sugCounts.all})</button>
          </div>
        </div>
        <div class="met-actions">
          <input class="met-search" type="search" placeholder="Search definitions…" bind:value={metricsSearch} />
          <button class="met-btn" onclick={() => {
            editSpec = emptySpec(); editIsNew = true; testResult = null; editError = '';
            editMode = 'describe'; nlText = ''; nlResult = null; resetBuilder();
            view = 'editor'; loadColumns();
          }}>+ New</button>
          <div class="met-more-wrap">
            <button class="met-btn-ghost" onclick={() => showMore = !showMore}>More ▾</button>
            {#if showMore}
              <div class="met-more-menu">
                <button onclick={() => { showMore = false; loadTemplates(); view = 'templates'; }}>Import template</button>
                <button onclick={() => { showMore = false; nlText = ''; nlResult = null; view = 'nl-draft'; }}>NL describe</button>
                <button onclick={() => { showMore = false; importText = ''; importResult = null; view = 'import'; }}>Import file</button>
                <button onclick={() => { showMore = false; loadPermissions(); view = 'permissions'; }}>Permissions</button>
                <button onclick={() => { showMore = false; loadDrift(); view = 'drift'; }}>Drift</button>
                {#if crmEligible}
                  <button onclick={openCrmPick} title="Preview & pick universal CRM metrics resolved to this project's columns (status: suggested)">＋ Add CRM metrics…</button>
                {/if}
              </div>
            {/if}
          </div>
          {#if Array.isArray(reviewQueue) && reviewQueue.length > 0}
            <button class="met-btn-warn" onclick={() => { view = 'review-queue'; }}>Review ({reviewQueue.length})</button>
          {/if}
        </div>
      </div>
    </div>

    <!-- ══ AI sub-tab ══ -->
    {#if dispMode === 'ai'}
      <div class="met-sug-tabview">
        <!-- Header panel -->
        <div class="met-sug-panel">
          <div class="met-sug-bar">
            <span class="met-sug-title">🧁 AI Suggestions <span class="met-sug-count">{sugCounts.all}</span></span>
            <button class="met-link" onclick={() => { loadRecNew(); loadDrift(); loadChatSug(); }}>↻ refresh</button>
          </div>
          <div class="met-sug-sub">Dash analyzed your schema, rules, drift, and chats. Accept or reject below.</div>
          <!-- source tabs (segmented) -->
          <div class="met-srctabs">
            <button class="met-srctab" class:on={sugSource === 'all'} onclick={() => sugSource = 'all'}>All <span class="met-srcnum">{sugCounts.all}</span></button>
            <button class="met-srctab" class:on={sugSource === 'human'} onclick={() => sugSource = 'human'}>🧑 Human <span class="met-srcnum">{sugCounts.human}</span></button>
            <button class="met-srctab" class:on={sugSource === 'training'} onclick={() => sugSource = 'training'}>🎓 Training AI <span class="met-srcnum">{sugCounts.training}</span></button>
            <button class="met-srctab" class:on={sugSource === 'chat'} onclick={() => sugSource = 'chat'}>💬 From Chat <span class="met-srcnum">{sugCounts.chat}</span></button>
          </div>
        </div>

        {#if recLoading && allSug.length === 0}
          <div class="met-rec-dim">Scanning schema…</div>
        {:else if sugByTab.length === 0}
          <div class="met-rec-dim">No suggestions in this source.</div>
        {/if}

        {#each sugByTab as sg}
          {@const d = sg.data}
          <div class="met-card met-sug-card">
            {#if sg.stype === 'new'}
              <div class="met-card-head"><div class="met-card-title">
                <span class="met-type met-type-new">✨ NEW METRIC</span>
                <span class="met-card-name">{d.name}</span>
              </div></div>
              <div class="met-card-def">{d.description || ''}</div>
              <div class="met-card-meta">{d.kind}{d.reason ? ' · why: ' + d.reason : ''} · source: 🎓 training</div>
              <div class="met-card-actions">
                <button class="met-btn met-small" onclick={() => createFromSuggestion(d)}>✓ Accept</button>
                <button class="met-link-danger" onclick={() => { recDismissed = new Set([...recDismissed, 'new:' + (d.name || '')]); }}>✕ Reject</button>
              </div>
            {:else if sg.stype === 'promote'}
              <div class="met-card-head"><div class="met-card-title">
                <span class="met-type met-type-rule">📝 PROMOTE RULE</span>
                <span class="met-card-name">{d.name} → metric</span>
              </div></div>
              <div class="met-card-def">{d.definition || ''}</div>
              <div class="met-card-meta">source: {sg.source === 'human' ? '🧑 human-authored rule' : '🎓 auto-suggested rule'}</div>
              <div class="met-card-actions">
                <button class="met-btn met-small" onclick={() => promoteRule(d)}>✓ Promote</button>
                <button class="met-link-danger" onclick={() => { recDismissed = new Set([...recDismissed, 'rule:' + (d.name || '').toLowerCase()]); }}>✕ Reject</button>
              </div>
            {:else if sg.stype === 'drift'}
              <div class="met-card-head"><div class="met-card-title">
                <span class="met-type met-type-drift">⚠ DRIFT</span>
                <span class="met-card-name">{d.name}</span>
              </div></div>
              <div class="met-card-def">Pinned {d.pinned} ≠ live {d.live}. Re-test and re-lock the definition.</div>
              <div class="met-card-meta">source: 🎓 drift monitor</div>
              <div class="met-card-actions">
                <button class="met-btn met-small" onclick={() => loadMetricForEdit(d.name)}>Re-test</button>
              </div>
            {:else}
              <div class="met-card-head"><div class="met-card-title">
                <span class="met-type met-type-chat">💬 FROM CHAT</span>
                <span class="met-card-name">{d.name}</span>
              </div></div>
              <div class="met-card-def">{d.definition || ''}</div>
              <div class="met-card-meta">{(d.type || 'rule').replace('_',' ')}{d.created_at ? ' · ' + String(d.created_at).slice(0,10) : ''} · source: 💬 chat</div>
              <div class="met-card-actions">
                <button class="met-btn met-small" onclick={() => acceptChatSug(d)}>✓ Accept</button>
                <button class="met-link-danger" onclick={() => rejectChatSug(d)}>✕ Reject</button>
              </div>
            {/if}
          </div>
        {/each}
      </div>

    <!-- ══ definitions (table / cards) ══ -->
    {:else if metricsLoading}
      <div class="met-skel-wrap">
        {#each [1,2,3] as _}
          <div class="met-skel"></div>
        {/each}
      </div>
    {:else if unifiedRows.length === 0}
      <div class="met-empty">
        <div>No {typeFilter === 'rules' ? 'rules' : typeFilter === 'metrics' ? 'metrics' : 'definitions'} yet.</div>
        <button class="met-btn" onclick={() => { editSpec = emptySpec(); editIsNew = true; editMode = 'describe'; nlText = ''; nlResult = null; resetBuilder(); view = 'editor'; loadColumns(); }}>+ Create first metric</button>
      </div>

    <!-- ── TABLE VIEW (dense, default) — definitions + business rules running ── -->
    {:else if dispMode === 'table'}
      <div class="met-table-wrap">
        <table class="met-table met-deftable">
          <thead><tr>
            <th>Name</th><th>Type</th><th>Kind</th><th>Definition</th><th>Pinned</th><th>Drift</th><th>Actions</th>
          </tr></thead>
          <tbody>
            {#each unifiedRows as m}
              {#if m._rowtype === 'metric'}
                {@const driftRow = driftData.find((d: any) => d.name === m.name)}
                <tr class="met-clickrow" onclick={() => loadMetricForEdit(m.name)} title="Click row to edit">
                  <td class="met-name">{m.name}</td>
                  <td><span class="met-type met-type-metric">✅ METRIC</span></td>
                  <td>{m.kind || '—'}</td>
                  <td class="met-td-def">{m.description || (Array.isArray(m.group_dims) ? m.group_dims.join(', ') : (m.group_dims || '—'))}</td>
                  <td class="met-pin">{formatPin(m.verified_answer)}</td>
                  <td>{#if driftRow}<span style="color:{driftRow.ok ? '#16a34a' : '#b45309'}">{driftRow.ok ? '✓' : '⚠'}</span>{:else}—{/if}</td>
                  <td class="met-row-actions" onclick={(e) => e.stopPropagation()}>
                    <button class="met-link" onclick={() => loadMetricForEdit(m.name)}>Edit</button>
                    <button class="met-link" onclick={() => { drawerView = 'history'; drawerMetricName = m.name; loadHistory(m.name); }}>Hist</button>
                    <button class="met-link-danger" onclick={() => deprecateMetric(m.name)} title="Deprecate">🗑</button>
                  </td>
                </tr>
              {:else}
                <tr class="met-clickrow met-rule-row" onclick={() => promoteRule(m)} title="Click row to promote → metric editor">
                  <td class="met-name">{m.name}</td>
                  <td><span class="met-type met-type-rule">📝 RULE</span></td>
                  <td>{(m.type || 'rule').replace('_', ' ')}</td>
                  <td class="met-td-def">{m.definition || '—'}</td>
                  <td>—</td>
                  <td>—</td>
                  <td class="met-row-actions" onclick={(e) => e.stopPropagation()}>
                    <button class="met-link" onclick={() => promoteRule(m)}>Promote</button>
                    {#if onOpenRules}<button class="met-link" onclick={() => onOpenRules?.()}>Rules</button>{/if}
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>

    <!-- ── CARDS VIEW ── -->
    {:else}
      <!-- Definition cards (Business-Rules style) -->
      <div class="met-cards">
        {#each unifiedRows as m}
          {#if m._rowtype === 'metric'}
            {@const driftRow = driftData.find((d: any) => d.name === m.name)}
            <div class="met-card">
              <div class="met-card-head">
                <div class="met-card-title">
                  <span class="met-card-name">{m.name}</span>
                  <span class="met-type met-type-metric">✅ METRIC · {m.kind || '—'} · {m.status || 'draft'}</span>
                </div>
                <button class="met-card-del" title="Deprecate" onclick={() => deprecateMetric(m.name)} aria-label="Deprecate">🗑</button>
              </div>
              <div class="met-card-def">{m.description || (Array.isArray(m.group_dims) ? m.group_dims.join(', ') : (m.group_dims || '—'))}</div>
              <div class="met-card-meta">
                pinned: {formatPin(m.verified_answer)}
                {#if Array.isArray(m.group_dims) && m.group_dims.length}· group: {m.group_dims.join(', ')}{/if}
                · drift: {#if driftRow}{driftRow.ok ? '✓ matches' : `⚠ pinned ${driftRow.pinned} vs live ${driftRow.live}`}{:else}—{/if}
              </div>
              <div class="met-card-actions">
                <button class="met-link" onclick={() => loadMetricForEdit(m.name)}>Edit</button>
                <button class="met-link" onclick={() => { drawerView = 'history'; drawerMetricName = m.name; loadHistory(m.name); }}>History</button>
                <button class="met-link" onclick={() => { drawerView = 'aliases'; drawerMetricName = m.name; aliasesText = Array.isArray(m.synonyms) ? m.synonyms.join(', ') : ''; }}>Aliases</button>
              </div>
            </div>
          {:else}
            <div class="met-card met-card-rule">
              <div class="met-card-head">
                <div class="met-card-title">
                  <span class="met-card-name">{m.name}</span>
                  <span class="met-type met-type-rule">📝 RULE · {(m.type || 'rule').replace('_', ' ')}</span>
                </div>
              </div>
              <div class="met-card-def">{m.definition || '—'}</div>
              {#if m.source || m.created_at}
                <div class="met-card-meta">{m.source === 'user' ? 'USER-DEFINED' : 'AUTO-SUGGESTED'}{m.created_at ? ' · ' + String(m.created_at).slice(0,10) : ''}</div>
              {/if}
              <div class="met-card-actions">
                <button class="met-link" onclick={() => promoteRule(m)} title="Open metric editor prefilled — add filters, test, lock">Promote → Metric</button>
                {#if onOpenRules}<button class="met-link" onclick={() => onOpenRules?.()}>Edit in Rules</button>{/if}
              </div>
            </div>
          {/if}
        {/each}
      </div>
    {/if}

  <!-- ── B2/B6 EDITOR ── -->
  {:else if view === 'editor'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics {editIsNew ? 'create' : 'edit'}</span>
        <span class="cli-dim">{editSpec.name || '<new>'}</span>
      </div>
      <div class="met-actions">
        <button class="met-btn-ghost" onclick={() => { view = 'dashboard'; testResult = null; }}>← Back</button>
        <button class="met-btn-ghost" onclick={() => { drawerView = 'schema'; loadSchemaExplorer(); }}>Schema explorer</button>
        {#if !editIsNew}
          <button class="met-btn-ghost" onclick={() => { drawerView = 'history'; drawerMetricName = editSpec.name; loadHistory(editSpec.name); }}>History</button>
        {/if}
      </div>
    </div>

    <div class="met-editor-grid">
      <!-- Left: form -->
      <div class="met-form">
        <!-- Mode toggle: describe-it (LLM) vs manual builder -->
        <div class="met-modetoggle">
          <button class="met-modebtn" class:on={editMode === 'describe'} onclick={() => { editMode = 'describe'; }}>✨ Describe it</button>
          <button class="met-modebtn" class:on={editMode === 'manual'} onclick={() => { editMode = 'manual'; }}>⚙ Build manually</button>
        </div>

        {#if editMode === 'describe'}
          <!-- Conversational KPI builder: chat → AI recommends KPIs from real schema →
               refine in natural language → batch-generate with live progress → save all. -->
          <div class="met-describe">
            {#if buildPhase === 'chat'}
              <div class="met-chatlog">
                {#if chatMsgs.length === 0}
                  <div class="met-chatempty">
                    <div class="met-describe-q">What do you want to measure?</div>
                    <div class="met-dim">Ask "which KPIs can we build?" to see proposals, or describe one metric. Refine in plain English — the AI grounds on your real schema.</div>
                  </div>
                {/if}
                {#each chatMsgs as m}
                  <div class="met-msg met-msg-{m.role}">
                    <span class="met-msg-who">{m.role === 'user' ? 'you' : 'ai'}</span>
                    <div class="met-msg-body">{m.text}</div>
                  </div>
                {/each}
                {#if chatBusy}<div class="met-msg met-msg-ai"><span class="met-msg-who">ai</span><div class="met-msg-body met-dim">thinking…</div></div>{/if}

                {#if candidates.length}
                  <div class="met-candbox">
                    {#each candidates as c (c.id)}
                      <label class="met-candrow">
                        <input type="checkbox" checked={c.checked} onchange={() => toggleCand(c.id)} />
                        <span class="met-recname">{c.spec.name}</span>
                        <span class="met-reckind">{c.spec.kind}</span>
                        {#if c.spec.description}<span class="met-dim">{c.spec.description}</span>{/if}
                      </label>
                    {/each}
                    <button class="met-btn" onclick={genSelected} disabled={!candidates.some((c: any) => c.checked)}>
                      ⚡ Generate selected ({candidates.filter((c: any) => c.checked).length})
                    </button>
                  </div>
                {/if}
              </div>

              <div class="met-describe-actions">
                <input class="met-chatinput" bind:value={chatInput}
                  placeholder="which KPIs can we build? — or refine…"
                  onkeydown={(e) => { if (e.key === 'Enter') chatSend(); }} />
                <button class="met-btn" onclick={chatSend} disabled={chatBusy || !chatInput.trim()}>➤</button>
                <button class="met-btn-ghost met-small" onclick={() => { editMode = 'manual'; loadColumns(); }}>✎ Manual</button>
              </div>
            {:else if buildPhase === 'building'}
              <!-- Building phase: test each selected KPI in turn, plain-English explanation per KPI. -->
              <div class="met-buildhd">Building {candidates.filter((c: any) => c.checked).length} KPI(s)…</div>
              <div class="met-buildlist">
                {#each candidates.filter((c: any) => c.checked) as c (c.id)}
                  <div class="met-buildcard">
                    <div class="met-buildtop">
                      <span class="met-buildmark met-bm-{c.status}">
                        {c.status === 'done' ? '✓' : c.status === 'fail' ? '✗' : c.status === 'testing' ? '⟳' : '○'}
                      </span>
                      <span class="met-recname">{c.spec.name}</span>
                      <span class="met-buildval met-bm-{c.status}">
                        {#if c.status === 'done'}= {c.value ?? 'ok'}{:else if c.status === 'fail'}{c.error || 'failed'}{:else if c.status === 'testing'}testing…{:else}queued{/if}
                      </span>
                    </div>
                    <div class="met-builddesc">{explainSpec(c.spec)}</div>
                    <div class="met-buildcols">
                      columns used: {#if columnsOfSpec(c.spec).length}{columnsOfSpec(c.spec).join(' · ')}{:else}none — just counts rows{/if}
                    </div>
                  </div>
                {/each}
              </div>
              {#if editError}<div class="met-error">{editError}</div>{/if}
              <div class="met-describe-actions">
                <button class="met-btn" onclick={saveAll}
                  disabled={savingAll || !candidates.some((c: any) => c.checked && c.status === 'done')}>
                  {savingAll ? 'Saving…' : `✓ Save ${candidates.filter((c: any) => c.checked && c.status === 'done').length} KPI(s)`}
                </button>
                <button class="met-btn-ghost" onclick={() => { buildPhase = 'chat'; }}>← back to refine</button>
              </div>
            {:else}
              <!-- Done: plain "Created" confirmation. -->
              <div class="met-createdbox">
                <div class="met-createdhd">✓ Created {createdCount} KPI{createdCount === 1 ? '' : 's'} — live in Definitions</div>
                <div class="met-buildlist">
                  {#each candidates.filter((c: any) => c.checked && c.status === 'done') as c (c.id)}
                    <div class="met-buildcard">
                      <div class="met-buildtop">
                        <span class="met-buildmark met-bm-done">✓</span>
                        <span class="met-recname">{c.spec.name}</span>
                        <span class="met-buildval met-bm-done">= {c.value ?? 'ok'}</span>
                      </div>
                      <div class="met-builddesc">{explainSpec(c.spec)}</div>
                    </div>
                  {/each}
                </div>
                <div class="met-describe-actions">
                  <button class="met-btn" onclick={() => { resetBuilder(); view = 'dashboard'; }}>View in Definitions →</button>
                  <button class="met-btn-ghost" onclick={() => { resetBuilder(); }}>+ Build more KPIs</button>
                </div>
              </div>
            {/if}
          </div>
        {:else}
        <div class="met-field">
          <label>Name *</label>
          <input type="text" bind:value={editSpec.name} placeholder="e.g. weekly_active_users" />
        </div>
        <div class="met-field">
          <label>Synonyms (comma-separated)</label>
          <input type="text"
            value={Array.isArray(editSpec.synonyms) ? editSpec.synonyms.join(', ') : ''}
            oninput={(e) => { editSpec.synonyms = (e.currentTarget as HTMLInputElement).value.split(',').map(s => s.trim()).filter(Boolean); }}
            placeholder="wau, weekly actives" />
        </div>
        <div class="met-field">
          <label>Description</label>
          <textarea bind:value={editSpec.description} rows={2} placeholder="Business definition of this metric…"></textarea>
        </div>
        <div class="met-row">
          <div class="met-field">
            <label>Kind *</label>
            <select bind:value={editSpec.kind}>
              {#each ['count','rate','ratio','contribution','sum','avg'] as k}
                <option value={k}>{k}</option>
              {/each}
            </select>
          </div>
          <div class="met-field">
            <label>Status</label>
            <select bind:value={editSpec.status}>
              {#each ['draft','verified','deprecated'] as s}
                <option value={s}>{s}</option>
              {/each}
            </select>
          </div>
        </div>
        <div class="met-field">
          <label>Source glob</label>
          <input type="text" bind:value={editSpec.source_glob} placeholder="*.csv, sales/**" />
        </div>
        <div class="met-field">
          <label>Source tables <span class="met-hint">— pick from your data (leave empty = all)</span></label>
          {#if tableList.length}
            <div class="met-tablepick">
              {#each tableList as t}
                <label class="met-tablechip" class:on={Array.isArray(editSpec.source_tables) && editSpec.source_tables.includes(t)}>
                  <input type="checkbox"
                    checked={Array.isArray(editSpec.source_tables) && editSpec.source_tables.includes(t)}
                    onchange={(e) => {
                      const on = (e.currentTarget as HTMLInputElement).checked;
                      const cur = Array.isArray(editSpec.source_tables) ? editSpec.source_tables : [];
                      editSpec.source_tables = on ? [...cur, t] : cur.filter((x: string) => x !== t);
                    }} />
                  {t}
                </label>
              {/each}
            </div>
          {:else}
            <input type="text"
              value={Array.isArray(editSpec.source_tables) ? editSpec.source_tables.join(', ') : ''}
              oninput={(e) => { editSpec.source_tables = (e.currentTarget as HTMLInputElement).value.split(',').map(s => s.trim()).filter(Boolean); }}
              placeholder="loading tables…" />
          {/if}
        </div>

        <!-- Columns reference: every column in the checked tables, one-click → filter or group -->
        {#if scopedColumns.length}
          <div class="met-field">
            <label>Columns in selected tables <span class="met-hint">— #️⃣ number columns can be summed/averaged · click to use</span></label>
            <div class="met-colref">
              {#each scopedColumns as c}
                {@const num = isNumericCol(c.dtype)}
                <div class="met-colchip">
                  <span class="met-colname">{c.column}</span>
                  <span class="met-coltype" class:met-coltype-num={num}>{num ? '#️⃣ ' : ''}{c.dtype}</span>
                  {#if num}
                    <button class="met-colact met-colact-sum" title="Use as the sum/avg measure (sets KIND to sum)"
                      onclick={() => useAsMeasure(c.column)}
                      class:on={editSpec.measure_col === c.column}>
                      {editSpec.measure_col === c.column ? '✓ measure' : 'sum/avg'}
                    </button>
                  {/if}
                  <button class="met-colact" title="Add as filter" onclick={() => useAsFilter(c.column)}>filter</button>
                  <button class="met-colact" title="Add to group by"
                    onclick={() => useAsGroup(c.column)}
                    disabled={Array.isArray(editSpec.group_dims) && editSpec.group_dims.includes(c.column)}>group</button>
                </div>
              {/each}
            </div>
            <div class="met-hint">For a total/average, click <strong>sum/avg</strong> on a number column (e.g. amount, qty). For a headcount, leave KIND = count. To slice by a category, use <strong>group</strong>.</div>
          </div>
        {/if}

        {#if editSpec.kind === 'sum' || editSpec.kind === 'avg'}
          <div class="met-field">
            <label>Measure column</label>
            <select bind:value={editSpec.measure_col}>
              <option value="">— pick a numeric column —</option>
              {#each (numericColumns.length ? numericColumns : scopedColumns) as c}
                <option value={c.column}>{colLabel(c)}</option>
              {/each}
            </select>
          </div>
        {/if}

        <!-- B3 Filter rows -->
        <div class="met-section-hd">Filters</div>
        {#if Array.isArray(editSpec.filters)}
          {#each editSpec.filters as f, i}
            <div class="met-filter-row">
              <select value={f.col} onchange={(e) => updateFilter('filters', i, 'col', (e.currentTarget as HTMLSelectElement).value)}>
                <option value="">— column —</option>
                {#each scopedColumns as c}
                  <option value={c.column}>{colLabel(c)}</option>
                {/each}
              </select>
              <select value={f.op} onchange={(e) => updateFilter('filters', i, 'op', (e.currentTarget as HTMLSelectElement).value)}>
                {#each OPS as op}
                  <option value={op}>{op}</option>
                {/each}
              </select>
              {#if f.op !== 'IS NULL' && f.op !== 'IS NOT NULL'}
                {#if colSamples(f.col).length > 0}
                  <select value={f.value} onchange={(e) => updateFilter('filters', i, 'value', (e.currentTarget as HTMLSelectElement).value)}>
                    <option value="">— value —</option>
                    {#each colSamples(f.col) as s}
                      <option value={s}>{s}</option>
                    {/each}
                  </select>
                {:else}
                  <input type="text" value={f.value} oninput={(e) => updateFilter('filters', i, 'value', (e.currentTarget as HTMLInputElement).value)} placeholder={f.op === 'IN' ? 'a, b, c' : 'value'} />
                {/if}
              {/if}
              <label class="met-chk"><input type="checkbox" checked={f.trim} onchange={(e) => updateFilter('filters', i, 'trim', (e.currentTarget as HTMLInputElement).checked)} /> trim</label>
              <button class="met-link-danger" onclick={() => removeFilter('filters', i)}>✕</button>
            </div>
          {/each}
        {/if}
        <button class="met-btn-ghost met-small" onclick={() => addFilter('filters')}>+ Add filter</button>

        <!-- B4 Denominator filters for rate/ratio -->
        {#if editSpec.kind === 'rate' || editSpec.kind === 'ratio'}
          <div class="met-section-hd">Denominator filters</div>
          {#if Array.isArray(editSpec.denom_filters)}
            {#each editSpec.denom_filters as f, i}
              <div class="met-filter-row">
                <select value={f.col} onchange={(e) => updateFilter('denom_filters', i, 'col', (e.currentTarget as HTMLSelectElement).value)}>
                  <option value="">— column —</option>
                  {#each scopedColumns as c}
                    <option value={c.column}>{colLabel(c)}</option>
                  {/each}
                </select>
                <select value={f.op} onchange={(e) => updateFilter('denom_filters', i, 'op', (e.currentTarget as HTMLSelectElement).value)}>
                  {#each OPS as op}
                    <option value={op}>{op}</option>
                  {/each}
                </select>
                {#if f.op !== 'IS NULL' && f.op !== 'IS NOT NULL'}
                  {#if colSamples(f.col).length > 0}
                    <select value={f.value} onchange={(e) => updateFilter('denom_filters', i, 'value', (e.currentTarget as HTMLSelectElement).value)}>
                      <option value="">— value —</option>
                      {#each colSamples(f.col) as s}
                        <option value={s}>{s}</option>
                      {/each}
                    </select>
                  {:else}
                    <input type="text" value={f.value} oninput={(e) => updateFilter('denom_filters', i, 'value', (e.currentTarget as HTMLInputElement).value)} placeholder="value" />
                  {/if}
                {/if}
                <button class="met-link-danger" onclick={() => removeFilter('denom_filters', i)}>✕</button>
              </div>
            {/each}
          {/if}
          <button class="met-btn-ghost met-small" onclick={() => addFilter('denom_filters')}>+ Add denom filter</button>
        {/if}

        <!-- Group dims -->
        <div class="met-section-hd">Group by dims <span class="met-hint">— pick columns to break the metric down by</span></div>
        <div class="met-chips">
          {#if Array.isArray(editSpec.group_dims)}
            {#each editSpec.group_dims as dim}
              <span class="met-chip">{dim}
                <button onclick={() => { editSpec.group_dims = editSpec.group_dims.filter((d: string) => d !== dim); }}>✕</button>
              </span>
            {/each}
          {/if}
          <select class="met-chip-input"
            value=""
            onchange={(e) => {
              const v = (e.currentTarget as HTMLSelectElement).value;
              if (v && Array.isArray(editSpec.group_dims) && !editSpec.group_dims.includes(v)) {
                editSpec.group_dims = [...editSpec.group_dims, v];
              }
              (e.currentTarget as HTMLSelectElement).value = '';
            }}>
            <option value="">+ add dim…</option>
            {#each scopedColumns.filter((c: any) => !(Array.isArray(editSpec.group_dims) && editSpec.group_dims.includes(c.column))) as c}
              <option value={c.column}>{colLabel(c)}</option>
            {/each}
          </select>
        </div>

        <!-- Verified answer / pin truth -->
        <div class="met-field">
          <label>Pinned truth (verified_answer total)</label>
          <input type="text" bind:value={editSpec.verified_answer} placeholder="e.g. 12345" />
        </div>

        <!-- Footer actions -->
        {#if editError}
          <div class="met-error">{editError}</div>
        {/if}
        <div class="met-form-footer">
          <button class="met-btn" onclick={saveMetric} disabled={editSaving}>{editSaving ? 'Saving…' : 'Save metric'}</button>
          <button class="met-btn-ghost" onclick={testMetric} disabled={testLoading}>{testLoading ? 'Testing…' : '▶ Test live'}</button>
          <button class="met-btn-ghost" onclick={() => { showTierCompare = !showTierCompare; }}>Why lock?</button>
          {#if !editIsNew}
            <button class="met-link-danger" onclick={() => deprecateMetric(editSpec.name)}>Deprecate</button>
          {/if}
        </div>

        <!-- B8 Tier compare panel -->
        {#if showTierCompare}
          <div class="met-tier-panel">
            <div class="met-section-hd">Tier compare</div>
            <input type="text" bind:value={tierQuestion} placeholder="Question for tier compare…" />
            <button class="met-btn-ghost met-small" onclick={runTierCompare} disabled={tierLoading}>{tierLoading ? 'Comparing…' : 'Run tier compare'}</button>
            {#if tierResult}
              {#if tierResult.error}
                <div class="met-error">{tierResult.error}</div>
              {:else}
                <div class="met-tier-result">
                  <div><strong>Locked total:</strong> {tierResult.locked_total ?? '—'}</div>
                  {#if Array.isArray(tierResult.tiers)}
                    <table class="met-table met-small-table">
                      <thead><tr><th>Tier</th><th>Answer</th><th>Match</th><th>Error</th></tr></thead>
                      <tbody>
                        {#each tierResult.tiers as t}
                          <tr>
                            <td>{t.tier}</td>
                            <td>{t.answer ?? '—'}</td>
                            <td>{t.answer == tierResult.locked_total ? '✓' : '✗'}</td>
                            <td class="met-dim">{t.error || ''}</td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  {/if}
                </div>
              {/if}
            {/if}
          </div>
        {/if}
        {/if}
      </div>

      <!-- Right: B7 Test result -->
      <div class="met-test-panel">
        <div class="met-section-hd">Test result</div>
        {#if testLoading}
          <div class="met-skel"></div>
        {:else if testResult}
          <div class="met-test-result">
            <div class="met-test-status" style="color:{testResult.ok ? '#22c55e' : '#ef4444'}">
              {testResult.ok ? '✓ Pass' : '✗ Fail'}
              {#if testResult.total != null} — total: <strong>{testResult.total}</strong>{/if}
              {#if editSpec.verified_answer && testResult.total != null}
                {testResult.total == editSpec.verified_answer ? ' ✓ matches pin' : ' ⚠ differs from pin'}
              {/if}
            </div>
            {#if testResult.sql}
              <pre class="met-sql">{testResult.sql}</pre>
            {/if}
            {#if testResult.table_md}
              <pre class="met-table-md">{testResult.table_md}</pre>
            {/if}
            {#if testResult.error}
              <div class="met-error">{testResult.error}</div>
            {/if}
          </div>
        {:else}
          <div class="met-dim">Run ▶ Test live to see results</div>
        {/if}
      </div>
    </div>

  <!-- ── B5 NL DRAFT ── -->
  {:else if view === 'nl-draft'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics derive</span>
        <span class="cli-dim">--from-natural-language</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    <div class="met-section">
      <label>Describe the metric in plain language</label>
      <textarea bind:value={nlText} rows={4} placeholder="e.g. Count of active users who logged in at least once in the last 7 days, grouped by country…"></textarea>
      <button class="met-btn" onclick={deriveDraft} disabled={nlLoading || !nlText.trim()}>{nlLoading ? 'Drafting…' : 'Draft with LLM'}</button>
      {#if nlResult}
        {#if nlResult.error}
          <div class="met-error">{nlResult.error}</div>
        {:else}
          <div class="met-nl-result">
            <div class="met-section-hd">LLM draft — confidence: {nlResult.confidence ?? '?'}</div>
            <pre class="met-sql">{JSON.stringify(nlResult.spec, null, 2)}</pre>
            <div class="met-form-footer">
              <button class="met-btn" onclick={acceptNlDraft}>Accept → editor</button>
              <button class="met-btn-ghost" onclick={() => { nlResult = null; }}>Discard</button>
            </div>
          </div>
        {/if}
      {/if}
    </div>

  <!-- ── B11 REVIEW QUEUE ── -->
  {:else if view === 'review-queue'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics review-queue</span>
        <span class="cli-ok" style="margin-left:auto;">{Array.isArray(reviewQueue) ? reviewQueue.length : 0} pending</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    {#if !Array.isArray(reviewQueue) || reviewQueue.length === 0}
      <div class="met-empty">No drafts in review queue.</div>
    {:else}
      <div class="met-table-wrap">
        <table class="met-table">
          <thead><tr><th>Name</th><th>Kind</th><th>Description</th><th>Actions</th></tr></thead>
          <tbody>
            {#each reviewQueue as m}
              <tr>
                <td>{m.name}</td>
                <td>{m.kind || '—'}</td>
                <td class="met-dim">{m.description || '—'}</td>
                <td class="met-row-actions">
                  <button class="met-link" onclick={() => loadMetricForEdit(m.name)}>Edit</button>
                  <button class="met-btn met-small" onclick={() => approveMetric(m.name)}>Approve</button>
                  <button class="met-link-danger" onclick={() => deprecateMetric(m.name)}>Reject</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

  <!-- ── B12 TEMPLATES ── -->
  {:else if view === 'templates'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics templates</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    {#if !Array.isArray(templates) || templates.length === 0}
      <div class="met-empty">No templates available.</div>
    {:else}
      <div class="met-tpl-cards">
        {#each templates as tpl}
          <div class="met-tpl-card">
            <div class="met-tpl-card-name">{tpl.name || '(template)'}</div>
            <div class="met-dim">{tpl.description || ''}</div>
            <button class="met-btn met-small" onclick={() => {
              editSpec = { ...emptySpec(), ...tpl };
              if (!Array.isArray(editSpec.filters)) editSpec.filters = [];
              if (!Array.isArray(editSpec.denom_filters)) editSpec.denom_filters = [];
              if (!Array.isArray(editSpec.synonyms)) editSpec.synonyms = [];
              if (!Array.isArray(editSpec.group_dims)) editSpec.group_dims = [];
              editIsNew = true;
              view = 'editor';
              loadColumns();
            }}>Clone → editor</button>
          </div>
        {/each}
      </div>
    {/if}

  <!-- ── CRM STARTER PICKER ── -->
  {:else if view === 'crm-pick'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics add-crm</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    <div class="met-dim" style="margin:4px 0 10px;">
      Universal CRM metrics resolved to this project's columns. Saved as <b>suggested</b> (review &amp; confirm before relying on them).
      {#if Array.isArray(crmTables) && crmTables.length}<br/>Tables: {crmTables.join(', ')}{/if}
    </div>
    {#if !Array.isArray(crmCandidates) || crmCandidates.length === 0}
      <div class="met-empty">No CRM metrics resolvable for this schema.</div>
    {:else}
      <div style="display:flex; gap:8px; margin-bottom:10px;">
        <button class="met-btn-ghost met-small" onclick={() => { const s={...crmSelected}; for (const c of crmCandidates) s[c.name]=true; crmSelected=s; }}>Select all</button>
        <button class="met-btn-ghost met-small" onclick={() => { const s={...crmSelected}; for (const c of crmCandidates) s[c.name]=false; crmSelected=s; }}>Clear</button>
      </div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        {#each crmCandidates as c (c.name)}
          <label style="display:flex; align-items:flex-start; gap:10px; padding:8px 10px; border:1px solid var(--pw-border,#e3e0d6); background:var(--pw-surface,#fff); cursor:pointer;">
            <input type="checkbox" checked={!!crmSelected[c.name]} onchange={(e) => crmSelected = { ...crmSelected, [c.name]: (e.currentTarget as HTMLInputElement).checked }} style="margin-top:3px;" />
            <span style="flex:1; min-width:0;">
              <span style="font-weight:600; color:var(--pw-ink,#2c2a26);">{c.name}</span>
              <span style="font-size:10px; font-weight:700; text-transform:uppercase; color:var(--pw-muted,#888); margin-left:6px;">{c.kind}</span>
              {#if c.already_exists}<span style="font-size:10px; color:#a06000; margin-left:6px;">· already exists (will update)</span>{/if}
              <div class="met-dim" style="margin-top:2px;">{c.description}</div>
              {#if Array.isArray(c.columns_used) && c.columns_used.length}
                <div style="margin-top:3px; font-family:var(--pw-mono,monospace); font-size:11px; color:var(--pw-muted,#6f6e69);">cols: {c.columns_used.join(', ')}</div>
              {/if}
            </span>
          </label>
        {/each}
      </div>
      <div style="display:flex; gap:8px; margin-top:14px;">
        <button class="met-btn" onclick={seedSelectedCrm} disabled={crmSeeding}>{crmSeeding ? 'Seeding…' : `Add ${crmCandidates.filter((c) => crmSelected[c.name]).length} metric(s)`}</button>
        <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>Cancel</button>
      </div>
    {/if}

  <!-- ── B13 IMPORT ── -->
  {:else if view === 'import'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics import</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    <div class="met-section">
      <label>Paste JSON array of metric specs</label>
      <textarea bind:value={importText} rows={8} placeholder='[name, kind, source_glob, ...]'></textarea>
      <button class="met-btn" onclick={doImport} disabled={importLoading || !importText.trim()}>{importLoading ? 'Importing…' : 'Import'}</button>
      {#if importResult}
        {#if importResult.error}
          <div class="met-error">{importResult.error}</div>
        {:else}
          <div class="met-ok">Created: {importResult.created ?? 0}. Errors: {Array.isArray(importResult.errors) ? importResult.errors.length : 0}</div>
          {#if Array.isArray(importResult.errors) && importResult.errors.length > 0}
            <ul>
              {#each importResult.errors as e}
                <li class="met-error">{typeof e === 'string' ? e : JSON.stringify(e)}</li>
              {/each}
            </ul>
          {/if}
        {/if}
      {/if}
    </div>

  <!-- ── B14 DRIFT MONITOR ── -->
  {:else if view === 'drift'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics drift</span>
      </div>
      <div class="met-actions">
        <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
        <button class="met-btn-ghost" onclick={loadDrift}>↻ Refresh</button>
      </div>
    </div>
    {#if !Array.isArray(driftData) || driftData.length === 0}
      <div class="met-empty">No drift data. Run metrics tests to populate.</div>
    {:else}
      <div class="met-table-wrap">
        <table class="met-table">
          <thead><tr><th>Metric</th><th>Pinned</th><th>Live</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            {#each driftData as d}
              <tr>
                <td>{d.name}</td>
                <td>{d.pinned ?? '—'}</td>
                <td>{d.live ?? '—'}</td>
                <td><span style="color:{d.ok ? '#22c55e' : '#f59e0b'}">{d.ok ? '✓ OK' : '⚠ Drift'}</span></td>
                <td>
                  <button class="met-link" onclick={() => loadMetricForEdit(d.name)}>Re-test</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

  <!-- ── B16 PERMISSIONS ── -->
  {:else if view === 'permissions'}
    <div class="met-toolbar">
      <div class="cli-strip">
        <span class="cli-prompt">$</span>
        <span class="cli-cmd">dash metrics permissions</span>
      </div>
      <button class="met-btn-ghost" onclick={() => view = 'dashboard'}>← Back</button>
    </div>
    {#if !permissions}
      <div class="met-empty">Loading permissions…</div>
    {:else}
      <div class="met-section">
        <div class="met-section-hd">Role → Capability Matrix (read-only)</div>
        <div class="met-table-wrap">
          <table class="met-table">
            <thead>
              <tr>
                <th>Role</th>
                {#each Object.values(permissions)[0] ? Object.keys(Object.values(permissions)[0] as any) : [] as cap}
                  <th>{cap}</th>
                {/each}
              </tr>
            </thead>
            <tbody>
              {#each Object.entries(permissions) as [role, caps]}
                <tr>
                  <td><strong>{role}</strong></td>
                  {#each Object.values(caps as any) as v}
                    <td style="color:{v ? '#22c55e' : '#6b7280'}">{v ? '✓' : '✗'}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}
  {/if}

  <!-- ══════════════════════════════════════════════════════════ -->
  <!-- DRAWERS                                                   -->
  <!-- ══════════════════════════════════════════════════════════ -->

  <!-- B9 Schema explorer drawer -->
  {#if drawerView === 'schema'}
    <div class="met-drawer-backdrop" onclick={() => drawerView = null}></div>
    <div class="met-drawer">
      <div class="met-drawer-hd">
        <span>Schema Explorer</span>
        <button class="met-link" onclick={() => drawerView = null}>✕</button>
      </div>
      <input type="text" class="met-search" placeholder="Filter columns…" bind:value={schemaTableFilter} />
      {#if schemaLoading}
        <div class="met-skel"></div>
      {:else if !Array.isArray(schemaColumns) || schemaColumns.length === 0}
        <div class="met-dim">No schema data.</div>
      {:else}
        {@const filtered = schemaColumns.filter((c: any) => !schemaTableFilter || (c.column || '').toLowerCase().includes(schemaTableFilter.toLowerCase()) || (c.table || '').toLowerCase().includes(schemaTableFilter.toLowerCase()))}
        {#each filtered as c}
          <div class="met-schema-row">
            <div class="met-schema-col">
              <span class="met-kind">{c.column}</span>
              <span class="met-dim">{c.table} · {c.dtype} · {c.distinct ?? '?'} distinct</span>
            </div>
            <div class="met-schema-samples">
              {#if Array.isArray(c.samples)}
                {#each c.samples.slice(0,4) as s}
                  <span class="met-sample">{s}</span>
                {/each}
              {/if}
            </div>
            <div class="met-schema-actions">
              <button class="met-link" onclick={() => useAsFilter(c.column)}>+ filter</button>
              <button class="met-link" onclick={() => useAsGroup(c.column)}>+ group</button>
            </div>
          </div>
        {/each}
      {/if}
    </div>
  {/if}

  <!-- B10 History drawer -->
  {#if drawerView === 'history'}
    <div class="met-drawer-backdrop" onclick={() => drawerView = null}></div>
    <div class="met-drawer">
      <div class="met-drawer-hd">
        <span>History — {drawerMetricName}</span>
        <button class="met-link" onclick={() => drawerView = null}>✕</button>
      </div>
      {#if historyLoading}
        <div class="met-skel"></div>
      {:else if !Array.isArray(historyData) || historyData.length === 0}
        <div class="met-dim">No history.</div>
      {:else}
        {#each historyData as h, i}
          <div class="met-history-row">
            <div><strong>v{h.version ?? i+1}</strong> · {h.created_at ? new Date(h.created_at).toLocaleString() : ''}</div>
            {#if h.diff}
              <pre class="met-diff">{h.diff}</pre>
            {/if}
            <button class="met-btn-ghost met-small" onclick={() => rollback(drawerMetricName, h.version ?? i+1)}>Rollback</button>
          </div>
        {/each}
      {/if}
    </div>
  {/if}

  <!-- B15 Aliases drawer -->
  {#if drawerView === 'aliases'}
    <div class="met-drawer-backdrop" onclick={() => drawerView = null}></div>
    <div class="met-drawer">
      <div class="met-drawer-hd">
        <span>Aliases — {drawerMetricName}</span>
        <button class="met-link" onclick={() => drawerView = null}>✕</button>
      </div>
      <div class="met-field">
        <label>Synonyms (comma-separated)</label>
        <textarea bind:value={aliasesText} rows={3} placeholder="wau, weekly_actives, weekly active users"></textarea>
      </div>
      <button class="met-btn" onclick={() => saveAliases(drawerMetricName)} disabled={aliasesSaving}>{aliasesSaving ? 'Saving…' : 'Save aliases'}</button>
    </div>
  {/if}

</div>

<style>
.met-root { font-family: var(--pw-font-body, 'JetBrains Mono', monospace); font-size: 13px; color: var(--pw-ink, #2c2a26); padding: 0; }
.met-toolbar { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; padding: 12px 14px; background: var(--pw-bg-alt, #f5f0e8); border: 1px solid var(--pw-muted, #c8c3b8); }
.met-tb-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.met-tb-head { padding-bottom: 8px; border-bottom: 1px solid var(--pw-muted, #c8c3b8); }
.met-summary { display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--pw-muted,#888); }
.met-summary b { color: var(--pw-ink,#2c2a26); font-weight: 700; }
.met-tb-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.met-tb-controls { row-gap: 8px; }
.met-more-wrap { position: relative; }
.met-more-menu { position: absolute; top: calc(100% + 4px); right: 0; z-index: 50; background: var(--pw-surface,#fff); border: 1px solid var(--pw-muted,#c8c3b8); display: flex; flex-direction: column; min-width: 150px; box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.met-more-menu button { text-align: left; padding: 7px 12px; background: none; border: none; border-bottom: 1px solid var(--pw-bg-alt,#eee); font-size: 12px; font-family: inherit; color: var(--pw-ink,#2c2a26); cursor: pointer; }
.met-more-menu button:last-child { border-bottom: none; }
.met-more-menu button:hover { background: var(--pw-bg-alt,#f5f0e8); }
.cli-strip { display: flex; align-items: center; gap: 6px; font-size: 13px; min-width: 0; }
.cli-prompt { color: var(--pw-accent, #c96342); font-weight: 700; }
.cli-cmd { color: var(--pw-ink, #2c2a26); font-weight: 600; }
.cli-dim { color: var(--pw-muted, #888); }
.cli-ok { color: #22c55e; font-size: 11px; }
.met-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.met-search { padding: 4px 8px; border: 1px solid var(--pw-muted, #c8c3b8); background: var(--pw-bg, #fdfaf5); font-size: 12px; font-family: inherit; outline: none; min-width: 140px; }
.met-btn { padding: 4px 12px; background: var(--pw-accent, #c96342); color: #fff; border: none; font-size: 12px; font-family: inherit; cursor: pointer; font-weight: 600; }
.met-btn:disabled { opacity: 0.5; cursor: default; }
.met-btn-ghost { padding: 4px 10px; background: transparent; color: var(--pw-ink, #2c2a26); border: 1px solid var(--pw-muted, #c8c3b8); font-size: 12px; font-family: inherit; cursor: pointer; }
.met-btn-ghost:disabled { opacity: 0.5; cursor: default; }
.met-btn-warn { padding: 4px 10px; background: #f59e0b22; color: #f59e0b; border: 1px solid #f59e0b44; font-size: 12px; font-family: inherit; cursor: pointer; }
.met-small { padding: 2px 8px; font-size: 11px; }
.met-link { background: none; border: none; color: var(--pw-accent, #c96342); cursor: pointer; font-size: 12px; font-family: inherit; text-decoration: underline; padding: 0 4px; }
.met-link-danger { background: none; border: none; color: #ef4444; cursor: pointer; font-size: 12px; font-family: inherit; text-decoration: underline; padding: 0 4px; }
.met-skel-wrap { display: flex; flex-direction: column; gap: 8px; }
.met-skel { height: 36px; background: linear-gradient(90deg, var(--pw-bg-alt,#f5f0e8) 25%, var(--pw-bg,#fdfaf5) 50%, var(--pw-bg-alt,#f5f0e8) 75%); background-size: 200% 100%; animation: met-shimmer 1.2s infinite; border: 1px solid var(--pw-muted,#c8c3b8); }
@keyframes met-shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
.met-empty { padding: 40px; text-align: center; color: var(--pw-muted,#888); display: flex; flex-direction: column; gap: 12px; align-items: center; }
.met-table-wrap { overflow-x: auto; }
.met-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.met-table th { background: var(--pw-bg-alt,#f5f0e8); border: 1px solid var(--pw-muted,#c8c3b8); border-radius: var(--pw-radius-sm)!important; padding: 6px 10px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted,#888); font-weight: 600; }
.met-table td { border: 1px solid var(--pw-muted,#c8c3b8); border-radius: var(--pw-radius-sm)!important; padding: 6px 10px; vertical-align: top; }
.met-table tr:hover td { background: var(--pw-bg-alt,#f5f0e8); }
.met-small-table { font-size: 11px; }
.met-name { cursor: pointer; color: var(--pw-accent,#c96342); font-weight: 600; }
.met-name:hover { text-decoration: underline; }
/* View toggle (Table / Cards / AI) */
.met-toggle-sep { width: 1px; height: 18px; background: var(--pw-muted,#c8c3b8); margin: 0 4px; }
.met-viewtoggle { display: inline-flex; border: 1px solid var(--pw-muted,#c8c3b8); }
.met-vbtn { padding: 3px 12px; font-size: 12px; font-family: inherit; cursor: pointer; background: var(--pw-bg,#fdfaf5); color: var(--pw-ink,#2c2a26); border: none; border-right: 1px solid var(--pw-muted,#c8c3b8); border-radius: var(--pw-radius-sm); }
.met-vbtn:last-child { border-right: none; }
.met-vbtn.on { background: var(--pw-ink,#2c2a26); color: #fff; font-weight: 600; }
/* Dense definitions table */
.met-deftable td { font-size: 12px; }
.met-deftable .met-clickrow { cursor: pointer; }
.met-deftable .met-clickrow:hover td { background: var(--pw-bg-alt,#f5f0e8); }
.met-td-def { max-width: 480px; color: var(--pw-ink,#2c2a26); line-height: 1.45; }
.met-sug-tabview { margin-top: 12px; border-top: none; padding-top: 0; }
.met-kind { display: inline-block; padding: 1px 6px; background: var(--pw-bg-alt,#f5f0e8); border: 1px solid var(--pw-muted,#c8c3b8); font-size: 11px; font-family: inherit; }
.met-btn-ai { padding: 4px 10px; background: #7c3aed; color: #fff; border: none; font-size: 12px; font-family: inherit; cursor: pointer; font-weight: 600; }
/* No rounded corners anywhere in this tab */
.met-root :where(.met-card, .met-type, .met-btn, .met-btn-ghost, .met-btn-warn, .met-btn-ai, .met-chip, .met-badge, .met-kind, .met-search, .met-sug-card, button, input, .met-sug-tab) { border-radius: var(--pw-radius-sm)!important; }
/* Card layout (Business-Rules style) */
.met-cards { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.met-card { border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-surface,#fff); padding: 12px 16px; }
.met-card-rule { background: var(--pw-bg-alt,#faf7f0); }
.met-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.met-card-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0; }
.met-card-name { font-size: 13px; font-weight: 900; color: var(--pw-ink,#2c2a26); }
.met-card-del { background: none; border: none; cursor: pointer; font-size: 13px; opacity: 0.5; }
.met-card-del:hover { opacity: 1; }
.met-card-def { font-size: 12px; color: var(--pw-ink,#2c2a26); margin-top: 6px; line-height: 1.5; }
.met-card-meta { font-size: 11px; color: var(--pw-muted,#888); margin-top: 6px; font-family: monospace; word-break: break-word; overflow-wrap: anywhere; }
.met-card-actions { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
/* AI suggestions */
.met-sug-tabview { display: flex; flex-direction: column; gap: 10px; }
.met-sug-panel { padding: 12px 14px; background: var(--pw-bg-alt,#f5f0e8); border: 1px solid var(--pw-muted,#c8c3b8); display: flex; flex-direction: column; gap: 8px; }
.met-sug-bar { display: flex; align-items: center; justify-content: space-between; }
.met-sug-title { font-size: 14px; font-weight: 700; color: var(--pw-ink,#2c2a26); display: flex; align-items: center; gap: 8px; }
.met-sug-count { font-size: 11px; font-weight: 700; padding: 1px 8px; background: #7c3aed; color: #fff; }
.met-sug-sub { font-size: 12px; color: var(--pw-muted,#888); }
.met-srctabs { display: flex; gap: 0; border: 1px solid var(--pw-muted,#c8c3b8); align-self: flex-start; flex-wrap: wrap; }
.met-srctab { padding: 5px 14px; font-size: 12px; font-family: inherit; cursor: pointer; background: var(--pw-surface,#fff); color: var(--pw-ink,#2c2a26); border: none; border-right: 1px solid var(--pw-muted,#c8c3b8); display: flex; align-items: center; gap: 6px; border-radius: var(--pw-radius-sm); }
.met-srctab:last-child { border-right: none; }
.met-srctab.on { background: var(--pw-ink,#2c2a26); color: #fff; font-weight: 600; }
.met-srcnum { font-size: 10px; font-weight: 700; padding: 0 6px; background: rgba(0,0,0,0.12); }
.met-srctab.on .met-srcnum { background: rgba(255,255,255,0.22); }
.met-sug-card { border-left: 3px solid #7c3aed; }
.met-type-new { background: #7c3aed22; color: #6d28d9; border-color: #7c3aed44; }
.met-type-drift { background: #f59e0b22; color: #b45309; border-color: #f59e0b44; }
.met-type-chat { background: #0ea5e922; color: #0369a1; border-color: #0ea5e944; }
.met-rec-dim { padding: 8px 0; font-size: 12px; color: var(--pw-muted,#888); }
.met-rec-sec { margin: 14px 0; border: 1px solid var(--pw-muted,#c8c3b8); }
.met-rec-head { background: var(--pw-bg-alt,#f5f0e8); padding: 6px 12px; font-size: 12px; font-weight: 700; border-bottom: 1px solid var(--pw-muted,#c8c3b8); }
.met-rec-dim { padding: 12px; font-size: 12px; color: var(--pw-muted,#888); }
.met-rec-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--pw-bg-alt,#eee); }
.met-rec-row:last-child { border-bottom: none; }
.met-rec-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.met-rec-name { font-weight: 600; color: var(--pw-accent,#c96342); font-size: 13px; }
.met-rec-desc { font-size: 12px; color: var(--pw-muted,#888); }
.met-typefilter { display: flex; gap: 6px; padding: 8px 0 4px; }
.met-chip { padding: 3px 12px; font-size: 12px; font-family: inherit; cursor: pointer; background: var(--pw-bg,#fdfaf5); color: var(--pw-ink,#2c2a26); border: 1px solid var(--pw-muted,#c8c3b8); border-radius: var(--pw-radius-sm); }
.met-chip.on { background: var(--pw-accent,#c96342); color: #fff; border-color: var(--pw-accent,#c96342); font-weight: 600; }
.met-type { display: inline-block; padding: 1px 7px; font-size: 11px; font-weight: 600; white-space: nowrap; border: 1px solid; }
.met-type-metric { background: #22c55e22; color: #16a34a; border-color: #22c55e44; }
.met-type-rule { background: #f59e0b22; color: #b45309; border-color: #f59e0b44; }
.met-rule-row td { background: var(--pw-bg-alt,#faf7f0); }
.met-badge { display: inline-block; padding: 1px 7px; font-size: 11px; font-weight: 600; border-radius: var(--pw-radius-sm); }
.met-dims { color: var(--pw-muted,#888); font-size: 11px; }
.met-pin { font-family: monospace; font-size: 11px; }
.met-drift { font-size: 12px; }
.met-dim { color: var(--pw-muted,#888); font-size: 11px; }
.met-row-actions { white-space: nowrap; }
.met-row-actions .met-link, .met-row-actions .met-link-danger { margin-right: 6px; }
/* Editor */
.met-editor-grid { display: grid; grid-template-columns: 1fr 340px; gap: 16px; align-items: start; }
@media (max-width: 900px) { .met-editor-grid { grid-template-columns: 1fr; } }
.met-form { display: flex; flex-direction: column; gap: 10px; }
.met-field { display: flex; flex-direction: column; gap: 3px; }
.met-field label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted,#888); font-weight: 600; }
.met-field input, .met-field select, .met-field textarea { padding: 6px 8px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 12px; color: var(--pw-ink,#2c2a26); outline: none; }
.met-field input:focus, .met-field select:focus, .met-field textarea:focus { border-color: var(--pw-accent,#c96342); }
.met-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.met-section-hd { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--pw-accent,#c96342); font-weight: 700; margin-top: 6px; padding-bottom: 4px; border-bottom: 1px solid var(--pw-muted,#c8c3b8); }
.met-filter-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 4px 0; }
.met-filter-row select, .met-filter-row input[type="text"] { padding: 4px 6px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 12px; outline: none; }
.met-chk { display: flex; align-items: center; gap: 4px; font-size: 12px; cursor: pointer; }
.met-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.met-chip { display: flex; align-items: center; gap: 4px; padding: 2px 8px; background: var(--pw-bg-alt,#f5f0e8); border: 1px solid var(--pw-muted,#c8c3b8); font-size: 12px; }
.met-chip button { background: none; border: none; cursor: pointer; color: var(--pw-muted,#888); font-size: 11px; padding: 0 2px; }
.met-chip-input { border: 1px dashed var(--pw-muted,#c8c3b8); background: transparent; padding: 2px 8px; font-size: 12px; font-family: inherit; outline: none; min-width: 80px; cursor: pointer; }
.met-hint { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--pw-muted,#999); font-size: 11px; }
.met-modetoggle { display: flex; gap: 0; margin-bottom: 4px; }
.met-modebtn { padding: 7px 16px; font-size: 13px; font-family: inherit; cursor: pointer; background: var(--pw-bg,#fdfaf5); color: var(--pw-ink,#2c2a26); border: 1px solid var(--pw-muted,#c8c3b8); }
.met-modebtn + .met-modebtn { border-left: none; }
.met-modebtn.on { background: var(--pw-accent,#c96342); color: #fff; border-color: var(--pw-accent,#c96342); font-weight: 600; }
.met-describe { display: flex; flex-direction: column; gap: 10px; }
.met-describe-q { font-size: 13px; font-weight: 600; color: var(--pw-ink,#2c2a26); }
.met-describe textarea { padding: 10px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 14px; outline: none; resize: vertical; }
.met-describe textarea:focus { border-color: var(--pw-accent,#c96342); }
.met-describe-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.met-draftcard { display: flex; flex-direction: column; gap: 7px; padding: 14px; background: var(--pw-bg-alt,#f7f6f3); border: 1px solid var(--pw-muted,#c8c3b8); }
.met-draftrow { display: flex; gap: 10px; align-items: baseline; font-size: 13px; }
.met-draftlbl { flex: 0 0 70px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted,#999); font-weight: 600; }
.met-conf { font-size: 10px; font-weight: 700; padding: 1px 6px; letter-spacing: 0.04em; }
.met-conf-high { background: rgba(201,99,66,0.14); color: var(--pw-accent,#c96342); }
.met-conf-medium { background: rgba(160,96,0,0.14); color: #a06000; }
.met-conf-low { background: rgba(192,57,42,0.12); color: #c0392b; }
.met-reclist { display: flex; flex-direction: column; gap: 6px; }
.met-reccard { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 8px 10px; background: var(--pw-bg,#fdfaf5); border: 1px solid var(--pw-muted,#c8c3b8); cursor: pointer; font-family: inherit; }
.met-reccard:hover { border-color: var(--pw-accent,#c96342); background: rgba(201,99,66,0.04); }
.met-recname { font-size: 13px; font-weight: 600; color: var(--pw-ink,#2c2a26); }
.met-reckind { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-accent,#c96342); }
/* Conversational KPI builder */
.met-chatlog { display: flex; flex-direction: column; gap: 10px; max-height: 420px; overflow-y: auto; padding: 4px 2px; }
.met-chatempty { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; }
.met-msg { display: flex; gap: 8px; align-items: flex-start; }
.met-msg-who { flex: 0 0 28px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; padding-top: 3px; }
.met-msg-user .met-msg-who { color: var(--pw-muted,#999); }
.met-msg-ai .met-msg-who { color: var(--pw-accent,#c96342); }
.met-msg-body { font-size: 13px; line-height: 1.5; color: var(--pw-ink,#2c2a26); background: var(--pw-bg-alt,#f7f6f3); padding: 7px 10px; border: 1px solid var(--pw-muted,#e2ddd2); flex: 1; }
.met-msg-user .met-msg-body { background: rgba(201,99,66,0.06); }
.met-candbox { display: flex; flex-direction: column; gap: 5px; padding: 10px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); }
.met-candrow { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; padding: 3px 0; }
.met-candrow input { margin: 0; }
.met-candbox .met-btn { margin-top: 6px; align-self: flex-start; }
.met-chatinput { flex: 1; padding: 8px 10px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 14px; outline: none; }
.met-chatinput:focus { border-color: var(--pw-accent,#c96342); }
.met-buildhd { font-size: 13px; font-weight: 600; color: var(--pw-ink,#2c2a26); margin-bottom: 4px; }
.met-buildlist { display: flex; flex-direction: column; gap: 4px; }
.met-buildrow { display: flex; align-items: center; gap: 10px; font-size: 13px; padding: 6px 10px; border: 1px solid var(--pw-muted,#e2ddd2); background: var(--pw-bg-alt,#f7f6f3); }
.met-buildmark { flex: 0 0 16px; font-weight: 700; text-align: center; }
.met-bm-done { color: var(--pw-accent,#c96342); }
.met-bm-fail { color: #c0392b; }
.met-bm-testing { color: #a06000; }
.met-bm-idle { color: var(--pw-muted,#999); }
.met-buildcard { display: flex; flex-direction: column; gap: 3px; padding: 8px 12px; border: 1px solid var(--pw-muted,#e2ddd2); background: var(--pw-bg-alt,#f7f6f3); }
.met-buildtop { display: flex; align-items: center; gap: 8px; }
.met-buildval { margin-left: auto; font-weight: 600; font-size: 13px; }
.met-builddesc { font-size: 12px; color: var(--pw-ink,#2c2a26); padding-left: 24px; }
.met-buildcols { font-size: 11px; color: var(--pw-muted,#999); padding-left: 24px; }
.met-createdbox { display: flex; flex-direction: column; gap: 10px; }
.met-createdhd { font-size: 15px; font-weight: 700; color: var(--pw-accent,#c96342); }
.met-tablepick { display: flex; flex-wrap: wrap; gap: 6px; }
.met-tablechip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; font-size: 12px; font-family: inherit; cursor: pointer; background: var(--pw-bg,#fdfaf5); color: var(--pw-ink,#2c2a26); border: 1px solid var(--pw-muted,#c8c3b8); user-select: none; }
.met-tablechip.on { background: var(--pw-accent,#c96342); color: #fff; border-color: var(--pw-accent,#c96342); font-weight: 600; }
.met-tablechip input { margin: 0; }
.met-colref { display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; border: 1px solid var(--pw-muted,#e2ddd2); padding: 6px; background: var(--pw-bg,#fdfaf5); }
.met-colchip { display: flex; align-items: center; gap: 8px; padding: 4px 6px; font-size: 12px; }
.met-colchip:hover { background: var(--pw-bg-alt,#f7f6f3); }
.met-colname { font-weight: 600; color: var(--pw-ink,#2c2a26); flex: 0 0 auto; }
.met-coltype { color: var(--pw-muted,#999); font-size: 11px; flex: 1; }
.met-colact { font-family: inherit; font-size: 11px; padding: 2px 8px; border: 1px solid var(--pw-muted,#c8c3b8); background: transparent; color: var(--pw-accent,#c96342); cursor: pointer; }
.met-colact:hover:not(:disabled) { background: var(--pw-accent,#c96342); color: #fff; border-color: var(--pw-accent,#c96342); }
.met-colact:disabled { color: var(--pw-muted,#bbb); cursor: default; }
.met-coltype-num { color: var(--pw-accent,#c96342); font-weight: 600; }
.met-colact-sum.on { background: var(--pw-accent,#c96342); color: #fff; border-color: var(--pw-accent,#c96342); }
.met-form-footer { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding-top: 8px; border-top: 1px solid var(--pw-muted,#c8c3b8); }
.met-error { color: #ef4444; font-size: 12px; padding: 4px 0; }
.met-ok { color: #22c55e; font-size: 12px; padding: 4px 0; }
/* Test panel */
.met-test-panel { border: 1px solid var(--pw-muted,#c8c3b8); padding: 12px; background: var(--pw-bg,#fdfaf5); display: flex; flex-direction: column; gap: 10px; }
.met-test-result { display: flex; flex-direction: column; gap: 8px; }
.met-test-status { font-size: 13px; font-weight: 600; }
.met-sql { background: #1a1614; color: #e8e3d6; font-family: monospace; font-size: 11px; padding: 10px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 0; }
.met-table-md { background: var(--pw-bg-alt,#f5f0e8); font-size: 11px; padding: 8px; overflow-x: auto; white-space: pre; margin: 0; }
/* Tier compare */
.met-tier-panel { border: 1px solid var(--pw-muted,#c8c3b8); padding: 12px; display: flex; flex-direction: column; gap: 8px; margin-top: 8px; background: var(--pw-bg,#fdfaf5); }
.met-tier-result { display: flex; flex-direction: column; gap: 6px; }
/* NL draft */
.met-section { display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid var(--pw-muted,#c8c3b8); }
.met-section label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--pw-muted,#888); font-weight: 600; }
.met-section textarea { padding: 8px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 12px; outline: none; resize: vertical; }
.met-section input[type="text"] { padding: 6px 8px; border: 1px solid var(--pw-muted,#c8c3b8); background: var(--pw-bg,#fdfaf5); font-family: inherit; font-size: 12px; outline: none; }
.met-nl-result { display: flex; flex-direction: column; gap: 8px; padding-top: 8px; border-top: 1px solid var(--pw-muted,#c8c3b8); }
/* Templates */
.met-tpl-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px,1fr)); gap: 12px; padding: 12px 0; }
.met-tpl-card { border: 1px solid var(--pw-muted,#c8c3b8); padding: 12px; display: flex; flex-direction: column; gap: 6px; background: var(--pw-bg,#fdfaf5); }
.met-tpl-card-name { font-weight: 600; font-size: 13px; }
/* Drawers */
.met-drawer-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 9000; }
.met-drawer { position: fixed; right: 0; top: 56px; bottom: 0; width: 420px; background: var(--pw-bg,#fdfaf5); border-left: 2px solid var(--pw-muted,#c8c3b8); z-index: 9001; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding: 16px; }
.met-drawer-hd { display: flex; justify-content: space-between; align-items: center; font-weight: 700; font-size: 13px; border-bottom: 1px solid var(--pw-muted,#c8c3b8); padding-bottom: 8px; }
.met-schema-row { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; border-bottom: 1px solid var(--pw-muted,#c8c3b8); }
.met-schema-col { display: flex; align-items: center; gap: 8px; }
.met-schema-samples { display: flex; flex-wrap: wrap; gap: 4px; }
.met-sample { padding: 1px 5px; background: var(--pw-bg-alt,#f5f0e8); border: 1px solid var(--pw-muted,#c8c3b8); font-size: 11px; }
.met-schema-actions { display: flex; gap: 6px; }
.met-history-row { border-bottom: 1px solid var(--pw-muted,#c8c3b8); padding: 8px 0; display: flex; flex-direction: column; gap: 6px; }
.met-diff { background: #1a1614; color: #e8e3d6; font-size: 11px; padding: 8px; overflow-x: auto; white-space: pre-wrap; margin: 0; }
</style>
