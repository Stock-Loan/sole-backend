from datetime import datetime, timezone
import uuid

import pytest

from app.api import deps
from app.models.org_membership import OrgMembership
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.schemas.onboarding import OnboardingUserCreate
from app.services import onboarding


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("Unexpected query in FakeSession")
        return FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


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

    db = FakeSession([None, None, None, None])

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

    user = User(
        id=user_id,
        org_id="org-1",
        email="existing.user@example.com",
        hashed_password="hash",
        is_active=True,
        is_superuser=False,
        token_version=0,
        mfa_enabled=False,
        must_change_password=False,
    )
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

    db = FakeSession([user, membership, profile])

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

