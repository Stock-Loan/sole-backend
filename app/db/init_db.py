import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.models.department import Department
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.identity import Identity
from app.models.org import Org
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.models.vesting_event import VestingEvent
from app.services.authz import ensure_user_in_role, seed_system_roles
from app.services.orgs import ensure_audit_partitions_for_orgs


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_seed_org_ids() -> list[str]:
    org_ids = [settings.default_org_id]
    if settings.extra_seed_org_ids:
        extras = [oid.strip() for oid in settings.extra_seed_org_ids.split(",") if oid.strip()]
        org_ids.extend(extras)
    seen: set[str] = set()
    ordered: list[str] = []
    for oid in org_ids:
        if oid in seen:
            continue
        seen.add(oid)
        ordered.append(oid)
    return ordered


def _org_name_for_seed(org_id: str) -> str:
    if org_id == settings.default_org_id:
        return settings.default_org_name
    return f"{org_id.replace('-', ' ').title()} Organization"


def _org_slug_for_seed(org_id: str) -> str:
    if org_id == settings.default_org_id:
        return settings.default_org_slug or settings.default_org_id
    return org_id


def _is_production() -> bool:
    return settings.environment.lower() in {"production", "prod"}


# ---------------------------------------------------------------------------
# Ensure helpers
# ---------------------------------------------------------------------------

async def _ensure_org(session: AsyncSession, org_id: str) -> Org:
    org_stmt = select(Org).where(Org.id == org_id)
    org_result = await session.execute(org_stmt)
    org = org_result.scalar_one_or_none()
    if org:
        return org
    org = Org(
        id=org_id,
        name=_org_name_for_seed(org_id),
        slug=_org_slug_for_seed(org_id),
        status="ACTIVE",
    )
    session.add(org)
    await session.commit()
    return org


async def _ensure_department(
    session: AsyncSession, *, org_id: str, name: str, code: str
) -> Department:
    stmt = select(Department).where(
        Department.org_id == org_id, Department.code == code
    )
    dept = (await session.execute(stmt)).scalar_one_or_none()
    if dept:
        return dept
    dept = Department(org_id=org_id, name=name, code=code)
    session.add(dept)
    await session.commit()
    return dept


async def _ensure_membership_and_profile(
    session: AsyncSession,
    *,
    org_id: str,
    user: User,
    employee_id: str,
    full_name: str,
    first_name: str,
    last_name: str,
    department_id: Any | None = None,
    employment_start_date: date | None = None,
    middle_name: str | None = None,
    preferred_name: str | None = None,
    phone_number: str | None = None,
    timezone_str: str | None = None,
    marital_status: str | None = None,
    country: str | None = None,
    state: str | None = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    postal_code: str | None = None,
) -> OrgMembership:
    # Check by user_id first, then fall back to employee_id (handles re-seeds
    # where the email changed but employee_id stayed the same).
    mem_stmt = select(OrgMembership).where(
        OrgMembership.org_id == org_id, OrgMembership.user_id == user.id
    )
    mem_result = await session.execute(mem_stmt)
    membership = mem_result.scalar_one_or_none()
    if not membership:
        eid_stmt = select(OrgMembership).where(
            OrgMembership.org_id == org_id, OrgMembership.employee_id == employee_id
        )
        membership = (await session.execute(eid_stmt)).scalar_one_or_none()
    if not membership:
        now = datetime.now(timezone.utc)
        membership = OrgMembership(
            org_id=org_id,
            user_id=user.id,
            employee_id=employee_id,
            department_id=department_id,
            employment_start_date=employment_start_date,
            employment_status="ACTIVE",
            platform_status="ACTIVE",
            invitation_status="ACCEPTED",
            invited_at=now,
            accepted_at=now,
        )
        session.add(membership)
        await session.commit()

    profile_stmt = select(OrgUserProfile).where(
        OrgUserProfile.org_id == org_id,
        OrgUserProfile.membership_id == membership.id,
    )
    profile = (await session.execute(profile_stmt)).scalar_one_or_none()
    if not profile:
        profile = OrgUserProfile(
            org_id=org_id,
            membership_id=membership.id,
            full_name=full_name,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            preferred_name=preferred_name,
            timezone=timezone_str,
            phone_number=phone_number,
            marital_status=marital_status,
            country=country,
            state=state,
            address_line1=address_line1,
            address_line2=address_line2,
            postal_code=postal_code,
        )
        session.add(profile)
        await session.commit()
    return membership


