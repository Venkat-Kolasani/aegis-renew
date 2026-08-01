"""Prava mandate and payment execution routes."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.exc import SQLAlchemyError

from backend.db.connection import DatabaseConfigurationError, session_scope
from backend.db.models import Domain
from backend.payments.prava_mandate import (
    PravaConfigurationError,
    PravaMandateError,
    create_yearly_mandate_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])


class MandateRequest(BaseModel):
    """Request body for setting up a renewal mandate."""

    domain_id: int = Field(gt=0)
    merchant_name: str = Field(min_length=1)
    merchant_url: HttpUrl
    merchant_country: str = Field(min_length=2, max_length=2)
    cap_amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    frequency: str = "yearly"


class ExecuteRequest(BaseModel):
    """Request body for a server-derived renewal payment."""

    domain_id: int = Field(gt=0)


class MandateResponse(BaseModel):
    """Contract response for mandate setup."""

    status: str
    approval_url: str


class PlaceholderResponse(BaseModel):
    """Standard response for a route not implemented yet."""

    detail: str


def _require_domain(domain_id: int) -> Domain:
    """Load a monitored domain or raise an HTTP error."""
    try:
        with session_scope() as session:
            domain = session.get(Domain, domain_id)
            if domain is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Domain not found",
                )
            # Detach scalar fields before the session closes.
            session.expunge(domain)
            return domain
    except DatabaseConfigurationError as exc:
        logger.error("Database is not configured for mandate setup")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to load domain %s for mandate setup", domain_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not load domain for mandate setup",
        ) from exc


def _cap_amount_as_decimal(raw_amount: float) -> Decimal:
    """Convert the request float to a NUMERIC(12, 2)-safe Decimal."""
    try:
        # Stringify first to avoid binary float artifacts reaching Prava.
        quantized = Decimal(str(raw_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cap_amount must be a valid decimal amount",
        ) from exc
    if quantized <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cap_amount must be greater than zero",
        )
    return quantized


@router.post("/payments/mandate", response_model=MandateResponse)
def create_mandate(body: MandateRequest) -> MandateResponse:
    """Create a merchant-locked yearly Prava mandate and return the approval URL."""
    if body.frequency != "yearly":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only yearly mandates are supported",
        )

    country = body.merchant_country.strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="merchant_country must be a 2-letter ISO country code",
        )

    currency = body.currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="currency must be a 3-letter ISO currency code",
        )

    domain = _require_domain(body.domain_id)
    merchant_url = str(body.merchant_url)
    if not merchant_url.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="merchant_url must use https",
        )

    cap_amount = _cap_amount_as_decimal(body.cap_amount)

    # Intentional placeholder identity: Aegis has no end-user auth yet, so Prava
    # customer ids are derived from the monitored domain row (stable per domain).
    # Replace with the real Aegis user identity when auth lands.
    user_id = f"aegis_domain_{domain.id}"
    user_email = f"aegis+domain-{domain.id}@example.com"

    try:
        session = create_yearly_mandate_session(
            domain=domain.domain,
            merchant_name=body.merchant_name.strip(),
            merchant_url=merchant_url,
            merchant_country=country,
            cap_amount=cap_amount,
            currency=currency,
            user_id=user_id,
            user_email=user_email,
        )
    except PravaConfigurationError as exc:
        logger.error("Prava configuration error during mandate setup: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prava is not configured",
        ) from exc
    except PravaMandateError as exc:
        logger.error("Prava mandate error during setup: %s", exc)
        if exc.status_code == 400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid mandate request for Prava",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Prava mandate setup failed",
        ) from exc

    return MandateResponse(status="pending_approval", approval_url=session.iframe_url)


@router.post(
    "/payments/execute",
    response_model=PlaceholderResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
def execute_payment(_: ExecuteRequest) -> PlaceholderResponse:
    """Execute a covered renewal when the payment service is implemented."""
    return PlaceholderResponse(detail="Payment execution is not implemented yet.")


class DemoQuoteResponse(BaseModel):
    """DEMO: Fixed registrar renewal quote."""

    merchant_name: str
    merchant_url: str
    merchant_country: str
    product_description: str
    amount: str
    currency: str


class DemoCheckoutRequest(BaseModel):
    """DEMO: Checkout body that accepts a Prava network-token credential."""

    domain: str = Field(min_length=1)
    token: str = Field(min_length=16, max_length=16)
    dynamic_cvv: str = Field(min_length=3, max_length=4)
    expiry_month: str = Field(min_length=1, max_length=2)
    expiry_year: str = Field(min_length=4, max_length=4)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)


class DemoCheckoutResponse(BaseModel):
    """DEMO: Sanitized completed checkout response."""

    completed: bool
    merchant_order_ref: str
    amount: str
    currency: str
    product_description: str
    completed_at: str


@router.get("/demo/registrar/quote", response_model=DemoQuoteResponse)
def demo_registrar_quote() -> DemoQuoteResponse:
    """DEMO: Return the fixed domain-renewal quote for Aegis Demo Registrar."""
    from backend.payments.demo_merchant import get_demo_renewal_quote

    quote = get_demo_renewal_quote()
    return DemoQuoteResponse(
        merchant_name=quote.merchant_name,
        merchant_url=quote.merchant_url,
        merchant_country=quote.merchant_country,
        product_description=quote.product_description,
        amount=f"{quote.amount:.2f}",
        currency=quote.currency,
    )


@router.post("/demo/registrar/checkout", response_model=DemoCheckoutResponse)
def demo_registrar_checkout(body: DemoCheckoutRequest) -> DemoCheckoutResponse:
    """DEMO: Complete a domain-renewal checkout with a Prava network token."""
    from backend.payments.demo_merchant import (
        DemoCheckoutError,
        complete_demo_renewal_checkout,
    )

    try:
        amount = Decimal(body.amount).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="amount must be a valid decimal",
        ) from exc

    try:
        result = complete_demo_renewal_checkout(
            domain=body.domain,
            token=body.token,
            dynamic_cvv=body.dynamic_cvv,
            expiry_month=body.expiry_month,
            expiry_year=body.expiry_year,
            amount=amount,
            currency=body.currency.upper(),
        )
    except DemoCheckoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return DemoCheckoutResponse(
        completed=result.completed,
        merchant_order_ref=result.merchant_order_ref,
        amount=f"{result.amount:.2f}",
        currency=result.currency,
        product_description=result.product_description,
        completed_at=result.completed_at,
    )
