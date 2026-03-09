#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Ensure .env exists
if [ ! -f .env ]; then
  echo "No .env found. Copying .env.example to .env"
  cp .env.example .env
  echo "Edit .env with your keys, then run ./start.sh again."
  exit 0
fi

if [ "${1:-}" = "local" ]; then
  echo "Local mode: installing deps and running backend..."
  VENV="${VENV:-.venv}"
  if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  pip install -q -r backend/requirements.txt
  [ -f .env ] && cp .env backend/.env
  cd backend && exec uvicorn app.main:app --host 0.0.0.0 --port 8000
else
  echo "Starting with Docker (Postgres + Backend)..."
  docker compose up --build
fi
