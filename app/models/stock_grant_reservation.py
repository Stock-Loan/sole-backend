import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class StockGrantReservation(Base):
    __tablename__ = "stock_grant_reservations"
    __allow_unmapped__ = True
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "org_membership_id"],
            ["org_memberships.org_id", "org_memberships.id"],
            ondelete="CASCADE",
            name="fk_stock_reservations_org_membership",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_membership_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    grant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("employee_stock_grants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    loan_application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("loan_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shares_reserved = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False, default="SUBMITTED", server_default="SUBMITTED")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
