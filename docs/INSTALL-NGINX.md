# Production install — nginx + HTTPS

This is the recommended production setup: the Docker stack runs the app on
`127.0.0.1:8011`, and **nginx** on the host terminates TLS and proxies to it.
(The old bundled Caddy is now optional — see the end.)

---

## 1. Set your domain

In `.env`:

```ini
DOMAIN=agent.yourcompany.com
# PUBLIC_URL is auto-derived as https://<DOMAIN> — leave blank unless overriding.
# CORS_ORIGINS optional; PUBLIC_URL covers the dashboard origin automatically.
```

Point an A record for `agent.yourcompany.com` at the server's public IP.

## 2. Start the stack

```bash
docker compose -f compose.yaml up -d
# wait for health:
curl -s http://127.0.0.1:8011/api/health   # -> {"status":"ok",...}
```

## 3. Install nginx config

```bash
sudo cp deploy/nginx/cityagent.conf /etc/nginx/conf.d/cityagent.conf
sudo sed -i 's/agent.yourcompany.com/YOUR_REAL_DOMAIN/g' /etc/nginx/conf.d/cityagent.conf
sudo nginx -t && sudo systemctl reload nginx
```

This config **preserves the `Origin` header** (`proxy_set_header Origin
$http_origin`). That matters: if Origin is dropped, embedded widgets get a false
*"Origin not in allowlist (403)"* even when the allow-list is correct.

## 4. Get a free HTTPS certificate

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d agent.yourcompany.com
```

certbot fills in the certificate paths and sets up auto-renewal. Done — the site
is live on `https://agent.yourcompany.com`.

## 5. Verify embedding end-to-end

```bash
curl -I https://agent.yourcompany.com/api/embed/widget.js   # 200
```

Then in the dashboard: **Embedding → your widget → Snippet & Deploy** — the
snippet now shows your real `https://` domain. Hand it to the embedding team
(see `docs/EMBED.md`). Add their site under the **Access** tab, or let it appear
under *Sites trying to embed* after their first attempt and click **Allow**.

---

## Firewall

Open **80** and **443** to the world (nginx). Keep **8011** bound to localhost
only (it already is). SFTP, if used, needs **2222** (see `docs/SFTP.md`).

## Want the bundled Caddy instead of nginx?

If you'd rather not run your own nginx, the old bundled Caddy (auto-HTTPS) is
available as an overlay:

```bash
docker compose -f compose.yaml -f compose.caddy.yaml up -d
```

Don't run both — they'd both want ports 80/443.
