import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class EmployeeStockGrant(Base):
    __tablename__ = "employee_stock_grants"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint("total_shares >= 0", name="ck_stock_grants_total_shares_nonnegative"),
        CheckConstraint("exercise_price >= 0", name="ck_stock_grants_exercise_price_nonnegative"),
        ForeignKeyConstraint(
            ["org_id", "org_membership_id"],
            ["org_memberships.org_id", "org_memberships.id"],
            ondelete="CASCADE",
            name="fk_stock_grants_org_membership",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_membership_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    grant_date = Column(Date, nullable=False)
    total_shares = Column(BigInteger, nullable=False)
    exercise_price = Column(Numeric(18, 6), nullable=False)
    status = Column(String(50), nullable=False, default="ACTIVE")
    vesting_strategy = Column(String(50), nullable=False, default="SCHEDULED")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    vesting_events = relationship(
        "VestingEvent",
        back_populates="grant",
        cascade="all, delete-orphan",
        order_by="VestingEvent.vest_date",
    )
