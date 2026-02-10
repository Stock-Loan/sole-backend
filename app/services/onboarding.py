import csv
import io
import logging
import re
import secrets
import string
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Literal

from rapidfuzz import process, fuzz
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.security import get_password_hash
from app.services.authz import assign_default_employee_role
from app.models.identity import Identity
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.schemas.common import (
    EmploymentStatus,
    normalize_employment_status,
    normalize_marital_status,
)
from app.schemas.onboarding import (
    BulkOnboardingResult,
    BulkOnboardingRowError,
    BulkOnboardingRowSuccess,
    OnboardingUserOut,
    OnboardingUserCreate,
)
from app.resources.countries import COUNTRIES, SUBDIVISIONS

logger = logging.getLogger(__name__)


class OnboardingRoleAssignmentError(RuntimeError):
    """Raised when default role assignment fails during onboarding."""


UserStatus = Literal["new", "existing"]
MembershipStatus = Literal["created", "already_exists"]

CONSTRAINT_MESSAGES: dict[str, str] = {
    "uq_users_org_email": "Email already exists in this organization",
    "uq_membership_org_employee": "Employee ID already in use for this organization",
    "uq_membership_org_user": "User is already onboarded in this organization",
}
REQUIRED_FIELD_LABELS: dict[str, str] = {
    "employee_id": "Employee ID",
    "employment_start_date": "Employment start date",
    "email": "Email",
    "first_name": "First name",
    "last_name": "Last name",
}

_COLUMN_IN_DETAIL_RE = re.compile(r'column "(?P<column>[^"]+)"', re.IGNORECASE)
_KEY_IN_DETAIL_RE = re.compile(r"Key \\((?P<column>[^)]+)\\)=", re.IGNORECASE)


@dataclass(frozen=True)
class OnboardingResult:
    user: User
    membership: OrgMembership
    profile: OrgUserProfile | None
    temporary_password: str | None
    user_status: UserStatus
    membership_status: MembershipStatus


def _generate_temp_password(length: int = 16) -> str:
    if length < 12:
        length = 12
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    specials = "!@#$%^&*()-_=+"
    all_chars = lowercase + uppercase + digits + specials

    required_chars = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(specials),
    ]
    remaining = [secrets.choice(all_chars) for _ in range(length - len(required_chars))]
    password_chars = required_chars + remaining
    secrets.SystemRandom().shuffle(password_chars)
    return "".join(password_chars)


def _parse_date(value: str | None):
    if not value:
        return None
    value = value.strip()
    # Accept ISO date
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        pass
    # Accept Excel serial numbers (assuming 1899-12-30 epoch)
    if value.isdigit():
        try:
            base = datetime(1899, 12, 30, tzinfo=timezone.utc)
            days = int(value)
            return (base + timedelta(days=days)).date()
        except (TypeError, ValueError, OverflowError):
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
    MAX_LEN = 50
    if not country:
        return None, None
    raw = country.strip()
    if len(raw) > MAX_LEN:
        raise ValueError(f"country exceeds max length {MAX_LEN}")
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
        if len(state_raw) > MAX_LEN:
            raise ValueError(f"state exceeds max length {MAX_LEN}")
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
                    match = process.extractOne(
                        name_key, choices, scorer=fuzz.WRatio, score_cutoff=85
                    )
                    if match:
                        normalized_state = allowed_names[match[0]]
                    else:
                        raise ValueError(
                            f"Unsupported state '{state_raw}' for country {country_code}"
                        )
        else:
            normalized_state = state_upper[:10]

    return country_code, normalized_state


def _normalize_text(
    value: str | None, *, lower: bool = False, upper: bool = False, title: bool = False
) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if lower:
        return cleaned.lower()
    if upper:
        return cleaned.upper()
    if title:
        return cleaned.title()
    return cleaned


