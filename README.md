# Nof1 Polymarket Clone

Full-stack foundation for a Polymarket-style prediction trading platform with:
- FastAPI backend
- PostgreSQL database
- Model forecasting + per-model budget control
- Polymarket CLOB trading integration hooks

## Quick Start

1. Copy env file:
   - `cp .env.example .env`
2. Start (local, no Docker):
   - `./start.sh` — on Linux (Ubuntu/Debian) this installs PostgreSQL, creates the `nof1` database and user, and sets `DATABASE_URL` in `.env`.
   - Or with Docker: `./start.sh docker`
3. Open in browser (replace with your server IP when deploying, e.g. `http://159.89.172.175:8000`):
   - **Dashboard:** `/dashboard` or `/`
   - **API docs:** `/docs`
   - Optional: set `OPENROUTER_SITE_URL` in `.env` to your public URL (e.g. `http://159.89.172.175:8000`) for OpenRouter referrer.

## What Is Included

- Market + forecast + trade data model
- Internal "paper trading" ledger
- External Polymarket trade bridge service wrapper
- Monthly budget guardrails for each model (set to `$100` by default)
- REST APIs for markets, forecasts, and order execution

## Models & Optimization

Default models are chosen for prediction-market performance (benchmarks: GPT-4/Claude/Gemini lead; DeepSeek R1 for reasoning):
- `openai/gpt-5.2-pro`, `anthropic/claude-sonnet-4.5`, `anthropic/claude-opus-4.5`, `google/gemini-2.5-pro-preview`, `deepseek/deepseek-r1-0528`

Prompts use chain-of-thought reasoning and calibration hints. Set `GAME_EDGE_THRESHOLD` (default 0.08 = 8%) to require minimum edge before trading. Override `MODEL_NAMES` and `MODEL_ACCOUNT_CONFIGS` in `.env` or `model_config.json`.

## Notes

- This is a serious starting implementation, not a complete production exchange.
- Before live trading, add key management, compliance checks, circuit breakers, and strategy controls.

## Auto Claiming

Auto claimer is integrated and can be enabled with:
- `AUTO_CLAIM_ENABLED=true`
- `AUTO_CLAIM_INTERVAL_SECONDS=600`
- `POLYGON_RPC_URL=...`
- `PRIVATE_KEY=...`
- `WALLET_ADDRESS=...` (optional; use for Safe/proxy flow)

Claim records are visible at `GET /admin/auto-claims`.

## Test trade (1 USD)

With the backend running and `.env` set (e.g. `ENABLE_LIVE_TRADING=true` and a model `private_key` in `MODEL_ACCOUNT_CONFIGS`):

```bash
# Optional: sync markets first
curl -X POST "http://127.0.0.1:8000/markets/sync"

# Place one test trade with 1 USD notional
curl -X POST "http://127.0.0.1:8000/admin/test-trade?usd_value=1"
```

Or use the script (defaults to `http://127.0.0.1:8000`; set `BASE_URL` for deployment):

```bash
./scripts/test-trade-1usd.sh
# On deployment: BASE_URL=http://159.89.172.175:8000 ./scripts/test-trade-1usd.sh
```
