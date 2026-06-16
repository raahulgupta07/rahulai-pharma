#!/usr/bin/env bash
# Embed smoke test — proves the embed flow works against any deploy (local or AWS).
# Usage:
#   ./scripts/embed_smoke.sh https://your-aws-domain.com emb_xxx pub_xxxxx https://customer-site.com
#
#   $1 = BASE   : the deploy URL (where CityAgent runs).  e.g. https://agent.acme.com
#   $2 = EMBED  : the embed_id (emb_...) from Settings -> Embed / DB.
#   $3 = PUBKEY : the embed public key (pub_...) from Settings -> Embed.
#   $4 = ORIGIN : the customer site that will host the widget. e.g. https://shop.acme.com
#
# Exit 0 = all green. Any FAIL line = that step would break in a real browser.
set -uo pipefail

BASE="${1:?need BASE url, e.g. https://agent.acme.com}"
EMBED="${2:?need embed_id emb_...}"
PUBKEY="${3:?need embed public key pub_...}"
ORIGIN="${4:?need customer Origin, e.g. https://shop.acme.com}"
BASE="${BASE%/}"

pass(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
FAILED=0

echo "== Embed smoke test =="
echo "BASE=$BASE  ORIGIN=$ORIGIN"
echo

# 1. Is the deploy reachable + healthy?
echo "[1] health"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/health")
[ "$code" = "200" ] && pass "health 200" || fail "health returned $code (deploy down / wrong URL)"

# 2. widget.js served + does the snippet host match BASE (PUBLIC_URL set right)?
echo "[2] widget.js + base url"
wjs=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/embed/widget.js")
[ "$wjs" = "200" ] && pass "widget.js 200" || fail "widget.js $wjs (not deployed)"
# the deploy page that emits the install snippet must contain BASE, not localhost/internal
snip=$(curl -s "$BASE/api/embed/deploy?key=$PUBKEY" 2>/dev/null)
if echo "$snip" | grep -q "$BASE/api/embed/widget.js"; then
  pass "install snippet points at $BASE (PUBLIC_URL correct)"
elif echo "$snip" | grep -qiE "localhost|127\.0\.0\.1|:8000"; then
  fail "snippet points at localhost/internal — set PUBLIC_URL=$BASE and rebuild"
else
  printf '  \033[33mSKIP\033[0m could not read deploy snippet (auth?) — check PUBLIC_URL manually\n'
fi

# 3. Cross-origin session create — the real gate. Simulates the browser's Origin.
echo "[3] cross-origin session (Origin: $ORIGIN)"
resp=$(curl -s -D /tmp/_emb_h -o /tmp/_emb_b -w '%{http_code}' \
  -X POST "$BASE/api/embed/session/create" \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d "{\"embed_id\":\"$EMBED\",\"public_key\":\"$PUBKEY\"}")
if [ "$resp" = "200" ]; then
  pass "session 200 — $ORIGIN is allowed for this key"
elif [ "$resp" = "403" ]; then
  fail "403 — $ORIGIN NOT in this key's allowed_origins (add it, or set origin_mode)"
else
  fail "session returned $resp (body: $(head -c 200 /tmp/_emb_b))"
fi

# 4. CORS header present (widget fetches with credentials:'omit', so '*' is VALID;
#    only a MISSING Allow-Origin header blocks the embed).
echo "[4] CORS response header"
aco=$(grep -i '^access-control-allow-origin:' /tmp/_emb_h | tr -d '\r' | awk '{print $2}')
if [ "$aco" = "$ORIGIN" ]; then
  pass "Allow-Origin echoes $ORIGIN (browser accepts)"
elif [ "$aco" = "*" ]; then
  pass "Allow-Origin '*' — OK: widget uses credentials:'omit', browser accepts '*'"
elif [ -z "$aco" ]; then
  fail "no Allow-Origin header — browser blocks the response"
else
  fail "Allow-Origin '$aco' != $ORIGIN — browser blocks"
fi

# 5. HTTPS / mixed-content sanity
echo "[5] scheme"
case "$BASE" in
  https://*) pass "https — no mixed-content block" ;;
  http://127.0.0.1*|http://localhost*) printf '  \033[33mWARN\033[0m http localhost — fine for local test; AWS MUST be https\n' ;;
  http://*)  fail "http — if customer site is https, browser blocks the widget (mixed content)" ;;
esac

echo
[ "$FAILED" = "0" ] && echo "ALL GREEN — embed should load in a browser on $ORIGIN" \
                     || echo "FAILURES above — fix before handing widget to customer"
exit "$FAILED"
