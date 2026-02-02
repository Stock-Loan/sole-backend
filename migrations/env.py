import asyncio
import os
from logging.config import fileConfig
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import Base
from app import models

# Alembic Config object (ini values)
config = context.config

# Configure Python logging using alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
 

def _normalize_database_url(url: str) -> str:
    """
    Normalize DATABASE_URL so asyncpg/sqlalchemy don't choke.

    Known gotcha:
      - Neon often uses ?ssl=true, but asyncpg expects sslmode=require (or ssl handled via connect_args).
    We'll convert ssl=true -> sslmode=require
    """
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))

    # Convert ssl=true to sslmode=require for asyncpg compatibility
    ssl_val = q.get("ssl")
    if ssl_val and ssl_val.lower() in ("1", "true", "yes", "on"):
        q.pop("ssl", None)
        q.setdefault("sslmode", "require")

    new_query = urlencode(q, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _get_database_url() -> str:
    """
    Prefer DATABASE_URL from environment (Cloud Run secrets / local env).
    Fall back to alembic.ini sqlalchemy.url if not present.
    """
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url:
        return _normalize_database_url(env_url)
    return config.get_main_option("sqlalchemy.url")


# Force sqlalchemy.url to the resolved URL (so both offline/online modes use the same value)
config.set_main_option("sqlalchemy.url", _get_database_url())


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode (no DB connection).
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """
    Actual migration runner (sync context, executed via connection.run_sync).
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode using an async engine.
    """
    # Important: async_engine_from_config reads sqlalchemy.url from config (we set it above).
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
