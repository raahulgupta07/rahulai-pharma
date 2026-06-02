<script lang="ts">
  // Animated "Agent flow" diagram for the Cockpit.
  // Documents → Ingest Crew → 5 stores (fill ∝ size) → Chat Lanes → Answer.
  let {
    processing = false,
    docs = 0,
    chunks = 0,
    graph = 0,
    edges = 0,
    wiki = 0,
    memory = 0,
    datasets = 0,
  }: {
    processing?: boolean;
    docs?: number; chunks?: number; graph?: number; edges?: number;
    wiki?: number; memory?: number; datasets?: number;
  } = $props();

  const stores = $derived([
    { label: 'CHUNKS',   n: chunks,   sub: 'vectors' },
    { label: 'GRAPH',    n: graph,    sub: edges ? `${edges} edges` : 'triples' },
    { label: 'WIKI',     n: wiki,     sub: 'notes' },
    { label: 'MEMORY',   n: memory,   sub: 'facts' },
    { label: 'DATASETS', n: datasets, sub: datasets === 1 ? 'table' : 'tables' },
  ]);
  const crew = ['enrich', 'graph', 'skill', 'memory', 'config', 'curate'];
</script>

<div class="aflow" class:running={processing}>
  <div class="aflow-head">
    <span class="aflow-title">Agent flow</span>
    <span class="aflow-status"><span class="dot"></span>{processing ? 'processing' : 'live'}</span>
  </div>

  <div class="aflow-card">
    <!-- Row 1: Documents → Ingest Crew -->
    <div class="row1">
      <div class="box box-doc">
        <div class="box-cap">DOCUMENTS</div>
        <div class="box-num">{docs}</div>
        <div class="box-sub">docs · 0 queued</div>
      </div>
      <div class="conn"><div class="dash-h"></div><span class="pkt pkt-x"></span></div>
      <div class="box box-crew">
        <div class="box-cap">INGEST CREW</div>
        <div class="crew-grid">
          {#each crew as c}
            <span class="crew-item"><span class="crew-dot"></span>{c}</span>
          {/each}
        </div>
      </div>
    </div>

    <!-- vertical drop from crew + horizontal bus -->
    <div class="drop-center"><div class="conn-v"><div class="dash-v"></div><span class="pkt pkt-y"></span></div></div>
    <div class="bus"><div class="conn"><div class="dash-h wide"></div><span class="pkt pkt-x slow"></span></div></div>

    <!-- Row 2: 5 stores w/ fan-down stubs -->
    <div class="stub-row">
      {#each stores as _, si}<div class="conn-v stub"><div class="dash-v"></div><span class="pkt pkt-y" style="animation-delay:{si * 0.3}s"></span></div>{/each}
    </div>
    <div class="stores">
      {#each stores as s}
        <div class="box store">
          <div class="box-cap">{s.label}</div>
          <div class="box-num big">{s.n.toLocaleString()}</div>
          <div class="box-sub">{s.sub}</div>
        </div>
      {/each}
    </div>

    <!-- converge to chat lanes -->
    <div class="stub-row up">
      {#each stores as _, si}<div class="conn-v stub up"><div class="dash-v rev"></div><span class="pkt pkt-yu" style="animation-delay:{si * 0.3}s"></span></div>{/each}
    </div>
    <div class="lanes-row">
      <div class="box box-lanes">
        <div class="box-cap">CHAT LANES</div>
        <div class="lanes">data · graph · wiki · memory · compute → answer</div>
      </div>
      <div class="conn answer-conn"><div class="dash-h"></div><span class="pkt pkt-x"></span></div>
      <div class="answer">→ ANSWER</div>
    </div>
  </div>

  <div class="legend">
    <span><span class="lg dot-run"></span> stage running</span>
    <span><span class="lg dot-idle"></span> idle</span>
  </div>
</div>

<style>
  .aflow { font-family: var(--pw-font-body); margin: 8px 0 16px; }
  .aflow-head { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
  .aflow-title { font-size:14px; font-weight:700; color:var(--pw-ink); }
  .aflow-status { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:var(--pw-muted); display:inline-flex; align-items:center; gap:6px; }
  .aflow.running .aflow-status { color:var(--pw-accent); }
  .aflow-status .dot { width:8px; height:8px; border-radius:50%; background:var(--pw-accent); animation:pulse 2.4s ease-in-out infinite; }
  .aflow.running .aflow-status .dot { animation-duration:1.2s; }

  .aflow-card { border:1px solid var(--pw-border); background:var(--pw-surface); padding:22px 18px; }

  .box { border:1px solid var(--pw-border); background:var(--pw-bg-alt); padding:12px 14px; }
  .box-cap { font-size:10px; font-weight:800; letter-spacing:0.08em; color:var(--pw-muted); text-transform:uppercase; }
  .box-num { font-size:22px; font-weight:800; color:var(--pw-ink); font-family:var(--pw-font-body); margin-top:2px; }
  .box-num.big { font-size:28px; }
  .box-sub { font-size:10.5px; color:var(--pw-muted); margin-top:2px; }

  .row1 { display:grid; grid-template-columns: minmax(150px,1fr) 60px minmax(220px,1.4fr); align-items:center; }
  .crew-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:4px 10px; margin-top:6px; }
  .crew-item { font-size:11px; color:var(--pw-ink); display:inline-flex; align-items:center; gap:5px; }
  .crew-dot { width:7px; height:7px; border-radius:50%; background:var(--pw-accent); animation:pulse 2.4s ease-in-out infinite; }
  .crew-item:nth-child(2) .crew-dot { animation-delay:0.4s; }
  .crew-item:nth-child(3) .crew-dot { animation-delay:0.8s; }
  .crew-item:nth-child(4) .crew-dot { animation-delay:1.2s; }
  .crew-item:nth-child(5) .crew-dot { animation-delay:1.6s; }
  .crew-item:nth-child(6) .crew-dot { animation-delay:2s; }
  .aflow.running .crew-dot { animation-duration:1s; }

  .drop-center { display:flex; justify-content:center; height:22px; }
  .bus { display:flex; justify-content:center; height:2px; margin:0 8%; }
  .stub-row { display:grid; grid-template-columns:repeat(5,1fr); height:22px; }
  .stub-row.up { margin-top:0; }
  .stub-row .stub { justify-self:center; }

  /* connector wrappers hold a dashed line + a traveling packet */
  .conn { position:relative; width:100%; display:flex; align-items:center; }
  .conn-v { position:relative; height:100%; display:flex; justify-content:center; }
  .conn-v.stub { width:2px; }
  .pkt { position:absolute; width:7px; height:7px; border-radius:50%;
    background:var(--pw-accent); box-shadow:0 0 6px var(--pw-accent);
    opacity:0.9; pointer-events:none; }
  /* horizontal travel: left edge → right edge */
  .pkt-x { top:50%; left:0; margin-top:-3.5px; animation:travelx 1.8s linear infinite; }
  .pkt-x.slow { animation-duration:2.6s; }
  /* vertical travel down: top → bottom */
  .pkt-y { left:50%; top:0; margin-left:-3.5px; animation:travely 1.4s linear infinite; }
  /* vertical travel up: bottom → top */
  .pkt-yu { left:50%; bottom:0; margin-left:-3.5px; animation:travelyu 1.4s linear infinite; }
  .aflow.running .pkt-x { animation-duration:0.9s; }
  .aflow.running .pkt-x.slow { animation-duration:1.3s; }
  .aflow.running .pkt-y, .aflow.running .pkt-yu { animation-duration:0.7s; }
  @keyframes travelx { 0%{left:-4px; opacity:0;} 12%{opacity:0.95;} 88%{opacity:0.95;} 100%{left:calc(100% + 4px); opacity:0;} }
  @keyframes travely { 0%{top:-4px; opacity:0;} 15%{opacity:0.95;} 85%{opacity:0.95;} 100%{top:calc(100% + 4px); opacity:0;} }
  @keyframes travelyu { 0%{bottom:-4px; opacity:0;} 15%{opacity:0.95;} 85%{opacity:0.95;} 100%{bottom:calc(100% + 4px); opacity:0;} }
  .answer-conn { width:42px; flex:0 0 42px; }

  .stores { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }
  .store { text-align:center; }

  .lanes-row { display:flex; align-items:center; gap:14px; justify-content:center; margin-top:0; }
  .box-lanes { flex:1 1 auto; max-width:620px; }
  .lanes { font-size:12px; color:var(--pw-muted); margin-top:4px; }
  .answer { font-size:13px; font-weight:800; color:var(--pw-accent); white-space:nowrap; }

  /* animated dashed connectors */
  .dash-h { height:2px; width:100%;
    background-image:repeating-linear-gradient(90deg, var(--pw-accent) 0 7px, transparent 7px 14px);
    background-size:14px 2px; }
  .dash-h.wide { width:100%; }
  .dash-v { width:2px; height:100%;
    background-image:repeating-linear-gradient(180deg, var(--pw-accent) 0 7px, transparent 7px 14px);
    background-size:2px 14px; }
  /* dashes ALWAYS drift (alive); faster when processing */
  .dash-h { animation:flowx 1.6s linear infinite; }
  .dash-v { animation:flowy 1.6s linear infinite; }
  .dash-v.rev { animation:flowyr 1.6s linear infinite; }
  .aflow.running .dash-h { animation-duration:0.6s; }
  .aflow.running .dash-v, .aflow.running .dash-v.rev { animation-duration:0.6s; }
  @keyframes flowx { to { background-position:14px 0; } }
  @keyframes flowy { to { background-position:0 14px; } }
  @keyframes flowyr { to { background-position:0 -14px; } }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.35;} }

  .legend { display:flex; gap:18px; margin-top:10px; font-size:11px; color:var(--pw-muted); flex-wrap:wrap; }
  .legend span { display:inline-flex; align-items:center; gap:6px; }
  .lg { width:10px; height:10px; border-radius:50%; }
  .dot-run { background:var(--pw-accent); }
  .dot-idle { background:var(--pw-muted); }

  @media (max-width: 820px) {
    .stores, .stub-row { grid-template-columns:repeat(2,1fr); }
    .row1 { grid-template-columns:1fr; }
  }
</style>
