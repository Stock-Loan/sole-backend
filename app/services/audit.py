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


def _diff_values(old: Any, new: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    if isinstance(old, dict) and isinstance(new, dict):
        keys = set(old.keys()) | set(new.keys())
        for key in keys:
            path = f"{prefix}.{key}" if prefix else str(key)
            changes.update(_diff_values(old.get(key), new.get(key), path))
        return changes
    if old != new:
        changes[prefix or "value"] = {"from": old, "to": new}
    return changes


def _build_summary(action: str, changes: dict[str, dict[str, Any]] | None) -> str:
    if not changes:
        return action
    keys = list(changes.keys())
    snippet = ", ".join(keys[:3])
    suffix = "..." if len(keys) > 3 else ""
    return f"{action}: {snippet}{suffix}"


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
    impersonator_id: Any | None = None,
) -> None:
    serialized_old = serialize_for_audit(old_value) if old_value is not None else None
    serialized_new = serialize_for_audit(new_value) if new_value is not None else None
    changes = None
    if serialized_old is not None or serialized_new is not None:
        changes = _diff_values(serialized_old or {}, serialized_new or {})
        if not changes:
            changes = None
    summary = _build_summary(action, changes)
    entry = AuditLog(
        org_id=ctx.org_id,
        actor_id=actor_id,
        impersonator_id=impersonator_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_value=serialized_old,
        new_value=serialized_new,
        changes=changes,
        summary=summary,
    )
    db.add(entry)


def record_audit_log_for_user(
    db: AsyncSession,
    ctx: deps.TenantContext,
    current_user,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    old_value: Any | None = None,
    new_value: Any | None = None,
) -> None:
    """Convenience wrapper that automatically captures impersonator_id from user."""
    impersonator_id = getattr(current_user, "_impersonator_user_id", None)
    record_audit_log(
        db,
        ctx,
        actor_id=current_user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_value=old_value,
        new_value=new_value,
        impersonator_id=impersonator_id,
    )
