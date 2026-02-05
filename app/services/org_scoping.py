from __future__ import annotations

from sqlalchemy import and_
from sqlalchemy.sql import Select
from sqlalchemy.orm import InstrumentedAttribute


def apply_org_filter(stmt: Select, org_id: str, *columns: InstrumentedAttribute) -> Select:
    if not columns:
        raise ValueError("apply_org_filter requires at least one org-scoped column")
    return stmt.where(*[col == org_id for col in columns])


def membership_join_condition(membership, org_id_column, membership_id_column):
    return and_(
        membership.id == membership_id_column,
        membership.org_id == org_id_column,
    )


def profile_join_condition(membership, profile):
    return and_(
        profile.membership_id == membership.id,
        profile.org_id == membership.org_id,
    )
