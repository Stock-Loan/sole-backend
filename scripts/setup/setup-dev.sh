#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SECRETS_DIR="${ROOT_DIR}/secrets"
PRIVATE_KEY_PATH="${SECRETS_DIR}/dev-jwt-private.pem"
PUBLIC_KEY_PATH="${SECRETS_DIR}/dev-jwt-public.pem"
PRIVATE_KEY_ENV_PATH="./secrets/dev-jwt-private.pem"
PUBLIC_KEY_ENV_PATH="./secrets/dev-jwt-public.pem"
NEW_ENV_CREATED=0

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
    : > "${ENV_FILE}"
    NEW_ENV_CREATED=1
    msg "Created empty .env"
  else
    msg "Found existing .env file."
  fi
}

add_section() {
  local section_name="$1"
  echo "" >> "${ENV_FILE}"
  echo "# ==========================================" >> "${ENV_FILE}"
  echo "# ${section_name}" >> "${ENV_FILE}"
  echo "# ==========================================" >> "${ENV_FILE}"
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

        # Check if value is empty
        if [[ -z "$value" ]]; then
            warn "Value cannot be empty. Please provide a value or accept the default."
            continue
        fi

        # Check validation regex if provided
        if [[ -n "$validation_regex" && ! "$value" =~ $validation_regex ]]; then
             warn "$error_msg"
             continue
        fi

        ensure_env_var "$var_name" "$value"
        break
    done
}

# --- Helper Functions ---

format_origins_as_json() {
    local input="$1"
  python3 - "$input" <<'PY'
import sys, json
origins_input = sys.argv[1].strip()
if origins_input.startswith('['):
  print(origins_input)
else:
  origins = [o.strip() for o in origins_input.split(',') if o.strip()]
  print(json.dumps(origins))
PY
}

format_boolean() {
    local input="$1"
    python3 - "$input" <<'PY'
import sys
val = sys.argv[1].strip().lower()
print("true" if val in ("true", "yes", "1", "on") else "false")
PY
}

format_uppercase() {
    local input="$1"
    echo "$input" | tr '[:lower:]' '[:upper:]'
}

format_lowercase() {
    local input="$1"
    echo "$input" | tr '[:upper:]' '[:lower:]'
}

format_title_case() {
    local input="$1"
    python3 - "$input" <<'PY'
import sys
text = sys.argv[1]
print(' '.join(word.capitalize() for word in text.split()))
PY
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
    
    local current_mode
    current_mode="$(grep -E "^TENANCY_MODE=" "${ENV_FILE}" | cut -d= -f2- || true)"
    local suggestion_mode="${current_mode:-multi}"
    
    while true; do
        printf "\nApp Mode (single/multi):\n"
        read -r -p "Value [${suggestion_mode}]: " mode_input
        local mode_value="${mode_input:-$suggestion_mode}"
        
        if [[ -z "$mode_value" ]]; then
            warn "Value cannot be empty."
            continue
        fi
        
        if ! [[ "$mode_value" =~ ^(single|multi)$ ]]; then
            warn "Mode must be 'single' or 'multi'."
            continue
        fi
        
        local formatted_mode
        formatted_mode="$(format_lowercase "$mode_value")"
        ensure_env_var "TENANCY_MODE" "$formatted_mode"
        break
    done

    local mode
    mode="$(grep -E "^TENANCY_MODE=" "${ENV_FILE}" | cut -d= -f2-)"
    if [[ "$mode" == "single" ]]; then
        prompt_input "DEFAULT_ORG_ID" \
            "Default Organization ID (for single tenancy):" \
            "sole-llc"
        prompt_input "DEFAULT_ORG_NAME" \
            "Default Organization Name (for single tenancy):" \
            "Sole LLC"
        prompt_input "DEFAULT_ORG_SLUG" \
            "Default Organization Slug (URL-safe, e.g., sole-llc):" \
            "sole-llc"
    else
      ensure_env_var "DEFAULT_ORG_ID" "sole-llc"
      ensure_env_var "DEFAULT_ORG_NAME" "Sole LLC"
      ensure_env_var "DEFAULT_ORG_SLUG" "sole-llc"
    fi
}

