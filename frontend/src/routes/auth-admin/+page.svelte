<script lang="ts">
  import AuthAdminPanel from '$lib/admin/AuthAdminPanel.svelte';
</script>
<svelte:head><title>Authentication — Admin</title></svelte:head>
<div class="auth-admin-route">
  <AuthAdminPanel />
</div>
<style>
  .auth-admin-route { max-width: 920px; margin: 0 auto; padding: 32px 20px 80px; }
</style>
