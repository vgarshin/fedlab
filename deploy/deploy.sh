#!/usr/bin/env bash
# Run on YOUR workstation. Syncs the project to the VM and (re)starts the stack there.
#   SSH_CONFIG=/home/vgarshin/yadisk/clouds/config deploy/deploy.sh [host]   (default host: fedlab-01)
# Secrets (config/secrets.json) are copied with mode 600; they are never baked into images.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-fedlab-01}"
SSH=(ssh -F "${SSH_CONFIG:-$HOME/.ssh/config}")
REMOTE=/opt/fedlab

rsync -az --delete -e "${SSH[*]}" \
    --exclude '.git' --exclude '_reqs' --exclude '_design' --exclude 'web/node_modules' --exclude 'web/dist' \
    --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'config/.pg_password' \
    ./ "$HOST:$REMOTE/"
"${SSH[@]}" "$HOST" "chmod 600 $REMOTE/config/secrets.json && cd $REMOTE && deploy/prepare-secrets.sh && \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build && \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps"