def _normalize_required_text(
    value: str | None,
    field_name: str,
    *,
    lower: bool = False,
    upper: bool = False,
    title: bool = False,
) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    if lower:
        return cleaned.lower()
    if upper:
        return cleaned.upper()
    if title:
        return cleaned.title()
    return cleaned


def _normalize_payload(payload: OnboardingUserCreate) -> OnboardingUserCreate:
    # Normalize textual fields to consistent casing/whitespace
    normalized_email = _normalize_required_text(payload.email, "email", lower=True)
    if payload.employment_start_date is None:
        raise ValueError("employment_start_date is required")
    return OnboardingUserCreate(
        email=normalized_email,
        first_name=_normalize_required_text(payload.first_name, "first_name", title=True),
        middle_name=_normalize_text(payload.middle_name, title=True),
        last_name=_normalize_required_text(payload.last_name, "last_name", title=True),
        preferred_name=_normalize_text(payload.preferred_name, title=True),
        timezone=_normalize_text(payload.timezone),
        phone_number=_normalize_text(payload.phone_number),
        marital_status=normalize_marital_status(payload.marital_status),
        country=_normalize_text(payload.country, upper=True),
        state=_normalize_text(payload.state, upper=True),
        address_line1=_normalize_text(payload.address_line1),
        address_line2=_normalize_text(payload.address_line2),
        postal_code=_normalize_text(payload.postal_code, upper=True),
        temporary_password=_normalize_text(payload.temporary_password),
        employee_id=_normalize_required_text(payload.employee_id, "employee_id"),
        employment_start_date=payload.employment_start_date,
        employment_status=normalize_employment_status(payload.employment_status)
        or EmploymentStatus.ACTIVE,
    )


def describe_integrity_error(exc: IntegrityError) -> str:
    orig = getattr(exc, "orig", None)
    constraint = getattr(orig, "constraint_name", None)
    if constraint and constraint in CONSTRAINT_MESSAGES:
        return CONSTRAINT_MESSAGES[constraint]

    sqlstate = getattr(orig, "sqlstate", None)
    detail = getattr(orig, "detail", "") or ""
    detail_text = detail or str(orig) if orig is not None else ""
    column_hint = None
    if detail_text:
        match = _COLUMN_IN_DETAIL_RE.search(detail_text)
        if match:
            column_hint = match.group("column")
        else:
            match = _KEY_IN_DETAIL_RE.search(detail_text)
            if match:
                column_hint = match.group("column").split(",")[0].strip()

    if sqlstate == "23502":  # not_null_violation
        column = getattr(orig, "column_name", None) or column_hint
        if column:
            label = REQUIRED_FIELD_LABELS.get(column, column.replace("_", " ").title())
            return f"{label} is required"
        return "Missing required field"
    if sqlstate == "23503":  # foreign_key_violation
        return "Related record not found"
    if sqlstate == "23505":  # unique_violation
        detail_lower = detail.lower()
        if "employee_id" in detail_lower:
            return "Employee ID already in use for this organization"
        if "email" in detail_lower:
            return "Email already exists in this organization"
        return "Duplicate user or employee_id"

    return "Unable to complete onboarding due to a data constraint"


def integrity_error_details(exc: IntegrityError) -> dict:
    orig = getattr(exc, "orig", None)
    detail = getattr(orig, "detail", None)
    detail_text = detail or (str(orig) if orig is not None else None)
    column = getattr(orig, "column_name", None)
    if not column and detail_text:
        match = _COLUMN_IN_DETAIL_RE.search(detail_text)
        if match:
            column = match.group("column")
    return {
        "constraint": getattr(orig, "constraint_name", None),
        "sqlstate": getattr(orig, "sqlstate", None),
        "column": column,
        "detail": detail_text,
    }


