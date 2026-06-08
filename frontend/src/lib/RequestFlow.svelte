<script lang="ts">
  // Reusable "cockpit lane" request-flow diagram — matches AgentFlow.svelte's
  // visual language (color bars, animated dashed connectors, traveling
  // particle, live dot, legend). Shows a worked example end-to-end: a USER
  // question bubble on the left → internal lanes → masked ANSWER bubble on the
  // right, so a developer sees the whole input→output path of a real chat.
  type Stage = {
    label: string;                 // lane header (GATE / AGENT / MASK …)
    title: string;                 // card title
    lines?: string[];              // mono detail lines (sub-steps)
    sub?: string;                  // muted footnote
    color?: 'cyan' | 'red' | 'amber' | 'coral';
  };
  type Legend = { label: string; color: 'cyan' | 'red' | 'amber' | 'coral' };
  type Bubble = { who: string; text: string };

  let {
    title = 'Request Flow',
    live = true,
    badge = '',
    inputBubble = null as Bubble | null,
    outputBubble = null as Bubble | null,
    stages = [] as Stage[],
    legend = [] as Legend[],
  } = $props();

  const firstColor = $derived(stages[0]?.color || 'amber');
  const lastColor = $derived(stages[stages.length - 1]?.color || 'coral');
</script>

