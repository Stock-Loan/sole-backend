from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.deps import require_authenticated_user
from app.db.session import get_db
from app.models import User
from app.schemas.onboarding import BulkOnboardingResult, OnboardingResponse, OnboardingUserCreate
from app.schemas.users import UpdateMembershipRequest, UpdateUserProfileRequest, UserDetailResponse, UserListResponse
from app.models.org_membership import OrgMembership
from app.models.user import User as UserModel
from app.services import onboarding

router = APIRouter(prefix="/org/users", tags=["users"])


@router.post("", response_model=OnboardingResponse, status_code=201, summary="Onboard a single user into the current org")
async def onboard_user(
    payload: OnboardingUserCreate,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingResponse:
    try:
        user, membership, temp_password = await onboarding.onboard_single_user(db, ctx, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Duplicate user or employee_id") from exc

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
    _: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> BulkOnboardingResult:
    content = (await file.read()).decode("utf-8")
    result = await onboarding.bulk_onboard_users(db, ctx, content)
    return result


@router.get("", response_model=UserListResponse, summary="List users for the current org")
async def list_users(
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(require_authenticated_user),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    stmt = (
        select(OrgMembership, UserModel)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .where(OrgMembership.org_id == ctx.org_id)
        .order_by(UserModel.created_at)
    )
    result = await db.execute(stmt)
    items = []
    for membership, user in result.all():
        items.append({"user": user, "membership": membership})
    return UserListResponse(items=items)


@router.get("/{membership_id}", response_model=UserDetailResponse, summary="Get a user membership detail")
async def get_user(
    membership_id: str,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(require_authenticated_user),
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


@router.patch("/{membership_id}", response_model=UserDetailResponse, summary="Update membership status fields")
async def update_membership(
    membership_id: str,
    payload: UpdateMembershipRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(require_authenticated_user),
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
    await db.refresh(membership)
    await db.refresh(user)
    return UserDetailResponse(user=user, membership=membership)


@router.patch("/{membership_id}/profile", response_model=UserDetailResponse, summary="Update user profile fields")
async def update_user_profile(
    membership_id: str,
    payload: UpdateUserProfileRequest,
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    _: User = Depends(require_authenticated_user),
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
