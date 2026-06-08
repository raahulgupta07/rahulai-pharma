# CityAgent Pharma — Chat Widget Integration (Dev Handoff)

Drop the CityAgent chat agent into any website or your PHP app.

## Your values (already provisioned)

| Key | Value |
|-----|-------|
| BASE_URL (dev) | `http://localhost:8011` — replace with your production URL |
| `embed_id` | `emb_rGd8VWW8DloS6WNNssvenA` |
| `public_key` | `pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT` |
| `secret_key` | **shown once** — get it in Dash → Integrations → Widgets → Rotate secret. Server-side ONLY, never ship to browser. |

> ⚠️ **PREREQUISITE (admin does this first, in Dash → Integrations → Widgets):**
> 1. Set widget **status = live** (currently `draft` — won't run otherwise).
> 2. Add your site's **origin(s)** to the allowlist (e.g. `https://yourpharmacy.com`). Empty = blocked.
> Until both are done every request returns `403 origin not allowed`.

---

## Path A — Drop-in chat bubble (anonymous, simplest)

One line in your HTML. Renders a floating chat bubble, isolated in a shadow DOM.

```html
<script
  src="http://localhost:8011/api/embed/widget.js"
  data-embed-id="emb_rGd8VWW8DloS6WNNssvenA"
  data-public-key="pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT"
  data-title="CityAgent Pharma"
  data-greeting="Hi! Ask me about stock, substitutes, or indications."
  data-position="bottom-right"
  data-accent="#c96342"
  async>
</script>
```

Optional attributes: `data-theme` (light/dark), `data-logo` (url), `data-stream="true"`, `data-show-branding="false"`.
Programmatic: `DashAgent.send("is paracetamol in stock?")`.

---

## Path B — User-scoped (PHP app, row-level masking) ← **your case**

For a logged-in app where each user should only see THEIR store's stock/price.
Your PHP **server** signs the user payload with `secret_key`; the browser never sees the secret.

### B1. PHP — sign the payload (server-side)

```php
<?php
// config — secret_key stays on the server, never echoed to the page
$EMBED_ID   = 'emb_rGd8VWW8DloS6WNNssvenA';
$PUBLIC_KEY = 'pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT';
$SECRET_KEY = getenv('CITYAGENT_EMBED_SECRET');   // from Rotate-secret, store in env

// the logged-in user, from YOUR session/DB
$user = [
  'id'       => (string) $currentUser->id,          // e.g. "alice"
  'store_id' => (string) $currentUser->store_code,  // e.g. "20063-CCBRBKMY"
  'role'     => 'staff',                            // staff | customer
];

// CANONICAL JSON: keys sorted, no spaces — must match exactly or signature fails
ksort($user);
$canonical = json_encode($user, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
$signature = hash_hmac('sha256', $canonical, $SECRET_KEY);

// hand these to the page
?>
```

### B2. HTML — pass payload + signature to the widget

```html
<script
  src="http://localhost:8011/api/embed/widget.js"
  data-embed-id="<?= $EMBED_ID ?>"
  data-public-key="<?= $PUBLIC_KEY ?>"
  data-user='<?= htmlspecialchars($canonical, ENT_QUOTES) ?>'
  data-user-sig="<?= $signature ?>"
  data-title="CityAgent Pharma"
  async>
</script>
```

Dash recomputes the HMAC, verifies it, and binds the chat session to that user's
`store_id` → the agent automatically masks other stores' qty/price (3-tier scope).

---

## Path C — Custom UI (your own chat box, raw REST)

Skip the widget, build your own interface. Two calls.

### C1. Create a session

```bash
POST http://localhost:8011/api/embed/session/create
Content-Type: application/json
Origin: https://yourpharmacy.com          # must be in the allowlist

{
  "embed_id":   "emb_rGd8VWW8DloS6WNNssvenA",
  "public_key": "pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT",
  "user":       {"id":"alice","role":"staff","store_id":"20063-CCBRBKMY"},  // HMAC mode only
  "signature":  "<hmac-sha256-hex from B1>"                                   // HMAC mode only
}
→ 200 {"session_token":"sess_xxx","expires_in":900,"feature_config":{}}
```

### C2. Send a message

```bash
POST http://localhost:8011/api/embed/chat
Content-Type: application/json

{ "session_token": "sess_xxx", "message": "is paracetamol in stock at my branch?" }
→ 200 {"content":"...answer...","session_token":"sess_xxx","external_user":"alice","latency_ms":1234}
```

### C3. Node example (public mode)

```js
const BASE = "http://localhost:8011";
const r1 = await fetch(`${BASE}/api/embed/session/create`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "Origin": "https://yourpharmacy.com" },
  body: JSON.stringify({
    embed_id: "emb_rGd8VWW8DloS6WNNssvenA",
    public_key: "pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT",
  }),
});
const { session_token } = await r1.json();

const r2 = await fetch(`${BASE}/api/embed/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_token, message: "list substitutes for amoxicillin" }),
});
console.log((await r2.json()).content);
```

---

## Quick verify (dev runs this)

```bash
# 1. widget loads
curl -I "http://localhost:8011/api/embed/widget.js"     # expect 200, content-type application/javascript

# 2. session + chat (public mode)
curl -s -X POST http://localhost:8011/api/embed/session/create \
  -H "Content-Type: application/json" -H "Origin: https://yourpharmacy.com" \
  -d '{"embed_id":"emb_rGd8VWW8DloS6WNNssvenA","public_key":"pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT"}'
# → {"session_token":"sess_...","expires_in":900}

curl -s -X POST http://localhost:8011/api/embed/chat \
  -H "Content-Type: application/json" \
  -d '{"session_token":"sess_...","message":"hello"}'
# → {"content":"...","latency_ms":...}
```

## Errors cheat-sheet

| Response | Fix |
|----------|-----|
| `403 origin not allowed` | Add your site origin in Dash → Widgets allowlist; send the `Origin` header |
| `403 embed disabled` | Widget status is `draft` — flip to `live` |
| `403 invalid user signature` | HMAC mismatch — canonical JSON must be sorted-keys + no spaces, same `secret_key` |
| `429` | Rate limited (per-embed/min) — back off, raise limit in Widget config |

Full live docs (no auth): **http://localhost:8011/api/embed/docs**
In-app copy-paste: **Dash → Integrations → Snippet & Docs** (tabs: HTML / Python / Node / PHP).
