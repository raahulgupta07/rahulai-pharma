<script>
  import { onMount } from 'svelte';
  import { formatInline, markdownToHtml } from '$lib';
  import Icon from '$lib/Icon.svelte';
  import {
    parseActionTitle,
    parseNarration,
    parseKpis,
    parseAttention,
    parseSegmentBreakdown,
    parseRecommendations,
    parseBenchmarks,
    parseScenarios,
    parseForecasts,
    parseRootCause,
    parseAudit,
    parseSkillUsed,
    parseMonograph,
    parseLead,
    parseWhy,
    parseSource,
    stripLegacyTags,
    scrubTags
  } from '$lib/answer-tags';

  let { content = '', tier = 'standard', msg = {}, onAction = () => {} } = $props();

  // Tier ranking
  const tierRank = { instant: 0, standard: 1, deep: 2, ultra: 3 };
  const rank = $derived(tierRank[tier] ?? 1);
  const tierLabel = $derived((tier || 'standard').toUpperCase());

  // ECharts dynamic
  let chartEl = $state(null);
  let chartInstance = $state(null);

  // Parse all tag blocks
  const titleParsed = $derived(parseActionTitle(content || ''));
  const narrationParsed = $derived(parseNarration(titleParsed.stripped));
  const kpisParsed = $derived(parseKpis(narrationParsed.stripped));
  const attentionParsed = $derived(parseAttention(kpisParsed.stripped));
  const segmentsParsed = $derived(parseSegmentBreakdown(attentionParsed.stripped));
  const rootCauseParsed = $derived(parseRootCause(segmentsParsed.stripped));
  const scenariosParsed = $derived(parseScenarios(rootCauseParsed.stripped));
  const benchmarksParsed = $derived(parseBenchmarks(scenariosParsed.stripped));
  const forecastsParsed = $derived(parseForecasts(benchmarksParsed.stripped));
  const recsParsed = $derived(parseRecommendations(forecastsParsed.stripped));
  const auditParsed = $derived(parseAudit(recsParsed.stripped));
  // Universal band tags (v1 lean contract).
  const leadParsed = $derived(parseLead(auditParsed.stripped));
  const whyParsed = $derived(parseWhy(leadParsed.stripped));
  const sourceParsed = $derived(parseSource(whyParsed.stripped));
  const skillUsed = $derived(parseSkillUsed(content || ''));
  // Strip any stray legacy tag (and truncated stream fragments) so no raw
  // bracketed tag can ever surface in the prose body.
  const cleanedBody = $derived(stripLegacyTags(sourceParsed.stripped));

  // Clinical monograph (chemist → chemist drug card). Present only when the
  // agent emitted a [DRUG:] tag → renders the monograph instead of exec blocks.
  const monoParsed = $derived(parseMonograph(content || ''));
  const mono = $derived(monoParsed.mono);
  const monoProse = $derived.by(() => {
    if (!mono) return '';
    let s = (monoParsed.stripped || '')
      .replace(/\[[A-Z][A-Z0-9_]{2,}:[^\]]*\]/g, '')
      .replace(/^\s*\|.*\|\s*$/gm, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    return s.length > 30 ? markdownToHtml(s) : '';
  });

  // ── Legacy-tag fallback helpers (so old assistant messages w/o new exec
  //    tags still render in exec layout). Pulls from HEADLINE / BECAUSE /
  //    SO_WHAT / ANCHOR / FINDING / ACTIONS / CONFIDENCE / RELATED. ──
  function _extractLegacyTag(re, src) {
    const out = [];
    const r = new RegExp(re.source, 'gi');
    let m;
    while ((m = r.exec(src || ''))) out.push(m[1].trim());
    return out;
  }
  const _legacyHeadline = $derived(_extractLegacyTag(/\[HEADLINE:\s*([^\]]+)\]/i, content)[0] || '');
  const _legacyBecause = $derived(_extractLegacyTag(/\[BECAUSE:\s*([^\]]+)\]/i, content)[0] || '');
  const _legacySoWhat = $derived(_extractLegacyTag(/\[SO_WHAT:\s*([^\]]+)\]/i, content));
  const _legacyFindings = $derived(_extractLegacyTag(/\[FINDING:\s*([^\]]+)\]/i, content));
  const _legacyActions = $derived(_extractLegacyTag(/\[ACTIONS:\s*([^\]]+)\]/i, content));
  const _legacyAnchor = $derived(_extractLegacyTag(/\[ANCHOR:\s*([^\]]+)\]/i, content)[0] || '');

  // Title / narration — prefer NEW tag, fall back to legacy HEADLINE.
  const actionTitle = $derived(titleParsed.items?.[0] || _legacyHeadline);
  // Narration: strip audit-style leakage (SOURCES/Tables/Rules/Confidence lines
  // and leading `---` separators) so block stays pure exec-prose.
  function cleanNarration(s) {
    if (!s) return '';
    return s
      .split('\n')
      .filter((ln) => {
        const t = ln.trim();
        if (!t) return false;
        if (/^---+$/.test(t)) return false;
        if (/^(sources?|tables?|rules?\s*applied|confidence|sql|columns?|row\s*count|schema)\s*:/i.test(t)) return false;
        return true;
      })
      .join(' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }
  const narration = $derived(cleanNarration(narrationParsed.items?.[0] || _legacyBecause || _legacyAnchor || ''));

  // Dedupe + sanitize KPIs:
  //  - drop duplicates by label (keep first w/ best change/status)
  //  - drop tiles w/ N/A or empty value
  //  - cap at 4 visible
  // Significant label tokens for cross-KPI dup detection: drop short/common
  // words so "BIOGESIC STOCK LEVEL" vs "HIGHEST STOCK (BIOGESIC)" share "biogesic".
  const _KPI_STOPWORDS = new Set([
    'the', 'and', 'for', 'with', 'stock', 'level', 'total', 'count',
    'highest', 'lowest', 'value', 'avg', 'average', 'sum', 'top', 'units'
  ]);
  function _kpiTokens(label) {
    return new Set(
      String(label || '')
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, ' ')
        .split(/\s+/)
        .filter((t) => t.length >= 4 && !_KPI_STOPWORDS.has(t))
    );
  }

  const kpiList = $derived.by(() => {
    const raw = kpisParsed.items || [];
    const seen = new Map();
    const kept = []; // {value, tokens, kpi}
    for (const k of raw) {
      const v = String(k.value ?? '').trim();
      const lbl = String(k.label ?? '').trim().toLowerCase();
      if (!lbl) continue;
      if (!v || v.toUpperCase() === 'N/A' || v === '—') continue;
      if (seen.has(lbl)) continue; // exact-label dup
      // Cross-label dup: same trimmed value AND a shared significant label token.
      const tokens = _kpiTokens(lbl);
      const isDup = kept.some((p) => {
        if (p.value !== v) return false;
        for (const t of tokens) if (p.tokens.has(t)) return true;
        return false;
      });
      if (isDup) continue;
      seen.set(lbl, k);
      kept.push({ value: v, tokens, kpi: k });
    }
    return kept.map((p) => p.kpi).slice(0, 4);
  });

  // Plain-English summary: strip ALL legacy + custom tags, KPI tags, markdown
  // tables, and HTML. Render as markdown HTML for clean prose.
  const summaryText = $derived.by(() => {
    if (!cleanedBody) return '';
    let s = cleanedBody
      // strip all storytelling tags (HEADLINE/FINDING/CONFIDENCE/CHART/MODE etc)
      .replace(/\[[A-Z][A-Z0-9_]{2,}:[^\]]*\]/g, '')
      // strip standalone bracketed flags (e.g. [CONFIRM_OUTLINE])
      .replace(/\[[A-Z][A-Z0-9_]{2,}\]/g, '')
      // strip markdown table rows + separator lines (| Col | Val | + | :--- |)
      .replace(/^\s*\|.*\|\s*$/gm, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    // Final orphan-bracket / truncated-tag scrub so no lone `]` or `[TAG: …`
    // fragment survives into the rendered prose.
    s = scrubTags(s).trim();
    if (!s) return '';
    // Render the remaining markdown prose to HTML so tables/bold/lists work
    return markdownToHtml(s);
  });

  // SO_WHAT / MEANS lines — only accept plain-text MEANS items (no pipes).
  // Filter out malformed lines that contain pipe-separated fields (those are
  // RECOMMENDATION-shaped, not MEANS-shaped).
  const meansItems = $derived.by(() => {
    const out = [];
    const re1 = /\[SO_WHAT:\s*([^\]]+)\]/gi;
    const re2 = /\[MEANS:\s*([^\]]+)\]/gi;
    let m;
    while ((m = re1.exec(content || ''))) {
      const v = m[1].trim();
      if (v && !v.includes('|')) out.push(v);
    }
    while ((m = re2.exec(content || ''))) {
      const v = m[1].trim();
      if (v && !v.includes('|')) out.push(v);
    }
    return out;
  });

  // RELATED chips — prefer [RELATED:] tags, fall back to scanning markdown
  // body for "Next Steps" / "Related" / "Follow-up" sections (agent often uses
  // these in DEEP/AGENTIC tier instead of explicit RELATED tags).
  const relatedItems = $derived.by(() => {
    const out = [];
    // 1) Explicit [RELATED:] tags
    const re = /\[RELATED:\s*([^\]]+)\]/gi;
    let m;
    while ((m = re.exec(content || ''))) {
      const parts = m[1].split('|').map((s) => s.trim()).filter(Boolean);
      out.push(...parts);
    }
    if (out.length) {
      const seen = new Set();
      const uniq = [];
      for (const q of out) {
        const k = q.toLowerCase();
        if (seen.has(k)) continue;
        seen.add(k); uniq.push(q);
      }
      return uniq.slice(0, 6);
    }

    // 2) Markdown section fallback — scan for heading then collect following
    //    bullet/numbered lines as related questions.
    const c = content || '';
    const sectionPatterns = [
      /(?:^|\n)#{1,4}\s*(?:next\s+steps?|follow[\s-]?ups?|related\s+questions?|you\s+might\s+also\s+ask|deeper\s+questions?|commonly\s+asked\s+next)[^\n]*\n([\s\S]+?)(?=\n#{1,4}|\n---|\n\n[A-Z]|$)/i,
      /(?:^|\n)\*\*\s*(?:next\s+steps?|follow[\s-]?ups?|related\s+questions?|you\s+might\s+also\s+ask|deeper\s+questions?|commonly\s+asked\s+next)\s*\*\*[^\n]*\n([\s\S]+?)(?=\n\*\*|\n#{1,4}|\n---|$)/i,
    ];
    for (const re2 of sectionPatterns) {
      const m2 = c.match(re2);
      if (m2) {
        const body = m2[1] || '';
        const lines = body.split('\n').map((l) => l.trim()).filter(Boolean);
        for (const ln of lines) {
          // strip bullet/number markers
          const cleaned = ln.replace(/^[-*•·]\s+/, '').replace(/^\d+[.)]\s+/, '').trim();
          // skip if it doesn't look like a question/action
          if (cleaned.length < 6 || cleaned.length > 160) continue;
          if (cleaned.startsWith('#') || cleaned.startsWith('|')) continue;
          out.push(cleaned);
          if (out.length >= 6) break;
        }
        if (out.length) break;
      }
    }
    return out.slice(0, 6);
  });

  // Top-N data from msg.data
  const topItems = $derived.by(() => {
    const data = msg?.data;
    if (!Array.isArray(data) || data.length === 0) return [];
    const sample = data[0] || {};
    const keys = Object.keys(sample);
    const numericKey = keys.find((k) => typeof sample[k] === 'number');
    const labelKey = keys.find((k) => typeof sample[k] === 'string') || keys[0];
    if (!numericKey) return [];
    const rows = data
      .map((r) => ({ label: String(r[labelKey] ?? ''), value: Number(r[numericKey] ?? 0) }))
      .filter((r) => Number.isFinite(r.value))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
    const max = Math.max(...rows.map((r) => r.value), 1);
    return rows.map((r) => ({ ...r, pct: (r.value / max) * 100 }));
  });

  const hasEcharts = $derived(!!(msg?.echartsOptions && typeof msg.echartsOptions === 'object'));

  // Block visibility flags
  const showNarration = $derived(narration && rank >= 1);
  const showSummary = $derived(summaryText && rank >= 1);
  const showKpis = $derived(kpiList.length > 0);
  const showTrend = $derived(hasEcharts);
  const showTopN = $derived(topItems.length > 0);
  const showAttention = $derived(attentionParsed.items?.length > 0 && rank >= 2);
  const showSegments = $derived(segmentsParsed.items?.length > 0 && rank >= 3);
  const showRootCause = $derived(rootCauseParsed.items?.length > 0 && rank >= 3);
  const showScenarios = $derived(scenariosParsed.items?.length > 0 && rank >= 3);
  const showBenchmarks = $derived(benchmarksParsed.items?.length > 0 && rank >= 3);
  const showForecasts = $derived(forecastsParsed.items?.length > 0 && rank >= 3);
  const showMeans = $derived(meansItems.length > 0);
  // Recommendations — prefer NEW [RECOMMENDATION:] tags, fall back to:
  //   • legacy [ACTION: label|type|param] tags
  //   • legacy [ACTIONS: n|action|owner|effort|risk] tags
  //   • synthesize from [SO_WHAT:] when nothing else exists
  // Goal: every answer shows at least 1 "what to do" item.
  const recsList = $derived.by(() => {
    if (recsParsed.items?.length) {
      return recsParsed.items.map((r) => ({
        priority: r.priority || '',
        action: r.action || r.text || '',
        impact: r.impact || '',
        effort: r.effort || '',
        cta_label: r.cta_label || 'Do it',
      }));
    }
    const out = [];
    // legacy [ACTION: label|type|param]
    const reA = /\[ACTION:\s*([^|\]]+)\|([^|\]]+)\|([^\]]+)\]/gi;
    let m;
    while ((m = reA.exec(content || ''))) {
      out.push({ priority: String(out.length + 1), action: m[1].trim(), impact: m[2].trim(), effort: m[3].trim(), cta_label: 'Do it' });
    }
    if (out.length) return out;
    // legacy [ACTIONS: n|action|owner|effort|risk]
    const reB = /\[ACTIONS:\s*([^\]]+)\]/gi;
    while ((m = reB.exec(content || ''))) {
      const f = m[1].split('|').map((s) => s.trim());
      out.push({ priority: f[0] || String(out.length + 1), action: f[1] || '', impact: f[2] || '', effort: f[3] || '', cta_label: 'Do it' });
    }
    if (out.length) return out;
    // synthesize from SO_WHAT (legacy storytelling)
    for (const s of _legacySoWhat.slice(0, 3)) {
      if (s && !s.includes('|')) {
        out.push({ priority: String(out.length + 1), action: s, impact: '', effort: '', cta_label: 'Do it' });
      }
    }
    return out;
  });
  const showRecs = $derived(recsList.length > 0);
  const showRelated = $derived(relatedItems.length > 0);
  const showAudit = $derived(auditParsed.items?.length > 0);

  // ── Universal "chemist-grade" band ──────────────────────────────────
  // One calm header band sits at the very top of every NON-chitchat answer.
  // Title = monograph salt if a monograph is present, else the [HEADLINE].
  const bandTitle = $derived(mono ? (mono.salt || '') : actionTitle);

  // Lead line (➜). Optional for monographs (they have their own summary).
  const bandLead = $derived(leadParsed.lead || '');

  // Confidence: [CONFIDENCE: HIGH|MEDIUM|LOW]. Drive a single dot + class.
  const _confRaw = $derived(
    (_extractLegacyTag(/\[CONFIDENCE:\s*([A-Za-z]+)\]/i, content)[0] || '').toUpperCase()
  );
  const confLevel = $derived(
    _confRaw === 'HIGH' || _confRaw === 'MEDIUM' || _confRaw === 'LOW' ? _confRaw : ''
  );
  function confGlyph(level) {
    if (level === 'HIGH') return '●';
    if (level === 'MEDIUM') return '◐';
    if (level === 'LOW') return '○';
    return '';
  }

  // Source chip (⌖). [SOURCE], falling back to a sensible default.
  const bandSource = $derived(
    sourceParsed.source || (mono ? 'catalog' : 'articles')
  );

  // Why bullets for the analytical body.
  const whyItems = $derived(whyParsed.items || []);

  // Evidence line text — prefer monograph evidence, else [EVIDENCE] / source.
  const _evidenceRaw = $derived(
    _extractLegacyTag(/\[EVIDENCE:\s*([^\]]+)\]/i, content)[0] || ''
  );
  const evidenceText = $derived(
    mono && mono.evidence
      ? [mono.evidence.article, mono.evidence.table].filter(Boolean).join(' · ')
      : _evidenceRaw || bandSource
  );

  // Is there ANY structured signal? If not (pure prose / chitchat) we skip the
  // band entirely and render plain markdown — no empty band for chitchat.
  const hasStructure = $derived(
    !!mono ||
    !!actionTitle ||
    kpiList.length > 0 ||
    whyItems.length > 0 ||
    meansItems.length > 0 ||
    !!confLevel ||
    !!sourceParsed.source ||
    showTrend ||
    showTopN
  );
  const showBand = $derived(hasStructure);

  // SO_WHAT — pipe-aware (action | owner | effort). meansItems drops piped
  // values, so parse it here for the calm "So what →" line.
  const soWhat = $derived.by(() => {
    const m = (content || '').match(/\[SO_WHAT:\s*([^\]]+)\]/i);
    if (!m) return null;
    const [action, owner, effort] = m[1].split('|').map((s) => s.trim());
    if (!action) return null;
    return { action, owner: owner || '', effort: effort || '' };
  });

  // Is there ANY structured signal at all (band / KPIs / monograph / why /
  // so-what / trend / topN / recs / related)? Used for the blank-body fallback
  // decision. NOTE: `hasStructure` (above) is the band-trigger; this is the
  // broader "is there anything to render besides prose" check.
  const hasAnyStructured = $derived(
    !!mono ||
    !!actionTitle ||
    kpiList.length > 0 ||
    whyItems.length > 0 ||
    meansItems.length > 0 ||
    recsList.length > 0 ||
    relatedItems.length > 0 ||
    !!confLevel ||
    !!sourceParsed.source ||
    showTrend ||
    showTopN
  );

  // Blank-body guard: everything got stripped (e.g. an all-tags answer that was
  // also truncated) AND there's no structured content to show → render a tiny
  // fallback line instead of an empty card. When structure DOES exist we render
  // the structured card with no prose paragraph (handled by showSummary).
  const fallbackBody = $derived(
    !summaryText && !narration && !hasAnyStructured
      ? '*(No answer text returned — please try rephrasing.)*'
      : ''
  );
  const showFallback = $derived(!!fallbackBody);

  onMount(async () => {
    if (!hasEcharts || !chartEl) return;
    try {
      const echarts = await import('echarts');
      chartInstance = echarts.init(chartEl);
      chartInstance.setOption(msg.echartsOptions);
      const ro = new ResizeObserver(() => chartInstance && chartInstance.resize());
      ro.observe(chartEl);
      return () => {
        ro.disconnect();
        chartInstance?.dispose();
      };
    } catch (e) {
      // silent
    }
  });

  function statusColor(status) {
    const s = (status || '').toLowerCase();
    if (s.includes('good') || s.includes('up') || s === 'green') return 'green';
    if (s.includes('warn') || s.includes('amber') || s.includes('flat')) return 'amber';
    if (s.includes('bad') || s.includes('down') || s === 'red') return 'red';
    return 'neutral';
  }

  function statusEmoji(status) {
    const c = statusColor(status);
    if (c === 'green') return '🟢';
    if (c === 'amber') return '🟡';
    if (c === 'red') return '🔴';
    return '⚪';
  }

  // Empty-delta gate: only show the change row for a real, non-zero value.
  const _EMPTY_DELTAS = new Set(['', '0', '--', '—', '-']);
  function hasRealDelta(kpi) {
    const d = String(kpi.delta ?? kpi.change ?? '').trim();
    return d !== '' && !_EMPTY_DELTAS.has(d);
  }

  function fmtMoney(v) {
    if (v == null || !Number.isFinite(Number(v))) return v ?? '';
    const n = Number(v);
    if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
    if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
    return `$${n.toFixed(0)}`;
  }
