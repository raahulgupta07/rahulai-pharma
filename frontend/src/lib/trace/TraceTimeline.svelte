<script lang="ts">
  // ── Agent thinking trace — OpenAI-style, grouped by agent ───────────────────
  // Light theme. Steps/tools nest under collapsible AGENT sections (model on the
  // section header). Each step shows a bold humanized title, narrative, a light
  // function-call / SQL code box, a → result line, and tiny per-step meta
  // (tokens · duration). Collapsed header is a chip cluster showing the RESOLVED
  // model tier + the analysis type the agent chose + running totals.
  import type { TraceItem } from '$lib/api';
  import { costOf, shortModel, fmtTokens, fmtCost, fmtDuration } from './cost';

  interface RouterDecision {
    tier?: string | number;
    model?: string;
    target?: string;
    reason?: string;
    confidence?: number;
  }

  let {
    steps = [],
    reasoning = [],
    usage = null,
    routerDecision = null,
    mode = '',
    analysis = '',
    elapsedMs = 0,
    live = false,
  }: {
    steps?: TraceItem[];
    reasoning?: TraceItem[];
    usage?: { input_tokens: number; output_tokens: number; model?: string } | null;
    routerDecision?: RouterDecision | null;
    mode?: string;       // user reasoning mode: auto | fast | deep
    analysis?: string;   // user analysis-type setting (usually auto)
    elapsedMs?: number;
    live?: boolean;
  } = $props();

  const safeSteps = $derived(Array.isArray(steps) ? steps.filter(Boolean) : []);
  const safeReasoning = $derived(Array.isArray(reasoning) ? reasoning.filter(Boolean) : []);

  type Row = {
    kind: 'route' | 'tool' | 'step' | 'summarize';
    name?: string;
    label: string;
    narrative?: string;
    args?: any;
    status: 'running' | 'done' | 'error';
    duration?: string | number;
    model?: string;
    tokensIn?: number;
    tokensOut?: number;
    cost?: number;
    agent?: string;
    /** SQL validator auto-fix descriptions surfaced as inline coral pill. */
    autoFix?: string[];
  };

  const rows = $derived.by<Row[]>(() => {
    const out: Row[] = [];
    if (routerDecision) {
      const tier = routerDecision.tier != null ? String(routerDecision.tier) : '';
      const friendly = tierLabel(routerDecision.tier ?? routerDecision.target);
      const reason = routerDecision.reason ? sanitizeTiers(routerDecision.reason) : '';
      out.push({
        kind: 'route',
        label: friendly ? `Routed · ${friendly} tier` : 'Routed the request',
        narrative: reason || (tier ? `complexity: ${tierLabel(tier).toLowerCase()}` : undefined),
        status: 'done',
        model: routerDecision.model,
      });
    }
    const merged: TraceItem[] = safeReasoning.length ? [...safeSteps, ...safeReasoning] : safeSteps;
    for (const it of merged) {
      const a = it as any;
      if (it.kind === 'tool') {
        // ── auto_fix extraction (forward-compatible) ──
        // Pull from step metadata (`a.auto_fix`), args (`_validator_fix` indicator),
        // or args.sql vs args.sql_original drift. Always coerce to string[].
        let autoFix: string[] | undefined = undefined;
        try {
          const raw = a.auto_fix ?? (a.args && (a.args._validator_fix ?? a.args.validator_fix));
          if (Array.isArray(raw) && raw.length) autoFix = raw.map((x: any) => String(x)).filter(Boolean);
          else if (typeof raw === 'string' && raw.trim()) autoFix = [raw.trim()];
          // Fallback: sql vs sql_original drift → generic label
          if (!autoFix && a.args && typeof a.args === 'object') {
            const sqlNew = a.args.sql ?? a.args.query;
            const sqlOrig = a.args.sql_original;
            if (typeof sqlNew === 'string' && typeof sqlOrig === 'string' && sqlNew.trim() !== sqlOrig.trim()) {
              autoFix = ['SQL auto-corrected by validator'];
            }
          }
        } catch { /* fail-soft */ }
        out.push({
          kind: 'tool', name: a.name, label: a.name || 'tool',
          narrative: typeof a.result === 'string' ? a.result : undefined,
          args: a.args,
          status: a.status === 'run' ? 'running' : a.status === 'err' ? 'error' : 'done',
          duration: a.duration, model: a.model,
          tokensIn: typeof a.tokensIn === 'number' ? a.tokensIn : undefined,
          tokensOut: typeof a.tokensOut === 'number' ? a.tokensOut : undefined,
          cost: typeof a.cost === 'number' ? a.cost : undefined,
          agent: a.agent,
          autoFix,
        });
      } else {
        out.push({
          kind: 'step', label: a.title || 'Reasoning',
          narrative: typeof a.text === 'string' ? a.text : undefined,
          status: 'done', model: a.model,
          tokensIn: typeof a.tokensIn === 'number' ? a.tokensIn : undefined,
          tokensOut: typeof a.tokensOut === 'number' ? a.tokensOut : undefined,
          agent: a.agent,
        });
      }
    }
    if (out.length || live) {
      out.push({ kind: 'summarize', label: live ? 'Composing the answer…' : 'Composed the answer', status: live ? 'running' : 'done' });
    }
    return out;
  });

  // ── group consecutive same-agent rows under one section ──
  type Group = { agent: string | null; model?: string; rows: Row[] };
  const groups = $derived.by<Group[]>(() => {
    const gs: Group[] = [];
    let cur: Group | null = null;
    for (const r of rows) {
      const loose = r.kind === 'route' || r.kind === 'summarize' || !r.agent;
      if (loose) { cur = null; gs.push({ agent: null, rows: [r] }); continue; }
      if (!cur || cur.agent !== r.agent) { cur = { agent: r.agent!, model: r.model, rows: [] }; gs.push(cur); }
      if (!cur.model && r.model) cur.model = r.model;
      cur.rows.push(r);
    }
    return gs;
  });

  const agentsInvolved = $derived([...new Set(groups.filter((g) => g.agent).map((g) => g.agent as string))]);

  const totals = $derived.by(() => {
    let tin = 0, tout = 0; let model: string | undefined;
    if (usage && typeof usage.input_tokens === 'number') {
      tin = usage.input_tokens || 0; tout = usage.output_tokens || 0; model = usage.model;
    } else {
      for (const r of rows) {
        if (typeof r.tokensIn === 'number') tin += r.tokensIn;
        if (typeof r.tokensOut === 'number') tout += r.tokensOut;
        if (r.model) model = r.model;
      }
    }
    return { tin, tout, model, cost: costOf(tin, tout, model) };
  });

  // Aggregate SQL validator auto-fix count across all tool rows (for header chip).
  const sqlAutoFixes = $derived(
    rows.reduce<string[]>((acc, r) => {
      if (r.kind === 'tool' && Array.isArray(r.autoFix) && r.autoFix.length) acc.push(...r.autoFix);
      return acc;
    }, [])
  );
  const stepCount = $derived(rows.filter((r) => r.kind === 'tool' || r.kind === 'step').length);
  const toolCount = $derived(rows.filter((r) => r.kind === 'tool').length);
  const agentCount = $derived(agentsInvolved.length);
  const elapsedS = $derived((Math.max(0, elapsedMs) / 1000).toFixed(1));
  const totalTok = $derived(totals.tin + totals.tout);
  const workedFor = $derived.by(() => {
    const s = Math.max(0, Math.round(elapsedMs / 1000));
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  });

  // ── header chips ──
  // Map the router's complexity tier (TRIVIAL/LOOKUP/ANALYSIS/AGENTIC/REASONING/
  // ULTRA) onto the picker's model-tier labels (Instant/Standard/Deep/Ultra) so
  // the chip matches the model dropdown the user actually controls.
  function tierLabel(t: string | number | undefined | null): string {
    if (t == null) return '';
    const k = String(t).toUpperCase().trim();
    const map: Record<string, string> = {
      TRIVIAL: 'Instant', LOOKUP: 'Instant',
      ANALYSIS: 'Standard', ANALYTICAL: 'Standard', STANDARD: 'Standard',
      AGENTIC: 'Deep', REASONING: 'Deep', DEEP: 'Deep',
      ULTRA: 'Ultra',
      // already-friendly passthrough
      INSTANT: 'Instant', LITE: 'Instant', MID: 'Standard',
    };
    return map[k] || titleCase(k);
  }
  // Replace any raw router-tier token inside free text with its picker label,
  // so "score=0.00 → LOOKUP" reads "score=0.00 → Instant".
  function sanitizeTiers(s: string): string {
    if (!s) return s;
    return s.replace(/\b(TRIVIAL|LOOKUP|ANALYSIS|ANALYTICAL|AGENTIC|REASONING|ULTRA)\b/gi, (m) => tierLabel(m));
  }
  // Resolved tier (what actually ran), mapped to a picker label — not "auto".
  const tierChip = $derived(tierLabel(routerDecision?.tier ?? routerDecision?.target));
  // Effort level the run used (low/medium/high/max), from the router decision.
  const effortChip = $derived.by(() => {
    const e = (routerDecision as any)?.reasoning_effort ?? (routerDecision as any)?.effort;
    if (!e || typeof e !== 'string') return '';
    const v = e.toLowerCase();
    return v && v !== 'auto' ? `${titleCase(v)} effort` : '';
  });
  // Only show a mode chip when the user FORCED a non-auto mode.
  const modeChip = $derived(mode && mode.toLowerCase() !== 'auto' ? mode.toUpperCase() : '');
  // Analysis the agent chose — derive from an `analyze(analysis_type=…)` tool
  // call in the trace; fall back to the user's setting if non-auto.
  const analysisChip = $derived.by(() => {
    for (const r of rows) {
      if (r.kind !== 'tool') continue;
      const a: any = r.args;
      if (a && typeof a === 'object') {
        const at = a.analysis_type || a.analysisType || a.type;
        if (typeof at === 'string' && at && at.toLowerCase() !== 'auto') return at;
      }
      const m = (r.name || '').match(/^(diagnostic|comparator|trend|predictive|prescriptive|anomaly|root_cause|pareto|scenario|benchmark|descriptive)/i);
      if (m) return m[1].replace(/_/g, ' ');
    }
    return analysis && analysis.toLowerCase() !== 'auto' ? analysis : '';
  });

  // Collapse: expanded while live, auto-collapse when done; user can override.
  let collapsed = $state(false);
  let userToggled = $state(false);
  $effect(() => { if (!userToggled) collapsed = !live; });
  function toggle() { userToggled = true; collapsed = !collapsed; }

  // Per-agent-section collapse.
  let agentCollapsed = $state<Record<number, boolean>>({});
  function toggleAgent(i: number) { agentCollapsed = { ...agentCollapsed, [i]: !agentCollapsed[i] }; }

  // ── helpers ──
  function titleCase(s: string): string {
    if (!s) return '';
    return s.toLowerCase().replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }
  function snakeToTitle(name: string): string {
    if (!name) return 'Step';
    return name.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().replace(/\b\w/g, (c) => c.toUpperCase());
  }
  function sqlFromArgs(args: any): string {
    if (args == null) return '';
    if (typeof args === 'string') return /\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|EXPLAIN)\b/i.test(args) ? args : '';
    if (typeof args === 'object') {
      for (const k of ['query', 'sql', 'statement', 'sql_query', 'q']) {
        const v = (args as any)[k];
        if (typeof v === 'string' && v.trim()) return v;
      }
    }
    return '';
  }
  function prettySql(sql: string): string {
    if (!sql || typeof sql !== 'string') return '';
    let s = sql.replace(/\s+/g, ' ').trim();
    return s.replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|RIGHT JOIN|INNER JOIN|JOIN|UNION)\b/gi, '\n$1');
  }
  function callPairs(args: any): { k: string; v: string }[] {
    if (args == null || typeof args !== 'object' || Array.isArray(args)) return [];
    const out: { k: string; v: string }[] = [];
    for (const k of Object.keys(args)) {
      if (['query', 'sql', 'statement', 'sql_query', 'q'].includes(k)) continue;
      let v = (args as any)[k];
      if (v == null) continue;
      let s: string;
      if (typeof v === 'string') s = `"${v}"`;
      else { try { s = JSON.stringify(v); } catch { s = String(v); } }
      if (s.length > 2000) s = s.slice(0, 2000) + '…';
      out.push({ k, v: s });
    }
    return out;
  }
  function hasCode(r: Row): boolean {
    return r.kind === 'tool' && (!!sqlFromArgs(r.args) || callPairs(r.args).length > 0);
  }
  function cleanText(v: unknown, max = 90): string {
    let s = typeof v === 'string' ? v : (() => { try { return JSON.stringify(v); } catch { return String(v); } })();
    if (s == null) return '';
    s = s.replace(/[{}"]/g, '').replace(/\s+/g, ' ').trim();
    return s.length > max ? s.slice(0, max) + '…' : s;
  }
  function resultSummary(r: Row): string {
    if (r.kind !== 'tool') return '';
    const raw = r.narrative;
    if (raw == null || raw === '') return '';
    let parsed: any = null;
    try { parsed = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { parsed = null; }
    if (Array.isArray(parsed)) {
      if (parsed.length === 0) return '0 rows';
      if (parsed.length === 1 && parsed[0] && typeof parsed[0] === 'object')
        return Object.keys(parsed[0]).slice(0, 4).map((k) => `${k}: ${cleanText(parsed[0][k], 30)}`).join(' · ');
      return `${parsed.length} rows`;
    }
    if (parsed && typeof parsed === 'object')
      return Object.keys(parsed).slice(0, 4).map((k) => `${k}: ${cleanText(parsed[k], 30)}`).join(' · ');
    return '';
  }
  function title(r: Row): string {
    if (r.kind === 'route' || r.kind === 'summarize') return r.label;
    if (r.kind === 'step') return r.label && r.label !== 'reasoning' ? r.label : 'Reasoning';
    const name = (r.name || '').toLowerCase();
    if (name === 'search_learnings') return 'Checking memory for past learnings';
    if (name === 'delegate_task_to_member') {
      const m = r.args && (r.args.member_id || r.args.member || r.args.agent);
      return m ? `Planning with ${m}` : 'Planning with a teammate';
    }
    if (name === 'search_all' || name === 'recall' || name === 'search') return 'Searching internal knowledge';
    if (name === 'introspect_schema') {
      const t = r.args && (r.args.table || r.args.table_name || r.args.name);
      return t ? `Inspecting schema of ${t}` : 'Inspecting dataset schema';
    }
    if (name === 'run_sql_query' || name === 'run_sql') return 'Querying the database';
    if (name === 'auto_visualize') return 'Building the chart';
    if (name === 'analyze') return `Running ${cleanText(r.args?.analysis_type || 'analysis', 24)} analysis`;
    return snakeToTitle(r.name || r.label);
  }
  // Strip the agent's structured answer tags ([KPI:…], [HEADLINE:…], [MODE:…],
  // etc.) out of any trace text. These belong to the rendered INSIGHT card, not
  // the reasoning trace — when a reconstructed reasoning step carries the final
  // tagged answer (after refresh) we must not show the raw tags.
  function stripStructureTags(s: string): string {
    if (!s) return s;
    return s
      .replace(/\[[A-Z][A-Z0-9_]*:[^\]]*\]/g, '')   // [KPI:…] [SO_WHAT:…] …
      .replace(/\[[A-Z][A-Z0-9_]*\]/g, '')          // bare [HIGH] style
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
  // Reasoning steps should read as plain prose. Strip markdown tables, bold,
  // and heading markers the model drafts inside its reasoning (the formatted
  // table belongs in the answer / DATA tab, not the thinking trace). Keeps
  // live + reloaded reasoning visually identical.
  function cleanReasoning(s: string): string {
    if (!s) return s;
    return s
      .replace(/^\s*\|?[\s:|.\-–—]+\|[\s:|.\-–—]*$/gm, '')  // table separator rows
      .replace(/^\s*\|.*\|\s*$/gm, '')                       // table data rows
      .replace(/\*\*(.+?)\*\*/g, '$1')                       // **bold** → bold
      .replace(/^#{1,6}\s+/gm, '')                           // # headings
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
  function narrative(r: Row): string {
    if (r.kind === 'tool') {
      const name = (r.name || '').toLowerCase();
      if (name === 'delegate_task_to_member' && r.args && typeof r.args === 'object') {
        const t = r.args.task || r.args.task_description || r.args.message || r.args.instruction;
        if (typeof t === 'string' && t.trim()) return stripStructureTags(t.trim());
      }
      return '';
    }
    return cleanReasoning(stripStructureTags((r.narrative || '').trim()));
  }
  function stepMeta(r: Row): string {
    if (r.kind !== 'tool') return '';
    const parts: string[] = [];
    if (typeof r.tokensIn === 'number' || typeof r.tokensOut === 'number')
      parts.push(`↑${fmtTokens(r.tokensIn || 0)} ↓${fmtTokens(r.tokensOut || 0)}`);
    if (r.duration != null) parts.push(fmtDuration(r.duration));
    return parts.join(' · ');
  }
</script>

{#snippet rowItem(r: Row)}
  <li class="tt-row" class:tt-run={r.status === 'running'} class:tt-err={r.status === 'error'}>
    <span class="tt-marker"><span class="tt-circle tt-circle-{r.status}" class:tt-pulse={r.status === 'running'}></span></span>
    <div class="tt-content">
      <div class="tt-name">{title(r)}{#if r.kind === 'tool' && r.name}<span class="tt-tool-tag">{r.name}</span>{/if}</div>
      {#if r.kind === 'tool' && Array.isArray(r.autoFix) && r.autoFix.length}
        <div class="tt-autofix" title={r.autoFix.join(' · ')}>
          <span class="tt-autofix-icon">✨</span>
          <span class="tt-autofix-text">SQL auto-fixed{r.autoFix.length === 1 ? ` · ${r.autoFix[0]}` : ` · ${r.autoFix.length} fixes`}</span>
        </div>
      {/if}
      {#if narrative(r)}
        <p class="tt-narr">{narrative(r).length > 1800 ? narrative(r).slice(0, 1800) + '…' : narrative(r)}</p>
      {/if}
      {#if hasCode(r)}
        <div class="tt-code">
          {#if sqlFromArgs(r.args)}
            <pre class="tt-sql">{prettySql(sqlFromArgs(r.args))}</pre>
          {:else}
            <div class="tt-callhead"><span class="tt-fn">{r.name}</span> {'{'}</div>
            {#each callPairs(r.args) as p, pi}
              <div class="tt-callrow"><span class="tt-key">"{p.k}"</span>: <span class="tt-val">{p.v}</span>{pi < callPairs(r.args).length - 1 ? ',' : ''}</div>
            {/each}
            <div class="tt-callfoot">{'}'}</div>
          {/if}
        </div>
      {/if}
      <div class="tt-resline">
        {#if r.status === 'running' && r.kind === 'tool'}
          <span class="tt-result tt-result-run">running…</span>
        {:else if resultSummary(r)}
          <span class="tt-result">→ {resultSummary(r)}</span>
        {/if}
        {#if stepMeta(r)}<span class="tt-meta">{stepMeta(r)}</span>{/if}
      </div>
    </div>
  </li>
{/snippet}

{#if rows.length}
  <div class="tt" class:tt-live={live}>
    <button class="tt-head" onclick={toggle} type="button" aria-expanded={!collapsed}>
      <span class="tt-chev-l" class:open={!collapsed}>▸</span>
      <span class="tt-title">{live ? 'Thinking…' : 'Thinking'}</span>
      {#if tierChip}<span class="tt-chip tt-chip-route">{tierChip}</span>{/if}
      {#if modeChip}<span class="tt-chip tt-chip-mode">{modeChip}</span>{/if}
      {#if analysisChip}<span class="tt-chip tt-chip-analysis">{analysisChip}</span>{/if}
      {#if effortChip}<span class="tt-chip tt-chip-effort">{effortChip}</span>{/if}
      {#if sqlAutoFixes.length}<span class="tt-chip tt-chip-autofix" title={sqlAutoFixes.join(' · ')}>✨ {sqlAutoFixes.length} auto-fix{sqlAutoFixes.length === 1 ? '' : 'es'}</span>{/if}
      <span class="tt-stat">
        {#if agentCount > 0}{agentCount} agent{agentCount === 1 ? '' : 's'} · {/if}{toolCount} tool{toolCount === 1 ? '' : 's'} · {stepCount} step{stepCount === 1 ? '' : 's'}{#if elapsedMs > 0} · {workedFor}{/if}
      </span>
      {#if totalTok > 0}<span class="tt-stat">{fmtTokens(totalTok)} tok</span>{/if}
      {#if totals.cost > 0}<span class="tt-stat">{fmtCost(totals.cost)}</span>{/if}
      {#if totals.model}<span class="tt-model">{shortModel(totals.model)}</span>{/if}
      {#if live}<span class="tt-spin">●</span>{/if}
      <span class="tt-chev" class:open={!collapsed}>⌄</span>
    </button>

    {#if !collapsed}
      <div class="tt-groups">
        {#each groups as g, gi (gi)}
          {#if g.agent === null}
            <ol class="tt-list"><!-- loose row -->
              {@render rowItem(g.rows[0])}
            </ol>
          {:else}
            <div class="tt-agent">
              <button class="tt-agent-head" onclick={() => toggleAgent(gi)} type="button">
                <span class="tt-agent-chev" class:open={!agentCollapsed[gi]}>▸</span>
                <span class="tt-agent-name">{g.agent}</span>
                <span class="tt-agent-count">{g.rows.length} step{g.rows.length === 1 ? '' : 's'}</span>
                {#if g.model}<span class="tt-agent-model">{shortModel(g.model)}</span>{/if}
              </button>
              {#if !agentCollapsed[gi]}
                <ol class="tt-list tt-list-agent">
                  {#each g.rows as r, ri (ri)}{@render rowItem(r)}{/each}
                </ol>
              {/if}
            </div>
          {/if}
        {/each}
      </div>

      {#if !live && totalTok > 0}
        <div class="tt-foot">
          <span>{fmtTokens(totalTok)} tokens</span>
          <span class="tt-foot-sep">·</span><span>{fmtCost(totals.cost)}</span>
          {#if elapsedMs > 0}<span class="tt-foot-sep">·</span><span>{elapsedS}s</span>{/if}
          {#if agentsInvolved.length}<span class="tt-foot-sep">·</span><span>{agentsInvolved.length} agent{agentsInvolved.length === 1 ? '' : 's'}</span>{/if}
          {#if totals.model}<span class="tt-foot-model">{shortModel(totals.model)}</span>{/if}
        </div>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .tt {
    margin: 10px 0;
    font-family: var(--pw-sans, 'Inter', system-ui, sans-serif);
    font-size: 13.5px;
    color: var(--pw-ink, #2c2a26);
    background: var(--pw-bg-alt, #f7f6f3);
    border: 1px solid var(--pw-border, #e3e0d6);
    border-radius: 10px;
    padding: 6px 14px 10px;
  }
  .tt-live { box-shadow: 0 0 0 2px rgba(201, 99, 66, 0.16); border-color: rgba(201, 99, 66, 0.35); }

  /* header */
  .tt-head {
    display: flex; align-items: center; gap: 7px; width: 100%;
    padding: 5px 2px; background: transparent; border: none;
    color: var(--pw-muted, #6f6e69); cursor: pointer; text-align: left;
    font: inherit; flex-wrap: wrap;
  }
  .tt-head:hover .tt-title { color: var(--pw-ink, #2c2a26); }
  .tt-chev-l { color: var(--pw-dim, #97968f); font-size: 10px; transition: transform 0.15s ease; }
  .tt-chev-l.open { transform: rotate(90deg); }
  .tt-title { font-weight: 600; color: var(--pw-ink-soft, #4a4a48); font-size: 13px; }
  .tt-chip { padding: 1px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.03em; line-height: 1.5; }
  .tt-chip-route { background: rgba(201, 99, 66, 0.14); color: var(--pw-accent, #c96342); }
  .tt-chip-mode { background: var(--pw-bg-alt, #efece4); color: var(--pw-muted, #6f6e69); }
  .tt-chip-analysis { background: rgba(122, 162, 247, 0.14); color: #4f6bd0; text-transform: capitalize; }
  .tt-chip-effort { background: rgba(45, 106, 79, 0.13); color: #2d6a4f; }
  .tt-chip-autofix {
    background: rgba(201, 99, 66, 0.10);
    color: var(--pw-accent, #c96342);
    border: 1px solid rgba(201, 99, 66, 0.45);
  }
  /* Inline SQL auto-fixed pill — coral-bordered, between step title and code box. */
  .tt-autofix {
    display: inline-flex; align-items: center; gap: 5px;
    margin: 4px 0 0;
    padding: 2px 8px;
    font-size: 11px; line-height: 1.4; font-weight: 500;
    color: var(--pw-accent, #c96342);
    background: rgba(201, 99, 66, 0.06);
    border: 1px solid var(--pw-accent, #c96342);
    border-radius: 999px;
    max-width: 100%;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tt-autofix-icon { font-size: 11px; line-height: 1; }
  .tt-autofix-text { overflow: hidden; text-overflow: ellipsis; }
  .tt-agents-h { color: var(--pw-muted, #6f6e69); font-size: 11.5px; }
  .tt-stat { color: var(--pw-dim, #97968f); font-size: 12px; }
  .tt-model { margin-left: auto; color: var(--pw-dim, #97968f); font-size: 11px; font-family: var(--pw-mono, monospace); }
  .tt-chev { color: var(--pw-dim, #97968f); transition: transform 0.15s ease; }
  .tt-chev.open { transform: rotate(180deg); }

  /* agent sections */
  .tt-groups { margin-top: 6px; }
  .tt-agent { margin: 2px 0; }
  .tt-agent-head {
    display: flex; align-items: center; gap: 7px; width: 100%;
    padding: 4px 2px; background: transparent; border: none; cursor: pointer;
    text-align: left; font: inherit;
  }
  .tt-agent-chev { color: var(--pw-dim, #97968f); font-size: 9px; transition: transform 0.15s ease; }
  .tt-agent-chev.open { transform: rotate(90deg); }
  .tt-agent-name { font-weight: 700; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--pw-accent, #c96342); }
  .tt-agent-count { color: var(--pw-dim, #97968f); font-size: 11px; }
  .tt-agent-model { margin-left: auto; color: var(--pw-dim, #97968f); font-size: 10.5px; font-family: var(--pw-mono, monospace); }
  .tt-list-agent { padding-left: 14px; border-left: 1px solid var(--pw-border, #e3e0d6); margin-left: 5px; }

  /* timeline rows */
  .tt-list { list-style: none; margin: 0; padding: 0; }
  .tt-row { position: relative; display: flex; gap: 12px; padding: 0 0 13px 0; }
  .tt-row::before { content: ''; position: absolute; left: 6px; top: 14px; bottom: 0; width: 1px; background: var(--pw-border, #e3e0d6); }
  .tt-row:last-child::before { display: none; }
  .tt-marker { flex: 0 0 auto; width: 13px; display: flex; justify-content: center; padding-top: 3px; }
  .tt-circle { width: 9px; height: 9px; border-radius: 50%; border: 1.5px solid var(--pw-dim, #b8b5ab); background: var(--pw-bg, #faf9f5); box-sizing: border-box; }
  .tt-circle-done { border-color: var(--pw-accent, #c96342); background: var(--pw-accent, #c96342); }
  .tt-circle-error { border-color: #d3573a; background: #fff; }
  .tt-circle-running { border-color: var(--pw-accent, #c96342); background: transparent; }

  .tt-content { flex: 1 1 auto; min-width: 0; }
  .tt-name { font-weight: 600; font-size: 13.5px; color: var(--pw-ink, #2c2a26); line-height: 1.4; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .tt-tool-tag { font-weight: 400; font-size: 10.5px; color: var(--pw-dim, #97968f); font-family: var(--pw-mono, monospace); }
  .tt-err .tt-name { color: #b54a30; }

  .tt-narr { margin: 4px 0 0; color: var(--pw-ink-soft, #57544d); line-height: 1.6; font-size: 13.5px; white-space: pre-wrap; word-break: break-word; }

  .tt-code { margin: 8px 0 2px; padding: 10px 12px; border: 1px solid var(--pw-border, #e3e0d6); border-radius: 8px; background: var(--pw-bg, #fdfcf9); font-family: var(--pw-mono, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: 12.5px; line-height: 1.55; overflow-x: auto; }
  .tt-sql { margin: 0; color: var(--pw-ink-soft, #4a4a48); white-space: pre-wrap; word-break: break-word; font-family: inherit; }
  .tt-callhead, .tt-callfoot { color: var(--pw-ink-soft, #4a4a48); }
  .tt-callrow { padding-left: 16px; white-space: pre-wrap; word-break: break-word; }
  .tt-fn { color: var(--pw-ink, #2c2a26); font-weight: 600; }
  .tt-key { color: #b04f30; }
  .tt-val { color: #2d6a4f; }

  .tt-resline { display: flex; align-items: baseline; gap: 10px; margin: 6px 0 0; flex-wrap: wrap; }
  .tt-result { color: var(--pw-muted, #6f6e69); font-size: 12.5px; }
  .tt-result-run { color: var(--pw-accent, #c96342); }
  .tt-meta { margin-left: auto; color: var(--pw-dim, #97968f); font-size: 11px; font-family: var(--pw-mono, monospace); white-space: nowrap; }

  .tt-foot { display: flex; align-items: center; gap: 6px; margin-top: 2px; padding: 8px 0 0; border-top: 1px solid var(--pw-border, #e3e0d6); color: var(--pw-dim, #97968f); font-size: 12px; }
  .tt-foot-sep { color: var(--pw-border-strong, #d8d5cb); }
  .tt-foot-model { margin-left: auto; }

  .tt-pulse { animation: tt-pulse 1.1s ease-in-out infinite; }
  @keyframes tt-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
  .tt-spin { color: var(--pw-accent, #c96342); animation: tt-pulse 1.1s ease-in-out infinite; font-size: 9px; }
</style>
