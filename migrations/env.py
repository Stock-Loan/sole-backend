import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import Base
from app.db.url import normalize_database_url
import app.models  # noqa: F401

# Alembic Config object (ini values)
config = context.config

# Configure Python logging using alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """
    Prefer DATABASE_URL_DIRECT from environment for migrations.
    Fall back to DATABASE_URL, then alembic.ini.
    Fall back to alembic.ini sqlalchemy.url if not present.
    """
    direct_url = os.getenv("DATABASE_URL_DIRECT", "").strip()
    if direct_url:
        return normalize_database_url(direct_url)
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url:
        return normalize_database_url(env_url)
    return normalize_database_url(config.get_main_option("sqlalchemy.url"))


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
