from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.employee_stock_grant import EmployeeStockGrant
from app.models.org_membership import OrgMembership
from app.models.org_settings import OrgSettings
from app.schemas.loan import (
    LoanQuoteOption,
    LoanQuoteRequest,
    LoanQuoteResponse,
    LoanSelectionMode,
    LoanShareAllocation,
)
from app.schemas.settings import LoanInterestType, LoanRepaymentMethod
from app.services import eligibility, settings as settings_service, stock_reservations, vesting_engine


@dataclass(frozen=True)
class LoanQuoteError(ValueError):
    code: str
    message: str
    details: dict

    def __str__(self) -> str:
        return self.message


TWOPLACES = Decimal("0.01")
ALLOCATION_STRATEGY_OLDEST = "OLDEST_VESTED_FIRST"


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _monthly_rate(annual_rate_percent: Decimal) -> Decimal:
    return annual_rate_percent / Decimal("1200")


def _payment_principal_and_interest(principal: Decimal, annual_rate_percent: Decimal, term_months: int) -> Decimal:
    if term_months <= 0:
        return Decimal("0")
    rate = _monthly_rate(annual_rate_percent)
    if rate == 0:
        return (principal / Decimal(term_months)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    factor = (Decimal("1") + rate) ** term_months
    payment = principal * rate * factor / (factor - Decimal("1"))
    return payment.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _payment_interest_only(principal: Decimal, annual_rate_percent: Decimal) -> Decimal:
    rate = _monthly_rate(annual_rate_percent)
    payment = principal * rate
    return payment.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _loan_quote_option(
    *,
    interest_type: LoanInterestType,
    repayment_method: LoanRepaymentMethod,
    term_months: int,
    annual_rate: Decimal,
    principal: Decimal,
) -> LoanQuoteOption:
    if repayment_method == LoanRepaymentMethod.PRINCIPAL_AND_INTEREST:
        monthly_payment = _payment_principal_and_interest(principal, annual_rate, term_months)
        total_payable = (monthly_payment * Decimal(term_months)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    else:
        monthly_payment = _payment_interest_only(principal, annual_rate)
        total_payable = (monthly_payment * Decimal(term_months) + principal).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )
    total_interest = (total_payable - principal).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return LoanQuoteOption(
        interest_type=interest_type,
        repayment_method=repayment_method,
        term_months=term_months,
        nominal_annual_rate=annual_rate.quantize(TWOPLACES, rounding=ROUND_HALF_UP),
        estimated_monthly_payment=monthly_payment,
        total_payable=total_payable,
        total_interest=total_interest,
    )


def _allocate_shares_oldest_first(
    *,
    grant_summaries: Iterable[vesting_engine.GrantVestingSummary],
    shares_to_exercise: int,
) -> tuple[Decimal, list[LoanShareAllocation]]:
    remaining = shares_to_exercise
    price = Decimal("0")
    allocations: list[LoanShareAllocation] = []
    for summary in sorted(grant_summaries, key=lambda item: item.grant_date):
        if remaining <= 0:
            break
        if summary.vested_shares <= 0:
            continue
        take = min(summary.vested_shares, remaining)
        if take <= 0:
            continue
        exercise_price = _as_decimal(summary.exercise_price)
        purchase_price = (exercise_price * Decimal(take)).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )
        allocations.append(
            LoanShareAllocation(
                grant_id=summary.grant_id,
                grant_date=summary.grant_date,
                shares=int(take),
                exercise_price=exercise_price.quantize(TWOPLACES, rounding=ROUND_HALF_UP),
                purchase_price=purchase_price,
            )
        )
        price += purchase_price
        remaining -= take
    return price, allocations


