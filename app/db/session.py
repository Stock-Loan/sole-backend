from collections.abc import AsyncGenerator
import os
import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings

connect_args: dict = {}

# Default to SSL in Cloud Run unless explicitly disabled
db_ssl = os.getenv("DB_SSL", "true").lower() in ("1", "true", "yes", "on")
if db_ssl:
    connect_args["ssl"] = ssl.create_default_context()

# Useful for Neon poolers
connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    settings.database_url,
    future=True,
    echo=False,
    connect_args=connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
