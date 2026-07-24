#!/usr/bin/env bash
set -euo pipefail
BASELINES_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$BASELINES_ROOT/envs/agentdiet/bin/python" \
  "$BASELINES_ROOT/run_harbor_smoke.py" agentdiet "$@"

