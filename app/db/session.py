from collections.abc import AsyncGenerator
import logging
import os
import ssl
import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.settings import settings
from app.db.url import normalize_database_url

logger = logging.getLogger(__name__)

db_url = normalize_database_url(settings.database_url)

# Default to SSL unless explicitly disabled.
db_ssl_env = os.getenv("DB_SSL")
if db_ssl_env is not None:
    db_ssl = db_ssl_env.lower() in ("1", "true", "yes", "on")
else:
    lowered = db_url.lower()
    db_ssl = not ("ssl=disable" in lowered or "sslmode=disable" in lowered)

def _ensure_sslmode(url: str, enable: bool) -> str:
    if not enable or not url:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "sslmode" not in query:
        query["sslmode"] = "require"
    new_query = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


db_url = _ensure_sslmode(db_url, db_ssl)

engine = create_async_engine(
    db_url,
    future=True,
    echo=False,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_statement_timeout(dbapi_connection, _connection_record) -> None:
    timeout_ms = settings.db_statement_timeout_ms
    if timeout_ms <= 0:
        return
    cursor = dbapi_connection.cursor()
    try:
        # Postgres does not accept bound params for SET, so inline a sanitized int.
        cursor.execute(f"SET statement_timeout = {int(timeout_ms)}")
    finally:
        cursor.close()


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    context._query_start_time = time.monotonic()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    start_time = getattr(context, "_query_start_time", None)
    if start_time is None:
        return
    duration_ms = (time.monotonic() - start_time) * 1000
    if settings.db_log_query_timings:
        logger.info("db.query_time_ms=%.2f statement=%s", duration_ms, statement[:200])
    elif duration_ms >= settings.db_slow_query_ms:
        logger.warning("db.slow_query_ms=%.2f statement=%s", duration_ms, statement[:200])


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
