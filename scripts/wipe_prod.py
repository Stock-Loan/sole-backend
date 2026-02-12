from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import asyncio
import asyncpg
from redis import Redis

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_DIR = BASE_DIR / "secret-prod"

# Connection strings are loaded from the secret-prod folder; rotate them after running this script.
def _load_secret(name: str) -> str:
    return (SECRET_DIR / name).read_text().strip()

def _build_database_url() -> str:
    raw_url = _load_secret("DATABASE_URL")
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.split("+", 1)[0]
    params = dict(parse_qsl(parsed.query))
    params["sslmode"] = "require"
    return urlunparse(parsed._replace(scheme=scheme, query=urlencode(params)))

DATABASE_URL = _build_database_url()
REDIS_URL = _load_secret("REDIS_URL")

async def wipe_postgres():
    print("🗑️  Wiping Postgres...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # Drops the public schema and recreates it (Removes all tables/enums/functions)
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        print("✅ Postgres Wiped.")
        await conn.close()
    except Exception as e:
        print(f"❌ Postgres Error: {e}")


def wipe_redis():
    print("🗑️  Wiping Redis...")
    try:
        r = Redis.from_url(REDIS_URL)
        r.flushdb()
        print("✅ Redis Wiped.")
    except Exception as e:
        print(f"❌ Redis Error: {e}")


async def main():
    print("⚠️  WARNING: THIS WILL DELETE ALL PRODUCTION DATA ⚠️")
    confirm = input("Type 'delete' to confirm: ")
    if confirm != "delete":
        print("Aborted.")
        return

    await wipe_postgres()
    wipe_redis()


if __name__ == "__main__":
    asyncio.run(main())
