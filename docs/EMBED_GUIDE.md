# Add the CityAgent Pharma chat to your website

A step-by-step guide for developers. Add an AI pharmacy assistant to any site by
pasting **one `<script>` tag**. No build step, no framework, no Composer. The
chat appears as a friendly robot in the bottom-right corner; visitors click it
to ask about stock, substitutes, prices and indications — in **English or
Burmese** (it replies in whichever language they use).

> **You don't download or host any code.** The widget is served by the
> CityAgent server. You update nothing when it improves — every site gets the
> new version automatically.

---

## What you'll need (2 minutes)

Ask your CityAgent administrator (or open the admin console yourself) for your
**widget cockpit**. It looks like this:

![Widget cockpit overview](images/embed/01-cockpit-overview.png)

From it you'll copy two public values and (optionally) one server-only secret.

---

## Step 1 — Copy your keys

Open **Admin console → Integrations → your widget**. The **KEYS** panel:

![KEYS panel — embed id, public key, secret, endpoint](images/embed/02-keys.png)

| Field | Looks like | Where it goes | Safe in the browser? |
|-------|-----------|---------------|----------------------|
| **EMBED ID** | `emb_xxxxxxxx` | the snippet | ✅ yes — public |
| **PUBLIC KEY** | `pub_xxxxxxxx` | the snippet | ✅ yes — public |
| **SECRET** | hidden | your **server** only (HMAC mode) | ⛔ **never** put in the browser |
| **ENDPOINT** | `https://…/api/embed/chat` | reference only | — |

Click **copy** next to **EMBED ID** and **PUBLIC KEY**. That's all you need for
the basic (anonymous) install.

> The **SECRET** is only used for *user-scoped* installs (Step 4, PHP). It's
> read by your server and used to sign the logged-in user — it never reaches
> the page.

---

## Step 2 — Check your widget is ON and Live

Top row of the cockpit: the **ON** toggle must be green, status **Live**.

![CONFIG panel — scope, role, rate, auth, status, style](images/embed/03-config.png)

The **CONFIG** panel tells you how this key behaves:

- **SCOPE** — which store's data it can see (e.g. *store 20064 · availability only*).
- **ROLE** — `staff` (full) or `customer` (prices/quantities hidden).
- **RATE** — requests/minute cap.
- **AUTH** — `public` (anyone) or `user-scoped` (signed users).
- **STYLE** — `consumer` (friendly, prices hidden) or analyst.

---

## Step 3 — Paste the snippet (plain HTML — any website)

The **DROP-IN SNIPPET** box has a one-click **copy**:

![DROP-IN SNIPPET box](images/embed/04-snippet.png)

Paste it just before the closing `</body>` tag of your page:

```html
<script
  src="https://YOUR-AGENT-DOMAIN/api/embed/widget.js"
  data-embed-id="emb_xxxxxxxx"
  data-key="pub_xxxxxxxx"
  data-title="City Pharmacy"
  data-greeting="Hi! Ask about stock, substitutes or prices."
  async></script>
```

Replace `YOUR-AGENT-DOMAIN`, `data-embed-id` and `data-key` with your values
(the cockpit fills these in automatically when you copy from there).

**That's it.** Reload the page → the robot launcher appears bottom-right.

![Widget launcher live on a page](images/embed/07-widget-live.png)

Click it → the chat opens with the robot, a greeting and suggested questions:

![Widget open — robot, greeting, suggested cards](images/embed/08-widget-open.png)

### Customise the look (optional)

Add any of these `data-*` attributes to the `<script>` tag:

| Attribute | Example | Effect |
|-----------|---------|--------|
| `data-title` | `"City Pharmacy"` | header title |
| `data-greeting` | `"Hi! How can I help?"` | first message |
| `data-position` | `bottom-right` / `bottom-left` | corner |
| `data-accent` | `#c96342` | brand colour (header, buttons, robot) |
| `data-theme` | `auto` / `light` / `dark` | colour scheme |
| `data-stream` | `true` | stream the answer token-by-token |

A **logo** set on the key replaces the robot with your uploaded logo.

---

## Step 4 — (Optional) User-scoped install with PHP

