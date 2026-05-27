#!/usr/bin/env bash
# deploy.sh — deploys domytrade-backend via Railway API (no browser login needed)
#
# Requires RAILWAY_TOKEN in env or ~/.zshrc.
# Token is a Railway personal API token from railway.com/account/tokens.
#
# Usage:
#   ./deploy.sh               — redeploy latest commit to production
#   ./deploy.sh logs          — tail logs via CLI (needs active CLI session)
#   ./deploy.sh status        — show deployment status

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

# Project identifiers (from ~/.railway/config.json)
SERVICE="a68e47f7-4c21-4e78-aecb-66901593cd86"
ENVIRONMENT="6afc0c82-7226-4196-8f21-ed41a4ef5382"

CMD="${1:-up}"

case "$CMD" in
  up)
    echo "Deploying domytrade-backend..."
    RESULT=$(curl -s -X POST https://backboard.railway.app/graphql/v2 \
      -H "Authorization: Bearer $RAILWAY_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"query\": \"mutation { serviceInstanceRedeploy(serviceId: \\\"$SERVICE\\\", environmentId: \\\"$ENVIRONMENT\\\") }\"}")
    if echo "$RESULT" | grep -q '"serviceInstanceRedeploy":true'; then
      echo "Deployment triggered successfully."
    else
      echo "Error: $RESULT"
      exit 1
    fi
    ;;
  logs)
    railway logs --tail "${2:-100}"
    ;;
  status)
    railway status
    ;;
  *)
    railway "$@"
    ;;
esac
