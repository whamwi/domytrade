#!/usr/bin/env bash
# deploy.sh — always runs railway from the correct project directory
# Prevents the CLI from traversing up to /Users/wassim/syriatalent and
# picking syriatalent-backend instead of domytrade-backend.
#
# Usage:
#   ./deploy.sh               — deploy current branch to production
#   ./deploy.sh logs          — tail logs
#   ./deploy.sh status        — show deployment status
#   ./deploy.sh link          — re-link after railway login

set -e
cd "$(dirname "$0")"   # always run from backend dir, regardless of caller's cwd

CMD="${1:-up}"

case "$CMD" in
  up)
    echo "Deploying domytrade-backend..."
    railway up --detach
    ;;
  logs)
    railway logs --tail "${2:-100}"
    ;;
  status)
    railway status
    ;;
  link)
    railway link --project domytrade-backend
    ;;
  *)
    railway "$@"
    ;;
esac