<div class="rf-wrap" class:rf-running={live}>
  <div class="rf-head">
    <span class="rf-title">{title}</span>
    <span class="rf-live"><span class="rf-dot"></span>{live ? 'LIVE' : 'IDLE'}</span>
    {#if badge}<span class="rf-badge">{badge}</span>{/if}
  </div>

  <div class="rf-lanes">
    {#if inputBubble}
      <div class="rf-stage rf-bubblecol">
        <div class="rf-lane">Input</div>
        <div class="rf-bubble rf-bubble-in">
          <div class="rf-bw">💬 {inputBubble.who}</div>
          <div class="rf-bt">“{inputBubble.text}”</div>
        </div>
      </div>
      <div class="rf-conn rf-conn-{firstColor}"><span class="rf-ptcl"></span></div>
    {/if}

    {#each stages as s, i}
      <div class="rf-stage rf-{s.color || 'coral'}">
        <div class="rf-lane">{s.label}</div>
        <div class="rf-card">
          <div class="rf-card-title">{s.title}</div>
          {#each (s.lines || []) as ln}<div class="rf-line">{ln}</div>{/each}
          {#if s.sub}<div class="rf-sub">{s.sub}</div>{/if}
        </div>
      </div>
      {#if i < stages.length - 1}
        <div class="rf-conn rf-conn-{s.color || 'coral'}"><span class="rf-ptcl"></span></div>
      {/if}
    {/each}

    {#if outputBubble}
      <div class="rf-conn rf-conn-{lastColor}"><span class="rf-ptcl"></span></div>
      <div class="rf-stage rf-bubblecol">
        <div class="rf-lane">Output</div>
        <div class="rf-bubble rf-bubble-out">
          <div class="rf-bw">💬 {outputBubble.who}</div>
          <div class="rf-bt">“{outputBubble.text}”</div>
        </div>
      </div>
    {/if}
  </div>

  {#if legend.length}
    <div class="rf-legend">
      {#each legend as l}<span><i class="rf-lg rf-lg-{l.color}"></i>{l.label}</span>{/each}
    </div>
  {/if}
</div>

<style>
  .rf-wrap { font-family: var(--pw-font-body); }
  .rf-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  .rf-title { font-size: 13px; font-weight: 700; color: var(--pw-ink, #2c2a26); }
  .rf-live {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--pw-muted, #8a8276); display: inline-flex; align-items: center; gap: 6px;
  }
  .rf-running .rf-live { color: var(--pw-accent, #c96342); }
  .rf-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pw-accent, #c96342); animation: rf-pulse 2.4s ease-in-out infinite; flex-shrink: 0; }
  .rf-badge { font-size: 11px; font-weight: 600; color: #0ca6b0; }

  .rf-lanes { display: flex; align-items: stretch; gap: 0; overflow-x: auto; padding-bottom: 4px; }
  .rf-stage { flex: 1 1 0; min-width: 150px; display: flex; flex-direction: column; gap: 6px; }
  .rf-lane { font-size: 9px; font-weight: 700; letter-spacing: 0.08em; color: #8a8070; text-transform: uppercase; padding-left: 2px; }
  .rf-card {
    background: #fff; border: 1px solid var(--pw-border, #e5ddcf); border-top-width: 3px;
    padding: 12px 13px; min-height: 104px; display: flex; flex-direction: column; gap: 5px;
  }
  .rf-card-title { font-size: 12px; font-weight: 800; letter-spacing: 0.02em; }
  .rf-line { font-size: 11px; color: #4a4438; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.4; }
  .rf-sub { font-size: 10px; color: #b0a898; margin-top: auto; }

  /* per-stage accent (top bar + title color) */
  .rf-cyan  .rf-card { border-top-color: #0ecad4; }   .rf-cyan  .rf-card-title { color: #0ca6b0; }
  .rf-red   .rf-card { border-top-color: #e05a4a; }   .rf-red   .rf-card-title { color: #c0392b; }
  .rf-amber .rf-card { border-top-color: #d4930e; }   .rf-amber .rf-card-title { color: #a8740b; }
  .rf-coral .rf-card { border-top-color: #c96342; }   .rf-coral .rf-card-title { color: #c96342; }

  /* chat bubbles (input question / output answer) */
  .rf-bubble {
    min-height: 104px; padding: 11px 13px; display: flex; flex-direction: column; gap: 7px;
    border: 1px solid var(--pw-border, #e5ddcf); border-radius: 12px;
  }
  .rf-bubble-in  { background: #f5efe6; border-bottom-left-radius: 3px; }
  .rf-bubble-out { background: #fbeee7; border: 1px solid #f0d9cc; border-bottom-right-radius: 3px; }
  .rf-bw { font-size: 9px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: #8a8070; }
  .rf-bt { font-size: 12.5px; color: #3a3530; line-height: 1.45; font-style: italic; }
  .rf-bubble-out .rf-bt { color: #9a4a2f; }

  /* animated dashed connector + traveling particle + arrowhead */
  .rf-conn { position: relative; flex: 0 0 38px; align-self: center; height: 26px; margin-top: 16px; }
  .rf-conn::before {
    content: ''; position: absolute; top: 50%; left: 2px; right: 10px; height: 2px; transform: translateY(-50%);
    background-image: repeating-linear-gradient(90deg, var(--rfc) 0 7px, transparent 7px 12px);
    animation: rf-dash 1.8s linear infinite;
  }
  .rf-conn::after {
    content: ''; position: absolute; top: 50%; right: 2px; transform: translateY(-50%);
    width: 0; height: 0; border-left: 7px solid var(--rfc); border-top: 4px solid transparent; border-bottom: 4px solid transparent;
  }
  .rf-ptcl {
    position: absolute; top: 50%; width: 5px; height: 5px; border-radius: 50%; margin-top: -2.5px;
    background: var(--rfc); filter: drop-shadow(0 0 3px var(--rfc)); animation: rf-trav 2s linear infinite;
  }
  .rf-conn-cyan  { --rfc: #0ecad4; }
  .rf-conn-red   { --rfc: #e05a4a; }
  .rf-conn-amber { --rfc: #d4930e; }
  .rf-conn-coral { --rfc: #c96342; }

  @keyframes rf-dash { to { background-position: 24px 0; } }
  @keyframes rf-trav { 0% { left: 0; opacity: 0; } 10% { opacity: 1; } 88% { opacity: 1; } 100% { left: calc(100% - 10px); opacity: 0; } }
  @keyframes rf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

  .rf-legend { display: flex; gap: 16px; margin-top: 12px; font-size: 11px; color: var(--pw-muted, #8a8276); flex-wrap: wrap; align-items: center; }
  .rf-legend span { display: inline-flex; align-items: center; gap: 5px; }
  .rf-lg { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
  .rf-lg-cyan  { background: #0ecad4; } .rf-lg-red { background: #e05a4a; }
  .rf-lg-amber { background: #d4930e; } .rf-lg-coral { background: #c96342; }

  @media (max-width: 680px) { .rf-stage { min-width: 150px; } }
</style>
