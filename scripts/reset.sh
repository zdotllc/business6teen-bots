#!/usr/bin/env bash
# Wipe ALL data and start completely fresh: destroys the company (config + db +
# landing pages + output), resets dashboard logins, and initializes a brand-new
# company with a default working team.
#   ./scripts/reset.sh [--name "My AI Company"]
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:-My AI Company}"
if [ "${2:-}" = "--name" ] || [ "${1:-}" = "--name" ]; then
  # allow: reset.sh --name "Foo"
  if [ "${1:-}" = "--name" ]; then shift; NAME="${1:-My AI Company}"; fi
fi

set -a; [ -f .env ] && . ./.env; set +a
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "✖ DEEPSEEK_API_KEY is not set in .env — add it first (see .env.example)." >&2
  exit 1
fi

echo "→ Stopping any running dashboard…"
pkill -f "agent-company-ai dashboard" 2>/dev/null || true

if [ -d .agent-company-ai ]; then
  echo "→ Destroying existing company data…"
  venv/bin/agent-company-ai destroy -y 2>/dev/null || rm -rf .agent-company-ai
fi

echo "→ Removing dashboard logins, wallet keys, and caches…"
rm -f dashboard_users.json
rm -rf .vault
rm -rf .pytest_cache
rm -rf src/agent_company_ai.egg-info 2>/dev/null || true

echo "→ Initializing fresh company '${NAME}' (provider: deepseek)…"
venv/bin/agent-company-ai init \
  --name "$NAME" \
  --provider deepseek \
  --api-key '${DEEPSEEK_API_KEY}' \
  --model deepseek-v4-flash \
  --skip-integrations

echo "→ Hiring the default team…"
venv/bin/agent-company-ai hire ceo --name Alex
venv/bin/agent-company-ai hire cto --name Jordan
venv/bin/agent-company-ai hire developer --name Sam
venv/bin/agent-company-ai hire marketer --name Morgan
venv/bin/agent-company-ai hire project_manager --name Casey

echo ""
echo "✔ Fresh start complete."
echo "  Company: $NAME"
echo "  Team:    Alex (CEO), Jordan (CTO), Sam (Developer), Morgan (Marketer), Casey (PM)"
echo "  Login:   admin / admin123  (change it on first login)"
echo ""
echo "  Start the dashboard:  ./scripts/run.sh"