async def onboard_single_user(
    db: AsyncSession,
    ctx: deps.TenantContext,
    payload: OnboardingUserCreate,
) -> OnboardingResult:
    payload = _normalize_payload(payload)

    # Check if a global identity exists for this email
    identity_stmt = select(Identity).where(Identity.email == payload.email)
    identity = (await db.execute(identity_stmt)).scalar_one_or_none()

    temporary_password: str | None = None
    now = datetime.now(timezone.utc)
    created_user = False
    created_membership = False

    if not identity:
        # New identity — create one with temporary credentials
        temporary_password = payload.temporary_password or _generate_temp_password()
        identity = Identity(
            email=payload.email,
            hashed_password=get_password_hash(temporary_password),
            is_active=True,
            mfa_enabled=False,
            token_version=0,
            must_change_password=True,
        )
        db.add(identity)
        await db.flush()

    # Check if a user record exists for this identity in this org
    user_stmt = select(User).where(
        User.org_id == ctx.org_id, User.identity_id == identity.id
    )
    user = (await db.execute(user_stmt)).scalar_one_or_none()

    if not user:
        created_user = True
        user = User(
            org_id=ctx.org_id,
            identity_id=identity.id,
            email=payload.email,
            is_active=True,
            is_superuser=False,
        )
        db.add(user)
        await db.flush()

    # Ensure membership exists for this org/user
    membership_stmt = select(OrgMembership).where(
        OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == user.id
    )
    membership_result = await db.execute(membership_stmt)
    membership = membership_result.scalar_one_or_none()
    if membership and payload.employee_id and membership.employee_id != payload.employee_id:
        raise ValueError("User already onboarded with a different employee_id")
    if not membership:
        conflict_stmt = select(OrgMembership).where(
            OrgMembership.org_id == ctx.org_id,
            OrgMembership.employee_id == payload.employee_id,
        )
        conflict = (await db.execute(conflict_stmt)).scalar_one_or_none()
        if conflict:
            raise ValueError("employee_id already in use for this organization")
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
        # Ensure membership.id is available before creating a profile.
        await db.flush()
        created_membership = True

    profile_stmt = select(OrgUserProfile).where(
        OrgUserProfile.org_id == ctx.org_id,
        OrgUserProfile.membership_id == membership.id,
    )
    profile = (await db.execute(profile_stmt)).scalar_one_or_none()
    if not profile:
        full_name = f"{payload.first_name} {payload.last_name}".strip()
        profile = OrgUserProfile(
            org_id=ctx.org_id,
            membership_id=membership.id,
            full_name=full_name,
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
        )
        db.add(profile)

    await db.flush()
    await db.refresh(user)
    await db.refresh(membership)
    await db.refresh(profile)
    # Ensure a minimal EMPLOYEE role so first login is possible even while invited
    try:
        await assign_default_employee_role(db, ctx.org_id, user.id)
    except IntegrityError as exc:
        logger.error(
            "Failed to assign default EMPLOYEE role during onboarding",
            exc_info=True,
            extra={"org_id": ctx.org_id, "user_id": str(user.id)},
        )
        raise OnboardingRoleAssignmentError("Failed to assign default employee role") from exc
    user_status: UserStatus = "new" if created_user else "existing"
    membership_status: MembershipStatus = "created" if created_membership else "already_exists"
    return OnboardingResult(
        user=user,
        membership=membership,
        profile=profile,
        temporary_password=temporary_password,
        user_status=user_status,
        membership_status=membership_status,
    )


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

MAX_BULK_ROWS = 30
MAX_FIELD_LEN = {
    "email": 255,
    "first_name": 100,
    "middle_name": 100,
    "last_name": 100,
    "preferred_name": 255,
    "timezone": 100,
    "phone_number": 50,
    "marital_status": 50,
    "country": 50,
    "state": 50,
    "address_line1": 255,
    "address_line2": 255,
    "postal_code": 32,
    "temporary_password": 255,
    "employee_id": 255,
    "employment_status": 50,
}


