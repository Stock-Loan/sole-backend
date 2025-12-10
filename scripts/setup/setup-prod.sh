#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.prod}"

msg() { printf "\033[1;32m[setup-prod]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup-prod]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[setup-prod]\033[0m %s\n" "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1. Please install it and retry."
}

write_env_var() {
  local key="$1"; local value="$2"
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
key, value = sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines() if path.exists() else []
out, found = [], False
for line in lines:
    if not line or line.lstrip().startswith('#'):
        out.append(line)
        continue
    current_key = line.split('=', 1)[0]
    if current_key == key:
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY
}

prompt_required() {
  local key="$1"; local prompt="$2"; local disallow="$3"
  while true; do
    printf "%s: " "$prompt"
    read -r val
    if [[ -z "$val" ]]; then
      warn "$key is required."
      continue
    fi
    if [[ -n "$disallow" && "$val" == "$disallow" ]]; then
      warn "Value resembles a dev default; provide a production value."
      continue
    fi
    write_env_var "$key" "$val"
    break
  done
}

main() {
  require_cmd python3

  msg "========================================"
  msg " SOLE Backend Setup (Production)"
  msg "========================================"
  msg "You will be prompted for required values. No secrets files will be written; JWT keys must be inline PEM."

  : > "$ENV_FILE"
  prompt_required "DATABASE_URL" "PostgreSQL URL (async driver, e.g., postgresql+asyncpg://user:pass@host:5432/dbname)" "postgresql+asyncpg://sole:sole@db:5432/sole"
  prompt_required "REDIS_URL" "Redis URL (e.g., redis://user:pass@host:6379/0)" "redis://redis:6379/0"
  prompt_required "TENANCY_MODE" "Tenancy mode (single|multi)" ""
  prompt_required "DEFAULT_ORG_ID" "Default org_id (used when TENANCY_MODE=single)" ""
  prompt_required "ALLOWED_ORIGINS" "Allowed CORS origins (e.g., [\"https://app.example.com\"])" ""
  prompt_required "ALLOWED_TENANT_HOSTS" "Allowed tenant hostnames for subdomain resolution (e.g., [\"api.example.com\"])" ""
  prompt_required "SECRET_KEY" "SECRET_KEY (min 32 chars)" "dev-secret-change-me-123456789"
  prompt_required "JWT_PRIVATE_KEY" "JWT PRIVATE KEY (inline PEM, RSA 4096)" ""
  prompt_required "JWT_PUBLIC_KEY" "JWT PUBLIC KEY (inline PEM, RSA 4096)" ""

  write_env_var "JWT_ALGORITHM" "RS256"
  write_env_var "SESSION_TIMEOUT_MINUTES" "${SESSION_TIMEOUT_MINUTES:-30}"
  write_env_var "ACCESS_TOKEN_EXPIRE_MINUTES" "${ACCESS_TOKEN_EXPIRE_MINUTES:-15}"
  write_env_var "REFRESH_TOKEN_EXPIRE_MINUTES" "${REFRESH_TOKEN_EXPIRE_MINUTES:-10080}"
  write_env_var "RATE_LIMIT_PER_MINUTE" "${RATE_LIMIT_PER_MINUTE:-60}"
  write_env_var "LOGIN_ATTEMPT_LIMIT" "${LOGIN_ATTEMPT_LIMIT:-5}"
  write_env_var "LOGIN_LOCKOUT_MINUTES" "${LOGIN_LOCKOUT_MINUTES:-15}"
  write_env_var "DEFAULT_PASSWORD_MIN_LENGTH" "${DEFAULT_PASSWORD_MIN_LENGTH:-12}"
  write_env_var "ENABLE_HSTS" "${ENABLE_HSTS:-true}"
  write_env_var "LOG_LEVEL" "${LOG_LEVEL:-INFO}"

  msg "========================================"
  msg " Production .env created at ${ENV_FILE}"
  msg "========================================"
}

main "$@"
