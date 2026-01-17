from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.audit_log import AuditLog


def serialize_for_audit(value: Any) -> Any:
    return jsonable_encoder(
        value,
        custom_encoder={
            Decimal: lambda v: str(v),
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat(),
        },
    )


def model_snapshot(model: Any, *, exclude: Iterable[str] | None = None) -> dict[str, Any]:
    if model is None:
        return {}
    excluded = set(exclude or [])
    data: dict[str, Any] = {}
    for column in model.__table__.columns:
        name = column.name
        if name in excluded:
            continue
        data[name] = getattr(model, name)
    return serialize_for_audit(data)


def record_audit_log(
    db: AsyncSession,
    ctx: deps.TenantContext,
    *,
    actor_id,
    action: str,
    resource_type: str,
    resource_id: str,
    old_value: Any | None = None,
    new_value: Any | None = None,
) -> None:
    entry = AuditLog(
        org_id=ctx.org_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_value=serialize_for_audit(old_value) if old_value is not None else None,
        new_value=serialize_for_audit(new_value) if new_value is not None else None,
    )
    db.add(entry)
