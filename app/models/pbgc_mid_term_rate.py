from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, Numeric, String, UniqueConstraint, func

from app.db.base import Base


class PbgcMidTermRate(Base):
    __tablename__ = "pbgc_mid_term_rates"
    __allow_unmapped__ = True
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_pbgc_mid_term_rates_year_month"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, index=True)
    month = Column(Integer, nullable=False, index=True)
    annual_rate_percent = Column(Numeric(10, 4), nullable=True)
    monthly_rate_percent = Column(Numeric(10, 4), nullable=True)
    source_url = Column(String(500), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )