"""DEMO checkout adapter: mandate charge → DEMO merchant → Prava report.

Credentials are held only in memory for the duration of the call and are never
persisted or returned to callers.
"""

from __future__ import annotations

import logging
import math
import secrets
from dataclasses import dataclass
from decimal import Decimal

from backend.agent.policy import RenewalQuote
from backend.payments.demo_merchant import (
    DemoCheckoutError,
    complete_demo_renewal_checkout,
)
from backend.payments.prava_charge import (
    ProviderMandate,
    charge_mandate,
    report_mandate_charge,
)
from backend.payments.prava_mandate import PravaMandateError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemoRenewalCheckoutOutcome:
    """Sanitized end-to-end DEMO renewal result (no credentials)."""

    completed: bool
    payment_status: str
    merchant_order_ref: str | None
    amount: Decimal
    currency: str
    prava_report_status: str | None
    detail: str


class PaymentAuthorizationError(RuntimeError):
    """Raised when the locked decision-to-payment contract is not satisfied."""


def on_agent_decision(
    domain: str, decision: str, amount: float, reason: str
) -> None:
    """Validate a freshly covered auto-renew decision before payment execution."""
    if not domain.strip() or decision != "auto_renew":
        raise PaymentAuthorizationError("Payment decision is not authorized")
    if not math.isfinite(amount) or amount <= 0:
        raise PaymentAuthorizationError("Payment amount is not authorized")
    if not reason.strip():
        raise PaymentAuthorizationError("Payment reason is not authorized")
    logger.info("Covered renewal authorized domain=%s", domain.strip().lower())


def run_demo_mandate_checkout(
    *,
    domain: str,
    provider_mandate: ProviderMandate,
    quote: RenewalQuote,
) -> DemoRenewalCheckoutOutcome:
    """Charge a resolved mandate, complete DEMO checkout, and report outcome."""
    reference = f"aegis-demo-renewal-{secrets.token_hex(6)}"
    charge = charge_mandate(
        mandate_id=provider_mandate.provider_id,
        amount=quote.amount,
        reference=reference,
    )

    try:
        checkout = complete_demo_renewal_checkout(
            domain=domain,
            token=charge.credentials.token,
            dynamic_cvv=charge.credentials.dynamic_cvv,
            expiry_month=charge.credentials.expiry_month,
            expiry_year=charge.credentials.expiry_year,
            amount=quote.amount,
            currency=quote.currency,
        )
    except DemoCheckoutError as exc:
        report_status: str | None = None
        try:
            report = report_mandate_charge(
                mandate_id=provider_mandate.provider_id,
                transaction_id=charge.transaction_id,
                txn_status="DECLINED",
                amount_paid=Decimal("0.00"),
            )
            report_status = report.status
            logger.error(
                "DEMO checkout failed; DECLINED reported exception_type=%s",
                type(exc).__name__,
            )
        except PravaMandateError as report_exc:
            logger.error(
                "DEMO checkout and DECLINED report failed "
                "checkout_exception_type=%s report_exception_type=%s",
                type(exc).__name__,
                type(report_exc).__name__,
            )
        return DemoRenewalCheckoutOutcome(
            completed=False,
            payment_status=(
                "declined" if report_status is not None else "declined_report_failed"
            ),
            merchant_order_ref=None,
            amount=quote.amount,
            currency=quote.currency,
            prava_report_status=report_status,
            detail="DEMO merchant checkout was not completed",
        )

    try:
        report = report_mandate_charge(
            mandate_id=provider_mandate.provider_id,
            transaction_id=charge.transaction_id,
            txn_status="APPROVED",
            amount_paid=checkout.amount,
        )
    except PravaMandateError as report_exc:
        logger.error(
            "DEMO checkout completed but APPROVED report failed exception_type=%s",
            type(report_exc).__name__,
        )
        return DemoRenewalCheckoutOutcome(
            completed=True,
            payment_status="reconciliation_required",
            merchant_order_ref=checkout.merchant_order_ref,
            amount=checkout.amount,
            currency=checkout.currency,
            prava_report_status=None,
            detail=(
                "DEMO registrar checkout completed but Prava APPROVED report failed; "
                "reconcile the mandate charge manually"
            ),
        )

    return DemoRenewalCheckoutOutcome(
        completed=True,
        payment_status="completed",
        merchant_order_ref=checkout.merchant_order_ref,
        amount=checkout.amount,
        currency=checkout.currency,
        prava_report_status=report.status,
        detail="DEMO registrar checkout completed and reported APPROVED to Prava",
    )
