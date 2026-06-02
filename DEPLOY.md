# Dash — Cloud Deployment Guide

## Quick Start (5 minutes)

```bash
# 1. Clone and enter
git clone <repo-url>
cd dash

# 2. Create env file
cp .env.example .env

# 3. Edit .env — set these 4 values:
OPENROUTER_API_KEY=sk-or-v1-xxxx    # Get from https://openrouter.ai/keys
DB_PASS=your-strong-password-here    # NOT "ai" in production
DOMAIN=dash.yourdomain.com           # Your actual domain
CORS_ORIGINS=https://dash.yourdomain.com

# 4. Deploy
docker compose -f compose.yaml up -d --build

# 5. Login
# Open https://dash.yourdomain.com
# Username: admin
# Password: admin
# CHANGE PASSWORD IMMEDIATELY after first login
```

## Common Mistakes

### DB_HOST — DO NOT change it
`compose.yaml` sets `DB_HOST=dash-db` automatically.
If you set `DB_HOST=localhost` in `.env`, the app can't reach the database.
**Leave DB_HOST out of your .env file.**

### DB_PORT — Leave as 5432
Inside Docker, PostgreSQL runs on port `5432`.
The host mapping (`5433:5432`) is only for local development.
**Leave DB_PORT out of your .env file or set to 5432.**

### docker compose down -v — DESTROYS ALL DATA
The `-v` flag deletes volumes (database + training data).
- Safe: `docker compose down` (keeps data)
- Safe: `docker compose restart`
- DANGEROUS: `docker compose down -v` (deletes everything)

### Frontend changes
If you edit frontend code:
```bash
cd frontend && npm install && npm run build && cd ..
docker compose -f compose.yaml up -d --build
```

## OpenRouter Setup

1. Go to https://openrouter.ai/keys
2. Create API key
3. Add credits ($5 minimum recommended)
4. Models used automatically:
   - **Chat:** `openai/gpt-5.4-mini` (fast, cheap)
   - **Training:** `google/gemini-3.1-flash-lite-preview` (cheapest)
5. No model configuration needed — app handles it

## Architecture

```
                    ┌─────────────┐
    Internet ──────>│    Caddy     │ (auto-SSL, port 443)
                    │  (reverse   │
                    │   proxy)    │
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │   Dash API  │ (FastAPI, port 8000)
                    │  (2 workers)│
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  PostgreSQL │ (port 5432)
                    │  + PgVector │
                    └─────────────┘
```

## If ports 80/443 are already taken (Nginx/Apache)

Comment out Caddy in `compose.yaml`:
```yaml
  # caddy:
  #   ... (comment out entire caddy section)
```

Add Nginx proxy:
```bash
sudo nano /etc/nginx/sites-available/dash
```
```nginx
server {
    listen 80;
    server_name dash.yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/dash /etc/nginx/sites-enabled/
sudo certbot --nginx -d dash.yourdomain.com
sudo nginx -t && sudo systemctl reload nginx
docker compose up -d --build
```

## Safe Update (code changes, data preserved)

```bash
# 1. ALWAYS backup first
docker exec dash-db pg_dump -U ai -d ai > backup_$(date +%Y%m%d).sql

# 2. Pull latest code
git pull origin main

# 3. Build ONLY the API container (DB untouched)
docker compose build dash-api

# 4. Restart ONLY the API
docker compose up -d dash-api

# 5. Verify
curl http://localhost:8001/health
```

## Backup & Restore

```bash
# Backup database
docker exec dash-db pg_dump -U ai -d ai > backup_$(date +%Y%m%d).sql

# Backup knowledge files
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/knowledge_$(date +%Y%m%d).tar.gz /data

# Restore database
cat backup_YYYYMMDD.sql | docker exec -i dash-db psql -U ai -d ai

# Restore knowledge
docker run --rm -v dash_knowledge_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/knowledge_YYYYMMDD.tar.gz -C /
```

## Rollback

```bash
# Rollback to any previous commit
git log --oneline -10             # find commit hash
git checkout <commit-hash>
docker compose build dash-api
docker compose up -d dash-api

# Rollback to pre-upload-agent backup
git checkout backup/pre-upload-agent
docker compose build dash-api
docker compose up -d dash-api
```

## What Survives Rebuild

| Data | Location | `down && up` | `down -v` |
|------|----------|:---:|:---:|
| Users, projects, chat | `pgdata` volume | ✅ | ❌ DELETED |
| Uploaded tables | `pgdata` volume | ✅ | ❌ DELETED |
| Training, memories | `pgdata` volume | ✅ | ❌ DELETED |
| Knowledge files | `knowledge_data` volume | ✅ | ❌ DELETED |
| SSL certificates | `caddy_data` volume | ✅ | ❌ DELETED |
| .env config | Host filesystem | ✅ | ✅ |
| Application code | Docker image | 🔄 Rebuilt | 🔄 Rebuilt |

## Pre-Deploy Checklist

- [ ] Backup database: `pg_dump`
- [ ] Backup knowledge: `tar czf`
- [ ] Check health: `curl /health`
- [ ] Pull code: `git pull`
- [ ] Build API only: `docker compose build dash-api`
- [ ] Restart API only: `docker compose up -d dash-api`
- [ ] Verify health: `curl /health`
- [ ] Login and check data intact

## Health Check

```bash
curl https://your-domain.com/health
# Should return: {"status":"ok","db":"connected"}
```

## Troubleshooting

### App won't start
```bash
docker compose -f compose.yaml logs dash-api | tail -20
```
Common causes:
- Missing `OPENROUTER_API_KEY` → app refuses to start
- Wrong `DB_PASS` → connection refused
- Port 443/80 already in use → Caddy fails

### Database won't connect
```bash
docker compose -f compose.yaml logs dash-db | tail -20
```
- Check `DB_PASS` matches between app and db
- Never set `DB_HOST=localhost` — it must be `dash-db`

### Training fails
- Check OpenRouter credits: https://openrouter.ai/credits
- Check API key is valid
- Check logs: `docker compose exec dash-api cat /tmp/training.log`

### Rebuild from scratch
```bash
docker compose -f compose.yaml down    # keeps data
docker compose -f compose.yaml up -d --build
```

### Full reset (DELETES ALL DATA)
```bash
docker compose -f compose.yaml down -v
docker compose -f compose.yaml up -d --build
```
