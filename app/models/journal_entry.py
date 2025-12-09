import uuid
from decimal import Decimal

from sqlalchemy import Column, Date, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.encryption import EncryptedString


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = {"postgresql_partition_by": "LIST (org_id)"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(String, nullable=False, index=True)
    entry_date = Column(Date, nullable=False, server_default=func.current_date())
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    debit_account = Column(String(255), nullable=False)
    credit_account = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    tax_id = Column(EncryptedString(length=255), nullable=True)
    bank_account_number = Column(EncryptedString(length=255), nullable=True)
