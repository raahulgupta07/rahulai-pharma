#!/usr/bin/env bash
# Set an embed key's allowed origins (the cross-site allowlist a customer's
# website must be on to load the widget). Safe wrapper around the DB update —
# use this instead of hand-writing SQL.
#
# Empty allowed_origins = block-all (the secure default). Each customer site
# that will host the widget must be added here, OR set --mode open for a
# trusted/internal embed that any origin may use.
#
# Usage:
#   scripts/set_embed_origins.sh <embed_id> <origin> [origin2 ...]
#   scripts/set_embed_origins.sh emb_2Gpd... https://shop.acme.com https://www.acme.com
#   scripts/set_embed_origins.sh emb_2Gpd... --mode open        # any origin (insecure)
#   scripts/set_embed_origins.sh emb_2Gpd... --mode strict --clear   # wipe to block-all
#   scripts/set_embed_origins.sh --list                          # show all keys + origins
#
# Origins must be scheme + host [+ port], NO trailing path:  https://shop.acme.com
# Glob is supported by verify_origin: https://*.acme.com
set -uo pipefail

DB_CONTAINER="${DB_CONTAINER:-cp-db}"
DB_USER="${DB_USER:-ai}"
DB_NAME="${DB_NAME:-ai}"
psql() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" "$@"; }

if [ "${1:-}" = "--list" ]; then
  echo "embed_id | origin_mode | allowed_origins"
  psql -tA -F' | ' -c "SELECT embed_id, COALESCE(origin_mode,'strict'), COALESCE(array_to_string(allowed_origins,','),'<empty=block-all>') FROM public.dash_agent_embeds ORDER BY created_at DESC;"
  exit 0
fi

EMBED="${1:?usage: set_embed_origins.sh <embed_id> <origin...>   (or --list)}"; shift

MODE=""           # only set if --mode passed
CLEAR=0
ORIGINS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:?--mode needs strict|subdomains|open}"; shift 2 ;;
    --clear) CLEAR=1; shift ;;
    http://*|https://*) ORIGINS+=("$1"); shift ;;
    *) echo "ERROR: bad arg '$1' — origins must start with http:// or https://" >&2; exit 1 ;;
  esac
done

# Validate origins have no path (scheme + host[:port] only)
for o in "${ORIGINS[@]:-}"; do
  [ -z "$o" ] && continue
  rest="${o#*://}"
  case "$rest" in
    */*) echo "ERROR: '$o' has a path — use scheme+host only, e.g. https://shop.acme.com" >&2; exit 1 ;;
  esac
done

# Confirm the key exists
exists=$(psql -tA -c "SELECT 1 FROM public.dash_agent_embeds WHERE embed_id='${EMBED//\'/}' LIMIT 1;")
if [ "$exists" != "1" ]; then
  echo "ERROR: no embed key '$EMBED' (run --list to see keys)" >&2; exit 1
fi

# Build the SQL array literal safely (origins already validated to http(s)+host)
if [ "$CLEAR" = "1" ] || [ ${#ORIGINS[@]} -eq 0 ]; then
  ARR="'{}'"
else
  joined=""
  for o in "${ORIGINS[@]}"; do joined="${joined:+$joined,}\"$o\""; done
  ARR="'{${joined}}'"
fi

SQL="UPDATE public.dash_agent_embeds SET allowed_origins=${ARR}"
[ -n "$MODE" ] && SQL="$SQL, origin_mode='${MODE//\'/}'"
SQL="$SQL WHERE embed_id='${EMBED//\'/}';"

echo "Applying: $SQL"
psql -c "$SQL"

echo "--- now: ---"
psql -tA -F' | ' -c "SELECT embed_id, COALESCE(origin_mode,'strict'), COALESCE(array_to_string(allowed_origins,','),'<empty=block-all>') FROM public.dash_agent_embeds WHERE embed_id='${EMBED//\'/}';"
echo "Tip: verify with  scripts/embed_smoke.sh <BASE> $EMBED <pub_key> <one-of-the-origins>"
