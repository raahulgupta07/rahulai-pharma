<script lang="ts">
  let {
    processing = false,
    docs = 0,
    chunks = 0,
    graph = 0,
    edges = 0,
    wiki = 0,
    memory = 0,
    datasets = 0,
    catalog = 0,
    stock = 0,
    sites = 0,
    substitutes = 0,
  }: {
    processing?: boolean;
    docs?: number;
    chunks?: number;
    graph?: number;
    edges?: number;
    wiki?: number;
    memory?: number;
    datasets?: number;
    catalog?: number;
    stock?: number;
    sites?: number;
    substitutes?: number;
  } = $props();

  const speed = $derived(processing ? 'fast' : 'slow');

  // Compact number: 102107 -> "102k", 4886 -> "4,886"
  function fmtNum(n: number): string {
    if (!n || n <= 0) return '—';
    if (n >= 1000) return (n / 1000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return n.toLocaleString();
  }
  function fmtFull(n: number): string {
    return n && n > 0 ? n.toLocaleString() : '—';
  }

  const hexNodes = [
    { cx: 498, cy: 82, r: 5, d: '0s' },
    { cx: 518, cy: 70, r: 4, d: '0.4s' },
    { cx: 538, cy: 82, r: 5, d: '0.8s' },
    { cx: 538, cy: 101, r: 4, d: '1.2s' },
    { cx: 518, cy: 113, r: 5, d: '0.6s' },
    { cx: 498, cy: 101, r: 4, d: '1s' },
    { cx: 518, cy: 91, r: 6, d: '0.2s' },
  ];

  const hexEdges: [number, number, number, number][] = [
    [498, 82, 518, 70],
    [518, 70, 538, 82],
    [538, 82, 538, 101],
    [538, 101, 518, 113],
    [518, 113, 498, 101],
    [498, 101, 498, 82],
    [498, 82, 518, 91],
    [518, 70, 518, 91],
    [538, 82, 518, 91],
  ];

  const memoryBarW = $derived(Math.round(49 + Math.min(memory / 50, 1) * 127));
</script>

<div class="af-wrap" class:running={processing}>
  <div class="af-head">
    <span class="af-title">Agent Process Flow</span>
    <span class="af-live"><span class="live-dot"></span>{processing ? 'PROCESSING' : 'LIVE'}</span>
    <span class="af-sites">⬡ {sites > 0 ? sites : '—'} SITES</span>
  </div>

  <div class="af-canvas">
    <svg viewBox="0 0 960 400" xmlns="http://www.w3.org/2000/svg" class="af-svg">

      <!-- COLUMN HEADER LABELS -->
      <text x="120" y="14" class="col-label" text-anchor="middle">SOURCES</text>
      <text x="390" y="14" class="col-label" text-anchor="middle">PUMP</text>
      <text x="605" y="14" class="col-label" text-anchor="middle">INTELLIGENCE</text>
      <text x="875" y="14" class="col-label" text-anchor="middle">OUTPUT</text>

      <!-- PIPES (behind cards) -->

      <!-- Catalog to Pump -->
      <polyline points="228,73 310,73 310,180 352,180" class="pipe pipe-cyan {speed}" fill="none"/>
      <circle class="ptcl pcyan {speed}" r="4"
        style="offset-path:path('M228,73 L310,73 L310,180 L352,180');animation-delay:0s"/>
      <circle class="ptcl pcyan {speed}" r="3"
        style="offset-path:path('M228,73 L310,73 L310,180 L352,180');animation-delay:0.9s"/>

      <!-- Stock to Pump -->
      <polyline points="228,193 300,193 300,210 352,210" class="pipe pipe-red {speed}" fill="none"/>
      <circle class="ptcl pred {speed}" r="4"
        style="offset-path:path('M228,193 L300,193 L300,210 L352,210');animation-delay:0.3s"/>
      <circle class="ptcl pred {speed}" r="3"
        style="offset-path:path('M228,193 L300,193 L300,210 L352,210');animation-delay:1.1s"/>

      <!-- Brain to Analyst -->
      <polyline points="228,313 430,313 430,290 450,290" class="pipe pipe-amber {speed}" fill="none"/>
      <circle class="ptcl pamber {speed}" r="4"
        style="offset-path:path('M228,313 L430,313 L430,290 L450,290');animation-delay:0.5s"/>

      <!-- Pump to KG -->
      <polyline points="428,178 450,78" class="pipe pipe-cyan {speed}" fill="none"/>
      <circle class="ptcl pcyan {speed}" r="4"
        style="offset-path:path('M428,178 L450,78');animation-delay:0.2s"/>

      <!-- KG to Analyst -->
      <polyline points="760,83 780,83 780,230 760,230" class="pipe pipe-cyan {speed}" fill="none"/>
      <circle class="ptcl pcyan {speed}" r="3"
        style="offset-path:path('M760,83 L780,83 L780,230 L760,230');animation-delay:0.4s"/>

      <!-- Analyst to Answer -->
      <polyline points="768,263 810,263 810,205" class="pipe pipe-coral {speed}" fill="none"/>
      <circle class="ptcl pcoral {speed}" r="5"
        style="offset-path:path('M768,263 L810,263 L810,205');animation-delay:0s"/>

      <!-- VALVES -->
      <polygon points="310,118 318,126 310,134 302,126" class="vlv-cy"/>
      <polygon points="300,201 308,209 300,217 292,209" class="vlv-re"/>

      <!-- CARD 1 — DRUG CATALOG -->
      <rect x="20" y="20" width="200" height="4" fill="#0ecad4"/>
      <rect x="20" y="24" width="200" height="96" fill="white" stroke="#e5ddd0" stroke-width="1" rx="3"/>
      <text x="32" y="44" class="card-label cyan-text">DRUG CATALOG</text>
      <text x="32" y="76" class="big-num cyan-text">{fmtFull(catalog)}</text>
      <text x="32" y="94" class="card-sub">articles</text>
      <rect x="32" y="104" width="176" height="5" fill="#e8e3d9" rx="2"/>
      <rect x="32" y="104" width="144" height="5" fill="#0ecad4" rx="2"/>
      <rect x="220" y="68" width="8" height="10" rx="1" fill="#ccc"/>

      <!-- CARD 2 — BALANCE STOCK -->
      <rect x="20" y="140" width="200" height="4" fill="#e05a4a"/>
      <rect x="20" y="144" width="200" height="96" fill="white" stroke="#e5ddd0" stroke-width="1" rx="3"/>
      <text x="32" y="164" class="card-label red-text">BALANCE STOCK</text>
      <text x="32" y="196" class="big-num red-text">{fmtNum(stock)}</text>
      <text x="32" y="214" class="card-sub">rows · {sites > 0 ? sites : '—'} sites</text>
      <rect x="32" y="224" width="176" height="5" fill="#e8e3d9" rx="2"/>
      <rect x="32" y="224" width="125" height="5" fill="#e05a4a" rx="2"/>
      <rect x="220" y="188" width="8" height="10" rx="1" fill="#ccc"/>

      <!-- CARD 3 — COMPANY BRAIN -->
      <rect x="20" y="260" width="200" height="4" fill="#d4930e"/>
      <rect x="20" y="264" width="200" height="96" fill="white" stroke="#e5ddd0" stroke-width="1" rx="3"/>
      <text x="32" y="284" class="card-label amber-text">COMPANY BRAIN</text>
      <text x="32" y="316" class="big-num amber-text">{memory}</text>
      <text x="32" y="334" class="card-sub">memories</text>
      <rect x="32" y="344" width="176" height="5" fill="#e8e3d9" rx="2"/>
      <rect x="32" y="344" width={memoryBarW} height="5" fill="#d4930e" rx="2"/>
      <rect x="220" y="308" width="8" height="10" rx="1" fill="#ccc"/>

      <!-- PUMP — STOCK CHECK -->
      <circle cx="390" cy="195" r="38" fill="white" stroke="#e5ddd0" stroke-width="1.5"/>
      <g class="pump-spin {speed}" style="transform-origin: 390px 195px">
        <path d="M390,195 L382,177 Q386,186 390,195 Z" fill="#0ecad4" opacity="0.75"/>
        <path d="M390,195 L398,213 Q394,204 390,195 Z" fill="#0ecad4" opacity="0.75"/>
        <path d="M390,195 L372,189 Q381,191 390,195 Z" fill="#e05a4a" opacity="0.75"/>
        <path d="M390,195 L408,201 Q399,199 390,195 Z" fill="#e05a4a" opacity="0.75"/>
      </g>
      <circle cx="390" cy="195" r="6" fill="#f0ebe0"/>
      <circle cx="390" cy="195" r="2.5" fill="#888"/>
      <text x="390" y="248" class="pump-label" text-anchor="middle">STOCK CHECK</text>
      <text x="390" y="260" class="pump-sub" text-anchor="middle">pump</text>

      <!-- KNOWLEDGE GRAPH CARD -->
      <rect x="450" y="20" width="310" height="4" fill="#0ecad4"/>
      <rect x="450" y="24" width="310" height="130" fill="white" stroke="#e5ddd0" stroke-width="1" rx="3"/>
      <text x="464" y="44" class="card-label cyan-text">KNOWLEDGE GRAPH</text>
      {#each hexNodes as n}
        <circle cx={n.cx} cy={n.cy} r={n.r} class="gnode" style="animation-delay:{n.d}"/>
      {/each}
      {#each hexEdges as [x1, y1, x2, y2]}
        <line {x1} {y1} {x2} {y2} class="ge"/>
      {/each}
      <text x="750" y="70" class="big-num-sm cyan-text" text-anchor="end">
        {graph > 0 ? graph.toLocaleString() : '—'}
      </text>
      <text x="750" y="86" class="card-sub" text-anchor="end">triples</text>
      <text x="750" y="104" class="stat-sm cyan-text" text-anchor="end">{fmtFull(substitutes)}</text>
      <text x="750" y="116" class="card-sub" text-anchor="end">drugs w/ substitutes</text>
      <circle cx="745" cy="34" r="5" class="sdot dot-cy"/>
      <rect x="760" y="78" width="8" height="10" rx="1" fill="#ccc"/>

      <!-- ANALYST AGENT CARD -->
      <rect x="450" y="170" width="310" height="4" fill="#c96342"/>
      <rect x="450" y="174" width="4" height="196" fill="#c96342"/>
      <rect x="450" y="174" width="310" height="196" fill="white" stroke="#e5ddd0" stroke-width="1" rx="3"/>
      <text x="464" y="194" class="card-label coral-text">ANALYST AGENT</text>
      <circle cx="745" cy="184" r="5" class="sdot {processing ? 'dot-co' : 'dot-idle'}"/>
      <text x="464" y="218" class="tool-line">▸ stock_check</text>
      <text x="464" y="232" class="tool-line">▸ find_substitutes</text>
      <text x="464" y="246" class="tool-line">▸ alternatives_for_indication</text>
      <text x="464" y="260" class="tool-line">▸ drug_relationships</text>
      <text x="464" y="274" class="tool-line tool-dim">▸ run_sql_query</text>
      <line x1="464" y1="284" x2="752" y2="284" stroke="#e8e3d9" stroke-width="1"/>
      <rect x="520" y="292" width="120" height="22" rx="11"
        fill={processing ? '#c96342' : '#f0ebe0'}/>
      <text x="580" y="307" class="mode-badge" text-anchor="middle"
        fill={processing ? 'white' : '#8a8070'}>
        {processing ? '● PROCESSING' : '○  IDLE'}
      </text>
      <text x="580" y="332" class="card-sub" text-anchor="middle">{memory} brain facts loaded</text>
      <rect x="760" y="258" width="8" height="10" rx="1" fill="#ccc"/>

      <!-- ANSWER CARD -->
      <rect x="810" y="150" width="130" height="4" fill="#c96342"/>
      <rect x="810" y="154" width="130" height="90" fill="white" stroke="#c96342" stroke-width="1.5" rx="3"/>
      <text x="875" y="192" class="answer-big" text-anchor="middle">ANSWER</text>
      <text x="875" y="210" class="card-sub" text-anchor="middle">CARD</text>
      <text x="875" y="228" class="answer-arrow" text-anchor="middle">→ UI</text>
      <circle cx="830" cy="164" r="4" class="sdot {processing ? 'dot-co' : 'dot-idle'}"/>

      <!-- ACTIVE SITES BARS -->
      {#each [0, 1, 2, 3, 4, 5, 6, 7] as i}
        <rect
          x={20 + i * 16}
          y="375"
          width="12"
          height="12"
          rx="1"
          fill={i < 5 ? '#0ecad4' : '#d4c8b8'}
          style="animation-delay:{i * 0.18}s"
          class={i < 5 && processing ? 'bar-active' : ''}
        />
      {/each}
      <text x="152" y="385" class="card-sub">{sites > 0 ? sites : '—'} ACTIVE SITES</text>

    </svg>
  </div>

  <div class="af-legend">
    <span>
      <svg width="22" height="8" style="display:inline">
        <line x1="0" y1="4" x2="22" y2="4" stroke="#0ecad4" stroke-width="2" stroke-dasharray="4,3"/>
      </svg>
      catalog / graph
    </span>
    <span>
      <svg width="22" height="8" style="display:inline">
        <line x1="0" y1="4" x2="22" y2="4" stroke="#e05a4a" stroke-width="2" stroke-dasharray="4,3"/>
      </svg>
      stock / inventory
    </span>
    <span>
      <svg width="22" height="8" style="display:inline">
        <line x1="0" y1="4" x2="22" y2="4" stroke="#d4930e" stroke-width="2" stroke-dasharray="4,3"/>
      </svg>
      brain / context
    </span>
    <span><span class="lg lg-run"></span> running</span>
    <span><span class="lg lg-idle"></span> idle</span>
  </div>
</div>

<style>
  .af-wrap { font-family: var(--pw-font-body); margin: 8px 0 16px; }
  .af-canvas { background: #faf7f2; border: 1px solid #e5ddd0; overflow: hidden; }
  .af-svg { width: 100%; height: auto; display: block; }

  .col-label {
    font-size: 9px;
    fill: #b0a898;
    font-weight: 700;
    letter-spacing: 0.08em;
    font-family: var(--pw-font-body);
  }
  .card-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.07em;
    font-family: var(--pw-font-body);
    fill: #8a8070;
  }
  .card-sub {
    font-size: 9px;
    fill: #9a8f80;
    font-family: var(--pw-font-body);
  }
  .big-num {
    font-size: 26px;
    font-weight: 800;
    font-family: var(--pw-font-body);
  }
  .big-num-sm {
    font-size: 24px;
    font-weight: 800;
    font-family: var(--pw-font-body);
  }
  .stat-sm {
    font-size: 14px;
    font-weight: 700;
    opacity: 0.7;
    font-family: var(--pw-font-body);
  }
  .cyan-text  { fill: #0ecad4; }
  .red-text   { fill: #e05a4a; }
  .amber-text { fill: #d4930e; }
  .coral-text { fill: #c96342; }

  .pump-label {
    font-size: 9px;
    fill: #8a8070;
    font-weight: 700;
    letter-spacing: 0.06em;
    font-family: var(--pw-font-body);
  }
  .pump-sub {
    font-size: 8.5px;
    fill: #b0a898;
    font-family: var(--pw-font-body);
  }
  .tool-line {
    font-size: 9px;
    fill: #4a4438;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .tool-dim { opacity: 0.4; }
  .mode-badge {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
    font-family: var(--pw-font-body);
  }
  .answer-big {
    font-size: 14px;
    font-weight: 800;
    fill: #c96342;
    font-family: var(--pw-font-body);
  }
  .answer-arrow {
    font-size: 9px;
    fill: #c96342;
    font-family: var(--pw-font-body);
  }

  /* Pipe animation */
  .pipe { fill: none; stroke-linecap: round; stroke-linejoin: round; }
  .pipe-cyan  { stroke: #0ecad4; stroke-dasharray: 7,5; animation: fp 1.8s linear infinite; stroke-width: 2; }
  .pipe-red   { stroke: #e05a4a; stroke-dasharray: 7,5; animation: fp 2.2s linear infinite; stroke-width: 2; }
  .pipe-amber { stroke: #d4930e; stroke-dasharray: 7,5; animation: fp 2.6s linear infinite; stroke-width: 2; }
  .pipe-coral { stroke: #c96342; stroke-dasharray: 7,5; animation: fp 1.5s linear infinite; stroke-width: 2; }
  .pipe.fast.pipe-cyan  { animation-duration: 0.7s; }
  .pipe.fast.pipe-red   { animation-duration: 0.9s; }
  .pipe.fast.pipe-amber { animation-duration: 1.0s; }
  .pipe.fast.pipe-coral { animation-duration: 0.55s; }
  @keyframes fp { to { stroke-dashoffset: -24; } }

  /* Particles */
  .ptcl { opacity: 0; animation: trav 2s linear infinite; pointer-events: none; }
  .pcyan  { fill: #0ecad4; filter: drop-shadow(0 0 3px #0ecad4); }
  .pred   { fill: #e05a4a; filter: drop-shadow(0 0 3px #e05a4a); }
  .pamber { fill: #d4930e; filter: drop-shadow(0 0 3px #d4930e); }
  .pcoral { fill: #c96342; filter: drop-shadow(0 0 4px #c96342); }
  .ptcl.fast { animation-duration: 0.8s; }
  @keyframes trav {
    0%   { opacity: 0; offset-distance: 0%; }
    10%  { opacity: 1; }
    90%  { opacity: 1; }
    100% { opacity: 0; offset-distance: 100%; }
  }

  /* Pump */
  .pump-spin { animation: spin 3s linear infinite; }
  .pump-spin.fast { animation-duration: 0.6s; }
  .pump-spin.slow { animation-duration: 5s; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Graph nodes */
  .gnode { fill: #0ecad4; animation: nb 2.4s ease-in-out infinite; }
  .ge    { stroke: #0ecad4; stroke-width: 1; opacity: 0.3; }
  @keyframes nb { 0%,100%{opacity:0.9} 50%{opacity:0.25} }

  /* Status dots */
  .sdot     { animation: pulse 2.4s ease-in-out infinite; }
  .dot-cy   { fill: #0ecad4; }
  .dot-re   { fill: #e05a4a; }
  .dot-am   { fill: #d4930e; }
  .dot-co   { fill: #c96342; }
  .dot-idle { fill: #c8c0b4; animation: none; }

  /* Valves */
  .vlv-cy { fill: #0ecad4; animation: vp 2s ease-in-out infinite; }
  .vlv-re { fill: #e05a4a; animation: vp 2.5s ease-in-out infinite; }
  @keyframes vp { 0%,100%{opacity:0.9} 50%{opacity:0.4} }

  /* Active bars */
  .bar-active { animation: sb 1.5s ease-in-out infinite; }
  @keyframes sb { 0%,100%{opacity:1} 50%{opacity:0.5} }

  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  /* Head + legend */
  .af-head {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .af-title { font-size: 13px; font-weight: 700; color: var(--pw-ink); }
  .af-live {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--pw-muted);
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .af-wrap.running .af-live { color: var(--pw-accent); }
  .af-sites { font-size: 11px; color: #0ecad4; font-weight: 600; }
  .live-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--pw-accent);
    animation: pulse 2.4s ease-in-out infinite;
    flex-shrink: 0;
  }
  .af-wrap.running .live-dot { animation-duration: 0.9s; }

  .af-legend {
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 11px;
    color: var(--pw-muted);
    flex-wrap: wrap;
    align-items: center;
  }
  .af-legend span { display: inline-flex; align-items: center; gap: 5px; }
  .lg { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
  .lg-run  { background: var(--pw-accent); animation: pulse 2s ease-in-out infinite; }
  .lg-idle { background: #c8c0b4; }

  @media (max-width: 680px) {
    .af-canvas { overflow-x: auto; }
    .af-svg    { min-width: 720px; }
  }
</style>
