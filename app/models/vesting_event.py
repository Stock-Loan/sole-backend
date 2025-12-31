import uuid

from sqlalchemy import BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class VestingEvent(Base):
    __tablename__ = "vesting_events"
    __allow_unmapped__ = True
    __table_args__ = (
        CheckConstraint("shares >= 0", name="ck_vesting_events_shares_nonnegative"),
        UniqueConstraint("grant_id", "vest_date", name="uq_vesting_events_grant_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    grant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("employee_stock_grants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vest_date = Column(Date, nullable=False, index=True)
    shares = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    grant = relationship("EmployeeStockGrant", back_populates="vesting_events")