def _resolve_shares_to_exercise(
    *,
    selection_mode: LoanSelectionMode,
    selection_value: Decimal,
    total_exercisable_shares: int,
) -> int:
    selection_mode = LoanSelectionMode(selection_mode)
    if total_exercisable_shares <= 0:
        raise LoanQuoteError(
            code="no_exercisable_shares",
            message="No exercisable shares are available",
            details={"field": "selection_value", "total_exercisable_shares": total_exercisable_shares},
        )
    if selection_mode == LoanSelectionMode.PERCENT:
        if selection_value <= 0 or selection_value > 100:
            raise LoanQuoteError(
                code="invalid_selection",
                message="Selection percent must be between 0 and 100",
                details={
                    "field": "selection_value",
                    "constraint": "0 < value <= 100",
                    "selection_value": str(selection_value),
                },
            )
        shares = int(
            (Decimal(total_exercisable_shares) * selection_value / Decimal("100")).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
    else:
        if selection_value <= 0:
            raise LoanQuoteError(
                code="invalid_selection",
                message="Selection shares must be greater than zero",
                details={
                    "field": "selection_value",
                    "constraint": "value > 0",
                    "selection_value": str(selection_value),
                },
            )
        if selection_value != selection_value.to_integral_value():
            raise LoanQuoteError(
                code="invalid_selection",
                message="Selection shares must be a whole number",
                details={
                    "field": "selection_value",
                    "constraint": "integer",
                    "selection_value": str(selection_value),
                },
            )
        shares = int(selection_value)
    if shares <= 0:
        raise LoanQuoteError(
            code="invalid_selection",
            message="Selection results in zero exercisable shares",
            details={
                "field": "selection_value",
                "constraint": "results_in_positive_shares",
                "selection_value": str(selection_value),
            },
        )
    if shares > total_exercisable_shares:
        raise LoanQuoteError(
            code="shares_exceed_eligibility",
            message="Requested shares exceed exercisable shares",
            details={
                "field": "selection_value",
                "requested_shares": shares,
                "total_exercisable_shares": total_exercisable_shares,
            },
        )
    return shares


def build_loan_quote_from_data(
    *,
    membership: OrgMembership,
    org_settings: OrgSettings,
    grants: list[EmployeeStockGrant],
    request: LoanQuoteRequest,
    as_of_date: date,
    reserved_by_grant: dict | None = None,
) -> LoanQuoteResponse:
    reserved_by_grant = reserved_by_grant or {}
    selection_mode = LoanSelectionMode(request.selection_mode)
    desired_interest_type = (
        LoanInterestType(request.desired_interest_type)
        if request.desired_interest_type is not None
        else None
    )
    desired_repayment_method = (
        LoanRepaymentMethod(request.desired_repayment_method)
        if request.desired_repayment_method is not None
        else None
    )
    totals = vesting_engine.aggregate_vesting(grants, as_of_date)
    total_reserved = sum(reserved_by_grant.values())
    available_vested = max(totals.total_vested_shares - total_reserved, 0)
    eligibility_totals = vesting_engine.VestingTotals(
        total_granted_shares=totals.total_granted_shares,
        total_vested_shares=totals.total_vested_shares,
        total_unvested_shares=totals.total_unvested_shares,
        next_vesting_event=totals.next_vesting_event,
    )
    eligibility_result = eligibility.evaluate_eligibility_from_totals(
        membership=membership,
        org_settings=org_settings,
        totals=eligibility_totals,
        as_of_date=as_of_date,
    )
    if not eligibility_result.eligible_to_exercise:
        raise LoanQuoteError(
            code="exercise_ineligible",
            message="Employee is not eligible to exercise shares",
            details={"reasons": [reason.model_dump() for reason in eligibility_result.reasons]},
        )

    shares_to_exercise = _resolve_shares_to_exercise(
        selection_mode=selection_mode,
        selection_value=request.selection_value,
        total_exercisable_shares=available_vested,
    )

    grant_summaries = vesting_engine.build_grant_summaries(grants, as_of_date)
    adjusted_summaries = []
    for summary in grant_summaries:
        reserved = reserved_by_grant.get(summary.grant_id, 0)
        available = max(summary.vested_shares - reserved, 0)
        adjusted_summaries.append(
            vesting_engine.GrantVestingSummary(
                grant_id=summary.grant_id,
                grant_date=summary.grant_date,
                total_shares=summary.total_shares,
                vested_shares=available,
                unvested_shares=summary.unvested_shares,
                exercise_price=summary.exercise_price,
            )
        )
    purchase_price, allocation = _allocate_shares_oldest_first(
        grant_summaries=adjusted_summaries, shares_to_exercise=shares_to_exercise
    )

    down_payment_percent = _as_decimal(org_settings.down_payment_percent or 0)
    if org_settings.require_down_payment:
        down_payment_amount = (purchase_price * down_payment_percent / Decimal("100")).quantize(
            TWOPLACES, rounding=ROUND_HALF_UP
        )
    else:
        down_payment_amount = Decimal("0")
    loan_principal = (purchase_price - down_payment_amount).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    allowed_interest_types = [LoanInterestType(value) for value in (org_settings.allowed_interest_types or [])]
    allowed_repayment_methods = [LoanRepaymentMethod(value) for value in (org_settings.allowed_repayment_methods or [])]

    if desired_interest_type and desired_interest_type not in allowed_interest_types:
        raise LoanQuoteError(
            code="interest_type_not_allowed",
            message="Desired interest type is not allowed by org policy",
            details={
                "field": "desired_interest_type",
                "allowed_interest_types": [item.value for item in allowed_interest_types],
            },
        )
    if desired_repayment_method and desired_repayment_method not in allowed_repayment_methods:
        raise LoanQuoteError(
            code="repayment_method_not_allowed",
            message="Desired repayment method is not allowed by org policy",
            details={
                "field": "desired_repayment_method",
                "allowed_repayment_methods": [item.value for item in allowed_repayment_methods],
            },
        )

    min_term = int(org_settings.min_loan_term_months or 0)
    max_term = int(org_settings.max_loan_term_months or 0)
    term_months = request.desired_term_months or min_term
    if term_months < min_term or term_months > max_term:
        raise LoanQuoteError(
            code="invalid_term",
            message="Loan term is outside of org policy bounds",
            details={
                "field": "desired_term_months",
                "min_loan_term_months": min_term,
                "max_loan_term_months": max_term,
            },
        )

    interest_types = [desired_interest_type] if desired_interest_type else allowed_interest_types
    repayment_methods = [desired_repayment_method] if desired_repayment_method else allowed_repayment_methods

    options: list[LoanQuoteOption] = []
    for interest_type in interest_types:
        if interest_type == LoanInterestType.FIXED:
            annual_rate = _as_decimal(org_settings.fixed_interest_rate_annual_percent or 0)
        else:
            base = _as_decimal(org_settings.variable_base_rate_annual_percent or 0)
            margin = _as_decimal(org_settings.variable_margin_annual_percent or 0)
            if (org_settings.variable_base_rate_annual_percent is None) or (
                org_settings.variable_margin_annual_percent is None
            ):
                raise LoanQuoteError(
                    code="variable_rate_missing",
                    message="Variable rate settings are required for variable interest quotes",
                    details={"variable_base_rate_annual_percent": str(base), "variable_margin_annual_percent": str(margin)},
                )
            annual_rate = base + margin
        for repayment_method in repayment_methods:
            options.append(
                _loan_quote_option(
                    interest_type=interest_type,
                    repayment_method=repayment_method,
                    term_months=term_months,
                    annual_rate=annual_rate,
                    principal=loan_principal,
                )
            )

    if not options:
        raise LoanQuoteError(
            code="no_quote_options",
            message="No loan quote options could be generated",
            details={},
        )

    return LoanQuoteResponse(
        as_of_date=as_of_date,
        selection_mode=selection_mode,
        selection_value=request.selection_value,
        total_exercisable_shares=available_vested,
        shares_to_exercise=shares_to_exercise,
        purchase_price=purchase_price.quantize(TWOPLACES, rounding=ROUND_HALF_UP),
        down_payment_amount=down_payment_amount,
        loan_principal=loan_principal,
        options=options,
        eligibility_result=eligibility_result,
        allocation_strategy=ALLOCATION_STRATEGY_OLDEST,
        allocation=allocation,
    )


async def calculate_loan_quote(
    db: AsyncSession,
    ctx: deps.TenantContext,
    membership: OrgMembership,
    request: LoanQuoteRequest,
) -> LoanQuoteResponse:
    as_of_date = request.as_of_date or date.today()
    org_settings = await settings_service.get_org_settings(db, ctx)
    grants = await vesting_engine.load_active_grants(db, ctx, membership.id)
    reserved_by_grant = await stock_reservations.get_active_reservations_by_grant(
        db, ctx, membership_id=membership.id, grant_ids=[grant.id for grant in grants]
    )
    return build_loan_quote_from_data(
        membership=membership,
        org_settings=org_settings,
        grants=grants,
        request=request,
        as_of_date=as_of_date,
        reserved_by_grant=reserved_by_grant,
    )
