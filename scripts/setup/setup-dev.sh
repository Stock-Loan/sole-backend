#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SECRETS_DIR="${ROOT_DIR}/secrets"
PRIVATE_KEY_PATH="${SECRETS_DIR}/dev-jwt-private.pem"
PUBLIC_KEY_PATH="${SECRETS_DIR}/dev-jwt-public.pem"

# --- Formatting Helpers ---
msg() { printf "\033[1;32m[setup-dev]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup-dev]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[setup-dev]\033[0m %s\n" "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1. Please install it and retry."
}

# --- Environment File Helpers ---

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f "${ROOT_DIR}/.env.example" ]]; then
      cp "${ROOT_DIR}/.env.example" "${ENV_FILE}"
      msg "Created .env from .env.example"
    else
      err ".env.example not found; cannot scaffold environment."
    fi
  else
    msg "Found existing .env file."
  fi
}

ensure_env_var() {
  local key="$1"
  local value="$2"
  python3 - "${ENV_FILE}" "$key" "$value" <<'PY'
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
  fi
}

# --- Interactive Prompts ---

prompt_input() {
    local var_name="$1"
    local prompt_text="$2"
    local default_val="$3"
    local validation_regex="${4:-}"
    local error_msg="${5:-Invalid input}"

    local current_val
    current_val="$(grep -E "^${var_name}=" "${ENV_FILE}" | cut -d= -f2- | tr -d '"' || true)"
    local suggestion="${current_val:-$default_val}"

    while true; do
        printf "\n%s\n" "$prompt_text"
        read -r -p "Value [${suggestion}]: " input
        local value="${input:-$suggestion}"

        if [[ -n "$validation_regex" && ! "$value" =~ $validation_regex ]]; then
             warn "$error_msg"
        else
             ensure_env_var "$var_name" "$value"
             break
        fi
    done
}

configure_database() {
    msg "--- Database Configuration ---"
    prompt_input "DATABASE_URL" \
        "Enter the PostgreSQL Connection String:" \
        "postgresql+asyncpg://sole:sole@db:5432/sole"
        
    prompt_input "REDIS_URL" \
        "Enter the Redis Connection String:" \
        "redis://redis:6379/0"
}

configure_tenancy() {
    msg "--- Tenancy Configuration ---"
    prompt_input "TENANCY_MODE" \
        "App Mode (single/multi):" \
        "single" \
        "^(single|multi)$" \
        "Mode must be 'single' or 'multi'."

    local mode
    mode="$(grep -E "^TENANCY_MODE=" "${ENV_FILE}" | cut -d= -f2-)"
    if [[ "$mode" == "single" ]]; then
        prompt_input "DEFAULT_ORG_ID" \
            "Default Organization ID (for single tenancy):" \
            "default"
    fi
}

configure_app_settings() {
    msg "--- Application Settings ---"
    prompt_input "ALLOWED_ORIGINS" \
        "Frontend Origin (CORS Allowed Hosts, format: [\"url\"]):" \
        '["http://localhost:3000"]'
    prompt_input "ENABLE_HSTS" \
        "Enable HSTS (Strict-Transport-Security)? (true/false):" \
        "false" \
        "^(true|false)$" \
        "Must be 'true' or 'false'."
    prompt_input "LOG_LEVEL" \
        "Log Level (DEBUG/INFO/WARNING/ERROR):" \
        "INFO" \
        "^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$" \
        "Invalid log level."
}

configure_proxy_settings() {
  if [[ ! -t 0 ]]; then
    return
  fi

  local current
  current="$(grep -E '^PROXIES_COUNT=' "${ENV_FILE}" | cut -d= -f2- || echo "1")"
  msg ""
  msg "--- Proxy Configuration ---"
  msg "Current PROXIES_COUNT: ${current}"
  printf "Do you want to change the proxy trust settings? [y/N] "
  read -r response
  if [[ ! "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    return
  fi

  printf "\nSelect your hosting environment:\n"
  printf "  1) Render / Heroku / AWS ALB (Standard PaaS) -> Sets 1\n"
  printf "  2) Direct Connection / VPS / Localhost -> Sets 0\n"
  printf "  3) Cloudflare -> Nginx/LB -> App -> Sets 2\n"
  printf "  4) Custom\n"
  read -p "Choice [1]: " choice
  choice=${choice:-1}
  local count="1"
  case "$choice" in
    1) count="1" ;;
    2) count="0" ;;
    3) count="2" ;;
    4) read -p "Enter integer value for PROXIES_COUNT: " count ;;
    *) count="1" ;;
  esac
  ensure_env_var "PROXIES_COUNT" "$count"
  msg "Updated PROXIES_COUNT to ${count}"
}

# --- Auto-Generators (Security) ---

generate_secret_key() {
  local current
  current="$(grep -E '^SECRET_KEY=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  if [[ -n "$current" && "$current" != dev-secret* && ${#current} -ge 32 ]]; then
    msg "SECRET_KEY already set; leaving it in place"
    return
  fi
  local generated
  generated="$(python3 - <<'PY'
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
    msg "Generating RSA private key (4096-bit)"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "${PRIVATE_KEY_PATH}" >/dev/null 2>&1
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

normalize_database_url() {
  local current
  current="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  if [[ "$current" == *"@localhost:"* || "$current" == *"@127.0.0.1:"* ]]; then
    warn "Detected localhost in DATABASE_URL. If running in Docker Compose, consider '@db:5432'."
  fi
}

# --- Main ---

main() {
  require_cmd python3
  require_cmd openssl

  msg "========================================"
  msg " SOLE Backend Setup (Development)"
  msg "========================================"
  msg "Press [Enter] to accept default values."
  ensure_env_file

  configure_database
  configure_tenancy
  configure_app_settings
  configure_proxy_settings

  msg ""
  msg "--- Security Keys ---"
  generate_secret_key
  generate_rsa_keys
  ensure_env_var "JWT_PRIVATE_KEY_PATH" "${PRIVATE_KEY_PATH}"
  ensure_env_var "JWT_PUBLIC_KEY_PATH" "${PUBLIC_KEY_PATH}"
  ensure_env_var "JWT_ALGORITHM" "RS256"

  ensure_default "SESSION_TIMEOUT_MINUTES" "30"
  ensure_default "ACCESS_TOKEN_EXPIRE_MINUTES" "15"
  ensure_default "REFRESH_TOKEN_EXPIRE_MINUTES" "10080"
  ensure_default "ALLOWED_TENANT_HOSTS" "[]"
  ensure_default "RATE_LIMIT_PER_MINUTE" "60"
  ensure_default "LOGIN_ATTEMPT_LIMIT" "5"
  ensure_default "LOGIN_LOCKOUT_MINUTES" "15"
  ensure_default "DEFAULT_PASSWORD_MIN_LENGTH" "12"
  ensure_default "POSTGRES_USER" "sole"
  ensure_default "POSTGRES_PASSWORD" "sole"
  ensure_default "POSTGRES_DB" "sole"

  normalize_database_url

  msg "========================================"
  msg " Development setup complete."
  msg "========================================"
  msg "  - Review .env"
  msg "  - Run: docker compose up --build"
}

main "$@"
