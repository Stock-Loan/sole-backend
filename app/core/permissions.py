from enum import Enum
from typing import Iterable, List


class PermissionCode(str, Enum):
    # Core / org
    SYSTEM_ADMIN = "system.admin"
    ORG_DASHBOARD_VIEW = "org.dashboard.view"
    ORG_SETTINGS_VIEW = "org.settings.view"
    ORG_SETTINGS_MANAGE = "org.settings.manage"
    AUDIT_LOG_VIEW = "audit_log.view"
    IMPERSONATION_PERFORM = "impersonation.perform"

    # Users / roles / departments / ACL
    USER_VIEW = "user.view"
    USER_MANAGE = "user.manage"
    USER_ONBOARD = "user.onboard"

    ROLE_VIEW = "role.view"
    ROLE_MANAGE = "role.manage"

    DEPARTMENT_VIEW = "department.view"
    DEPARTMENT_MANAGE = "department.manage"

    PERMISSION_CATALOG_VIEW = "permission_catalog.view"
    ACL_MANAGE = "acl.manage"

    # Announcements
    ANNOUNCEMENT_VIEW = "announcement.view"
    ANNOUNCEMENT_MANAGE = "announcement.manage"

    # Stock program
    STOCK_VIEW = "stock.view"
    STOCK_MANAGE = "stock.manage"
    STOCK_PROGRAM_VIEW = "stock.program.view"
    STOCK_PROGRAM_MANAGE = "stock.program.manage"
    STOCK_GRANT_VIEW = "stock.grant.view"
    STOCK_GRANT_MANAGE = "stock.grant.manage"
    STOCK_VESTING_VIEW = "stock.vesting.view"
    STOCK_ELIGIBILITY_VIEW = "stock.eligibility.view"
    STOCK_DASHBOARD_VIEW = "stock.dashboard.view"
    STOCK_SELF_VIEW = "stock.self.view"

    # Loan origination
    LOAN_APPLY = "loan.apply"
    LOAN_VIEW_OWN = "loan.view_own"
    LOAN_CANCEL_OWN = "loan.cancel_own"
    LOAN_VIEW_ALL = "loan.view_all"
    LOAN_MANAGE = "loan.manage"
    LOAN_DASHBOARD_VIEW = "loan.dashboard.view"

    # Loan workflow / queues
    LOAN_QUEUE_HR_VIEW = "loan.queue.hr.view"
    LOAN_WORKFLOW_HR_MANAGE = "loan.workflow.hr.manage"
    LOAN_QUEUE_FINANCE_VIEW = "loan.queue.finance.view"
    LOAN_WORKFLOW_FINANCE_MANAGE = "loan.workflow.finance.manage"
    LOAN_QUEUE_LEGAL_VIEW = "loan.queue.legal.view"
    LOAN_WORKFLOW_LEGAL_MANAGE = "loan.workflow.legal.manage"
    LOAN_WORKFLOW_ASSIGN_ANY = "loan.workflow.assign.any"
    LOAN_WORKFLOW_POST_ISSUANCE_MANAGE = "loan.workflow.post_issuance.manage"
    LOAN_WORKFLOW_83B_MANAGE = "loan.workflow.83b.manage"

    # Loan documents
    LOAN_DOCUMENT_VIEW = "loan.document.view"
    LOAN_DOCUMENT_MANAGE_HR = "loan.document.manage_hr"
    LOAN_DOCUMENT_MANAGE_FINANCE = "loan.document.manage_finance"
    LOAN_DOCUMENT_MANAGE_LEGAL = "loan.document.manage_legal"
    LOAN_DOCUMENT_SELF_VIEW = "loan.document.self_view"
    LOAN_DOCUMENT_SELF_UPLOAD_83B = "loan.document.self_upload_83b"

    # Loan servicing / schedules / payments / what-if / exports
    LOAN_SCHEDULE_VIEW = "loan.schedule.view"
    LOAN_SCHEDULE_SELF_VIEW = "loan.schedule.self.view"
    LOAN_PAYMENT_VIEW = "loan.payment.view"
    LOAN_PAYMENT_RECORD = "loan.payment.record"
    LOAN_PAYMENT_REFUND = "loan.payment.refund"

    LOAN_WHAT_IF_SIMULATE = "loan.what_if.simulate"
    LOAN_WHAT_IF_SELF_SIMULATE = "loan.what_if.self.simulate"

    LOAN_EXPORT_SCHEDULE = "loan.export.schedule"
    LOAN_EXPORT_WHAT_IF = "loan.export.what_if"
    LOAN_EXPORT_SELF = "loan.export.self"

    # Reporting / exports (org)
    REPORT_STOCK_EXPORT = "report.stock.export"
    REPORT_LOAN_EXPORT = "report.loan.export"
    REPORT_AUDIT_EXPORT = "report.audit.export"

    @classmethod
    def list_all(cls) -> List[str]:
        return [code.value for code in cls]

    @classmethod
    def normalize(cls, values: Iterable[str]) -> List[str]:
        """Return unique permission codes that are valid members."""
        seen = set()
        normalized: list[str] = []
        for value in values:
            try:
                code = cls(value)
            except ValueError:
                continue
            if code.value not in seen:
                seen.add(code.value)
                normalized.append(code.value)
        return normalized
