# RLAI-DASH — Operations Manual

## For: DevOps / Infrastructure Engineer

---

## 1. Fix "Too Many Clients" Error (URGENT)

```bash
ssh root@your-server-ip
cd /opt/rlai-dash
git pull
docker compose down
docker compose up -d --build
```

Verify:
```bash
# Wait 10 seconds for startup
sleep 10

# Check DB connections (should show 500)
docker compose exec dash-db psql -U ai -c "SHOW max_connections;"

# Check app health
curl http://localhost:8001/health

# Check active connections
docker compose exec dash-db psql -U ai -c "SELECT count(*) FROM pg_stat_activity;"
```

---

## 2. Common Issues & Fixes

### App won't start
```bash
docker compose logs dash-api | tail -20
```
| Error | Fix |
|-------|-----|
| `OPENROUTER_API_KEY is required` | Set API key in `.env` |
| `too many clients already` | `docker compose down && docker compose up -d` |
| `connection refused` | DB not ready — wait 10s, retry |

### DB won't connect
```bash
docker compose logs dash-db | tail -20
```
| Error | Fix |
|-------|-----|
| `FATAL: password authentication failed` | Check `DB_PASS` matches in `.env` |
| `database "ai" does not exist` | `docker compose down -v && docker compose up -d` (WARNING: deletes data) |

### Slow performance
```bash
# Check connection usage
docker compose exec dash-db psql -U ai -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# Check disk usage
docker system df

# Check memory
docker stats --no-stream
```

### Training stuck
```bash
# Check training logs
docker compose logs dash-api | grep "training\|TRAIN" | tail -20

# Restart app (keeps data)
docker compose restart dash-api
```

---

## 3. Routine Maintenance

### Daily
```bash
# Check health
curl http://localhost:8001/health

# Check disk space
df -h
```

### Weekly
```bash
# Prune old Docker images
docker image prune -f

# Check DB size
docker compose exec dash-db psql -U ai -c "SELECT pg_size_pretty(pg_database_size('ai'));"

# Check active connections
docker compose exec dash-db psql -U ai -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"
```

### Monthly
```bash
# Backup database
docker compose exec dash-db pg_dump -U ai ai > backup_$(date +%Y%m%d).sql

# Update code
cd /opt/rlai-dash
git pull
docker compose up -d --build
```

---

## 4. Backup & Restore

### Backup (run weekly)
```bash
# Database
docker compose exec dash-db pg_dump -U ai ai | gzip > /opt/backups/db_$(date +%Y%m%d).sql.gz

# Knowledge files
tar czf /opt/backups/knowledge_$(date +%Y%m%d).tar.gz /var/lib/docker/volumes/rlai-dash_knowledge_data/

# Keep last 7 backups
ls -t /opt/backups/db_*.sql.gz | tail -n +8 | xargs rm -f
ls -t /opt/backups/knowledge_*.tar.gz | tail -n +8 | xargs rm -f
```

### Restore
```bash
# Stop app
docker compose stop dash-api

# Restore database
gunzip < /opt/backups/db_20260421.sql.gz | docker compose exec -T dash-db psql -U ai ai

# Restart
docker compose start dash-api
```

### Auto-backup cron (recommended)
```bash
# Add to crontab
crontab -e

# Add this line (backup every day at 2 AM)
0 2 * * * cd /opt/rlai-dash && docker compose exec -T dash-db pg_dump -U ai ai | gzip > /opt/backups/db_$(date +\%Y\%m\%d).sql.gz
```

---

## 5. Scaling Guide

### Current: 40 users × 10 projects
```
PostgreSQL: max_connections=500
Pool per project: 2 connections
Total capacity: ~200 concurrent users
VPS: 4GB RAM minimum
```

### For 100+ users
Add PgBouncer (connection pooler):
```yaml
# Add to compose.yaml
  pgbouncer:
    image: edoburu/pgbouncer
    environment:
      DATABASE_URL: postgres://ai:${DB_PASS:-ai}@dash-db:5432/ai
      POOL_MODE: transaction
      MAX_CLIENT_CONN: 1000
      DEFAULT_POOL_SIZE: 50
    depends_on:
      - dash-db
```
Then change `DB_HOST=pgbouncer` in the app environment.

### For 500+ users
- Use managed PostgreSQL (AWS RDS, Google Cloud SQL)
- Add Redis for token cache + rate limiting
- Increase workers: `WORKERS=4`
- Add load balancer (multiple app instances)

---

## 6. Monitoring

### Check if app is healthy
```bash
curl -s http://localhost:8001/health | python3 -m json.tool
```

### Check logs
```bash
# App logs
docker compose logs dash-api --tail 50 -f

# DB logs
docker compose logs dash-db --tail 50 -f

# All logs
docker compose logs --tail 100 -f
```

### Check resource usage
```bash
docker stats --no-stream
```

Expected:
| Container | CPU | Memory |
|-----------|-----|--------|
| dash-api | 5-20% | 200-500MB |
| dash-db | 2-10% | 300-800MB |
| dash-caddy | <1% | 20MB |

### Alert if unhealthy
```bash
# Add to crontab (check every 5 minutes)
*/5 * * * * curl -sf http://localhost:8001/health > /dev/null || echo "DASH DOWN" | mail -s "Alert: RLAI-DASH unhealthy" admin@company.com
```

---

## 7. Ports & Network

| Service | Internal Port | External Port | Notes |
|---------|:------------:|:-------------:|-------|
| dash-api | 8000 | 8001 | FastAPI app |
| dash-db | 5432 | — | Not exposed externally |
| caddy | 80, 443 | 80, 443 | HTTPS reverse proxy |

### If ports 80/443 are taken (Nginx already running)
Comment out Caddy in `compose.yaml`, add Nginx proxy:
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
Then: `sudo certbot --nginx -d dash.yourdomain.com`

---

## 8. Environment Variables

| Variable | Required | Default | Notes |
|----------|:--------:|---------|-------|
| `OPENROUTER_API_KEY` | Yes | — | Get from openrouter.ai/keys |
| `DB_PASS` | Yes | `ai` | CHANGE in production |
| `DOMAIN` | Yes | `localhost` | Your domain for SSL |
| `CORS_ORIGINS` | Yes | `*` | Set to `https://your-domain.com` |
| `SUPER_ADMIN` | No | `admin` | Admin username |
| `SUPER_ADMIN_PASS` | No | `admin` | Admin password (first boot only) |
| `WORKERS` | No | `1` | Uvicorn workers |
| `DB_USER` | No | `ai` | PostgreSQL user |
| `DB_DATABASE` | No | `ai` | PostgreSQL database |

---

## 9. Emergency Procedures

### App completely down
```bash
docker compose down
docker compose up -d
sleep 10
curl http://localhost:8001/health
```

### Database corrupted
```bash
# Stop everything
docker compose down

# Restore from backup
docker compose up -d dash-db
sleep 5
gunzip < /opt/backups/db_latest.sql.gz | docker compose exec -T dash-db psql -U ai ai

# Start app
docker compose up -d
```

### Full reset (DELETES ALL DATA)
```bash
docker compose down -v
docker compose up -d --build
```

### Server running out of disk
```bash
# Check what's using space
docker system df
du -sh /var/lib/docker/volumes/*

# Clean unused images
docker system prune -af

# Clean old logs
truncate -s 0 /var/lib/docker/containers/*/*-json.log
```
