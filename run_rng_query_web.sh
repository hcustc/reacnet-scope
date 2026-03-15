#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-127.0.0.1}"
PORT="${2:-8765}"

python "${ROOT_DIR}/scripts/webapp/server.py" --host "${HOST}" --port "${PORT}"
