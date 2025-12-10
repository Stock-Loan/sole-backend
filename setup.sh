#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXEC_TARGET="${SCRIPT_DIR}/scripts/setup/setup.sh"
if [[ ! -x "$EXEC_TARGET" ]]; then
  echo "Setup script missing at $EXEC_TARGET" >&2
  exit 1
fi
exec "$EXEC_TARGET"
