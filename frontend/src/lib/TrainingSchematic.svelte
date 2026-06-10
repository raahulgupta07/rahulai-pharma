<script lang="ts">
  // Sibling to RequestFlow.svelte — same visual language (3px top-border cards,
  // animated dashed connectors, traveling particle, live dot) but for the
  // training pipeline. Uses ts-* class prefix to coexist without collision.

  type StageStatus = 'idle' | 'running' | 'done' | 'skipped' | 'error';

  type Stage = {
    idx: number;
    label: string;         // lane header label
    title: string;         // card title
    status: StageStatus;
    color: string;         // any CSS color (hex / var / named)
    count?: string;        // optional metric, e.g. "5,258 rows"
  };

  let {
    stages = [] as Stage[],
    live = true,
    title = 'Training Pipeline',
    badge = '',
    onpick = undefined as ((idx: number) => void) | undefined,
  } = $props();

  // Per-card flash state: store the idx of the currently-flashing card (or -1)
  let flashIdx = $state(-1);
  let flashTimer: ReturnType<typeof setTimeout> | null = null;

  function handlePick(idx: number) {
    onpick?.(idx);
    if (flashTimer !== null) clearTimeout(flashTimer);
    flashIdx = idx;
    flashTimer = setTimeout(() => { flashIdx = -1; }, 600);
  }

  // Connector state relative to the STAGE AFTER it (index i → connector between i and i+1)
  // A connector is 'active' if the stage it's entering is 'running'
  // 'done' if the stage it's entering is 'done'
  // 'idle' otherwise
  type ConnState = 'active' | 'done' | 'idle';
  function connState(i: number): ConnState {
    const next = stages[i + 1];
    if (!next) return 'idle';
    if (next.status === 'running') return 'active';
    if (next.status === 'done') return 'done';
    return 'idle';
  }

  const STATUS_PILL: Record<StageStatus, string> = {
    idle:    'Idle',
    running: 'Running',
    done:    'Done',
    skipped: 'Skipped',
    error:   'Error',
  };
</script>

