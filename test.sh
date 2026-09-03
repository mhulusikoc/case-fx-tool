#!/usr/bin/env bash
# Runs the test suite.
#
# Tests use fake httpx transports and never make real network calls, so this
# script passes even when FX_UPSTREAM_BASE points at a closed port:
#
#   FX_UPSTREAM_BASE=http://127.0.0.1:9 ./test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Locate a Python interpreter (same priority as run.sh)
# ---------------------------------------------------------------------------
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

# Run pytest from the repository root so relative imports resolve correctly.
cd "${SCRIPT_DIR}"
exec "${PYTHON}" -m pytest "$@"
