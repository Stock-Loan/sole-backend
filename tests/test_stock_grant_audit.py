from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from conftest import FakeAsyncSession, FakeResult

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.vesting_event import VestingEvent
from app.schemas.stock import EmployeeStockGrantCreate, EmployeeStockGrantUpdate, VestingEventCreate
from app.services import stock_grants


@pytest.mark.asyncio
async def test_audit_logged_on_grant_create():
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=uuid4(),
        employee_id="E-1",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=membership))
    ctx = deps.TenantContext(org_id="default")
    payload = EmployeeStockGrantCreate(
        grant_date=date(2025, 1, 1),
        total_shares=100,
        exercise_price=Decimal("1.25"),
        vesting_strategy="IMMEDIATE",
        vesting_events=[],
    )

    await stock_grants.create_grant(db, ctx, membership.id, payload, actor_id=membership.user_id)
    assert any(isinstance(obj, AuditLog) for obj in db.added)


@pytest.mark.asyncio
async def test_audit_logged_on_grant_update():
    membership = OrgMembership(
        id=uuid4(),
        org_id="default",
        user_id=uuid4(),
        employee_id="E-2",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
    )
    grant = EmployeeStockGrant(
        id=uuid4(),
        org_id="default",
        org_membership_id=membership.id,
        grant_date=date(2025, 1, 1),
        total_shares=100,
        exercise_price=Decimal("1.00"),
        status="ACTIVE",
        vesting_strategy="SCHEDULED",
    )
    grant.vesting_events = [
        VestingEvent(
            id=uuid4(),
            org_id="default",
            grant_id=grant.id,
            vest_date=date(2025, 6, 1),
            shares=100,
        )
    ]

    db = FakeAsyncSession()
    db.on_execute_return(FakeResult(scalar=membership))
    ctx = deps.TenantContext(org_id="default")
    payload = EmployeeStockGrantUpdate(
        notes="Updated by admin",
        vesting_events=[
            VestingEventCreate(vest_date=date(2025, 6, 1), shares=100),
        ],
    )

    await stock_grants.update_grant(db, ctx, grant, payload, actor_id=membership.user_id)
    assert any(isinstance(obj, AuditLog) for obj in db.added)
