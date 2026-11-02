#!/usr/bin/env bash
# Usage: scripts/screenshots.sh [subfolder]   (dev stack must be up: docker compose up -d api worker web, and a demo run exists)
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm --network host --ipc=host -e BASE -e RUN -e EMAIL -e MOCK_BATCHES -e MOCK_TEAMS -e DENSITY -e VIEWS -e PAGES -e OUT=/out \
    -v "$PWD/scripts:/s:ro" -v "$PWD/_design:/design:ro" -v "$PWD/_design/shots:/out" mcr.microsoft.com/playwright/python:v1.49.0-noble \
    sh -c 'pip install -q playwright==1.49.0 >/dev/null 2>&1; python /s/screenshots.py "$0"' "${1:-current}"
