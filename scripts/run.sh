#!/usr/bin/env bash
# Launch the dashboard (loads .env, then starts the server).
#   ./scripts/run.sh [--port N]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
exec venv/bin/agent-company-ai dashboard "$@"
