#!/usr/bin/env bash
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEV_SCRIPT="${ROOT_DIR}/scripts/setup/setup-dev.sh"
PROD_SCRIPT="${ROOT_DIR}/scripts/setup/setup-prod.sh"

if [[ ! -f "$DEV_SCRIPT" || ! -f "$PROD_SCRIPT" ]]; then
  echo "Setup scripts not found." >&2
  exit 1
fi

echo "========================================"
echo " SOLE Backend Setup"
echo "========================================"
echo "Choose environment:"
echo "  1) Development (writes .env, generates dev keys)"
echo "  2) Production (writes .env.prod, requires inline secrets and keys)"
read -p "Selection [1/2]: " choice
choice=${choice:-1}

case "$choice" in
  1)
    exec "$DEV_SCRIPT"
    ;;
  2)
    exec "$PROD_SCRIPT"
    ;;
  *)
    echo "Invalid selection" >&2
    exit 1
    ;;
esac