class BulkOnboardCSVError(Exception):
    def __init__(self, message: str, code: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


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
    # Header validation: strict order required
    if reader.fieldnames is None:
        raise BulkOnboardCSVError(
            "CSV is empty or missing header",
            code="csv_missing_header",
            details={"expected_headers": CSV_COLUMNS, "received_headers": None},
        )
    if reader.fieldnames != CSV_COLUMNS:
        raise BulkOnboardCSVError(
            "CSV headers do not match template",
            code="csv_invalid_headers",
            details={"expected_headers": CSV_COLUMNS, "received_headers": reader.fieldnames},
        )

    successes: list[BulkOnboardingRowSuccess] = []
    errors: list[BulkOnboardingRowError] = []

    rows_processed = 0
    for idx, row in enumerate(reader, start=2):  # row numbers (header=1)
        rows_processed += 1
        if rows_processed > MAX_BULK_ROWS:
            errors.append(
                BulkOnboardingRowError(
                    row_number=idx,
                    email=row.get("email"),
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    employee_id=row.get("employee_id"),
                    error=f"Too many rows; maximum {MAX_BULK_ROWS} allowed",
                )
            )
            break
        try:
            # Length guard before normalization/fuzzy matching
            for key, limit in MAX_FIELD_LEN.items():
                val = row.get(key) or ""
                if len(val) > limit:
                    raise ValueError(f"{key} exceeds max length {limit}")

            country_code, state_code = _normalize_location(
                row.get("country") or None, row.get("state") or None
            )
            employee_id = (row.get("employee_id") or "").strip()
            if not employee_id:
                raise ValueError("employee_id is required")
            employment_start_date = _parse_date(row.get("employment_start_date"))
            if employment_start_date is None:
                raise ValueError("employment_start_date is required")
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
                employee_id=employee_id,
                employment_start_date=employment_start_date,
                employment_status=row.get("employment_status") or "ACTIVE",
            )
            payload = _normalize_payload(payload)
            result = await onboard_single_user(db, ctx, payload)
            profile = result.profile
            user_out = OnboardingUserOut.model_validate(
                {
                    "id": result.user.id,
                    "org_id": ctx.org_id,
                    "email": result.user.email,
                    "is_active": result.user.is_active,
                    "is_superuser": result.user.is_superuser,
                    "created_at": result.user.created_at,
                    "first_name": profile.first_name if profile else None,
                    "middle_name": profile.middle_name if profile else None,
                    "last_name": profile.last_name if profile else None,
                    "preferred_name": profile.preferred_name if profile else None,
                    "timezone": profile.timezone if profile else None,
                    "phone_number": profile.phone_number if profile else None,
                    "marital_status": profile.marital_status if profile else None,
                    "country": profile.country if profile else None,
                    "state": profile.state if profile else None,
                    "address_line1": profile.address_line1 if profile else None,
                    "address_line2": profile.address_line2 if profile else None,
                    "postal_code": profile.postal_code if profile else None,
                }
            )
            success = BulkOnboardingRowSuccess(
                row_number=idx,
                user=user_out,
                membership=result.membership,
                user_status=result.user_status,
                membership_status=result.membership_status,
                credentials_issued=bool(result.temporary_password),
            )
            await db.commit()  # commit-ok: per-row isolation in bulk operation
            successes.append(success)
        except IntegrityError as exc:
            await db.rollback()
            errors.append(
                BulkOnboardingRowError(
                    row_number=idx,
                    email=row.get("email"),
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    employee_id=row.get("employee_id"),
                    error=describe_integrity_error(exc),
                )
            )
        except (ValueError, TypeError, IntegrityError, OnboardingRoleAssignmentError) as exc:
            await db.rollback()
            errors.append(
                BulkOnboardingRowError(
                    row_number=idx,
                    email=row.get("email"),
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    employee_id=row.get("employee_id"),
                    error=str(exc),
                )
            )

    if rows_processed == 0:
        raise BulkOnboardCSVError(
            "CSV contains no data rows",
            code="csv_empty",
            details={"expected_headers": CSV_COLUMNS},
        )

    return BulkOnboardingResult(successes=successes, errors=errors)
