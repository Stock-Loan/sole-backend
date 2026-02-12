from functools import lru_cache
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """A settings source that reads from a YAML file."""

    def __call__(self) -> dict[str, Any]:
        configured_file = (os.getenv("CONFIG_FILE") or "").strip()
        if configured_file:
            candidate_files = [configured_file]
        else:
            # Default to production config naming, with fallback for local variants.
            candidate_files = ["config.prod.yaml", "config.yaml"]

        config_file = next((path for path in candidate_files if os.path.exists(path)), None)
        if not config_file:
            return {}
        try:
            import yaml
        except ImportError:
            return {}

        try:
            with open(config_file, encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
                return payload if isinstance(payload, dict) else {}
        except (OSError, yaml.YAMLError):
            return {}

    def get_field_value(self, field, field_name):
        # This method is required by the abstract base class, 
        # but since we return the full dict in __call__, 
        # Pydantic's default processing will handle it.
        return None, field_name, False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", alias="ENVIRONMENT")
    database_url: str = Field(alias="DATABASE_URL")
    database_url_direct: str | None = Field(default=None, alias="DATABASE_URL_DIRECT")
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=0, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=10, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, alias="DB_POOL_RECYCLE")
    db_pool_retry_after_seconds: int = Field(default=3, alias="DB_POOL_RETRY_AFTER_SECONDS")
    db_statement_timeout_ms: int = Field(default=10000, alias="DB_STATEMENT_TIMEOUT_MS")
    db_slow_query_ms: int = Field(default=2000, alias="DB_SLOW_QUERY_MS")
    db_log_query_timings: bool = Field(default=False, alias="DB_LOG_QUERY_TIMINGS")
    redis_key_prefix: str = Field(default="sole", alias="REDIS_KEY_PREFIX")
    request_concurrency_limit: int = Field(default=0, alias="REQUEST_CONCURRENCY_LIMIT")
    request_concurrency_timeout_seconds: int = Field(
        default=0, alias="REQUEST_CONCURRENCY_TIMEOUT_SECONDS"
    )
    redis_url: str = Field(alias="REDIS_URL")
    tenancy_mode: Literal["single", "multi"] = Field(default="single", alias="TENANCY_MODE")
    session_timeout_minutes: int = Field(default=30, alias="SESSION_TIMEOUT_MINUTES")
    access_token_expire_minutes: int = Field(default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_minutes: int = Field(
        default=60 * 24 * 7, alias="REFRESH_TOKEN_EXPIRE_MINUTES"
    )
    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    enable_hsts: bool = Field(default=True, alias="ENABLE_HSTS")
    default_org_id: str = Field(default="default", alias="DEFAULT_ORG_ID")
    default_org_name: str = Field(default="Default Organization", alias="DEFAULT_ORG_NAME")
    default_org_slug: str = Field(default="default", alias="DEFAULT_ORG_SLUG")
    secret_key: str = Field(alias="SECRET_KEY", min_length=16)
    fernet_kdf_salt: str | None = Field(default=None, alias="FERNET_KDF_SALT")
    fernet_kdf_iterations: int = Field(default=210000, alias="FERNET_KDF_ITERATIONS")
    jwt_private_key: str | None = Field(default=None, alias="JWT_PRIVATE_KEY")
    jwt_public_key: str | None = Field(default=None, alias="JWT_PUBLIC_KEY")
    jwt_private_key_path: str | None = Field(default=None, alias="JWT_PRIVATE_KEY_PATH")
    jwt_public_key_path: str | None = Field(default=None, alias="JWT_PUBLIC_KEY_PATH")
    jwt_key_cache_ttl_seconds: int = Field(default=60, alias="JWT_KEY_CACHE_TTL_SECONDS")
    jwt_algorithm: Literal["RS256"] = Field(default="RS256", alias="JWT_ALGORITHM")
    allowed_tenant_hosts: list[str] = Field(default_factory=list, alias="ALLOWED_TENANT_HOSTS")
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    login_attempt_limit: int = Field(default=5, alias="LOGIN_ATTEMPT_LIMIT")
    login_lockout_minutes: int = Field(default=15, alias="LOGIN_LOCKOUT_MINUTES")
    default_password_min_length: int = Field(default=12, alias="DEFAULT_PASSWORD_MIN_LENGTH")
    proxies_count: int = Field(default=1, alias="PROXIES_COUNT")
    extra_seed_org_ids: str = Field(
        default="",
        alias="EXTRA_SEED_ORG_IDS",
        description="Comma-separated org_ids to also seed with dedicated admin users",
    )
    seed_admin_email: str = Field(alias="SEED_ADMIN_EMAIL")
    seed_admin_password: str = Field(alias="SEED_ADMIN_PASSWORD")
    seed_admin_full_name: str = Field(default="Admin User", alias="SEED_ADMIN_FULL_NAME")
    seed_skip_admin_user: bool = Field(default=False, alias="SEED_SKIP_ADMIN_USER")
    seed_demo_users_in_production: bool = Field(
        default=False,
        alias="SEED_DEMO_USERS_IN_PRODUCTION",
    )
    max_upload_size_mb: int = Field(default=50, alias="MAX_UPLOAD_SIZE_MB")
    local_upload_dir: str = Field(default="local_uploads", alias="LOCAL_UPLOAD_DIR")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")
    storage_provider: Literal["local", "gcs"] = Field(default="local", alias="STORAGE_PROVIDER")
    gcs_bucket: str | None = Field(default=None, alias="GCS_BUCKET")
    gcs_signed_url_expiry_seconds: int = Field(default=900, alias="GCS_SIGNED_URL_EXPIRY_SECONDS")
    pbgc_mid_term_rates_url: str = Field(
        default="https://www.pbgc.gov/employers-practitioners/interest-rates/historical-applicable-mid-term",
        alias="PBGC_MID_TERM_RATES_URL",
    )
    pbgc_rate_scrape_enabled: bool = Field(default=True, alias="PBGC_RATE_SCRAPE_ENABLED")
    pbgc_rate_scrape_day: int = Field(default=30, alias="PBGC_RATE_SCRAPE_DAY")
    pbgc_rate_scrape_hour: int = Field(default=0, alias="PBGC_RATE_SCRAPE_HOUR")
    pbgc_rate_scrape_minute: int = Field(default=0, alias="PBGC_RATE_SCRAPE_MINUTE")
    impersonation_max_minutes: int = Field(default=60, alias="IMPERSONATION_MAX_MINUTES")
    auth_refresh_cookie_enabled: bool = Field(default=True, alias="AUTH_REFRESH_COOKIE_ENABLED")
    auth_refresh_cookie_name: str = Field(default="sole_refresh", alias="AUTH_REFRESH_COOKIE_NAME")
    auth_csrf_cookie_name: str = Field(default="sole_csrf", alias="AUTH_CSRF_COOKIE_NAME")
    auth_csrf_header_name: str = Field(default="X-CSRF-Token", alias="AUTH_CSRF_HEADER_NAME")
    auth_cookie_domain: str | None = Field(default=None, alias="AUTH_COOKIE_DOMAIN")
    auth_cookie_path: str = Field(default="/api/v1/auth/refresh", alias="AUTH_COOKIE_PATH")
    auth_cookie_secure: bool = Field(default=True, alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax", alias="AUTH_COOKIE_SAMESITE"
    )
    content_security_policy: str | None = Field(
        default="default-src 'self'", alias="CONTENT_SECURITY_POLICY"
    )
    content_security_policy_report_only: bool = Field(
        default=False, alias="CONTENT_SECURITY_POLICY_REPORT_ONLY"
    )
    health_include_details: bool = Field(default=False, alias="HEALTH_INCLUDE_DETAILS")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    def allowed_origins_list(self) -> list[str]:
        raw = (self.allowed_origins or "").strip()
        if not raw:
            return []
        parsed_values: list[str]
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    parsed_values = [str(item).strip() for item in parsed if str(item).strip()]
                else:
                    parsed_values = []
            except json.JSONDecodeError:
                parsed_values = []
        else:
            parsed_values = [item.strip() for item in raw.split(",") if item.strip()]

        validated: list[str] = []
        for item in parsed_values:
            if item == "*":
                raise ValueError("Wildcard ALLOWED_ORIGINS ('*') is not allowed")
            parsed_origin = urlparse(item)
            if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
                raise ValueError(f"Invalid CORS origin: {item}")
            if any(
                [
                    parsed_origin.path not in {"", "/"},
                    parsed_origin.params,
                    parsed_origin.query,
                    parsed_origin.fragment,
                ]
            ):
                raise ValueError(f"CORS origin must not include path/query/fragment: {item}")
            normalized = f"{parsed_origin.scheme}://{parsed_origin.netloc}".rstrip("/")
            validated.append(normalized)
        return list(dict.fromkeys(validated))

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        normalized_env = (self.environment or "").strip().lower()
        is_production = normalized_env in {"production", "prod"}

        insecure_secret_values = {
            "replace-with-secure-random-64-char-secret",
            "dev-only-change-before-deploy",
            "changeme",
        }
        if is_production and (
            self.secret_key.strip().lower() in insecure_secret_values or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY is insecure for production; use a high-entropy secret at least 32 characters long"
            )

        insecure_seed_passwords = {
            "changeme123!",
            "admin123",
            "password",
            "password123",
            "changeme",
            "replace-with-strong-seed-admin-password1!",
            "devonlychangebeforedeploy123!",
        }
        if is_production and self.seed_admin_password.strip().lower() in insecure_seed_passwords:
            raise ValueError(
                "SEED_ADMIN_PASSWORD is insecure for production; configure a strong, unique password"
            )

        salt = (self.fernet_kdf_salt or "").strip()
        if is_production and len(salt) < 16:
            raise ValueError(
                "FERNET_KDF_SALT is required in production and must be at least 16 characters"
            )

        if not re.fullmatch(r"[a-zA-Z0-9:_-]+", self.redis_key_prefix or ""):
            raise ValueError("REDIS_KEY_PREFIX may only contain letters, numbers, ':', '_' and '-'")

        if self.fernet_kdf_iterations < 100_000:
            raise ValueError("FERNET_KDF_ITERATIONS must be at least 100000")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load application settings from environment variables.

    Settings are cached for the process lifetime. Any environment variable
    changes require a full process restart to take effect.
    """
    return Settings()


settings = get_settings()
