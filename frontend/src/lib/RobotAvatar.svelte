<script lang="ts">
  // Shared mini robot avatar — same character as the FloatingRobot, used as the
  // chat / embed message avatar. Mood drives small animations. Pure SVG+CSS,
  // no data/logic. KEEP everything else around it unchanged — this only renders
  // the little robot face.
  //   mood: idle | thinking | typing | tool | done | error
  let { mood = 'idle', size = 20 }: { mood?: string; size?: number } = $props();

  const accent = '#c96342';
  const dotColor = $derived(
    mood === 'error' ? '#e05a4a'
      : (mood === 'thinking' || mood === 'typing' || mood === 'tool') ? accent
      : '#0ecad4'
  );
</script>

<span class="ra ra-{mood}" style="--ra-size:{size}px; --ra-accent:{accent};" aria-hidden="true">
  <svg viewBox="0 0 32 36" width={size} height={size * 36 / 32} class="ra-svg">
    <!-- antenna -->
    <line x1="16" y1="7" x2="16" y2="3" stroke={accent} stroke-width="2" stroke-linecap="round" />
    <circle class="ra-ant" cx="16" cy="2.5" r="2" fill={dotColor} />
    <!-- head/body -->
    <rect class="ra-body" x="5" y="7" width="22" height="17" rx="5" fill={accent} />
    <!-- eyes (rects normally; thin lines when blinking/sleeping via CSS) -->
    <rect class="ra-eye ra-eye-l" x="10" y="12" width="4" height="4" rx="1" fill="#1a1414" />
    <rect class="ra-eye ra-eye-r" x="18" y="12" width="4" height="4" rx="1" fill="#1a1414" />
    <!-- mouth: flat normally, animates while typing -->
    <rect class="ra-mouth" x="12" y="19" width="8" height="1.6" rx="0.8" fill="#1a1414" />
    <!-- feet -->
    <rect x="10" y="25" width="3" height="5" rx="1" fill={accent} />
    <rect x="15" y="25" width="3" height="5" rx="1" fill={accent} />
    <rect x="20" y="25" width="3" height="5" rx="1" fill={accent} />
  </svg>
</span>

<style>
  .ra { display: inline-flex; align-items: center; justify-content: center; line-height: 0; }
  .ra-svg { display: block; }

  /* antenna tip pulse — faster when busy */
  .ra-ant { animation: ra-pulse 1.8s ease-in-out infinite; }
  .ra-thinking .ra-ant, .ra-typing .ra-ant, .ra-tool .ra-ant { animation-duration: 0.8s; }

  /* thinking: eyes glance up (smaller, shifted up) */
  .ra-thinking .ra-eye { transform: translateY(-1px) scaleY(0.7); transform-origin: center; }

  /* typing: mouth opens/closes like it's talking */
  .ra-typing .ra-mouth { animation: ra-talk 0.45s ease-in-out infinite; transform-origin: center; }

  /* done: brief happy squint */
  .ra-done .ra-eye { animation: ra-blink 3.5s ease-in-out infinite; }

  /* error: red body + slight shake */
  .ra-error .ra-body { fill: #c0392b; }
  .ra-error { animation: ra-shake 0.5s ease-in-out 2; }

  /* idle: occasional blink */
  .ra-idle .ra-eye { animation: ra-blink 5s ease-in-out infinite; }

  @keyframes ra-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  @keyframes ra-talk { 0%,100% { transform: scaleY(1); } 50% { transform: scaleY(2.4); } }
  @keyframes ra-blink { 0%,92%,100% { transform: scaleY(1); } 96% { transform: scaleY(0.1); } }
  @keyframes ra-shake { 0%,100% { transform: translateX(0); } 25% { transform: translateX(-1.5px); } 75% { transform: translateX(1.5px); } }

  @media (prefers-reduced-motion: reduce) {
    .ra-ant, .ra-mouth, .ra-eye, .ra-error { animation: none !important; }
  }
</style>
