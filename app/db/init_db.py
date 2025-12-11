import asyncio
from sqlalchemy import select

from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.models.user import User

async def init_db() -> None:
    """
    Seed the database with an initial user.
    """
    async with AsyncSessionLocal() as session:
        print("Seeding database...")
        stmt = select(User).where(User.email == settings.seed_admin_email, User.org_id == settings.default_org_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            print("Creating admin user...")
            user = User(
                org_id=settings.default_org_id,
                email=settings.seed_admin_email,
                hashed_password=get_password_hash(settings.seed_admin_password),
                is_active=True,
                is_superuser=True,
                token_version=0,
                full_name=settings.seed_admin_full_name,
            )
            session.add(user)
            await session.commit()
            print("Admin user created.")
        else:
            print("Admin user already exists.")

if __name__ == "__main__":
    asyncio.run(init_db())