async def _ensure_identity(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    must_change_password: bool,
) -> Identity:
    """Find or create a global Identity for the given email."""
    stmt = select(Identity).where(Identity.email == email)
    identity = (await session.execute(stmt)).scalar_one_or_none()
    if identity:
        if must_change_password and not identity.must_change_password:
            identity.must_change_password = True
            session.add(identity)
            await session.flush()
        return identity
    identity = Identity(
        email=email,
        hashed_password=get_password_hash(password),
        must_change_password=must_change_password,
    )
    session.add(identity)
    await session.commit()
    return identity


async def _seed_user(
    session: AsyncSession,
    *,
    org_id: str,
    roles: dict[str, object],
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    employee_id: str,
    is_superuser: bool,
    role_names: list[str],
    department_id: Any | None = None,
    employment_start_date: date | None = None,
    middle_name: str | None = None,
    preferred_name: str | None = None,
    phone_number: str | None = None,
    timezone_str: str | None = None,
    marital_status: str | None = None,
    country: str | None = None,
    state: str | None = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    postal_code: str | None = None,
    must_change_password: bool = False,
) -> OrgMembership:
    identity = await _ensure_identity(
        session,
        email=email,
        password=password,
        must_change_password=must_change_password,
    )

    stmt = select(User).where(User.org_id == org_id, User.email == email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            org_id=org_id,
            identity_id=identity.id,
            email=email,
            is_active=True,
            is_superuser=is_superuser,
        )
        session.add(user)
        await session.commit()

    for role_name in role_names:
        role = roles.get(role_name)
        if role:
            await ensure_user_in_role(session, org_id, user.id, role)

    full_name = f"{first_name} {last_name}".strip()
    membership = await _ensure_membership_and_profile(
        session,
        org_id=org_id,
        user=user,
        employee_id=employee_id,
        full_name=full_name,
        first_name=first_name,
        last_name=last_name,
        department_id=department_id,
        employment_start_date=employment_start_date,
        middle_name=middle_name,
        preferred_name=preferred_name,
        phone_number=phone_number,
        timezone_str=timezone_str,
        marital_status=marital_status,
        country=country,
        state=state,
        address_line1=address_line1,
        address_line2=address_line2,
        postal_code=postal_code,
    )
    return membership


# ---------------------------------------------------------------------------
# Stock grant seeding
# ---------------------------------------------------------------------------

