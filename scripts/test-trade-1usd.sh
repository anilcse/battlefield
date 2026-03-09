#!/usr/bin/env bash
# Run one live test trade with 1 USD notional.
# Prereqs: backend running (e.g. ./start.sh), .env with ENABLE_LIVE_TRADING=true and model private_key set.
set -e
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
echo "Using BASE_URL=$BASE_URL"
echo "Syncing markets..."
curl -s -X POST "$BASE_URL/markets/sync" > /dev/null || true
echo "Placing test trade (1 USD)..."
curl -s -X POST "$BASE_URL/admin/test-trade?usd_value=1" | python3 -m json.tool
echo "Done."
