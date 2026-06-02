<script lang="ts">
  import Icon from '$lib/Icon.svelte';
 import { onMount } from 'svelte';

 let profile = $state<any>(null);
 let loading = $state(true);
 let saving = $state(false);
 let saveMsg = $state('');

 // Editable fields
 let firstName = $state('');
 let lastName = $state('');
 let email = $state('');
 let department = $state('');
 let jobTitle = $state('');
 let phone = $state('');
 let bio = $state('');
 let timezone = $state('UTC');

 function _h(): Record<string, string> {
 const t = typeof localStorage !== 'undefined' ? localStorage.getItem('dash_token') : null;
 return t ? { Authorization: `Bearer ${t}` } : {};
 }

 onMount(async () => {
 const username = localStorage.getItem('dash_user') || '';
 try {
 const res = await fetch(`/api/auth/users/${username}/profile`, { headers: _h() });
 if (res.ok) {
 profile = await res.json();
 firstName = profile.first_name || '';
 lastName = profile.last_name || '';
 email = profile.email || '';
 department = profile.department || '';
 jobTitle = profile.job_title || '';
 phone = profile.phone || '';
 bio = profile.bio || '';
 timezone = profile.timezone || 'UTC';
 }
 } catch {}
 loading = false;
 });

 async function saveProfile() {
 saving = true; saveMsg = '';
 const username = localStorage.getItem('dash_user') || '';
 const params = new URLSearchParams({
 first_name: firstName, last_name: lastName, email, department, job_title: jobTitle, phone, bio, timezone,
 });
 try {
 const res = await fetch(`/api/auth/users/${username}/profile?${params}`, { method: 'PUT', headers: _h() });
 if (res.ok) saveMsg = 'Profile saved.';
 else saveMsg = 'Failed to save.';
 } catch { saveMsg = 'Connection error.'; }
 saving = false;
 }

 const initial = $derived((profile?.username || 'U').charAt(0).toUpperCase());
</script>

<div style="padding: 40px 24px; overflow-y: auto; height: 100%; background: var(--pw-bg); font-family: var(--pw-font-body);">
  <div style="max-width: 680px; margin: 0 auto;">

    <h1 style="font-family: var(--pw-font-headline); font-size: 20px; font-weight: 500; letter-spacing: -0.02em; color: var(--pw-ink); margin: 0 0 6px 0;">Your profile</h1>
    <p style="font-size: 11px; color: var(--pw-muted); margin: 0 0 28px 0;">Manage how others see you across Dash.</p>

    {#if loading}
      <div style="font-size: 11px; color: var(--pw-muted);">Loading…</div>
    {:else if profile}

      <!-- Profile card -->
      <div style="background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: var(--pw-radius); padding: 28px; box-shadow: var(--pw-shadow-sm);">

        <!-- Avatar row -->
        <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid var(--pw-border);">
          <div style="width: 64px; height: 64px; border-radius: 50%; background: var(--pw-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-family: var(--pw-font-headline); font-size: 26px; font-weight: 500;">
            {initial}
          </div>
          <div style="flex: 1;">
            <div style="font-size: 12px; font-weight: 500; color: var(--pw-ink);">{profile.username}</div>
            <div style="font-size: 11px; color: var(--pw-muted); margin-top: 2px;">Signed in via {profile.auth_provider || 'local'} · since {profile.created_at?.slice(0, 10) || '—'}</div>
          </div>
          <button type="button" style="background: transparent; border: 1px solid var(--pw-border-strong); color: var(--pw-ink-soft); padding: 8px 14px; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer;">Upload photo</button>
        </div>

        <div style="display: flex; flex-direction: column; gap: 16px;">
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">First name</label>
              <input type="text" bind:value={firstName} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Last name</label>
              <input type="text" bind:value={lastName} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
          </div>
          <div>
            <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Email</label>
            <input type="email" bind:value={email} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Department</label>
              <input type="text" bind:value={department} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Job title</label>
              <input type="text" bind:value={jobTitle} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Phone</label>
              <input type="text" bind:value={phone} style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
            <div>
              <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Timezone</label>
              <input type="text" bind:value={timezone} placeholder="UTC" style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 13px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none;" />
            </div>
          </div>
          <div>
            <label style="display: block; font-size: 12.5px; color: var(--pw-muted); margin-bottom: 6px;">Bio</label>
            <textarea bind:value={bio} rows="3" placeholder="Tell us a little about yourself…" style="width: 100%; border: 1px solid var(--pw-border-strong); padding: 10px 12px; font-family: var(--pw-font-body); font-size: 11px; background: var(--pw-surface); color: var(--pw-ink); border-radius: var(--pw-radius-sm); outline: none; resize: vertical;"></textarea>
          </div>

          {#if saveMsg}
            <div style="font-size: 11px; color: {saveMsg === 'Profile saved.' ? 'var(--pw-success)' : 'var(--pw-error)'};">{saveMsg}</div>
          {/if}

          <div style="display: flex; gap: 10px; padding-top: 8px;">
            <button type="button" onclick={saveProfile} disabled={saving} style="background: var(--pw-accent); color: #fff; border: none; padding: 11px 22px; font-family: var(--pw-font-body); font-size: 11px; font-weight: 500; border-radius: var(--pw-radius-pill); cursor: pointer; opacity: {saving ? 0.6 : 1};">
              {saving ? 'Saving…' : 'Save profile'}
            </button>
          </div>
        </div>
      </div>

      <!-- My Agent teaser card -->
      <a href="/ui/me/agent" style="display: block; margin-top: 18px; background: var(--pw-surface); border: 1px solid var(--pw-border); border-radius: var(--pw-radius); padding: 18px 22px; box-shadow: var(--pw-shadow-sm); text-decoration: none; color: inherit; transition: border-color 120ms ease, background 120ms ease;" onmouseover={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--pw-accent)'; }} onmouseout={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--pw-border)'; }}>
        <div style="display: flex; align-items: center; gap: 14px;">
          <div style="width: 44px; height: 44px; border-radius: 0; background: rgba(201,99,66,0.10); display: flex; align-items: center; justify-content: center; font-size: 19px;"><Icon name="dna" size={14} /></div>
          <div style="flex: 1; min-width: 0;">
            <div style="font-family: var(--pw-font-headline); font-size: 12px; font-weight: 600; color: var(--pw-ink); margin-bottom: 2px;">My Agent</div>
            <div style="font-size: 12.5px; color: var(--pw-muted);">Your personal AI w/ persistent memory across all projects</div>
          </div>
          <div style="font-size: 11px; font-weight: 600; color: var(--pw-accent); text-transform: uppercase; letter-spacing: 0.04em;">Manage →</div>
        </div>
      </a>
    {/if}
  </div>
</div>
