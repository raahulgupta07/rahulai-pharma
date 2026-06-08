<script lang="ts">
  let {
    status = 'watching',
    message = '',
    progress = 0,
    totalSteps = 14,
    countdown = 0,
    lastTrained = '',
    nextCheck = '',
    deltaInfo = '',
    onTrainNow = undefined as (() => void) | undefined,
    onPause = undefined as (() => void) | undefined,
    onDismiss = undefined as (() => void) | undefined,
    onRetry = undefined as (() => void) | undefined,
  }: {
    status?: 'watching' | 'detected' | 'training' | 'done' | 'error' | 'disabled';
    message?: string;
    progress?: number;
    totalSteps?: number;
    countdown?: number;
    lastTrained?: string;
    nextCheck?: string;
    deltaInfo?: string;
    onTrainNow?: (() => void) | undefined;
    onPause?: (() => void) | undefined;
    onDismiss?: (() => void) | undefined;
    onRetry?: (() => void) | undefined;
  } = $props();

  const stepNames = [
    'Catalog', 'Profile', 'Dimensions', 'Hierarchy', 'Sampling',
    'Analysis', 'Q&A Gen', 'Persona', 'Workflows', 'Relationships',
    'Vectors', 'Brain', 'Domain', 'Watermark'
  ];

  const currentStep = $derived(stepNames[Math.min(progress, stepNames.length - 1)] || '');
  const progressPct = $derived(Math.round((progress / totalSteps) * 100));
</script>

