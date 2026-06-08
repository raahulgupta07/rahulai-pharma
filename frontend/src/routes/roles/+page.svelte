<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { base } from '$app/paths';

  // Tiers & Roles merged into Users & Access. Keep this route alive as a redirect.
  onMount(() => { goto(`${base}/ui/users`, { replaceState: true }); });
</script>

<svelte:head><title>Roles &amp; Access → Users &amp; Access</title></svelte:head>

<div class="rr-redir">
  <p>Roles &amp; Access moved into <a href="{base}/ui/users">Users &amp; Access</a>. Redirecting…</p>
</div>

<style>
  .rr-redir { max-width: 600px; margin: 80px auto; padding: 0 20px; font-size: 14px; opacity: 0.75; }
  .rr-redir a { color: var(--pw-accent, #c96342); font-weight: 600; }
</style>
