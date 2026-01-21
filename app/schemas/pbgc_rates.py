from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PbgcMidTermRateEntry(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda value: str(value)},
    )

    year: int
    month: int
    annual_rate_percent: Decimal | None
    monthly_rate_percent: Decimal | None
    source_url: str
    fetched_at: datetime


class PbgcRateRefreshResponse(BaseModel):
    updated_rows: int
    fetched_at: datetime
