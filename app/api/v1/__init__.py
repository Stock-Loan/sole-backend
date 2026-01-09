from fastapi import APIRouter

from app.api.v1.routers import (
    auth,
    health,
    meta,
    onboarding,
    roles,
    acls,
    departments,
    announcements,
    settings,
    self as self_router,
    stock_grants,
    stock_summary,
    stock_dashboard,
    stock_self,
    loan_dashboard,
    loan_quotes,
    loan_applications,
    loan_admin,
    loan_borrower,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(meta.router)
api_router.include_router(onboarding.router)
api_router.include_router(roles.router)
api_router.include_router(acls.router)
api_router.include_router(departments.router)
api_router.include_router(announcements.router)
api_router.include_router(settings.router)
api_router.include_router(self_router.router)
api_router.include_router(stock_grants.router)
api_router.include_router(stock_summary.router)
api_router.include_router(stock_dashboard.router)
api_router.include_router(stock_self.router)
api_router.include_router(loan_dashboard.router)
api_router.include_router(loan_quotes.router)
api_router.include_router(loan_applications.router)
api_router.include_router(loan_admin.router)
api_router.include_router(loan_borrower.router)

__all__ = ["api_router"]
