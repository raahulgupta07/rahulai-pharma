<script lang="ts">
  // Usage & Cost — standalone full page (no command-center rail). Super-admin
  // only (the /api/admin/usage endpoints are super-gated; non-super sees empty).
  import UsagePanel from '$lib/admin/UsagePanel.svelte';
</script>

<svelte:head><title>Usage &amp; Cost</title></svelte:head>

<div class="usage-page">
  <UsagePanel />
</div>

<style>
  .usage-page { max-width: 1400px; margin: 0 auto; }
</style>
