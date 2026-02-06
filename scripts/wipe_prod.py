import os
import asyncio
import asyncpg
from redis import Redis

# 1. Manually paste your PROD connection strings here
# (Copy them from your secret-prod folder or Neon/Redis dashboard)
# Remeber to rotate your passwords after running this script!
DATABASE_URL = "postgres://user:pass@ep-xyz.us-east-2.aws.neon.tech/neondb?sslmode=require"
REDIS_URL = "redis://default:pass@fly-xyz.upstash.io:6379"

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