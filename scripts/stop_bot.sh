#!/usr/bin/env sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"

docker stop dice-roller-bot
