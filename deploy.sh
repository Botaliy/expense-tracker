#!/usr/bin/env bash
# Pull latest code and (re)deploy the app + nginx via docker compose.
# Usage: ./deploy.sh   (run on the server, inside the repo)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> Pulling latest code"
git pull --ff-only

if [ ! -f .env ]; then
    echo "==> ERROR: .env not found in $(pwd) — create it before deploying." >&2
    exit 1
fi

echo "==> Stopping and removing old containers"
docker compose down --remove-orphans

# In case an app container was ever started by hand (docker run ...)
# instead of compose, it won't be tracked by "compose down" above —
# make sure nothing is still squatting on our ports.
for port in 80 8000; do
    old=$(docker ps -q --filter "publish=${port}")
    if [ -n "$old" ]; then
        echo "==> Removing stray container holding port ${port}"
        docker rm -f "$old"
    fi
done

echo "==> Building new image"
docker compose build

echo "==> Starting containers"
docker compose up -d

echo "==> Cleaning up dangling images"
docker image prune -f

echo "==> Done. Current status:"
docker compose ps
