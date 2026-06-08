#!/usr/bin/env bash
# quickstart.sh — verify the embed API end-to-end from a terminal in ~10s.
# Public mode (no HMAC). For user-scoped mode use one of the SDK clients.
#
#   ./quickstart.sh
#   BASE=https://pharma.yoursite.com ORIGIN=https://yourpharmacy.com ./quickstart.sh
set -euo pipefail

BASE="${BASE:-http://localhost:8011}"
EMBED_ID="${EMBED_ID:-emb_rGd8VWW8DloS6WNNssvenA}"
PUBLIC_KEY="${PUBLIC_KEY:-pub_FWWyXah2Sv0iuN5f8TwQQH1v2LaoeIUT}"
ORIGIN="${ORIGIN:-https://yourpharmacy.com}"   # MUST be in the embed allowlist

echo "1/3  widget.js loads"
curl -fsS -I "$BASE/api/embed/widget.js" | grep -i "content-type" || { echo "  ✗ widget not served"; exit 1; }

echo "2/3  create session"
SESSION=$(curl -fsS -X POST "$BASE/api/embed/session/create" \
  -H "Content-Type: application/json" -H "Origin: $ORIGIN" \
  -d "{\"embed_id\":\"$EMBED_ID\",\"public_key\":\"$PUBLIC_KEY\"}" \
  | grep -o '"session_token":"[^"]*"' | cut -d'"' -f4)
[ -n "$SESSION" ] || { echo "  ✗ no session_token — check embed is live + origin allowlisted"; exit 1; }
echo "  session=$SESSION"

echo "3/3  chat"
curl -fsS -X POST "$BASE/api/embed/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_token\":\"$SESSION\",\"message\":\"is paracetamol in stock?\"}"
echo
echo "✓ embed API working"
