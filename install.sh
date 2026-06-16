#!/usr/bin/env bash
#
# install.sh — one-command turnkey install for CityAgent Pharma.
#
#   git clone <repo> && cd <repo>
#   ./install.sh
#
# Does: preflight checks -> generate .env (random secrets) -> start stack ->
# wait for health -> print the URL + admin login. Re-running is safe (it won't
# overwrite an existing .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. Preflight ────────────────────────────────────────────────────────────
say "1/5  Preflight"

command -v docker >/dev/null 2>&1 || die "docker not found — install Docker first."
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    die "docker compose plugin not found."
fi
ok "docker + compose present"

command -v openssl >/dev/null 2>&1 || die "openssl not found (needed to generate secrets)."
ok "openssl present"

# Port check (warn only — a fronting nginx may legitimately own 80/443).
check_port() {
    local p="$1" label="$2"
    if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
        warn "port $p ($label) already in use — make sure that's intended."
    fi
}
check_port 8011 "app"
check_port 2222 "sftp"
ok "port check done"

# RAM check (warn under ~4GB).
if command -v free >/dev/null 2>&1; then
    MB=$(free -m | awk '/^Mem:/{print $2}')
    [ "${MB:-9999}" -lt 4000 ] && warn "only ${MB}MB RAM — 4GB+ recommended." || ok "RAM ok (${MB}MB)"
fi

# ── 2. Generate .env ────────────────────────────────────────────────────────
say "2/5  Configuration"
bash "$ROOT/scripts/bootstrap_env.sh"

# ── 3. Check model key ──────────────────────────────────────────────────────
say "3/5  Model API key"
KEY="$(grep -E '^OPENROUTER_API_KEY=' .env | head -1 | cut -d= -f2- || true)"
case "$KEY" in
    ""|sk-or-v1-your-key-here)
        warn "OPENROUTER_API_KEY not set in .env."
        warn "The stack will start, but chat answers need a key."
        printf '  Paste your OpenRouter key now (or press Enter to skip): '
        read -r INKEY || true
        if [ -n "${INKEY:-}" ]; then
            sed -i.bak -E "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${INKEY}|" .env && rm -f .env.bak
            ok "key saved"
        fi
        ;;
    *) ok "model key present" ;;
esac

# ── 4. Start the stack ──────────────────────────────────────────────────────
say "4/5  Starting (first boot pulls images + runs migrations, ~1-2 min)"
$DC -f compose.yaml up -d
ok "containers started"

# ── 5. Wait for health ──────────────────────────────────────────────────────
say "5/5  Waiting for health"
HEALTHY=0
for i in $(seq 1 90); do
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/api/health 2>/dev/null || echo 000)"
    if [ "$code" = "200" ]; then HEALTHY=1; break; fi
    sleep 2
done

ADMIN_USER="$(grep -E '^SUPER_ADMIN=' .env | head -1 | cut -d= -f2-)"; ADMIN_USER="${ADMIN_USER:-admin}"
DOMAIN_VAL="$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2-)"; DOMAIN_VAL="${DOMAIN_VAL:-localhost}"

if [ "$HEALTHY" = "1" ]; then
    ok "healthy"
    say "Done."
    echo "  App:   http://127.0.0.1:8011   (local)"
    if [ "$DOMAIN_VAL" != "localhost" ]; then
        echo "  Public: front this with nginx — see docs/INSTALL-NGINX.md  (DOMAIN=$DOMAIN_VAL)"
    fi
    echo "  Admin user: $ADMIN_USER   (password was printed above by bootstrap)"
    echo "  Embedding:  dashboard -> Embedding -> Access  (see docs/EMBED.md)"
else
    warn "health endpoint not 200 yet. Check logs:  $DC -f compose.yaml logs -f dash-api"
fi
