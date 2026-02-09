"""Global test fixtures and shared test infrastructure.

Provides:
- Environment variable defaults (must be set before any app import)
- FakeResult / FakeScalarResult matching SQLAlchemy Result interface
- FakeAsyncSession matching SQLAlchemy AsyncSession interface
- Execute handler helpers (entity_handler, sequence_handler)
- Model factories (make_identity, make_user, make_membership, make_profile, make_org_settings)
- Shared pytest fixtures for dependency overrides, permissions, JWT keys
"""

from __future__ import annotations

import os

# Environment defaults — must be set before importing the app, which triggers
# pydantic Settings validation on import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")
os.environ.setdefault("SEED_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Password123!")

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import get_db
from app.main import app
from app.models.identity import Identity
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.models.org_user_profile import OrgUserProfile
from app.models.user import User
from app.services import authz


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

_UNSET = object()


# ---------------------------------------------------------------------------
# FakeResult / FakeScalarResult — mimics sqlalchemy.engine.Result
# ---------------------------------------------------------------------------


class FakeScalarResult:
    """Mimics the object returned by ``Result.scalars()``."""

    def __init__(self, items: list | None = None) -> None:
        self._items = list(items or [])

    def all(self) -> list:
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None

    def unique(self) -> FakeScalarResult:
        return self


class FakeResult:
    """Mimics ``sqlalchemy.engine.Result``.

    Parameters
    ----------
    scalar:
        Value returned by ``.scalar_one_or_none()`` / ``.scalar_one()``.
        Use ``_UNSET`` (omit the kwarg) to signal "no scalar configured".
    rows:
        List of row-like tuples for ``.first()`` / ``.all()`` / ``.fetchall()``.
    items:
        List of model instances for ``.scalars().all()`` / ``.scalars().first()``.
    """

    def __init__(
        self,
        *,
        scalar: Any = _UNSET,
        rows: list | None = None,
        items: list | None = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._items = items or []

    def scalar_one_or_none(self):
        if self._scalar is _UNSET:
            return None
        return self._scalar

    def scalar_one(self):
        if self._scalar is _UNSET or self._scalar is None:
            from sqlalchemy.exc import NoResultFound

            raise NoResultFound()
        return self._scalar

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)

    def first(self):
        if self._rows:
            return self._rows[0]
        return None

    def all(self) -> list:
        return list(self._rows)

    def fetchall(self) -> list:
        return self.all()


# ---------------------------------------------------------------------------
# FakeAsyncSession — mimics sqlalchemy.ext.asyncio.AsyncSession
# ---------------------------------------------------------------------------


class FakeAsyncSession:
    """Fake ``AsyncSession`` implementing the methods production code calls.

    Configure responses via ``on_execute``, ``on_execute_return``, and ``on_get``.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.committed: bool = False
        self.flushed: bool = False
        self._execute_handlers: list[Callable] = []
        self._get_store: dict[tuple, Any] = {}
        self._default_result = FakeResult()

    # -- Configuration helpers (called from test setup) --

    def on_execute(self, handler: Callable) -> FakeAsyncSession:
        """Register a handler: ``handler(stmt) -> FakeResult | None``."""
        self._execute_handlers.append(handler)
        return self

    def on_execute_return(self, result: FakeResult) -> FakeAsyncSession:
        """Always return *result* for any ``execute()`` call."""
        self._execute_handlers.append(lambda _stmt: result)
        return self

    def on_get(self, model_class: type, pk: Any, value: Any) -> FakeAsyncSession:
        """Pre-configure ``db.get(model_class, pk)`` to return *value*."""
        self._get_store[(model_class, str(pk))] = value
        return self

    # -- AsyncSession interface --

    async def execute(self, stmt, *args, **kwargs):
        for handler in self._execute_handlers:
            result = handler(stmt)
            if result is not None:
                return result
        return self._default_result

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        # Auto-assign id on add (mirrors FakeSession in test_onboarding_service)
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = uuid4()

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        self.flushed = True
        for obj in self.added:
            if hasattr(obj, "id") and getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: Any, attribute_names: list[str] | None = None) -> None:
        pass

    async def get(self, model: type, pk: Any):
        return self._get_store.get((model, str(pk)))

    def in_transaction(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Execute handler helpers
# ---------------------------------------------------------------------------


def entity_handler(entity_class: type, result: FakeResult) -> Callable:
    """Return *result* when the query targets *entity_class*.

    Routes based on ``stmt.column_descriptions[0]["entity"]``.
    """

    def _handler(stmt):
        descriptions = getattr(stmt, "column_descriptions", None)
        if descriptions and descriptions[0].get("entity") is entity_class:
            return result
        return None

    return _handler


def sequence_handler(results: list[FakeResult]) -> Callable:
    """Return results sequentially, one per ``execute()`` call."""
    iterator = iter(results)

    def _handler(_stmt):
        try:
            return next(iterator)
        except StopIteration:
            return None

    return _handler


# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------


def make_identity(
    *,
    email: str = "user@example.com",
    password: str = "Password123!",
    mfa_enabled: bool = False,
    must_change_password: bool = False,
    **overrides: Any,
) -> Identity:
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        email=email,
        hashed_password=get_password_hash(password),
        is_active=True,
        mfa_enabled=mfa_enabled,
        mfa_method="totp" if mfa_enabled else None,
        mfa_secret_encrypted=None,
        mfa_confirmed_at=None,
        token_version=0,
        last_active_at=None,
        must_change_password=must_change_password,
    )
    defaults.update(overrides)
    return Identity(**defaults)


def make_user(
    *,
    identity: Identity | None = None,
    org_id: str = "default",
    email: str | None = None,
    **overrides: Any,
) -> User:
    if identity is None:
        identity = make_identity(email=email or "user@example.com")
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        org_id=org_id,
        identity_id=identity.id,
        email=email or identity.email,
        is_active=True,
        is_superuser=False,
    )
    defaults.update(overrides)
    user = User(**defaults)
    # Wire up the relationship so ``user.identity`` works without DB loading.
    user.identity = identity
    return user


def make_membership(
    *,
    user: User | None = None,
    org_id: str = "default",
    employee_id: str | None = None,
    employment_start_date=None,
    **overrides: Any,
) -> OrgMembership:
    if user is None:
        user = make_user(org_id=org_id)
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        org_id=org_id,
        user_id=user.id,
        employee_id=employee_id or f"E-{uuid4().hex[:6]}",
        employment_start_date=employment_start_date,
        employment_status="ACTIVE",
        platform_status="ACTIVE",
        invitation_status="ACCEPTED",
    )
    defaults.update(overrides)
    return OrgMembership(**defaults)


def make_profile(
    *,
    membership: OrgMembership,
    full_name: str = "Test User",
    marital_status: str | None = None,
    **overrides: Any,
) -> OrgUserProfile:
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        org_id=membership.org_id,
        membership_id=membership.id,
        full_name=full_name,
        marital_status=marital_status,
    )
    defaults.update(overrides)
    profile = OrgUserProfile(**defaults)
    # Wire relationship so ``membership.profile`` works.
    membership.profile = profile
    return profile


def make_org_settings(*, org_id: str = "default", **overrides: Any) -> OrgSettings:
    defaults: dict[str, Any] = dict(
        org_id=org_id,
        allow_user_data_export=True,
        allow_profile_edit=True,
        require_two_factor=False,
        mfa_required_actions=[],
        remember_device_days=30,
        session_timeout_minutes=5,
        audit_log_retention_days=180,
        inactive_user_retention_days=180,
        enforce_service_duration_rule=False,
        min_service_duration_years=None,
        enforce_min_vested_to_exercise=False,
        min_vested_shares_to_exercise=None,
        allowed_repayment_methods=["INTEREST_ONLY", "BALLOON", "PRINCIPAL_AND_INTEREST"],
        min_loan_term_months=6,
        max_loan_term_months=60,
        allowed_interest_types=["FIXED", "VARIABLE"],
        fixed_interest_rate_annual_percent=Decimal("0"),
        variable_base_rate_annual_percent=None,
        variable_margin_annual_percent=None,
        require_down_payment=False,
        down_payment_percent=None,
        policy_version=1,
    )
    defaults.update(overrides)
    return OrgSettings(**defaults)


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Replace Redis-backed limiter with in-memory limiter for all tests."""
    original = app.state.limiter
    app.state.limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",
    )
    yield
    app.state.limiter = original


