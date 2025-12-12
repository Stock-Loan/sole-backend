import csv
import io
import secrets
import string
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.security import get_password_hash
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.onboarding import (
    BulkOnboardingResult,
    BulkOnboardingRowError,
    BulkOnboardingRowSuccess,
    OnboardingUserCreate,
)


def _generate_temp_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def onboard_single_user(
    db: AsyncSession,
    ctx: deps.TenantContext,
    payload: OnboardingUserCreate,
) -> Tuple[User, OrgMembership, str | None]:
    stmt = select(User).where(User.email == payload.email, User.org_id == ctx.org_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    temporary_password: str | None = None
    now = datetime.now(timezone.utc)

    if not user:
        temporary_password = _generate_temp_password()
        full_name = f"{payload.first_name} {payload.last_name}".strip()
        user = User(
            org_id=ctx.org_id,
            email=payload.email,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            last_name=payload.last_name,
            preferred_name=payload.preferred_name,
            timezone=payload.timezone,
            phone_number=payload.phone_number,
            full_name=full_name,
            hashed_password=get_password_hash(temporary_password),
            is_active=True,
            is_superuser=False,
            token_version=0,
            mfa_enabled=False,
            last_active_at=None,
        )
        db.add(user)
        await db.flush()

    # Ensure membership exists for this org/user
    membership_stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == user.id
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if not membership:
        membership = OrgMembership(
            org_id=ctx.org_id,
            user_id=user.id,
            employee_id=payload.employee_id,
            employment_start_date=payload.employment_start_date,
            employment_status=payload.employment_status,
            platform_status="INVITED",
            invitation_status="PENDING",
            invited_at=now,
        )
        db.add(membership)

    await db.commit()
    await db.refresh(user)
    await db.refresh(membership)
    return user, membership, temporary_password


CSV_COLUMNS = [
    "email",
    "first_name",
    "middle_name",
    "last_name",
    "preferred_name",
    "timezone",
    "phone_number",
    "employee_id",
    "employment_start_date",
    "employment_status",
]


def generate_csv_template() -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    return output.getvalue()


async def bulk_onboard_users(
    db: AsyncSession,
    ctx: deps.TenantContext,
    csv_content: str,
) -> BulkOnboardingResult:
    reader = csv.DictReader(io.StringIO(csv_content))
    successes: list[BulkOnboardingRowSuccess] = []
    errors: list[BulkOnboardingRowError] = []

    for idx, row in enumerate(reader, start=2):  # row numbers (header=1)
        try:
            payload = OnboardingUserCreate(
                email=row.get("email", "").strip(),
                first_name=row.get("first_name", "").strip(),
                middle_name=row.get("middle_name") or None,
                last_name=row.get("last_name", "").strip(),
                preferred_name=row.get("preferred_name") or None,
                timezone=row.get("timezone") or None,
                phone_number=row.get("phone_number") or None,
                employee_id=row.get("employee_id", "").strip(),
                employment_start_date=row.get("employment_start_date") or None,
                employment_status=row.get("employment_status") or "ACTIVE",
            )
            user, membership, temp_password = await onboard_single_user(db, ctx, payload)
            successes.append(
                BulkOnboardingRowSuccess(
                    row_number=idx,
                    user=user,
                    membership=membership,
                    temporary_password=temp_password,
                )
            )
        except IntegrityError as exc:
            await db.rollback()
            errors.append(
                BulkOnboardingRowError(
                    row_number=idx,
                    email=row.get("email"),
                    employee_id=row.get("employee_id"),
                    error="Duplicate user or employee_id",
                )
            )
        except Exception as exc:  # pragma: no cover - capture validation/runtime errors
            errors.append(
                BulkOnboardingRowError(
                    row_number=idx,
                    email=row.get("email"),
                    employee_id=row.get("employee_id"),
                    error=str(exc),
                )
            )

    return BulkOnboardingResult(successes=successes, errors=errors)
