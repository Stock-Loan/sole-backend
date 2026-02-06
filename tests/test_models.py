import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-boot")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DEFAULT_ORG_ID", "default")

from app.core.security import create_access_token, decode_token
from sqlalchemy import ForeignKeyConstraint
from app.models.audit_log import AuditLog
from app.models.journal_entry import JournalEntry
from app.models.types import EncryptedString
from app.models.user import User
from app.models.loan_application import LoanApplication
from app.models.vesting_event import VestingEvent
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.stock_grant_reservation import StockGrantReservation
from app.models.org_user_profile import OrgUserProfile


def test_partitioning_metadata_present() -> None:
    assert AuditLog.__table_args__["postgresql_partition_by"] == "LIST (org_id)"
    assert JournalEntry.__table_args__["postgresql_partition_by"] == "LIST (org_id)"


def test_encrypted_string_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-1234567890abcdef")
    enc = EncryptedString()
    token = enc.process_bind_param("123-45-6789", None)
    assert token is not None
    plain = enc.process_result_value(token, None)
    assert plain == "123-45-6789"


def test_jwt_rs256_round_trip(monkeypatch, tmp_path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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

    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(priv_file))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(pub_file))
    monkeypatch.setenv("SECRET_KEY", "placeholder-secret-for-settings")

    token = create_access_token("user-123")
    decoded = decode_token(token)
    assert decoded["sub"] == "user-123"


def test_user_unique_constraint() -> None:
    constraints = [c for c in User.__table__.constraints if hasattr(c, "name")]
    assert any(getattr(c, "name", "") == "uq_users_org_email" for c in constraints)


def test_vesting_event_unique_constraint() -> None:
    constraints = [c for c in VestingEvent.__table__.constraints if hasattr(c, "name")]
    assert any(getattr(c, "name", "") == "uq_vesting_events_grant_date" for c in constraints)


def test_loan_application_constraints_present() -> None:
    constraints = [c for c in LoanApplication.__table__.constraints if hasattr(c, "name")]
    names = {getattr(c, "name", "") for c in constraints}
    assert "ck_loan_app_shares_nonneg" in names
    assert "ck_loan_app_status" in names
    assert "ck_loan_app_selection_mode" in names


def _has_composite_fk(table, local_cols: list[str], remote_cols: list[str]) -> bool:
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        if list(constraint.column_keys) != local_cols:
            continue
        targets = [element.target_fullname for element in constraint.elements]
        if targets == remote_cols:
            return True
    return False


def test_org_scoped_composite_foreign_keys_present() -> None:
    assert _has_composite_fk(
        LoanApplication.__table__,
        ["org_id", "org_membership_id"],
        ["org_memberships.org_id", "org_memberships.id"],
    )
    assert _has_composite_fk(
        EmployeeStockGrant.__table__,
        ["org_id", "org_membership_id"],
        ["org_memberships.org_id", "org_memberships.id"],
    )
    assert _has_composite_fk(
        StockGrantReservation.__table__,
        ["org_id", "org_membership_id"],
        ["org_memberships.org_id", "org_memberships.id"],
    )
    assert _has_composite_fk(
        OrgUserProfile.__table__,
        ["org_id", "membership_id"],
        ["org_memberships.org_id", "org_memberships.id"],
    )
