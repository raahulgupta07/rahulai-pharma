# CityAgent Pharma — Integration Examples

Drop-in, zero-dependency client code so any developer can wire the agent into
their app in minutes. Pick your stack.

| File | Stack | Use it for |
|------|-------|-----------|
| `widget-embed.php` | PHP page | **Fastest.** Floating chat bubble in a logged-in PHP app, per-user store scoping (HMAC). One `<script>` tag. |
| `CityAgentClient.php` | PHP class | Your own UI / server-to-server. Blocking + streaming + HMAC signing. No Composer. |
| `rest_client.py` | Python 3.8+ | Same, stdlib only (no pip). |
| `rest_client.js` | Node 18+ | Same, zero deps (global `fetch`/`crypto`). |
| `quickstart.sh` | bash + curl | 10-second end-to-end smoke test (public mode). |

## 3 ways to integrate (pick one)

**A. Drop-in bubble (anonymous)** — one script tag, no backend code. Tier-3 global/catalog scope.
See Path A in [`../EMBED_DEV_HANDOFF.md`](../EMBED_DEV_HANDOFF.md).

**B. Drop-in bubble (user-scoped)** — `widget-embed.php`. Your server signs the
user payload with `secret_key`; the agent masks other stores' qty/price. **This is
the common pharma case.**

**C. Custom UI / server-to-server** — use a REST client (`CityAgentClient.php` /
`rest_client.py` / `rest_client.js`). You own the chat box.

## Before anything works (admin, one-time)

In **Dash → Integrations → Widgets**:
1. Set widget **status = live** (default `draft` → blocked).
2. Add your site **origin(s)** to the allowlist (e.g. `https://yourpharmacy.com`).
3. **Rotate secret** to get `secret_key` (shown once) — needed for HMAC mode (B/C with user).

Until 1+2 done, every call returns `403 origin not allowed`.

## Provisioned values (dev)

```
BASE_URL    http://localhost:8011        # replace with your prod URL
embed_id    emb_rGd8VWW8DloS6WNNssvenA
public_key  pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT   # safe in browser
secret_key  <get via Rotate secret>                # server-side ONLY, never ship to browser
```

Put `secret_key` in env, never in code:
```bash
export CITYAGENT_EMBED_SECRET="secret_xxx"
```

## Run the examples

```bash
# bash smoke test (public mode)
./quickstart.sh

# Python
CITYAGENT_EMBED_SECRET=secret_xxx python3 rest_client.py

# Node
CITYAGENT_EMBED_SECRET=secret_xxx node rest_client.js

# PHP page — serve under your app, then open in a browser
php -S localhost:9000 widget-embed.php
```

## Auth modes at a glance

| Mode | Pass a `user`? | Scope | Needs `secret_key`? |
|------|----------------|-------|---------------------|
| public | no | tier-3 global / catalog (no qty/price) | no |
| hmac | yes (server-signed) | tier-1 own store full, tier-2 others masked | yes |

Canonical user JSON (must byte-match server or signature fails): **sorted keys, no
spaces**. All three SDK clients have a `canonical()` + `sign()` helper that does this
for you.

## Errors

| Response | Fix |
|----------|-----|
| `403 origin not allowed` | Add your origin to the allowlist; send the `Origin` header |
| `403 embed disabled` | Status is `draft` → flip to `live` |
| `403 invalid user signature` | Canonical JSON must be sorted-keys + no-spaces, same `secret_key` |
| `429` | Rate limited per embed/min — back off or raise the limit |
| `401` session expired | Token TTL ~15 min; the SDK clients auto-refresh — just call again |

## Endpoints used

| Path | What |
|------|------|
| `GET  /api/embed/widget.js` | the bubble script |
| `POST /api/embed/session/create` | mint short-lived session token |
| `POST /api/embed/chat` | blocking answer |
| `POST /api/embed/chat/stream` | SSE: `token` / `step` / `done` events |
| `GET  /api/embed/docs` | live HTML docs (no auth) |

OpenAI-compatible alternative (Bearer-key, `/v1/chat/completions`): see
[`project_citypharma_apigw`] and `GET /api/v1/docs`.
