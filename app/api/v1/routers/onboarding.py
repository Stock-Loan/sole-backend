import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.db.session import get_db
from app.core.permissions import PermissionCode
from app.models import User
from app.schemas.onboarding import BulkOnboardingResult, OnboardingResponse, OnboardingUserCreate
from app.schemas.users import (
    BulkDeleteRequest,
    UpdateMembershipRequest,
    UpdateUserProfileRequest,
    UserDetailResponse,
    UserListResponse,
)
from app.models.org_membership import OrgMembership
from app.models.user import User as UserModel
from app.models.user_role import UserRole
from app.services import onboarding

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/org/users", tags=["users"])


@router.post("", response_model=OnboardingResponse, status_code=201, summary="Onboard a single user into the current org")
async def onboard_user(
    payload: OnboardingUserCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
    db: AsyncSession = Depends(get_db),
) -> OnboardingResponse:
    try:
        user, membership, temp_password = await onboarding.onboard_single_user(db, ctx, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate user or employee_id") from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return OnboardingResponse(user=user, membership=membership, temporary_password=temp_password)


@router.get(
    "/bulk/template",
    response_class=StreamingResponse,
    summary="Download CSV template for bulk onboarding",
)
async def download_template() -> StreamingResponse:
    content = onboarding.generate_csv_template()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="onboarding_template.csv"'},
    )


@router.post(
    "/bulk",
    response_model=BulkOnboardingResult,
    summary="Bulk onboard users via CSV upload",
)
async def bulk_onboard(
    file: UploadFile,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_ONBOARD)),
    db: AsyncSession = Depends(get_db),
) -> BulkOnboardingResult:
    content = (await file.read()).decode("utf-8")
    result = await onboarding.bulk_onboard_users(db, ctx, content)
    return result


@router.get("", response_model=UserListResponse, summary="List users for the current org")
async def list_users(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    employment_status: str | None = None,
    platform_status: str | None = None,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    offset = (page - 1) * page_size

    filters = [OrgMembership.org_id == ctx.org_id]
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                UserModel.full_name.ilike(pattern),
                UserModel.preferred_name.ilike(pattern),
                UserModel.email.ilike(pattern),
            )
        )
    if employment_status:
        filters.append(OrgMembership.employment_status.ilike(employment_status.strip()))
    if platform_status:
        filters.append(OrgMembership.platform_status.ilike(platform_status.strip()))

    base_stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(*filters)
        .order_by(UserModel.created_at)
    )
    count_stmt = (
        select(func.count())
        .select_from(OrgMembership)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(*filters)
    )
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    result = await db.execute(base_stmt.offset(offset).limit(page_size))
    items = []
    for membership, user in result.all():
        items.append({"user": user, "membership": membership})
    return UserListResponse(items=items, total=total)


@router.get("/{membership_id}", response_model=UserDetailResponse, summary="Get a user membership detail")
async def get_user(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row
    return UserDetailResponse(user=user, membership=membership)


@router.delete("/{membership_id}", status_code=204, summary="Delete a user membership and user if no other memberships")
async def delete_user(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    await db.delete(membership)
    await db.commit()

    # If user has no other memberships, delete user record
    other_stmt = select(OrgMembership.id).where(OrgMembership.user_id == user.id)
    other_result = await db.execute(other_stmt)
    if not other_result.first():
        await db.delete(user)
        await db.commit()
    return None


@router.post("/bulk/delete", status_code=200, summary="Bulk delete user memberships by ID")
async def bulk_delete_users(
    payload: BulkDeleteRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deleted = 0
    not_found: list[str] = []
    for membership_id in payload.membership_ids:
        stmt = (
            select(OrgMembership, UserModel)
            .join(UserModel, OrgMembership.user_id == UserModel.id)
            .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
        )
        result = await db.execute(stmt)
        row = result.one_or_none()
        if not row:
            not_found.append(membership_id)
            continue
        membership, user = row
        await db.delete(membership)
        await db.commit()
        other_stmt = select(OrgMembership.id).where(OrgMembership.user_id == user.id)
        other_result = await db.execute(other_stmt)
        if not other_result.first():
            await db.delete(user)
            await db.commit()
        deleted += 1
    return {"deleted": deleted, "not_found": not_found}


@router.patch("/{membership_id}", response_model=UserDetailResponse, summary="Update membership status fields")
async def update_membership(
    membership_id: str,
    payload: UpdateMembershipRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    if payload.employment_status:
        membership.employment_status = payload.employment_status
    if payload.platform_status:
        membership.platform_status = payload.platform_status

    db.add(membership)
    await db.commit()
    # Remove role assignments and revoke sessions if platform/employment status is not ACTIVE
    if (
        (membership.platform_status and membership.platform_status.upper() != "ACTIVE")
        or (membership.employment_status and membership.employment_status.upper() != "ACTIVE")
    ):
        await db.execute(
            delete(UserRole).where(UserRole.org_id == ctx.org_id, UserRole.user_id == membership.user_id)
        )
        user.token_version += 1
        db.add(user)
        await db.commit()
        logger.info(
            "Auto-removed roles due to inactive status",
            extra={
                "org_id": ctx.org_id,
                "user_id": str(membership.user_id),
                "platform_status": membership.platform_status,
                "employment_status": membership.employment_status,
            },
        )
    await db.refresh(membership)
    await db.refresh(user)
    return UserDetailResponse(user=user, membership=membership)


@router.patch("/{membership_id}/profile", response_model=UserDetailResponse, summary="Update user profile fields")
async def update_user_profile(
    membership_id: str,
    payload: UpdateUserProfileRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(deps.require_permission(PermissionCode.USER_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.id == membership_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user = row

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    # maintain full_name if first/last change
    if payload.first_name or payload.last_name:
        first = payload.first_name if payload.first_name is not None else user.first_name or ""
        last = payload.last_name if payload.last_name is not None else user.last_name or ""
        user.full_name = f"{first} {last}".strip()

    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.refresh(membership)
    return UserDetailResponse(user=user, membership=membership)
