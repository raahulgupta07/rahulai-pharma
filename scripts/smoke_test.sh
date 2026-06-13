#!/usr/bin/env bash
# Smoke test — "does each subsystem respond at all" check after deploy.
# Reads creds from .env (never prints secret values). Run from repo root:
#   bash scripts/smoke_test.sh
# Override target:  BASE=https://pharma.yourdomain.com bash scripts/smoke_test.sh
set -uo pipefail

BASE="${BASE:-http://127.0.0.1:8011}"
SLUG="${SLUG:-citypharma}"
ENV_FILE="${ENV_FILE:-.env}"
PASS=0 FAIL=0
ok(){   printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad(){  printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
hdr(){  printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# --- load creds from .env (no echo) ---
# SUPER_ADMIN = the admin USERNAME (not an email); SUPER_ADMIN_PASS = its password.
ADMIN_USER="$(grep -E '^SUPER_ADMIN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"')"
ADMIN_PASS="$(grep -E '^SUPER_ADMIN_PASS=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"')"
DB_USER="$(grep -E '^DB_USER='  "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"')"
DB_PASS="$(grep -E '^DB_PASS=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"')"
DB_NAME="$(grep -E '^DB_DATABASE=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"')"
ADMIN_USER="${ADMIN_USER:-demo}"
DB_USER="${DB_USER:-ai}"; DB_NAME="${DB_NAME:-postgres}"
JQ(){ command -v jq >/dev/null && jq "$@" || cat; }

# ============================================================
hdr "1. HEALTH"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health")
[ "$code" = 200 ] && ok "/health 200" || bad "/health = $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/health")
[ "$code" = 200 ] && ok "/api/health 200" || bad "/api/health = $code"

# ============================================================
hdr "2. AUTH (login → token)"
if [ -z "$ADMIN_PASS" ]; then
  bad "SUPER_ADMIN_PASS not in $ENV_FILE — skip auth-gated tests"; TOKEN=""
else
  TOKEN=$(curl -s "$BASE/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" | JQ -r '.token // empty')
  [ -n "$TOKEN" ] && ok "login → token" || bad "login failed (check SUPER_ADMIN_USER/PASS)"
fi
AUTH=(); [ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")
# wrong password must NOT issue a token
nope=$(curl -s "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"definitely-wrong-xyz\"}" | JQ -r '.token // empty')
[ -z "$nope" ] && ok "bad password rejected" || bad "bad password ISSUED A TOKEN"

# ============================================================
hdr "3. CONNECTOR (server-side DB test against cp-db)"
if [ -n "$TOKEN" ]; then
  ch=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/telemetry-admin/connector-health" "${AUTH[@]}")
  [ "$ch" = 200 ] && ok "connector-health 200" || bad "connector-health = $ch"
fi
if [ -n "$TOKEN" ] && [ -n "$DB_PASS" ]; then
  curl -s "$BASE/api/connectors/test" "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"db_type\":\"postgresql\",\"host\":\"cp-db\",\"port\":5432,\"username\":\"$DB_USER\",\"password\":\"$DB_PASS\",\"database\":\"$DB_NAME\"}" \
    -o /tmp/sm_conn 2>/dev/null
  grep -q '"success":true\|"success": true' /tmp/sm_conn \
    && ok "connector /test → success ($(JQ -r '.count' </tmp/sm_conn 2>/dev/null) tables)" \
    || { bad "connector /test failed"; cat /tmp/sm_conn; }
else
  bad "skip connector test (need TOKEN + DB_PASS)"
fi

# ============================================================
hdr "4. FILE UPLOAD"
if [ -n "$TOKEN" ]; then
  # 4a. DOC upload (knowledge base: txt/md/pdf/docx/png... via /api/upload-doc, SSE)
  printf '# Smoke doc\nParacetamol 500mg, qty 10.\n' > /tmp/sm_smoke.md
  curl -s "$BASE/api/upload-doc" "${AUTH[@]}" -F "file=@/tmp/sm_smoke.md" -F "project=$SLUG" \
    -o /tmp/sm_up 2>/dev/null
  grep -qiE 'done|complete|ingested|success|page|chunk' /tmp/sm_up \
    && ok "upload-doc accepted .md" || { bad "upload-doc"; head -c 300 /tmp/sm_up; echo; }
  # 4b. TABULAR upload (csv/xlsx → table via /api/upload)
  printf 'sku,name,qty\nA1,Paracetamol,10\nB2,Amoxicillin,5\n' > /tmp/sm_smoke.csv
  curl -s "$BASE/api/upload" "${AUTH[@]}" -F "file=@/tmp/sm_smoke.csv" \
    -F "project=$SLUG" -F "table_name=smoke_test_tbl" -F "action=auto" -o /tmp/sm_tab 2>/dev/null
  grep -qiE 'table|rows|success|loaded|smoke_test_tbl' /tmp/sm_tab \
    && ok "tabular /api/upload accepted CSV" || { bad "tabular upload"; head -c 300 /tmp/sm_tab; echo; }
  # 4c. unsupported ext rejected (security: no arbitrary file persisted)
  printf '<x/>' > /tmp/sm_bad.svg
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/upload-doc" "${AUTH[@]}" \
    -F "file=@/tmp/sm_bad.svg" -F "project=$SLUG" 2>/dev/null)
  [ "$code" = 400 ] && ok ".svg rejected (400)" || bad ".svg upload returned $code (expect 400)"
  rm -f /tmp/sm_smoke.csv /tmp/sm_bad.svg /tmp/sm_tab 2>/dev/null
else
  bad "skip upload (no token)"
fi

# ============================================================
hdr "5. CHAT (agent end-to-end)"
if [ -n "$TOKEN" ]; then
  curl -s -N --max-time 90 "$BASE/api/projects/$SLUG/chat" "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    -d '{"message":"how many products do we have? one number.","session_id":"smoke"}' \
    -o /tmp/sm_chat 2>/dev/null
  [ -s /tmp/sm_chat ] && ok "chat streamed $(wc -c </tmp/sm_chat | tr -d ' ') bytes" || bad "chat empty"
else
  bad "skip chat (no token)"
fi

# ============================================================
hdr "RESULT"
printf '  \033[32m%d pass\033[0m  \033[31m%d fail\033[0m\n' "$PASS" "$FAIL"
rm -f /tmp/sm_smoke.md /tmp/sm_conn /tmp/sm_up /tmp/sm_chat 2>/dev/null
[ "$FAIL" = 0 ]
