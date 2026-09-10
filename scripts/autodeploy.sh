#!/bin/bash
set -euo pipefail
TOOL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$TOOL_ROOT"
TOOL_PYTHON="${DEPLOY_PYTHON:-$TOOL_ROOT/.venv/bin/python}"
if [[ ! -x "$TOOL_PYTHON" ]]; then TOOL_PYTHON="${DEPLOY_PYTHON:-python3}"; fi
exec "$TOOL_PYTHON" -m autodeploy.cli "$@"
