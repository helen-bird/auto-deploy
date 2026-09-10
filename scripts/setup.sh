#!/bin/bash
set -euo pipefail
TOOL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"${DEPLOY_PYTHON:-python3}" -m venv "$TOOL_ROOT/.venv"
"$TOOL_ROOT/.venv/bin/python" -m pip install -r "$TOOL_ROOT/requirements.txt"
printf '%s\n' 'Tool environment ready. Copy deployment.example.yaml to your Mac mini config directory.'
