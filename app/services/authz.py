from typing import Iterable, Set, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PermissionCode
from app.models.access_control_list import AccessControlList
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.org_membership import OrgMembership
from app.models.user import User

if TYPE_CHECKING:
    from app.api.deps import TenantContext


async def _load_permissions(
    db: AsyncSession, user_id, org_id: str
) -> Set[str]:
    stmt = (
        select(Role.permissions)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id, UserRole.org_id == org_id, Role.org_id == org_id)
    )
    result = await db.execute(stmt)
    permissions: set[str] = set()
    for row in result.all():
        raw = row[0] or []
        if isinstance(raw, Iterable):
            for value in raw:
                try:
                    code = PermissionCode(value)
                except ValueError:
                    continue
                permissions.add(code.value)
    return permissions


async def _load_acl_permissions(
    db: AsyncSession, user_id, org_id: str, resource_type: str, resource_id: str
) -> Set[str]:
    stmt = select(AccessControlList.permissions).where(
        AccessControlList.org_id == org_id,
        AccessControlList.user_id == user_id,
        AccessControlList.resource_type == resource_type,
        AccessControlList.resource_id == resource_id,
    )
    result = await db.execute(stmt)
    permissions: set[str] = set()
    for row in result.all():
        raw = row[0] or []
        if isinstance(raw, Iterable):
            for value in raw:
                try:
                    code = PermissionCode(value)
                except ValueError:
                    continue
                permissions.add(code.value)
    return permissions


