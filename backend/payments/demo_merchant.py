"""DEMO: Minimal registrar-renewal checkout that accepts a Prava network token.

This is not a real registrar. It validates credential shape, refuses to persist
raw PAN/CVV, and returns a merchant order reference for completed renewals.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from backend.payments.demo_constants import (
    DEMO_CURRENCY,
    DEMO_MERCHANT_COUNTRY,
    DEMO_MERCHANT_NAME,
    DEMO_MERCHANT_URL,
    DEMO_PRODUCT_DESCRIPTION,
    DEMO_RENEWAL_AMOUNT,
)

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"^\d{16}$")
_CVV_PATTERN = re.compile(r"^\d{3,4}$")
_MONTH_PATTERN = re.compile(r"^(0?[1-9]|1[0-2])$")
_YEAR_PATTERN = re.compile(r"^\d{4}$")


class DemoCheckoutError(RuntimeError):
    """Raised when the DEMO merchant rejects a checkout attempt."""


@dataclass(frozen=True)
class DemoQuote:
    """Server-derived DEMO renewal quote."""

    merchant_name: str
    merchant_url: str
    merchant_country: str
    product_description: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class DemoCheckoutResult:
    """Sanitized DEMO checkout completion (no credentials)."""

    completed: bool
    merchant_order_ref: str
    amount: Decimal
    currency: str
    product_description: str
    completed_at: str


def get_demo_renewal_quote() -> DemoQuote:
    """Return the fixed DEMO domain-renewal quote."""
    # DEMO: price and merchant identity are server-derived, never browser-chosen.
    return DemoQuote(
        merchant_name=DEMO_MERCHANT_NAME,
        merchant_url=DEMO_MERCHANT_URL,
        merchant_country=DEMO_MERCHANT_COUNTRY,
        product_description=DEMO_PRODUCT_DESCRIPTION,
        amount=DEMO_RENEWAL_AMOUNT,
        currency=DEMO_CURRENCY,
    )


def complete_demo_renewal_checkout(
    *,
    domain: str,
    token: str,
    dynamic_cvv: str,
    expiry_month: str,
    expiry_year: str,
    amount: Decimal,
    currency: str,
) -> DemoCheckoutResult:
    """Accept a Prava network-token credential and complete the DEMO renewal.

    Parameters are validated for shape only. Credentials are never logged or
    returned. Amount/currency must match the fixed DEMO quote.
    """
    quote = get_demo_renewal_quote()
    if amount != quote.amount or currency.upper() != quote.currency:
        raise DemoCheckoutError("Checkout amount/currency does not match DEMO quote")

    _validate_credential_shape(
        token=token,
        dynamic_cvv=dynamic_cvv,
        expiry_month=expiry_month,
        expiry_year=expiry_year,
    )

    normalized_domain = domain.strip().lower()
    if not normalized_domain:
        raise DemoCheckoutError("Domain is required for DEMO renewal checkout")

    order_ref = (
        f"DEMO-REN-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        f"{secrets.token_hex(4).upper()}"
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "DEMO registrar checkout completed domain=%s order_ref=%s amount=%s %s",
        normalized_domain,
        order_ref,
        quote.amount,
        quote.currency,
    )
    return DemoCheckoutResult(
        completed=True,
        merchant_order_ref=order_ref,
        amount=quote.amount,
        currency=quote.currency,
        product_description=quote.product_description,
        completed_at=completed_at,
    )


def _validate_credential_shape(
    *,
    token: str,
    dynamic_cvv: str,
    expiry_month: str,
    expiry_year: str,
) -> None:
    """Validate network-token field shapes without retaining the values."""
    if not _TOKEN_PATTERN.fullmatch(token.strip()):
        raise DemoCheckoutError("Payment token must be a 16-digit network token")
    if not _CVV_PATTERN.fullmatch(dynamic_cvv.strip()):
        raise DemoCheckoutError("Dynamic CVV must be 3 or 4 digits")
    if not _MONTH_PATTERN.fullmatch(expiry_month.strip()):
        raise DemoCheckoutError("Expiry month must be 01-12")
    if not _YEAR_PATTERN.fullmatch(expiry_year.strip()):
        raise DemoCheckoutError("Expiry year must be a 4-digit year")
