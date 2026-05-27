#!/usr/bin/env bash
# deploy.sh — deploys domytrade (backend + frontend) with no browser login
#
# Requires RAILWAY_TOKEN in env or ~/.zshrc.
# Token is a Railway personal API token from railway.com/account/tokens.
#
# Usage:
#   ./deploy.sh               — deploy backend (Railway) + frontend (Vercel)
#   ./deploy.sh backend       — backend only
#   ./deploy.sh frontend      — frontend only
#   ./deploy.sh logs          — tail Railway logs

set -e
cd "$(dirname "$0")"

# Load token from .zshrc if not already in env
if [[ -z "$RAILWAY_TOKEN" ]]; then
  source ~/.zshrc 2>/dev/null || true
fi

if [[ -z "$RAILWAY_TOKEN" ]]; then
  echo "Error: RAILWAY_TOKEN not set. Add it to ~/.zshrc or export it before running."
  exit 1
fi

# Railway project identifiers
SERVICE="a68e47f7-4c21-4e78-aecb-66901593cd86"
ENVIRONMENT="6afc0c82-7226-4196-8f21-ed41a4ef5382"

deploy_backend() {
  echo "Deploying domytrade-backend (Railway)..."
  RESULT=$(curl -s -X POST https://backboard.railway.app/graphql/v2 \
    -H "Authorization: Bearer $RAILWAY_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"mutation { serviceInstanceRedeploy(serviceId: \\\"$SERVICE\\\", environmentId: \\\"$ENVIRONMENT\\\") }\"}")
  if echo "$RESULT" | grep -q '"serviceInstanceRedeploy":true'; then
    echo "  ✓ Backend deployment triggered."
  else
    echo "  ✗ Backend error: $RESULT"
    exit 1
  fi
}

deploy_frontend() {
  echo "Deploying domytrade-frontend (Vercel)..."
  vercel deploy --prod --cwd "$(dirname "$0")/../frontend" 2>&1 | tail -5
  echo "  ✓ Frontend deployed."
}

CMD="${1:-all}"

case "$CMD" in
  all|up)
    deploy_backend
    deploy_frontend
    ;;
  backend)
    deploy_backend
    ;;
  frontend)
    deploy_frontend
    ;;
  logs)
    railway logs --tail "${2:-100}"
    ;;
  *)
    railway "$@"
    ;;
esac
