#!/bin/bash

############################################################################
#
#    RLAI-DASH — Railway Deployment
#
#    Usage:
#      1. Install Railway CLI: npm install -g @railway/cli
#      2. Login: railway login
#      3. Set your API key: export OPENROUTER_API_KEY=sk-or-v1-xxx
#      4. Run: ./scripts/railway_up.sh
#
############################################################################

set -e

GREEN='\033[0;32m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${GREEN}${BOLD}RLAI-DASH — Railway Deploy${NC}"
echo ""

# Load .env if exists
if [[ -f .env ]]; then
    set -a; source .env; set +a
    echo -e "${DIM}Loaded .env${NC}"
fi

# Check prerequisites
if ! command -v railway &> /dev/null; then
    echo "Railway CLI not found. Install: npm install -g @railway/cli"
    exit 1
fi

if [[ -z "$OPENROUTER_API_KEY" ]]; then
    echo "OPENROUTER_API_KEY not set. Add to .env or: export OPENROUTER_API_KEY=sk-or-v1-xxx"
    exit 1
fi

# Step 1: Create project
echo -e "${BOLD}1. Creating Railway project...${NC}"
railway init -n "rlai-dash"

# Step 2: Add PostgreSQL with pgvector
echo ""
echo -e "${BOLD}2. Adding PostgreSQL + pgvector...${NC}"
railway add -s pgvector -i agnohq/pgvector:18 \
    -v "POSTGRES_USER=${DB_USER:-ai}" \
    -v "POSTGRES_PASSWORD=${DB_PASS:-ai}" \
    -v "POSTGRES_DB=${DB_DATABASE:-ai}" \
    -v "PGDATA=/var/lib/postgresql/data"

# Add volume for data persistence
railway service link pgvector
railway volume add -m /var/lib/postgresql/data 2>/dev/null || echo -e "${DIM}Volume exists${NC}"

echo -e "${DIM}Waiting 15s for database...${NC}"
sleep 15

# Step 3: Deploy app
echo ""
echo -e "${BOLD}3. Deploying RLAI-DASH app...${NC}"
railway add -s rlai-dash \
    -v "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}" \
    -v "DB_USER=${DB_USER:-ai}" \
    -v "DB_PASS=${DB_PASS:-ai}" \
    -v "DB_HOST=pgvector.railway.internal" \
    -v "DB_PORT=5432" \
    -v "DB_DATABASE=${DB_DATABASE:-ai}" \
    -v "DB_DRIVER=postgresql+psycopg" \
    -v "SUPER_ADMIN=${SUPER_ADMIN:-admin}" \
    -v "SUPER_ADMIN_PASS=${SUPER_ADMIN_PASS:-admin}" \
    -v "AGNO_DEBUG=True" \
    -v "AGENTOS_URL=http://127.0.0.1:8000" \
    -v "PYTHONPATH=/app" \
    -v "PORT=8000"

# Step 4: Deploy
echo ""
echo -e "${BOLD}4. Building and deploying...${NC}"
railway up --service rlai-dash -d

# Step 5: Generate domain
echo ""
echo -e "${BOLD}5. Generating domain...${NC}"
railway domain --service rlai-dash

echo ""
echo -e "${GREEN}${BOLD}Done!${NC}"
echo ""
echo "  Next steps:"
echo "  1. Wait 2-3 minutes for build to complete"
echo "  2. Open the domain URL above"
echo "  3. Login: ${SUPER_ADMIN:-admin} / ${SUPER_ADMIN_PASS:-admin}"
echo ""
echo "  Useful commands:"
echo "    railway logs --service rlai-dash    # View logs"
echo "    railway up --service rlai-dash -d   # Redeploy"
echo "    railway status                      # Check status"
echo ""
