#!/bin/bash

############################################################################
#
#    Agno Container Entrypoint
#
############################################################################

# Colors
ORANGE='\033[38;5;208m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${ORANGE}"
cat << 'BANNER'
     █████╗  ██████╗ ███╗   ██╗ ██████╗
    ██╔══██╗██╔════╝ ████╗  ██║██╔═══██╗
    ███████║██║  ███╗██╔██╗ ██║██║   ██║
    ██╔══██║██║   ██║██║╚██╗██║██║   ██║
    ██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝
    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝
BANNER
echo -e "${NC}"

if [[ "$PRINT_ENV_ON_LOAD" = true || "$PRINT_ENV_ON_LOAD" = True ]]; then
    echo -e "    ${DIM}Environment:${NC}"
    printenv | sed 's/^/    /'
    echo ""
fi

# Best-effort: ensure runtime-writable dirs exist on the mounted /app/knowledge
# volume. Runs as the non-root 'dash' user — succeeds when the volume is
# dash-owned (Dockerfile pre-creates it so a fresh volume inherits ownership).
# If the volume is root-owned (legacy/misconfigured mount) these fail silently;
# the app degrades gracefully (see /decks fail-soft in app/main.py) instead of
# crashing. To repair a root-owned volume run once on the host:
#   docker run --rm -v <project>_knowledge_data:/k alpine chown -R 999:999 /k
for _d in _decks embed_logos seeds tables business queries rules; do
    mkdir -p "/app/knowledge/$_d" 2>/dev/null || true
done

if [[ "$WAIT_FOR_DB" = true || "$WAIT_FOR_DB" = True ]]; then
    echo -e "    ${DIM}Waiting for database at ${DB_HOST}:${DB_PORT}...${NC}"
    # Pure-bash TCP wait (replaced dockerize — removed: 8 HIGH CVEs + abandoned repo).
    _db_deadline=$((SECONDS + 300))
    until (exec 3<>"/dev/tcp/${DB_HOST}/${DB_PORT}") 2>/dev/null; do
        if [ "$SECONDS" -ge "$_db_deadline" ]; then
            echo -e "    Database wait timed out after 300s" >&2
            exit 1
        fi
        sleep 1
    done
    exec 3>&- 2>/dev/null || true
    echo -e "    ${BOLD}Database ready.${NC}"
    echo ""
fi

case "$1" in
    chill)
        echo -e "    ${DIM}Mode: chill${NC}"
        echo -e "    ${BOLD}Container running.${NC}"
        echo ""
        while true; do sleep 18000; done
        ;;
    *)
        echo -e "    ${DIM}> $@${NC}"
        echo ""
        exec "$@"
        ;;
esac