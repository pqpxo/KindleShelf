#!/usr/bin/env bash
# version 1
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created .env from .env.example. Edit it before starting KindleShelf."
    exit 0
fi

docker compose config >/dev/null
docker compose up -d --build
docker compose ps