async def _seed_stock_grant(
    session: AsyncSession,
    *,
    org_id: str,
    membership_id: Any,
    grant_date: date,
    total_shares: int,
    exercise_price: Decimal,
    vesting_strategy: str,
    vesting_events: list[dict[str, Any]],
    notes: str | None = None,
) -> None:
    """Create a stock grant with vesting events if it doesn't already exist."""
    stmt = select(EmployeeStockGrant).where(
        EmployeeStockGrant.org_id == org_id,
        EmployeeStockGrant.org_membership_id == membership_id,
        EmployeeStockGrant.grant_date == grant_date,
        EmployeeStockGrant.total_shares == total_shares,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return

    grant = EmployeeStockGrant(
        org_id=org_id,
        org_membership_id=membership_id,
        grant_date=grant_date,
        total_shares=total_shares,
        exercise_price=exercise_price,
        status="ACTIVE",
        vesting_strategy=vesting_strategy,
        notes=notes,
    )
    session.add(grant)
    await session.flush()

    for evt in vesting_events:
        ve = VestingEvent(
            org_id=org_id,
            grant_id=grant.id,
            vest_date=evt["vest_date"],
            shares=evt["shares"],
        )
        session.add(ve)

    await session.commit()


# ---------------------------------------------------------------------------
# Demo seed definitions (dev / staging only)
# ---------------------------------------------------------------------------

_DEMO_DEPARTMENTS = [
    {"name": "Engineering", "code": "ENG"},
    {"name": "Human Resources", "code": "HR"},
    {"name": "Finance", "code": "FIN"},
    {"name": "Legal", "code": "LGL"},
    {"name": "Sales", "code": "SLS"},
    {"name": "Marketing", "code": "MKT"},
    {"name": "Operations", "code": "OPS"},
]


def _demo_users(org_id: str) -> list[dict[str, Any]]:
    """Return 10 fully-populated demo user definitions."""
    return [
        {
            "email": f"harper.reed@{org_id}.example.com",
            "first_name": "Harper",
            "middle_name": "Avery",
            "last_name": "Reed",
            "preferred_name": "Harper",
            "employee_id": f"{org_id}-HR-001",
            "role_names": ["HR", "EMPLOYEE"],
            "department_code": "HR",
            "employment_start_date": date(2022, 3, 15),
            "phone_number": "+1-555-101-0001",
            "timezone_str": "America/New_York",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "NY",
            "address_line1": "120 Broadway",
            "address_line2": "Suite 400",
            "postal_code": "10271",
            "grants": [
                {
                    "grant_date": date(2022, 6, 1),
                    "total_shares": 5000,
                    "exercise_price": Decimal("12.50"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2023, 6, 1), "shares": 1250},
                        {"vest_date": date(2024, 6, 1), "shares": 1250},
                        {"vest_date": date(2025, 6, 1), "shares": 1250},
                        {"vest_date": date(2026, 6, 1), "shares": 1250},
                    ],
                },
            ],
        },
        {
            "email": f"evan.chen@{org_id}.example.com",
            "first_name": "Evan",
            "middle_name": None,
            "last_name": "Chen",
            "preferred_name": "Evan",
            "employee_id": f"{org_id}-ENG-001",
            "role_names": ["EMPLOYEE"],
            "department_code": "ENG",
            "employment_start_date": date(2021, 1, 10),
            "phone_number": "+1-555-101-0002",
            "timezone_str": "America/Los_Angeles",
            "marital_status": "SINGLE",
            "country": "US",
            "state": "CA",
            "address_line1": "456 Market St",
            "address_line2": "Apt 12B",
            "postal_code": "94105",
            "grants": [
                {
                    "grant_date": date(2021, 4, 1),
                    "total_shares": 10000,
                    "exercise_price": Decimal("10.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest with cliff",
                    "vesting_events": [
                        {"vest_date": date(2022, 4, 1), "shares": 2500},
                        {"vest_date": date(2023, 4, 1), "shares": 2500},
                        {"vest_date": date(2024, 4, 1), "shares": 2500},
                        {"vest_date": date(2025, 4, 1), "shares": 2500},
                    ],
                },
                {
                    "grant_date": date(2024, 1, 15),
                    "total_shares": 2000,
                    "exercise_price": Decimal("18.75"),
                    "vesting_strategy": "IMMEDIATE",
                    "notes": "Performance bonus grant",
                    "vesting_events": [
                        {"vest_date": date(2024, 1, 15), "shares": 2000},
                    ],
                },
            ],
        },
        {
            "email": f"maria.santos@{org_id}.example.com",
            "first_name": "Maria",
            "middle_name": "Lucia",
            "last_name": "Santos",
            "preferred_name": "Mari",
            "employee_id": f"{org_id}-FIN-001",
            "role_names": ["FINANCE", "EMPLOYEE"],
            "department_code": "FIN",
            "employment_start_date": date(2020, 7, 1),
            "phone_number": "+1-555-101-0003",
            "timezone_str": "America/Chicago",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "IL",
            "address_line1": "233 S Wacker Dr",
            "address_line2": None,
            "postal_code": "60606",
            "grants": [
                {
                    "grant_date": date(2020, 10, 1),
                    "total_shares": 8000,
                    "exercise_price": Decimal("8.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2021, 10, 1), "shares": 2000},
                        {"vest_date": date(2022, 10, 1), "shares": 2000},
                        {"vest_date": date(2023, 10, 1), "shares": 2000},
                        {"vest_date": date(2024, 10, 1), "shares": 2000},
                    ],
                },
                {
                    "grant_date": date(2023, 7, 1),
                    "total_shares": 3000,
                    "exercise_price": Decimal("15.50"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Promotion grant — 3-year vest",
                    "vesting_events": [
                        {"vest_date": date(2024, 7, 1), "shares": 1000},
                        {"vest_date": date(2025, 7, 1), "shares": 1000},
                        {"vest_date": date(2026, 7, 1), "shares": 1000},
                    ],
                },
            ],
        },
        {
            "email": f"james.wright@{org_id}.example.com",
            "first_name": "James",
            "middle_name": "Thomas",
            "last_name": "Wright",
            "preferred_name": "Jim",
            "employee_id": f"{org_id}-LGL-001",
            "role_names": ["LEGAL", "EMPLOYEE"],
            "department_code": "LGL",
            "employment_start_date": date(2021, 9, 1),
            "phone_number": "+1-555-101-0004",
            "timezone_str": "America/New_York",
            "marital_status": "DIVORCED",
            "country": "US",
            "state": "DC",
            "address_line1": "1200 Pennsylvania Ave NW",
            "address_line2": "Floor 3",
            "postal_code": "20004",
            "grants": [
                {
                    "grant_date": date(2021, 12, 1),
                    "total_shares": 6000,
                    "exercise_price": Decimal("11.25"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2022, 12, 1), "shares": 1500},
                        {"vest_date": date(2023, 12, 1), "shares": 1500},
                        {"vest_date": date(2024, 12, 1), "shares": 1500},
                        {"vest_date": date(2025, 12, 1), "shares": 1500},
                    ],
                },
            ],
        },
        {
            "email": f"priya.sharma@{org_id}.example.com",
            "first_name": "Priya",
            "middle_name": None,
            "last_name": "Sharma",
            "preferred_name": None,
            "employee_id": f"{org_id}-ENG-002",
            "role_names": ["EMPLOYEE"],
            "department_code": "ENG",
            "employment_start_date": date(2023, 2, 1),
            "phone_number": "+1-555-101-0005",
            "timezone_str": "America/Los_Angeles",
            "marital_status": "SINGLE",
            "country": "US",
            "state": "WA",
            "address_line1": "400 Broad St",
            "address_line2": "Unit 8C",
            "postal_code": "98109",
            "grants": [
                {
                    "grant_date": date(2023, 5, 1),
                    "total_shares": 7500,
                    "exercise_price": Decimal("16.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2024, 5, 1), "shares": 1875},
                        {"vest_date": date(2025, 5, 1), "shares": 1875},
                        {"vest_date": date(2026, 5, 1), "shares": 1875},
                        {"vest_date": date(2027, 5, 1), "shares": 1875},
                    ],
                },
                {
                    "grant_date": date(2025, 2, 1),
                    "total_shares": 1500,
                    "exercise_price": Decimal("22.00"),
                    "vesting_strategy": "IMMEDIATE",
                    "notes": "Spot bonus — immediate vest",
                    "vesting_events": [
                        {"vest_date": date(2025, 2, 1), "shares": 1500},
                    ],
                },
            ],
        },
        {
            "email": f"daniel.okafor@{org_id}.example.com",
            "first_name": "Daniel",
            "middle_name": "Emeka",
            "last_name": "Okafor",
            "preferred_name": "Dan",
            "employee_id": f"{org_id}-SLS-001",
            "role_names": ["EMPLOYEE"],
            "department_code": "SLS",
            "employment_start_date": date(2022, 6, 15),
            "phone_number": "+1-555-101-0006",
            "timezone_str": "America/Denver",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "CO",
            "address_line1": "1600 Champa St",
            "address_line2": None,
            "postal_code": "80202",
            "grants": [
                {
                    "grant_date": date(2022, 9, 1),
                    "total_shares": 4000,
                    "exercise_price": Decimal("13.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2023, 9, 1), "shares": 1000},
                        {"vest_date": date(2024, 9, 1), "shares": 1000},
                        {"vest_date": date(2025, 9, 1), "shares": 1000},
                        {"vest_date": date(2026, 9, 1), "shares": 1000},
                    ],
                },
            ],
        },
        {
            "email": f"lisa.martinez@{org_id}.example.com",
            "first_name": "Lisa",
            "middle_name": "Anne",
            "last_name": "Martinez",
            "preferred_name": None,
            "employee_id": f"{org_id}-MKT-001",
            "role_names": ["EMPLOYEE"],
            "department_code": "MKT",
            "employment_start_date": date(2023, 8, 1),
            "phone_number": "+1-555-101-0007",
            "timezone_str": "America/New_York",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "FL",
            "address_line1": "801 Brickell Ave",
            "address_line2": "Suite 900",
            "postal_code": "33131",
            "grants": [
                {
                    "grant_date": date(2023, 11, 1),
                    "total_shares": 5000,
                    "exercise_price": Decimal("17.25"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2024, 11, 1), "shares": 1250},
                        {"vest_date": date(2025, 11, 1), "shares": 1250},
                        {"vest_date": date(2026, 11, 1), "shares": 1250},
                        {"vest_date": date(2027, 11, 1), "shares": 1250},
                    ],
                },
            ],
        },
        {
            "email": f"robert.kim@{org_id}.example.com",
            "first_name": "Robert",
            "middle_name": "Joon",
            "last_name": "Kim",
            "preferred_name": "Rob",
            "employee_id": f"{org_id}-OPS-001",
            "role_names": ["EMPLOYEE"],
            "department_code": "OPS",
            "employment_start_date": date(2020, 11, 1),
            "phone_number": "+1-555-101-0008",
            "timezone_str": "America/Chicago",
            "marital_status": "SINGLE",
            "country": "US",
            "state": "TX",
            "address_line1": "1000 Main St",
            "address_line2": None,
            "postal_code": "77002",
            "grants": [
                {
                    "grant_date": date(2021, 2, 1),
                    "total_shares": 9000,
                    "exercise_price": Decimal("9.50"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 3-year vest",
                    "vesting_events": [
                        {"vest_date": date(2022, 2, 1), "shares": 3000},
                        {"vest_date": date(2023, 2, 1), "shares": 3000},
                        {"vest_date": date(2024, 2, 1), "shares": 3000},
                    ],
                },
                {
                    "grant_date": date(2024, 5, 1),
                    "total_shares": 4000,
                    "exercise_price": Decimal("19.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Retention grant — 2-year vest",
                    "vesting_events": [
                        {"vest_date": date(2025, 5, 1), "shares": 2000},
                        {"vest_date": date(2026, 5, 1), "shares": 2000},
                    ],
                },
            ],
        },
        {
            "email": f"sarah.johnson@{org_id}.example.com",
            "first_name": "Sarah",
            "middle_name": "Elizabeth",
            "last_name": "Johnson",
            "preferred_name": None,
            "employee_id": f"{org_id}-HR-002",
            "role_names": ["HR", "EMPLOYEE"],
            "department_code": "HR",
            "employment_start_date": date(2024, 1, 15),
            "phone_number": "+1-555-101-0009",
            "timezone_str": "America/New_York",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "MA",
            "address_line1": "100 Federal St",
            "address_line2": "Floor 10",
            "postal_code": "02110",
            "grants": [
                {
                    "grant_date": date(2024, 4, 1),
                    "total_shares": 3500,
                    "exercise_price": Decimal("20.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2025, 4, 1), "shares": 875},
                        {"vest_date": date(2026, 4, 1), "shares": 875},
                        {"vest_date": date(2027, 4, 1), "shares": 875},
                        {"vest_date": date(2028, 4, 1), "shares": 875},
                    ],
                },
            ],
        },
        {
            "email": f"alex.nguyen@{org_id}.example.com",
            "first_name": "Alex",
            "middle_name": "Tuan",
            "last_name": "Nguyen",
            "preferred_name": "Alex",
            "employee_id": f"{org_id}-ENG-003",
            "role_names": ["EMPLOYEE"],
            "department_code": "ENG",
            "employment_start_date": date(2019, 5, 1),
            "phone_number": "+1-555-101-0010",
            "timezone_str": "America/Los_Angeles",
            "marital_status": "MARRIED",
            "country": "US",
            "state": "CA",
            "address_line1": "1 Infinite Loop",
            "address_line2": None,
            "postal_code": "95014",
            "grants": [
                {
                    "grant_date": date(2019, 8, 1),
                    "total_shares": 12000,
                    "exercise_price": Decimal("6.50"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Initial hire grant — 4-year vest",
                    "vesting_events": [
                        {"vest_date": date(2020, 8, 1), "shares": 3000},
                        {"vest_date": date(2021, 8, 1), "shares": 3000},
                        {"vest_date": date(2022, 8, 1), "shares": 3000},
                        {"vest_date": date(2023, 8, 1), "shares": 3000},
                    ],
                },
                {
                    "grant_date": date(2023, 8, 1),
                    "total_shares": 5000,
                    "exercise_price": Decimal("17.00"),
                    "vesting_strategy": "SCHEDULED",
                    "notes": "Refresh grant — 3-year vest",
                    "vesting_events": [
                        {"vest_date": date(2024, 8, 1), "shares": 1666},
                        {"vest_date": date(2025, 8, 1), "shares": 1667},
                        {"vest_date": date(2026, 8, 1), "shares": 1667},
                    ],
                },
                {
                    "grant_date": date(2025, 1, 1),
                    "total_shares": 2500,
                    "exercise_price": Decimal("22.50"),
                    "vesting_strategy": "IMMEDIATE",
                    "notes": "Recognition award — immediate vest",
                    "vesting_events": [
                        {"vest_date": date(2025, 1, 1), "shares": 2500},
                    ],
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """
    Seed the database with initial orgs and optional demo data.

    Defaults:
    - Production: seed admin user only.
    - Dev / staging: seed admin plus 10 demo users with stock grants.

    Optional overrides:
    - SEED_SKIP_ADMIN_USER=true: skip admin user seeding.
    - SEED_DEMO_USERS_IN_PRODUCTION=true: allow demo user + grant seeding in production.
    """
    async with AsyncSessionLocal() as session:
        logger.info("Seeding database...")
        try:
            await session.execute(select(Org).limit(1))
        except ProgrammingError as exc:
            logger.warning("Skipping seed: database not migrated (%s)", exc)
            return

        org_ids = _parse_seed_org_ids()
        for org_id in org_ids:
            await _ensure_org(session, org_id)

        await ensure_audit_partitions_for_orgs(session)

        is_production = _is_production()
        for org_id in org_ids:
            roles = await seed_system_roles(session, org_id)
            if settings.seed_skip_admin_user:
                logger.info("[%s] Skipping admin seed (SEED_SKIP_ADMIN_USER=true).", org_id)
            else:
                admin_is_superuser = org_id == settings.default_org_id
                await _seed_user(
                    session,
                    org_id=org_id,
                    roles=roles,
                    email=settings.seed_admin_email,
                    password=settings.seed_admin_password,
                    first_name=settings.seed_admin_full_name,
                    last_name="",
                    employee_id=f"{org_id}-admin",
                    is_superuser=admin_is_superuser,
                    role_names=["ORG_ADMIN", "EMPLOYEE"],
                    must_change_password=True,
                )

            if is_production and not settings.seed_demo_users_in_production:
                logger.info(
                    "[%s] Production mode — skipping demo data "
                    "(set SEED_DEMO_USERS_IN_PRODUCTION=true to enable).",
                    org_id,
                )
                continue

            # Demo users + stock grants (always in non-prod, opt-in for prod).
            logger.info("[%s] Seeding departments...", org_id)
            dept_map: dict[str, Department] = {}
            for dept_def in _DEMO_DEPARTMENTS:
                dept = await _ensure_department(
                    session, org_id=org_id, name=dept_def["name"], code=dept_def["code"]
                )
                dept_map[dept_def["code"]] = dept

            logger.info("[%s] Seeding 10 demo users with stock grants...", org_id)
            for demo in _demo_users(org_id):
                dept = dept_map.get(demo["department_code"])
                membership = await _seed_user(
                    session,
                    org_id=org_id,
                    roles=roles,
                    email=demo["email"],
                    password=settings.seed_admin_password,
                    first_name=demo["first_name"],
                    last_name=demo["last_name"],
                    employee_id=demo["employee_id"],
                    is_superuser=False,
                    role_names=demo["role_names"],
                    department_id=dept.id if dept else None,
                    employment_start_date=demo.get("employment_start_date"),
                    middle_name=demo.get("middle_name"),
                    preferred_name=demo.get("preferred_name"),
                    phone_number=demo.get("phone_number"),
                    timezone_str=demo.get("timezone_str"),
                    marital_status=demo.get("marital_status"),
                    country=demo.get("country"),
                    state=demo.get("state"),
                    address_line1=demo.get("address_line1"),
                    address_line2=demo.get("address_line2"),
                    postal_code=demo.get("postal_code"),
                    must_change_password=True,
                )

                for grant_def in demo.get("grants", []):
                    await _seed_stock_grant(
                        session,
                        org_id=org_id,
                        membership_id=membership.id,
                        grant_date=grant_def["grant_date"],
                        total_shares=grant_def["total_shares"],
                        exercise_price=grant_def["exercise_price"],
                        vesting_strategy=grant_def["vesting_strategy"],
                        vesting_events=grant_def["vesting_events"],
                        notes=grant_def.get("notes"),
                    )

            logger.info("[%s] Demo data seeded successfully.", org_id)

    logger.info("Database seeding complete.")


if __name__ == "__main__":
    asyncio.run(init_db())
