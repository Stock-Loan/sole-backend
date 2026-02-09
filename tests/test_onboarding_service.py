from datetime import datetime, timezone
import uuid

import pytest

from conftest import FakeAsyncSession, FakeResult, make_identity, make_user, sequence_handler

from app.api import deps
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.schemas.onboarding import OnboardingUserCreate
from app.services import onboarding


@pytest.mark.asyncio
async def test_onboard_single_user_new(monkeypatch):
    async def _noop_assign(*_args, **_kwargs):
        return None

    monkeypatch.setattr(onboarding, "assign_default_employee_role", _noop_assign)

    ctx = deps.TenantContext(org_id="org-1")
    payload = OnboardingUserCreate(
        email="new.user@example.com",
        first_name="New",
        last_name="User",
        employee_id="E-001",
        employment_status="ACTIVE",
    )

    db = FakeAsyncSession()
    db.on_execute(sequence_handler([
        FakeResult(scalar=None),
        FakeResult(scalar=None),
        FakeResult(scalar=None),
        FakeResult(scalar=None),
    ]))

    result = await onboarding.onboard_single_user(db, ctx, payload)

    assert result.user_status == "new"
    assert result.membership_status == "created"
    assert result.temporary_password
    assert result.user.org_id == "org-1"
    assert result.membership.org_id == "org-1"
    assert result.membership.user_id == result.user.id
    assert result.profile is not None
    assert result.profile.membership_id == result.membership.id


@pytest.mark.asyncio
async def test_onboard_single_user_existing_in_org(monkeypatch):
    async def _noop_assign(*_args, **_kwargs):
        return None

    monkeypatch.setattr(onboarding, "assign_default_employee_role", _noop_assign)

    ctx = deps.TenantContext(org_id="org-1")
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()

    identity = make_identity(email="existing.user@example.com")
    user = make_user(identity=identity, org_id="org-1", id=user_id)
    membership = OrgMembership(
        id=membership_id,
        org_id="org-1",
        user_id=user_id,
        employee_id="E-002",
        employment_status="ACTIVE",
        platform_status="ACTIVE",
        invitation_status="ACCEPTED",
        invited_at=datetime.now(timezone.utc),
    )
    profile = OrgUserProfile(
        id=uuid.uuid4(),
        org_id="org-1",
        membership_id=membership_id,
        full_name="Existing User",
    )

    db = FakeAsyncSession()
    db.on_execute(sequence_handler([
        FakeResult(scalar=user),
        FakeResult(scalar=membership),
        FakeResult(scalar=profile),
    ]))

    payload = OnboardingUserCreate(
        email="existing.user@example.com",
        first_name="Existing",
        last_name="User",
        employee_id="E-002",
        employment_status="ACTIVE",
    )

    result = await onboarding.onboard_single_user(db, ctx, payload)

    assert result.user_status == "existing"
    assert result.membership_status == "already_exists"
    assert result.temporary_password is None
    assert result.user.id == user_id
    assert result.membership.id == membership_id
