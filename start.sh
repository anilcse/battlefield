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
    if ! command -v python3 &>/dev/null; then
      echo "Installing Python3..."
      sudo apt-get update -qq
      sudo apt-get install -y python3 python3-venv python3-pip
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

if [ "${1:-}" = "local" ]; then
  install_linux_deps local
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
  install_linux_deps
  if ! command -v docker &>/dev/null; then
    echo "Docker not found and could not install. Run ./start.sh local for Python-only mode (set DATABASE_URL in .env)."
    exit 1
  fi
  echo "Starting with Docker (Postgres + Backend)..."
  docker_compose_cmd up --build
fi
