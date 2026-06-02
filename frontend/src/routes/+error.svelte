<script lang="ts">
  import { page } from '$app/state';
</script>

<div style="min-height: 100vh; background: var(--pw-bg); display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-body); padding: 40px;">
  <div style="text-align: center; max-width: 520px;">
    <div style="font-family: var(--pw-font-headline); font-size: 88px; font-weight: 500; color: var(--pw-ink); letter-spacing: -0.03em; line-height: 1;">{page.status}</div>
    <h1 style="font-family: var(--pw-font-headline); font-size: 18px; font-weight: 500; color: var(--pw-ink); letter-spacing: -0.02em; margin: 16px 0 8px 0;">Something went wrong</h1>
    <p style="font-size: 12px; color: var(--pw-muted); line-height: 1.55; margin: 0 0 28px 0;">
      {page.error?.message || (page.status === 404 ? 'The page you were looking for could not be found.' : page.status === 403 ? 'You do not have access to this page.' : 'An unexpected error occurred.')}
    </p>
    <a href="/ui/home" style="display: inline-flex; align-items: center; gap: 8px; padding: 12px 22px; background: var(--pw-accent); color: #fff; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; text-decoration: none; border-radius: var(--pw-radius-pill); transition: background 0.15s;">
      ← Back home
    </a>
  </div>
</div>
