from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir="/run/secrets",
    )

    environment: str = Field(default="development", alias="ENVIRONMENT")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    tenancy_mode: Literal["single", "multi"] = Field(default="single", alias="TENANCY_MODE")
    session_timeout_minutes: int = Field(default=30, alias="SESSION_TIMEOUT_MINUTES")
    access_token_expire_minutes: int = Field(default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_minutes: int = Field(default=60 * 24 * 7, alias="REFRESH_TOKEN_EXPIRE_MINUTES")
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"], alias="ALLOWED_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    enable_hsts: bool = Field(default=True, alias="ENABLE_HSTS")
    default_org_id: str = Field(default="default", alias="DEFAULT_ORG_ID")
    secret_key: str = Field(alias="SECRET_KEY", min_length=16)
    jwt_private_key: str | None = Field(default=None, alias="JWT_PRIVATE_KEY")
    jwt_public_key: str | None = Field(default=None, alias="JWT_PUBLIC_KEY")
    jwt_private_key_path: str | None = Field(default=None, alias="JWT_PRIVATE_KEY_PATH")
    jwt_public_key_path: str | None = Field(default=None, alias="JWT_PUBLIC_KEY_PATH")
    jwt_algorithm: Literal["RS256"] = Field(default="RS256", alias="JWT_ALGORITHM")
    allowed_tenant_hosts: list[str] = Field(default_factory=list, alias="ALLOWED_TENANT_HOSTS")
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    login_attempt_limit: int = Field(default=5, alias="LOGIN_ATTEMPT_LIMIT")
    login_lockout_minutes: int = Field(default=15, alias="LOGIN_LOCKOUT_MINUTES")
    default_password_min_length: int = Field(default=12, alias="DEFAULT_PASSWORD_MIN_LENGTH")
    proxies_count: int = Field(default=0, alias="PROXIES_COUNT")
    extra_seed_org_ids: str = Field(default="", alias="EXTRA_SEED_ORG_IDS", description="Comma-separated org_ids to also grant seed admin ORG_ADMIN")
    seed_admin_email: str = Field(alias="SEED_ADMIN_EMAIL")
    seed_admin_password: str = Field(alias="SEED_ADMIN_PASSWORD")
    seed_admin_full_name: str = Field(default="Admin User", alias="SEED_ADMIN_FULL_NAME")
    local_upload_dir: str = Field(default="local_uploads", alias="LOCAL_UPLOAD_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
