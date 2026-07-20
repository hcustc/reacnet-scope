#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-127.0.0.1}"
PORT="${2:-8765}"

if python -c "import rdkit" >/dev/null 2>&1; then
  exec python "${ROOT_DIR}/scripts/webapp/server.py" --host "${HOST}" --port "${PORT}"
fi

if command -v uv >/dev/null 2>&1; then
  echo "[INFO] RDKit not found in current python, fallback to uv environment."
  exec uv run python "${ROOT_DIR}/scripts/webapp/server.py" --host "${HOST}" --port "${PORT}"
fi

echo "[WARN] RDKit is unavailable; structure rendering may fail."
exec python "${ROOT_DIR}/scripts/webapp/server.py" --host "${HOST}" --port "${PORT}"
