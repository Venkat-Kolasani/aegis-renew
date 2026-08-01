"""Prava mandate and payment execution routes."""

from __future__ import annotations

import logging

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

    try:
        session = create_yearly_mandate_session(
            domain=domain.domain,
            merchant_name=body.merchant_name.strip(),
            merchant_url=merchant_url,
            merchant_country=country,
            cap_amount=body.cap_amount,
            currency=currency,
            user_id=f"aegis_domain_{domain.id}",
            user_email=f"aegis+domain-{domain.id}@example.com",
        )
    except PravaConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except PravaMandateError as exc:
        status_code = status.HTTP_502_BAD_GATEWAY
        if exc.status_code == 401:
            status_code = status.HTTP_502_BAD_GATEWAY
        elif exc.status_code == 400:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return MandateResponse(status="pending_approval", approval_url=session.iframe_url)


@router.post(
    "/payments/execute",
    response_model=PlaceholderResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
def execute_payment(_: ExecuteRequest) -> PlaceholderResponse:
    """Execute a covered renewal when the payment service is implemented."""
    return PlaceholderResponse(detail="Payment execution is not implemented yet.")
