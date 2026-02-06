#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.prod}"

# --- Formatting Helpers ---
msg() { printf "\033[1;32m[setup-prod]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup-prod]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[setup-prod]\033[0m %s\n" "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1. Please install it and retry."
}

# --- Environment File Helpers ---

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

add_section() {
  local section_name="$1"
  echo "" >> "$ENV_FILE"
  echo "# ==========================================" >> "$ENV_FILE"
  echo "# ${section_name}" >> "$ENV_FILE"
  echo "# ==========================================" >> "$ENV_FILE"
}

# --- Interactive Prompts (ALL REQUIRED, NO DEFAULTS) ---

prompt_required() {
    local var_name="$1"
    local prompt_text="$2"
    local validation_regex="${3:-}"
    local error_msg="${4:-Invalid input}"

    while true; do
        printf "\n%s\n" "$prompt_text"
        read -r -p "Value: " input

        # Check if value is empty
        if [[ -z "$input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi

        # Check validation regex if provided
        if [[ -n "$validation_regex" && ! "$input" =~ $validation_regex ]]; then
             warn "$error_msg"
             continue
        fi

        write_env_var "$var_name" "$input"
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
    prompt_required "DATABASE_URL" \
        "Enter the POOLED PostgreSQL URL (Neon pooler, hostname includes -pooler). Example: postgresql+psycopg://user:pass@host/dbname" \
        "^postgres(ql)?(\\+psycopg)?://" \
        "Must be a valid PostgreSQL URL starting with 'postgresql+psycopg://' or 'postgresql://'"

    prompt_required "DATABASE_URL_DIRECT" \
        "Enter the DIRECT PostgreSQL URL (non-pooler, used for migrations/admin). Example: postgresql+psycopg://user:pass@host/dbname" \
        "^postgres(ql)?(\\+psycopg)?://" \
        "Must be a valid PostgreSQL URL starting with 'postgresql+psycopg://' or 'postgresql://'"
        
    prompt_required "REDIS_URL" \
        "Enter the Redis Connection String (e.g., redis://user:pass@host:6379/0):" \
        "^redis://" \
        "Must be a valid Redis URL starting with 'redis://'"
}

configure_tenancy() {
    msg "--- Tenancy Configuration ---"
    
    # TENANCY_MODE - format as lowercase
    while true; do
        printf "\nTenancy Mode (single/multi):\n"
        read -r -p "Value: " mode_input
        
        if [[ -z "$mode_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$mode_input" =~ ^(single|multi)$ ]] && ! [[ "$mode_input" =~ ^(SINGLE|MULTI)$ ]]; then
            warn "Mode must be 'single' or 'multi'."
            continue
        fi
        
        local formatted_mode
        formatted_mode="$(format_lowercase "$mode_input")"
        write_env_var "TENANCY_MODE" "$formatted_mode"
        break
    done

    local mode
    mode="$(grep -E "^TENANCY_MODE=" "${ENV_FILE}" | cut -d= -f2-)"
    if [[ "$mode" == "single" ]]; then
        prompt_required "DEFAULT_ORG_ID" \
            "Default Organization ID (for single tenancy):"
        prompt_required "DEFAULT_ORG_NAME" \
            "Default Organization Name (for single tenancy):"
        prompt_required "DEFAULT_ORG_SLUG" \
            "Default Organization Slug (URL-safe):"
    else
        # For multi-tenancy, set defaults
        write_env_var "DEFAULT_ORG_ID" "sole-llc"
        write_env_var "DEFAULT_ORG_NAME" "Sole LLC"
        write_env_var "DEFAULT_ORG_SLUG" "sole-llc"
    fi
}

configure_app_settings() {
    msg "--- Application Settings ---"
    
    # Handle ALLOWED_ORIGINS with conversion to JSON
    while true; do
        printf "\nFrontend Origins (CORS Allowed Hosts):\n"
        printf "  - Single: https://app.example.com\n"
        printf "  - Multiple: https://app.example.com, https://api.example.com\n"
        printf "  - Or JSON: [\"https://app.example.com\"]\n"
        read -r -p "Value: " origins_input
        
        if [[ -z "$origins_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        # Convert to JSON format
        local json_origins
        json_origins="$(format_origins_as_json "$origins_input")"
        write_env_var "ALLOWED_ORIGINS" "$json_origins"
        break
    done
    
    # ENABLE_HSTS - format as lowercase boolean
    while true; do
        printf "\nEnable HSTS (Strict-Transport-Security)? (true/false):\n"
        read -r -p "Value: " hsts_input
        
        if [[ -z "$hsts_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$hsts_input" =~ ^(true|false|yes|no|1|0|on|off)$ ]]; then
            warn "Must be 'true' or 'false'."
            continue
        fi
        
        local formatted_hsts
        formatted_hsts="$(format_boolean "$hsts_input")"
        write_env_var "ENABLE_HSTS" "$formatted_hsts"
        break
    done
    
    # LOG_LEVEL - format as uppercase
    while true; do
        printf "\nLog Level (DEBUG/INFO/WARNING/ERROR/CRITICAL):\n"
        read -r -p "Value: " log_input
        
        if [[ -z "$log_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$log_input" =~ ^(debug|info|warning|error|critical)$ ]] && ! [[ "$log_input" =~ ^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$ ]]; then
            warn "Must be 'DEBUG', 'INFO', 'WARNING', 'ERROR', or 'CRITICAL'."
            continue
        fi
        
        local formatted_log
        formatted_log="$(format_uppercase "$log_input")"
        write_env_var "LOG_LEVEL" "$formatted_log"
        break
    done
}

configure_security() {
    msg "--- Authentication & Security ---"
    prompt_required "SECRET_KEY" \
        "SECRET_KEY (min 32 chars, or press '!' to auto-generate):" \
        "^(.{32,}|!|auto)$" \
        "Secret key must be at least 32 characters or '!' to auto-generate."
    
    # Check if user wants auto-generation
    local secret_key_value
    secret_key_value="$(grep -E "^SECRET_KEY=" "${ENV_FILE}" | cut -d= -f2-)"
    if [[ "$secret_key_value" == "!" || "$secret_key_value" == "auto" ]]; then
        msg "Auto-generating SECRET_KEY..."
        generate_secret_key
    fi
    
    # JWT_ALGORITHM - format as uppercase
    while true; do
        printf "\nJWT Algorithm (RS256 recommended, or HS256):\n"
        read -r -p "Value: " algo_input
        
        if [[ -z "$algo_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$algo_input" =~ ^(RS256|HS256)$ ]] && ! [[ "$algo_input" =~ ^(rs256|hs256)$ ]]; then
            warn "Only 'RS256' or 'HS256' are supported."
            continue
        fi
        
        local formatted_algo
        formatted_algo="$(format_uppercase "$algo_input")"
        write_env_var "JWT_ALGORITHM" "$formatted_algo"
        break
    done
    
    # Ask about key generation
    msg ""
    msg "JWT Key Options:"
    msg "  1) Auto-generate RSA keys (recommended)"
    msg "  2) Provide inline PEM format keys"
    printf "\nChoice [1]: "
    read -r key_choice
    key_choice=${key_choice:-1}
    
    if [[ "$key_choice" == "1" ]]; then
        msg ""
        msg "Auto-generating RSA keys..."
        generate_rsa_keys
    else
        prompt_required "JWT_PRIVATE_KEY" \
            "JWT PRIVATE KEY (inline PEM format, RSA 4096):" \
            "-----BEGIN" \
            "Must be a valid PEM private key starting with '-----BEGIN'."
        
        prompt_required "JWT_PUBLIC_KEY" \
            "JWT PUBLIC KEY (inline PEM format, RSA 4096):" \
            "-----BEGIN" \
            "Must be a valid PEM public key starting with '-----BEGIN'."
    fi
}

configure_tokens_and_limits() {
    msg "--- Token & Session Configuration ---"
    prompt_required "SESSION_TIMEOUT_MINUTES" \
        "Session timeout (in minutes):" \
        "^[0-9]+$" \
        "Must be a number."
    
    prompt_required "ACCESS_TOKEN_EXPIRE_MINUTES" \
        "Access token expiration (in minutes):" \
        "^[0-9]+$" \
        "Must be a number."
    
    prompt_required "REFRESH_TOKEN_EXPIRE_MINUTES" \
        "Refresh token expiration (in minutes):" \
        "^[0-9]+$" \
        "Must be a number."
    
    prompt_required "DEFAULT_PASSWORD_MIN_LENGTH" \
        "Minimum password length:" \
        "^[0-9]+$" \
        "Must be a number."

    msg ""
    msg "--- Rate Limiting & Protection ---"
    prompt_required "RATE_LIMIT_PER_MINUTE" \
        "Rate limit (requests per minute):" \
        "^[0-9]+$" \
        "Must be a number."
    
    prompt_required "LOGIN_ATTEMPT_LIMIT" \
        "Login attempt limit before lockout:" \
        "^[0-9]+$" \
        "Must be a number."
    
    prompt_required "LOGIN_LOCKOUT_MINUTES" \
        "Login lockout duration (in minutes):" \
        "^[0-9]+$" \
        "Must be a number."
}

configure_advanced() {
    msg "--- Advanced Configuration ---"

    
    prompt_required "LOCAL_UPLOAD_DIR" \
        "Local upload directory (absolute path recommended):"
    
    # PBGC_RATE_SCRAPE_ENABLED - format as lowercase boolean
    while true; do
        printf "\nEnable PBGC rate scraping? (true/false):\n"
        read -r -p "Value: " pbgc_input
        
        if [[ -z "$pbgc_input" ]]; then
            warn "Value cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$pbgc_input" =~ ^(true|false|yes|no|1|0|on|off)$ ]]; then
            warn "Must be 'true' or 'false'."
            continue
        fi
        
        local formatted_pbgc
        formatted_pbgc="$(format_boolean "$pbgc_input")"
        write_env_var "PBGC_RATE_SCRAPE_ENABLED" "$formatted_pbgc"
        break
    done
    
    prompt_required "PBGC_RATE_SCRAPE_DAY" \
        "PBGC scrape day of month (0-31):" \
        "^(0|[1-9]|[12][0-9]|3[01])$" \
        "Must be a day (0-31)."
    
    prompt_required "PBGC_RATE_SCRAPE_HOUR" \
        "PBGC scrape hour (0-23):" \
        "^([0-9]|1[0-9]|2[0-3])$" \
        "Must be an hour (0-23)."
    
    prompt_required "PBGC_RATE_SCRAPE_MINUTE" \
        "PBGC scrape minute (0-59):" \
        "^([0-9]|[1-5][0-9])$" \
        "Must be a minute (0-59)."
}

# --- Auto-Generators (Security) ---

generate_secret_key() {
  local generated
  generated="$(python3 - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(64)))
PY
)"
  write_env_var "SECRET_KEY" "$generated"
  msg "✓ Generated strong SECRET_KEY"
}

generate_rsa_keys() {
  local temp_private="/tmp/prod-jwt-private.pem"
  local temp_public="/tmp/prod-jwt-public.pem"
  
  msg "Generating RSA keys (4096-bit)... this may take a moment"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$temp_private" >/dev/null 2>&1
  msg "✓ Generated RSA private key"
  
  openssl rsa -in "$temp_private" -pubout -out "$temp_public" >/dev/null 2>&1
  msg "✓ Generated RSA public key"
  
  # Read the keys and store them inline in the env file
  local private_key
  local public_key
  private_key="$(cat "$temp_private")"
  public_key="$(cat "$temp_public")"
  
  write_env_var "JWT_PRIVATE_KEY" "$private_key"
  write_env_var "JWT_PUBLIC_KEY" "$public_key"
  
  # Cleanup temp files
  rm -f "$temp_private" "$temp_public"
}

configure_seed_admin() {
    msg "--- Seed Admin Credentials ---"
    
    # SEED_ADMIN_EMAIL - format as lowercase
    while true; do
        printf "\nSeed admin email address:\n"
        read -r -p "Value: " email_input
        
        if [[ -z "$email_input" ]]; then
            warn "Email cannot be empty. This field is required."
            continue
        fi
        
        if ! [[ "$email_input" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
            warn "Invalid email address."
            continue
        fi
        
        local formatted_email
        formatted_email="$(format_lowercase "$email_input")"
        write_env_var "SEED_ADMIN_EMAIL" "$formatted_email"
        break
    done
    
    prompt_required "SEED_ADMIN_PASSWORD" \
        "Seed admin password (min 12 chars for production):" \
        "^.{12,}$" \
        "Password must be at least 12 characters for production."
    
    # SEED_ADMIN_FULL_NAME - format as title case
    while true; do
        printf "\nSeed admin full name:\n"
        read -r -p "Value: " fullname_input
        
        if [[ -z "$fullname_input" ]]; then
            warn "Full name cannot be empty. This field is required."
            continue
        fi
        
        local formatted_fullname
        formatted_fullname="$(format_title_case "$fullname_input")"
        write_env_var "SEED_ADMIN_FULL_NAME" "$formatted_fullname"
        break
    done
}

# --- Main ---

main() {
  require_cmd python3

  msg "========================================"
  msg " SOLE Backend Setup (Production)"
  msg "========================================"
  msg "ALL VALUES ARE REQUIRED. No defaults will be applied."
  msg "You will be prompted for every configuration parameter."

  # Create empty env file
  : > "$ENV_FILE"
  
  add_section "Database Configuration"
  configure_database
  
  add_section "Application Settings"
  configure_app_settings
  
  add_section "Tenancy Configuration"
  configure_tenancy
  
  add_section "Authentication & Security"
  configure_security
  
  add_section "Token & Session Configuration"
  configure_tokens_and_limits
  
  add_section "Seed Admin Credentials"
  configure_seed_admin
  
  add_section "Advanced Configuration"
  configure_advanced

  msg ""
  msg "========================================"
  msg " Production .env.prod created"
  msg "========================================"
  msg "Location: ${ENV_FILE}"
  msg ""
  msg "Next steps:"
  msg "  1. Review .env.prod for accuracy"
  msg "  2. Ensure DATABASE_URL, DATABASE_URL_DIRECT, and REDIS_URL are correct"
  msg "  3. Verify JWT keys are valid PEM format"
  msg "  4. Run: docker compose -f compose.yaml up"
}

main "$@"
