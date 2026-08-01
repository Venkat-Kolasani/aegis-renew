"""DEMO checkout adapter: mandate charge → DEMO merchant → Prava report.

Credentials are held only in memory for the duration of the call and are never
persisted or returned to callers.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from decimal import Decimal

from backend.payments.demo_constants import DEMO_MERCHANT_NAME
from backend.payments.demo_merchant import (
    DemoCheckoutError,
    complete_demo_renewal_checkout,
    get_demo_renewal_quote,
)
from backend.payments.prava_charge import (
    charge_mandate,
    find_active_demo_mandate,
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


def run_demo_mandate_checkout(
    *,
    domain: str,
    customer_id: str,
    mandate_id: str | None = None,
) -> DemoRenewalCheckoutOutcome:
    """Charge a DEMO-merchant mandate, complete DEMO checkout, and report outcome.

    If mandate_id is omitted, the first active mandate matching the DEMO merchant
    name for customer_id is used.
    """
    quote = get_demo_renewal_quote()

    resolved_mandate_id = mandate_id
    if not resolved_mandate_id:
        found = find_active_demo_mandate(
            customer_id=customer_id,
            merchant_name=DEMO_MERCHANT_NAME,
        )
        if not found or not isinstance(found.get("id"), str):
            raise PravaMandateError("No active DEMO merchant mandate found for customer")
        resolved_mandate_id = found["id"]

    reference = f"aegis-demo-renewal-{secrets.token_hex(6)}"
    charge = charge_mandate(
        mandate_id=resolved_mandate_id,
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
        report = report_mandate_charge(
            mandate_id=resolved_mandate_id,
            transaction_id=charge.transaction_id,
            txn_status="DECLINED",
            amount_paid=Decimal("0.00"),
        )
        logger.error("DEMO checkout failed; reported DECLINED: %s", exc)
        return DemoRenewalCheckoutOutcome(
            completed=False,
            payment_status="declined",
            merchant_order_ref=None,
            amount=quote.amount,
            currency=quote.currency,
            prava_report_status=report.status,
            detail=str(exc),
        )

    report = report_mandate_charge(
        mandate_id=resolved_mandate_id,
        transaction_id=charge.transaction_id,
        txn_status="APPROVED",
        amount_paid=checkout.amount,
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
