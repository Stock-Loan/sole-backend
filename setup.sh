#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SECRETS_DIR="${ROOT_DIR}/secrets"
PRIVATE_KEY_PATH="${SECRETS_DIR}/dev-jwt-private.pem"
PUBLIC_KEY_PATH="${SECRETS_DIR}/dev-jwt-public.pem"

msg() { printf "\033[1;32m[setup]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[setup]\033[0m %s\n" "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1. Please install it and retry."
}

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${ROOT_DIR}/.env.example" ]]; then
      cp "${ROOT_DIR}/.env.example" "${ENV_FILE}"
      msg "Created .env from .env.example"
    else
      err ".env.example not found; cannot scaffold environment."
    fi
  else
    msg ".env already exists; leaving it in place"
  fi
}

ensure_env_var() {
  local key="$1"
  local value="$2"
  python - "$ENV_FILE" "$key" "$value" <<'PY'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
key, value = sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines() if path.exists() else []
out = []
found = False
for line in lines:
    if not line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    current_key = line.split("=", 1)[0]
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

ensure_default() {
  local key="$1"
  local value="$2"
  if ! grep -E "^${key}=" "${ENV_FILE}" >/dev/null 2>&1; then
    ensure_env_var "$key" "$value"
    msg "Set default ${key}"
  fi
}

normalize_database_url() {
  local current
  current="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  if [[ "$current" == *"@localhost:"* || "$current" == *"@127.0.0.1:"* ]]; then
    local updated="${current/@localhost:/@db:}"
    updated="${updated/@127.0.0.1:/@db:}"
    ensure_env_var "DATABASE_URL" "$updated"
    warn "Updated DATABASE_URL to use service host 'db' for docker-compose"
  fi
}

normalize_redis_url() {
  local current
  current="$(grep -E '^REDIS_URL=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  if [[ "$current" == *"@localhost"* || "$current" == *"@127.0.0.1"* ]]; then
    local updated="${current/localhost/redis}"
    updated="${updated/127.0.0.1/redis}"
    ensure_env_var "REDIS_URL" "$updated"
    warn "Updated REDIS_URL to use service host 'redis' for docker-compose"
  fi
}

generate_secret_key() {
  local current
  current="$(grep -E '^SECRET_KEY=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  if [[ -n "$current" && "$current" != dev-secret* && ${#current} -ge 32 ]]; then
    msg "SECRET_KEY already set; leaving it in place"
    return
  fi
  local generated
  generated="$(python - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(64)))
PY
)"
  ensure_env_var "SECRET_KEY" "$generated"
  msg "Generated strong SECRET_KEY in .env"
}

generate_rsa_keys() {
  mkdir -p "${SECRETS_DIR}"
  if [[ ! -f "${PRIVATE_KEY_PATH}" ]]; then
    msg "Generating RSA private key (2048-bit)"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${PRIVATE_KEY_PATH}" >/dev/null 2>&1
  else
    msg "Private key already exists; leaving it in place"
  fi
  if [[ ! -f "${PUBLIC_KEY_PATH}" ]]; then
    msg "Deriving RSA public key"
    openssl rsa -in "${PRIVATE_KEY_PATH}" -pubout -out "${PUBLIC_KEY_PATH}" >/dev/null 2>&1
  else
    msg "Public key already exists; leaving it in place"
  fi
}

main() {
  require_cmd python
  require_cmd openssl

  ensure_env_file
  ensure_default "DATABASE_URL" "postgresql+asyncpg://sole:sole@db:5432/sole"
  ensure_default "REDIS_URL" "redis://redis:6379/0"
  ensure_default "TENANCY_MODE" "single"
  ensure_default "SESSION_TIMEOUT_MINUTES" "30"
  ensure_default "ACCESS_TOKEN_EXPIRE_MINUTES" "15"
  ensure_default "REFRESH_TOKEN_EXPIRE_MINUTES" "10080"
  ensure_default "ALLOWED_ORIGINS" "[\"http://localhost:3000\"]"
  ensure_default "LOG_LEVEL" "INFO"
  ensure_default "ENABLE_HSTS" "false"
  ensure_default "RATE_LIMIT_PER_MINUTE" "60"
  ensure_default "LOGIN_ATTEMPT_LIMIT" "5"
  ensure_default "LOGIN_LOCKOUT_MINUTES" "15"
  ensure_default "DEFAULT_PASSWORD_MIN_LENGTH" "12"
  ensure_default "POSTGRES_USER" "sole"
  ensure_default "POSTGRES_PASSWORD" "sole"
  ensure_default "POSTGRES_DB" "sole"
  normalize_database_url
  normalize_redis_url
  generate_secret_key
  generate_rsa_keys

  ensure_env_var "JWT_PRIVATE_KEY_PATH" "${PRIVATE_KEY_PATH}"
  ensure_env_var "JWT_PUBLIC_KEY_PATH" "${PUBLIC_KEY_PATH}"
  ensure_env_var "JWT_ALGORITHM" "RS256"

  msg "Setup complete. Next steps:"
  msg "  - Review .env to ensure values match your environment"
  msg "  - Run: docker compose up --build"
}

main "$@"
