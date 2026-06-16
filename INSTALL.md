# Install CityAgent Pharma

Turnkey install. The box configures itself — you only supply a model API key
(and a domain, for production HTTPS).

## Quickstart (3 steps)

```bash
git clone <repo-url> cityagent && cd cityagent
./install.sh
# open http://127.0.0.1:8011  — log in with the admin password install.sh printed
```

`./install.sh` does everything:
- checks Docker, ports, RAM
- generates `.env` with strong random secrets (DB, SFTP, admin, encryption)
- prompts for your OpenRouter model key (or skip and add later)
- starts the stack and waits until healthy
- prints your **admin login** and the URL

Re-running `./install.sh` is safe — it never overwrites an existing `.env`.

## Production (public HTTPS)

1. Before installing, decide your domain and put it in `.env`:
   ```ini
   DOMAIN=agent.yourcompany.com
   ```
   (or edit `.env` after `./install.sh` and `docker compose up -d` again.)
   `PUBLIC_URL` auto-derives to `https://<DOMAIN>` — embed snippets + CORS follow.
2. Front the stack with nginx + free HTTPS — see **[docs/INSTALL-NGINX.md](docs/INSTALL-NGINX.md)**.
3. Hand embedding teams a snippet — see **[docs/EMBED.md](docs/EMBED.md)**.

## Use the published image (skip local build)

If a CI release has pushed the image to GHCR, set in `.env`:
```ini
IMAGE_NAME=ghcr.io/<owner>/citypharma
IMAGE_TAG=latest
```
then:
```bash
docker compose -f compose.yaml pull
docker compose -f compose.yaml up -d
```
(See `.github/workflows/release.yml` for how the image is built/published.)

## Manual config

Skip the installer and edit `.env` yourself:
```bash
cp .env.example .env
# set at minimum: OPENROUTER_API_KEY, DB_PASS, SUPER_ADMIN_PASS, SFTPGO_ADMIN_PASS
docker compose -f compose.yaml up -d
curl -s http://127.0.0.1:8011/api/health    # {"status":"ok",...}
```

## Common commands

| Command | What |
|---------|------|
| `./install.sh` | first install / safe re-run |
| `docker compose -f compose.yaml logs -f dash-api` | app logs |
| `docker compose -f compose.yaml up -d --force-recreate dash-api` | restart app |
| `docker compose -f compose.yaml down` | stop all |
| `make health` | health check |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| install.sh: "docker not found" | Install Docker Desktop / Engine first. |
| health never 200 | `docker compose -f compose.yaml logs -f dash-api` — usually a missing model key or a port clash. |
| chat says no answer | `OPENROUTER_API_KEY` not set in `.env`; set it and restart the app. |
| embed shows 403 | Allow the site: dashboard → Embedding → Access (see docs/EMBED.md). |
