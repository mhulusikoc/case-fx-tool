#!/usr/bin/env bash
# Starts the FX conversion service.
#
# Environment variables:
#   PORT            TCP port to listen on (default: 8080)
#   FX_UPSTREAM_BASE  Upstream base URL (default: https://api.frankfurter.dev)
#                   Reviewers point this at a fake upstream — never hardcode
#                   frankfurter.dev anywhere else in the codebase.
set -euo pipefail

PORT="${PORT:-8080}"

# ---------------------------------------------------------------------------
# Locate a Python interpreter
# Prefer the project virtualenv if present, then fall back to system Python.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    PYTHON="${SCRIPT_DIR}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "ERROR: no Python interpreter found" >&2
    exit 1
fi

exec "${PYTHON}" -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT}"