</script>

<article class="answer-card">
  <span class="tier-badge">{tierLabel}</span>

  {#if skillUsed}
    <span class="skill-chip" title="Applied skill: {skillUsed.name} (#{skillUsed.id})">
      <svg class="skill-chip-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/>
        <path d="M14.5 2a2.5 2.5 0 0 0-2.5 2.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/>
      </svg>
      <span class="skill-chip-name">{skillUsed.name}</span>
    </span>
  {/if}

  {#if mono}
    <!-- ─── Clinical monograph (chemist → chemist) ─── -->
    <div class="mono-head">
      <span class="mono-icon" aria-hidden="true">🧪</span>
      <div class="mono-title-wrap">
        <h2 class="mono-salt">{mono.salt}</h2>
        {#if mono.brand || mono.article}
          <div class="mono-sub">{#if mono.brand}{mono.brand}{/if}{#if mono.brand && mono.article} · {/if}{#if mono.article}<span class="mono-art">{mono.article}</span>{/if}</div>
        {/if}
      </div>
      {#if mono.status || mono.klass}
        <span class="mono-status">{[mono.status, mono.klass].filter(Boolean).join(' · ')}</span>
      {/if}
      <span class="band-meta">
        {#if confLevel}<span class="conf-dot conf-{confLevel.toLowerCase()}" title="Confidence: {confLevel}">{confGlyph(confLevel)} {confLevel}</span>{/if}
        <span class="src-chip" title="Source: {bandSource}">⌖ {bandSource}</span>
      </span>
    </div>

    {#if bandLead}
      <p class="band-lead"><span class="lead-arrow" aria-hidden="true">➜</span> {@html formatInline(bandLead)}</p>
    {/if}

    {#if mono.composition || mono.indication || mono.dose}
      <div class="mono-fields">
        {#if mono.composition}<div class="mono-row"><span class="mono-k">COMPOSITION</span><span class="mono-v">{mono.composition}</span></div>{/if}
        {#if mono.indication}<div class="mono-row"><span class="mono-k">INDICATION</span><span class="mono-v">{mono.indication}</span></div>{/if}
        {#if mono.dose}<div class="mono-row"><span class="mono-k">DOSE</span><span class="mono-v">{mono.dose}</span></div>{/if}
      </div>
    {/if}

    {#if mono.caution.length || mono.interacts.length}
      <div class="mono-safety">
        {#each mono.caution as c}
          <div class="mono-safety-row"><span class="mono-safety-tag">⚠ CAUTION</span><span class="mono-safety-text">{c}</span></div>
        {/each}
        {#each mono.interacts as it}
          <div class="mono-safety-row"><span class="mono-safety-tag">⚠ INTERACTS</span><span class="mono-safety-text">{it}</span></div>
        {/each}
      </div>
    {/if}

    {#if mono.stock}
      <div class="mono-strip">
        <span class="mono-strip-label">DISPENSING{#if mono.stock.branch} · branch {mono.stock.branch}{/if}</span>
        <div class="mono-strip-vals">
          <span class="mono-stock">{mono.stock.status || '✅'} {mono.stock.qty}{#if mono.stock.skus} · {mono.stock.skus} SKUs{/if}</span>
          {#if mono.stock.cost}<span class="mono-cost">COST {mono.stock.cost}</span>{/if}
        </div>
      </div>
    {/if}

    {#if mono.equiv.length}
      <hr />
      <div class="block-label">🔄 Therapeutic equivalents</div>
      <div class="mono-equiv">
        {#each mono.equiv as e}
          <div class="mono-equiv-row">
            <span class="mono-equiv-name">{e.name}</span>
            <span class="mono-equiv-meta">{#if e.qty}{e.qty} u{/if}{#if e.qty && (e.cost || e.article)} · {/if}{#if e.cost}{e.cost}{/if}{#if e.cost && e.article} · {/if}{#if e.article}<span class="mono-art">{e.article}</span>{/if}</span>
          </div>
        {/each}
      </div>
    {/if}

    {#if monoProse}
      <hr />
      <div class="summary-card">{@html monoProse}</div>
    {/if}

    {#if showRelated}
      <hr />
      <div class="block-label">Related questions</div>
      <div class="chips">
        {#each relatedItems as q}
          <button class="chip" onclick={() => onAction('related', q)}>{q}</button>
        {/each}
      </div>
    {/if}

    {#if mono.evidence}
      <div class="mono-evidence">🔗 evidence&nbsp;&nbsp;{mono.evidence.article}{#if mono.evidence.table} · {mono.evidence.table}{/if}<span class="mono-verified">✓ verified</span></div>
    {/if}
  {:else}

  {#if showBand && (bandTitle || confLevel || bandSource)}
    <!-- ─── Universal "chemist-grade" band (analytical) ─── -->
    <div class="chem-band">
      <div class="chem-band-title">
        <span class="band-hex" aria-hidden="true">⬡</span>
        <span class="band-title-text">{@html formatInline(bandTitle || 'Answer')}</span>
      </div>
      <div class="band-meta">
        {#if confLevel}<span class="conf-dot conf-{confLevel.toLowerCase()}" title="Confidence: {confLevel}">{confGlyph(confLevel)} {confLevel}</span>{/if}
        <span class="src-chip" title="Source: {bandSource}">⌖ {bandSource}</span>
      </div>
    </div>
    {#if bandLead}
      <p class="band-lead"><span class="lead-arrow" aria-hidden="true">➜</span> {@html formatInline(bandLead)}</p>
    {/if}
  {:else if actionTitle}
    <h2 class="action-title">
      <span class="title-icon" aria-hidden="true">🧪</span>
      {@html formatInline(actionTitle)}
    </h2>
  {/if}

  {#if showNarration}
    <p class="narration">{@html formatInline(narration)}</p>
  {/if}

  {#if showSummary}
    <hr />
    <div class="summary-card">
      {@html summaryText}
    </div>
  {/if}

  {#if showFallback}
    <!-- Everything got stripped and there's no structured content to show.
         Render a minimal note rather than a blank card. -->
    <p class="fallback-note">{@html formatInline(fallbackBody)}</p>
  {/if}

  {#if showKpis}
    <hr />
    <div class="kpi-strip">
      {#each kpiList as kpi}
        {@const color = statusColor(kpi.status)}
        {@const emoji = statusEmoji(kpi.status)}
        <div class="kpi-tile kpi-{color}">
          <div class="kpi-label">{kpi.label || ''}</div>
          <div class="kpi-value">{kpi.value ?? ''}</div>
          <div class="kpi-meta">
            {#if hasRealDelta(kpi)}
              <span class="kpi-delta kpi-delta-{color}">{kpi.delta || kpi.change}</span>
            {/if}
            {#if emoji && emoji !== '⚪'}
              <span class="kpi-emoji">{emoji}</span>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  {#if whyItems.length}
    <div class="why-block">
      <div class="why-label">Why</div>
      <ul class="why-list">
        {#each whyItems as w}
          <li>{@html formatInline(w)}</li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if soWhat}
    <div class="sowhat-line">
      <span class="sowhat-arrow" aria-hidden="true">So what →</span>
      <span class="sowhat-action">{@html formatInline(soWhat.action)}</span>
      {#if soWhat.owner || soWhat.effort}
        <span class="sowhat-meta">({[soWhat.owner, soWhat.effort].filter(Boolean).join(' · ')})</span>
      {/if}
    </div>
  {/if}

  {#if showBand && evidenceText}
    <div class="evidence-row" title="Grounding">▸ Evidence&nbsp;&nbsp;{evidenceText}</div>
  {/if}

  {#if showTrend}
    <hr />
    <div class="trend-wrap">
      <div bind:this={chartEl} class="trend-chart"></div>
    </div>
  {/if}

  {#if showTopN}
    <hr />
    <div class="topn">
      <div class="block-label">Top items</div>
      {#each topItems as row}
        <div class="topn-row">
          <span class="topn-label">{row.label}</span>
          <div class="topn-bar-wrap">
            <div class="topn-bar" style="width: {row.pct}%"></div>
          </div>
          <span class="topn-val">{row.value.toLocaleString()}</span>
        </div>
      {/each}
    </div>
  {/if}

  {#if showAttention}
    <hr />
    <div class="block-label">Needs attention</div>
    <div class="attention-list">
      {#each attentionParsed.items as item}
        <div class="attention-card">
          <div class="attention-head">
            <strong>{item.sku || item.id || ''}</strong>
            <span class="muted">{item.name || ''}</span>
          </div>
          <div class="attention-grid">
            {#if item.days_out != null}
              <div><span class="muted">Days out:</span> <strong>{item.days_out}</strong></div>
            {/if}
            {#if item.daily_demand != null}
              <div><span class="muted">Daily demand:</span> <strong>{item.daily_demand}</strong></div>
            {/if}
            {#if item.loss_per_day != null}
              <div><span class="muted">Loss/day:</span> <strong>{fmtMoney(item.loss_per_day)}</strong></div>
            {/if}
          </div>
          {#if item.action}
            <button class="btn-action" onclick={() => onAction('attention', item)}>{item.action}</button>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if showSegments}
    <hr />
    <div class="block-label">Segment breakdown</div>
    <div class="segments">
      {#each segmentsParsed.items as seg}
        {@const pct = Math.max(0, Math.min(100, Number(seg.share ?? seg.pct ?? 0)))}
        {@const color = statusColor(seg.status || (Number(seg.delta) >= 0 ? 'up' : 'down'))}
        <div class="seg-row">
          <div class="seg-label">{seg.name || seg.label || ''}</div>
          <div class="seg-bar-wrap">
            <div class="seg-bar seg-bar-{color}" style="width: {pct}%"></div>
          </div>
          {#if seg.delta != null}
            <div class="seg-delta seg-delta-{color}">{seg.delta}</div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if showRootCause}
    <hr />
    <div class="block-label">Root cause</div>
    <ol class="root-list">
      {#each rootCauseParsed.items as rc, i}
        {@const pct = Math.max(0, Math.min(100, Number(rc.contribution ?? rc.pct ?? 0)))}
        <li>
          <div class="root-head">
            <span class="root-num">{i + 1}</span>
            <span class="root-text">{@html formatInline(rc.cause || rc.text || '')}</span>
            <span class="root-pct">{pct}%</span>
          </div>
          <div class="root-bar-wrap">
            <div class="root-bar" style="width: {pct}%"></div>
          </div>
        </li>
      {/each}
    </ol>
  {/if}

  {#if showScenarios}
    <hr />
    <div class="block-label">Scenario modeling</div>
    <div class="scenarios">
      {#each scenariosParsed.items as sc}
        <blockquote class="scenario">
          {#if sc.name}<div class="scenario-name">{sc.name}</div>{/if}
          <div class="scenario-body">{@html formatInline(sc.description || sc.text || '')}</div>
          {#if sc.impact}<div class="scenario-impact muted">Impact: {sc.impact}</div>{/if}
        </blockquote>
      {/each}
    </div>
  {/if}

  {#if showBenchmarks}
    <hr />
    <div class="block-label">Benchmark</div>
    <table class="benchmark-tbl">
      <thead>
        <tr><th>Metric</th><th>Yours</th><th>Industry</th><th>Status</th></tr>
      </thead>
      <tbody>
        {#each benchmarksParsed.items as b}
          {@const color = statusColor(b.status)}
          <tr>
            <td>{b.metric || ''}</td>
            <td><strong>{b.yours ?? b.value ?? ''}</strong></td>
            <td class="muted">{b.industry ?? b.benchmark ?? ''}</td>
            <td><span class="badge-status badge-{color}">{b.status || ''}</span></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  {#if showForecasts}
    <hr />
    {#each forecastsParsed.items as f}
      <div class="forecast-line">
        <strong>Forecast:</strong>
        {f.value || f.point || ''}{#if f.date} by {f.date}{/if}
        {#if f.range}(±{f.range}){/if}.
        {#if f.method}<span class="muted">Method: {f.method}.</span>{/if}
      </div>
    {/each}
  {/if}

  {#if showMeans}
    <hr />
    <div class="block-label">What it means</div>
    <ul class="means-list">
      {#each meansItems as m}
        <li>{@html formatInline(m)}</li>
      {/each}
    </ul>
  {/if}

  {#if showRecs}
    <hr />
    <div class="block-label">Read next</div>
    <ol class="recs-list">
      {#each recsList as rec, i}
        {@const pr = (rec.priority || 'P2').toString().toUpperCase()}
        <li class="rec-row">
          <span class="priority-badge priority-{pr.toLowerCase()}">{pr}</span>
          <div class="rec-body">
            <div class="rec-action">{@html formatInline(rec.action || rec.text || '')}</div>
          </div>
          <button class="btn-cta" onclick={() => onAction('followup', { question: rec.action || rec.text || '', rec })}>
            Ask →
          </button>
        </li>
      {/each}
    </ol>
  {/if}

  {#if showRelated}
    <hr />
    <div class="block-label">Related questions</div>
    <div class="chips">
      {#each relatedItems as q}
        <button class="chip" onclick={() => onAction('related', q)}>{q}</button>
      {/each}
    </div>
  {/if}

  {#if showAudit}
    <hr />
    <div class="audit-footer">
      <span class="audit-src">🔗 source</span>
      {#each auditParsed.items as a, i}
        <span>{a}</span>{#if i < auditParsed.items.length - 1}<span class="sep"> · </span>{/if}
      {/each}
    </div>
  {/if}

  {/if}
</article>

<style>
  .answer-card {
    position: relative;
    background: var(--pw-bg, #fdf8f1);
    color: var(--pw-ink, #1f1a14);
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 4px;
    padding: 20px 22px 16px;
    margin: 12px 0;
    font-family: inherit;
  }

  /* ── Universal "chemist-grade" band ───────────────────────────────── */
  .chem-band {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 0 0 10px;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  }
  .chem-band-title {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .band-hex {
    color: #9a4a2f;
    font-size: 15px;
    flex-shrink: 0;
  }
  .band-title-text {
    font-size: 16px;
    font-weight: 700;
    color: var(--pw-ink, #1f1a14);
    line-height: 1.25;
    overflow-wrap: anywhere;
  }
  .band-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .conf-dot {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .conf-high { color: #2e7d32; background: rgba(46, 125, 50, 0.10); }
  .conf-medium { color: #b9770e; background: rgba(185, 119, 14, 0.10); }
  .conf-low { color: #8a8a8a; background: rgba(138, 138, 138, 0.12); }
  .src-chip {
    font-size: 11px;
    font-weight: 600;
    color: #9a4a2f;
    background: #f3ece1;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .band-lead {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.5;
    margin: 0 0 12px;
    color: var(--pw-ink, #1f1a14);
  }
  .lead-arrow { color: #9a4a2f; flex-shrink: 0; }

  /* Why bullets + So-what + evidence (analytical body) */
  .why-block { margin: 12px 0 4px; }
  .why-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #8a7a66;
    margin-bottom: 4px;
  }
  .why-list { margin: 0; padding-left: 18px; }
  .why-list li { font-size: 13.5px; line-height: 1.55; margin: 2px 0; }
  .sowhat-line {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: baseline;
    margin: 10px 0 4px;
    padding: 8px 12px;
    background: rgba(154, 74, 47, 0.05);
    border-left: 2px solid #9a4a2f;
    border-radius: 3px;
    font-size: 13.5px;
  }
  .sowhat-arrow { font-weight: 700; color: #9a4a2f; white-space: nowrap; }
  .sowhat-action { font-weight: 600; }
  .sowhat-meta { color: #8a7a66; font-size: 12.5px; }
  .evidence-row {
    margin: 10px 0 2px;
    font-size: 12px;
    color: #8a7a66;
    font-variant-numeric: tabular-nums;
  }

  .tier-badge {
    position: absolute;
    top: 10px;
    right: 12px;
    font-size: 10px;
    letter-spacing: 0.12em;
    color: var(--pw-ink-muted, #7a6f60);
    background: transparent;
    border: 1px solid rgba(0, 0, 0, 0.1);
    border-radius: 999px;
    padding: 2px 8px;
    text-transform: uppercase;
  }

  .skill-chip {
    position: absolute;
    top: 10px;
    right: 78px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    font-weight: 600;
    color: #ffffff;
    background: var(--pw-brand-teal, #0e7c86);
    border-radius: 999px;
    padding: 2px 9px;
    cursor: default;
    line-height: 1.2;
    white-space: nowrap;
    max-width: 200px;
  }
  .skill-chip-icon {
    flex-shrink: 0;
  }
  .skill-chip-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 160px;
  }

  hr {
    border: none;
    border-top: 1px solid #f1e6d2;
    margin: 14px 0;
  }

  .action-title {
    font-family: var(--pw-font-body);
    font-size: 24px;
    line-height: 1.2;
    font-weight: 600;
    margin: 0;
    color: var(--pw-ink, #1f1a14);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .title-icon { font-size: 22px; }

  .narration {
    font-style: italic;
    color: var(--pw-ink-muted, #7a6f60);
    margin: 8px 0 0;
    font-size: 14px;
  }

  .fallback-note {
    font-style: italic;
    color: var(--pw-ink-muted, #7a6f60);
    margin: 4px 0;
    font-size: 13.5px;
  }

  .summary-card {
    background: var(--pw-bg-alt, #f6ecda);
    border-radius: 4px;
    padding: 14px 18px;
    font-size: 14px;
    line-height: 1.6;
    width: 100%;
  }
  .summary-card :global(h1) { font-size: 20px; font-weight: 700; margin: 20px 0 10px; font-family: 'Source Serif Pro', Georgia, serif; color: var(--pw-ink, #1a1614); }
  .summary-card :global(h2) { font-size: 17px; font-weight: 700; margin: 18px 0 8px; font-family: 'Source Serif Pro', Georgia, serif; color: var(--pw-ink, #1a1614); }
  .summary-card :global(h3) { font-size: 15px; font-weight: 600; margin: 16px 0 6px; color: var(--pw-ink, #1a1614); text-transform: none; }
  .summary-card :global(h3.num-section) { font-size: 14px; font-weight: 700; color: var(--pw-accent, #c96342); margin: 18px 0 6px; border-bottom: 1px solid rgba(201,99,66,0.2); padding-bottom: 4px; }
  .summary-card :global(h4) { font-size: 13.5px; font-weight: 700; margin: 14px 0 4px; color: var(--pw-ink, #1a1614); }
  .summary-card :global(h4.step-section) { font-size: 13.5px; font-weight: 700; color: var(--pw-accent, #c96342); margin: 14px 0 6px; padding-left: 8px; border-left: 3px solid var(--pw-accent, #c96342); }
  .summary-card :global(hr) { border: none; border-top: 1px dashed rgba(122,111,96,0.3); margin: 14px 0; }
  .summary-card :global(p) { margin: 6px 0; }
  .summary-card :global(ul), .summary-card :global(ol) { margin: 8px 0; padding-left: 22px; }
  .summary-card :global(li) { margin: 4px 0; }
  .summary-card :global(code) { background: rgba(0,0,0,0.05); padding: 1px 5px; border-radius: 3px; font-size: 12.5px; font-family: 'JetBrains Mono', monospace; }
  .summary-card :global(strong) { color: var(--pw-ink, #1a1614); font-weight: 700; }
  .summary-card :global(table) { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }
  .summary-card :global(th), .summary-card :global(td) { border: 1px solid rgba(122,111,96,0.2); padding: 6px 10px; text-align: left; }
  .summary-card :global(th) { background: rgba(0,0,0,0.04); font-weight: 700; }
  .summary-card :global(details.code-collapse) { margin: 10px 0; border: 1px solid rgba(122,111,96,0.25); border-radius: 4px; background: var(--pw-bg, #fdfaf5); }
  .summary-card :global(details.code-collapse summary) { padding: 8px 12px; cursor: pointer; display: flex; gap: 10px; align-items: center; font-size: 11.5px; user-select: none; }
  .summary-card :global(details.code-collapse .code-collapse-lang) { font-weight: 700; color: var(--pw-accent, #c96342); }
  .summary-card :global(details.code-collapse .code-collapse-info) { color: var(--pw-ink-muted, #7a6f60); }
  .summary-card :global(details.code-collapse .code-collapse-copy) { margin-left: auto; background: transparent; border: 1px solid rgba(122,111,96,0.3); border-radius: 3px; padding: 2px 8px; font-size: 10px; cursor: pointer; }
  .summary-card :global(details.code-collapse pre.code-collapse-pre) { margin: 0; padding: 12px 14px; background: #1a1614; color: #e8e3d6; border-radius: 0 0 4px 4px; overflow-x: auto; font-size: 12.5px; line-height: 1.5; }
  .summary-card :global(details.code-collapse[open] summary .code-collapse-arrow) { transform: rotate(90deg); display: inline-block; }

  .block-label {
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--pw-ink-muted, #7a6f60);
    margin-bottom: 8px;
  }

  /* KPI */
  .kpi-strip {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
  }
  .kpi-tile {
    background: var(--pw-bg-alt, #f6ecda);
    border-left: 3px solid #bbb;
    border-radius: 4px;
    padding: 10px 12px;
  }
  .kpi-green { border-left-color: #16a34a; }
  .kpi-amber { border-left-color: #a06000; }
  .kpi-red { border-left-color: #c0392b; }
  .kpi-neutral { border-left-color: #b8a98c; }
  .kpi-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--pw-ink-muted, #7a6f60);
  }
  .kpi-value {
    font-family: var(--pw-font-body);
    font-size: 32px;
    line-height: 1.1;
    margin-top: 2px;
  }
  .kpi-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 4px;
  }
  .kpi-delta {
    font-size: 12px;
    padding: 1px 8px;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.04);
  }
  .kpi-delta-green { color: #16a34a; }
  .kpi-delta-amber { color: #a06000; }
  .kpi-delta-red { color: #c0392b; }
  .kpi-emoji { font-size: 12px; }

  /* Trend */
  .trend-wrap { width: 100%; }
  .trend-chart { width: 100%; height: 180px; }

  /* TopN */
  .topn-row {
    display: grid;
    grid-template-columns: 140px 1fr 80px;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
    font-size: 13px;
  }
  .topn-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .topn-bar-wrap {
    background: var(--pw-bg-alt, #f6ecda);
    height: 10px;
    border-radius: 2px;
    overflow: hidden;
  }
  .topn-bar {
    background: var(--pw-accent, #d97757);
    height: 100%;
  }
  .topn-val { text-align: right; font-variant-numeric: tabular-nums; }

  /* Attention */
  .attention-list { display: flex; flex-direction: column; gap: 8px; }
  .attention-card {
    border: 1px solid rgba(192, 57, 43, 0.25);
    border-left: 3px solid #c0392b;
    border-radius: 4px;
    padding: 10px 12px;
    background: #fff;
  }
  .attention-head { display: flex; gap: 8px; align-items: baseline; }
  .attention-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 6px 12px;
    margin-top: 6px;
    font-size: 13px;
  }
  .btn-action {
    margin-top: 8px;
    background: #c0392b;
    color: #fff;
    border: none;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  /* Segments */
  .segments { display: flex; flex-direction: column; gap: 6px; }
  .seg-row {
    display: grid;
    grid-template-columns: 140px 1fr 70px;
    gap: 10px;
    align-items: center;
    font-size: 13px;
  }
  .seg-bar-wrap {
    background: var(--pw-bg-alt, #f6ecda);
    height: 12px;
    border-radius: 2px;
    overflow: hidden;
  }
  .seg-bar { height: 100%; }
  .seg-bar-green { background: #16a34a; }
  .seg-bar-amber { background: #a06000; }
  .seg-bar-red { background: #c0392b; }
  .seg-bar-neutral { background: var(--pw-accent, #d97757); }
  .seg-delta { text-align: right; font-variant-numeric: tabular-nums; font-size: 12px; }
  .seg-delta-green { color: #16a34a; }
  .seg-delta-amber { color: #a06000; }
  .seg-delta-red { color: #c0392b; }

  /* Root cause */
  .root-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
  .root-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .root-num {
    width: 22px; height: 22px; border-radius: 50%;
    background: var(--pw-accent, #d97757); color: #fff;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 11px; flex-shrink: 0;
  }
  .root-text { flex: 1; }
  .root-pct { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--pw-ink-muted); }
  .root-bar-wrap {
    background: var(--pw-bg-alt, #f6ecda);
    height: 6px; border-radius: 2px; margin-left: 30px; overflow: hidden;
  }
  .root-bar { background: var(--pw-accent, #d97757); height: 100%; }

  /* Scenarios */
  .scenarios { display: flex; flex-direction: column; gap: 8px; }
  .scenario {
    border-left: 3px solid var(--pw-accent, #d97757);
    background: var(--pw-bg-alt, #f6ecda);
    padding: 8px 12px;
    margin: 0;
    border-radius: 0 4px 4px 0;
    font-size: 13px;
  }
  .scenario-name { font-weight: 600; margin-bottom: 4px; }

  /* Benchmark */
  .benchmark-tbl {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .benchmark-tbl th, .benchmark-tbl td {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid #f1e6d2;
  }
  .benchmark-tbl th {
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--pw-ink-muted);
    font-weight: 500;
  }
  .badge-status {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
    background: rgba(0,0,0,0.04);
  }
  .badge-green { color: #16a34a; }
  .badge-amber { color: #a06000; }
  .badge-red { color: #c0392b; }

  /* Forecast */
  .forecast-line {
    background: var(--pw-bg-alt, #f6ecda);
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 13px;
  }

  /* Means */
  .means-list { padding-left: 18px; margin: 0; font-size: 13px; line-height: 1.6; }
  .means-list li { margin-bottom: 2px; }

  /* Recs */
  .recs-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
  .rec-row {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 10px;
    align-items: center;
    background: #fff;
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 4px;
    padding: 8px 10px;
  }
  .priority-badge {
    font-size: 10px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
    letter-spacing: 0.08em;
  }
  .priority-p0, .priority-p1 { background: #c0392b; color: #fff; }
  .priority-p2 { background: #a06000; color: #fff; }
  .priority-p3, .priority-p4 { background: var(--pw-bg-alt, #f6ecda); color: var(--pw-ink); }
  .rec-action { font-size: 13px; }
  .rec-meta { font-size: 11px; margin-top: 2px; }
  .btn-cta {
    background: var(--pw-accent, #d97757);
    color: #fff;
    border: none;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }

  /* Chips */
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    background: var(--pw-bg-alt, #f6ecda);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 12px;
    cursor: pointer;
    color: var(--pw-ink);
  }
  .chip:hover { background: var(--pw-accent, #d97757); color: #fff; }

  /* Audit */
  .audit-footer {
    font-size: 10px;
    color: var(--pw-ink-muted, #7a6f60);
    letter-spacing: 0.04em;
    font-family: ui-monospace, monospace;
  }
  .audit-footer .sep { opacity: 0.5; }

  /* Action bar */
  .action-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .ab-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 4px;
    padding: 5px 10px;
    font-size: 12px;
    color: var(--pw-ink);
    cursor: pointer;
  }
  .ab-btn:hover { background: var(--pw-bg-alt, #f6ecda); }

  .muted { color: var(--pw-ink-muted, #7a6f60); }

  /* ─── Clinical monograph ─── */
  .mono-head { display: flex; align-items: flex-start; gap: 10px; padding-right: 90px; }
  .mono-icon { font-size: 22px; line-height: 1.1; flex-shrink: 0; }
  .mono-title-wrap { flex: 1; min-width: 0; }
  .mono-salt {
    font-family: var(--pw-font-body);
    font-size: 22px; line-height: 1.15; font-weight: 700; margin: 0;
    text-transform: uppercase; letter-spacing: 0.01em;
    color: var(--pw-ink, #1f1a14);
  }
  .mono-sub { font-size: 12px; color: var(--pw-ink-muted, #7a6f60); margin-top: 2px; }
  .mono-art { font-family: ui-monospace, monospace; font-size: 11px; }
  .mono-status {
    flex-shrink: 0; align-self: flex-start;
    font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--pw-accent, #c96342); background: rgba(201,99,66,0.10);
    border-radius: 999px; padding: 3px 10px; white-space: nowrap;
  }
  .mono-fields { margin-top: 14px; display: flex; flex-direction: column; }
  .mono-row { display: grid; grid-template-columns: 120px 1fr; gap: 12px; padding: 7px 0; border-bottom: 1px solid #f1e6d2; font-size: 13.5px; }
  .mono-row:last-child { border-bottom: none; }
  .mono-k { font-size: 10px; font-weight: 700; letter-spacing: 0.1em; color: var(--pw-ink-muted, #7a6f60); padding-top: 2px; }
  .mono-v { color: var(--pw-ink, #1f1a14); }

  /* safety strip — red, surfaced near top */
  .mono-safety { margin-top: 12px; background: rgba(192,57,43,0.07); border: 1px solid rgba(192,57,43,0.25); border-left: 3px solid #c0392b; border-radius: 4px; padding: 8px 12px; display: flex; flex-direction: column; gap: 5px; }
  .mono-safety-row { display: flex; gap: 10px; align-items: baseline; font-size: 13px; }
  .mono-safety-tag { flex-shrink: 0; font-size: 10px; font-weight: 700; letter-spacing: 0.05em; color: #c0392b; white-space: nowrap; }
  .mono-safety-text { color: var(--pw-ink, #1f1a14); }

  /* dispensing ledger */
  .mono-strip { margin-top: 12px; background: var(--pw-bg-alt, #f6ecda); border-radius: 4px; padding: 10px 14px; }
  .mono-strip-label { font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--pw-ink-muted, #7a6f60); }
  .mono-strip-vals { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-top: 4px; flex-wrap: wrap; }
  .mono-stock { font-size: 16px; font-weight: 600; color: var(--pw-ink, #1f1a14); }
  .mono-cost { font-size: 12px; color: var(--pw-ink-muted, #7a6f60); font-variant-numeric: tabular-nums; }

  /* therapeutic equivalents */
  .mono-equiv { display: flex; flex-direction: column; }
  .mono-equiv-row { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid #f1e6d2; font-size: 13px; }
  .mono-equiv-row:last-child { border-bottom: none; }
  .mono-equiv-name { font-weight: 600; }
  .mono-equiv-meta { color: var(--pw-ink-muted, #7a6f60); font-variant-numeric: tabular-nums; text-align: right; }

  /* evidence footer */
  .mono-evidence { margin-top: 14px; font-size: 11px; font-family: ui-monospace, monospace; color: var(--pw-ink-muted, #7a6f60); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
  .mono-verified { margin-left: auto; color: #16a34a; font-weight: 700; }

  .audit-src { font-weight: 700; margin-right: 4px; }

  @media (max-width: 900px) {
    .mono-row { grid-template-columns: 90px 1fr; }
    .mono-head { padding-right: 0; flex-wrap: wrap; }
    .kpi-strip { grid-template-columns: 1fr; }
    .topn-row { grid-template-columns: 100px 1fr 60px; font-size: 12px; }
    .seg-row { grid-template-columns: 100px 1fr 50px; }
    .attention-grid { grid-template-columns: 1fr; }
    .action-bar { flex-wrap: wrap; }
    .rec-row { grid-template-columns: auto 1fr; }
    .rec-row .btn-cta { grid-column: 1 / -1; }
  }
</style>
