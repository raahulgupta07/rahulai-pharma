#!/usr/bin/env bash
#
# bootstrap_env.sh — zero-config first boot.
#
# If .env does not exist, create it from .env.example and fill the
# boot-required secrets with strong random values so the stack starts with no
# manual editing. Existing .env is NEVER touched.
#
# Generated secrets:  DB_PASS, SFTPGO_ADMIN_PASS, SUPER_ADMIN_PASS,
#                     CONNECTION_ENCRYPTION_KEY
# Left for you:       OPENROUTER_API_KEY (your model key — cannot be generated)
#
# Usage:  ./scripts/bootstrap_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE="$ROOT/.env.example"

if [ -f "$ENV_FILE" ]; then
    echo "[bootstrap] .env already exists — leaving it untouched."
    exit 0
fi

if [ ! -f "$EXAMPLE" ]; then
    echo "[bootstrap] ERROR: .env.example not found at $EXAMPLE" >&2
    exit 1
fi

# URL-safe random secret (no /+= so it's shell/URL/DSN-safe).
gen() { openssl rand -base64 24 | tr -d '/+=' | cut -c1-32; }

DB_PASS_VAL="$(gen)"
SFTP_PASS_VAL="$(gen)"
ADMIN_PASS_VAL="$(gen)"
ENC_KEY_VAL="$(gen)"

cp "$EXAMPLE" "$ENV_FILE"

# Replace KEY=... lines in place. Portable sed (works on GNU + BSD/macOS).
set_kv() {
    local key="$1" val="$2"
    # Escape sed-special chars in the value (we control the charset, but be safe).
    local esc; esc="$(printf '%s' "$val" | sed -e 's/[\/&]/\\&/g')"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i.bak -E "s/^${key}=.*/${key}=${esc}/" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
}

set_kv DB_PASS "$DB_PASS_VAL"
set_kv SFTPGO_ADMIN_PASS "$SFTP_PASS_VAL"
set_kv SUPER_ADMIN_PASS "$ADMIN_PASS_VAL"
set_kv CONNECTION_ENCRYPTION_KEY "$ENC_KEY_VAL"
rm -f "$ENV_FILE.bak"

# Read back the admin username default (don't assume).
ADMIN_USER="$(grep -E '^SUPER_ADMIN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
ADMIN_USER="${ADMIN_USER:-admin}"

echo ""
echo "[bootstrap] Created .env with fresh random secrets."
echo "============================================================"
echo "  SUPER ADMIN LOGIN (save this — shown once):"
echo "     user:     ${ADMIN_USER}"
echo "     password: ${ADMIN_PASS_VAL}"
echo "============================================================"
echo ""
echo "  STILL TO SET in .env before first chat works:"
echo "    OPENROUTER_API_KEY   (your model API key)"
echo "    DOMAIN               (your public domain; leave 'localhost' for local)"
echo ""