@pytest.fixture
def fake_db() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def tenant_ctx() -> deps.TenantContext:
    return deps.TenantContext(org_id="default")


@pytest.fixture
def test_identity() -> Identity:
    return make_identity()


@pytest.fixture
def test_user(test_identity) -> User:
    return make_user(identity=test_identity)


@pytest.fixture
def override_deps(fake_db, test_user, tenant_ctx):
    """Standard dependency overrides: db, current_user, require_authenticated_user, tenant_context."""

    async def _get_db():
        yield fake_db

    async def _get_ctx():
        return tenant_ctx

    async def _get_user():
        return test_user

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[deps.get_current_user] = _get_user
    app.dependency_overrides[deps.require_authenticated_user] = _get_user

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def override_deps_with_permissions(fake_db, test_user, tenant_ctx):
    """Overrides that also replace the ``require_permission`` factory."""

    async def _get_db():
        yield fake_db

    async def _get_ctx():
        return tenant_ctx

    async def _get_user():
        return test_user

    def _require_permission_override(*args, **kwargs):
        async def _dep():
            return test_user

        return _dep

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[deps.get_tenant_context] = _get_ctx
    app.dependency_overrides[deps.get_current_user] = _get_user
    app.dependency_overrides[deps.require_authenticated_user] = _get_user
    app.dependency_overrides[deps.require_permission] = _require_permission_override

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def client(override_deps) -> TestClient:
    return TestClient(app)


@pytest.fixture
def client_with_permissions(override_deps_with_permissions) -> TestClient:
    return TestClient(app)


@pytest.fixture
def allow_all_permissions(monkeypatch):
    """Mock ``authz.check_permission`` to always return True."""

    async def _allow(*args, **kwargs):
        return True

    monkeypatch.setattr(authz, "check_permission", _allow)


@pytest.fixture
def deny_all_permissions(monkeypatch):
    """Mock ``authz.check_permission`` to always return False."""

    async def _deny(*args, **kwargs):
        return False

    monkeypatch.setattr(authz, "check_permission", _deny)


@pytest.fixture
def patch_jwt_keys(monkeypatch, tmp_path):
    """Generate ephemeral RSA keys and patch settings for JWT tests."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.core import security
    from app.core.settings import settings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_file = tmp_path / "priv.pem"
    pub_file = tmp_path / "pub.pem"
    priv_file.write_bytes(private_pem)
    pub_file.write_bytes(public_pem)
    monkeypatch.setattr(settings, "jwt_private_key_path", str(priv_file))
    monkeypatch.setattr(settings, "jwt_public_key_path", str(pub_file))
    monkeypatch.setattr(settings, "secret_key", "placeholder-secret-for-settings")
    security._load_private_key.cache_clear()
    security._load_public_key.cache_clear()
    yield
    security._load_private_key.cache_clear()
    security._load_public_key.cache_clear()