<div class="robot-card status-{status}">
  <!-- SVG Robot -->
  <div class="robot-svg-wrap">
    <svg viewBox="0 0 64 64" width="72" height="72" class="robot-svg body-{status}">
      <!-- Antenna -->
      <line x1="32" y1="4" x2="32" y2="0" stroke="#c96342" stroke-width="1.5" />
      <circle cx="32" cy="0" r="3" fill="#c96342" />

      <!-- Head -->
      <rect x="20" y="4" width="24" height="20" rx="4" fill="white" stroke="#c96342" stroke-width="1.5" />

      <!-- Eyes per state -->
      {#if status === 'watching'}
        <circle cx="26" cy="12" r="3" fill="#0ecad4" class="eye-pulse" />
        <circle cx="38" cy="12" r="3" fill="#0ecad4" class="eye-pulse" />
      {:else if status === 'detected'}
        <!-- Diamond/star eyes -->
        <polygon points="26,9 28,12 26,15 24,12" fill="#d4930e" class="eye-blink" />
        <polygon points="38,9 40,12 38,15 36,12" fill="#d4930e" class="eye-blink" />
      {:else if status === 'training'}
        <!-- Spinning arc eyes -->
        <circle cx="26" cy="12" r="3" fill="none" stroke="#c96342" stroke-width="1.5" stroke-dasharray="10 4" class="eye-spin" />
        <circle cx="38" cy="12" r="3" fill="none" stroke="#c96342" stroke-width="1.5" stroke-dasharray="10 4" class="eye-spin" />
      {:else if status === 'done'}
        <!-- Checkmark eyes -->
        <polyline points="23,12 25,14 29,10" fill="none" stroke="#2d8a4e" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        <polyline points="35,12 37,14 41,10" fill="none" stroke="#2d8a4e" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      {:else if status === 'error'}
        <!-- X eyes -->
        <line x1="23" y1="9" x2="29" y2="15" stroke="#e05a4a" stroke-width="1.5" stroke-linecap="round" />
        <line x1="29" y1="9" x2="23" y2="15" stroke="#e05a4a" stroke-width="1.5" stroke-linecap="round" />
        <line x1="35" y1="9" x2="41" y2="15" stroke="#e05a4a" stroke-width="1.5" stroke-linecap="round" />
        <line x1="41" y1="9" x2="35" y2="15" stroke="#e05a4a" stroke-width="1.5" stroke-linecap="round" />
      {:else}
        <!-- Disabled: dash eyes -->
        <line x1="23" y1="12" x2="29" y2="12" stroke="#c8c0b4" stroke-width="2" stroke-linecap="round" />
        <line x1="35" y1="12" x2="41" y2="12" stroke="#c8c0b4" stroke-width="2" stroke-linecap="round" />
      {/if}

      <!-- Mouth per state -->
      {#if status === 'watching'}
        <!-- Flat neutral mouth -->
        <line x1="27" y1="20" x2="37" y2="20" stroke="#c96342" stroke-width="1.2" stroke-linecap="round" />
      {:else if status === 'detected'}
        <!-- Open excited mouth -->
        <rect x="28" y="19" width="8" height="4" rx="2" fill="#d4930e" />
      {:else if status === 'training'}
        <!-- Spinning gear mouth -->
        <circle cx="32" cy="21" r="2.5" fill="none" stroke="#c96342" stroke-width="1.2" stroke-dasharray="4 2" class="mouth-spin" />
      {:else if status === 'done'}
        <!-- Curved smile -->
        <path d="M27 19 Q32 24 37 19" fill="none" stroke="#2d8a4e" stroke-width="1.5" stroke-linecap="round" />
      {:else if status === 'error'}
        <!-- Downward curve frown -->
        <path d="M27 22 Q32 17 37 22" fill="none" stroke="#e05a4a" stroke-width="1.5" stroke-linecap="round" />
      {:else}
        <!-- Disabled: flat line -->
        <line x1="27" y1="20" x2="37" y2="20" stroke="#c8c0b4" stroke-width="1.2" stroke-linecap="round" />
      {/if}

      <!-- Body -->
      <rect x="16" y="26" width="32" height="22" rx="3" fill="white" stroke="#c96342" stroke-width="1.5" />

      <!-- Chest detail -->
      <rect x="24" y="30" width="16" height="8" rx="2" fill="#f6f2ea" stroke="#c96342" stroke-width="1" />

      <!-- Chest indicator dot -->
      {#if status === 'training'}
        <circle cx="32" cy="34" r="2.5" fill="#c96342" class="chest-pulse" />
      {:else if status === 'done'}
        <circle cx="32" cy="34" r="2.5" fill="#2d8a4e" />
      {:else if status === 'error'}
        <circle cx="32" cy="34" r="2.5" fill="#e05a4a" />
      {:else if status === 'detected'}
        <circle cx="32" cy="34" r="2.5" fill="#d4930e" class="chest-pulse" />
      {:else}
        <circle cx="32" cy="34" r="2.5" fill="#c8c0b4" />
      {/if}

      <!-- Left Arm -->
      <rect x="8" y="28" width="8" height="14" rx="3" fill="white" stroke="#c96342" stroke-width="1.5" />

      <!-- Right Arm -->
      <rect x="48" y="28" width="8" height="14" rx="3" fill="white" stroke="#c96342" stroke-width="1.5" />

      <!-- Left Leg -->
      <rect x="20" y="50" width="10" height="12" rx="2" fill="white" stroke="#c96342" stroke-width="1.5" />

      <!-- Right Leg -->
      <rect x="34" y="50" width="10" height="12" rx="2" fill="white" stroke="#c96342" stroke-width="1.5" />
    </svg>
  </div>

  <!-- Status badge -->
  <div class="robot-badge badge-{status}">
    {#if status === 'watching'}
      <span class="badge-dot"></span> WATCHING
    {:else if status === 'detected'}
      <span class="badge-dot"></span> CHANGE DETECTED
    {:else if status === 'training'}
      <span class="badge-spinner"></span> TRAINING
    {:else if status === 'done'}
      ✓ COMPLETE
    {:else if status === 'error'}
      ✗ FAILED
    {:else}
      ○ PAUSED
    {/if}
  </div>

  <!-- Message -->
  {#if message}
    <p class="robot-msg">{message}</p>
  {/if}

  <!-- Countdown (detected state) -->
  {#if status === 'detected' && countdown > 0}
    <div class="robot-countdown">
      <div class="countdown-track">
        <div class="countdown-bar" style="animation-duration:{countdown}s"></div>
      </div>
      <span class="robot-sub">Starting in {countdown}s</span>
    </div>
  {/if}

  <!-- Progress (training state) -->
  {#if status === 'training'}
    <div class="robot-progress">
      <div class="prog-track">
        <div class="prog-fill" style="width:{progressPct}%"></div>
      </div>
      <span class="robot-sub">Step {progress}/{totalSteps} — {currentStep}</span>
    </div>
  {/if}

  <!-- Stats (watching/done) -->
  {#if (status === 'watching' || status === 'done') && (lastTrained || nextCheck)}
    <div class="robot-stats">
      {#if lastTrained}<span>Last: {lastTrained}</span>{/if}
      {#if lastTrained && nextCheck}<span class="stat-sep">·</span>{/if}
      {#if nextCheck}<span>Next: {nextCheck}</span>{/if}
    </div>
  {/if}

  <!-- Delta info -->
  {#if deltaInfo}
    <p class="robot-delta">{deltaInfo}</p>
  {/if}

  <!-- Actions -->
  <div class="robot-actions">
    {#if status === 'watching'}
      {#if onPause}
        <button class="rb-ghost" onclick={onPause}>⏸ Pause</button>
      {/if}
      {#if onTrainNow}
        <button class="rb-coral" onclick={onTrainNow}>▶ Train Now</button>
      {/if}
    {:else if status === 'detected'}
      {#if onDismiss}
        <button class="rb-ghost" onclick={onDismiss}>✕ Cancel</button>
      {/if}
      {#if onTrainNow}
        <button class="rb-coral" onclick={onTrainNow}>▶ Train Now</button>
      {/if}
    {:else if status === 'done'}
      {#if onDismiss}
        <button class="rb-ghost" onclick={onDismiss}>✕ Dismiss</button>
      {/if}
    {:else if status === 'error'}
      {#if onRetry}
        <button class="rb-coral" onclick={onRetry}>↻ Retry</button>
      {/if}
    {/if}
  </div>
</div>

<style>
  /* ── CSS variables (match CityPharma light-mode) ── */
  :root {
    --pw-accent: #c96342;
    --pw-bg-alt: #f6f2ea;
    --pw-surface: #fff;
    --pw-ink: #1a1614;
    --pw-muted: #877f74;
    --pw-border: #e5ddd0;
  }

  /* ── Card ── */
  .robot-card {
    background: var(--pw-surface, #fff);
    border: 1px solid var(--pw-border, #e5ddd0);
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(26, 22, 20, 0.07), 0 0 0 0 transparent;
    padding: 16px;
    width: 100%;
    max-width: 340px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    font-family: inherit;
    box-sizing: border-box;
  }

  /* ── Robot SVG wrapper ── */
  .robot-svg-wrap {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 80px;
  }

  /* ── Body animations ── */
  .body-watching {
    animation: bob 3s ease-in-out infinite;
  }

  .body-detected {
    animation: bounce 0.8s ease-in-out infinite;
  }

  .body-training {
    animation: sway 1s ease-in-out infinite;
  }

  .body-done {
    animation: jump 0.6s ease-out forwards;
  }

  .body-error {
    animation: shake 0.4s ease-in-out;
  }

  .body-disabled {
    opacity: 0.6;
  }

  /* ── Eye animations ── */
  .eye-pulse {
    animation: eyepulse 2s ease-in-out infinite;
  }

  .eye-blink {
    animation: eyepulse 0.5s ease-in-out infinite;
  }

  .eye-spin {
    transform-origin: center;
    animation: spin 1s linear infinite;
    transform-box: fill-box;
  }

  .mouth-spin {
    transform-origin: center;
    animation: spin 1.5s linear infinite;
    transform-box: fill-box;
  }

  .chest-pulse {
    animation: eyepulse 1s ease-in-out infinite;
  }

  /* ── Status badge ── */
  .robot-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 3px 10px;
    border-radius: 999px;
    line-height: 1.4;
  }

  .badge-watching {
    background: #ede9e2;
    color: #877f74;
  }

  .badge-detected {
    background: #fef3cd;
    color: #a06800;
  }

  .badge-training {
    background: #fbe8e2;
    color: #c96342;
  }

  .badge-done {
    background: #d6f0e0;
    color: #2d8a4e;
  }

  .badge-error {
    background: #fde8e6;
    color: #c0392b;
  }

  .badge-disabled {
    background: #ede9e2;
    color: #c8c0b4;
  }

  .badge-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }

  .badge-spinner {
    display: inline-block;
    width: 8px;
    height: 8px;
    border: 1.5px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  /* ── Message ── */
  .robot-msg {
    margin: 0;
    font-size: 13px;
    color: #4a4438;
    text-align: center;
    line-height: 1.5;
  }

  /* ── Countdown ── */
  .robot-countdown {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .countdown-track {
    width: 100%;
    height: 4px;
    background: #e5ddd0;
    border-radius: 2px;
    overflow: hidden;
  }

  .countdown-bar {
    height: 100%;
    background: #d4930e;
    border-radius: 2px;
    animation: countdown linear forwards;
    width: 100%;
  }

  /* ── Training progress ── */
  .robot-progress {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .prog-track {
    width: 100%;
    height: 6px;
    background: #e5ddd0;
    border-radius: 3px;
    overflow: hidden;
  }

  .prog-fill {
    height: 100%;
    background: var(--pw-accent, #c96342);
    border-radius: 3px;
    transition: width 0.4s ease;
  }

  /* ── Stats row ── */
  .robot-stats {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--pw-muted, #877f74);
    flex-wrap: wrap;
    justify-content: center;
  }

  .stat-sep {
    color: var(--pw-border, #e5ddd0);
  }

  /* ── Sub text ── */
  .robot-sub {
    font-size: 11px;
    color: var(--pw-muted, #877f74);
    text-align: center;
  }

  /* ── Delta info ── */
  .robot-delta {
    margin: 0;
    font-size: 12px;
    font-weight: 600;
    color: #a06800;
    background: #fef3cd;
    padding: 3px 10px;
    border-radius: 4px;
    text-align: center;
  }

  /* ── Action buttons ── */
  .robot-actions {
    display: flex;
    gap: 8px;
    width: 100%;
    justify-content: center;
    flex-wrap: wrap;
    margin-top: 2px;
  }

  .rb-ghost,
  .rb-coral {
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
    border-radius: 4px;
    cursor: pointer;
    border: 1px solid;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
    line-height: 1.2;
    font-family: inherit;
  }

  .rb-ghost {
    background: transparent;
    color: var(--pw-muted, #877f74);
    border-color: var(--pw-border, #e5ddd0);
  }

  .rb-ghost:hover {
    background: var(--pw-bg-alt, #f6f2ea);
    color: var(--pw-ink, #1a1614);
    border-color: #c8c0b4;
  }

  .rb-coral {
    background: var(--pw-accent, #c96342);
    color: #fff;
    border-color: var(--pw-accent, #c96342);
  }

  .rb-coral:hover {
    background: #b5512e;
    border-color: #b5512e;
  }

  .rb-ghost:active,
  .rb-coral:active {
    transform: scale(0.97);
  }

  /* ── Keyframe animations ── */
  @keyframes bob {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-3px); }
  }

  @keyframes bounce {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-6px); }
  }

  @keyframes sway {
    0%, 100% { transform: translateX(0); }
    50% { transform: translateX(2px); }
  }

  @keyframes shake {
    0% { transform: translateX(0); }
    25% { transform: translateX(-3px); }
    75% { transform: translateX(3px); }
    100% { transform: translateX(0); }
  }

  @keyframes jump {
    0% { transform: translateY(0); }
    30% { transform: translateY(-10px); }
    60% { transform: translateY(-4px); }
    80% { transform: translateY(-1px); }
    100% { transform: translateY(0); }
  }

  @keyframes eyepulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  @keyframes countdown {
    from { width: 100%; }
    to { width: 0%; }
  }
</style>
