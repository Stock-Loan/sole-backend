import csv
import io
import secrets
import string
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Tuple

from rapidfuzz import process, fuzz
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.security import get_password_hash
from app.services.authz import assign_default_employee_role
from app.models.org_membership import OrgMembership
from app.models.user import User
from app.schemas.onboarding import (
    BulkOnboardingResult,
    BulkOnboardingRowError,
    BulkOnboardingRowSuccess,
    OnboardingUserCreate,
)
from app.resources.countries import COUNTRIES, SUBDIVISIONS


def _generate_temp_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _parse_date(value: str | None):
    if not value:
        return None
    value = value.strip()
    # Accept ISO date
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        pass
    # Accept Excel serial numbers (assuming 1899-12-30 epoch)
    if value.isdigit():
        try:
            base = datetime(1899, 12, 30, tzinfo=timezone.utc)
            days = int(value)
            return (base + timedelta(days=days)).date()
        except Exception:
            return None
    return None


COUNTRY_MAP = {c["code"]: c["name"].lower() for c in COUNTRIES}
COUNTRY_NAME_TO_CODE = {v: k for k, v in COUNTRY_MAP.items()}
COUNTRY_ALIASES = {
    "usa": "US",
    "unitedstates": "US",
    "unitedstatesofamerica": "US",
    "america": "US",
    "uk": "GB",
    "unitedkingdom": "GB",
    "greatbritain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northernireland": "GB",
}


def _normalize_label(text: str) -> str:
    """Lowercase and strip diacritics for fuzzy matching."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return "".join(ch for ch in ascii_text.lower() if ch.isalnum())


def _normalize_location(country: str | None, state: str | None) -> tuple[str | None, str | None]:
    if not country:
        return None, None
    raw = country.strip()
    norm = _normalize_label(raw)

    # Exact code
    if raw.upper() in COUNTRY_MAP:
        country_code = raw.upper()
    # Alias
    elif norm in COUNTRY_ALIASES:
        country_code = COUNTRY_ALIASES[norm]
    # Name exact
    elif norm in COUNTRY_NAME_TO_CODE:
        country_code = COUNTRY_NAME_TO_CODE[norm]
    else:
        # Fuzzy match country names
        choices = list(COUNTRY_NAME_TO_CODE.keys())
        match = process.extractOne(norm, choices, scorer=fuzz.WRatio, score_cutoff=90)
        if match:
            country_code = COUNTRY_NAME_TO_CODE[match[0]]
        else:
            raise ValueError(f"Unsupported country: {country}")

    normalized_state = None
    if state:
        state_raw = state.strip()
        state_upper = state_raw.upper()
        allowed = SUBDIVISIONS.get(country_code, [])
        allowed_codes = {s["code"] for s in allowed}
        allowed_names = {_normalize_label(s["name"]): s["code"] for s in allowed}
        if allowed:
            if state_upper in allowed_codes:
                normalized_state = state_upper
            else:
                name_key = _normalize_label(state_raw)
                if name_key in allowed_names:
                    normalized_state = allowed_names[name_key]
                else:
                    # Fuzzy match subdivision names
                    choices = list(allowed_names.keys())
                    match = process.extractOne(name_key, choices, scorer=fuzz.WRatio, score_cutoff=85)
                    if match:
                        normalized_state = allowed_names[match[0]]
                    else:
                        raise ValueError(f"Unsupported state '{state_raw}' for country {country_code}")
        else:
            normalized_state = state_upper[:10]

    return country_code, normalized_state


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
        temporary_password = payload.temporary_password or _generate_temp_password()
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
            marital_status=payload.marital_status,
            country=payload.country,
            state=payload.state,
            address_line1=payload.address_line1,
            address_line2=payload.address_line2,
            postal_code=payload.postal_code,
            full_name=full_name,
            hashed_password=get_password_hash(temporary_password),
            is_active=True,
            is_superuser=False,
            token_version=0,
            mfa_enabled=False,
            last_active_at=None,
            must_change_password=True,
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
    # Ensure a minimal EMPLOYEE role so first login is possible even while invited
    try:
        await assign_default_employee_role(db, ctx.org_id, user.id)
    except Exception:
        # Best-effort; do not fail onboarding if role assignment fails
        pass
    return user, membership, temporary_password


CSV_COLUMNS = [
    "email",
    "first_name",
    "middle_name",
    "last_name",
    "preferred_name",
    "timezone",
    "phone_number",
    "marital_status",
    "country",
    "state",
    "address_line1",
    "address_line2",
    "postal_code",
    "temporary_password",
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
            country_code, state_code = _normalize_location(
                row.get("country") or None, row.get("state") or None
            )
            payload = OnboardingUserCreate(
                email=row.get("email", "").strip(),
                first_name=row.get("first_name", "").strip(),
                middle_name=row.get("middle_name") or None,
                last_name=row.get("last_name", "").strip(),
                preferred_name=row.get("preferred_name") or None,
                timezone=row.get("timezone") or None,
                phone_number=row.get("phone_number") or None,
                marital_status=row.get("marital_status") or None,
                country=country_code,
                state=state_code,
                address_line1=(row.get("address_line1") or "").strip() or None,
                address_line2=(row.get("address_line2") or "").strip() or None,
                postal_code=(row.get("postal_code") or "").strip() or None,
                temporary_password=(row.get("temporary_password") or "").strip() or None,
                employee_id=row.get("employee_id", "").strip(),
                employment_start_date=_parse_date(row.get("employment_start_date")),
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
