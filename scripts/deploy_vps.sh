#!/usr/bin/env bash
# VPS deploy helper for REED.
#
# Use this on a fresh small always-on box (1 vCPU, 1 GB RAM is enough)
# to bring up the backend in Docker, build the static dashboard, and
# serve it from the same container. Idempotent. Run as a non-root user
# with docker access.
#
# Usage:
#   REED_HOST=0.0.0.0 REED_PORT=80 ./scripts/deploy_vps.sh
#
# Required environment (passed through to the container):
#   One provider key (OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY,
#   or OLLAMA_API_KEY plus OLLAMA_HOST for Ollama).
#   Optional: REED_TRIGGER_TOKEN, REED_STORE=mirror plus HF_DATASET_REPO
#   and HF_TOKEN, REED_SEARCH_PROVIDER plus the matching search key.
#
# The script does not write any of these to disk. They live in
# /etc/reed/reed.env with mode 0600 and are read by `docker compose
# --env-file`. Treat the file the same as the keys themselves.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required tool: $1" >&2
    exit 1
  fi
}

require docker
require git
require node
require npm
require curl

if ! docker info >/dev/null 2>&1; then
  echo "docker daemon is not running or the current user cannot reach it" >&2
  exit 1
fi

ENV_DIR="/etc/reed"
ENV_FILE="$ENV_DIR/reed.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE" >&2
  echo "create it with mode 0600 and the keys the container needs:" >&2
  echo "  sudo install -d -m 0750 /etc/reed" >&2
  echo "  sudo install -m 0600 /dev/null /etc/reed/reed.env" >&2
  echo "  sudo $EDITOR /etc/reed/reed.env" >&2
  exit 1
fi

if [ ! -d .git ]; then
  echo "this script must run from inside the REED repository" >&2
  exit 1
fi

echo "fetching the latest commit on the current branch"
git fetch --prune origin
git pull --ff-only

echo "building the backend image"
docker build -t reed-backend:local backend

echo "building the static dashboard"
(
  cd dashboard
  if [ ! -d node_modules ]; then
    npm ci
  fi
  if [ -z "${VITE_DATASET_BASE:-}" ]; then
    echo "VITE_DATASET_BASE is unset; building a dashboard with only the baked sample" >&2
  fi
  npm run build:demo
)

echo "stopping any previous REED container"
docker compose down --remove-orphans || true

echo "starting the backend"
docker compose up -d --no-build backend

echo "waiting for /api/health"
HEALTH_URL="http://localhost:8000/api/health"
for _ in $(seq 1 60); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "backend is healthy"
    break
  fi
  sleep 2
done

if ! curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
  echo "backend did not become healthy within 120 seconds" >&2
  echo "tail of backend logs:" >&2
  docker compose logs --no-color --tail=100 backend >&2
  exit 1
fi

echo
echo "REED is up."
echo "  API health: $HEALTH_URL"
echo "  Dashboard dist: $REPO_ROOT/dashboard/dist"
echo "  Container logs: docker compose logs -f backend"
echo
echo "Next steps:"
echo "  1. Serve dashboard/dist/ behind nginx or any static host."
echo "  2. If you want external cron triggers, ensure REED_TRIGGER_TOKEN is set in $ENV_FILE and POST to http://localhost:8000/api/trigger/{session} on the schedule."