#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-127.0.0.1}"
PORT="${2:-8060}"

if command -v uv >/dev/null 2>&1; then
  exec uv run reacnet-scope-web-dash --host "${HOST}" --port "${PORT}"
fi

exec python -m scripts.webapp_dash.app --host "${HOST}" --port "${PORT}"
