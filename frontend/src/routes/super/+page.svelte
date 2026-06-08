<script lang="ts">
  import { onMount } from 'svelte';
  import { base } from '$app/paths';
  // Super Dashboard folded into the unified Admin Console (command-center → Cockpit tab).
  onMount(() => { window.location.replace(`${base}/ui/command-center`); });
</script>

<div style="padding: 48px; color: var(--pw-ink-soft, #8a8275); font-size: 14px;">Redirecting to Admin Console…</div>
