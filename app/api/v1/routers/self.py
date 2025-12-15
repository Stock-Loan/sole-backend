from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.permissions import PermissionCode
from app.db.session import get_db
from app.models.org import Org
from app.models.role import Role
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.self import OrgSummary, RoleSummary, SelfContextResponse

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

    return SelfContextResponse(
        org=OrgSummary.model_validate(org),
        roles=[RoleSummary.model_validate(r) for r in roles],
        permissions=sorted(perm_set),
    )
