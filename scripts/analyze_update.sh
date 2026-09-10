#!/bin/bash
set -euo pipefail
exec "$(dirname "$0")/autodeploy.sh" --config "${DEPLOY_CONFIG:-deployment.yaml}" check "$@"
