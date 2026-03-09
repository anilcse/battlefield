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
3. Open in browser:
   - **Dashboard:** [http://localhost:8000/dashboard](http://localhost:8000/dashboard) (or [http://localhost:8000/](http://localhost:8000/))
   - **API docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

## What Is Included

- Market + forecast + trade data model
- Internal "paper trading" ledger
- External Polymarket trade bridge service wrapper
- Monthly budget guardrails for each model (set to `$100` by default)
- REST APIs for markets, forecasts, and order execution

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