Use this when your site has **logged-in users** and you want the agent to scope
answers to *their* store (and hide other stores' numbers). Your server signs the
user with the **SECRET** — the secret never touches the browser.

The cockpit gives you ready-to-run PHP under **FULL PHP CODE**
(`widget-embed.php` + `CityAgentClient.php`). Click **show** / **download**:

![FULL PHP CODE tabs](images/embed/05-php-code.png)

`widget-embed.php` (abridged — your real keys are templated in on download):

```php
<?php
require __DIR__ . '/CityAgentClient.php';

$BASE_URL   = getenv('CITYAGENT_BASE')   ?: 'https://YOUR-AGENT-DOMAIN';
$EMBED_ID   = getenv('CITYAGENT_EMBED')  ?: 'emb_xxxxxxxx';
$PUBLIC_KEY = getenv('CITYAGENT_PUBKEY') ?: 'pub_xxxxxxxx';
$SECRET_KEY = getenv('CITYAGENT_EMBED_SECRET');   // set this in your server env

// your real logged-in user:
$user = [
  'id'       => (string)$currentUser->id,
  'store_id' => (string)$currentUser->store_code,
  'role'     => 'staff',          // staff | customer
];

// sign it server-side
$canonical = CityAgentClient::canonical($user);
$signature = hash_hmac('sha256', $canonical, (string)$SECRET_KEY);
?>
<script
  src="<?= $BASE_URL ?>/api/embed/widget.js"
  data-embed-id="<?= $EMBED_ID ?>"
  data-public-key="<?= $PUBLIC_KEY ?>"
  data-user='<?= htmlspecialchars($canonical, ENT_QUOTES) ?>'
  data-user-sig="<?= $signature ?>"
  data-title="City Pharmacy"
  async></script>
```

Set `CITYAGENT_EMBED_SECRET` in your server environment (see the **SECRET**
field → *"set CITYAGENT_EMBED_SECRET (HMAC mode)"* in the cockpit). Other
languages are in the [`examples/`](../examples/) folder (Python, Node, bash).

---

## Step 5 — Allow your website (required, security)

For safety, a widget only answers on **websites the admin has allow-listed**.
Until your site is added, the chat shows:
*"This chat isn't enabled for &lt;your site&gt; yet."*

The admin opens the widget's **Access** tab and clicks **Allow** next to your
site under *Sites trying to embed* (it appears the first time you load the
widget). No redeploy — it works instantly.

![Access tab — allow your site](images/embed/06-access-allow.png)

> Add every domain you embed on, including `https://staging.example.com` and any
> subdomains. Wildcards like `https://*.example.com` are supported.

---

## Security model (read this)

| Value | Lives where | Why it's safe |
|-------|-------------|---------------|
| **EMBED ID** + **PUBLIC KEY** | in the page (browser) | public by design; can only reach **your** widget, scoped + rate-limited |
| **Origin allow-list** | server | only allow-listed sites get answers (blocks key copy-paste abuse) |
| **SECRET** | your server env only | signs the logged-in user; **never** sent to the browser |
| Customer mode | server | hides other stores' quantities and prices automatically |

The browser widget always calls with `credentials: 'omit'` — no cookies, no
cross-site auth leakage.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No robot appears | site not allow-listed, or wrong domain in `src` | admin **Allow**s your origin; check `YOUR-AGENT-DOMAIN` is correct + HTTPS |
| *"This chat isn't enabled for your site yet"* | origin not allow-listed | admin → Access → **Allow** |
| Robot shows but won't open | old cached widget | hard-refresh (Cmd/Ctrl + Shift + R); the snippet auto-appends `?v=` to bust cache after updates |
| `403 origin_denied` in console | request Origin stripped by a proxy, or not allow-listed | ensure your reverse proxy forwards the `Origin` header (see `docs/INSTALL-NGINX.md`) |
| Numbers show as `[banded]` | this key is customer-scoped | by design — quantities/prices hidden for non-owning stores |
| Mixed-content / blocked on HTTPS | widget `src` is `http://` | use the `https://` domain (the cockpit emits https when behind a proxy) |

---

## Reference

- **One-page handoff for embedding teams:** [`../EMBED_DEV_HANDOFF.md`](../EMBED_DEV_HANDOFF.md)
- **Full snippet variants (React, Next.js, WordPress):** [`EMBED.md`](EMBED.md)
- **Drop-in SDKs (PHP, Python, Node, bash):** [`../examples/`](../examples/) (or **download** from the cockpit)
- **Reverse-proxy / Origin forwarding:** [`INSTALL-NGINX.md`](INSTALL-NGINX.md)

---

*Widget served from `/api/embed/widget.js`. Questions shown in the chat are
learned from real usage and rotate each visit. The assistant can make
mistakes — verify critical information.*