configure_app_settings() {
    msg "--- Application Settings ---"

  prompt_input "ENVIRONMENT" \
    "Environment (development/staging/production):" \
    "development" \
    "^(development|staging|production)$" \
    "Must be one of: development, staging, production."
    
    # Handle ALLOWED_ORIGINS with conversion to JSON
    local current_origins
    current_origins="$(grep -E "^ALLOWED_ORIGINS=" "${ENV_FILE}" | cut -d= -f2- | tr -d '"' || true)"
    local suggestion_origins="${current_origins:-http://localhost:5173}"
    if [[ "$suggestion_origins" == *'${input}'* ]]; then
      suggestion_origins="http://localhost:5173"
    fi
    
    while true; do
        printf "\nFrontend Origins (CORS Allowed Hosts):\n"
        printf "  - Single: http://localhost:5173\n"
        printf "  - Multiple: http://localhost:5173, https://app.example.com\n"
        printf "  - Or JSON: [\"http://localhost:5173\"]\n"
        read -r -p "Value [${suggestion_origins}]: " origins_input
        local origins_value="${origins_input:-$suggestion_origins}"
        
        if [[ -z "$origins_value" ]]; then
            warn "Value cannot be empty."
            continue
        fi
        
        # Convert to JSON format
        local json_origins
        json_origins="$(format_origins_as_json "$origins_value")"
        ensure_env_var "ALLOWED_ORIGINS" "$json_origins"
        break
    done
    
    # ENABLE_HSTS - format as lowercase boolean
    local current_hsts
    current_hsts="$(grep -E "^ENABLE_HSTS=" "${ENV_FILE}" | cut -d= -f2- || true)"
    local suggestion_hsts="${current_hsts:-true}"
    
    while true; do
        printf "\nEnable HSTS (Strict-Transport-Security)? (true/false):\n"
        read -r -p "Value [${suggestion_hsts}]: " hsts_input
        local hsts_value="${hsts_input:-$suggestion_hsts}"
        
        if [[ -z "$hsts_value" ]]; then
            warn "Value cannot be empty."
            continue
        fi
        
        if ! [[ "$hsts_value" =~ ^(true|false|yes|no|1|0|on|off)$ ]]; then
            warn "Must be 'true' or 'false'."
            continue
        fi
        
        local formatted_hsts
        formatted_hsts="$(format_boolean "$hsts_value")"
        ensure_env_var "ENABLE_HSTS" "$formatted_hsts"
        break
    done
    
    # LOG_LEVEL - format as uppercase
    local current_log_level
    current_log_level="$(grep -E "^LOG_LEVEL=" "${ENV_FILE}" | cut -d= -f2- || true)"
    local suggestion_log_level="${current_log_level:-INFO}"
    
    while true; do
        printf "\nLog Level (DEBUG/INFO/WARNING/ERROR):\n"
        read -r -p "Value [${suggestion_log_level}]: " log_input
        local log_value="${log_input:-$suggestion_log_level}"
        
        if [[ -z "$log_value" ]]; then
            warn "Value cannot be empty."
            continue
        fi
        
        if ! [[ "$log_value" =~ ^(DEBUG|INFO|WARNING|ERROR|CRITICAL|debug|info|warning|error|critical)$ ]]; then
            warn "Invalid log level."
            continue
        fi
        
        local formatted_log
        formatted_log="$(format_uppercase "$log_value")"
        ensure_env_var "LOG_LEVEL" "$formatted_log"
        break
    done
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
  msg "All values must be provided. Defaults are pre-filled and can be overridden."
  ensure_env_file

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Database Configuration"
  fi
  configure_database

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Docker Compose Services (not read by app)"
  fi
  msg "--- Docker Compose Configuration ---"
  prompt_input "POSTGRES_USER" \
      "PostgreSQL username:" \
      "sole"
  prompt_input "POSTGRES_PASSWORD" \
      "PostgreSQL password:" \
      "sole"
  prompt_input "POSTGRES_DB" \
      "PostgreSQL database name:" \
      "sole"

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Application Settings"
  fi
  configure_app_settings

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Tenancy Configuration"
  fi
  configure_tenancy

  msg ""
  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Authentication & Security"
  fi
  msg "--- Security Keys ---"
  generate_secret_key
  generate_rsa_keys
  ensure_env_var "JWT_PRIVATE_KEY_PATH" "${PRIVATE_KEY_ENV_PATH}"
  ensure_env_var "JWT_PUBLIC_KEY_PATH" "${PUBLIC_KEY_ENV_PATH}"
  ensure_env_var "JWT_ALGORITHM" "RS256"

  msg ""
  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Token & Session Configuration"
  fi
  msg "--- Token & Session Configuration ---"
  prompt_input "SESSION_TIMEOUT_MINUTES" \
      "Session timeout (in minutes):" \
      "30" \
      "^[0-9]+$" \
      "Must be a number."
  prompt_input "ACCESS_TOKEN_EXPIRE_MINUTES" \
      "Access token expiration (in minutes):" \
      "15" \
      "^[0-9]+$" \
      "Must be a number."
  prompt_input "REFRESH_TOKEN_EXPIRE_MINUTES" \
      "Refresh token expiration (in minutes):" \
      "10080" \
      "^[0-9]+$" \
      "Must be a number."
  prompt_input "DEFAULT_PASSWORD_MIN_LENGTH" \
      "Minimum password length:" \
      "12" \
      "^[0-9]+$" \
      "Must be a number."

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Rate Limiting & Protection"
  fi
  prompt_input "RATE_LIMIT_PER_MINUTE" \
      "Rate limit (requests per minute):" \
      "60" \
      "^[0-9]+$" \
      "Must be a number."
  prompt_input "LOGIN_ATTEMPT_LIMIT" \
      "Login attempt limit before lockout:" \
      "5" \
      "^[0-9]+$" \
      "Must be a number."
  prompt_input "LOGIN_LOCKOUT_MINUTES" \
      "Login lockout duration (in minutes):" \
      "15" \
      "^[0-9]+$" \
      "Must be a number."
  configure_proxy_settings

  msg ""
  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "Seed Admin Credentials"
  fi
  msg "--- Seed Admin Configuration ---"
  
  # SEED_ADMIN_EMAIL - format as lowercase
  local current_email
  current_email="$(grep -E "^SEED_ADMIN_EMAIL=" "${ENV_FILE}" | cut -d= -f2- || true)"
  local suggestion_email="${current_email:-admin@example.com}"
  
  while true; do
    printf "\nSeed admin email address:\n"
    read -r -p "Value [${suggestion_email}]: " email_input
    local email_value="${email_input:-$suggestion_email}"
    
    if [[ -z "$email_value" ]]; then
      warn "Email cannot be empty."
      continue
    fi
    
    if ! [[ "$email_value" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
      warn "Invalid email address."
      continue
    fi
    
    local formatted_email
    formatted_email="$(format_lowercase "$email_value")"
    ensure_env_var "SEED_ADMIN_EMAIL" "$formatted_email"
    break
  done
  
  prompt_input "SEED_ADMIN_PASSWORD" \
      "Seed admin password (min 8 chars):" \
      "ChangeMe123!" \
      "^.{8,}$" \
      "Password must be at least 8 characters."
  
  # SEED_ADMIN_FULL_NAME - format as title case
  local current_full_name
  current_full_name="$(grep -E "^SEED_ADMIN_FULL_NAME=" "${ENV_FILE}" | cut -d= -f2- || true)"
  local suggestion_full_name="${current_full_name:-Admin User}"
  
  while true; do
    printf "\nSeed admin full name:\n"
    read -r -p "Value [${suggestion_full_name}]: " fullname_input
    local fullname_value="${fullname_input:-$suggestion_full_name}"
    
    if [[ -z "$fullname_value" ]]; then
      warn "Full name cannot be empty."
      continue
    fi
    
    local formatted_fullname
    formatted_fullname="$(format_title_case "$fullname_value")"
    ensure_env_var "SEED_ADMIN_FULL_NAME" "$formatted_fullname"
    break
  done

  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "File Upload Configuration"
  fi
  prompt_input "LOCAL_UPLOAD_DIR" \
      "Local upload directory:" \
      "local_uploads"
  
  if [[ "${NEW_ENV_CREATED}" == "1" ]]; then
    add_section "PBGC Rate Scraping Configuration"
  fi
  # PBGC_RATE_SCRAPE_ENABLED - format as lowercase boolean
  local current_pbgc
  current_pbgc="$(grep -E "^PBGC_RATE_SCRAPE_ENABLED=" "${ENV_FILE}" | cut -d= -f2- || true)"
  local suggestion_pbgc="${current_pbgc:-true}"
  
  while true; do
    printf "\nEnable PBGC rate scraping? (true/false):\n"
    read -r -p "Value [${suggestion_pbgc}]: " pbgc_input
    local pbgc_value="${pbgc_input:-$suggestion_pbgc}"
    
    if [[ -z "$pbgc_value" ]]; then
      warn "Value cannot be empty."
      continue
    fi
    
    if ! [[ "$pbgc_value" =~ ^(true|false|yes|no|1|0|on|off)$ ]]; then
      warn "Must be 'true' or 'false'."
      continue
    fi
    
    local formatted_pbgc
    formatted_pbgc="$(format_boolean "$pbgc_value")"
    ensure_env_var "PBGC_RATE_SCRAPE_ENABLED" "$formatted_pbgc"
    break
  done
  
  prompt_input "PBGC_RATE_SCRAPE_DAY" \
      "PBGC scrape day of month:" \
      "30" \
      "^(0|[1-9]|[12][0-9]|3[01])$" \
      "Must be a day (0-31)."
  prompt_input "PBGC_RATE_SCRAPE_HOUR" \
      "PBGC scrape hour (0-23):" \
      "0" \
      "^([0-9]|1[0-9]|2[0-3])$" \
      "Must be an hour (0-23)."
  prompt_input "PBGC_RATE_SCRAPE_MINUTE" \
      "PBGC scrape minute (0-59):" \
      "0" \
      "^([0-9]|[1-5][0-9])$" \
      "Must be a minute (0-59)."

  normalize_database_url

  msg "========================================"
  msg " Development setup complete."
  msg "========================================"
  msg "  - Review .env"
  msg "  - Run: docker compose up --build"
}

main "$@"
