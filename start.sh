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

# Install missing deps on Linux/Ubuntu (Docker + Compose, or for local: Python3/venv/pip)
install_linux_deps() {
  if [ ! -f /etc/os-release ]; then
    return 0
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "$ID" != "ubuntu" ] && [ "$ID" != "debian" ] && [ "$ID_LIKE" != "debian" ]; then
    return 0
  fi
  DOCKER_DISTRO="${ID:-ubuntu}"
  VERSION_CODENAME="${VERSION_CODENAME:-jammy}"

  if ! command -v docker &>/dev/null; then
    echo "Installing Docker and Docker Compose on Linux..."
    sudo apt-get update -qq
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL "https://download.docker.com/linux/${DOCKER_DISTRO}/gpg" -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${DOCKER_DISTRO} ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl start docker
    sudo systemctl enable docker
    echo "Docker installed. If you get 'permission denied' below, run: sudo ./start.sh  (or: sudo usermod -aG docker $USER then log out and back in)."
  fi

  if [ "${1:-}" = "local" ]; then
    if ! command -v python3 &>/dev/null || ! python3 -c "import venv" 2>/dev/null; then
      echo "Installing Python3 and venv..."
      sudo apt-get update -qq
      sudo apt-get install -y python3 python3-venv python3-pip
    fi
  fi
}

# Install and start PostgreSQL, create DB and user for local mode (Linux)
setup_local_postgres() {
  if [ ! -f /etc/os-release ]; then
    return 0
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "$ID" != "ubuntu" ] && [ "$ID" != "debian" ] && [[ "${ID_LIKE:-}" != *"debian"* ]]; then
    return 0
  fi
  if ! command -v psql &>/dev/null; then
    echo "Installing PostgreSQL..."
    sudo apt-get update -qq
    sudo apt-get install -y postgresql postgresql-contrib
  fi
  if ! sudo systemctl is-active --quiet postgresql 2>/dev/null; then
    echo "Starting PostgreSQL..."
    sudo systemctl start postgresql
    sudo systemctl enable postgresql 2>/dev/null || true
  fi
  # Create database and set password for postgres user (idempotent)
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='nof1'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE nof1;"
  sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';" 2>/dev/null || true
  # Point .env at local Postgres
  if [ -f .env ]; then
    if grep -q "^DATABASE_URL=" .env; then
      sed -i.bak 's|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nof1|' .env 2>/dev/null || true
    else
      echo "DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nof1" >> .env
    fi
  fi
}

# Prefer docker compose (plugin); fallback to docker-compose (standalone)
docker_compose_cmd() {
  if docker compose version &>/dev/null; then
    docker compose "$@"
  elif command -v docker-compose &>/dev/null; then
    docker-compose "$@"
  else
    echo "Docker Compose not found. Install docker-compose-plugin or docker-compose."
    exit 1
  fi
}

# Default: local (no Docker). Use ./start.sh docker for Docker.
if [ "${1:-}" = "docker" ]; then
  install_linux_deps
  if ! command -v docker &>/dev/null; then
    echo "Docker not found and could not install. Run ./start.sh for local mode (no Docker)."
    exit 1
  fi
  echo "Starting with Docker (Postgres + Backend)..."
  docker_compose_cmd up --build
else
  install_linux_deps local
  setup_local_postgres
  echo "Local mode: installing deps and running backend (no Docker)..."
  ROOT="$(pwd)"
  VENV="${VENV:-$ROOT/.venv}"
  PY="$VENV/bin/python"
  if [ ! -x "$PY" ]; then
    echo "Creating virtual environment at $VENV..."
    python3 -m venv "$VENV"
  fi
  if ! "$PY" -m pip --version &>/dev/null; then
    echo "Bootstrapping pip in venv..."
    "$PY" -m ensurepip --upgrade
  fi
  "$PY" -m pip install -q -r backend/requirements.txt
  [ -f .env ] && cp .env backend/.env
  cd backend && exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
