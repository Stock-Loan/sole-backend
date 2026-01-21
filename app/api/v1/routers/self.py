from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.department import Department
from app.models.org import Org
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.self import OrgSummary, RoleSummary, SelfContextResponse
from app.schemas.settings import OrgPolicyResponse
from app.schemas.users import UserDetailResponse
from app.services import pbgc_rates, settings as settings_service

router = APIRouter(prefix="/self", tags=["self"])


@router.get("/context", response_model=SelfContextResponse, summary="Get current org context, roles, and permissions")
async def get_self_context(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> SelfContextResponse:
    org_stmt = select(Org).where(Org.id == ctx.org_id)
    org_result = await db.execute(org_stmt)
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    roles_stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == current_user.id, Role.org_id == ctx.org_id, UserRole.org_id == ctx.org_id)
    )
    roles_result = await db.execute(roles_stmt)
    roles = roles_result.scalars().all()

    # Effective permissions from roles (ACLs not included here)
    perm_set: set[str] = set()
    for role in roles:
        for code in role.permissions or []:
            try:
                perm_set.add(PermissionCode(code).value)
            except ValueError:
                continue

    # Get org settings for session timeout
    org_settings = await settings_service.get_org_settings(db, ctx)

    return SelfContextResponse(
        org=OrgSummary.model_validate(org),
        roles=[RoleSummary.model_validate(r) for r in roles],
        permissions=sorted(perm_set),
        session_timeout_minutes=org_settings.session_timeout_minutes,
    )


@router.get("/policy", response_model=OrgPolicyResponse, summary="Get current org policy")
async def get_self_policy(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> OrgPolicyResponse:
    settings = await settings_service.get_org_settings(db, ctx)
    latest_rate = await pbgc_rates.get_latest_annual_rate(db)
    response = OrgPolicyResponse.model_validate(settings)
    if latest_rate is not None:
        response = response.model_copy(
            update={"variable_base_rate_annual_percent": latest_rate}
        )
    return response


@router.get("/profile", response_model=UserDetailResponse, summary="Get current user profile")
async def get_self_profile(
    current_user: User = Depends(deps.require_authenticated_user),
    ctx: deps.TenantContext = Depends(deps.get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> UserDetailResponse:
    from app.models.org_membership import OrgMembership
    from app.models.user import User as UserModel

    stmt = (
        select(OrgMembership, UserModel, Department)
        .join(UserModel, OrgMembership.user_id == UserModel.id)
        .join(Department, OrgMembership.department_id == Department.id, isouter=True)
        .where(OrgMembership.org_id == ctx.org_id, OrgMembership.user_id == current_user.id)
        .options(selectinload(UserModel.roles).selectinload(UserRole.role))
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership, user, dept = row
    membership.department_name = dept.name if dept else None
    roles = [ur.role for ur in user.roles if ur.org_id == ctx.org_id]
    return UserDetailResponse(user=user, membership=membership, roles=roles)
