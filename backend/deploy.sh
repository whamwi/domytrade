#!/usr/bin/env bash
# deploy.sh — deploys domytrade (backend + frontend) with no browser login
#
# Requires RAILWAY_TOKEN (project token) in env or ~/.zshrc.
# Get/create project tokens at: railway.com → project → Settings → Tokens
#
# Usage:
#   ./deploy.sh               — deploy backend (Railway) + frontend (Vercel)
#   ./deploy.sh backend       — backend only
#   ./deploy.sh frontend      — frontend only
#   ./deploy.sh logs          — tail Railway logs

set -e
cd "$(dirname "$0")"

# Load tokens from .zshrc if not already in env
if [[ -z "$RAILWAY_TOKEN" ]]; then
  source ~/.zshrc 2>/dev/null || true
fi

if [[ -z "$RAILWAY_TOKEN" ]]; then
  echo "Error: RAILWAY_TOKEN not set. Add the project token to ~/.zshrc or export it before running."
  exit 1
fi

deploy_backend() {
  echo "Deploying domytrade-backend (Railway)..."
  railway up --service domytrade-backend
  echo "  ✓ Backend deployed."
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