<div class="ts-wrap" class:ts-running={live}>
  <!-- Header -->
  <div class="ts-head">
    <span class="ts-title">{title}</span>
    <span class="ts-live"><span class="ts-dot"></span>{live ? 'LIVE' : 'IDLE'}</span>
    {#if badge}<span class="ts-badge">{badge}</span>{/if}
  </div>

  <!-- Pipeline -->
  <div class="ts-lanes">
    {#each stages as s, i}
      <!-- Stage card -->
      <div
        class="ts-stage ts-s-{s.status}"
        class:ts-flash={flashIdx === s.idx}
        role="button"
        tabindex="0"
        aria-label="Stage {s.title}"
        onclick={() => handlePick(s.idx)}
        onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && handlePick(s.idx)}
        style="--ts-color: {s.color};"
      >
        <div class="ts-lane">{s.label}</div>
        <div class="ts-card">
          <!-- Diagonal hatch overlay for skipped -->
          {#if s.status === 'skipped'}<div class="ts-hatch" aria-hidden="true"></div>{/if}

          <div class="ts-card-top">
            <div class="ts-card-title">{s.title}</div>
            <!-- Status pill -->
            <span class="ts-pill ts-pill-{s.status}">
              {#if s.status === 'done'}✓&nbsp;{/if}{#if s.status === 'error'}✕&nbsp;{/if}{STATUS_PILL[s.status]}
            </span>
          </div>
          {#if s.count}
            <div class="ts-count">{s.count}</div>
          {/if}
        </div>
      </div>

      <!-- Connector to next stage -->
      {#if i < stages.length - 1}
        {@const cs = connState(i)}
        <div class="ts-conn ts-conn-{cs}">
          {#if cs === 'active'}<span class="ts-ptcl"></span>{/if}
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
  /* ── Wrapper ─────────────────────────────────────────────────── */
  .ts-wrap {
    font-family: var(--pw-font-body, Inter, sans-serif);
    overflow-x: auto;
  }

  /* ── Header ──────────────────────────────────────────────────── */
  .ts-head {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .ts-title {
    font-size: 13px;
    font-weight: 700;
    color: var(--pw-ink, #2c2c2c);
  }
  .ts-live {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--pw-muted, #8a8276);
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .ts-running .ts-live { color: var(--pw-accent, #c96342); }
  .ts-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--pw-accent, #c96342);
    flex-shrink: 0;
    animation: ts-pulse 2.4s ease-in-out infinite;
  }
  .ts-badge {
    font-size: 11px;
    font-weight: 600;
    color: #0ca6b0;
  }

  /* ── Pipeline row ────────────────────────────────────────────── */
  .ts-lanes {
    display: flex;
    align-items: stretch;
    gap: 0;
    overflow-x: auto;
    padding-bottom: 4px;
  }

  /* ── Stage ───────────────────────────────────────────────────── */
  .ts-stage {
    flex: 1 1 0;
    min-width: 150px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    cursor: pointer;
    outline: none;
    position: relative;
  }
  .ts-lane {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #8a8070;
    text-transform: uppercase;
    padding-left: 2px;
  }

  /* ── Card ─────────────────────────────────────────────────────── */
  .ts-card {
    background: var(--pw-bg, #fff);
    border: 1px solid var(--pw-border, #e5ddcf);
    border-top: 3px solid var(--ts-color, #c96342);
    padding: 11px 13px;
    min-height: 96px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    position: relative;
    overflow: hidden;
    transition: box-shadow 0.15s, border-color 0.15s;
  }
  .ts-card-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 6px;
  }
  .ts-card-title {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.02em;
    color: var(--ts-color, #c96342);
    flex: 1;
  }
  .ts-count {
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #4a4438;
    line-height: 1.4;
    margin-top: auto;
  }

  /* ── Status pills ────────────────────────────────────────────── */
  .ts-pill {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: 3px;
    flex-shrink: 0;
    white-space: nowrap;
  }
  .ts-pill-idle    { background: #f0ebe2; color: #9a8e80; }
  .ts-pill-running { background: #fbeee7; color: var(--pw-accent, #c96342); animation: ts-pill-pulse 1.8s ease-in-out infinite; }
  .ts-pill-done    { background: #eaf5ec; color: #2e7d3e; }
  .ts-pill-skipped { background: #f5f0e8; color: #b0a070; }
  .ts-pill-error   { background: #fdecea; color: #c0392b; }

  /* ── Per-status card overrides ───────────────────────────────── */

  /* running — pulsing glow + bright accent border */
  .ts-s-running .ts-card {
    border-color: var(--ts-color, #c96342);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--ts-color, #c96342) 18%, transparent);
    animation: ts-card-glow 2.2s ease-in-out infinite;
  }

  /* done — solid, muted */
  .ts-s-done .ts-card {
    border-top-color: var(--ts-color, #c96342);
    opacity: 0.82;
  }
  .ts-s-done .ts-card-title { filter: saturate(0.5); }

  /* idle — dim / greyed, dashed border */
  .ts-s-idle .ts-card {
    border-style: dashed;
    border-top-style: solid;
    opacity: 0.55;
    background: #faf8f5;
  }
  .ts-s-idle .ts-card-title { color: #9a8e80; }

  /* skipped — very dim + diagonal hatch */
  .ts-s-skipped .ts-card {
    opacity: 0.35;
    background: #f8f5ef;
    border-top-color: #c8c0b0;
  }
  .ts-s-skipped .ts-card-title { color: #9a8e80; }

  /* error — red border */
  .ts-s-error .ts-card {
    border-color: #e05a4a;
    border-top-color: #e05a4a;
    background: #fff8f7;
  }
  .ts-s-error .ts-card-title { color: #c0392b; }

  /* diagonal hatch for skipped */
  .ts-hatch {
    position: absolute;
    inset: 0;
    background-image: repeating-linear-gradient(
      45deg,
      transparent,
      transparent 6px,
      rgba(0,0,0,0.04) 6px,
      rgba(0,0,0,0.04) 8px
    );
    pointer-events: none;
  }

  /* ── Flash highlight ─────────────────────────────────────────── */
  .ts-flash .ts-card {
    animation: ts-flash-anim 0.55s ease-out forwards !important;
  }
  @keyframes ts-flash-anim {
    0%   { background: color-mix(in srgb, var(--ts-color, #c96342) 22%, #fff); }
    100% { background: var(--pw-bg, #fff); }
  }

  /* ── Connectors ──────────────────────────────────────────────── */
  .ts-conn {
    position: relative;
    flex: 0 0 38px;
    align-self: center;
    height: 26px;
    margin-top: 16px;
  }
  /* arrow head */
  .ts-conn::after {
    content: '';
    position: absolute;
    top: 50%;
    right: 2px;
    transform: translateY(-50%);
    width: 0;
    height: 0;
    border-left: 7px solid var(--ts-cc, #d4c8b8);
    border-top: 4px solid transparent;
    border-bottom: 4px solid transparent;
  }
  /* dashed line */
  .ts-conn::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 2px;
    right: 10px;
    height: 2px;
    transform: translateY(-50%);
    background-image: repeating-linear-gradient(
      90deg,
      var(--ts-cc, #d4c8b8) 0 7px,
      transparent 7px 12px
    );
  }

  /* active — animated dash + solid arrowhead */
  .ts-conn-active {
    --ts-cc: #c96342;
  }
  .ts-conn-active::before { animation: ts-dash 1.8s linear infinite; }

  /* done — solid filled line (no dash) */
  .ts-conn-done {
    --ts-cc: #2e7d3e;
  }
  .ts-conn-done::before {
    background-image: none;
    background-color: var(--ts-cc, #2e7d3e);
  }

  /* idle — faint dashed */
  .ts-conn-idle {
    --ts-cc: #d4c8b8;
  }
  .ts-conn-idle::before { opacity: 0.55; }
  .ts-conn-idle::after  { opacity: 0.55; }

  /* traveling particle (active connectors only) */
  .ts-ptcl {
    position: absolute;
    top: 50%;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    margin-top: -2.5px;
    background: var(--ts-cc, #c96342);
    filter: drop-shadow(0 0 3px var(--ts-cc, #c96342));
    animation: ts-trav 2s linear infinite;
  }

  /* ── Keyframes ───────────────────────────────────────────────── */
  @keyframes ts-dash       { to { background-position: 24px 0; } }
  @keyframes ts-trav       { 0% { left: 0; opacity: 0; } 10% { opacity: 1; } 88% { opacity: 1; } 100% { left: calc(100% - 10px); opacity: 0; } }
  @keyframes ts-pulse      { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  @keyframes ts-pill-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
  @keyframes ts-card-glow  { 0%, 100% { box-shadow: 0 0 0 2px color-mix(in srgb, var(--ts-color, #c96342) 18%, transparent); } 50% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--ts-color, #c96342) 34%, transparent); } }

  @media (max-width: 680px) {
    .ts-stage { min-width: 150px; }
  }
</style>
