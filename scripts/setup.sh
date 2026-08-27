#!/usr/bin/env bash
# First-time setup for a fresh agent-company-ai clone.
#   ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d venv ]; then
  echo "→ Creating virtualenv…"
  python3 -m venv venv
fi

echo "→ Installing agent-company-ai (editable, with dev extras)…"
venv/bin/pip install -e ".[dev]" --quiet

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ Created .env — open it and paste your DeepSeek API key (DEEPSEEK_API_KEY=...)."
fi

echo ""
if [ -d .agent-company-ai ]; then
  echo "✔ Environment ready — a company already exists. Start it with:  ./scripts/run.sh"
  echo "  (or wipe everything and start fresh with:  ./scripts/reset.sh)"
else
  echo "✔ Environment ready. Now INITIALIZE your company (this is where you name it):"
  echo ""
  echo "    venv/bin/agent-company-ai init"
  echo ""
  echo "  It will ask you for:"
  echo "    - company name        (e.g. 'My AI Company')"
  echo "    - provider            (choose: openai)"
  echo "    - model               (deepseek-v4-flash)"
  echo "    - base URL            (https://api.deepseek.com/v1)"
  echo "    - API key             (paste from your .env)"
  echo "    - integrations        (say no to skip for now)"
  echo ""
  echo "  Then start it with:    ./scripts/run.sh"
fi