async def check_permission(
    user: User,
    ctx: "TenantContext",
    permission_code: PermissionCode | str,
    db: AsyncSession,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> bool:
    """Compute effective permissions from role buckets plus optional resource-scoped ACL entries."""
    if user.is_superuser:
        # Superuser still must belong to the current org
        membership_stmt = select(OrgMembership.id).where(
            OrgMembership.org_id == ctx.org_id,
            OrgMembership.user_id == user.id,
        )
        membership_result = await db.execute(membership_stmt)
        if membership_result.scalar_one_or_none() is None:
            return False
        return True
    target = permission_code.value if isinstance(permission_code, PermissionCode) else str(permission_code)
    permission_set = await _load_permissions(db, user.id, ctx.org_id)
    if resource_type and resource_id:
        permission_set.update(await _load_acl_permissions(db, user.id, ctx.org_id, resource_type, resource_id))
    return target in permission_set


SYSTEM_ROLE_DEFINITIONS = {
    "ORG_ADMIN": {
        "description": "Full control within the organization",
        "permissions": PermissionCode.list_all(),
    },
    "HR": {
        "description": "HR role with user management and HR loan workflow permissions",
        "permissions": PermissionCode.normalize(
            [
                PermissionCode.USER_VIEW,
                PermissionCode.USER_MANAGE,
                PermissionCode.USER_ONBOARD,
                PermissionCode.ROLE_VIEW,
                PermissionCode.DEPARTMENT_VIEW,
                PermissionCode.DEPARTMENT_MANAGE,
                PermissionCode.ANNOUNCEMENT_VIEW,
                PermissionCode.ANNOUNCEMENT_MANAGE,
                PermissionCode.PERMISSION_CATALOG_VIEW,
                PermissionCode.LOAN_QUEUE_HR_VIEW,
                PermissionCode.LOAN_WORKFLOW_HR_MANAGE,
                PermissionCode.LOAN_DOCUMENT_VIEW,
                PermissionCode.LOAN_DOCUMENT_MANAGE_HR,
                PermissionCode.LOAN_VIEW_ALL,
                PermissionCode.LOAN_DASHBOARD_VIEW,
            ]
        ),
    },
    "FINANCE": {
        "description": "Finance role for loan processing and payments",
        "permissions": PermissionCode.normalize(
            [
                PermissionCode.LOAN_VIEW_ALL,
                PermissionCode.LOAN_MANAGE,
                PermissionCode.LOAN_QUEUE_FINANCE_VIEW,
                PermissionCode.LOAN_WORKFLOW_FINANCE_MANAGE,
                PermissionCode.LOAN_DOCUMENT_VIEW,
                PermissionCode.LOAN_DOCUMENT_MANAGE_FINANCE,
                PermissionCode.LOAN_PAYMENT_VIEW,
                PermissionCode.LOAN_PAYMENT_RECORD,
                PermissionCode.LOAN_PAYMENT_REFUND,
                PermissionCode.LOAN_EXPORT_SCHEDULE,
                PermissionCode.LOAN_EXPORT_WHAT_IF,
                PermissionCode.LOAN_DASHBOARD_VIEW,
            ]
        ),
    },
    "LEGAL": {
        "description": "Legal role for loan execution and post-issuance steps",
        "permissions": PermissionCode.normalize(
            [
                PermissionCode.LOAN_VIEW_ALL,
                PermissionCode.LOAN_QUEUE_LEGAL_VIEW,
                PermissionCode.LOAN_WORKFLOW_LEGAL_MANAGE,
                PermissionCode.LOAN_WORKFLOW_POST_ISSUANCE_MANAGE,
                PermissionCode.LOAN_WORKFLOW_83B_MANAGE,
                PermissionCode.LOAN_DOCUMENT_VIEW,
                PermissionCode.LOAN_DOCUMENT_MANAGE_LEGAL,
            ]
        ),
    },
    "EMPLOYEE": {
        "description": "Base employee role with self-service access",
        "permissions": PermissionCode.normalize(
            [
                PermissionCode.ORG_DASHBOARD_VIEW,
                PermissionCode.STOCK_SELF_VIEW,
                PermissionCode.STOCK_DASHBOARD_VIEW,
                PermissionCode.LOAN_APPLY,
                PermissionCode.LOAN_VIEW_OWN,
                PermissionCode.LOAN_CANCEL_OWN,
                PermissionCode.LOAN_DOCUMENT_SELF_VIEW,
                PermissionCode.LOAN_DOCUMENT_SELF_UPLOAD_83B,
                PermissionCode.LOAN_SCHEDULE_SELF_VIEW,
                PermissionCode.LOAN_WHAT_IF_SELF_SIMULATE,
                PermissionCode.LOAN_EXPORT_SELF,
            ]
        ),
    },
}


async def seed_system_roles(db: AsyncSession, org_id: str) -> dict[str, Role]:
    """
    Ensure system roles exist for the org, returning a name->Role mapping.
    """
    existing_stmt = select(Role).where(Role.org_id == org_id, Role.is_system_role.is_(True))
    existing_result = await db.execute(existing_stmt)
    existing = {role.name: role for role in existing_result.scalars().all()}

    created: dict[str, Role] = {}
    for name, definition in SYSTEM_ROLE_DEFINITIONS.items():
        role = existing.get(name)
        if role:
            role.permissions = definition["permissions"]
            role.description = definition["description"]
        else:
            role = Role(
                org_id=org_id,
                name=name,
                description=definition["description"],
                is_system_role=True,
                permissions=definition["permissions"],
            )
            db.add(role)
        created[name] = role
    await db.commit()
    for role in created.values():
        await db.refresh(role)
    return created


async def ensure_user_in_role(db: AsyncSession, org_id: str, user_id, role: Role) -> None:
    stmt = select(UserRole).where(
        UserRole.org_id == org_id, UserRole.user_id == user_id, UserRole.role_id == role.id
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        return
    db.add(UserRole(org_id=org_id, user_id=user_id, role_id=role.id))
    await db.commit()


async def ensure_org_admin_for_seed_user(db: AsyncSession, seed_user_id, org_ids: list[str]) -> None:
    """
    For the seed user, ensure ORG_ADMIN in each provided org_id (creating roles if needed).
    """
    for org_id in org_ids:
        roles = await seed_system_roles(db, org_id)
        admin_role = roles.get("ORG_ADMIN")
        if admin_role:
            await ensure_user_in_role(db, org_id, seed_user_id, admin_role)


async def assign_default_employee_role(
    db: AsyncSession,
    org_id: str,
    user_id,
) -> None:
    """
    Assign EMPLOYEE role if present; used during onboarding to avoid zero-permission users.
    """
    roles = await seed_system_roles(db, org_id)
    employee_role = roles.get("EMPLOYEE")
    if not employee_role:
        return
    await ensure_user_in_role(db, org_id, user_id, employee_role)
