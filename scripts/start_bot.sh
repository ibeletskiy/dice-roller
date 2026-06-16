#!/usr/bin/env sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    echo "Create .env from .env.example and set API_TOKEN before starting the bot." >&2
    exit 1
fi

docker compose up -d --build dice-roller-bot
