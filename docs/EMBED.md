# Embedding the CityAgent chat on your website

Add an AI chat bubble to any website by pasting one `<script>` tag. The bubble
loads in the bottom-right corner; visitors click it to chat. No build step, no
framework required.

---

## 1. Get your snippet

Ask your CityAgent administrator for:

- **Embed ID** — looks like `emb_xxxxxxxx`
- **Public key** — looks like `pub_xxxxxxxx`
- The **site address(es)** you'll embed on (e.g. `https://shop.example.com`)

The admin allow-lists your site address in the dashboard
(**Embedding → your widget → Access**). If your site isn't allow-listed, the
chat shows: *"This chat isn't enabled for &lt;your site&gt; yet."* — the admin
just clicks **Allow** next to your site under *Sites trying to embed*.

---

## 2. Plain HTML (any website)

Paste this just before the closing `</body>` tag:

```html
<script
  src="https://YOUR-AGENT-DOMAIN/api/embed/widget.js"
  data-embed-id="emb_xxxxxxxx"
  data-key="pub_xxxxxxxx"
  data-title="Support"
  data-greeting="Hi! How can I help?"
  async></script>
```

Replace `YOUR-AGENT-DOMAIN`, `data-embed-id`, and `data-key` with your values.
That's it.

### Optional attributes
| Attribute | What it does | Example |
|-----------|--------------|---------|
| `data-position` | corner | `bottom-left` |
| `data-theme` | look | `dark`, `consumer`, `auto` |
| `data-title` | header title | `Pharmacy Help` |
| `data-greeting` | first message | `Ask about stock…` |
| `data-accent` | brand color | `#0066ff` |
| `data-logo` | logo image URL | `https://…/logo.png` |
| `data-stream` | live typing (default on) | `false` to disable |

---

## 3. React / Next.js

Load the script once on mount:

```jsx
import { useEffect } from "react";

export default function ChatWidget() {
  useEffect(() => {
    const s = document.createElement("script");
    s.src = "https://YOUR-AGENT-DOMAIN/api/embed/widget.js";
    s.async = true;
    s.dataset.embedId = "emb_xxxxxxxx";
    s.dataset.key = "pub_xxxxxxxx";
    s.dataset.title = "Support";
    document.body.appendChild(s);
    return () => { s.remove(); };
  }, []);
  return null;
}
```

Drop `<ChatWidget />` in your root layout. In Next.js App Router, mark the file
`"use client"`.

---

## 4. WordPress

**Easiest:** install a "header/footer scripts" plugin (e.g. *WPCode* or
*Insert Headers and Footers*), then paste the **Plain HTML** snippet from
section 2 into the **Footer** box. Save.

**Theme editor:** Appearance → Theme File Editor → `footer.php`, paste the
snippet just before `</body>`.

---

## 5. Signed users (optional, more secure)

If the admin set your key to require a signed user (so visitors can't share the
key), your server signs a small payload with the **secret key** (never put the
secret in the browser):

```php
<?php
$payload = json_encode(["id" => "user-123", "role" => "staff", "ts" => time()]);
$sig = hash_hmac("sha256", $payload, "YOUR_SECRET_KEY"); // server-side only
?>
<script
  src="https://YOUR-AGENT-DOMAIN/api/embed/widget.js"
  data-embed-id="emb_xxxxxxxx"
  data-key="pub_xxxxxxxx"
  data-user='<?= $payload ?>'
  data-user-sig="<?= $sig ?>"
  async></script>
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| *"This chat isn't enabled for … yet"* (403) | Your site address isn't allow-listed | Admin: Embedding → widget → **Access** → **Allow** your site (it appears automatically after one visit). Or add `https://*.yourdomain.com`. |
| Bubble doesn't appear | Wrong `data-embed-id` / `data-key`, or script blocked | Check the browser console; verify the two IDs. |
| Works on `www.` but not bare domain (or vice-versa) | Each is a separate origin | Allow both, or ask admin for **Subdomains** match mode. |
| Updated widget not showing | Browser cache | The snippet auto-appends `?v=…`; hard-refresh once after a new deploy. |

Admin reference for allow-listing + match modes: dashboard **Embedding →
Access** tab.
